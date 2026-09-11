"""项目目录定义。"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
TEMPLATE_DIR = DATA_DIR / "templates"
INTERIM_DATA_DIR = DATA_DIR / "interim"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
FEATURE_DATA_DIR = DATA_DIR / "feature_store"
RESULT_DIR = PROJECT_ROOT / "result"
DOCS_DIR = PROJECT_ROOT / "docs"


def project_path(path: str | Path) -> Path:
    """将相对路径解析为项目根目录下的绝对路径。"""

    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value

ASSETS_DIR = PROJECT_ROOT / "assets"


def figure_directory(output: Path) -> Path:
    """求解期间的临时图表目录，交付时复制到结果包。"""
    return output / "figures"
