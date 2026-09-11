"""命令行帮助的中文格式。"""

import argparse
import sys


class ArgumentParser(argparse.ArgumentParser):
    """统一帮助标题、用法前缀与退出提示。"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置参数"
        self._optionals.title = "可选参数"
        self.add_argument("-h", "--help", action="help", help="显示帮助并退出")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"参数错误：{message}\n")
