"""项目命令行入口。"""

from __future__ import annotations

import sys
from importlib import import_module

from src.arguments import ArgumentParser

COMMANDS = {
    "preprocess": ("preprocessing", "生成求解输入"),
    "features": ("feature_pipeline", "构建特征与数据诊断"),
    "convert": ("convert_data", "导出附件与模板的表格文本"),
    "solve": ("solver", "执行全部问题"),
    "q1": ("solver", "仅求解问题一"),
    "q2": ("solver", "仅求解问题二"),
    "q3": ("solver", "仅求解问题三"),
    "q4": ("solver", "求解问题四的两种策略"),
    "test": ("test_models", "运行回归测试"),
    "audit": ("validation", "核验最终结果文件"),
    "validate": ("validation", "核对最终工作簿、汇总与输入哈希"),
}


def main() -> int:
    parser = ArgumentParser(description="光伏、储能与购电计划优化工具")
    parser.add_argument("command", choices=tuple(COMMANDS), help="需要执行的步骤")
    parser.epilog = "；".join(f"{name}：{label}" for name, (_, label) in COMMANDS.items())
    # 子命令参数交给业务模块，避免顶层入口截获 --help。
    args = parser.parse_args(sys.argv[1:2])
    module_name, _ = COMMANDS[args.command]
    command = import_module(f".{module_name}", package="src").main
    original_argv = sys.argv
    try:
        sys.argv = [f"{original_argv[0]} {args.command}", *original_argv[2:]]
        if args.command.startswith("q"):
            sys.argv += ["--question", args.command[1:]]
        result = command()
    finally:
        sys.argv = original_argv
    return 0 if result is None else result


if __name__ == "__main__":
    raise SystemExit(main())
