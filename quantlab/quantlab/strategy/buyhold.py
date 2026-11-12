"""买入持有基准策略：等权持有整个股票池，仅建仓一次。"""
from __future__ import annotations

from ..data.portal import MarketPanel
from .base import Strategy, register


@register
class BuyHoldStrategy(Strategy):
    name = "buy_hold"
    label = "等权买入持有"
    description = "在回测开始时等权买入股票池全部标的并持有到底，用作策略对照。"
    rebalance = "ONCE"
    warmup = 1

    def __init__(self):
        self.max_weight = 1.0

    def generate_targets(self, panel: MarketPanel) -> dict[str, float]:
        tickers = [t for t in panel.close.columns if not panel.close[t].dropna().empty]
        if not tickers:
            return {}
        return {t: 1.0 / len(tickers) for t in tickers}
