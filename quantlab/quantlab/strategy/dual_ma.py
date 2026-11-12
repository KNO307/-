"""双均线趋势策略：快线上穿慢线后持有，月度调仓。"""
from __future__ import annotations

from ..data.portal import MarketPanel
from .base import Strategy, register


@register
class DualMAStrategy(Strategy):
    name = "dual_ma"
    label = "双均线趋势 (50/200)"
    description = (
        "计算 50 日均线与 200 日均线，持有快线在慢线之上且斜率最强的股票，"
        "等权配置；月度调仓，空头信号时降为现金。"
    )
    rebalance = "M"

    def __init__(self, fast: int = 50, slow: int = 200, top_n: int = 5):
        self.fast = int(fast)
        self.slow = int(slow)
        self.top_n = int(top_n)
        self.warmup = self.slow + 10
        self.max_weight = 1.0 / max(self.top_n, 1) + 0.02

    def scores(self, panel: MarketPanel) -> dict[str, float]:
        out: dict[str, float] = {}
        for ticker in panel.close.columns:
            series = panel.close[ticker].dropna()
            if len(series) < self.slow + 1:
                continue
            fast_ma = series.iloc[-self.fast:].mean()
            slow_ma = series.iloc[-self.slow:].mean()
            if slow_ma <= 0 or fast_ma <= slow_ma:
                continue
            out[ticker] = float(fast_ma / slow_ma - 1.0)
        return out

    def generate_targets(self, panel: MarketPanel) -> dict[str, float]:
        ranked = sorted(self.scores(panel).items(), key=lambda kv: kv[1], reverse=True)
        selected = [t for t, score in ranked[: self.top_n] if score > 0]
        return self._equal_weights(selected)
