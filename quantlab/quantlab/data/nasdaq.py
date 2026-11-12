"""Nasdaq 历史行情数据源。

接口（无需 Key）：
    https://api.nasdaq.com/api/quote/SPY/historical?assetclass=etf&fromdate=2019-01-01&limit=9999&todate=2025-12-31
返回 JSON，字段为 date / open / high / low / close / volume（货币符号与千分位需要清洗）。
按标的落盘缓存，避免重复请求。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .base import DataError, DataUnavailableError, NoDataError, normalize_ohlcv

logger = logging.getLogger(__name__)

NASDAQ_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_ETF = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "GLD", "TLT"}


def _money(value) -> float:
    if value is None:
        return float("nan")
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in ("", "N/A", "n/a", "-"):
        return float("nan")
    try:
        return float(text)
    except ValueError:
        return float("nan")


class NasdaqProvider:
    name = "nasdaq"

    def __init__(self, cache_dir: str | Path, timeout: int = 30, retries: int = 2):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retries = retries

    def _path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}.csv"

    @staticmethod
    def asset_class(ticker: str) -> str:
        return "etf" if ticker.upper() in _ETF else "stocks"

    # ------------------------------------------------------------------ public
    def fetch(
        self,
        ticker: str,
        start: str | date | None = None,
        end: str | date | None = None,
        refresh: bool = False,
    ) -> pd.DataFrame:
        cached = None if refresh else self._read_cache(ticker)
        if cached is None:
            df = self._download(ticker)
            self._write_cache(ticker, df)
        else:
            df = cached

        if start is not None:
            df = df.loc[df.index >= pd.Timestamp(start).normalize()]
        if end is not None:
            df = df.loc[df.index <= pd.Timestamp(end).normalize()]
        if df.empty:
            raise NoDataError(f"{ticker}: {start} ~ {end} 区间没有数据")
        return df

    # ------------------------------------------------------------------ cache
    def _read_cache(self, ticker: str) -> pd.DataFrame | None:
        path = self._path(ticker)
        if not path.exists():
            return None
        try:
            return normalize_ohlcv(pd.read_csv(path), ticker)
        except (DataError, OSError, pd.errors.ParserError) as exc:
            logger.warning("缓存 %s 读取失败（%s），重新下载", path, exc)
            return None

    def _write_cache(self, ticker: str, df: pd.DataFrame) -> None:
        path = self._path(ticker)
        tmp = path.with_suffix(".csv.tmp")
        out = df.reset_index()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        out.to_csv(tmp, index=False)
        tmp.replace(path)

    # ------------------------------------------------------------------ network
    def _download(self, ticker: str) -> pd.DataFrame:
        symbol = ticker.upper()
        params = {
            "assetclass": self.asset_class(symbol),
            "fromdate": "2015-01-01",
            "limit": "9999",
            "todate": pd.Timestamp.today().strftime("%Y-%m-%d"),
        }
        url = NASDAQ_URL.format(symbol=symbol)
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": _UA, "Accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
                rows = ((payload or {}).get("data") or {}).get("tradesTable", {}).get("rows")
                if not rows:
                    raise NoDataError(f"{ticker}: Nasdaq 未返回历史数据")
                records = []
                for row in rows:
                    records.append(
                        {
                            "date": pd.to_datetime(row.get("date"), format="%m/%d/%Y", errors="coerce"),
                            "open": _money(row.get("open")),
                            "high": _money(row.get("high")),
                            "low": _money(row.get("low")),
                            "close": _money(row.get("close")),
                            "volume": _money(row.get("volume")),
                        }
                    )
                df = pd.DataFrame(records)
                df = df.dropna(subset=["date"]).set_index("date")
                return normalize_ohlcv(df, ticker)
            except (requests.RequestException, ValueError, DataError, KeyError) as exc:
                last_err = exc
                logger.warning("Nasdaq 下载 %s 失败（%d/%d）：%s", ticker, attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(1.0 * attempt)
        raise DataUnavailableError(f"{ticker}: Nasdaq 下载失败：{last_err}") from last_err
