"""Stooq 日线数据源。

Stooq 的 CSV 接口无需 API Key：https://stooq.com/q/d/l/?s=spy.us&i=d
下载结果按标的落盘缓存，重复回测无需再次联网。
"""
from __future__ import annotations

import io
import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .base import DataError, DataUnavailableError, NoDataError, normalize_ohlcv

logger = logging.getLogger(__name__)

STOOQ_URL = "https://stooq.com/q/d/l/"
_UA = "Mozilla/5.0 (compatible; QuantLab/1.0; research)"


class StooqProvider:
    name = "stooq"

    def __init__(self, cache_dir: str | Path, timeout: int = 30, retries: int = 3):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retries = retries

    # ------------------------------------------------------------------ symbol
    @staticmethod
    def symbol(ticker: str) -> str:
        t = ticker.strip().lower()
        if "." in t:  # 已带市场后缀，如 spy.us
            return t
        return f"{t}.us"

    @property
    def _cache_path(self) -> Path:
        return self.cache_dir

    def _path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}.csv"

    # ------------------------------------------------------------------ public
    def fetch(
        self,
        ticker: str,
        start: str | date | None = None,
        end: str | date | None = None,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """返回 [start, end] 区间的规范化日线；优先使用本地缓存。"""
        cached = self._read_cache(ticker)
        if cached is not None and not refresh:
            df = cached
        else:
            df = self._download(ticker)
            self._write_cache(ticker, df)

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
        if not path.exists() or path.stat().st_size == 0:
            return None
        try:
            raw = pd.read_csv(path)
            return normalize_ohlcv(raw, ticker)
        except (DataError, pd.errors.ParserError, OSError) as exc:
            logger.warning("缓存 %s 读取失败（%s），将重新下载", path, exc)
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
        params = {
            "s": self.symbol(ticker),
            "i": "d",
            "d1": "19900101",
            "d2": pd.Timestamp.today().strftime("%Y%m%d"),
        }
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.get(
                    STOOQ_URL,
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": _UA},
                )
                resp.raise_for_status()
                text = resp.text.strip()
                if not text.lower().startswith("date,"):
                    raise DataUnavailableError(
                        f"Stooq 返回异常内容（{ticker}，前 80 字符：{text[:80]!r}）"
                    )
                raw = pd.read_csv(io.StringIO(text))
                return normalize_ohlcv(raw, ticker)
            except (requests.RequestException, DataError, pd.errors.ParserError) as exc:
                last_err = exc
                logger.warning(
                    "下载 %s 失败（第 %d/%d 次）：%s", ticker, attempt, self.retries, exc
                )
                if attempt < self.retries:
                    time.sleep(1.2 * attempt)
        raise DataUnavailableError(f"{ticker}: Stooq 下载失败：{last_err}") from last_err
