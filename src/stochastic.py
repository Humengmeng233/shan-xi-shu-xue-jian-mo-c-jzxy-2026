"""含时序储能控制的有限支撑 Wasserstein 均值-CVaR 模型。

该模型是多阶段问题的受限策略近似：下一次求解前，各场景共用购电、充电、放电和
储电量决策。运输距离模糊集只定义在给定支撑集上，不扩展到整个 144 维空间。
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .model_config import ModelConfig
from .optimization import DispatchResult


@dataclass
class StochasticPlan:
    q: np.ndarray
    charge: np.ndarray
    discharge: np.ndarray
    storage: np.ndarray
    objective: float
    scenario_cost: np.ndarray
    scenario_emergency: np.ndarray
    residual: float
    used_milp: bool
    mip_gap: float


def transport_distance(net: np.ndarray, prices: np.ndarray) -> np.ndarray:
    """使用给定场景计算无量纲日尺度 L1 距离。"""
    net_scale = max(float(np.std(net, axis=0).mean()), 1.0)
    price_scale = max(float(np.std(prices, axis=0).mean()), 0.01)
    distance = np.abs(net[:, None, :] - net[None, :, :]).mean(axis=2) / net_scale
    distance += np.abs(prices[:, None, :] - prices[None, :, :]).mean(axis=2) / price_scale
    return distance


def solve_stochastic_plan(
    net: np.ndarray,
    prices: np.ndarray,
    probability: np.ndarray,
    initial: float,
    config: ModelConfig,
    *,
    risk_weight: float = 0.20,
    original: np.ndarray | None = None,
    fixed_q: np.ndarray | None = None,
) -> StochasticPlan:
    """在有限支撑集上最小化最坏分布的期望费用与 CVaR 加权和。

    运输距离对偶约束为 beta_j >= C_s + lambda*xi_s/(1-alpha) - theta*D_js，
    xi_s >= C_s-zeta。计划终端储电量取 6000 kWh，实际执行状态不会被强制重置。
    调增与调减费用相对于原始计划 q0 在模型内计算。线性松弛仅在充放电互补条件
    成立时接受，否则启用二进制模式并求解同一混合整数模型。
    """
    config.validate()
    net, prices, probability = map(lambda x: np.asarray(x, dtype=float), (net, prices, probability))
    if net.ndim != 2 or prices.shape != net.shape:
        raise ValueError("净负荷场景与电价场景必须具有相同的 场景×时段 维度")
    scenarios, horizon = net.shape
    if (
        probability.shape != (scenarios,)
        or np.any(probability <= 0)
        or not np.isclose(probability.sum(), 1)
    ):
        raise ValueError("场景概率必须为正且总和为 1")
    if not np.isfinite(net).all() or not np.isfinite(prices).all() or np.any(prices <= 0):
        raise ValueError("场景数值必须有限，且电价必须为正")
    if not 0 <= risk_weight <= 1:
        raise ValueError("risk_weight 必须在 [0, 1] 内")
    if not config.storage_min_kwh - 1e-6 <= initial <= config.storage_max_kwh + 1e-6:
        raise ValueError("初始储电量超出物理边界")
    initial = float(np.clip(initial, config.storage_min_kwh, config.storage_max_kwh))
    q = np.arange(horizon)
    c, d, e = q + horizon, q + 2 * horizon, q + 3 * horizon
    h = np.arange(scenarios * horizon).reshape(scenarios, horizon) + 4 * horizon
    cursor = 4 * horizon + scenarios * horizon
    up, down = np.arange(horizon) + cursor, np.arange(horizon) + cursor + horizon
    cursor += 2 * horizon
    loss, xi, beta = [np.arange(scenarios) + cursor + k * scenarios for k in range(3)]
    zeta, theta = cursor + 3 * scenarios, cursor + 3 * scenarios + 1
    binary = np.arange(horizon) + theta + 1
    count = int(binary[-1] + 1)
    objective = np.zeros(count)
    objective[beta] = probability
    objective[zeta], objective[theta] = risk_weight, config.robustness_radius
    objective[c] = objective[d] = config.throughput_penalty_yuan_per_kwh
    bounds = [(0.0, None) for _ in range(count)]
    max_flow = config.max_interval_energy_kwh
    for t in range(horizon):
        bounds[c[t]] = bounds[d[t]] = (0.0, max_flow)
        bounds[e[t]] = (config.storage_min_kwh, config.storage_max_kwh)
        bounds[binary[t]] = (0.0, 1.0)
        if fixed_q is not None:
            value = float(max(0.0, fixed_q[t]))
            bounds[q[t]] = (value, value)
        if original is None:
            bounds[up[t]] = bounds[down[t]] = (0.0, 0.0)
        else:
            bounds[down[t]] = (0.0, float(max(0.0, original[t])))
    for index in np.r_[loss, beta, zeta]:
        bounds[index] = (None, None)
    eq_rows, eq_cols, eq_values, eq_rhs = [], [], [], []
    ub_rows, ub_cols, ub_values, ub_rhs = [], [], [], []

    def add(indices, values, rhs, equal=False):
        rows, cols, vals, right = (
            (eq_rows, eq_cols, eq_values, eq_rhs)
            if equal
            else (ub_rows, ub_cols, ub_values, ub_rhs)
        )
        row = len(right)
        indices, values = np.asarray(indices).ravel(), np.asarray(values).ravel()
        rows.extend([row] * len(indices))
        cols.extend(indices.tolist())
        vals.extend(values.tolist())
        right.append(float(rhs))

    for t in range(horizon):
        indices, values = [e[t], c[t], d[t]], [1.0, -config.eta_charge, 1 / config.eta_discharge]
        if t:
            indices.append(e[t - 1])
            values.append(-1.0)
        add(indices, values, initial if t == 0 else 0.0, True)
        # 各支撑轨迹均禁止将储能放电量直接弃置。
        add([d[t], c[t]], [1.0, -1.0], max(0.0, float(net[:, t].min())))
        add([c[t], binary[t]], [1.0, -max_flow], 0.0)
        add([d[t], binary[t]], [1.0, max_flow], max_flow)
        if original is not None:
            add([q[t], up[t], down[t]], [1.0, -1.0, 1.0], original[t], True)
    add([e[-1]], [1.0], config.storage_initial_kwh, True)
    for s in range(scenarios):
        for t in range(horizon):
            add([c[t], d[t], q[t], h[s, t]], [1.0, -1.0, -1.0, -1.0], -net[s, t])
        indices = list(h[s]) + [loss[s]]
        values = list(config.emergency_price_multiplier * prices[s]) + [-1.0]
        constant = 0.0
        if original is None:
            indices += list(q)
            values += list(prices[s])
        else:
            indices += list(up) + list(down)
            values += list(config.adjustment_up_multiplier * prices[s])
            values += list(
                (config.adjustment_down_penalty_multiplier - config.down_refund_ratio) * prices[s]
            )
            constant = float(prices[s] @ original)
        add(indices, values, -constant, True)
        add([loss[s], zeta, xi[s]], [1.0, -1.0, -1.0], 0.0)
    distance = transport_distance(net, prices)
    for j in range(scenarios):
        for s in range(scenarios):
            add(
                [loss[s], xi[s], theta, beta[j]],
                [1.0, risk_weight / (1 - config.risk_alpha), -distance[j, s], -1.0],
                0.0,
            )
    a_eq = coo_matrix((eq_values, (eq_rows, eq_cols)), shape=(len(eq_rhs), count)).tocsr()
    a_ub = coo_matrix((ub_values, (ub_rows, ub_cols)), shape=(len(ub_rhs), count)).tocsr()
    kwargs = dict(A_ub=a_ub, b_ub=ub_rhs, A_eq=a_eq, b_eq=eq_rhs, bounds=bounds, method="highs")
    result = linprog(objective, **kwargs)
    used_milp = False
    if result.success and np.max(np.minimum(result.x[c], result.x[d])) > 1e-6:
        used_milp = True
        integrality = np.zeros(count, dtype=int)
        integrality[binary] = 1
        result = linprog(
            objective, integrality=integrality, options={"mip_rel_gap": 1e-6}, **kwargs
        )
    if not result.success:
        raise RuntimeError(f"有限支撑分布鲁棒优化求解失败：{result.message}")
    values = result.x
    residual = max(
        float(np.max(np.abs(a_eq @ values - eq_rhs))), float(np.max(a_ub @ values - ub_rhs))
    )
    if residual > 1e-4 or np.max(np.minimum(values[c], values[d])) > 1e-5:
        raise RuntimeError(f"求解结果未通过可行性检查：{residual}")
    # 非最坏场景的紧急购电变量可能存在松弛，因此按实际补救量重新计算费用。
    emergency = np.maximum(net + values[c] - values[d] - values[q], 0.0)
    if original is None:
        base = prices @ values[q]
    else:
        delta = values[q] - original
        base = prices @ (
            original
            + config.adjustment_up_multiplier * np.maximum(delta, 0.0)
            + (config.adjustment_down_penalty_multiplier - config.down_refund_ratio)
            * np.maximum(-delta, 0.0)
        )
    scenario_cost = base + config.emergency_price_multiplier * np.sum(prices * emergency, axis=1)
    return StochasticPlan(
        values[q],
        values[c],
        values[d],
        values[e],
        float(result.fun),
        scenario_cost,
        emergency.sum(axis=1),
        residual,
        used_milp,
        float(getattr(result, "mip_gap", 0.0) or 0.0),
    )


def execute_controls(net, q, charge, discharge, initial, config) -> DispatchResult:
    """按当前物理状态执行给定控制量。

    控制过程不读取未来实际净负荷；已购买但未使用的电量不售出。紧急购电可以满足
    计划充电需求，其费用按全量紧急购电计算。
    """
    net, q = np.asarray(net), np.asarray(q)
    used, emergency, c, d, spill, energy = [np.zeros(len(net)) for _ in range(6)]
    state = float(initial)
    for t, demand in enumerate(net):
        c[t] = min(
            max(0.0, charge[t]),
            config.max_interval_energy_kwh,
            max(0.0, (config.storage_max_kwh - state) / config.eta_charge),
        )
        d[t] = min(
            max(0.0, discharge[t]),
            config.max_interval_energy_kwh,
            max(0.0, (state - config.storage_min_kwh) * config.eta_discharge),
            max(0.0, demand),
        )
        required = demand + c[t] - d[t]
        used[t] = min(max(0.0, q[t]), max(0.0, required))
        emergency[t] = max(0.0, required - used[t])
        spill[t] = max(0.0, -required)
        state += config.eta_charge * c[t] - d[t] / config.eta_discharge
        energy[t] = state
    residual = used + emergency + d - c - spill - net
    return DispatchResult(used, emergency, c, d, spill, energy, float(np.max(np.abs(residual))))
