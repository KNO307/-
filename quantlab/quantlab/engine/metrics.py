"""绩效指标：收益、风险、交易行为与相对基准表现。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _clean(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    s = pd.Series(series).astype(float).dropna()
    return s


def max_drawdown(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def monthly_return_table(equity: pd.Series) -> pd.DataFrame:
    """年 x 月 的收益率矩阵（小数），用于热力图。"""
    s = _clean(equity)
    if s.empty:
        return pd.DataFrame()
    month_end = s.resample("ME").last()
    rets = month_end.pct_change()
    if len(rets) > 0:
        rets.iloc[0] = month_end.iloc[0] / s.iloc[0] - 1.0
    frame = pd.DataFrame(
        {
            "year": rets.index.year,
            "month": rets.index.month,
            "return": rets.to_numpy(),
        }
    )
    table = frame.pivot(index="year", columns="month", values="return")
    table = table.reindex(columns=range(1, 13))
    return table


def compute_metrics(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    trades: pd.DataFrame | None = None,
    exposure: pd.Series | None = None,
    rf_annual: float = 0.0,
) -> dict:
    eq = _clean(equity)
    if len(eq) < 2:
        return {"num_days": int(len(eq)), "final_equity": float(eq.iloc[-1]) if len(eq) else 0.0}

    returns = eq.pct_change().dropna()
    n = len(returns)
    years = n / TRADING_DAYS if n else 0.0
    total_return = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    cagr = (
        float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0)
        if years > 0 and eq.iloc[0] > 0
        else 0.0
    )

    vol = float(returns.std(ddof=0) * math.sqrt(TRADING_DAYS)) if n > 1 else 0.0
    rf_daily = rf_annual / TRADING_DAYS
    excess = returns - rf_daily
    sharpe = (
        float(excess.mean() / returns.std(ddof=0) * math.sqrt(TRADING_DAYS))
        if n > 1 and returns.std(ddof=0) > 0
        else 0.0
    )
    downside = np.sqrt(np.mean(np.minimum(excess.to_numpy(), 0.0) ** 2)) if n else 0.0
    sortino = float(excess.mean() / downside * math.sqrt(TRADING_DAYS)) if downside > 0 else 0.0
    mdd = max_drawdown(eq)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0

    month_table = monthly_return_table(eq)
    monthly = month_table.to_numpy().ravel()
    monthly = monthly[np.isfinite(monthly)]
    best_month = float(np.max(monthly)) if monthly.size else 0.0
    worst_month = float(np.min(monthly)) if monthly.size else 0.0
    monthly_win = float((monthly > 0).mean()) if monthly.size else 0.0

    # 交易行为
    num_trades = 0
    total_fees = 0.0
    win_rate = 0.0
    turnover = 0.0
    if trades is not None and len(trades) > 0:
        tr = trades.copy()
        num_trades = int(len(tr))
        total_fees = float(tr["fee"].sum()) if "fee" in tr else 0.0
        sells = tr[tr["side"] == "SELL"] if "side" in tr else tr.iloc[:0]
        if len(sells) and "realized_pnl" in sells:
            pnl = pd.to_numeric(sells["realized_pnl"], errors="coerce").dropna()
            if len(pnl):
                win_rate = float((pnl > 0).mean())
        if "notional" in tr and years > 0:
            one_way = float(tr["notional"].sum()) / 2.0
            avg_equity = float(eq.mean())
            if avg_equity > 0:
                turnover = float(one_way / avg_equity / years)

    avg_exposure = float(_clean(exposure).mean()) if exposure is not None else 0.0

    # 相对基准
    alpha = beta = ir = 0.0
    bench_total = 0.0
    if benchmark is not None:
        b = _clean(benchmark).reindex(eq.index).ffill().dropna()
        if len(b) > 1 and b.iloc[0] > 0:
            b_ret = b.pct_change().dropna()
            aligned = pd.concat([returns, b_ret], axis=1, join="inner").dropna()
            if len(aligned) > 1:
                rp = aligned.iloc[:, 0].to_numpy()
                rb = aligned.iloc[:, 1].to_numpy()
                var_b = np.var(rb)
                beta = float(np.cov(rp, rb, ddof=0)[0, 1] / var_b) if var_b > 0 else 0.0
                alpha = float((rp.mean() - beta * rb.mean()) * TRADING_DAYS)
                active = rp - rb
                if active.std(ddof=0) > 0:
                    ir = float(active.mean() / active.std(ddof=0) * math.sqrt(TRADING_DAYS))
            bench_total = float(b.iloc[-1] / b.iloc[0] - 1.0)

    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "annual_volatility": round(vol, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(mdd, 6),
        "calmar": round(calmar, 4),
        "benchmark_return": round(bench_total, 6),
        "excess_return": round(total_return - bench_total, 6),
        "alpha": round(alpha, 6),
        "beta": round(beta, 4),
        "information_ratio": round(ir, 4),
        "win_rate": round(win_rate, 4),
        "turnover": round(turnover, 4),
        "avg_exposure": round(avg_exposure, 4),
        "best_month": round(best_month, 6),
        "worst_month": round(worst_month, 6),
        "monthly_win_rate": round(monthly_win, 4),
        "num_trades": num_trades,
        "total_fees": round(total_fees, 2),
        "num_days": int(len(eq)),
        "final_equity": round(float(eq.iloc[-1]), 2),
    }
