"""QuantLab 命令行入口。"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from .config import (
    BENCHMARK,
    CACHE_DIR,
    DEFAULT_CAPITAL,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_UNIVERSE,
    RUNS_DIR,
)
from .data import DataError, DataPortal, NasdaqProvider, SyntheticProvider
from .engine import BacktestConfig, BacktestEngine
from .report import render_html
from .strategy import create_strategy, list_strategies

DEMO_STRATEGIES = ["momentum", "mean_reversion", "dual_ma", "composite", "buy_hold"]


def _portal(offline: bool) -> DataPortal:
    synthetic = SyntheticProvider()
    if offline:
        return DataPortal(primary=synthetic)
    return DataPortal(primary=NasdaqProvider(CACHE_DIR), fallback=synthetic)


def _tickers(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_UNIVERSE)
    return [t.strip().upper() for t in value.replace("，", ",").split(",") if t.strip()]


def _strategy(name: str, top_n: int | None):
    params = {} if name == "buy_hold" or top_n is None else {"top_n": int(top_n)}
    return create_strategy(name, **params)


def cmd_list(args) -> int:
    print("可用策略：")
    for item in list_strategies():
        print(f"  {item['name']:<16} {item['label']:<24} 调仓={item['rebalance']:<4} {item['description']}")
    print(f"\n基准：{BENCHMARK}")
    print(f"默认股票池（{len(DEFAULT_UNIVERSE)}）：{', '.join(DEFAULT_UNIVERSE)}")
    return 0


def cmd_fetch(args) -> int:
    tickers = _tickers(args.tickers) + [BENCHMARK]
    portal = _portal(offline=False)
    print(f"开始下载 {len(tickers)} 个标的的日线数据（缓存目录 {CACHE_DIR}）…")
    failed = 0
    for ticker in tickers:
        try:
            df = portal.history(ticker, as_of=args.end)
            print(f"  {ticker:<6} {len(df):>5} 行  {df.index[0].date()} ~ {df.index[-1].date()}")
        except DataError as exc:
            failed += 1
            print(f"  {ticker:<6} 失败：{exc}")
    print(f"完成，成功 {len(tickers) - failed} 个，失败 {failed} 个。")
    return 0 if failed == 0 else 1


def cmd_run(args) -> int:
    logging.basicConfig(level=logging.WARNING)
    config = BacktestConfig(
        tickers=_tickers(args.tickers),
        start=args.start,
        end=args.end,
        initial_capital=args.capital,
        commission_bp=args.commission_bp,
        slippage_bp=args.slippage_bp,
    )
    portal = _portal(args.offline)
    result = BacktestEngine(portal, config).run(_strategy(args.strategy, args.top_n))

    out_dir = Path(args.out) if args.out else RUNS_DIR / result.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = result.save(out_dir)
    if not args.no_report:
        report = out_dir / "report.html"
        report.write_text(render_html(result), encoding="utf-8")
        paths["report"] = str(report)

    print("\n".join(result.summary_lines()))
    if result.warnings:
        print("\n警告：")
        for warning in result.warnings:
            print(f"  - {warning}")
    print("\n输出文件：")
    for key, path in paths.items():
        print(f"  {key:<8} {path}")
    return 0


def cmd_demo(args) -> int:
    logging.basicConfig(level=logging.WARNING)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.out) if args.out else RUNS_DIR / f"demo_{stamp}"
    out_root.mkdir(parents=True, exist_ok=True)
    portal = _portal(args.offline)

    print(f"QuantLab 演示：{len(DEFAULT_UNIVERSE)} 只美股，{args.start} ~ {args.end}")
    print(f"数据模式：{'确定性合成行情（离线）' if args.offline else 'Nasdaq 真实日线（失败自动回退）'}\n")
    header = f"{'策略':<16}{'总收益':>10}{'年化':>10}{'Sharpe':>9}{'最大回撤':>11}{'交易':>8}  报告"
    print(header)
    print("-" * len(header))

    for name in DEMO_STRATEGIES:
        config = BacktestConfig(
            start=args.start,
            end=args.end,
            initial_capital=args.capital,
            commission_bp=args.commission_bp,
            slippage_bp=args.slippage_bp,
        )
        result = BacktestEngine(portal, config).run(_strategy(name, args.top_n))
        run_dir = out_root / name
        result.save(run_dir)
        report = run_dir / "report.html"
        report.write_text(render_html(result), encoding="utf-8")
        m = result.metrics
        print(
            f"{name:<16}{m['total_return'] * 100:>9.2f}%{m['cagr'] * 100:>9.2f}%"
            f"{m['sharpe']:>9.2f}{m['max_drawdown'] * 100:>10.2f}%{m['num_trades']:>8}  {report}"
        )

    print(f"\n全部产物已写入：{out_root}")
    print("启动 Web 界面：python -m quantlab serve --port 8000")
    return 0


def cmd_serve(args) -> int:
    from .web.server import serve

    serve(host=args.host, port=args.port, offline=args.offline)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quantlab",
        description="QuantLab：可运行的量化策略研究与回测系统",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出策略与默认股票池")
    p_list.set_defaults(func=cmd_list)

    p_fetch = sub.add_parser("fetch", help="下载并缓存日线数据")
    p_fetch.add_argument("--tickers", help="逗号分隔的股票代码，默认使用内置股票池")
    p_fetch.add_argument("--end", default=DEFAULT_END, help="截止日期")
    p_fetch.set_defaults(func=cmd_fetch)

    def add_run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--strategy", default="momentum", help="策略名（quantlab list 查看）")
        p.add_argument("--tickers", help="逗号分隔的股票代码，默认使用内置股票池")
        p.add_argument("--start", default=DEFAULT_START, help="开始日期 YYYY-MM-DD")
        p.add_argument("--end", default=DEFAULT_END, help="结束日期 YYYY-MM-DD")
        p.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金")
        p.add_argument("--commission-bp", type=float, default=2.0, help="佣金（基点）")
        p.add_argument("--slippage-bp", type=float, default=5.0, help="滑点（基点）")
        p.add_argument("--top-n", type=int, default=5, help="持仓数量")
        p.add_argument("--offline", action="store_true", help="只用确定性合成行情，不联网")

    p_run = sub.add_parser("run", help="运行单次回测并生成报告")
    add_run_args(p_run)
    p_run.add_argument("--out", help="输出目录")
    p_run.add_argument("--no-report", action="store_true", help="不生成 HTML 报告")
    p_run.set_defaults(func=cmd_run)

    p_demo = sub.add_parser("demo", help="运行全部策略并生成对比演示")
    add_run_args(p_demo)
    p_demo.add_argument("--out", help="输出目录")
    p_demo.set_defaults(func=cmd_demo)

    p_serve = sub.add_parser("serve", help="启动 Web 界面")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--offline", action="store_true", help="离线模式（合成行情）")
    p_serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
