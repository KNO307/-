"""自包含 HTML 回测报告（内联 CSS + 内联 SVG，无任何 CDN 依赖）。"""
from __future__ import annotations

import html
from datetime import datetime

from ..engine.result import BacktestResult

_CSS = """
:root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--bg:#f8fafc;--card:#ffffff;
--brand:#0f766e;--brand-soft:#ccfbf1;--up:#15803d;--down:#b91c1c;--bench:#94a3b8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
font-size:14px;line-height:1.55}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 64px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:24px;flex-wrap:wrap}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:13px}
.badge{display:inline-block;background:var(--brand-soft);color:#115e59;border-radius:999px;
padding:3px 10px;font-size:12px;font-weight:600;margin-right:6px}
section{margin-top:26px}
h2{font-size:16px;margin:0 0 12px;letter-spacing:.01em}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .k{color:var(--muted);font-size:12px;margin-bottom:4px}
.card .v{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.card .v.up{color:var(--up)}.card .v.down{color:var(--down)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.chart{width:100%;height:auto;display:block}
.legend{display:flex;gap:18px;margin:0 0 10px;color:var(--muted);font-size:12px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-size:12px;font-weight:600;background:#fbfdff}
tbody tr:hover{background:#f8fafc}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;font-weight:600}
.tag.buy{background:#dcfce7;color:#166534}.tag.sell{background:#fee2e2;color:#991b1b}
.pos{color:var(--up)}.neg{color:var(--down)}
.heat{border-collapse:separate;border-spacing:2px;font-size:12px}
.heat td,.heat th{border:none;padding:5px 6px;text-align:center;border-radius:4px;min-width:44px}
.heat th{background:transparent;color:var(--muted);font-weight:500}
.heat td.year{font-weight:600;color:var(--ink);text-align:right;background:transparent}
.note{color:var(--muted);font-size:12px;margin-top:10px}
.warn{background:#fffbeb;border:1px solid #fde68a;color:#92400e;border-radius:10px;
padding:10px 14px;font-size:13px;margin-top:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}
footer{margin-top:40px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:16px}
"""


# ---------------------------------------------------------------------- format
def _pct(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.{digits}f}%"


def _money(value) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _cls(value) -> str:
    if value is None:
        return ""
    return "up" if value > 0 else ("down" if value < 0 else "")


# ---------------------------------------------------------------------- charts
def _points(values: list[float], x0: float, y0: float, w: float, h: float,
            vmin: float, vmax: float) -> str:
    n = len(values)
    span = (vmax - vmin) or 1.0
    out = []
    for i, v in enumerate(values):
        x = x0 + (w * i / (n - 1) if n > 1 else 0)
        y = y0 + h - (v - vmin) / span * h
        out.append(f"{x:.1f},{y:.1f}")
    return " ".join(out)


def _yaxis(vmin: float, vmax: float, x0: float, y0: float, w: float, h: float,
           fmt) -> str:
    parts = []
    for i in range(5):
        frac = i / 4
        y = y0 + h - frac * h
        value = vmin + frac * (vmax - vmin)
        parts.append(
            f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x0 + w:.1f}" y2="{y:.1f}" '
            f'stroke="#eef2f7" stroke-width="1"/>'
            f'<text x="{x0 - 8:.1f}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#94a3b8">{fmt(value)}</text>'
        )
    return "".join(parts)


