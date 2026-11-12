"""基于 Python 标准库的 HTTP 服务（无 FastAPI/Flask 依赖）。

接口：
    GET  /api/health          健康检查
    GET  /api/strategies      策略列表
    GET  /api/universe        默认股票池
    GET  /api/runs            历史回测列表
    GET  /api/runs/<id>       单次回测完整结果
    POST /api/backtest        执行回测（同步）
    GET  /reports/<id>.html   自包含回测报告
    GET  /                    前端页面
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .. import __version__
from ..config import (
    BENCHMARK,
    CACHE_DIR,
    DEFAULT_CAPITAL,
    DEFAULT_CASH_BUFFER,
    DEFAULT_COMMISSION_BP,
    DEFAULT_END,
    DEFAULT_SLIPPAGE_BP,
    DEFAULT_START,
    DEFAULT_UNIVERSE,
    RUNS_DIR,
    STATIC_DIR,
)
from ..data import DataPortal, NasdaqProvider, SyntheticProvider
from ..engine import BacktestConfig, BacktestEngine
from ..report import render_html
from ..strategy import create_strategy, list_strategies
from ..strategy.base import _REGISTRY

logger = logging.getLogger(__name__)

MAX_BODY = 1 << 20  # 1MB
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _coerce_params(name: str, raw: dict | None) -> dict:
    if not raw:
        return {}
    import inspect

    cls = _REGISTRY[name]
    allowed = set(inspect.signature(cls.__init__).parameters) - {"self"}
    defaults = {
        k: v
        for k, v in vars(cls()).items()
        if not k.startswith("_") and isinstance(v, (int, float, bool, str))
    }
    clean: dict = {}
    for key, value in raw.items():
        if key not in allowed or key not in defaults:
            continue
        default = defaults[key]
        try:
            if isinstance(default, bool):
                clean[key] = bool(value)
            elif isinstance(default, int):
                clean[key] = int(value)
            elif isinstance(default, float):
                clean[key] = float(value)
            else:
                clean[key] = value
        except (TypeError, ValueError):
            continue
    return clean


class QuantLabService:
    def __init__(self, portal: DataPortal, runs_dir: Path = RUNS_DIR):
        self.portal = portal
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_existing()

    def _load_existing(self) -> None:
        for path in sorted(self.runs_dir.glob("*/payload.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self._runs[payload["run_id"]] = payload
            except (OSError, ValueError, KeyError):
                continue

    # ------------------------------------------------------------------ run
    def run_backtest(self, body: dict) -> dict:
        name = str(body.get("strategy") or "momentum")
        if name not in _REGISTRY:
            raise ValueError(f"未知策略：{name}")

        tickers = body.get("tickers") or list(DEFAULT_UNIVERSE)
        if isinstance(tickers, str):
            tickers = [t.strip() for t in tickers.replace("，", ",").split(",") if t.strip()]
        if not isinstance(tickers, list) or not tickers:
            raise ValueError("股票池不能为空")
        if len(tickers) > 50:
            raise ValueError("股票池最多 50 个标的")

        config = BacktestConfig(
            tickers=[str(t).strip().upper() for t in tickers],
            start=str(body.get("start") or DEFAULT_START),
            end=str(body.get("end") or DEFAULT_END),
            initial_capital=float(body.get("capital") or DEFAULT_CAPITAL),
            commission_bp=float(body.get("commission_bp", DEFAULT_COMMISSION_BP)),
            slippage_bp=float(body.get("slippage_bp", DEFAULT_SLIPPAGE_BP)),
            cash_buffer=float(body.get("cash_buffer", DEFAULT_CASH_BUFFER)),
            benchmark=str(body.get("benchmark") or BENCHMARK),
        )
        config.validate()
        if config.initial_capital < 10_000:
            raise ValueError("初始资金至少 10,000")

        params = _coerce_params(name, body.get("params") or {})
        strategy = create_strategy(name, **params)
        result = BacktestEngine(self.portal, config).run(strategy)

        run_dir = self.runs_dir / result.run_id
        result.save(run_dir)
        report_file = run_dir / "report.html"
        report_file.write_text(render_html(result), encoding="utf-8")

        payload = result.to_payload()
        payload["report_url"] = f"/reports/{result.run_id}.html"
        payload["downloads"] = {
            "metrics": f"/runs/{result.run_id}/metrics.json",
            "equity": f"/runs/{result.run_id}/equity.csv",
            "trades": f"/runs/{result.run_id}/trades.csv",
        }
        (run_dir / "payload.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        with self._lock:
            self._runs[result.run_id] = payload
        return payload

    # ------------------------------------------------------------------ list
    def list_runs(self) -> list[dict]:
        summaries = []
        for payload in self._runs.values():
            summaries.append(
                {
                    "run_id": payload["run_id"],
                    "strategy": payload["strategy"]["label"],
                    "start": payload["config"]["start"],
                    "end": payload["config"]["end"],
                    "total_return": payload["metrics"].get("total_return"),
                    "sharpe": payload["metrics"].get("sharpe"),
                    "created_at": payload.get("created_at"),
                    "report_url": payload.get("report_url"),
                }
            )
        return sorted(summaries, key=lambda x: x["created_at"] or "", reverse=True)

    def get_run(self, run_id: str) -> dict | None:
        return self._runs.get(run_id)

    def run_file(self, run_id: str, filename: str) -> Path | None:
        if "/" in run_id or ".." in run_id or "/" in filename or ".." in filename:
            return None
        path = (self.runs_dir / run_id / filename).resolve()
        if self.runs_dir.resolve() not in path.parents:
            return None
        return path if path.is_file() else None


class Handler(BaseHTTPRequestHandler):
    service: QuantLabService
    server_version = f"QuantLab/{__version__}"

    # ------------------------------------------------------------------ utils
    def log_message(self, fmt: str, *args):  # noqa: A003
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: int, payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _MIME.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # ------------------------------------------------------------------ GET
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in ("/", "/index.html"):
            self._file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in target.parents or not target.is_file():
                self._error(HTTPStatus.NOT_FOUND, "静态文件不存在")
                return
            self._file(target)
            return
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "version": __version__,
                    "time": datetime.now().isoformat(timespec="seconds"),
                },
            )
            return
        if path == "/api/strategies":
            self._json(HTTPStatus.OK, {"strategies": list_strategies()})
            return
        if path == "/api/universe":
            self._json(
                HTTPStatus.OK,
                {"universe": DEFAULT_UNIVERSE, "benchmark": BENCHMARK},
            )
            return
        if path == "/api/runs":
            self._json(HTTPStatus.OK, {"runs": self.service.list_runs()})
            return
        if path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/"):]
            payload = self.service.get_run(run_id)
            if payload is None:
                self._error(HTTPStatus.NOT_FOUND, f"找不到回测 {run_id}")
                return
            self._json(HTTPStatus.OK, payload)
            return
        if path.startswith("/reports/") and path.endswith(".html"):
            run_id = path[len("/reports/"):-len(".html")]
            file = self.service.run_file(run_id, "report.html")
            if file is None:
                self._error(HTTPStatus.NOT_FOUND, "报告不存在")
                return
            self._file(file)
            return
        if path.startswith("/runs/"):
            parts = path[len("/runs/"):].split("/")
            if len(parts) == 2:
                file = self.service.run_file(parts[0], parts[1])
                if file is not None:
                    self._file(file)
                    return
            self._error(HTTPStatus.NOT_FOUND, "文件不存在")
            return

        self._error(HTTPStatus.NOT_FOUND, f"未知路径 {path}")

    # ------------------------------------------------------------------ POST
    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/backtest":
            self._error(HTTPStatus.NOT_FOUND, "未知接口")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError("请求体为空或过大")
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
            payload = self.service.run_backtest(body)
            self._json(HTTPStatus.OK, payload)
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error("回测失败：%s\n%s", exc, traceback.format_exc())
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"回测失败：{exc}")


def build_portal(offline: bool = False, cache_dir: Path = CACHE_DIR) -> DataPortal:
    synthetic = SyntheticProvider()
    if offline:
        logger.info("离线模式：使用确定性合成行情")
        return DataPortal(primary=synthetic)
    return DataPortal(primary=NasdaqProvider(cache_dir), fallback=synthetic)


def serve(host: str = "0.0.0.0", port: int = 8000, offline: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    service = QuantLabService(build_portal(offline))
    handler = type("BoundHandler", (Handler,), {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    mode = "离线合成数据" if offline else "Stooq（失败回退合成数据）"
    print(f"QuantLab Web 服务已启动: http://{host}:{port}  数据源: {mode}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
    finally:
        httpd.server_close()
