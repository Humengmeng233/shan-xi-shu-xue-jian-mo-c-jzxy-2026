"""生成建模特征、数据质量记录和诊断图。

运行方式：
    python main.py features

默认读取 ``data/raw``，Excel 读取失败时使用 ``data/interim/raw`` 中的 CSV 镜像。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import traceback
from datetime import datetime, time
from pathlib import Path
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from matplotlib import font_manager
except ImportError as exc:
    print(
        f"缺少运行依赖。请先执行 `pip install pandas numpy openpyxl matplotlib`，原始错误：{exc}",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc

from .paths import ASSETS_DIR, FEATURE_DATA_DIR, INTERIM_DATA_DIR, PROJECT_ROOT, RAW_DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOGGER = logging.getLogger("c_problem_preprocessing")

STEP_MINUTES = 10
STEP_HOURS = STEP_MINUTES / 60.0
FEATURE_START_DATE = pd.Timestamp("2025-02-01")
IQR_MULTIPLIER = 3.0


class DataPipelineError(RuntimeError):
    """表示输入结构、物理约束或完整性校验无法通过。"""


def find_project_root() -> Path:
    """返回项目根目录，并确认原始数据目录存在。"""

    if not RAW_DATA_DIR.is_dir():
        raise FileNotFoundError(f"未找到原始数据目录：{RAW_DATA_DIR}")
    return PROJECT_ROOT


def read_csv_compatible(path: Path) -> pd.DataFrame:
    """按常见中文数据编码依次尝试读取 CSV。"""

    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            frame = pd.read_csv(path, encoding=encoding)
            LOGGER.info("CSV 读取成功：%s，编码=%s", path.name, encoding)
            return frame
        except Exception as exc:  # noqa: BLE001 - 需要兼容多种底层读取异常。
            errors.append(f"{encoding}: {type(exc).__name__}: {exc}")
    raise DataPipelineError(f"CSV 读取失败：{path}\n" + "\n".join(errors))


def read_table_compatible(
    project_root: Path,
    workbook_name: str,
    sheet_name: str,
    fallback_csv_name: str,
) -> tuple[pd.DataFrame, str]:
    """优先读取 Excel 工作表，失败时回退到对应 CSV。"""

    excel_path = RAW_DATA_DIR / workbook_name
    csv_path = INTERIM_DATA_DIR / "raw" / fallback_csv_name
    excel_error: Exception | None = None
    if excel_path.is_file():
        try:
            frame = pd.read_excel(
                excel_path,
                sheet_name=sheet_name,
                engine="openpyxl",
                keep_default_na=False,
            )
            LOGGER.info("Excel 读取成功：%s / %s", workbook_name, sheet_name)
            return frame, f"Excel:{workbook_name}/{sheet_name}"
        except Exception as exc:  # noqa: BLE001 - 兼容引擎缺失、文件损坏等情况。
            excel_error = exc
            LOGGER.warning(
                "Excel 读取失败，将尝试 CSV：%s / %s；原因=%s: %s",
                workbook_name,
                sheet_name,
                type(exc).__name__,
                exc,
            )
    else:
        excel_error = FileNotFoundError(excel_path)
        LOGGER.warning("未找到 Excel，将尝试 CSV：%s", excel_path)
    if csv_path.is_file():
        frame = read_csv_compatible(csv_path)
        return frame, f"CSV:{fallback_csv_name}"
    raise FileNotFoundError(
        f"无法读取 {workbook_name}/{sheet_name}。Excel 错误：{excel_error}；CSV 不存在：{csv_path}"
    )


def sha256(path: Path) -> str:
    """计算文件 SHA-256，用于确认原始数据未被覆盖。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def time_to_minute(value: Any) -> int:
    """把 Excel 时间、字符串时间和 0:00+1 统一转换为日内分钟数。"""

    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, float | np.floating) and 0.0 <= float(value) <= 1.0:
        return int(round(float(value) * 24 * 60))
    text = str(value).strip()
    if text in {"0:00+1", "00:00+1", "24:00", "24:00:00"}:
        return 24 * 60
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if not match:
        raise DataPipelineError(f"无法解析时间值：{value!r}")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise DataPipelineError(f"时间越界：{value!r}")
    return hour * 60 + minute


def minute_label(minute: int) -> str:
    """把分钟数格式化为统一的 HH:MM 标签。"""

    if minute == 24 * 60:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"


def validate_ten_minute_grid(minutes: list[int]) -> None:
    """检查时间列是否完整覆盖 144 个 10 分钟区间。"""

    expected = list(range(STEP_MINUTES, 24 * 60 + STEP_MINUTES, STEP_MINUTES))
    if minutes != expected:
        raise DataPipelineError("10 分钟时间网格不完整、重复或顺序错误。")


def numeric_series(series: pd.Series, label: str) -> pd.Series:
    """把连续型字段严格转换为浮点数，并对转换失败给出字段名。"""

    try:
        converted = pd.to_numeric(series, errors="raise").astype(float)
        return converted
    except Exception as exc:  # noqa: BLE001 - 需要包装 pandas 的多种转换异常。
        raise DataPipelineError(f"字段 {label} 无法转换为连续数值：{exc}") from exc


