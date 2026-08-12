"""
券商适配器层 — 统一接口，屏蔽不同券商API差异

支持模式：
  - simulation: 模拟交易（文件存储，无需开户）【默认】
  - qmt:        迅投QMT（Windows，需券商开通量化权限）
  - webhook:    HTTP Webhook（支持任何 REST API 券商）
  - joinquant:  聚宽（仅数据，真实下单需注册 joinquant.com）

快速开始：
  1. 模拟交易（默认）：直接用，无需配置
  2. 真实券商：设置 BROKER_MODE=qmt 并配置环境变量
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Any

# ─── 数据模型 ────────────────────────────────────────────────────


@dataclass
class Order:
    order_id: str
    stock_code: str
    direction: str  # "buy" | "sell"
    price: float
    volume: int
    status: str  # "pending" | "filled" | "cancelled" | "rejected"
    created_at: str
    horizon: str = "medium"  # "short" | "medium" | "long"
    filled_at: Optional[str] = None
    filled_price: Optional[float] = None
    stock_name: str = ""


@dataclass
class Position:
    stock_code: str
    stock_name: str
    volume: int
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    pnl_ratio: float
    horizon: str = "medium"  # "short" | "medium" | "long"


# ─── 券商适配器基类 ───────────────────────────────────────────────


class BrokerAdapter(ABC):
    """券商适配器抽象基类"""

    @abstractmethod
    def buy(self, stock_code: str, volume: int, price: Optional[float] = None) -> Order:
        ...

    @abstractmethod
    def sell(self, stock_code: str, volume: int, price: Optional[float] = None) -> Order:
        ...

    @abstractmethod
    def get_positions(self) -> List[Position]:
        ...

    @abstractmethod
    def get_orders(self, limit: int = 50) -> List[Order]:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_balance(self) -> dict:
        ...


# ─── 工厂函数 ─────────────────────────────────────────────────────


def get_broker(mode: Optional[str] = None, **kwargs) -> BrokerAdapter:
    """
    根据 mode 返回对应券商适配器

    环境变量：
      BROKER_MODE   = simulation | qmt | webhook
      WEBHOOK_*     = webhook 模式配置（见 webhook_broker.py）
      QMT_*         = QMT 模式配置（见 qmt_broker.py）
    """
    import os
    mode = mode or os.getenv("BROKER_MODE", "simulation")

    if mode == "simulation":
        from simulation_broker import SimulationBroker
        return SimulationBroker(**kwargs)

    elif mode == "qmt":
        from qmt_broker import QMTBroker
        # QMTBroker 实现了 BrokerAdapter 的全部方法
        return QMTBroker()

    elif mode == "webhook":
        from webhook_broker import WebhookBroker
        return WebhookBroker(**kwargs)

    elif mode == "joinquant":
        from joinquant_broker import JoinQuantBroker
        return JoinQuantBroker(
            username=kwargs.get("username", os.getenv("JQ_USERNAME")),
            password=kwargs.get("password", os.getenv("JQ_PASSWORD")),
        )

    else:
        raise ValueError(f"Unknown broker mode: {mode}. 可选: simulation | qmt | webhook")


# ─── 通用工具 ─────────────────────────────────────────────────────


def normalize_stock_code(code: str) -> str:
    """统一股票代码格式：6位数字"""
    code = code.strip().upper()
    if code.startswith(("SH", "SZ")):
        code = code[2:]
    return code


def format_order_summary(order: Order) -> str:
    direction_zh = "买入" if order.direction == "buy" else "卖出"
    status_zh = {"pending": "挂单中", "filled": "已成交", "cancelled": "已撤单", "rejected": "已拒绝"}
    s = status_zh.get(order.status, order.status)
    price_str = f"{order.filled_price:.2f}" if order.filled_price else f"{order.price:.2f}"
    return f"{direction_zh} {order.stock_code} × {order.volume}股 @{price_str} [{s}]"
