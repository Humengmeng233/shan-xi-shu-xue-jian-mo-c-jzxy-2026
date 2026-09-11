"""将原始工作簿和结果模板转换为便于检查的 CSV。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .paths import INTERIM_DATA_DIR, RAW_DATA_DIR, TEMPLATE_DIR

SOURCE_DIRECTORIES = {
    "raw": RAW_DATA_DIR,
    "templates": TEMPLATE_DIR,
}


def convert_workbook(source: Path, destination: Path) -> int:
    """按工作表拆分一个工作簿，返回生成的 CSV 数量。"""

    workbook = pd.ExcelFile(source)
    for sheet_name in workbook.sheet_names:
        frame = pd.read_excel(source, sheet_name=sheet_name)
        output = destination / f"{source.stem}__{sheet_name}.csv"
        frame.to_csv(output, index=False, encoding="utf-8-sig")
        print(f"已生成 {output.name}，尺寸={frame.shape}")
    return len(workbook.sheet_names)


def main() -> None:
    for category, source_directory in SOURCE_DIRECTORIES.items():
        destination = INTERIM_DATA_DIR / category
        destination.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_directory.glob("*.xlsx")):
            count = convert_workbook(source, destination)
            print(f"已处理 {source.name}，生成 {count} 个 CSV")


if __name__ == "__main__":
    main()
