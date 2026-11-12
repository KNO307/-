"""日频事件驱动回测引擎。

时间线（关键，用于杜绝前视）：
    t 收盘：用截止 t 的数据生成目标权重
    t+1 开盘：按开盘价 + 滑点成交
    t 收盘：按收盘价盯市记录权益
调仓频率支持 D / W / M / Q / ONCE。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..config import (
    BENCHMARK,
    DEFAULT_CAPITAL,
    DEFAULT_CASH_BUFFER,
    DEFAULT_COMMISSION_BP,
    DEFAULT_SLIPPAGE_BP,
    DEFAULT_UNIVERSE,
)
from ..data.base import DataError
from ..data.portal import DataPortal, MarketPanel
from ..strategy.base import Strategy
from .broker import Broker
from .metrics import compute_metrics, monthly_return_table
from .result import BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    tickers: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    start: str = "2019-01-01"
    end: str = "2025-12-31"
    initial_capital: float = DEFAULT_CAPITAL
    commission_bp: float = DEFAULT_COMMISSION_BP
    slippage_bp: float = DEFAULT_SLIPPAGE_BP
    cash_buffer: float = DEFAULT_CASH_BUFFER
    benchmark: str = BENCHMARK
    rf_annual: float = 0.0

    def validate(self) -> None:
        self.start = pd.Timestamp(self.start).strftime("%Y-%m-%d")
        self.end = pd.Timestamp(self.end).strftime("%Y-%m-%d")
        if pd.Timestamp(self.start) >= pd.Timestamp(self.end):
            raise ValueError("start 必须早于 end")
        if not self.tickers:
            raise ValueError("股票池不能为空")
        if self.initial_capital <= 0:
            raise ValueError("初始资金必须大于 0")
        if self.commission_bp < 0 or self.slippage_bp < 0:
            raise ValueError("交易成本不能为负")
        if not (0.0 <= self.cash_buffer < 1.0):
            raise ValueError("cash_buffer 必须在 [0, 1) 之间")
        seen = set()
        unique = []
        for t in self.tickers:
            key = t.strip().upper()
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        self.tickers = unique
        self.benchmark = self.benchmark.strip().upper()


class BacktestEngine:
    def __init__(self, portal: DataPortal, config: BacktestConfig):
        self.portal = portal
        self.config = config

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _is_period_end(d: pd.Timestamp, next_d: pd.Timestamp, freq: str) -> bool:
        if freq == "D":
            return True
        if freq == "W":
            return d.isocalendar()[:2] != next_d.isocalendar()[:2]
        if freq == "M":
            return (d.year, d.month) != (next_d.year, next_d.month)
        if freq == "Q":
            return (d.year, d.quarter) != (next_d.year, next_d.quarter)
        return False

    def _sanitize(self, targets: dict[str, float], strategy: Strategy) -> dict[str, float]:
        clean: dict[str, float] = {}
        for ticker, weight in (targets or {}).items():
            key = str(ticker).upper()
            try:
                value = float(weight)
            except (TypeError, ValueError):
                continue
            if value <= 0 or value != value or key not in self._tradable:
                continue
            clean[key] = min(value, strategy.max_weight)
        total = sum(clean.values())
        if total > 1.0:
            clean = {k: v / total for k, v in clean.items()}
        return clean

    def _build_panel(
        self,
        as_of: pd.Timestamp,
        lookback: int,
        close_df: pd.DataFrame,
        open_df: pd.DataFrame,
        high_df: pd.DataFrame,
        low_df: pd.DataFrame,
        volume_df: pd.DataFrame,
    ) -> MarketPanel:
        """只截取 as_of（含）之前的数据，这是防前视的最后一道闸门。"""
        window = close_df.loc[:as_of].tail(lookback)
        if window.empty:
            raise DataError(f"as_of={as_of.date()} 面板为空")
        idx = window.index

        def frame(df: pd.DataFrame) -> pd.DataFrame:
            return df.reindex(idx)

        return MarketPanel(
            as_of=as_of,
            close=window,
            open=frame(open_df),
            high=frame(high_df),
            low=frame(low_df),
            volume=frame(volume_df),
        )

    # ------------------------------------------------------------------ run
    def run(self, strategy: Strategy) -> BacktestResult:
        self.config.validate()
        start = pd.Timestamp(self.config.start)
        end = pd.Timestamp(self.config.end)
        benchmark = self.config.benchmark
        warnings: list[str] = []

        # 1) 加载数据（整段一次性加载，但策略只能看到 as_of 之前的部分）
        frames: dict[str, pd.DataFrame] = {}
        for ticker in self.config.tickers:
            try:
                frames[ticker] = self.portal.history(ticker, as_of=end)
            except DataError as exc:
                warnings.append(f"{ticker} 无可用数据，已从股票池剔除：{exc}")
                logger.warning("剔除 %s：%s", ticker, exc)
        try:
            bench_df = self.portal.history(benchmark, as_of=end)
        except DataError as exc:
            raise DataError(f"基准 {benchmark} 数据不可用：{exc}") from exc

        self._tradable = list(frames)
        if not self._tradable:
            raise DataError("股票池中没有可用标的")

        dates = bench_df.index[(bench_df.index >= start) & (bench_df.index <= end)]
        if len(dates) < 30:
            raise ValueError(f"回测区间有效交易日过少（{len(dates)}），请扩大区间")

        def wide(col: str) -> pd.DataFrame:
            return pd.concat(
                {t: frames[t][col] for t in self._tradable}, axis=1
            ).reindex(dates).sort_index()

        close_raw = wide("close")
        open_df = wide("open")
        high_df = wide("high")
        low_df = wide("low")
        volume_df = wide("volume")
        close_df = close_raw.ffill()  # 仅向前填充，不引入未来信息

        bench_close = bench_df["close"].reindex(dates).ffill()
        benchmark_curve = bench_close / float(bench_close.iloc[0]) * self.config.initial_capital

        # 2) 事件循环
        broker = Broker(
            self.config.initial_capital,
            commission_bp=self.config.commission_bp,
            slippage_bp=self.config.slippage_bp,
        )
        pending: dict[str, float] = {}
        lookback = max(strategy.warmup + 60, 120)
        equity_rows: list[dict] = []
        rebalances = 0

        for i, day in enumerate(dates):
            if pending:
                broker.execute(
                    day,
                    pending,
                    open_prices={
                        k: float(v) for k, v in open_df.loc[day].dropna().items()
                    },
                    cash_buffer=self.config.cash_buffer,
                )
                pending = {}

            close_row = {k: float(v) for k, v in close_df.loc[day].dropna().items()}
            equity_value, invested = broker.mark(close_row)
            equity_rows.append(
                {
                    "date": day,
                    "equity": equity_value,
                    "exposure": (invested / equity_value) if equity_value > 0 else 0.0,
                }
            )

            if i >= len(dates) - 1:
                break

            next_day = dates[i + 1]
            should = (
                i == 0
                if strategy.rebalance == "ONCE"
                else self._is_period_end(day, next_day, strategy.rebalance)
            )
            if not should:
                continue

            try:
                panel = self._build_panel(
                    day, lookback, close_df, open_df, high_df, low_df, volume_df
                )
                targets = self._sanitize(strategy.generate_targets(panel), strategy)
                pending = targets
                rebalances += 1
            except DataError as exc:
                warnings.append(f"{day.date()} 信号生成失败：{exc}")
                logger.warning("信号生成失败 %s：%s", day.date(), exc)

        # 3) 汇总
        equity_df = pd.DataFrame(equity_rows).set_index("date")
        equity_df["benchmark"] = benchmark_curve.reindex(equity_df.index).to_numpy()
        equity_df["drawdown"] = (
            equity_df["equity"] / equity_df["equity"].cummax() - 1.0
        )
        equity_df = equity_df[["equity", "benchmark", "drawdown", "exposure"]]

        trades_df = pd.DataFrame(
            broker.trades,
            columns=[
                "date", "ticker", "side", "shares", "price",
                "notional", "fee", "realized_pnl",
            ],
        )
        if len(trades_df):
            trades_df["date"] = pd.to_datetime(trades_df["date"])

        last_close = {k: float(v) for k, v in close_df.iloc[-1].dropna().items()}
        position_rows = []
        total_equity = float(equity_df["equity"].iloc[-1])
        for ticker, pos in sorted(broker.positions.items()):
            price = last_close.get(ticker, pos.last_price or pos.avg_cost)
            value = pos.shares * price
            position_rows.append(
                {
                    "ticker": ticker,
                    "shares": int(pos.shares),
                    "avg_cost": round(pos.avg_cost, 4),
                    "last_price": round(price, 4),
                    "market_value": round(value, 2),
                    "weight": round(value / total_equity, 4) if total_equity > 0 else 0.0,
                }
            )
        positions_df = pd.DataFrame(
            position_rows,
            columns=["ticker", "shares", "avg_cost", "last_price", "market_value", "weight"],
        )

        metrics = compute_metrics(
            equity_df["equity"],
            benchmark=equity_df["benchmark"],
            trades=trades_df,
            exposure=equity_df["exposure"],
            rf_annual=self.config.rf_annual,
        )
        metrics["rebalances"] = rebalances
        metrics["num_holdings"] = int(len(positions_df))
        metrics["cash"] = round(broker.cash, 2)

        monthly = monthly_return_table(equity_df["equity"])
        sources = {
            t: self.portal.sources.get(t, "?") for t in self._tradable + [benchmark]
        }
        if any(src == "synthetic" for src in sources.values()):
            warnings.append("部分或全部标的使用了合成行情（数据源不可用时的回退），结果仅用于验证内核。")

        run_id = (
            f"{strategy.name}-{start.strftime('%Y%m%d')}-"
            f"{end.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        )
        config_dict = {
            "tickers": self._tradable,
            "start": self.config.start,
            "end": self.config.end,
            "initial_capital": self.config.initial_capital,
            "commission_bp": self.config.commission_bp,
            "slippage_bp": self.config.slippage_bp,
            "cash_buffer": self.config.cash_buffer,
            "benchmark": benchmark,
            "rf_annual": self.config.rf_annual,
            "trading_days": int(len(dates)),
        }

        return BacktestResult(
            run_id=run_id,
            strategy_name=strategy.name,
            strategy_label=strategy.label,
            strategy_params=strategy.params(),
            config=config_dict,
            metrics=metrics,
            equity=equity_df,
            trades=trades_df,
            positions=positions_df,
            monthly_returns=monthly,
            data_sources=sources,
            warnings=warnings,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
