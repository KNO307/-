# QuantLab

一个可真实运行的量化策略研究与回测系统：真实行情接入、事件驱动回测、绩效归因、自包含 HTML 报告，以及基于 Python 标准库的 Web 界面。

不是静态页面演示——所有净值、交易与指标均由后端逐日撮合计算得出。

## 设计要点

借鉴 TradingAgents 的工程化思路，但去掉了 LLM/多智能体部分，只保留量化研究真正需要的骨架：

| TradingAgents 的做法 | QuantLab 的落地 |
| --- | --- |
| point-in-time 数据夹紧，禁止未来数据 | `DataPortal.history(ticker, as_of, lookback)` 强制截断；回测引擎只在调仓日以当日收盘组装面板 |
| 类型化数据错误与 NO_DATA 哨兵 | `DataError` / `NoDataError` / `DataUnavailableError`，调用方可以区分"没数据"和"源不可用" |
| vendor 回退链 | Nasdaq → 确定性合成行情；Stooq 与本地 CSV 作为可替换 provider |
| 确定性验证快照 | 合成数据由固定种子生成；所有回测有唯一 `run_id`，产物完整落盘可复现 |
| 策略/信号与决策分层 | `Strategy.generate_targets(panel)` 只产出目标权重，Broker 负责撮合与成本 |
| 结构化输出 + 可读报告双通道 | `metrics.json / equity.csv / trades.csv` + 自包含 `report.html` + Web JSON API |
| 评估边界诚实 | 报告明确标注数据来源、成本假设、回测区间与现金仓位，不粉饰结果 |

## 架构

```
数据接入                 策略信号                 回测引擎                 结果输出
┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Nasdaq API   │   │ Momentum 12-1    │   │ DataPortal       │   │ metrics.json     │
│ Stooq API    │──▶│ MeanReversion    │──▶│  ↓ point-in-time │──▶│ equity.csv       │
│ Local CSV    │   │ DualMA 50/200    │   │ Strategy         │   │ trades.csv       │
│ Synthetic    │   │ Composite 3 因子 │   │  ↓ 目标权重       │   │ report.html      │
└──────────────┘   │ Buy & Hold       │   │ Broker t+1 撮合   │   │ Web API / 页面    │
                   └──────────────────┘   │  ↓ 成本/持仓      │   └──────────────────┘
                                          │ Metrics 绩效归因  │
                                          └──────────────────┘
```

目录：

```
quantlab/
├── quantlab/
│   ├── data/        # base / nasdaq / stooq / csv_provider / synthetic / portal
│   ├── strategy/    # base / momentum / mean_reversion / dual_ma / composite / buyhold
│   ├── engine/      # broker / backtest / metrics / result
│   ├── report/      # 自包含 HTML（内联 CSS + 内联 SVG）
│   ├── web/         # stdlib ThreadingHTTPServer + 静态页面
│   ├── cli.py       # list / fetch / run / demo / serve
│   └── config.py
├── tests/           # 22 项 unittest，无 pytest 依赖
├── runs/            # 每次回测的产物与报告
└── requirements.txt # pandas / numpy / requests
```

## 快速开始

```bash
cd /workspace/quantlab
pip install -r requirements.txt

python -m quantlab list          # 查看策略与默认股票池
python -m quantlab fetch         # 下载并缓存 21 只美股 + SPY 的真实日线
python -m quantlab demo          # 用全部策略跑 2019-01-01 ~ 2025-12-31
python -m quantlab serve --port 8000
```

打开 `http://localhost:8000` 即可在页面中选择策略、股票池、区间与费用并运行回测。

单次回测：

```bash
python -m quantlab run \
  --strategy momentum \
  --start 2019-01-01 --end 2025-12-31 \
  --capital 1000000 \
  --commission-bp 2 --slippage-bp 5 \
  --top-n 5 \
  --out runs/momentum_demo
```

离线运行（不联网，使用确定性合成行情）：

```bash
python -m quantlab demo --offline --out runs/demo_offline
```

## 策略

| 名称 | 逻辑 | 调仓 |
| --- | --- | --- |
| `momentum` | 12-1 动量（跳过最近 21 日），要求收盘价在 200 日均线上，选前 N 名等权 | 每月 |
| `mean_reversion` | 在长期趋势向上的股票中，选 20 日 z-score 最负的标的 | 每周 |
| `dual_ma` | 50 日均线高于 200 日均线，按均线乖离率排序选前 N | 每月 |
| `composite` | 12-1 动量、63 日低波动、200 日趋势三个因子横截面 z-score 合成 | 每月 |
| `buy_hold` | 期初等权买入股票池全部标的 | 仅一次 |

所有策略只输出目标权重，单票权重上限由策略声明，引擎再统一做上限与现金缓冲处理。

## 回测内核

时间线（杜绝前视的核心）：

