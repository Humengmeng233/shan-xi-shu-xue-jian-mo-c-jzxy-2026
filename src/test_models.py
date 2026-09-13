"""数值约束与信息集边界回归测试。"""

import unittest
from dataclasses import replace

import numpy as np

from src.dispatch import load_inputs, solve_question1
from src.causal_scenarios import CausalScenarioFactory
from src.model_config import ModelConfig
from src.paths import PROJECT_ROOT
from src.solver import january_initialization
from src.stochastic import execute_controls, solve_stochastic_plan


def discrete_cvar(loss, probability, alpha):
    return min(float(z + probability @ np.maximum(loss - z, 0) / (1 - alpha)) for z in loss)


class TestFiniteSupportOptimization(unittest.TestCase):
    def test_transport_cvar_against_bruteforce(self):
        """对照双支撑分布的解析运输结果与购电量枚举结果。"""
        net = np.array([[10.0], [20.0]])
        price = np.ones_like(net)
        prob = np.array([0.9, 0.1])
        for radius, weight in (
            (0.0, 0.0),
            (0.15, 0.0),
            (0.3, 0.0),
            (0.0, 0.2),
            (0.15, 0.2),
            (0.0, 1.0),
        ):
            config = ModelConfig(robustness_radius=radius)
            plan = solve_stochastic_plan(net, price, prob, 6000.0, config, risk_weight=weight)
            # 标准化距离为 |20-10|/std([10,20]) = 2。
            worst = np.array([1 - min(1.0, 0.1 + radius / 2), min(1.0, 0.1 + radius / 2)])
            candidates = []
            for q in np.linspace(0, 30, 601):
                loss = q + 5 * np.maximum(net[:, 0] - q, 0.0)
                candidates.append(float(worst @ loss + weight * discrete_cvar(loss, worst, 0.8)))
            self.assertAlmostEqual(plan.objective, min(candidates), places=6)
            self.assertLess(plan.residual, 1e-6)

    def test_adjustment_is_inside_optimizer(self):
        config = ModelConfig(robustness_radius=0.0)
        net = np.array([[20.0]])
        plan = solve_stochastic_plan(
            net,
            np.ones_like(net),
            np.ones(1),
            6000.0,
            config,
            original=np.array([10.0]),
            risk_weight=0.0,
        )
        self.assertAlmostEqual(plan.q[0], 20.0, places=6)
        self.assertAlmostEqual(plan.objective, 25.0, places=6)
        refunded = solve_stochastic_plan(
            net,
            np.ones_like(net),
            np.ones(1),
            6000.0,
            replace(config, down_refund_ratio=1.0),
            original=np.array([30.0]),
            risk_weight=0.0,
        )
        self.assertAlmostEqual(refunded.objective, 25.0, places=6)

    def test_execution_is_prefix_causal(self):
        config = ModelConfig()
        net = np.array([100.0, -300.0, 150.0, 200.0])
        q = np.array([50.0, 0.0, 100.0, 300.0])
        c = np.array([0.0, 200.0, 0.0, 100.0])
        d = np.array([20.0, 0.0, 50.0, 0.0])
        first = execute_controls(net, q, c, d, 6000.0, config)
        second = execute_controls(np.r_[net[:2], 9999.0, -9999.0], q, c, d, 6000.0, config)
        np.testing.assert_allclose(first.storage_kwh[:2], second.storage_kwh[:2])
        self.assertLess(first.max_balance_residual_kwh, 1e-8)
        self.assertTrue(np.all(first.curtail_kwh <= np.maximum(-net, 0.0) + 1e-8))

    def test_invalid_parameters_fail(self):
        for config in (
            ModelConfig(risk_alpha=1.0),
            ModelConfig(eta_charge=0.0),
            ModelConfig(down_refund_ratio=2.0),
        ):
            with self.assertRaises(ValueError):
                config.validate()


class TestActualDataCausality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ModelConfig()
        cls.data = load_inputs(PROJECT_ROOT, cls.config)
        cls.factory = CausalScenarioFactory(cls.data, cls.config)

    def test_future_actuals_and_late_forecasts_do_not_change_decision_inputs(self):
        day, release, slot = 78, 1, 36
        altered = replace(
            self.data,
            load_kwh=self.data.load_kwh.copy(),
            net_kwh=self.data.net_kwh.copy(),
            pv_forecast_kwh=self.data.pv_forecast_kwh.copy(),
            variable_price=self.data.variable_price.copy(),
        )
        altered.load_kwh[day, slot:] *= 7
        altered.net_kwh[day, slot:] *= 9
        altered.load_kwh[day + 1 :] *= 11
        altered.net_kwh[day + 1 :] *= 13
        altered.variable_price[day:] *= 4
        altered.pv_forecast_kwh[day, release + 1 :] *= 6
        factory = CausalScenarioFactory(altered, self.config)
        before = self.factory.get(
            day, release, pv_information=True, variable_price=True, unknown_price=True
        )
        after = factory.get(
            day, release, pv_information=True, variable_price=True, unknown_price=True
        )
        for name in ("center", "net", "prices", "probability", "lower", "upper"):
            np.testing.assert_allclose(
                getattr(before, name), getattr(after, name), rtol=0, atol=1e-10
            )
        self.assertLess(before.source_indices.max(), day)

    def test_january_is_explicit_and_continuous(self):
        initial, frame = january_initialization(self.data, self.config)
        self.assertEqual(len(frame), 31 * 144)
        self.assertEqual(initial, frame.storage_kwh.iloc[-1])
        self.assertAlmostEqual(initial, 6000.0)
        residual = (
            frame.used_plan_kwh
            + frame.emergency_kwh
            - frame.curtail_kwh
            - frame.actual_net_load_kwh
        )
        self.assertLess(np.abs(residual).max(), 1e-8)

    def test_question1_bounds_and_cycle(self):
        result, _ = solve_question1(self.data, self.config)
        self.assertAlmostEqual(result.storage_kwh[-1], 6000.0)
        self.assertLess(result.max_balance_residual_kwh, 1e-8)
        self.assertLess(result.simultaneous_flow_kwh, 1e-8)
        self.assertGreaterEqual(result.storage_kwh.min(), 1200.0 - 1e-8)
        self.assertLessEqual(result.storage_kwh.max(), 10800.0 + 1e-8)
        pure, _ = solve_question1(
            self.data, replace(self.config, throughput_penalty_yuan_per_kwh=0.0)
        )
        self.assertAlmostEqual(
            float(self.data.fixed_price @ result.purchase_kwh),
            float(self.data.fixed_price @ pure.purchase_kwh),
            places=6,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


def main():
    """从根目录运行全部回归测试。"""
    suite = unittest.defaultTestLoader.discover(str(PROJECT_ROOT / "src"), pattern="test_*.py", top_level_dir=str(PROJECT_ROOT))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
