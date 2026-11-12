import unittest
from datetime import date

import numpy as np
import pandas as pd

from quantlab.data import DataPortal, normalize_ohlcv
from quantlab.data.base import NoDataError


def make_frame(n: int = 60, start: str = "2021-01-01", price: float = 100.0):
    dates = pd.bdate_range(start, periods=n)
    prices = price + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "Open": prices - 0.5,
            "High": prices + 1.0,
            "Low": prices - 1.0,
            "Close": prices,
            "Volume": np.full(n, 1_000_000),
        }
    )


class FrameProvider:
    name = "test"

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = {k.upper(): v for k, v in frames.items()}

    def fetch(self, ticker, start=None, end=None, refresh=False):
        df = self.frames[ticker.upper()].copy()
        raw = normalize_ohlcv(df, ticker)
        if start is not None:
            raw = raw.loc[raw.index >= pd.Timestamp(start)]
        if end is not None:
            raw = raw.loc[raw.index <= pd.Timestamp(end)]
        return raw


class TestNormalize(unittest.TestCase):
    def test_normalize_columns_and_dedup(self):
        raw = make_frame(10)
        raw["CLOSE"] = raw["Close"]
        df = normalize_ohlcv(raw, "AAA")
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertTrue(isinstance(df.index, pd.DatetimeIndex))
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertEqual(len(df), 10)
        # 高低价被修正到合法区间
        self.assertTrue((df["high"] >= df["close"]).all())
        self.assertTrue((df["low"] <= df["close"]).all())

    def test_empty_raises(self):
        with self.assertRaises(NoDataError):
            normalize_ohlcv(pd.DataFrame(), "AAA")


class TestPointInTime(unittest.TestCase):
    def setUp(self):
        self.cut = pd.Timestamp("2021-02-01")
        self.provider = FrameProvider({"AAA": make_frame(60), "SPY": make_frame(60)})
        self.portal = DataPortal(self.provider)

    def test_history_never_returns_future(self):
        df = self.portal.history("AAA", as_of=self.cut)
        self.assertLessEqual(df.index.max(), self.cut)
        self.assertGreater(len(df), 10)

    def test_lookback(self):
        df = self.portal.history("AAA", as_of=self.cut, lookback=5)
        self.assertEqual(len(df), 5)
        self.assertLessEqual(df.index.max(), self.cut)

    def test_panel_is_clamped(self):
        panel = self.portal.panel(["AAA", "SPY"], as_of=self.cut, lookback=30)
        self.assertLessEqual(panel.close.index.max(), self.cut)
        self.assertLessEqual(panel.as_of, self.cut)

    def test_future_tamper_does_not_change_past(self):
        base = self.portal.history("AAA", as_of=self.cut).copy()

        raw = make_frame(60)
        raw.loc[raw.index >= 40, ["Open", "High", "Low", "Close"]] *= 10.0
        tampered = DataPortal(FrameProvider({"AAA": raw}))
        changed = tampered.history("AAA", as_of=self.cut)

        pd.testing.assert_frame_equal(base, changed)

    def test_no_data_before_start(self):
        with self.assertRaises(NoDataError):
            self.portal.history("AAA", as_of=date(2020, 1, 1))


if __name__ == "__main__":
    unittest.main()
