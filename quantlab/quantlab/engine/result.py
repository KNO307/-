"""回测结果对象：结构化导出 + 供 Web/JSON 使用的 payload。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def jsonable(obj):
    """递归地把 numpy / pandas 类型转换成标准 JSON 类型。"""
    if obj is None or obj is pd.NaT:
        return None
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, np.ndarray):
        return [jsonable(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(x) for x in obj]
    if isinstance(obj, pd.Series):
        return [jsonable(x) for x in obj.tolist()]
    return obj


@dataclass
class BacktestResult:
    run_id: str
    strategy_name: str
    strategy_label: str
    strategy_params: dict
    config: dict
    metrics: dict
    equity: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    monthly_returns: pd.DataFrame
    data_sources: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # ------------------------------------------------------------------ output
    def to_payload(self, trade_limit: int = 500) -> dict:
        eq = self.equity
        trades = self.trades
        if len(trades) > trade_limit:
            trades = trades.tail(trade_limit)
            truncated = True
        else:
            truncated = False

        monthly = []
        if self.monthly_returns is not None and len(self.monthly_returns):
            for year, row in self.monthly_returns.iterrows():
                monthly.append(
                    {
                        "year": int(year),
                        "months": [
                            jsonable(row.get(m)) for m in range(1, 13)
                        ],
                    }
                )

        return jsonable(
            {
                "run_id": self.run_id,
                "strategy": {
                    "name": self.strategy_name,
                    "label": self.strategy_label,
                    "params": self.strategy_params,
                },
                "config": self.config,
                "metrics": self.metrics,
                "equity": {
                    "dates": [d.strftime("%Y-%m-%d") for d in eq.index],
                    "equity": eq["equity"].tolist(),
                    "benchmark": eq["benchmark"].tolist(),
                    "drawdown": eq["drawdown"].tolist(),
                    "exposure": eq["exposure"].tolist(),
                },
                "monthly": monthly,
                "trades": trades.to_dict(orient="records"),
                "trades_total": int(len(self.trades)),
                "trades_truncated": truncated,
                "positions": self.positions.to_dict(orient="records"),
                "data_sources": self.data_sources,
                "warnings": self.warnings,
                "created_at": self.created_at,
            }
        )

    def save(self, out_dir: str | Path) -> dict[str, str]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "metrics": out / "metrics.json",
            "config": out / "config.json",
            "equity": out / "equity.csv",
            "trades": out / "trades.csv",
            "positions": out / "positions.csv",
        }
        paths["metrics"].write_text(
            json.dumps(jsonable(self.metrics), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths["config"].write_text(
            json.dumps(jsonable(self.config), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.equity.to_csv(paths["equity"], encoding="utf-8-sig")
        self.trades.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
        self.positions.to_csv(paths["positions"], index=False, encoding="utf-8-sig")
        return {k: str(v) for k, v in paths.items()}

    def summary_lines(self) -> list[str]:
        m = self.metrics
        pct = lambda x: f"{x * 100:.2f}%" if isinstance(x, (int, float)) else "-"
        return [
            f"策略        : {self.strategy_label} ({self.strategy_name})",
            f"区间        : {self.config.get('start')} ~ {self.config.get('end')}",
            f"期末权益    : {m.get('final_equity', 0):,.2f}",
            f"总收益      : {pct(m.get('total_return'))}",
            f"年化收益    : {pct(m.get('cagr'))}",
            f"年化波动    : {pct(m.get('annual_volatility'))}",
            f"Sharpe      : {m.get('sharpe')}",
            f"Sortino     : {m.get('sortino')}",
            f"最大回撤    : {pct(m.get('max_drawdown'))}",
            f"Calmar      : {m.get('calmar')}",
            f"基准收益    : {pct(m.get('benchmark_return'))}",
            f"超额收益    : {pct(m.get('excess_return'))}",
            f"Alpha/Beta  : {m.get('alpha')} / {m.get('beta')}",
            f"信息比率    : {m.get('information_ratio')}",
            f"胜率(卖出)  : {pct(m.get('win_rate'))}",
            f"年换手率    : {m.get('turnover')}",
            f"平均仓位    : {pct(m.get('avg_exposure'))}",
            f"交易次数    : {m.get('num_trades')}，总费用 {m.get('total_fees', 0):,.2f}",
        ]
