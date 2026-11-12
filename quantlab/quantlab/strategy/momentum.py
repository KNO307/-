"""横截面动量策略：12-1 动量 + 长期趋势过滤，月度调仓。"""
from __future__ import annotations

from ..data.portal import MarketPanel
from .base import Strategy, register


@register
class MomentumStrategy(Strategy):
    name = "momentum"
    label = "横截面动量 (12-1)"
    description = (
        "计算过去 12 个月（跳过最近 1 个月）的收益率，选排名靠前且价格在 200 日均线"
        "之上的股票等权持有；月度调仓，趋势向下时持有现金。"
    )
    rebalance = "M"

    def __init__(
        self,
        lookback: int = 252,
        skip: int = 21,
        top_n: int = 5,
        ma_window: int = 200,
    ):
        self.lookback = int(lookback)
        self.skip = int(skip)
        self.top_n = int(top_n)
        self.ma_window = int(ma_window)
        self.warmup = self.lookback + self.skip + 10
        self.max_weight = 1.0 / max(self.top_n, 1) + 0.02

    def scores(self, panel: MarketPanel) -> dict[str, float]:
        out: dict[str, float] = {}
        needed = self.lookback + self.skip + 1
        for ticker in panel.close.columns:
            series = panel.close[ticker].dropna()
            if len(series) < needed:
                continue
            if self.ma_window > 0 and len(series) >= self.ma_window:
                ma = series.iloc[-self.ma_window:].mean()
                if series.iloc[-1] <= ma:
                    continue
            recent = series.iloc[-1 - self.skip]
            past = series.iloc[-1 - self.skip - self.lookback]
            if past > 0:
                out[ticker] = float(recent / past - 1.0)
        return out

    def generate_targets(self, panel: MarketPanel) -> dict[str, float]:
        ranked = sorted(self.scores(panel).items(), key=lambda kv: kv[1], reverse=True)
        selected = [t for t, score in ranked[: self.top_n] if score > 0]
        return self._equal_weights(selected)
