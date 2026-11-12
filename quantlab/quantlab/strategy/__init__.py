"""策略注册入口：导入即完成注册。"""
from .base import Strategy, create_strategy, list_strategies, register
from .buyhold import BuyHoldStrategy
from .composite import CompositeStrategy
from .dual_ma import DualMAStrategy
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy

__all__ = [
    "Strategy",
    "create_strategy",
    "list_strategies",
    "register",
    "BuyHoldStrategy",
    "CompositeStrategy",
    "DualMAStrategy",
    "MeanReversionStrategy",
    "MomentumStrategy",
]