def interpolate_short_numeric_gaps(
    frame: pd.DataFrame,
    columns: list[str],
    group_columns: list[str] | None,
    max_gap: int,
    audit: dict[str, Any],
) -> pd.DataFrame:
    """仅对连续时间数值的短内部缺口做线性插值，长缺口直接报错。"""

    result = frame.copy()
    for column in columns:
        before_missing = int(result[column].isna().sum())
        if before_missing == 0:
            audit.setdefault("numeric_missing", {})[column] = {
                "before": 0,
                "filled": 0,
                "remaining": 0,
                "method": "无需填充",
            }
            continue
        if group_columns:
            result[column] = result.groupby(group_columns, sort=False)[column].transform(
                lambda values: values.interpolate(
                    method="linear",
                    limit=max_gap,
                    limit_area="inside",
                )
            )
        else:
            result[column] = result[column].interpolate(
                method="linear",
                limit=max_gap,
                limit_area="inside",
            )
        after_missing = int(result[column].isna().sum())
        audit.setdefault("numeric_missing", {})[column] = {
            "before": before_missing,
            "filled": before_missing - after_missing,
            "remaining": after_missing,
            "method": f"同序列线性插值，最多连续 {max_gap} 个内部时间点",
        }
        if after_missing > 0:
            raise DataPipelineError(
                f"字段 {column} 插值后仍有 {after_missing} 个缺失值；为避免制造数据，流程已停止。"
            )
    return result


