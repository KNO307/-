import unittest

import numpy as np
import pandas as pd

from quantlab.engine import BacktestConfig, BacktestEngine, Broker, compute_metrics, max_drawdown
from quantlab.engine.metrics import monthly_return_table
from quantlab.data import DataPortal
from quantlab.strategy import create_strategy


class FrameProvider:
    name = "test"

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = {k.upper(): v for k, v in frames.items()}

    def fetch(self, ticker, start=None, end=None, refresh=False):
        return self.frames[ticker.upper()].copy()


def make_frames(n=70, start="2021-01-01", tickers=("AAA",)) -> dict[str, pd.DataFrame]:
    dates = pd.bdate_range(start, periods=n)
    frames = {}
    for idx, ticker in enumerate(tickers):
        base = 100.0 + idx * 20
        open_ = base + np.arange(n) * 0.1
        close = open_ + 0.5
        frames[ticker] = pd.DataFrame(
            {
                "open": open_,
                "high": close + 0.2,
                "low": open_ - 0.2,
                "close": close,
                "volume": np.full(n, 1_000_000),
            },
            index=dates,
        )
    spy = frames.get("SPY")
    if spy is None:
        open_ = 300.0 + np.arange(n) * 0.2
        spy = pd.DataFrame(
            {"open": open_, "high": open_ + 1, "low": open_ - 1,
             "close": open_ + 0.5, "volume": np.full(n, 1_000_000)},
            index=dates,
        )
        frames["SPY"] = spy
    return frames


class TamperedProvider(FrameProvider):
    def __init__(self, frames, cut, factor):
        super().__init__(frames)
        self.cut = pd.Timestamp(cut)
        self.factor = factor

    def fetch(self, ticker, start=None, end=None, refresh=False):
        df = super().fetch(ticker).copy()
        mask = df.index >= self.cut
        for col in ("open", "high", "low", "close"):
            df.loc[mask, col] = df.loc[mask, col] * self.factor
        return df


def run(provider, strategy_name="buy_hold", **kwargs):
    config = BacktestConfig(
        tickers=kwargs.pop("tickers", ["AAA"]),
        start="2021-01-01",
        end="2021-04-01",
        **kwargs,
    )
    return BacktestEngine(DataPortal(provider), config).run(create_strategy(strategy_name))


class TestExecution(unittest.TestCase):
    def test_next_open_execution(self):
        frames = make_frames()
        result = run(FrameProvider(frames), commission_bp=0, slippage_bp=0)
        trades = result.trades
        self.assertEqual(len(trades), 1)
        first = trades.iloc[0]
        expected_day = frames["AAA"].index[1]
        self.assertEqual(first["date"], expected_day)
        self.assertAlmostEqual(first["price"], frames["AAA"]["open"].iloc[1], places=4)
        self.assertAlmostEqual(first["shares"] * first["price"], 980_000, delta=500)

    def test_commission_and_slippage(self):
        frames = make_frames()
        result = run(
            FrameProvider(frames), commission_bp=10, slippage_bp=20, cash_buffer=0.0
        )
        first = result.trades.iloc[0]
        expected = frames["AAA"]["open"].iloc[1] * 1.002
        self.assertAlmostEqual(first["price"], expected, places=4)
        self.assertAlmostEqual(first["fee"], first["notional"] * 0.001, places=2)
        self.assertAlmostEqual(result.metrics["total_fees"], first["fee"], places=2)

    def test_broker_cash_and_realized_pnl(self):
        broker = Broker(100_000, commission_bp=10, slippage_bp=0)
        broker.execute(pd.Timestamp("2021-01-04"), {"AAA": 0.5}, {"AAA": 100.0}, cash_buffer=0)
        self.assertEqual(broker.positions["AAA"].shares, 500)
        self.assertAlmostEqual(broker.cash, 49_950.0, places=2)
        broker.mark({"AAA": 100.0})
        broker.execute(pd.Timestamp("2021-01-05"), {}, {"AAA": 110.0}, cash_buffer=0)
        sell = broker.trades[-1]
        self.assertEqual(sell["side"], "SELL")
        self.assertAlmostEqual(sell["realized_pnl"], (110 - 100.1) * 500 - 55, places=2)


