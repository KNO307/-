"""DataPortal：所有策略读取数据的唯一入口。

核心约束（借鉴 TradingAgents 的 point-in-time 设计）：
    * 任何读取都必须经过 `history(..., as_of=...)`，返回结果永远不含 as_of 之后的记录；
    * 回测引擎只在调仓日以当日收盘为 as_of 组装面板，从结构上杜绝未来函数；
    * 数据源失败时可按回退链降级（Stooq -> 合成数据），并记录实际来源。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from .base import DataError, NoDataError, DataUnavailableError, clamp_history, normalize_ohlcv

logger = logging.getLogger(__name__)


@dataclass
class MarketPanel:
    """截止 as_of 的历史窗口，列是标的，行是交易日。"""

    as_of: pd.Timestamp
    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def tickers(self) -> list[str]:
        return list(self.close.columns)

    def prices(self, ticker: str) -> pd.Series:
        if ticker not in self.close.columns:
            raise KeyError(ticker)
        return self.close[ticker].dropna()

    def __len__(self) -> int:
        return len(self.close)


class DataPortal:
    def __init__(
        self,
        primary,
        fallback=None,
        max_lookback: int = 3000,
    ):
        self.primary = primary
        self.fallback = fallback
        self.max_lookback = max_lookback
        self._frames: dict[str, pd.DataFrame] = {}
        self.sources: dict[str, str] = {}

    # ------------------------------------------------------------------ loading
    def _load(self, ticker: str) -> pd.DataFrame:
        key = ticker.upper()
        if key in self._frames:
            return self._frames[key]

        errors: list[str] = []
        for provider in (self.primary, self.fallback):
            if provider is None:
                continue
            try:
                df = normalize_ohlcv(provider.fetch(key), key)
                self._frames[key] = df
                self.sources[key] = getattr(provider, "name", provider.__class__.__name__)
                return df
            except DataError as exc:
                errors.append(f"{getattr(provider, 'name', provider)}: {exc}")
                if provider is self.fallback:
                    break
                logger.warning("主数据源获取 %s 失败，尝试回退：%s", key, exc)
        raise DataUnavailableError(f"{key}: 所有数据源均失败；" + " | ".join(errors))

    # ------------------------------------------------------------------ public
    def history(
        self,
        ticker: str,
        as_of: pd.Timestamp | str | None = None,
        lookback: int | None = None,
    ) -> pd.DataFrame:
        df = self._load(ticker)
        df = clamp_history(df, as_of)
        if lookback is not None and lookback > 0:
            df = df.iloc[-lookback:]
        if df.empty:
            raise NoDataError(f"{ticker}: as_of={as_of} 之前没有数据")
        return df.copy()

    def latest_price(self, ticker: str, as_of: pd.Timestamp) -> float:
        df = self.history(ticker, as_of=as_of)
        return float(df["close"].iloc[-1])

    def panel(
        self,
        tickers: list[str],
        as_of: pd.Timestamp | str,
        lookback: int = 400,
    ) -> MarketPanel:
        """组装截止 as_of 的历史面板；缺失数据的标的会被剔除。"""
        ts = pd.Timestamp(as_of).normalize()
        lookback = min(int(lookback), self.max_lookback)
        frames: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            try:
                frames[ticker] = self.history(ticker, as_of=ts, lookback=lookback)
            except DataError as exc:
                logger.warning("面板构建跳过 %s：%s", ticker, exc)

        if not frames:
            raise NoDataError(f"as_of={ts.date()} 没有任何可用标的")

        def wide(col: str) -> pd.DataFrame:
            return pd.concat(
                {t: df[col] for t, df in frames.items()}, axis=1
            ).sort_index()

        return MarketPanel(
            as_of=ts,
            close=wide("close"),
            open=wide("open"),
            high=wide("high"),
            low=wide("low"),
            volume=wide("volume"),
            frames=frames,
        )

    def preload(self, tickers: list[str]) -> dict[str, str]:
        """预加载并返回每个标的实际使用的数据源，便于在结果中做诚实标注。"""
        for ticker in tickers:
            self._load(ticker)
        return {t.upper(): self.sources.get(t.upper(), "?") for t in tickers}