def read_typical_day(
    project_root: Path, audit: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读取附件 1，并生成统一的代表日 10 分钟表。"""

    raw, source = read_table_compatible(
        project_root,
        "附件1.xlsx",
        "Sheet1",
        "附件1__Sheet1.csv",
    )
    audit["input_sources"]["attachment_1"] = source
    required = ["时间", "电价", "小区负载", "光伏发电预测功率"]
    missing_columns = [column for column in required if column not in raw.columns]
    if missing_columns:
        raise DataPipelineError(f"附件 1 缺少字段：{missing_columns}")
    end_minutes = [time_to_minute(value) for value in raw["时间"]]
    validate_ten_minute_grid(end_minutes)
    clean = pd.DataFrame(
        {
            "interval_index": np.arange(1, len(raw) + 1, dtype=int),
            "interval_start": [minute_label(value - STEP_MINUTES) for value in end_minutes],
            "interval_end": [minute_label(value) for value in end_minutes],
            "interval_start_minute": np.asarray(end_minutes, dtype=int) - STEP_MINUTES,
            "interval_end_minute": np.asarray(end_minutes, dtype=int),
            "duration_hours": STEP_HOURS,
            "electricity_price_yuan_per_kwh": numeric_series(raw["电价"], "附件1.电价"),
            "load_kw": numeric_series(raw["小区负载"], "附件1.小区负载"),
            "pv_forecast_kw": numeric_series(raw["光伏发电预测功率"], "附件1.光伏预测"),
        }
    )
    clean = interpolate_short_numeric_gaps(
        clean,
        ["electricity_price_yuan_per_kwh", "load_kw", "pv_forecast_kw"],
        group_columns=None,
        max_gap=2,
        audit=audit,
    )
    clean["load_energy_kwh"] = clean["load_kw"] * clean["duration_hours"]
    clean["pv_forecast_energy_kwh"] = clean["pv_forecast_kw"] * clean["duration_hours"]
    return raw, clean


def wide_sheet_to_long(
    raw: pd.DataFrame,
    value_name: str,
    audit: dict[str, Any],
) -> pd.DataFrame:
    """把日期×144 时段的连续数值宽表转换为带完整时间戳的长表。"""

    date_column = raw.columns[0]
    dates = pd.to_datetime(raw[date_column], errors="coerce").dt.normalize()
    if dates.isna().any():
        raise DataPipelineError(f"{value_name} 的日期列有 {int(dates.isna().sum())} 个无法解析值。")
    if dates.duplicated().any():
        raise DataPipelineError(f"{value_name} 存在重复日期。")
    value_columns = list(raw.columns[1:])
    end_minutes = [time_to_minute(value) for value in value_columns]
    validate_ten_minute_grid(end_minutes)
    values = raw[value_columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    operating_dates = np.repeat(dates.to_numpy(dtype="datetime64[ns]"), len(end_minutes))
    repeated_minutes = np.tile(np.asarray(end_minutes, dtype=int), len(dates))
    interval_end = pd.to_datetime(operating_dates) + pd.to_timedelta(repeated_minutes, unit="m")
    long = pd.DataFrame(
        {
            "operating_date": pd.to_datetime(operating_dates),
            "interval_index": np.tile(np.arange(1, len(end_minutes) + 1, dtype=int), len(dates)),
            "interval_start": interval_end - pd.Timedelta(minutes=STEP_MINUTES),
            "interval_end": interval_end,
            "duration_hours": STEP_HOURS,
            value_name: values.reshape(-1),
        }
    )
    long = interpolate_short_numeric_gaps(
        long,
        [value_name],
        group_columns=None,
        max_gap=2,
        audit=audit,
    )
    return long


def read_annual_data(
    project_root: Path,
    audit: dict[str, Any],
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """读取附件 2 和附件 4，转换并合并为统一 10 分钟表。"""

    raw_load, source_load = read_table_compatible(
        project_root,
        "附件2.xlsx",
        "小区负载",
        "附件2__小区负载.csv",
    )
    raw_pv, source_pv = read_table_compatible(
        project_root,
        "附件2.xlsx",
        "光伏发电实际功率",
        "附件2__光伏发电实际功率.csv",
    )
    raw_price, source_price = read_table_compatible(
        project_root,
        "附件4.xlsx",
        "Sheet1",
        "附件4__Sheet1.csv",
    )
    audit["input_sources"].update(
        {
            "attachment_2_load": source_load,
            "attachment_2_pv": source_pv,
            "attachment_4_price": source_price,
        }
    )
    load = wide_sheet_to_long(raw_load, "load_kw", audit)
    pv = wide_sheet_to_long(raw_pv, "pv_actual_kw", audit)
    price = wide_sheet_to_long(raw_price, "electricity_price_yuan_per_kwh", audit)
    keys = ["operating_date", "interval_index", "interval_start", "interval_end", "duration_hours"]
    annual = load.merge(pv, on=keys, how="inner", validate="one_to_one")
    annual = annual.merge(price, on=keys, how="inner", validate="one_to_one")
    annual["load_energy_kwh"] = annual["load_kw"] * annual["duration_hours"]
    annual["pv_actual_energy_kwh"] = annual["pv_actual_kw"] * annual["duration_hours"]
    annual = annual.sort_values(["operating_date", "interval_index"], ignore_index=True)
    return {"load": raw_load, "pv": raw_pv, "price": raw_price}, annual


def read_hourly_forecasts(
    project_root: Path,
    audit: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读取附件 3，补全结构性日期并转换为小时预测长表。"""

    raw, source = read_table_compatible(
        project_root,
        "附件3.xlsx",
        "Sheet1",
        "附件3__Sheet1.csv",
    )
    audit["input_sources"]["attachment_3"] = source
    if "日期" not in raw.columns or "预报时刻" not in raw.columns:
        raise DataPipelineError("附件 3 缺少日期或预报时刻字段。")
    raw_dates = raw["日期"].replace(r"^\s*$", np.nan, regex=True)
    structural_blank_count = int(raw_dates.isna().sum())
    filled_dates = raw_dates.ffill()
    if filled_dates.isna().any():
        raise DataPipelineError("附件 3 首个日期为空，无法按版式向下补全。")
    dates = pd.to_datetime(filled_dates, errors="coerce").dt.normalize()
    if dates.isna().any():
        raise DataPipelineError("附件 3 存在无法解析的日期。")
    release_minutes = raw["预报时刻"].map(time_to_minute)
    issue_timestamp = dates + pd.to_timedelta(release_minutes, unit="m")
    forecast_columns = [f"预报{hour}小时" for hour in range(1, 25)]
    missing_columns = [column for column in forecast_columns if column not in raw.columns]
    if missing_columns:
        raise DataPipelineError(f"附件 3 缺少预测字段：{missing_columns}")
    forecast_values = raw[forecast_columns].copy()
    for column in forecast_columns:
        forecast_values[column] = pd.to_numeric(forecast_values[column], errors="coerce")
    forecast_values.insert(0, "issue_timestamp", issue_timestamp)
    hourly = forecast_values.melt(
        id_vars="issue_timestamp",
        var_name="horizon_label",
        value_name="pv_forecast_kw",
    )
    hourly["horizon_hour"] = hourly["horizon_label"].str.extract(r"(\d+)", expand=False).astype(int)
    hourly = hourly.sort_values(["issue_timestamp", "horizon_hour"], ignore_index=True)
    hourly = interpolate_short_numeric_gaps(
        hourly,
        ["pv_forecast_kw"],
        group_columns=["issue_timestamp"],
        max_gap=2,
        audit=audit,
    )
    hourly["target_timestamp"] = hourly["issue_timestamp"] + pd.to_timedelta(
        hourly["horizon_hour"], unit="h"
    )
    hourly["issue_date"] = hourly["issue_timestamp"].dt.normalize()
    hourly["issue_time"] = hourly["issue_timestamp"].dt.strftime("%H:%M")
    release_counts = (
        hourly[["issue_date", "issue_time"]].drop_duplicates().groupby("issue_date").size()
    )
    if not release_counts.eq(4).all():
        raise DataPipelineError("附件 3 并非每天恰好四个发布时刻。")
    audit["structural_missing"] = {
        "field": "附件3.日期",
        "before": structural_blank_count,
        "filled": structural_blank_count,
        "remaining": 0,
        "method": "按每日 0:00、6:00、12:00、18:00 四行版式前向填充",
    }
    hourly = hourly[
        [
            "issue_date",
            "issue_time",
            "issue_timestamp",
            "horizon_hour",
            "target_timestamp",
            "pv_forecast_kw",
        ]
    ]
    return raw, hourly


def align_forecasts_to_ten_minutes(
    hourly: pd.DataFrame,
    annual: pd.DataFrame,
    audit: dict[str, Any],
) -> pd.DataFrame:
    """把每次发布的 24 个小时预测线性插值到 10 分钟调度网格。"""

    actual_at_timestamp = annual.set_index("interval_end")["pv_actual_kw"]
    target_minutes = np.arange(STEP_MINUTES, 24 * 60 + STEP_MINUTES, STEP_MINUTES, dtype=int)
    target_hours = target_minutes / 60.0
    pieces: list[pd.DataFrame] = []
    assumed_zero_anchor_count = 0
    for issue_timestamp, group in hourly.groupby("issue_timestamp", sort=True):
        group = group.sort_values("horizon_hour")
        if issue_timestamp in actual_at_timestamp.index:
            anchor = float(actual_at_timestamp.loc[issue_timestamp])
        elif issue_timestamp == pd.Timestamp("2025-01-01 00:00:00"):
            anchor = 0.0
            assumed_zero_anchor_count += 1
        else:
            raise DataPipelineError(f"缺少发布时刻实际光伏功率：{issue_timestamp}")
        known_hours = np.arange(0, 25, dtype=float)
        known_values = np.concatenate(([anchor], group["pv_forecast_kw"].to_numpy(dtype=float)))
        interpolated = np.interp(target_hours, known_hours, known_values)
        targets = issue_timestamp + pd.to_timedelta(target_minutes, unit="m")
        piece = pd.DataFrame(
            {
                "issue_date": issue_timestamp.normalize(),
                "issue_time": issue_timestamp.strftime("%H:%M"),
                "issue_timestamp": issue_timestamp,
                "target_timestamp": targets,
                "lead_minutes": target_minutes,
                "pv_forecast_kw": interpolated,
                "anchor_pv_actual_kw": anchor,
            }
        )
        piece["pv_forecast_energy_kwh"] = piece["pv_forecast_kw"] * STEP_HOURS
        for label in ("00:00", "06:00", "12:00", "18:00"):
            encoded_name = f"issue_{label.replace(':', '')}"
            piece[encoded_name] = int(issue_timestamp.strftime("%H:%M") == label)
        pieces.append(piece)
    result = pd.concat(pieces, ignore_index=True)
    audit["forecast_alignment"] = {
        "method": "发布时刻实际值作为 0 小时锚点，相邻小时预测点线性插值到 10 分钟",
        "first_boundary_zero_anchor_count": assumed_zero_anchor_count,
        "categorical_encoding": "issue_time 使用 One-Hot；未使用标签编码",
    }
    return result


def extreme_iqr_outlier_flags(
    frame: pd.DataFrame,
    value_column: str,
) -> pd.Series:
    """使用极端 Tukey 围栏识别连续数值候选异常。"""

    q1 = float(frame[value_column].quantile(0.25))
    q3 = float(frame[value_column].quantile(0.75))
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series(False, index=frame.index)
    lower = q1 - IQR_MULTIPLIER * iqr
    upper = q3 + IQR_MULTIPLIER * iqr
    return (frame[value_column] < lower) | (frame[value_column] > upper)


def add_anomaly_flags(annual: pd.DataFrame, audit: dict[str, Any]) -> pd.DataFrame:
    """执行物理边界检查和稳健统计异常检测。"""

    result = annual.copy()
    negative_load = result["load_kw"] < 0
    negative_pv = result["pv_actual_kw"] < 0
    nonfinite = ~np.isfinite(
        result[["load_kw", "pv_actual_kw", "electricity_price_yuan_per_kwh"]].to_numpy(dtype=float)
    )
    physical_invalid_count = int(negative_load.sum() + negative_pv.sum() + nonfinite.sum())
    if physical_invalid_count > 0:
        raise DataPipelineError(f"发现 {physical_invalid_count} 个物理非法或非有限数值。")
    result["load_robust_outlier"] = extreme_iqr_outlier_flags(result, "load_kw")
    result["pv_robust_outlier"] = extreme_iqr_outlier_flags(result, "pv_actual_kw")
    result["price_robust_outlier"] = extreme_iqr_outlier_flags(
        result, "electricity_price_yuan_per_kwh"
    )
    audit["anomaly_detection"] = {
        "physical_invalid_count": physical_invalid_count,
        "method": f"连续数值采用极端 Tukey 围栏：Q1-{IQR_MULTIPLIER}×IQR 至 Q3+{IQR_MULTIPLIER}×IQR；同时执行物理边界检查",
        "candidate_counts": {
            "load_kw": int(result["load_robust_outlier"].sum()),
            "pv_actual_kw": int(result["pv_robust_outlier"].sum()),
            "electricity_price_yuan_per_kwh": int(result["price_robust_outlier"].sum()),
        },
        "correction_or_deletion": "未执行；候选峰谷仍满足物理边界，可能是真实天气、负载或市场波动",
    }
    return result


def month_to_season(month: int) -> str:
    """把月份映射为无序季节类别。"""

    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def build_model_features(
    annual: pd.DataFrame, audit: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """提取时序、周期、滞后、滚动和分类特征，并执行类型适配缩放。"""

    features = annual.copy()
    features["net_load_kw"] = features["load_kw"] - features["pv_actual_kw"]
    features["net_load_energy_kwh"] = features["net_load_kw"] * features["duration_hours"]
    features["month"] = features["interval_end"].dt.month.astype(int)
    features["day_of_week"] = features["interval_end"].dt.dayofweek.astype(int)
    features["is_weekend"] = (features["day_of_week"] >= 5).astype(int)
    features["season"] = features["month"].map(month_to_season)
    minute_of_day = (
        features["interval_end"].dt.hour * 60 + features["interval_end"].dt.minute
    ).astype(float)
    features["time_sin"] = np.sin(2 * math.pi * minute_of_day / (24 * 60))
    features["time_cos"] = np.cos(2 * math.pi * minute_of_day / (24 * 60))
    day_of_year = features["interval_end"].dt.dayofyear.astype(float)
    features["year_sin"] = np.sin(2 * math.pi * day_of_year / 365.0)
    features["year_cos"] = np.cos(2 * math.pi * day_of_year / 365.0)
    features["load_lag_10min_kw"] = features["load_kw"].shift(1)
    features["load_lag_1h_kw"] = features["load_kw"].shift(6)
    features["load_lag_1d_kw"] = features["load_kw"].shift(144)
    features["pv_lag_1d_kw"] = features["pv_actual_kw"].shift(144)
    features["price_lag_1d_yuan_per_kwh"] = features["electricity_price_yuan_per_kwh"].shift(144)
    features["load_rolling_1h_mean_kw"] = features["load_kw"].shift(1).rolling(6).mean()
    features["load_rolling_24h_mean_kw"] = features["load_kw"].shift(1).rolling(144).mean()
    features["pv_ramp_10min_kw"] = features["pv_actual_kw"].diff()
    features["price_change_10min"] = features["electricity_price_yuan_per_kwh"].diff()
    features = features.loc[features["operating_date"] >= FEATURE_START_DATE].copy()
    engineered_columns = [
        "load_lag_10min_kw",
        "load_lag_1h_kw",
        "load_lag_1d_kw",
        "pv_lag_1d_kw",
        "price_lag_1d_yuan_per_kwh",
        "load_rolling_1h_mean_kw",
        "load_rolling_24h_mean_kw",
        "pv_ramp_10min_kw",
        "price_change_10min",
    ]
    if features[engineered_columns].isna().any().any():
        raise DataPipelineError("正式建模区间的滞后或滚动特征仍有缺失。")
    calibration = annual.loc[annual["operating_date"] < FEATURE_START_DATE].copy()
    calibration["net_load_kw"] = calibration["load_kw"] - calibration["pv_actual_kw"]
    standardize_columns = ["load_kw", "net_load_kw", "electricity_price_yuan_per_kwh"]
    scaler_parameters: dict[str, Any] = {
        "fit_period": [
            calibration["operating_date"].min().date().isoformat(),
            calibration["operating_date"].max().date().isoformat(),
        ],
        "standardization": {},
        "normalization": {},
    }
    for column in standardize_columns:
        mean = float(calibration[column].mean())
        std = float(calibration[column].std(ddof=1))
        if std == 0 or not np.isfinite(std):
            raise DataPipelineError(f"字段 {column} 的标准差无效，无法标准化。")
        features[f"{column}_zscore"] = (features[column] - mean) / std
        scaler_parameters["standardization"][column] = {
            "mean": mean,
            "std": std,
            "method": "Z-score",
            "reason": "连续变量量纲和波动幅度不同，标准化仅供距离/梯度敏感模型使用",
        }
    pv_min = float(calibration["pv_actual_kw"].min())
    pv_max = float(calibration["pv_actual_kw"].max())
    if pv_max == pv_min:
        raise DataPipelineError("一月光伏功率极差为零，无法归一化。")
    features["pv_actual_kw_minmax"] = (features["pv_actual_kw"] - pv_min) / (pv_max - pv_min)
    scaler_parameters["normalization"]["pv_actual_kw"] = {
        "min": pv_min,
        "max": pv_max,
        "method": "Min-Max",
        "reason": "光伏功率非负且零点有明确物理意义；按历史一月尺度归一化，不裁剪越界值",
    }
    weekday_category = features["day_of_week"].map(lambda value: f"dow_{int(value)}")
    one_hot = pd.get_dummies(
        pd.DataFrame({"weekday": weekday_category, "season": features["season"]}),
        columns=["weekday", "season"],
        dtype=int,
    )
    features = pd.concat([features, one_hot], axis=1)
    features = features.drop(columns=["season", "month", "day_of_week"])
    audit["feature_engineering"] = {
        "output_start": FEATURE_START_DATE.date().isoformat(),
        "warmup_period": "2025-01-01 至 2025-01-31",
        "continuous_features": [
            "net_load_kw",
            "power_to_energy",
            "10min/1h/1d lags",
            "1h/24h trailing means",
            "PV ramp",
            "price change",
        ],
        "cyclical_features": ["time_sin", "time_cos", "year_sin", "year_cos"],
        "one_hot_features": sorted(one_hot.columns.tolist()),
        "label_encoding": "未使用；weekday、season 和 issue_time 均无自然等级顺序",
        "scaling_scope": "只写入独立 model_features_10min.csv，不改变基础物理量 CSV",
    }
    return features.reset_index(drop=True), scaler_parameters


def frame_quality(
    frame: pd.DataFrame,
    key_columns: list[str],
    numeric_columns: list[str],
) -> dict[str, Any]:
    """计算通用数据完整性指标。"""

    numeric_matrix = frame[numeric_columns].to_numpy(dtype=float)
    return {
        "rows": int(frame.shape[0]),
        "columns": int(frame.shape[1]),
        "duplicate_keys": int(frame.duplicated(key_columns).sum()),
        "missing_cells": int(frame.isna().sum().sum()),
        "nonfinite_numeric_cells": int((~np.isfinite(numeric_matrix)).sum()),
        "ranges": {
            column: {"min": float(frame[column].min()), "max": float(frame[column].max())}
            for column in numeric_columns
        },
    }


def choose_font() -> str:
    """自动选择可用中文字体，若没有则安全回退。"""

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans CN"):
        if candidate in available:
            return candidate
    return "DejaVu Sans"


def make_before_after_figure(
    output_path: Path,
    raw_typical: pd.DataFrame,
    clean_typical: pd.DataFrame,
    raw_annual: dict[str, pd.DataFrame],
    annual: pd.DataFrame,
    raw_forecast: pd.DataFrame,
    forecast_10min: pd.DataFrame,
) -> None:
    """生成四个附件各自的处理前、处理后双列可视化。"""

    font_name = choose_font()
    plt.rcParams.update(
        {
            "font.family": font_name,
            "axes.unicode_minus": False,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
        }
    )
    figure, axes = plt.subplots(4, 2, figsize=(17, 18), constrained_layout=True)
    figure.patch.set_facecolor("white")
    colors = {"load": "#2457A7", "pv": "#E69F00", "price": "#2A9D8F", "accent": "#C44E52"}

    axes[0, 0].plot(
        raw_typical.index + 1,
        pd.to_numeric(raw_typical["小区负载"]),
        color=colors["load"],
        label="负载功率",
    )
    axes[0, 0].plot(
        raw_typical.index + 1,
        pd.to_numeric(raw_typical["光伏发电预测功率"]),
        color=colors["pv"],
        label="光伏预测功率",
    )
    axes[0, 0].set_title("附件 1 处理前：原始功率")
    axes[0, 0].set_xlabel("原始行号")
    axes[0, 0].set_ylabel("功率（千瓦）")
    axes[0, 0].legend(frameon=False, ncol=2)

    axes[0, 1].plot(
        clean_typical["interval_end_minute"] / 60,
        clean_typical["load_energy_kwh"],
        color=colors["load"],
        label="负载电量",
    )
    axes[0, 1].plot(
        clean_typical["interval_end_minute"] / 60,
        clean_typical["pv_forecast_energy_kwh"],
        color=colors["pv"],
        label="光伏预测电量",
    )
    axes[0, 1].set_title("附件 1 处理后：10 分钟对齐电量")
    axes[0, 1].set_xlabel("区间结束时刻（小时）")
    axes[0, 1].set_ylabel("时段电量（千瓦时）")
    axes[0, 1].legend(frameon=False, ncol=2)

    raw_load_matrix = (
        raw_annual["load"].iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    )
    heat_load = axes[1, 0].imshow(
        raw_load_matrix, aspect="auto", cmap="Blues", interpolation="nearest"
    )
    axes[1, 0].set_title("附件 2 处理前：365×144 负载矩阵")
    axes[1, 0].set_xlabel("10 分钟时段")
    axes[1, 0].set_ylabel("年度日期序号")
    figure.colorbar(heat_load, ax=axes[1, 0], shrink=0.8, label="负载功率（千瓦）")

    daily = annual.groupby("operating_date", as_index=False)[
        ["load_energy_kwh", "pv_actual_energy_kwh"]
    ].sum()
    axes[1, 1].plot(
        daily["operating_date"],
        daily["load_energy_kwh"],
        color=colors["load"],
        linewidth=1.2,
        label="每日负载",
    )
    axes[1, 1].plot(
        daily["operating_date"],
        daily["pv_actual_energy_kwh"],
        color=colors["pv"],
        linewidth=1.2,
        label="每日光伏",
    )
    axes[1, 1].set_title("附件 2 处理后：长表每日电量")
    axes[1, 1].set_xlabel("运行日期")
    axes[1, 1].set_ylabel("日电量（千瓦时）")
    axes[1, 1].legend(frameon=False, ncol=2)

    sample_date_text = "2025-6-21"
    raw_forecast_dates = (
        raw_forecast["日期"].replace(r"^\s*$", np.nan, regex=True).ffill().astype(str)
    )
    sample_raw = raw_forecast.loc[
        pd.to_datetime(raw_forecast_dates).dt.date == pd.Timestamp("2025-06-21").date()
    ].copy()
    for _, row in sample_raw.iterrows():
        raw_values = [float(row[f"预报{hour}小时"]) for hour in range(1, 25)]
        axes[2, 0].plot(range(1, 25), raw_values, linewidth=1.5, label=str(row["预报时刻"]))
    axes[2, 0].set_title(f"附件 3 处理前：24 个小时预测点（{sample_date_text}）")
    axes[2, 0].set_xlabel("预测提前时间（小时）")
    axes[2, 0].set_ylabel("光伏预测功率（千瓦）")
    axes[2, 0].legend(frameon=False, ncol=4)

    sample_after = forecast_10min.loc[forecast_10min["issue_date"] == pd.Timestamp("2025-06-21")]
    for issue_time, group in sample_after.groupby("issue_time", sort=True):
        axes[2, 1].plot(
            group["lead_minutes"] / 60, group["pv_forecast_kw"], linewidth=1.5, label=issue_time
        )
    axes[2, 1].set_title("附件 3 处理后：10 分钟对齐预测")
    axes[2, 1].set_xlabel("预测提前时间（小时）")
    axes[2, 1].set_ylabel("光伏预测功率（千瓦）")
    axes[2, 1].legend(frameon=False, ncol=4)

    raw_price_matrix = (
        raw_annual["price"].iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    )
    heat_price = axes[3, 0].imshow(
        raw_price_matrix, aspect="auto", cmap="viridis", interpolation="nearest"
    )
    axes[3, 0].set_title("附件 4 处理前：365×144 电价矩阵")
    axes[3, 0].set_xlabel("10 分钟时段")
    axes[3, 0].set_ylabel("年度日期序号")
    figure.colorbar(heat_price, ax=axes[3, 0], shrink=0.8, label="电价（元/千瓦时）")

    daily_price = (
        annual.groupby("operating_date")["electricity_price_yuan_per_kwh"]
        .agg(["min", "mean", "max"])
        .reset_index()
    )
    axes[3, 1].fill_between(
        daily_price["operating_date"],
        daily_price["min"],
        daily_price["max"],
        color="#B7E4C7",
        alpha=0.65,
        label="每日最小值—最大值",
    )
    axes[3, 1].plot(
        daily_price["operating_date"],
        daily_price["mean"],
        color=colors["price"],
        linewidth=1.4,
        label="每日均值",
    )
    axes[3, 1].set_title("附件 4 处理后：长表每日统计")
    axes[3, 1].set_xlabel("运行日期")
    axes[3, 1].set_ylabel("电价（元/千瓦时）")
    axes[3, 1].legend(frameon=False)

    for axis in axes.flat:
        axis.grid(True, color="#D9D9D9", linewidth=0.5, alpha=0.55)
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle("C 题数据处理前后对比", fontsize=17, fontweight="bold")
    figure.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def make_scaling_figure(output_path: Path, features: pd.DataFrame) -> None:
    """生成连续物理量与缩放特征的处理前后分布对比图。"""

    plt.rcParams.update({"font.family": choose_font(), "axes.unicode_minus": False})
    figure, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    figure.patch.set_facecolor("white")
    axes[0, 0].hist(features["load_kw"], bins=50, color="#2457A7", alpha=0.85)
    axes[0, 0].set_title("处理前：负载物理量")
    axes[0, 0].set_xlabel("负载功率（千瓦）")
    axes[0, 1].hist(features["load_kw_zscore"], bins=50, color="#5B8FF9", alpha=0.85)
    axes[0, 1].set_title("处理后：负载标准化")
    axes[0, 1].set_xlabel("标准化负载")
    axes[1, 0].hist(features["pv_actual_kw"], bins=50, color="#E69F00", alpha=0.85)
    axes[1, 0].set_title("处理前：光伏物理量")
    axes[1, 0].set_xlabel("光伏功率（千瓦）")
    axes[1, 1].hist(features["pv_actual_kw_minmax"], bins=50, color="#F4A261", alpha=0.85)
    axes[1, 1].set_title("处理后：以一月为基准的光伏归一化")
    axes[1, 1].set_xlabel("归一化光伏功率")
    for axis in axes.flat:
        axis.set_ylabel("样本数")
        axis.grid(True, color="#D9D9D9", linewidth=0.5, alpha=0.55)
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle("缩放仅应用于模型特征表", fontsize=15, fontweight="bold")
    figure.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def write_preview(output_path: Path, outputs: dict[str, pd.DataFrame]) -> str:
    """保存并返回每个处理后数据表的前 10 行和后 5 行。"""

    sections: list[str] = []
    for name, frame in outputs.items():
        sections.append(f"\n{'=' * 100}\n{name} | 尺寸={frame.shape}\n{'=' * 100}")
        sections.append("[前 10 行]\n" + frame.head(10).to_string(index=False))
        sections.append("[后 5 行]\n" + frame.tail(5).to_string(index=False))
    text = "\n".join(sections)
    output_path.write_text(text, encoding="utf-8")
    return text


def validate_exported_csvs(
    output_dir: Path,
    expected: dict[str, tuple[int, list[str], list[str]]],
) -> dict[str, Any]:
    """回读 CSV，验证行数、必需字段、主键、缺失和哈希。"""

    results: dict[str, Any] = {}
    for name, (expected_rows, key_columns, required_columns) in expected.items():
        path = output_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise DataPipelineError(f"导出文件不存在或为空：{path}")
        frame = pd.read_csv(path, encoding="utf-8-sig")
        if len(frame) != expected_rows:
            raise DataPipelineError(f"{name} 行数错误：期望 {expected_rows}，实际 {len(frame)}")
        missing_columns = [column for column in required_columns if column not in frame.columns]
        if missing_columns:
            raise DataPipelineError(f"{name} 缺少字段：{missing_columns}")
        duplicate_keys = int(frame.duplicated(key_columns).sum())
        if duplicate_keys:
            raise DataPipelineError(f"{name} 有 {duplicate_keys} 个重复主键。")
        missing_cells = int(frame.isna().sum().sum())
        if missing_cells:
            raise DataPipelineError(f"{name} 回读后有 {missing_cells} 个缺失单元格。")
        results[name] = {
            "status": "PASS",
            "rows": int(frame.shape[0]),
            "columns": int(frame.shape[1]),
            "duplicate_keys": duplicate_keys,
            "missing_cells": missing_cells,
            "bytes": int(path.stat().st_size),
            "sha256": sha256(path),
        }
    return results


def main() -> int:
    """执行读取、类型适配、预处理、绘图、导出和完整性校验。"""

    try:
        project_root = find_project_root()
        output_dir = FEATURE_DATA_DIR
        figure_dir = ASSETS_DIR / "data"
        figure_dir.mkdir(parents=True, exist_ok=True)
        audit: dict[str, Any] = {
            "project_root_detection": "使用 src.paths 中的项目目录定义",
            "input_sources": {},
        }
        raw_typical, typical = read_typical_day(project_root, audit)
        raw_annual, annual = read_annual_data(project_root, audit)
        annual = add_anomaly_flags(annual, audit)
        raw_forecast, hourly_forecast = read_hourly_forecasts(project_root, audit)
        forecast_10min = align_forecasts_to_ten_minutes(hourly_forecast, annual, audit)
        model_features, scaler_parameters = build_model_features(annual, audit)
        outputs = {
            "typical_day_10min.csv": typical,
            "year_actuals_tariff_10min.csv": annual,
            "pv_forecasts_hourly_long.csv": hourly_forecast,
            "pv_forecasts_10min_linear.csv": forecast_10min,
            "model_features_10min.csv": model_features,
        }
        for name, frame in outputs.items():
            frame.to_csv(
                output_dir / name,
                index=False,
                encoding="utf-8-sig",
                float_format="%.10g",
            )
        (output_dir / "scaler_parameters.json").write_text(
            json.dumps(scaler_parameters, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quality = {
            "typical_day": frame_quality(
                typical,
                ["interval_index"],
                ["electricity_price_yuan_per_kwh", "load_kw", "pv_forecast_kw"],
            ),
            "annual": frame_quality(
                annual,
                ["operating_date", "interval_index"],
                ["load_kw", "pv_actual_kw", "electricity_price_yuan_per_kwh"],
            ),
            "hourly_forecast": frame_quality(
                hourly_forecast,
                ["issue_timestamp", "horizon_hour"],
                ["pv_forecast_kw"],
            ),
            "forecast_10min": frame_quality(
                forecast_10min,
                ["issue_timestamp", "lead_minutes"],
                ["pv_forecast_kw", "pv_forecast_energy_kwh"],
            ),
            "model_features": frame_quality(
                model_features,
                ["operating_date", "interval_index"],
                ["load_kw", "pv_actual_kw", "net_load_kw"],
            ),
        }
        hourly_check = hourly_forecast.rename(columns={"pv_forecast_kw": "hourly_kw"})
        aligned_hours = forecast_10min.loc[forecast_10min["lead_minutes"] % 60 == 0].rename(
            columns={"pv_forecast_kw": "interpolated_kw"}
        )
        anchor_check = hourly_check.merge(
            aligned_hours[["issue_timestamp", "target_timestamp", "interpolated_kw"]],
            on=["issue_timestamp", "target_timestamp"],
            how="inner",
            validate="one_to_one",
        )
        max_anchor_error = float(
            np.max(np.abs(anchor_check["hourly_kw"] - anchor_check["interpolated_kw"]))
        )
        if max_anchor_error > 1e-9:
            raise DataPipelineError(f"小时预测与 10 分钟插值的整点误差过大：{max_anchor_error}")
        audit["forecast_alignment"]["max_hourly_anchor_error_kw"] = max_anchor_error
        make_before_after_figure(
            figure_dir / "all_datasets_before_after.png",
            raw_typical,
            typical,
            raw_annual,
            annual,
            raw_forecast,
            forecast_10min,
        )
        make_scaling_figure(figure_dir / "feature_scaling_before_after.png", model_features)
        preview_text = write_preview(output_dir / "processed_data_preview.txt", outputs)
        expected = {
            "typical_day_10min.csv": (
                144,
                ["interval_index"],
                ["interval_index", "load_kw", "pv_forecast_kw"],
            ),
            "year_actuals_tariff_10min.csv": (
                365 * 144,
                ["operating_date", "interval_index"],
                ["operating_date", "load_kw", "pv_actual_kw", "electricity_price_yuan_per_kwh"],
            ),
            "pv_forecasts_hourly_long.csv": (
                365 * 4 * 24,
                ["issue_timestamp", "horizon_hour"],
                ["issue_timestamp", "horizon_hour", "pv_forecast_kw"],
            ),
            "pv_forecasts_10min_linear.csv": (
                365 * 4 * 144,
                ["issue_timestamp", "lead_minutes"],
                ["issue_timestamp", "lead_minutes", "pv_forecast_kw"],
            ),
            "model_features_10min.csv": (
                334 * 144,
                ["operating_date", "interval_index"],
                ["operating_date", "net_load_kw", "time_sin", "time_cos"],
            ),
        }
        export_validation = validate_exported_csvs(output_dir, expected)
        run_report = {
            "status": "PASS",
            "method_audit": audit,
            "data_quality": quality,
            "export_validation": export_validation,
            "figures": [
                str((figure_dir / "all_datasets_before_after.png").relative_to(project_root)),
                str((figure_dir / "feature_scaling_before_after.png").relative_to(project_root)),
            ],
        }
        (output_dir / "preprocessing_run_report.json").write_text(
            json.dumps(run_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        LOGGER.info("预处理与完整性校验全部通过，输出目录：%s", output_dir)
        print(preview_text)
        print("\n运行摘要")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "output_rows": {name: len(frame) for name, frame in outputs.items()},
                    "anomaly_candidates": audit["anomaly_detection"]["candidate_counts"],
                    "max_hourly_anchor_error_kw": max_anchor_error,
                    "output_directory": str(output_dir.relative_to(project_root)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - 顶层必须捕获并报告所有运行失败。
        LOGGER.error("预处理失败：%s: %s", type(exc).__name__, exc)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
