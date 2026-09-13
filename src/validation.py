"""核验最终结果包，不生成额外中间文件。"""

import csv
import json
import math

import openpyxl

from .arguments import ArgumentParser
from .delivery import file_hash
from .paths import PROJECT_ROOT, project_path


def close(actual, expected, label, tolerance=1e-4):
    if not math.isfinite(actual) or abs(actual - expected) > tolerance:
        raise ValueError(f"{label}不一致：工作簿 {actual}，摘要 {expected}")


def validate_package(folder):
    record = json.loads((folder / "运行结果.json").read_text(encoding="utf-8"))
    workbook_path = folder / f"{folder.name}.xlsx"
    if file_hash(workbook_path) != record["工作簿SHA256"]:
        raise ValueError(f"{folder.name} 工作簿哈希发生变化")
    for name, expected in record["输入文件哈希"].items():
        if file_hash(PROJECT_ROOT / name.replace("\\", "/")) != expected:
            raise ValueError(f"输入文件发生变化：{name}")
    for name, expected in record["图片SHA256"].items():
        if file_hash(folder / name) != expected:
            raise ValueError(f"结果图片发生变化：{name}")
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=False)
    try:
        tables = [list(sheet.values) for sheet in workbook.worksheets]
    finally:
        workbook.close()
    for table in tables:
        for row in table:
            for value in row:
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("工作簿存在非有限数值")
                if isinstance(value, str) and value.startswith(("#REF!", "#VALUE!", "#DIV/0!", "#NUM!")):
                    raise ValueError("工作簿存在公式错误")
    metrics = record["费用与电量"]
    plan = tables[0]
    if record["模型"] == "Q1":
        if len(plan) != 145:
            raise ValueError("典型日工作簿必须包含144个时段")
        with (PROJECT_ROOT / "data/processed/typical_day_10min.csv").open(encoding="utf-8-sig") as handle:
            prices = [float(row["electricity_price_yuan_per_kwh"]) for row in csv.DictReader(handle)]
        close(sum(float(row[1]) * price for row, price in zip(plan[1:], prices, strict=True)), metrics["cost_yuan"], "典型日费用")
    else:
        if len(plan) != record["计算天数"] + 1:
            raise ValueError("计划表日期行数与计算天数不一致")
        close(sum(float(row[146]) for row in plan[1:]), metrics["plan_cost_yuan"], "原始计划费用")
        rolling = record["模型"] in {"Q3", "Q4-3"}
        count = 2 if rolling else 1
        if rolling:
            close(sum(float(row[146]) for row in tables[1][1:]), metrics["plan_cost_yuan"] + metrics["adjustment_cost_yuan"], "调整后计划费用")
        storage, emergency = tables[count], tables[count + 1]
        if len(storage) != 6 * record["计算天数"] + 1:
            raise ValueError("储能汇总必须包含每日六个时间块")
        close(sum(float(row[2]) for row in storage[1:]), metrics["charge_energy_kwh"], "充电量")
        close(sum(float(row[3]) for row in storage[1:]), metrics["discharge_energy_kwh"], "放电量")
        close(sum(float(row[2]) for row in emergency[1:]), metrics["emergency_energy_kwh"], "紧急购电量")
        parameters = record["模型参数"]
        for index, row in enumerate(storage[1:]):
            charge, discharge, initial, final = map(float, row[2:6])
            close(final, initial + parameters["eta_charge"] * charge - discharge / parameters["eta_discharge"], "四小时储能状态")
            if index:
                close(initial, float(storage[index][5]), "时间块衔接")
        close(metrics["plan_cost_yuan"] + metrics["adjustment_cost_yuan"] + metrics["emergency_cost_yuan"], metrics["total_cost_yuan"], "总费用")
    return record


def main():
    parser = ArgumentParser(description="核验最终工作簿、汇总数值、图片和输入哈希")
    parser.add_argument("--input", default="result", help="结果根目录或单个结果包")
    args = parser.parse_args()
    folder = project_path(args.input)
    packages = [folder] if (folder / "运行结果.json").exists() else sorted(folder.glob("result*/"))
    if not packages:
        parser.error("没有找到结果包，请先执行求解")
    for package in packages:
        record = validate_package(package)
        print(f"{package.name}：工作簿、汇总、图片与输入哈希检查通过；计算 {record['计算天数']} 天")
    print("十分钟物理约束在求解发布前核验；本命令从最终文件复核可恢复的费用、电量与四小时储能状态。")
