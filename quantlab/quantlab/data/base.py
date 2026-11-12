"""数据层的公共类型与 OHLCV 规范化。

设计要点（借鉴 TradingAgents 的类型化数据错误与 point-in-time 约束）：
    * 所有数据错误都抛 DataError 子类，调用方可以区分"没数据"和"数据源不可用"；
    * normalize_ohlcv 是唯一的数据清洗入口，保证列名、索引、排序、去重一致。
"""
from __future__ import annotations

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataError(Exception):
    """数据层错误基类。"""


class NoDataError(DataError):
    """请求的标的/区间没有可用数据（NO_DATA）。"""


class DataUnavailableError(DataError):
    """数据源不可用，例如网络失败、返回格式异常（DATA_UNAVAILABLE）。"""


def normalize_ohlcv(df: pd.DataFrame | None, ticker: str = "?") -> pd.DataFrame:
    """把任意来源的日线数据规范成统一格式。

    返回：DatetimeIndex（tz-naive、已归一化到日）+ 小写列 open/high/low/close/volume，
    按日期升序、去重、剔除收盘价缺失或非正的记录。
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        raise NoDataError(f"{ticker}: 没有可用数据")

    raw = df.copy()
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    # 大小写不同的重复列只保留最后一列，避免列选择返回 DataFrame
    raw = raw.loc[:, ~raw.columns.duplicated(keep="last")]

    if "date" in raw.columns:
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
        raw = raw.dropna(subset=["date"]).set_index("date")
    else:
        raw.index = pd.to_datetime(raw.index, errors="coerce")
        raw = raw[raw.index.notna()]

    raw.index = pd.DatetimeIndex(raw.index).normalize()
    raw.index.name = "date"

    missing = [c for c in ("close",) if c not in raw.columns]
    if missing:
        raise NoDataError(f"{ticker}: 数据缺少必要列 {missing}")

    for col in OHLCV_COLUMNS:
        if col not in raw.columns:
            raw[col] = raw["close"] if col != "volume" else 0.0
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw = raw[OHLCV_COLUMNS]
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()
    raw = raw.dropna(subset=["close"])
    raw = raw[raw["close"] > 0]
    if raw.empty:
        raise NoDataError(f"{ticker}: 清洗后没有有效数据")

    # 缺失的开高低用收盘价兜底；量缺失记 0
    for col in ("open", "high", "low"):
        raw[col] = raw[col].fillna(raw["close"])
    raw["volume"] = raw["volume"].fillna(0.0).clip(lower=0.0)

    # 修正明显不合法的高低价
    raw["high"] = raw[["high", "open", "close"]].max(axis=1)
    raw["low"] = raw[["low", "open", "close"]].min(axis=1)
    return raw


def clamp_history(df: pd.DataFrame, as_of: pd.Timestamp | str | None) -> pd.DataFrame:
    """Point-in-time 夹紧：只保留 <= as_of 的记录，禁止使用未来数据。"""
    if as_of is None:
        return df
    ts = pd.Timestamp(as_of).normalize()
    return df.loc[df.index <= ts]
