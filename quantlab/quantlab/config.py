from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
CACHE_DIR = PROJECT_ROOT / ".cache" / "nasdaq"
STOOQ_CACHE_DIR = PROJECT_ROOT / ".cache" / "stooq"
RUNS_DIR = PROJECT_ROOT / "runs"
STATIC_DIR = PACKAGE_DIR / "web" / "static"

BENCHMARK = "SPY"

# 高流动性美股，Stooq 符号统一追加 .us 后缀
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "XOM", "JNJ",
    "PG", "UNH", "HD", "V", "MA", "COST", "AVGO", "LLY", "MRK", "KO", "WMT",
]

DEFAULT_START = "2019-01-01"
DEFAULT_END = "2025-12-31"
DEFAULT_CAPITAL = 1_000_000.0

# 交易成本默认值（基点，1bp = 0.01%）
DEFAULT_COMMISSION_BP = 2.0
DEFAULT_SLIPPAGE_BP = 5.0
DEFAULT_CASH_BUFFER = 0.02
