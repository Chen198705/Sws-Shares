"""
迅投 QMT 券商适配器

前置条件：
  1. Windows 或 macOS 虚拟机（QMT 暂无 Mac 原生版）
  2. 在支持的券商开户并开通量化交易权限（银河/国金/招商等）
  3. 安装 QMT 客户端，获取交易账号和极简口令
  4. pip install xtquant

安装：
  # Windows 命令行
  pip install xtquant

配置（设置环境变量或修改下方 DEFAULT_*）：
  export BROKER_MODE=qmt
  export QMT_ACCOUNT=你的资金账号        # 如 1234567890
  export QMT_PASSWORD=你的交易密码
  export QMT_COMBO=极简口令              # 券商提供的 6 位数字
  export QMT_COMBO_TYPE=STOCK            # 或 FUTURES/OPTION
  export QMT_NOTIFY_EMAIL=可选通知邮箱

使用：
  from broker_adapter import get_broker
  broker = get_broker("qmt")
  broker.buy("000001", 1000, 11.50)  # 平安银行
  broker.get_positions()

──────────────────────────────────────────
接口对应关系（xtquant）：
  broker.buy(code, vol, price)     → qclient.order_stock(acc, code, 23, price, vol, 'stock')
  broker.sell(code, vol, price)    → qclient.order_stock(acc, code, 24, price, vol, 'stock')
  broker.cancel_order(id)          → qclient.cancel_order_stock(acc, id)
  broker.get_positions()           → qclient.get_positions(acc)
  broker.get_orders()              → qclient.get_orders(acc)
  broker.get_balance()             → qclient.get_account_data(acc)
──────────────────────────────────────────

下单价类型（xtquant 枚举）：
  23 = 限价委托（指定价格）
  24 = 上海市价（最优五档）
  27 = 深圳市价（即时成交剩余转限价）
  29 = 科创板市价（最优五档）
"""
import os
import time
from datetime import datetime
from typing import Optional

# ── QMT 连接状态（全局）────────────────────────────────────────
_qmt_client = None
_qmt_account = None


def _ensure_connected():
    """
    确保 QMT 连接已建立
    xtquant 需要先运行 QTM_QuantClient().start()
    """
    global _qmt_client, _qmt_account
    if _qmt_client is not None:
        return _qmt_client, _qmt_account

    try:
        import pythoncom
        from xtquant import xtquant_client as qclient_mod

        pythoncom.CoInitialize()

        account = os.getenv("QMT_ACCOUNT", "")
        password = os.getenv("QMT_PASSWORD", "")
        combo = os.getenv("QMT_COMBO", "")
        combo_type = os.getenv("QMT_COMBO_TYPE", "STOCK")

        if not account or not password:
            raise RuntimeError(
                "QMT 配置不完整，请设置环境变量：\n"
                "  QMT_ACCOUNT=你的资金账号\n"
                "  QMT_PASSWORD=你的交易密码\n"
                "  QMT_COMBO=极简口令（6位数字）"
            )

        client = qclient_mod.QT_QuantClient()
        client.start()
        time.sleep(2)  # 等待连接建立

        # 登录交易账号
        # combo_type: STOCK / FUTURES / OPTION
        result = client.login(
            account,
            password,
            combo,
            combo_type,
            localhost=True,
        )
        if not result:
            raise RuntimeError(f"QMT 登录失败，请检查账号密码和极简口令")

        _qmt_client = client
        _qmt_account = account
        print(f"[QMT] 登录成功，账号: {account}")
        return client, account

    except ImportError:
        raise RuntimeError(
            "xtquant 未安装。请在 Windows 机器上运行：\n"
            "  pip install xtquant\n"
            "  或联系券商获取 QMT Python API 文档"
        )


def _normalize_qmt_code(code: str) -> str:
    """
    将 A 股 6 位代码转换为 QMT 格式
    600519 → 600519.XSHE（上交所）/ 000001 → 000001.XSHE
    """
    code = code.strip()
    # 上交所：6开头
    if code.startswith(("6", "9")):
        return f"{code}.XSHG"
    # 深交所：0/3开头
    return f"{code}.XSHE"


