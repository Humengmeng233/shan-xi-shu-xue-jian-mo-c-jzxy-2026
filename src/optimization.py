"""稀疏线性规划与时序调度执行。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .model_config import ModelConfig


@dataclass
class PlanResult:
    purchase_kwh: np.ndarray
    charge_kwh: np.ndarray
    discharge_kwh: np.ndarray
    curtail_kwh: np.ndarray
    storage_kwh: np.ndarray
    objective_yuan: float
    max_balance_residual_kwh: float
    simultaneous_flow_kwh: float
    solver_status: str


@dataclass
class DispatchResult:
    used_plan_kwh: np.ndarray
    emergency_kwh: np.ndarray
    charge_kwh: np.ndarray
    discharge_kwh: np.ndarray
    curtail_kwh: np.ndarray
    storage_kwh: np.ndarray
    max_balance_residual_kwh: float


def _as_vector(values: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(values, dtype=float).reshape(-1)
    if result.size == 0:
        raise ValueError(f"{name} 不能为空")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} 含 NaN 或无穷值")
    return result


def solve_energy_plan(
    net_load_kwh: np.ndarray,
    price_yuan_per_kwh: np.ndarray,
    storage_start_kwh: float,
    config: ModelConfig,
    *,
    terminal_target_kwh: float | None,
) -> PlanResult:
    """求解含风险修正的确定性计划线性规划。

    终端储电量用于约束滚动时域的能量中性。问题 2 至问题 4 在每次优化时取当前储电量
    为终端目标；实际执行结果允许偏离该目标，并将实际状态传递到下一日。
    """

    net = _as_vector(net_load_kwh, "net_load_kwh")
    price = _as_vector(price_yuan_per_kwh, "price_yuan_per_kwh")
    if len(net) != len(price):
        raise ValueError("净负荷和电价长度必须一致")
    if (price < 0.0).any():
        raise ValueError("基础模型不支持负电价；如需支持必须增加计划电弃电变量")
    if not config.storage_min_kwh <= storage_start_kwh <= config.storage_max_kwh:
        raise ValueError("优化初始 SOC 越界")

    horizon = len(net)
    q0 = 0
    c0 = horizon
    d0 = 2 * horizon
    s0 = 3 * horizon
    e0 = 4 * horizon
    variable_count = 5 * horizon

    objective = np.zeros(variable_count, dtype=float)
    objective[q0:c0] = price
    objective[c0:d0] = config.throughput_penalty_yuan_per_kwh
    objective[d0:s0] = config.throughput_penalty_yuan_per_kwh

    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    rhs: list[float] = []

    # 时段能量平衡：购电 + 放电 - 充电 - 弃光 = 净负荷。
    for t in range(horizon):
        row = len(rhs)
        rows.extend([row, row, row, row])
        cols.extend([q0 + t, c0 + t, d0 + t, s0 + t])
        data.extend([1.0, -1.0, 1.0, -1.0])
        rhs.append(float(net[t]))

    # 储能状态同时计入充电效率和放电损耗。
    for t in range(horizon):
        row = len(rhs)
        rows.extend([row, row, row])
        cols.extend([c0 + t, d0 + t, e0 + t])
        data.extend([-config.eta_charge, 1.0 / config.eta_discharge, 1.0])
        if t == 0:
            rhs.append(float(storage_start_kwh))
        else:
            rows.append(row)
            cols.append(e0 + t - 1)
            data.append(-1.0)
            rhs.append(0.0)

    if terminal_target_kwh is not None:
        if not config.storage_min_kwh <= terminal_target_kwh <= config.storage_max_kwh:
            raise ValueError("terminal_target_kwh 越界")
        row = len(rhs)
        rows.append(row)
        cols.append(e0 + horizon - 1)
        data.append(1.0)
        rhs.append(float(terminal_target_kwh))

    a_eq = coo_matrix((data, (rows, cols)), shape=(len(rhs), variable_count), dtype=float).tocsr()
    b_eq = np.asarray(rhs, dtype=float)

    max_flow = config.max_interval_energy_kwh
    pv_surplus = np.maximum(-net, 0.0)
    bounds = (
        [(0.0, None)] * horizon
        + [(0.0, max_flow)] * horizon
        + [(0.0, max_flow)] * horizon
        + [(0.0, float(value)) for value in pv_surplus]
        + [(config.storage_min_kwh, config.storage_max_kwh)] * horizon
    )

    result = linprog(
        objective,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
        options={
            "dual_feasibility_tolerance": 1.0e-7,
            "primal_feasibility_tolerance": 1.0e-7,
        },
    )
    if not result.success:
        raise RuntimeError(f"HiGHS 求解失败：{result.message}")

    values = result.x
    purchase = values[q0:c0]
    charge = values[c0:d0]
    discharge = values[d0:s0]
    curtail = values[s0:e0]
    storage = values[e0:]
    balance = purchase + discharge - charge - curtail - net
    return PlanResult(
        purchase_kwh=purchase,
        charge_kwh=charge,
        discharge_kwh=discharge,
        curtail_kwh=curtail,
        storage_kwh=storage,
        objective_yuan=float(result.fun),
        max_balance_residual_kwh=float(np.max(np.abs(balance))),
        simultaneous_flow_kwh=float(np.minimum(charge, discharge).sum()),
        solver_status=str(result.message),
    )




def adjustment_cost(
    adjusted_plan_kwh: np.ndarray,
    original_plan_kwh: np.ndarray,
    price_yuan_per_kwh: np.ndarray,
    config: ModelConfig,
) -> float:
    adjusted = _as_vector(adjusted_plan_kwh, "adjusted_plan_kwh")
    original = _as_vector(original_plan_kwh, "original_plan_kwh")
    price = _as_vector(price_yuan_per_kwh, "price_yuan_per_kwh")
    increase = np.maximum(adjusted - original, 0.0)
    decrease = np.maximum(original - adjusted, 0.0)
    return float(
        np.sum(
            config.adjustment_up_multiplier * price * increase
            + (config.adjustment_down_penalty_multiplier - config.down_refund_ratio)
            * price
            * decrease
        )
    )
