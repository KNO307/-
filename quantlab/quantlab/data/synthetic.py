"""确定性合成行情，用于离线演示与单元测试。

同一标的在任意机器、任意运行中都生成完全一致的序列（种子由代码固定），
因此回测结果可复现；它不是"随机假数据"，而是带市场因子、波动率差异与
牛熊阶段的几何布朗运动，用于验证回测内核的正确性。
"""
from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from .base import normalize_ohlcv

_START = "2004-01-05"


def _seed_of(text: str) -> int:
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


class SyntheticProvider:
    name = "synthetic"

    def __init__(self, start: str = _START, end: str | date | None = None):
        self.start = start
        self.end = end
        self._cache: dict[str, pd.DataFrame] = {}
        self._market: pd.Series | None = None

    # ------------------------------------------------------------------ factor
    def _market_factor(self, index: pd.DatetimeIndex) -> pd.Series:
        if self._market is not None:
            return self._market
        rng = np.random.default_rng(20240501)
        n = len(index)
        # 逐年漂移 + 衰退/危机阶段，制造可被策略识别的趋势与回撤
        drift = np.full(n, 0.00035)
        years = index.year.to_numpy()
        for i, y in enumerate(years):
            drift[i] += 0.0002 * np.sin((y - 2004) * 1.1)
        for start, length, shock in [
            ("2008-09-01", 70, -0.0028),
            ("2011-08-01", 30, -0.0018),
            ("2015-08-01", 25, -0.0015),
            ("2018-10-01", 45, -0.0016),
            ("2020-02-20", 35, -0.0045),
            ("2022-01-03", 90, -0.0016),
        ]:
            mask = (index >= pd.Timestamp(start)) & (
                index < pd.Timestamp(start) + pd.Timedelta(days=length * 2)
            )
            drift[mask] += shock
        vol = np.full(n, 0.0105)
        rets = rng.normal(0.0, vol) + drift
        # 消除抽样偏差，让长期漂移严格等于设定值，保证路径稳定可复现
        rets += drift.mean() - rets.mean()
        series = pd.Series(np.exp(np.cumsum(rets)), index=index, name="market")
        self._market = series
        return series

    # ------------------------------------------------------------------ public
    def fetch(
        self,
        ticker: str,
        start: str | date | None = None,
        end: str | date | None = None,
        refresh: bool = False,
    ) -> pd.DataFrame:
        key = ticker.upper()
        df = self._cache.get(key)
        if df is None:
            df = self._generate(key)
            self._cache[key] = df
        out = df
        if start is not None:
            out = out.loc[out.index >= pd.Timestamp(start).normalize()]
        if end is not None:
            out = out.loc[out.index <= pd.Timestamp(end).normalize()]
        return out.copy()

    # ------------------------------------------------------------------ engine
    def _generate(self, ticker: str) -> pd.DataFrame:
        end = pd.Timestamp(self.end) if self.end else pd.Timestamp.today().normalize()
        index = pd.bdate_range(self.start, end)
        rng = np.random.default_rng(_seed_of(ticker))
        market = self._market_factor(index)

        is_market = ticker in {"SPY", "MARKET"}
        if is_market:
            close = market.to_numpy() * 250.0  # 让 SPY 的价格量级接近真实
        else:
            beta = 0.75 + rng.random() * 0.6
            idio_vol = 0.006 + rng.random() * 0.014
            idio = rng.normal(0.0, idio_vol, len(index))
            alpha = rng.normal(0.00008, 0.00012)
            rets = beta * np.diff(np.log(market.to_numpy()), prepend=np.log(market.to_numpy()[0]))
            rets = rets + idio + alpha
            close = np.exp(np.cumsum(rets)) * (50 + rng.random() * 150)

        close = np.maximum(close, 0.5)
        prev_close = np.concatenate([[close[0]], close[:-1]])
        open_ = prev_close * (1 + rng.normal(0, 0.0025, len(index)))
        spread = np.abs(rng.normal(0, 0.006, len(index)))
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        volume = rng.lognormal(mean=14.2, sigma=0.45, size=len(index)).astype(int)

        raw = pd.DataFrame(
            {
                "date": index,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        return normalize_ohlcv(raw, ticker)
