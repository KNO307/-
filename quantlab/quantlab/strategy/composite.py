"""多因子合成策略：动量 + 低波动 + 趋势，横截面 z-score 合成。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.portal import MarketPanel
from .base import Strategy, register


def _zscore(values: dict[str, float]) -> dict[str, float]:
    if len(values) < 2:
        return {}
    arr = np.array(list(values.values()), dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=0))
    if std <= 0:
        return {}
    return {k: float((v - mean) / std) for k, v in values.items()}


@register
class CompositeStrategy(Strategy):
    name = "composite"
    label = "多因子合成 (动量+低波+趋势)"
    description = (
        "对 12-1 动量、60 日低波动、价格相对 200 日均线趋势三个因子做横截面 "
        "z-score，等权合成后选前 N 名；月度调仓，分散度更高。"
    )
    rebalance = "M"

    def __init__(
        self,
        top_n: int = 5,
        mom_lookback: int = 252,
        skip: int = 21,
        vol_window: int = 63,
        ma_window: int = 200,
    ):
        self.top_n = int(top_n)
        self.mom_lookback = int(mom_lookback)
        self.skip = int(skip)
        self.vol_window = int(vol_window)
        self.ma_window = int(ma_window)
        self.warmup = self.mom_lookback + self.skip + 10
        self.max_weight = 1.0 / max(self.top_n, 1) + 0.02

    def generate_targets(self, panel: MarketPanel) -> dict[str, float]:
        momentum: dict[str, float] = {}
        low_vol: dict[str, float] = {}
        trend: dict[str, float] = {}
        need = self.mom_lookback + self.skip + 1

        for ticker in panel.close.columns:
            series = panel.close[ticker].dropna()
            if len(series) < need:
                continue
            # 12-1 动量
            momentum[ticker] = float(
                series.iloc[-1 - self.skip] / series.iloc[-1 - self.skip - self.mom_lookback] - 1
            )
            # 低波动（取负号，波动越低分越高）
            rets = series.pct_change().dropna().iloc[-self.vol_window:]
            if len(rets) >= max(10, self.vol_window // 3):
                low_vol[ticker] = -float(rets.std(ddof=0))
            # 趋势
            if len(series) >= self.ma_window:
                trend[ticker] = float(series.iloc[-1] / series.iloc[-self.ma_window:].mean() - 1)

        components = [c for c in (_zscore(m) for m in (momentum, low_vol, trend)) if c]
        if not components:
            return {}
        common = set(components[0])
        for comp in components[1:]:
            common &= set(comp)
        if not common:
            return {}

        composite = {
            t: float(np.mean([comp[t] for comp in components])) for t in sorted(common)
        }
        ranked = sorted(composite.items(), key=lambda kv: kv[1], reverse=True)
        selected = [t for t, _ in ranked[: self.top_n]]
        return self._equal_weights(selected)