```
t 日收盘：DataPortal 只提供 <= t 的数据 → 策略生成目标权重
t+1 开盘：Broker 按开盘价成交（买入价 × (1+滑点)，卖出价 × (1-滑点)）
t 日收盘：按收盘价盯市，记录权益、仓位与回撤
```

- **成本模型**：佣金按成交额收取（默认 2bp），滑点直接体现在成交价（默认 5bp），先卖后买避免现金不足。
- **仓位约束**：只做多、整数股、无杠杆、默认 2% 现金缓冲，单票权重上限由策略控制。
- **防前视**：数据门户截断、策略只接收 `as_of` 之前的面板、成交发生在信号次日开盘；`tests/test_engine.py` 用"篡改未来价格"的方式验证过去权益与成交完全不变。
- **可复现**：行情落盘缓存，合成数据固定种子；结果包含 `run_id`、配置、数据来源与生成时间。

绩效指标：总收益、CAGR、年化波动、Sharpe、Sortino、最大回撤、Calmar、Alpha/Beta、信息比率、月度胜率、卖出胜率、年化换手率、平均仓位、最佳/最差月份、总费用。

## 数据接入

- **Nasdaq**（默认）：`api.nasdaq.com` 历史行情 JSON，约 10 年日线，价格已做拆股调整。按标的缓存到 `.cache/nasdaq/`。
- **Stooq**：保留完整实现（CSV 接口 + 缓存 + 重试），当前站点对数据中心 IP 的 CSV 下载返回 `Access denied`，因此不作为默认源。
- **本地 CSV**：`LocalCSVProvider(data_dir)`，文件名 `TICKER.csv`，列名大小写不敏感。
- **合成行情**：`SyntheticProvider`，由市场因子 + 个股 beta/特质波动 + 牛熊阶段构成，固定种子、完全确定性，作为失败回退和离线测试数据。

任何回退都会记录在结果的 `data_sources` 与报告的"数据来源"区块中，不会把合成数据伪装成真实行情。

## Web API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/strategies` | 策略列表与描述 |
| GET | `/api/universe` | 默认股票池与基准 |
| POST | `/api/backtest` | 执行回测，返回完整 JSON 结果 |
| GET | `/api/runs` | 历史回测摘要 |
| GET | `/api/runs/<run_id>` | 单次回测完整结果 |
| GET | `/reports/<run_id>.html` | 自包含 HTML 报告 |
| GET | `/runs/<run_id>/equity.csv` | 净值序列下载 |

请求示例：

```bash
curl -X POST http://localhost:8000/api/backtest \
  -H 'Content-Type: application/json' \
  -d '{
    "strategy": "composite",
    "tickers": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,JNJ,PG,UNH,HD,V,MA,COST,AVGO,LLY,MRK,KO,WMT",
    "start": "2020-01-01",
    "end": "2024-12-31",
    "capital": 1000000,
    "commission_bp": 2,
    "slippage_bp": 5,
    "params": {"top_n": 5}
  }'
```

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖的关键行为：

- `test_data`：OHLCV 清洗、point-in-time 截断、篡改未来数据不影响历史。
- `test_engine`：t+1 开盘成交价、佣金/滑点扣减、Broker 现金与已实现盈亏、未来价格篡改不影响过去权益、payload 可 JSON 序列化。
- `test_strategy`：动量排序、趋势过滤空仓、均值回归选超跌、权重上限与买入持有等权。
- `test_metrics`：总收益、最大回撤、CAGR、平坦净值、月度表。

## 真实数据回测结果

2019-01-01 ~ 2025-12-31，21 只美股，初始资金 100 万，佣金 2bp、滑点 5bp：

| 策略 | 总收益 | 年化 | Sharpe | 最大回撤 | 交易笔数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 动量 12-1 | 469.87% | 28.31% | 1.08 | -29.12% | 413 |
| 均值回归 | 149.62% | 14.00% | 0.80 | -25.00% | 1743 |
| 双均线 | 616.10% | 32.58% | 1.16 | -33.41% | 441 |
| 多因子合成 | 381.21% | 25.24% | 1.08 | -28.70% | 433 |
| 等权买入持有 | 627.11% | 32.87% | 1.20 | -36.37% | 21 |

这七年是美股（尤其科技股）强势期，等权买入持有跑赢大多数主动调仓策略，这是真实结果，不做修饰。不同区间、股票池与成本假设会显著改变排序。

## 已知边界

- 日频、只做多、整数股、无杠杆、无做空；未建模停牌、涨跌停与冲击成本。
- Nasdaq 价格做了拆股调整但未包含分红；用未复权口径计算收益会略低估长期总回报。
- 股票池是当前高流动性名单，存在幸存者偏差。
- 滑点使用固定 bp，未按成交额/波动率动态建模。
- 历史回测不代表未来收益，本项目仅用于研究与教学，不构成投资建议。
