"""本地 CSV 数据源。

目录下按 `TICKER.csv` 或 `TICKER.us.csv` 放置日线文件，列名大小写不敏感，
至少包含 date/close，推荐包含 open/high/low/close/volume。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import DataError, NoDataError, normalize_ohlcv


class LocalCSVProvider:
    name = "csv"

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def _find(self, ticker: str) -> Path:
        stem = ticker.upper()
        candidates = [
            self.data_dir / f"{stem}.csv",
            self.data_dir / f"{stem}.us.csv",
            self.data_dir / f"{stem.lower()}.csv",
            self.data_dir / f"{stem.lower()}.us.csv",
        ]
        for path in candidates:
            if path.exists():
                return path
        raise NoDataError(f"{ticker}: 在 {self.data_dir} 下找不到 CSV 文件")

    def fetch(self, ticker: str, start=None, end=None, refresh: bool = False) -> pd.DataFrame:
        path = self._find(ticker)
        try:
            raw = pd.read_csv(path)
            df = normalize_ohlcv(raw, ticker)
        except DataError:
            raise
        except (OSError, pd.errors.ParserError) as exc:
            raise DataError(f"{ticker}: 读取 {path} 失败：{exc}") from exc

        if start is not None:
            df = df.loc[df.index >= pd.Timestamp(start).normalize()]
        if end is not None:
            df = df.loc[df.index <= pd.Timestamp(end).normalize()]
        if df.empty:
            raise NoDataError(f"{ticker}: 指定区间没有数据")
        return df
