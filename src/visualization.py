"""图表中文字体与显示格式。"""

import matplotlib.pyplot as plt


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.labelsize": 10,
            "legend.fontsize": 9,
            "figure.dpi": 130,
            "savefig.dpi": 180,
        }
    )
