<a name="q2"></a>

[项目首页](../README.md) · [章节目录](../README.md#chapters)

# 问题二：年度日前计划

[模型构建](#q2-section-1) · [模型求解](#q2-section-2) · [结果分析](#q2-section-3) · [数值检验](#q2-section-4)

[返回主要结果速览](../README.md#results)

**核心实现。** [causal_scenarios.py](../src/causal_scenarios.py) 的 `CausalScenarioFactory` 负责预测、区间校准与场景缩减；[stochastic.py](../src/stochastic.py) 的 `solve_stochastic_plan` 求解计划，`execute_controls` 按已观察状态执行。

```powershell
uv run python main.py q2
```

正式结果：[result2.xlsx](../result/result2/result2.xlsx)。

<a name="q2-section-1"></a>

## 模型构建

### 模型原理

问题2的关键在于：每日0:00尚不能获知当天真实负荷与光伏出力，计划购电不足会产生5倍电价的紧急购电，而计划购电过多仍需承担相应费用。因此，本文将其建模为两阶段不确定决策，并构建**因果组合预测—有限支持分布鲁棒调度模型**：第一阶段依据历史信息确定计划购电和储能基准动作，第二阶段在实际净负荷到达后以紧急购电补足缺口。

本文直接预测净负荷 $`N_t=L_t-G_t`$，以保留负荷与光伏的抵消关系。预测中心由“近期同一时段中位数”和“历史同星期中位数”加权得到，权重根据此前预测误差动态更新；随后使用彼此分离的历史拟合样本与校准样本构造保形预测区间，并由历史整日残差生成净负荷轨迹，经场景缩减后形成有限支持集。

在每次日前求解中，计划购电量及储能充放电量对所有场景保持一致，紧急购电量随场景变化。因此，该模型属于共同储能动作下的有限场景近似，而非具有任意场景自适应储能策略的完整多阶段模型。目标是在有限支持的Wasserstein模糊集内，最小化最坏分布下的期望购电费用与CVaR风险。

### 核心公式与推导逻辑

**因果组合点预测**：

设 $`\widetilde N_{t}^{(r)}`$ 为最近7天同一时段净负荷的中位数，$`\widetilde N_{t}^{(w)}`$ 为历史同星期日期对应时段的中位数，则预测中心为

```math
\widehat N_t=w_r\widetilde N_t^{(r)}+w_w\widetilde N_t^{(w)},
\qquad w_r+w_w=1,
```

其中权重按此前滚动窗口内两类预测的平均绝对误差倒数确定：

```math
w_j=\frac{[\max(\mathrm{MAE}_j,1)]^{-1}}
{[\max(\mathrm{MAE}_r,1)]^{-1}+[\max(\mathrm{MAE}_w,1)]^{-1}},
\qquad j\in\{r,w\}.
```

**分割保形校准**：

将最近56天历史残差按时间顺序划分为拟合集 $`\mathcal I_f`$ 和校准集 $`\mathcal I_c`$。令 $`\xi=(1-\alpha_c)/2`$，在拟合集上计算 $`a_t=Q_{\xi}(r_{i,t})`$ 与 $`b_t=Q_{1-\xi}(r_{i,t})`$，并对校准样本定义非一致性分数

```math
s_{i,t}=\max\{a_t-r_{i,t},\ r_{i,t}-b_t\},
\qquad i\in\mathcal I_c.
```

令 $`\gamma_t=\max\{0,Q_{\alpha_c}^{\mathrm{fin}}(s_{i,t})\}`$，其中 $`Q_{\alpha_c}^{\mathrm{fin}}`$ 表示采用秩 $`\lceil(|\mathcal I_c|+1)\alpha_c\rceil`$ 的有限样本经验分位数，则名义覆盖率为 $`\alpha_c`$ 的预测区间为

```math
\left[\widehat N_t+a_t-\gamma_t,\;
\widehat N_t+b_t+\gamma_t\right].
```

该区间只用于预测校准与诊断；优化场景由预测中心叠加历史整日残差构造，不直接取区间上界作为计划购电量。

**两阶段分布鲁棒目标**：

设场景 $`\omega`$ 下的结算费用为

```math
C^\omega=\sum_{t=1}^{T}\left(p_tq_t^0+5p_th_t^\omega\right),
```

则有限支持分布鲁棒模型为

```math
\min_{q^0,c,d,E,\{x^\omega,h^\omega,s^\omega\}_{\omega=1}^{S}}
\ \sup_{\mathbb P\in\mathcal P_\varepsilon}
\left\{\mathbb E_{\mathbb P}[C^\omega]
+\lambda\mathrm{CVaR}_{\alpha,\mathbb P}(C^\omega)\right\}
+\kappa\sum_t(c_t+d_t).
```

其中 $`\mathcal P_\varepsilon`$ 是以缩减后经验分布为中心、定义在同一有限场景支持集上的Wasserstein模糊集：

```math
\mathcal P_\varepsilon=\{\mathbb P:W_1(\mathbb P,\hat{\mathbb P}_N)\le\varepsilon\}.
```

场景间距离采用归一化日内平均绝对距离；在问题4的未知价格情形下，有

```math
D_{ij}=\frac{1}{T\sigma_N}\sum_{t=1}^{T}|N_{i,t}-N_{j,t}|
+\frac{1}{T\sigma_p}\sum_{t=1}^{T}|p_{i,t}-p_{j,t}|,
```

其中 $`\sigma_N`$ 与 $`\sigma_p`$ 分别为场景集中净负荷和价格的尺度参数；固定电价或价格已知时，价格距离项为0。Wasserstein距离由场景概率之间的最小运输成本定义。

CVaR 项通过 Rockafellar–Uryasev 公式线性化：

```math
\mathrm{CVaR}_{\alpha,\mathbb P}(C^\omega)
=\min_{\zeta}\left\{\zeta+\frac{1}{1-\alpha}
\mathbb E_{\mathbb P}\bigl[(C^\omega-\zeta)_+\bigr]\right\}.
```

**逐时段能量平衡与储能动态（场景 $`\omega`$ 下）**：

```math
x_t^\omega+h_t^\omega+d_t
=N_t^\omega+c_t+s_t^\omega,
```

```math
E_t=E_{t-1}+\eta_c c_t-\frac{d_t}{\eta_d},
\qquad 1200\le E_t\le10800.
```

**实际取用计划电约束**：

```math
0\le x_t^\omega\le q_t^0,\qquad
h_t^\omega\ge0,\qquad
0\le s_t^\omega\le\max\{-N_t^\omega,0\}.
```

上式采用净负荷口径，其中 $`s_t^\omega`$ 表示无法消纳的余电。上式将“按计划付费”与“实际取用”严格区分：目标函数按 $`q_t^0`$ 计费，但已付款而未使用的计划电量不强行注入系统。$`c_t,d_t,E_t`$ 在同一次日前求解中对各场景相同，$`x_t^\omega,h_t^\omega,s_t^\omega`$ 为场景相关的平衡变量。为避免日末储能价值缺失，每次规划设置终端参考约束 $`E_T=6000`$ kWh；实际执行不强制回到该值，而是将日末储电量连续传递至下一日。

### 有限支撑运输约束与代码实现

设 $`\gamma_{j\omega}`$ 把经验场景 $`j`$ 的概率质量运输到场景 $`\omega`$，满足

```math
\sum_\omega\gamma_{j\omega}=\pi_j,\qquad
\sum_{j,\omega}D_{j\omega}\gamma_{j\omega}\le\varepsilon,\qquad
\gamma_{j\omega}\ge0.
```

在给定支撑集上，对运输问题取线性规划对偶，可用以下有限维目标与约束实现最坏期望及风险项：

```math
\min\ \lambda\zeta+\varepsilon\theta+\sum_j\pi_j\beta_j
+\kappa\sum_t(c_t+d_t),
```

```math
\beta_j\ge C^\omega+\frac{\lambda\xi_\omega}{1-\alpha}-\theta D_{j\omega},
\quad\xi_\omega\ge C^\omega-\zeta,\quad\xi_\omega\ge0,\quad\theta\ge0.
```

这里 $`\beta_j`$ 和 $`\zeta`$ 为自由变量。半径限制的是标准化轨迹间的运输成本，不是预测误差百分比；$`\varepsilon=0`$ 对应选定支撑上的经验分布风险模型。共同储能动作和有限支撑近似仍然存在，不能把该对偶形式解释为完整多阶段模型的全局最优证明。

<a name="q2-section-2"></a>

## 模型求解

求解时以1月数据完成历史初始化；自2月1日起，每日0:00更新组合预测权重，并利用最近56天残差进行分割保形校准。历史轨迹经确定性场景缩减后保留8条代表轨迹，再以 $`\varepsilon=0.15`$ 构造有限支持Wasserstein模糊集，取CVaR风险权重 $`\lambda=0.2`$、置信水平 $`\alpha=0.8`$ 求解计划购电和共同储能动作。实际执行时按当期净负荷修正计划电取用量并记录紧急购电，同时保持跨日储电量连续。

全年 334 日回测结果如下：原始计划费 15,706,243.96 元，紧急购电费 993,419.64 元，实际总费 16,699,663.60 元；紧急购电总量 255,870.79 kWh；日均紧急购电量 766.08 kWh；单日最大紧急购电量 12,686.74 kWh；无紧急购电天数 73 天。日总费用均值 49,998.99 元、中位数 51,159.89 元、标准差 13,075.87 元。

代表日的预测区间覆盖率为 97.92%，平均绝对误差 39.09 kWh/时段，表明校准区间在考核期具备较高的实测覆盖水平。

![净负荷预测与实际执行](../assets/results/02_forecast_execution.png)

*图2：代表日净负荷预测 MAE 为 39.09 kWh/时段，校准带实际覆盖 97.92%。预测带仅作诊断；购电量来自场景 DRO 求解，不等于预测上界。*

<a name="q2-section-3"></a>

## 结果分析

### 费用与电量变化

问题2的平均每日购电费用为 49,998.99 元，全年累计 16,699,663.60 元；紧急购电累计 255,870.79 kWh，占外购总电量的比例较低（每 10 分钟时段平均紧急量约 5.32 kWh，对全日购电量影响有限），但其费用占实际总费约 5.95%。这意味着**少数日期的大额紧急购电对总费用的贡献不可忽视**——紧急购电量最大的 34 天（约占考核期 10%）贡献了紧急购电总量的 45.53%。

### 机制解释与比较边界

日总费用变异系数 $`CV=\sigma_C/\bar C=26.15\%`$，方差为 170,978,444.85 元²。日净负荷电量与日总费用的 Pearson 相关系数为 0.930，表明能量需求较高的日期通常对应较高费用；但该相关未剔除季节性、电价与预测误差等因素，不能解释为净负荷单独造成了相同比例的费用变化。预测区间平均覆盖率 90.33% 接近名义 90%，但不意味着每一天都达到 90%，时间序列依赖性仍是限制条件。

<a name="q2-section-4"></a>

## 数值检验

| 模式 | 能量平衡最大残差（千瓦时） | 状态递推最大残差（千瓦时） | 费用重算最大误差（元） | 违规时段 |
|---|---:|---:|---:|---:|
| Q2 | 3.98e-13 | 4.49e-12 | 2.18e-11 | 0 |

各模式均核对 48,096 个时段，储电量位于 1,200～10,800 千瓦时，跨日状态误差为零。以上数值采用独立回读检验口径，与求解器内部浮点残差可能有微小差异。

日前预测平均绝对误差为 80.59 千瓦时，名义 90% 区间的实际覆盖率为 90.33%。季节朴素预测的平均绝对误差为 54.63 千瓦时，因此覆盖率接近名义水平不意味着点预测优于简单基线。

---

[返回本章顶部](#q2) · [返回项目首页](../README.md)