class QMTBroker:
    """
    迅投 QMT 券商适配器

    订单状态映射（xtquant → 统一）：
      unknown → pending
      queued / not_concentrated → pending
      confirmed / partially_filled → filled
      cancelled → cancelled
      rejected / error → rejected

    下单类型：
      buy_limit  = 23 (限价买入)
      sell_limit = 24 (限价卖出)
    """

    ORDER_TYPE_BUY_LIMIT = 23
    ORDER_TYPE_SELL_LIMIT = 24

    def buy(self, stock_code: str, volume: int, price: Optional[float] = None) -> "Order":
        from broker_adapter import Order, normalize_stock_code
        stock_code = normalize_stock_code(stock_code)
        qmt_code = _normalize_qmt_code(stock_code)

        # price=None 时用市价
        exec_price = price if price else 0.0
        order_type = self.ORDER_TYPE_BUY_LIMIT

        client, account = _ensure_connected()
        order_id = client.order_stock(
            account,
            qmt_code,
            order_type,
            exec_price,
            volume,
            "stock",
        )

        time.sleep(0.5)
        # 查询订单结果
        orders = client.get_orders(account)
        matched = next((o for o in orders if str(o.get("order_id", "")) == str(order_id)), {})
        status = self._map_status(matched.get("status", ""))

        return Order(
            order_id=str(order_id),
            stock_code=stock_code,
            direction="buy",
            price=exec_price,
            volume=volume,
            status=status,
            created_at=datetime.now().isoformat(),
            filled_at=datetime.now().isoformat() if status == "filled" else None,
            filled_price=exec_price if status == "filled" else None,
        )

    def sell(self, stock_code: str, volume: int, price: Optional[float] = None) -> "Order":
        from broker_adapter import Order, normalize_stock_code
        stock_code = normalize_stock_code(stock_code)
        qmt_code = _normalize_qmt_code(stock_code)

        exec_price = price if price else 0.0
        order_type = self.ORDER_TYPE_SELL_LIMIT

        client, account = _ensure_connected()
        order_id = client.order_stock(
            account,
            qmt_code,
            order_type,
            exec_price,
            volume,
            "stock",
        )

        time.sleep(0.5)
        orders = client.get_orders(account)
        matched = next((o for o in orders if str(o.get("order_id", "")) == str(order_id)), {})
        status = self._map_status(matched.get("status", ""))

        return Order(
            order_id=str(order_id),
            stock_code=stock_code,
            direction="sell",
            price=exec_price,
            volume=volume,
            status=status,
            created_at=datetime.now().isoformat(),
            filled_at=datetime.now().isoformat() if status == "filled" else None,
            filled_price=exec_price if status == "filled" else None,
        )

    def get_positions(self) -> list:
        from broker_adapter import Position
        client, account = _ensure_connected()
        raw_positions = client.get_positions(account)
        positions = []
        for p in raw_positions:
            stock_code = str(p.get("stock_code", "")).split(".")[0]
            positions.append(Position(
                stock_code=stock_code,
                stock_name=str(p.get("stock_name", stock_code)),
                volume=int(p.get("volume", 0)),
                avg_cost=float(p.get("avg_cost", 0)),
                current_price=float(p.get("current_price", 0)),
                unrealized_pnl=float(p.get("unrealized_pnl", 0)),
                pnl_ratio=float(p.get("pnl_ratio", 0)),
                prev_close=float(p.get("prev_close", 0.0)),
            ))
        return positions

    def get_orders(self, limit: int = 50) -> list:
        from broker_adapter import Order
        client, account = _ensure_connected()
        raw_orders = client.get_orders(account)[:limit]
        return [
            Order(
                order_id=str(o.get("order_id", "")),
                stock_code=str(o.get("stock_code", "")).split(".")[0],
                direction="buy" if o.get("direction") == "buy" else "sell",
                price=float(o.get("price", 0)),
                volume=int(o.get("volume", 0)),
                status=self._map_status(o.get("status", "")),
                created_at=str(o.get("created_at", "")),
                filled_at=str(o.get("filled_at", "")) or None,
                filled_price=float(o.get("filled_price", 0) or 0),
            )
            for o in raw_orders
        ]

    def cancel_order(self, order_id: str) -> bool:
        client, account = _ensure_connected()
        try:
            client.cancel_order_stock(account, order_id)
            return True
        except Exception:
            return False

    def get_balance(self) -> dict:
        client, account = _ensure_connected()
        data = client.get_account_data(account)
        return {
            "cash": float(data.get("cash", 0)),
            "market_value": float(data.get("market_value", data.get("marketValue", 0))),
            "total_assets": float(data.get("total_assets", data.get("totalAssets", 0))),
            "positions_count": int(data.get("positions_count", 0)),
        }

    @staticmethod
    def _map_status(qmt_status: str) -> str:
        mapping = {
            "queued": "pending",
            "not_concentrated": "pending",
            "confirmed": "filled",
            "partially_filled": "filled",
            "cancelled": "cancelled",
            "rejected": "rejected",
            "error": "rejected",
        }
        return mapping.get(qmt_status.lower(), "pending")
