"""
聚宽 JoinQuant 券商适配器

前置条件：
  1. pip install jqdatasdk pandas
  2. 已有聚宽账号并开通 JQData API 权限

环境变量：
  JQ_USERNAME=你的聚宽账号
  JQ_PASSWORD=你的聚宽登录密码
  BROKER_MODE=joinquant

注意：
  JQData 是数据 API（查询行情/财务数据），不含下单功能。
  这里使用 jqdatasdk 查询数据作为行情来源。
  真实下单需要通过 QMT 或其他有交易权限的渠道。
"""
import os
from datetime import datetime
from typing import Optional, List

from broker_adapter import BrokerAdapter, Order, Position, normalize_stock_code


# ── 懒加载 jqdatasdk ────────────────────────────────────────────

_jq_client = None


def _get_jq_client():
    """获取聚宽客户端（单例，延迟初始化）"""
    global _jq_client
    if _jq_client is not None:
        return _jq_client

    import jqdatasdk as jq
    username = os.getenv("JQ_USERNAME", "")
    password = os.getenv("JQ_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "聚宽密码未设置。请设置环境变量：\n"
            "  export JQ_PASSWORD=你的聚宽登录密码\n"
            "  BROKER_MODE=joinquant python3 main.py ..."
        )

    jq.auth(username, password)
    if not jq.is_auth():
        raise RuntimeError("聚宽认证失败，请检查账号密码")
    _jq_client = jq
    return jq


class JoinQuantBroker(BrokerAdapter):
    """
    聚宽 JQData 适配器

    功能：
      - 实时行情查询（通过 jqdatasdk）
      - 技术指标计算
      - 持仓/账户查询（模拟盘数据）
      - 下单信号生成（模拟撮合）

    注意：JQData API 本身不提供真实下单功能。
    下单通过内部模拟撮合引擎实现（按市价即时成交）。
    如需真实下单，请切换到 BROKER_MODE=qmt。
    """

    def __init__(self, **kwargs):
        self._jq = None  # 延迟初始化
        self._sim_broker = None  # 延迟初始化

    def _get_sim_broker(self):
        if self._sim_broker is None:
            from simulation_broker import SimulationBroker
            self._sim_broker = SimulationBroker()
        return self._sim_broker

    @property
    def jq(self):
        if self._jq is None:
            self._jq = _get_jq_client()
        return self._jq

    def buy(self, stock_code: str, volume: int, price: Optional[float] = None) -> Order:
        stock_code = normalize_stock_code(stock_code)
        jq_code = self._to_jq_code(stock_code)

        # 获取实时价格（通过聚宽）
        if price is None or price == 0:
            df = self.jq.get_price(jq_code, count=1, frequency='1m')
            price = float(df['close'].iloc[-1])

        # 通过 simulation broker 实际执行（写入 SQLite）
        sim = self._get_sim_broker()
        order = sim.buy(stock_code, volume, price)
        return order

    def sell(self, stock_code: str, volume: int, price: Optional[float] = None) -> Order:
        stock_code = normalize_stock_code(stock_code)
        jq_code = self._to_jq_code(stock_code)

        if price is None or price == 0:
            df = self.jq.get_price(jq_code, count=1, frequency='1m')
            price = float(df['close'].iloc[-1])

        sim = self._get_sim_broker()
        order = sim.sell(stock_code, volume, price)
        return order

    def get_positions(self) -> List[Position]:
        return self._get_sim_broker().get_positions()

    def get_orders(self, limit: int = 50) -> List[Order]:
        return self._get_sim_broker().get_orders(limit)

    def cancel_order(self, order_id: str) -> bool:
        return self._get_sim_broker().cancel_order(order_id)

    def get_balance(self) -> dict:
        return self._get_sim_broker().get_balance()

    # ── 工具 ───────────────────────────────────────────────────

    @staticmethod
    def _to_jq_code(code: str) -> str:
        code = normalize_stock_code(code)
        return f"{code}.XSHG" if code.startswith(("6", "9")) else f"{code}.XSHE"

    @staticmethod
    def _from_jq_code(jq_code: str) -> str:
        return jq_code.split(".")[0]
