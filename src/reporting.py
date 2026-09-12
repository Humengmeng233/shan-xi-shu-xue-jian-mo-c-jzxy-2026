"""生成结果图表与模型说明。"""

import textwrap

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .causal_scenarios import CausalScenarioFactory
from .visualization import configure_matplotlib
from .paths import figure_directory


def _save(fig, path, caption):
    lines = textwrap.wrap(caption, width=64)
    fig.text(0.08, 0.025, "\n".join(lines), ha="left", va="bottom", fontsize=10, color="#334155")
    fig.tight_layout(rect=(0.02, 0.07 + 0.022 * len(lines), 0.98, 0.97))
    fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)


def create_figures(output, data, q1, info, modes, config):
    configure_matplotlib()
    figures = figure_directory(output)
    figures.mkdir(parents=True, exist_ok=True)
    captions = []

    def save(fig, filename, caption):
        _save(fig, figures / filename, caption)
        captions.append((filename, caption))

    hour = np.arange(144) / 6
    blue, green, orange, red = "#28649A", "#2E8B70", "#D58A32", "#C44E52"
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for col, label, color in (
        ("load_energy_kwh", "负载", "#718096"),
        ("pv_forecast_energy_kwh", "光伏", green),
        ("purchase_kwh", "计划购电", blue),
    ):
        axes[0].plot(hour, q1[col], label=label, color=color)
    axes[0].bar(hour, q1.charge_kwh, width=0.14, label="充电", color=orange, alpha=0.6)
    axes[0].bar(
        hour, -q1.discharge_kwh, width=0.14, label="放电（负向展示）", color=red, alpha=0.65
    )
    axes[0].set(title="问题1｜典型日最优能量调度", ylabel="时段电量（kWh / 10分钟）")
    axes[1].plot(np.arange(145) / 6, np.r_[6000.0, q1.storage_kwh], label="储电量", color=blue)
    axes[1].axhline(1200, color=red, ls="--", label="SOC下限1200 kWh")
    axes[1].axhline(10800, color=orange, ls="--", label="SOC上限10800 kWh")
    axes[1].set(
        xlabel="时刻（小时）", ylabel="储电量（kWh）", xlim=(0, 24), xticks=np.arange(0, 25, 4)
    )
    for ax in axes:
        ax.legend(ncol=3, fontsize=9)
        ax.grid(alpha=0.2)
    first = info["question1"]
    saving = 100 * (1 - first["cost_yuan"] / first["baseline_cost_yuan"])
    save(
        fig,
        "01_typical_dispatch.png",
        f"图1：典型日购电成本由无储能基线的{first['baseline_cost_yuan']:,.2f}元降至{first['cost_yuan']:,.2f}元，下降{saving:.2f}%；末态回到6000 kWh。",
    )

    if not modes:
        return captions
    day = min(78, 30 + len(modes["Q2"].dates))
    index = day - 31
    factory = CausalScenarioFactory(data, config, info["solver_parameters"]["scenarios"])
    forecast = factory.get(day)
    actual = data.net_kwh[day]
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    axes[0].fill_between(
        hour,
        forecast.lower,
        forecast.upper,
        color=blue,
        alpha=0.15,
        label="历史分割校准带（名义90%）",
    )
    axes[0].plot(hour, actual, color="#374151", label="实际净负荷")
    axes[0].plot(hour, forecast.center, color=blue, label="日前组合预测")
    axes[0].set(
        title=f"问题2｜{data.dates[day]:%Y-%m-%d}日前预测与执行",
        ylabel="净负荷电量（kWh / 10分钟）",
    )
    axes[1].plot(hour, modes["Q2"].original_plan_kwh[index], label="原始计划", color=blue)
    axes[1].plot(hour, modes["Q2"].used_plan_kwh[index], label="实际使用计划电", color=green)
    axes[1].bar(hour, modes["Q2"].emergency_kwh[index], width=0.14, label="紧急购电", color=red)
    axes[1].set(
        xlabel="时刻（小时）",
        ylabel="时段电量（kWh / 10分钟）",
        xlim=(0, 24),
        xticks=np.arange(0, 25, 4),
    )
    for ax in axes:
        ax.legend(fontsize=9, ncol=2)
        ax.grid(alpha=0.2)
    coverage = 100 * np.mean((actual >= forecast.lower) & (actual <= forecast.upper))
    mae = float(np.abs(actual - forecast.center).mean())
    save(
        fig,
        "02_forecast_execution.png",
        f"图2：代表日净负荷预测MAE为{mae:.2f} kWh/时段，校准带实际覆盖{coverage:.2f}%。预测带仅作诊断；购电量来自场景DRO求解，不等于预测上界。",
    )

    summary = pd.DataFrame(info["models"]).set_index("model")
    for k, (names, title, filename) in enumerate(
        (
            (("Q2", "Q3"), "问题2与问题3｜固定电价成本构成", "03_fixed_costs.png"),
            (("Q4-2", "Q4-3"), "问题4｜变动电价成本构成", "04_variable_costs.png"),
        ),
        start=3,
    ):
        fig, axes = plt.subplots(1, 2, figsize=(13, 7), gridspec_kw={"width_ratios": [1, 1.7]})
        bottom = np.zeros(2)
        for col, label, color in (
            ("plan_cost_yuan", "原始计划费用", blue),
            ("adjustment_cost_yuan", "调整净费用", orange),
            ("emergency_cost_yuan", "紧急购电费用", red),
        ):
            values = summary.loc[list(names), col].to_numpy() / 1e4
            axes[0].bar(names, values, bottom=bottom, label=label, color=color)
            bottom += values
        for x, value in enumerate(bottom):
            axes[0].text(x, value, f"{value:,.1f}", ha="center", va="bottom")
        axes[0].set(
            title=title, ylabel="累计实际费用（万元）", xlabel="策略", ylim=(0, max(bottom) * 1.15)
        )
        axes[0].legend(fontsize=9, loc="lower right")
        for name, color in zip(names, [blue, orange], strict=False):
            daily = modes[name].daily.copy()
            daily["month"] = pd.to_datetime(daily.date).dt.month
            monthly = daily.groupby("month").total_cost_yuan.sum() / 1e4
            axes[1].plot(monthly.index, monthly.values, marker="o", label=name, color=color)
        axes[1].set(
            title="按月实际总费用", xlabel="月份", ylabel="月费用（万元）", xticks=monthly.index
        )
        axes[1].legend()
        axes[1].grid(alpha=0.2)
        difference = 100 * (
            1 - summary.loc[names[1], "total_cost_yuan"] / summary.loc[names[0], "total_cost_yuan"]
        )
        direction = "下降" if difference >= 0 else "上升"
        save(
            fig,
            filename,
            f"图{k}：{names[1]}相比{names[0]}累计费用{direction}{abs(difference):.2f}%。两者的信息集和滚动决策机制同时不同，不能把差额全部归因于价值门控。",
        )

    fig, axes = plt.subplots(1, 2, figsize=(13, 7))
    for name, color in (("Q3", blue), ("Q4-3", orange)):
        counts = (
            modes[name].gates.groupby("release_time").accepted.sum()
            if len(modes[name].gates)
            else pd.Series(dtype=float)
        )
        position = np.arange(len(counts)) + (-0.18 if name == "Q3" else 0.18)
        axes[0].bar(position, counts.to_numpy(), width=0.35, label=name, color=color)
    axes[0].set(
        title="日内购电调整门控",
        xlabel="预报发布时间",
        ylabel="接受调整次数",
        xticks=np.arange(3),
        xticklabels=["06:00", "12:00", "18:00"],
    )
    axes[0].legend()
    axes[0].grid(axis="y", alpha=0.2)
    no_update = [name for name in summary.index if "no-update" in name]
    if no_update:
        labels = ["Q3-no-update", "Q3", "Q4-3-no-update", "Q4-3"]
        axes[1].bar(
            np.arange(4),
            summary.loc[labels, "total_cost_yuan"] / 1e4,
            color=[green, blue, green, orange],
            label="累计实际费用",
        )
        axes[1].set(
            xticks=np.arange(4),
            xticklabels=[
                "固定价\n不更新",
                "固定价\n门控滚动",
                "变动价\n不更新",
                "变动价\n门控滚动",
            ],
            ylabel="累计实际费用（万元）",
            xlabel="相同00:00信息，是否进行日内更新",
            title="信息匹配的运行基线",
        )
        axes[1].legend()
        fixed_gain = 100 * (
            1
            - summary.loc["Q3", "total_cost_yuan"] / summary.loc["Q3-no-update", "total_cost_yuan"]
        )
        variable_gain = 100 * (
            1
            - summary.loc["Q4-3", "total_cost_yuan"]
            / summary.loc["Q4-3-no-update", "total_cost_yuan"]
        )
        caption = f"图5：相同00:00预报信息下，门控滚动相对不做日内更新的费用变化为固定价{-fixed_gain:+.2f}%、变动价{-variable_gain:+.2f}%。该对比同时包含日内新预报、储能重优化和购电门控的作用。"
    else:
        labels = ["Q2", "Q3", "Q4-2", "Q4-3"]
        axes[1].bar(
            labels,
            summary.loc[labels, "emergency_energy_kwh"] / 1e3,
            color=[green, blue, green, orange],
            label="紧急购电",
        )
        axes[1].set(title="紧急购电量", xlabel="策略", ylabel="紧急购电量（千kWh）")
        axes[1].legend()
        caption = "图5：门控次数反映购电计划更新频率；右图比较实际紧急购电量。次数较多本身不等于经济性更好。"
    save(fig, "05_gate_and_baseline.png", caption)
    return captions


def create_question_figures(output, data, q1, info, modes, config):
    """绘制单问结果，不依赖其他问题的计算输出。"""
    captions = create_figures(output, data, q1, info, {}, config)
    for name, mode in modes.items():
        fig, axes = plt.subplots(2, 1, figsize=(12, 8))
        hours = np.arange(144) / 6
        axes[0].plot(hours, mode.used_plan_kwh[0], label="实际使用计划电")
        axes[0].plot(hours, mode.emergency_kwh[0], label="紧急购电")
        axes[0].set(title=f"{name}｜首日执行", xlabel="时刻（小时）", ylabel="时段电量（千瓦时）")
        axes[0].legend()
        axes[1].plot(pd.to_datetime(mode.daily.date), mode.daily.total_cost_yuan)
        axes[1].set(title="每日总费用", xlabel="日期", ylabel="费用（元）")
        caption = f"{name}：计算 {len(mode.dates)} 天，总费用 {mode.daily.total_cost_yuan.sum():,.2f} 元。"
        filename = f"{name}_dispatch.png"
        _save(fig, figure_directory(output) / filename, caption)
        captions.append((filename, caption))
    return captions
