import unittest

import numpy as np
import pandas as pd

from quantlab.data.portal import MarketPanel
from quantlab.strategy import create_strategy


def make_panel(growth: dict[str, float], periods: int = 320) -> MarketPanel:
    dates = pd.bdate_range("2020-01-01", periods=periods)
    close = pd.DataFrame(
        {
            ticker: 100.0 * np.exp(rate * np.arange(periods))
            for ticker, rate in growth.items()
        },
        index=dates,
    )
    return MarketPanel(
        as_of=dates[-1],
        close=close,
        open=close,
        high=close,
        low=close,
        volume=close * 0,
    )


class TestMomentum(unittest.TestCase):
    def test_ranks_by_12_1_return(self):
        panel = make_panel({"AAA": 0.0003, "BBB": 0.0012, "CCC": 0.0006})
        strategy = create_strategy("momentum", top_n=1)
        targets = strategy.generate_targets(panel)
        self.assertEqual(list(targets), ["BBB"])
        self.assertAlmostEqual(sum(targets.values()), 1.0)

    def test_weights_respect_cap(self):
        panel = make_panel({f"S{i}": 0.0005 + i * 0.0001 for i in range(8)})
        strategy = create_strategy("momentum", top_n=3)
        targets = strategy.generate_targets(panel)
        self.assertEqual(len(targets), 3)
        self.assertTrue(all(w <= strategy.max_weight + 1e-9 for w in targets.values()))
        self.assertLessEqual(sum(targets.values()), 1.0)

    def test_trend_filter_moves_to_cash(self):
        # 先涨后暴跌，价格跌破 200 日均线 -> 空仓
        periods = 320
        dates = pd.bdate_range("2020-01-01", periods=periods)
        prices = np.concatenate([100 * np.exp(0.001 * np.arange(280)),
                                 100 * np.exp(0.001 * 279) * np.exp(-0.02 * np.arange(40))])
        close = pd.DataFrame({"AAA": prices}, index=dates)
        panel = MarketPanel(
            as_of=dates[-1], close=close, open=close, high=close, low=close,
            volume=close * 0,
        )
        targets = create_strategy("momentum", top_n=1).generate_targets(panel)
        self.assertEqual(targets, {})


class TestMeanReversion(unittest.TestCase):
    def test_selects_oversold(self):
        periods = 320
        dates = pd.bdate_range("2020-01-01", periods=periods)
        aaa = 100 * np.exp(0.0015 * np.arange(periods))
        bbb = 100 * np.exp(0.0015 * np.arange(periods))
        bbb[-5:] *= np.array([0.98, 0.96, 0.94, 0.92, 0.90])
        ccc = 100 * np.exp(0.0015 * np.arange(periods))
        close = pd.DataFrame({"AAA": aaa, "BBB": bbb, "CCC": ccc}, index=dates)
        panel = MarketPanel(
            as_of=dates[-1], close=close, open=close, high=close, low=close,
            volume=close * 0,
        )
        targets = create_strategy("mean_reversion", top_n=1).generate_targets(panel)
        self.assertEqual(list(targets), ["BBB"])


class TestOtherStrategies(unittest.TestCase):
    def test_dual_ma_and_composite_return_valid_weights(self):
        panel = make_panel({f"S{i}": 0.0004 + i * 0.0002 for i in range(6)})
        for name in ("dual_ma", "composite"):
            targets = create_strategy(name, top_n=3).generate_targets(panel)
            self.assertTrue(targets, name)
            self.assertLessEqual(sum(targets.values()), 1.0 + 1e-9)
            self.assertTrue(all(w > 0 for w in targets.values()))

    def test_buy_hold_equal_weight(self):
        panel = make_panel({"AAA": 0.0001, "BBB": 0.0002, "CCC": 0.0003})
        targets = create_strategy("buy_hold").generate_targets(panel)
        self.assertEqual(len(targets), 3)
        self.assertAlmostEqual(sum(targets.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