def _xlabels(dates: list[str], x0: float, y0: float, w: float, h: float) -> str:
    if not dates:
        return ""
    picks = [0, len(dates) // 4, len(dates) // 2, 3 * len(dates) // 4, len(dates) - 1]
    parts = []
    for i in sorted(set(picks)):
        x = x0 + (w * i / (len(dates) - 1) if len(dates) > 1 else 0)
        anchor = "middle"
        parts.append(
            f'<text x="{x:.1f}" y="{y0 + h + 20:.1f}" text-anchor="{anchor}" '
            f'font-size="11" fill="#94a3b8">{dates[i]}</text>'
        )
    return "".join(parts)


def _equity_chart(result: BacktestResult) -> str:
    eq = result.equity
    if eq.empty:
        return "<p>无净值数据</p>"
    dates = [d.strftime("%Y-%m-%d") for d in eq.index]
    strategy = eq["equity"].tolist()
    benchmark = eq["benchmark"].tolist()
    values = strategy + benchmark
    vmin, vmax = min(values), max(values)
    pad = (vmax - vmin) * 0.08 or max(vmax * 0.02, 1.0)
    vmin, vmax = vmin - pad, vmax + pad
    w, h, x0, y0 = 860.0, 300.0, 74.0, 12.0
    fmt = lambda v: f"{v / 1_000_000:.2f}M" if abs(v) >= 1_000_000 else f"{v:,.0f}"
    return f"""
<div class="legend">
  <span><i class="dot" style="background:#0f766e"></i>策略净值</span>
  <span><i class="dot" style="background:#94a3b8"></i>基准（{html.escape(result.config.get('benchmark', 'SPY'))}）</span>
</div>
<svg class="chart" viewBox="0 0 {x0 + w + 20:.0f} {y0 + h + 34:.0f}" role="img" aria-label="净值曲线">
  {_yaxis(vmin, vmax, x0, y0, w, h, fmt)}
  <polyline fill="none" stroke="#94a3b8" stroke-width="1.6"
    points="{_points(benchmark, x0, y0, w, h, vmin, vmax)}"/>
  <polyline fill="none" stroke="#0f766e" stroke-width="2.2"
    points="{_points(strategy, x0, y0, w, h, vmin, vmax)}"/>
  {_xlabels(dates, x0, y0, w, h)}
</svg>"""


def _drawdown_chart(result: BacktestResult) -> str:
    eq = result.equity
    if eq.empty:
        return "<p>无回撤数据</p>"
    dates = [d.strftime("%Y-%m-%d") for d in eq.index]
    values = eq["drawdown"].tolist()
    vmin = min(values + [0.0])
    vmax = 0.0
    span = (vmax - vmin) or 1.0
    w, h, x0, y0 = 860.0, 180.0, 74.0, 12.0
    pts = _points(values, x0, y0, w, h, vmin, vmax)
    base_y = y0 + h - (vmax - vmin) / span * h
    area = f"{x0:.1f},{base_y:.1f} " + pts + f" {x0 + w:.1f},{base_y:.1f}"
    fmt = lambda v: f"{v * 100:.0f}%"
    return f"""
<svg class="chart" viewBox="0 0 {x0 + w + 20:.0f} {y0 + h + 34:.0f}" role="img" aria-label="回撤">
  {_yaxis(vmin, 0.0, x0, y0, w, h, fmt)}
  <polygon fill="rgba(185,28,28,.16)" points="{area}"/>
  <polyline fill="none" stroke="#b91c1c" stroke-width="1.6" points="{pts}"/>
  {_xlabels(dates, x0, y0, w, h)}
</svg>"""


def _heatmap(result: BacktestResult) -> str:
    table = result.monthly_returns
    if table is None or table.empty:
        return "<p>无月度数据</p>"
    values = [
        float(v) for v in table.to_numpy().ravel()
        if v == v
    ]
    max_abs = max((abs(v) for v in values), default=0.0) or 1.0
    month_names = ["1月", "2月", "3月", "4月", "5月", "6月",
                   "7月", "8月", "9月", "10月", "11月", "12月"]
    head = "".join(f"<th>{m}</th>" for m in month_names)
    rows = []
    for year, row in table.iterrows():
        cells = []
        for month in range(1, 13):
            value = row.get(month)
            if value is None or value != value:
                cells.append("<td></td>")
                continue
            alpha = min(abs(float(value)) / max_abs, 1.0) * 0.78 + 0.08
            color = (
                f"rgba(21,128,61,{alpha:.2f})" if value >= 0
                else f"rgba(185,28,28,{alpha:.2f})"
            )
            text_color = "#ffffff" if alpha > 0.45 else "#334155"
            cells.append(
                f'<td style="background:{color};color:{text_color}" '
                f'title="{year}-{month:02d}">{value * 100:.1f}</td>'
            )
        rows.append(f'<tr><td class="year">{int(year)}</td>{"".join(cells)}</tr>')
    return f"""
<table class="heat">
  <thead><tr><th></th>{head}<th>全年</th></tr></thead>
  <tbody>
    {"".join(rows)}
  </tbody>
</table>
<p class="note">单位为百分比；颜色越深表示幅度越大。</p>"""


# ---------------------------------------------------------------------- tables
def _positions_table(result: BacktestResult) -> str:
    df = result.positions
    if df is None or df.empty:
        return "<p>期末无持仓（空仓）。</p>"
    rows = "".join(
        f"<tr><td>{html.escape(str(r['ticker']))}</td><td>{int(r['shares']):,}</td>"
        f"<td>{_num(r['avg_cost'])}</td><td>{_num(r['last_price'])}</td>"
        f"<td>{_money(r['market_value'])}</td><td>{_pct(r['weight'])}</td></tr>"
        for _, r in df.iterrows()
    )
    return f"""
<table><thead><tr><th>标的</th><th>股数</th><th>成本价</th><th>最新价</th>
<th>市值</th><th>权重</th></tr></thead><tbody>{rows}</tbody></table>"""


def _trades_table(result: BacktestResult, limit: int = 120) -> str:
    df = result.trades
    if df is None or df.empty:
        return "<p>回测期内没有成交。</p>"
    rows = []
    for _, r in df.tail(limit).iloc[::-1].iterrows():
        side = str(r["side"])
        cls = "buy" if side == "BUY" else "sell"
        pnl = r.get("realized_pnl")
        pnl_cell = "-" if pnl is None or pnl != pnl else f'<span class="{_cls(pnl)}">{_money(pnl)}</span>'
        rows.append(
            f"<tr><td>{r['date'].strftime('%Y-%m-%d')}</td><td>{html.escape(str(r['ticker']))}</td>"
            f'<td><span class="tag {cls}">{"买入" if side == "BUY" else "卖出"}</span></td>'
            f"<td>{int(r['shares']):,}</td><td>{_num(r['price'])}</td>"
            f"<td>{_money(r['notional'])}</td><td>{_num(r['fee'])}</td><td>{pnl_cell}</td></tr>"
        )
    shown = min(limit, len(df))
    return f"""
<table><thead><tr><th>日期</th><th>标的</th><th>方向</th><th>股数</th><th>成交价</th>
<th>成交额</th><th>费用</th><th>已实现盈亏</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<p class="note">共 {len(df):,} 笔成交，此处展示最近 {shown} 笔。</p>"""


# ---------------------------------------------------------------------- main
def render_html(result: BacktestResult, report_title: str | None = None) -> str:
    m = result.metrics
    cfg = result.config

    def card(label: str, value: str, cls: str = "") -> str:
        return (
            f'<div class="card"><div class="k">{label}</div>'
            f'<div class="v {cls}">{value}</div></div>'
        )

    cards = "".join(
        [
            card("总收益", _pct(m.get("total_return")), _cls(m.get("total_return"))),
            card("年化收益 (CAGR)", _pct(m.get("cagr")), _cls(m.get("cagr"))),
            card("年化波动", _pct(m.get("annual_volatility"))),
            card("Sharpe", _num(m.get("sharpe"), 2), _cls(m.get("sharpe"))),
            card("Sortino", _num(m.get("sortino"), 2), _cls(m.get("sortino"))),
            card("最大回撤", _pct(m.get("max_drawdown")), "down"),
            card("Calmar", _num(m.get("calmar"), 2)),
            card("基准收益", _pct(m.get("benchmark_return")), _cls(m.get("benchmark_return"))),
            card("超额收益", _pct(m.get("excess_return")), _cls(m.get("excess_return"))),
            card("信息比率", _num(m.get("information_ratio"), 2)),
            card("Alpha / Beta", f'{_pct(m.get("alpha"))} / {_num(m.get("beta"), 2)}'),
            card("期末权益", _money(m.get("final_equity"))),
            card("平均仓位", _pct(m.get("avg_exposure"))),
            card("年换手率", _num(m.get("turnover"), 2)),
            card("胜率（卖出）", _pct(m.get("win_rate"))),
            card("交易笔数", f'{int(m.get("num_trades", 0)):,}'),
        ]
    )

    warning_html = "".join(
        f'<div class="warn">{html.escape(w)}</div>' for w in result.warnings
    )
    source_items = "".join(
        f'<span class="badge">{html.escape(k)} · {html.escape(str(v))}</span>'
        for k, v in result.data_sources.items()
    )
    params = " · ".join(
        f"{k}={v}" for k, v in result.strategy_params.items()
        if k not in ("max_weight", "rebalance")
    )
    freq_map = {"D": "每日", "W": "每周", "M": "每月", "Q": "每季", "ONCE": "一次"}
    rebalance = freq_map.get(result.strategy_params.get("rebalance", ""), result.strategy_name)
    title = report_title or f"QuantLab 回测报告 · {result.strategy_label}"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>{html.escape(result.strategy_label)}</h1>
      <div class="sub">
        区间 {html.escape(str(cfg.get('start')))} ~ {html.escape(str(cfg.get('end')))} ·
        初始资金 ${_money(cfg.get('initial_capital'))} ·
        佣金 {cfg.get('commission_bp')}bp · 滑点 {cfg.get('slippage_bp')}bp ·
        调仓 {html.escape(rebalance)}
      </div>
      <div class="sub" style="margin-top:8px">
        <span class="badge">run {html.escape(result.run_id)}</span>
        <span class="badge">交易成本已计入</span>
        <span class="badge">t 收盘信号 → t+1 开盘成交</span>
      </div>
    </div>
    <div class="sub" style="text-align:right">
      生成时间<br/>{html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}
      <div style="margin-top:6px">参数：{html.escape(params)}</div>
    </div>
  </header>

  {warning_html}

  <section>
    <h2>核心指标</h2>
    <div class="cards">{cards}</div>
  </section>

  <section>
    <h2>净值曲线</h2>
    <div class="panel">{_equity_chart(result)}</div>
  </section>

  <section>
    <h2>回撤</h2>
    <div class="panel">{_drawdown_chart(result)}</div>
  </section>

  <div class="grid2">
    <section>
      <h2>月度收益热力图</h2>
      <div class="panel" style="overflow-x:auto">{_heatmap(result)}</div>
    </section>
    <section>
      <h2>期末持仓</h2>
      <div class="panel" style="overflow-x:auto">{_positions_table(result)}</div>
    </section>
  </div>

  <section>
    <h2>成交明细</h2>
    <div class="panel" style="overflow-x:auto">{_trades_table(result)}</div>
  </section>

  <section>
    <h2>数据来源</h2>
    <div class="panel">
      {source_items or '<span class="sub">未知</span>'}
      <p class="note">数据接入、信号生成、撮合与指标计算全部由 QuantLab 完成后端执行；
      本报告为静态快照，不依赖任何前端框架或 CDN。</p>
    </div>
  </section>

  <footer>
    QuantLab · 事件驱动日频回测。历史表现不代表未来收益，本报告仅用于研究与教学。
  </footer>
</div>
</body>
</html>"""
