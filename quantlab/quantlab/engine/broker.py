"""Broker：现金/持仓/成本模型与调仓撮合。

约定：
    * 引擎在 t 日收盘生成目标权重，Broker 在 t+1 日开盘按开盘价成交；
    * 佣金按成交额收取，滑点直接体现在成交价（买入上浮、卖出下调）；
    * 只做多、整数股、不使用杠杆。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Position:
    shares: int = 0
    avg_cost: float = 0.0
    last_price: float = 0.0


class Broker:
    def __init__(
        self,
        cash: float,
        commission_bp: float = 0.0,
        slippage_bp: float = 0.0,
    ):
        self.cash = float(cash)
        self.initial_cash = float(cash)
        self.commission_rate = float(commission_bp) / 10_000.0
        self.slippage_rate = float(slippage_bp) / 10_000.0
        self.positions: dict[str, Position] = {}
        self.trades: list[dict] = []

    # ------------------------------------------------------------------ value
    def price_of(self, ticker: str, prices: dict[str, float] | None = None) -> float:
        pos = self.positions.get(ticker)
        if prices:
            p = prices.get(ticker)
            if p is not None and np.isfinite(p) and p > 0:
                return float(p)
        if pos and pos.last_price > 0:
            return pos.last_price
        if pos and pos.avg_cost > 0:
            return pos.avg_cost
        return 0.0

    def equity(self, prices: dict[str, float] | None = None) -> float:
        value = self.cash
        for ticker, pos in self.positions.items():
            value += pos.shares * self.price_of(ticker, prices)
        return float(value)

    def invested(self, prices: dict[str, float] | None = None) -> float:
        return float(
            sum(pos.shares * self.price_of(t, prices) for t, pos in self.positions.items())
        )

    def mark(self, close_prices: dict[str, float]) -> tuple[float, float]:
        """按收盘价盯市，并更新持仓的最新价格。返回 (总权益, 持仓市值)。"""
        for ticker, pos in self.positions.items():
            p = close_prices.get(ticker)
            if p is not None and np.isfinite(p) and p > 0:
                pos.last_price = float(p)
        return self.equity(), self.invested()

    # ------------------------------------------------------------------ trade
    def execute(
        self,
        date: pd.Timestamp,
        targets: dict[str, float],
        open_prices: dict[str, float],
        cash_buffer: float = 0.02,
    ) -> list[dict]:
        prices = {
            t: self.price_of(t, open_prices)
            for t in set(targets) | set(self.positions)
        }
        equity = self.equity(prices)
        investable = max(equity * (1.0 - max(0.0, cash_buffer)), 0.0)

        target_shares: dict[str, int] = {}
        for ticker, weight in targets.items():
            price = prices.get(ticker, 0.0)
            if price <= 0 or weight <= 0:
                target_shares[ticker] = 0
                continue
            buy_fill = price * (1.0 + self.slippage_rate)
            target_shares[ticker] = int(investable * float(weight) // buy_fill)

        made: list[dict] = []

        # 先卖后买，避免现金不足
        for ticker in list(self.positions):
            pos = self.positions[ticker]
            target = target_shares.get(ticker, 0)
            if pos.shares <= target:
                continue
            price = prices.get(ticker, 0.0)
            if price <= 0:
                continue
            qty = pos.shares - target
            fill = price * (1.0 - self.slippage_rate)
            notional = fill * qty
            fee = notional * self.commission_rate
            realized = (fill - pos.avg_cost) * qty - fee
            self.cash += notional - fee
            pos.shares -= qty
            if pos.shares <= 0:
                del self.positions[ticker]
            made.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "side": "SELL",
                    "shares": qty,
                    "price": round(fill, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 2),
                    "realized_pnl": round(realized, 2),
                }
            )

        for ticker, target in sorted(target_shares.items(), key=lambda kv: kv[1], reverse=True):
            if target <= 0:
                continue
            pos = self.positions.get(ticker)
            held = pos.shares if pos else 0
            qty = target - held
            if qty <= 0:
                continue
            price = prices.get(ticker, 0.0)
            if price <= 0:
                continue
            fill = price * (1.0 + self.slippage_rate)
            if fill <= 0:
                continue
            max_qty = int(self.cash // (fill * (1.0 + self.commission_rate)))
            qty = min(qty, max_qty)
            if qty <= 0:
                continue
            notional = fill * qty
            fee = notional * self.commission_rate
            cost = notional + fee
            if cost > self.cash + 1e-9:
                continue
            self.cash -= cost
            if pos is None:
                pos = Position(shares=0, avg_cost=0.0, last_price=price)
                self.positions[ticker] = pos
            total_shares = pos.shares + qty
            pos.avg_cost = (pos.avg_cost * pos.shares + fill * qty + fee) / total_shares
            pos.shares = total_shares
            pos.last_price = price
            made.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "side": "BUY",
                    "shares": qty,
                    "price": round(fill, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 2),
                    "realized_pnl": np.nan,
                }
            )

        self.trades.extend(made)
        return made
