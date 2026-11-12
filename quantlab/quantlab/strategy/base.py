"""策略基类、注册表与公共权重工具。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..data.portal import MarketPanel

_REGISTRY: dict[str, type["Strategy"]] = {}


def register(cls: type["Strategy"]) -> type["Strategy"]:
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} 缺少 name")
    _REGISTRY[cls.name] = cls
    return cls


def list_strategies() -> list[dict]:
    return [
        {
            "name": cls.name,
            "label": cls.label,
            "description": cls.description,
            "rebalance": cls.rebalance,
            "warmup": cls.warmup,
        }
        for cls in _REGISTRY.values()
    ]


def create_strategy(name: str, **params) -> "Strategy":
    if name not in _REGISTRY:
        raise KeyError(f"未知策略：{name}，可选：{', '.join(_REGISTRY)}")
    return _REGISTRY[name](**params)


class Strategy(ABC):
    """输入截止当日收盘的历史面板，输出目标权重（t 收盘出信号，t+1 开盘成交）。"""

    name: str = ""
    label: str = ""
    description: str = ""
    rebalance: str = "M"  # D / W / M / Q / ONCE
    warmup: int = 260  # 需要的历史交易日数量（提示用）
    max_weight: float = 0.25  # 单票权重上限

    @abstractmethod
    def generate_targets(self, panel: MarketPanel) -> dict[str, float]:
        """返回 {ticker: weight}，weight 之和不超过 1，未列出的标的视为 0。"""

    # ---------------------------------------------------------------- helpers
    def _equal_weights(self, tickers: list[str]) -> dict[str, float]:
        if not tickers:
            return {}
        base = 1.0 / len(tickers)
        return {t: min(base, self.max_weight) for t in tickers}

    def params(self) -> dict:
        values = {
            k: v
            for k, v in vars(self).items()
            if not k.startswith("_") and isinstance(v, (int, float, str, bool))
        }
        values["rebalance"] = self.rebalance
        return values

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.params()})"
