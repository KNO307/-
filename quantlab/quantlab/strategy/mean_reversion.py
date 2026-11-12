"""短期均值回归策略：20 日 z-score 超跌 + 长期趋势过滤，周度调仓。"""
from __future__ import annotations

import pandas as pd

from ..data.portal import MarketPanel
from .base import Strategy, register


@register
class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    label = "均值回归 (20 日 z-score)"
    description = (
        "在长期趋势向上（收盘价高于 200 日均线）的股票中，买入短期偏离度最低"
        "（z-score 最负）的标的，等待价格回归均线；周度调仓。"
    )
    rebalance = "W"

    def __init__(self, window: int = 20, top_n: int = 3, ma_window: int = 200):
        self.window = int(window)
        self.top_n = int(top_n)
        self.ma_window = int(ma_window)
        self.warmup = self.ma_window + 5
        self.max_weight = 1.0 / max(self.top_n, 1) + 0.02

    def scores(self, panel: MarketPanel) -> dict[str, float]:
        out: dict[str, float] = {}
        for ticker in panel.close.columns:
            series = panel.close[ticker].dropna()
            if len(series) < max(self.window, self.ma_window) + 1:
                continue
            if self.ma_window > 0:
                ma_long = series.iloc[-self.ma_window:].mean()
                if series.iloc[-1] <= ma_long:
                    continue
            recent = series.iloc[-self.window:]
            mean = recent.mean()
            std = recent.std(ddof=0)
            if not std or std <= 0:
                continue
            out[ticker] = float((series.iloc[-1] - mean) / std)
        return out

    def generate_targets(self, panel: MarketPanel) -> dict[str, float]:
        ranked = sorted(self.scores(panel).items(), key=lambda kv: kv[1])
        # 只买真正处于均值下方的标的
        selected = [t for t, z in ranked[: self.top_n] if z < 0]
        return self._equal_weights(selected)
