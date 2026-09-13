"""命令分派与参数隔离测试。"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import main


class TestCommandLine(unittest.TestCase):
    def test_question_command_forwards_scope(self):
        """分问入口附加正确的求解范围。"""
        for number in range(1, 5):
            with self.subTest(question=number):
                def command():
                    self.assertEqual(sys.argv[-2:], ["--question", str(number)])
                with patch.object(sys, "argv", ["main.py", f"q{number}", "--days", "1"]), patch(
                    "main.import_module", return_value=SimpleNamespace(main=command)
                ):
                    self.assertEqual(main(), 0)

    def test_subcommand_help_and_argument_restoration(self):
        """子命令收到帮助选项，退出后恢复调用方参数。"""
        original = ["main.py", "solve", "--help"]

        def command():
            self.assertEqual(sys.argv, ["main.py solve", "--help"])
            raise SystemExit(0)

        with patch.object(sys, "argv", original), patch(
            "main.import_module", return_value=SimpleNamespace(main=command)
        ) as importer:
            with self.assertRaises(SystemExit) as result:
                main()
            self.assertEqual(result.exception.code, 0)
            self.assertIs(sys.argv, original)
            importer.assert_called_once_with(".solver", package="src")
