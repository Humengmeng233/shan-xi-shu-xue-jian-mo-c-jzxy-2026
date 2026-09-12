"""模型输入、典型日求解与调度结果数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .model_config import ModelConfig
from .optimization import PlanResult, solve_energy_plan

RELEASE_SLOTS = (0, 36, 72, 108)
RELEASE_LABELS = ("00:00", "06:00", "12:00", "18:00")


@dataclass
class DataBundle:
    typical: pd.DataFrame
    dates: pd.DatetimeIndex
    load_kwh: np.ndarray
    pv_kwh: np.ndarray
    net_kwh: np.ndarray
    variable_price: np.ndarray
    fixed_price: np.ndarray
    pv_forecast_kwh: np.ndarray


@dataclass
class ModeResult:
    name: str
    dates: pd.DatetimeIndex
    original_plan_kwh: np.ndarray
    accepted_plan_kwh: np.ndarray
    used_plan_kwh: np.ndarray
    emergency_kwh: np.ndarray
    charge_kwh: np.ndarray
    discharge_kwh: np.ndarray
    curtail_kwh: np.ndarray
    storage_kwh: np.ndarray
    storage_start_kwh: np.ndarray
    forecast_center_kwh: np.ndarray
    forecast_risk_kwh: np.ndarray
    price_yuan_per_kwh: np.ndarray
    daily: pd.DataFrame
    gates: pd.DataFrame


def _matrix_from_long(frame: pd.DataFrame, column: str) -> np.ndarray:
    ordered = frame.sort_values(["operating_date", "interval_index"])
    dates = ordered["operating_date"].nunique()
    if len(ordered) != dates * 144:
        raise ValueError(f"{column} 不是完整的 日期×144 时段矩阵")
    return ordered[column].to_numpy(dtype=float).reshape(dates, 144)


def load_inputs(root: Path, config: ModelConfig) -> DataBundle:
    """读取模型输入并检查日期、时段和矩阵维度。"""

    processed = root / "data" / "processed"
    typical = pd.read_csv(processed / "typical_day_10min.csv")
    actual = pd.read_csv(
        processed / "year_actuals_tariff_10min.csv",
        parse_dates=["operating_date", "interval_start", "interval_end"],
    )
    forecast_long = pd.read_csv(
        processed / "pv_forecasts_10min_linear.csv",
        parse_dates=["issue_date", "issue_timestamp", "target_timestamp"],
    )

    if len(typical) != 144:
        raise ValueError("典型日数据必须正好包含 144 个10分钟时段")
    if actual["operating_date"].nunique() != 365:
        raise ValueError("年度实际数据必须包含 365 个自然日")
    if actual.duplicated(["operating_date", "interval_index"]).any():
        raise ValueError("年度数据存在重复 日期×时段 键")

    actual = actual.sort_values(["operating_date", "interval_index"]).reset_index(drop=True)
    dates = pd.DatetimeIndex(actual["operating_date"].drop_duplicates().sort_values())
    load = _matrix_from_long(actual, "load_energy_kwh")
    pv = _matrix_from_long(actual, "pv_actual_energy_kwh")
    variable_price = _matrix_from_long(actual, "electricity_price_yuan_per_kwh")
    fixed_price = typical["electricity_price_yuan_per_kwh"].to_numpy(dtype=float)

    date_to_index = {date.normalize(): idx for idx, date in enumerate(dates)}
    release_to_index = {label: idx for idx, label in enumerate(RELEASE_LABELS)}
    pv_forecast = np.full((len(dates), 4, 144), np.nan, dtype=float)
    for row in forecast_long.itertuples(index=False):
        issue_date = pd.Timestamp(row.issue_date).normalize()
        if issue_date not in date_to_index or row.issue_time not in release_to_index:
            continue
        minutes_from_day_start = int(
            (pd.Timestamp(row.target_timestamp) - issue_date).total_seconds() // 60
        )
        if 10 <= minutes_from_day_start <= 1440:
            slot = minutes_from_day_start // 10 - 1
            pv_forecast[date_to_index[issue_date], release_to_index[row.issue_time], slot] = (
                float(row.pv_forecast_kw) * config.step_hours
            )

    for release_idx, release_slot in enumerate(RELEASE_SLOTS):
        if np.isnan(pv_forecast[:, release_idx, release_slot:]).any():
            missing = int(np.isnan(pv_forecast[:, release_idx, release_slot:]).sum())
            raise ValueError(f"{RELEASE_LABELS[release_idx]} 预报在可用时域内缺少 {missing} 个值")

    if not np.isfinite(load).all() or not np.isfinite(pv).all():
        raise ValueError("负载或光伏数据含非有限值")
    if (load < 0).any() or (pv < 0).any() or (variable_price < 0).any():
        raise ValueError("基础求解器要求负载、光伏和电价均非负")

    return DataBundle(
        typical=typical,
        dates=dates,
        load_kwh=load,
        pv_kwh=pv,
        net_kwh=load - pv,
        variable_price=variable_price,
        fixed_price=fixed_price,
        pv_forecast_kwh=pv_forecast,
    )


def solve_question1(data: DataBundle, config: ModelConfig) -> tuple[PlanResult, pd.DataFrame]:
    """求解典型日确定性能量流模型。"""

    net = data.typical["load_energy_kwh"].to_numpy(dtype=float) - data.typical[
        "pv_forecast_energy_kwh"
    ].to_numpy(dtype=float)
    plan = solve_energy_plan(
        net,
        data.fixed_price,
        config.storage_initial_kwh,
        config,
        terminal_target_kwh=config.storage_initial_kwh,
    )
    frame = data.typical[
        [
            "interval_index",
            "interval_start",
            "interval_end",
            "load_energy_kwh",
            "pv_forecast_energy_kwh",
        ]
    ].copy()
    frame["purchase_kwh"] = plan.purchase_kwh
    frame["charge_kwh"] = plan.charge_kwh
    frame["discharge_kwh"] = plan.discharge_kwh
    frame["curtail_kwh"] = plan.curtail_kwh
    frame["storage_kwh"] = plan.storage_kwh
    frame["price_yuan_per_kwh"] = data.fixed_price
    return plan, frame


def mode_summary(mode: ModeResult) -> dict[str, Any]:
    daily = mode.daily
    plan_cost = float(daily["plan_cost_yuan"].sum())
    adjustment = float(daily["adjustment_cost_yuan"].sum())
    emergency = float(daily["emergency_cost_yuan"].sum())
    return {
        "model": mode.name,
        "days": int(len(daily)),
        "plan_cost_yuan": plan_cost,
        "adjustment_cost_yuan": adjustment,
        "emergency_cost_yuan": emergency,
        "total_cost_yuan": plan_cost + adjustment + emergency,
        "plan_energy_kwh": float(daily["plan_energy_kwh"].sum()),
        "emergency_energy_kwh": float(daily["emergency_energy_kwh"].sum()),
        "charge_energy_kwh": float(daily["charge_energy_kwh"].sum()),
        "discharge_energy_kwh": float(daily["discharge_energy_kwh"].sum()),
        "max_balance_residual_kwh": float(daily["max_balance_residual_kwh"].max()),
        "minimum_storage_kwh": float(mode.storage_kwh.min()),
        "maximum_storage_kwh": float(mode.storage_kwh.max()),
        "accepted_gate_count": (int(mode.gates["accepted"].sum()) if len(mode.gates) else 0),
    }


def interval_label(slot: int) -> tuple[str, str]:
    start_minutes = slot * 10
    end_minutes = (slot + 1) * 10
    start = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
    end = "24:00" if end_minutes == 1440 else f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
    return start, end


def mode_interval_frame(mode: ModeResult, actual_net_kwh: np.ndarray) -> pd.DataFrame:
    days = len(mode.dates)
    return pd.DataFrame(
        {
            "date": np.repeat(mode.dates.to_numpy(), 144),
            "interval_index": np.tile(np.arange(1, 145), days),
            "interval_start": np.tile([interval_label(t)[0] for t in range(144)], days),
            "interval_end": np.tile([interval_label(t)[1] for t in range(144)], days),
            "price_yuan_per_kwh": mode.price_yuan_per_kwh.reshape(-1),
            "actual_net_load_kwh": actual_net_kwh.reshape(-1),
            "forecast_center_kwh": mode.forecast_center_kwh.reshape(-1),
            "forecast_risk_kwh": mode.forecast_risk_kwh.reshape(-1),
            "original_plan_kwh": mode.original_plan_kwh.reshape(-1),
            "accepted_plan_kwh": mode.accepted_plan_kwh.reshape(-1),
            "used_plan_kwh": mode.used_plan_kwh.reshape(-1),
            "emergency_kwh": mode.emergency_kwh.reshape(-1),
            "charge_kwh": mode.charge_kwh.reshape(-1),
            "discharge_kwh": mode.discharge_kwh.reshape(-1),
            "curtail_kwh": mode.curtail_kwh.reshape(-1),
            "storage_kwh": mode.storage_kwh.reshape(-1),
        }
    )


def _emergency_events(values: np.ndarray, tolerance: float = 1.0e-6) -> list[tuple[str, float]]:
    events: list[tuple[str, float]] = []
    active = np.flatnonzero(values > tolerance)
    if not len(active):
        return events
    start = int(active[0])
    previous = start
    for slot in active[1:]:
        slot = int(slot)
        if slot != previous + 1:
            events.append(
                (
                    f"{interval_label(start)[0]}-{interval_label(previous)[1]}",
                    float(values[start : previous + 1].sum()),
                )
            )
            start = slot
        previous = slot
    events.append(
        (
            f"{interval_label(start)[0]}-{interval_label(previous)[1]}",
            float(values[start : previous + 1].sum()),
        )
    )
    return events
