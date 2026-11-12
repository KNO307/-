"""QuantLab - 一个可运行的量化策略研究与回测系统。

模块划分：
    data     : 数据接入（Stooq / 本地 CSV / 确定性合成数据）+ 防前视数据门户
    strategy : 策略信号（动量、均值回归、双均线、多因子合成、买入持有）
    engine   : 回测引擎（Broker、t+1 撮合、绩效指标、结果导出）
    report   : 自包含 HTML 报告
    web      : stdlib HTTP 服务 + 前端页面
"""

__version__ = "1.0.0"