class TestNoLookahead(unittest.TestCase):
    def _frames(self):
        rng = np.random.default_rng(42)
        n = 900
        dates = pd.bdate_range("2019-01-01", periods=n)
        frames = {}
        for i, ticker in enumerate(["AAA", "BBB", "CCC", "DDD"]):
            rets = rng.normal(0.0004, 0.012, n)
            close = 100 * np.exp(np.cumsum(rets))
            open_ = close * (1 + rng.normal(0, 0.002, n))
            frames[ticker] = pd.DataFrame(
                {
                    "open": open_,
                    "high": np.maximum(open_, close) * 1.005,
                    "low": np.minimum(open_, close) * 0.995,
                    "close": close,
                    "volume": np.full(n, 1e6),
                },
                index=dates,
            )
        spy = 300 * np.exp(np.cumsum(rng.normal(0.0003, 0.009, n)))
        frames["SPY"] = pd.DataFrame(
            {
                "open": spy, "high": spy * 1.004, "low": spy * 0.996,
                "close": spy, "volume": np.full(n, 1e6),
            },
            index=dates,
        )
        return frames

    def _run(self, provider):
        config = BacktestConfig(
            tickers=["AAA", "BBB", "CCC", "DDD"],
            start="2020-01-01",
            end="2022-06-30",
            commission_bp=1,
            slippage_bp=2,
        )
        return BacktestEngine(DataPortal(provider), config).run(
            create_strategy("momentum", top_n=2)
        )

    def test_future_prices_do_not_change_past_equity(self):
        frames = self._frames()
        base = self._run(FrameProvider(frames))
        cut = pd.Timestamp("2021-06-01")
        tampered = self._run(TamperedProvider(frames, cut, 5.0))

        past_a = base.equity.loc[base.equity.index < cut]
        past_b = tampered.equity.loc[tampered.equity.index < cut]
        self.assertGreater(len(past_a), 100)
        np.testing.assert_allclose(past_a["equity"].to_numpy(), past_b["equity"].to_numpy())

        trades_a = base.trades[base.trades["date"] < cut]
        trades_b = tampered.trades[tampered.trades["date"] < cut]
        self.assertGreater(len(trades_a), 0)
        self.assertEqual(len(trades_a), len(trades_b))
        np.testing.assert_allclose(
            trades_a["price"].to_numpy(), trades_b["price"].to_numpy(), rtol=1e-9
        )

    def test_payload_is_json_serializable(self):
        import json

        result = run(FrameProvider(make_frames()))
        text = json.dumps(result.to_payload(), ensure_ascii=False)
        self.assertIn("metrics", text)
        self.assertGreater(len(text), 100)


class TestMetricsMath(unittest.TestCase):
    def test_total_return_and_drawdown(self):
        equity = pd.Series(
            [100.0, 110.0, 99.0, 108.9],
            index=pd.bdate_range("2021-01-01", periods=4),
        )
        metrics = compute_metrics(equity)
        self.assertAlmostEqual(metrics["total_return"], 0.089, places=6)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.1, places=6)
        self.assertAlmostEqual(max_drawdown(equity), -0.1, places=6)

    def test_cagr_uses_trading_days(self):
        n = 253
        equity = pd.Series(
            np.linspace(100.0, 200.0, n),
            index=pd.bdate_range("2020-01-01", periods=n),
        )
        metrics = compute_metrics(equity)
        self.assertAlmostEqual(metrics["cagr"], 1.0, places=4)

    def test_flat_equity(self):
        equity = pd.Series(
            [100.0] * 50, index=pd.bdate_range("2021-01-01", periods=50)
        )
        metrics = compute_metrics(equity)
        self.assertEqual(metrics["total_return"], 0.0)
        self.assertEqual(metrics["sharpe"], 0.0)
        self.assertEqual(metrics["max_drawdown"], 0.0)

    def test_monthly_table(self):
        equity = pd.Series(
            np.linspace(100.0, 150.0, 260),
            index=pd.bdate_range("2021-01-01", periods=260),
        )
        table = monthly_return_table(equity)
        self.assertIn(1, table.columns)
        self.assertEqual(table.shape[1], 12)


if __name__ == "__main__":
    unittest.main()
