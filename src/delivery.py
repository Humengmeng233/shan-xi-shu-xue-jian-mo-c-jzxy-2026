"""生成按问题分组的最终交付文件。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from .paths import ASSETS_DIR, PROJECT_ROOT

PACKAGES = {
    "result1": ("Q1", "问题一：典型日计划", "q1", ["01_typical_dispatch.png"]),
    "result2": ("Q2", "问题二：年度日前计划", "q2", ["02_forecast_execution.png"]),
    "result3": ("Q3", "问题三：日内滚动调整", "q3", ["03_fixed_costs.png", "05_gate_and_baseline.png"]),
    "result4-2": ("Q4-2", "问题四：波动电价日前计划", "q4", ["04_variable_costs.png"]),
    "result4-3": ("Q4-3", "问题四：波动电价日内调整", "q4", ["04_variable_costs.png", "05_gate_and_baseline.png"]),
}

EXPLANATIONS = {
    "Q1": "购电和储能按典型日已知输入联合优化，日末储电量回到初值。节约来自光伏消纳与购电时段转移，不能直接外推为全年相同比例的收益。",
    "Q2": "日前计划使用历史场景平衡提前购电与高价补救成本。预测区间只作诊断，计划购电量不等于区间上界。",
    "Q3": "日内新预测触发剩余时段重优化；只有费用节约或应急风险下降达到门槛才调整购电。小额补购可能更频繁，总费用下降不表示每天都省钱。",
    "Q4-2": "在波动电价下重新求解日前购电与储能计划。主结果假设当天完整电价在零点已知，不能把这一条件下的结果当作未知价格的收益保证。",
    "Q4-3": "波动电价下同时考虑滚动预测、储能动作和调整费用。与日前模式的差额包含信息与运行机制的综合影响，不能全部归因于调整门槛。",
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_results(source: Path, destination: Path, info: dict, figures: Path,
                    validation: dict | None = None, *, update_assets: bool = False) -> list[Path]:
    """只发布工作簿、图片、运行摘要、控制台输出与使用说明。"""
    models = {row["model"]: row for row in info["models"]}
    delivered = []
    for package, (mode, title, command, picture_names) in PACKAGES.items():
        workbook = source / f"{package}.xlsx"
        if not workbook.exists():
            continue
        folder = destination / package
        folder.mkdir(parents=True, exist_ok=True)
        previous_images = []
        previous_record = folder / "运行结果.json"
        if previous_record.exists():
            previous_images = list(json.loads(previous_record.read_text(encoding="utf-8")).get("图片SHA256", {}))
        shutil.copy2(workbook, folder / workbook.name)
        images = []
        single_picture = figures / f"{mode}_dispatch.png"
        candidates = [single_picture] if single_picture.exists() else [figures / n for n in picture_names]
        for picture in candidates:
            if picture.exists():
                shutil.copy2(picture, folder / picture.name)
                images.append(picture.name)
        days = 1 if mode == "Q1" else int(models[mode]["days"])
        metrics = info["question1"] if mode == "Q1" else models[mode]
        cost = metrics["cost_yuan"] if mode == "Q1" else metrics["total_cost_yuan"]
        output = [f"{title}", f"计算天数：{days}", f"实际总费用：{cost:,.2f} 元"]
        if mode == "Q1":
            base = metrics["baseline_cost_yuan"]
            output += [f"无储能费用：{base:,.2f} 元", f"费用下降：{100 * (1 - cost / base):.2f}%"]
        else:
            output += [f"紧急购电量：{metrics['emergency_energy_kwh']:,.2f} 千瓦时",
                       f"紧急购电费用：{metrics['emergency_cost_yuan']:,.2f} 元",
                       f"接受调整次数：{metrics['accepted_gate_count']}"]
        output_text = "\n".join(output) + "\n"
        (folder / "程序输出.txt").write_text(output_text, encoding="utf-8")
        record = {
            "问题": title, "模型": mode, "计算天数": days,
            "是否部分日期": mode != "Q1" and days < 334,
            "费用与电量": metrics, "模型参数": info["parameters"],
            "运行参数": {key: value for key, value in info["solver_parameters"].items() if key != "output"},
            "交付目录": os.path.relpath(folder, PROJECT_ROOT).replace("\\", "/"),
            "物理检验": info.get("physical_checks", {}).get(mode, {}),
            "工作簿检验": next((r for r in info["workbook_checks"] if r["file"] == workbook.name), {}),
            "输入文件哈希": info["source_hashes"],
            "工作簿SHA256": file_hash(folder / workbook.name),
            "图片SHA256": {name: file_hash(folder / name) for name in images},
            "适用范围": info.get("limitations", []),
        }
        if validation:
            record["原计算批次独立检验"] = {
                key: [row for row in validation.get(key, []) if row.get("model") == mode]
                for key in ("physical", "forecast", "risk")
            }
            record["原计算批次独立检验"]["comparisons"] = [
                row for row in validation.get("comparisons", [])
                if row["comparison"].endswith(f" -> {mode}")
            ]
            record["原计算批次独立检验"]["bootstrap"] = [
                row for row in validation.get("bootstrap", [])
                if row["comparison"].endswith(f" -> {mode}")
            ]
            if mode in {"Q3", "Q4-3"}:
                record["原计算批次独立检验"]["intraday_forecast"] = validation["intraday_forecast"]
        comparison = {"Q3": "Q2", "Q4-3": "Q4-2"}.get(mode)
        interpretation = EXPLANATIONS[mode]
        if comparison in models:
            difference = models[comparison]["total_cost_yuan"] - cost
            record["与日前策略费用差_元"] = difference
            interpretation += f" 本批次相较 {comparison} 的总费用差为 {difference:,.2f} 元（正值表示节约）。"
        control = f"{mode}-no-update"
        if control in models:
            record["信息一致的不更新对照"] = models[control]
            gain = 100 * (1 - cost / models[control]["total_cost_yuan"])
            interpretation += f" 相对相同零点信息的不更新对照组，费用下降 {gain:.2f}%。"
        (folder / "运行结果.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        flags = " --ablations" if info["solver_parameters"].get("ablations") and command in {"q3", "q4"} else ""
        if mode != "Q1" and days < 334:
            flags += f" --days {days}"
        if destination != PROJECT_ROOT / "result":
            relative = os.path.relpath(destination, PROJECT_ROOT).replace("\\", "/")
            flags += f' --output "{relative}"'
        for name, default in (("scenarios", 8), ("risk_weight", 0.2), ("radius", 0.15), ("refund", 0.0), ("price_information", "known")):
            value = info["solver_parameters"].get(name, default)
            if value != default:
                flags += f" --{name.replace('_', '-')} {value}"
        readme_link = os.path.relpath(PROJECT_ROOT / "README.md", folder).replace("\\", "/")
        lines = [f"# {title}", "", f"完整模型与本问推导见 [项目 README]({readme_link}#{command})。", "",
                 "## 运行方式", "", "在项目根目录执行：", "", "```powershell", "uv sync",
                 f"uv run python main.py {command}{flags}", "```", "",
                 "## 程序输出", "", "下列数值由保存的计算结果生成；机器可读数据见 [运行结果](运行结果.json)。", "",
                 "```text", output_text.rstrip(), "```", "", "## 结果文件", "",
                 f"- [{workbook.name}]({workbook.name})：计划购电与储能策略；年度模式同时记录紧急购电，滚动模式另含调整购电。",
                 "- [程序输出.txt](程序输出.txt)：本批次费用、电量与调整次数摘要。", "",
                 "## 结果说明与解读", "", interpretation, "",
                 "工作簿保留题目模板结构。年度计划按日期与十分钟时段记录，储能按模板时间块汇总，紧急购电按连续事件记录。", "",
                 "部分日期结果只用于运行检查，不能与完整年度费用直接比较。工作簿合计若为公式，表格软件打开后可重新计算；保留原有数值单元格，不用缺失公式缓存代替真实值。", ""]
        lines += ["## 数值与检验说明", ""]
        if mode != "Q1":
            lines += ["| 指标 | 本批次结果 |", "|---|---:|",
                      f"| 原始计划费用（元） | {metrics['plan_cost_yuan']:,.2f} |",
                      f"| 调整净费用（元） | {metrics['adjustment_cost_yuan']:,.2f} |",
                      f"| 紧急购电费用（元） | {metrics['emergency_cost_yuan']:,.2f} |",
                      f"| 充电量（千瓦时） | {metrics['charge_energy_kwh']:,.2f} |",
                      f"| 放电量（千瓦时） | {metrics['discharge_energy_kwh']:,.2f} |", ""]
        lines += ["工作簿发布前已与求解数值核对，检验信息保存在运行结果中。可在项目根目录执行：", "", "```powershell",
                  f'uv run python main.py validate --input "{os.path.relpath(folder, PROJECT_ROOT).replace(chr(92), chr(47))}"', "```", "",
                  "本命令检查文件哈希、工作簿汇总和可恢复的储能状态；原计算批次的独立统计检验不等于对未来收益的保证。", ""]
        for name in images:
            lines += [f"![{title}结果图]({name})", ""]
        (folder / "README.md").write_text("\n".join(lines), encoding="utf-8")
        for name in set(previous_images) - set(images):
            if Path(name).name == name and Path(name).suffix == ".png":
                (folder / name).unlink(missing_ok=True)
        delivered.append(folder)
        print(output_text, end="")
    if update_assets:
        asset_folder = ASSETS_DIR / "results"
        asset_folder.mkdir(parents=True, exist_ok=True)
        for picture in figures.glob("*.png"):
            if picture.resolve() != (asset_folder / picture.name).resolve():
                shutil.copy2(picture, asset_folder / picture.name)
    return delivered
