"""
HTTP Webhook 券商适配器

通用适配器，支持任何提供 REST API 下单接口的券商/服务。
原理：把你的券商 API（HTTP POST/GET）包装成 BrokerAdapter 接口。

使用方式：
  1. 在 broker_adapter.py 工厂函数注册
  2. 设置环境变量：
     BROKER_MODE=webhook
     WEBHOOK_BASE_URL=https://your-broker-api.example.com
     WEBHOOK_TOKEN=your_api_token
     WEBHOOK_BUY_ENDPOINT=/order/buy
     WEBHOOK_SELL_ENDPOINT=/order/sell
     WEBHOOK_POSITIONS_ENDPOINT=/account/positions
     WEBHOOK_ORDERS_ENDPOINT=/account/orders
     WEBHOOK_CANCEL_ENDPOINT=/order/cancel
     WEBHOOK_BALANCE_ENDPOINT=/account/balance
"""
import os
import requests
import uuid
from datetime import datetime
from typing import Optional

from broker_adapter import BrokerAdapter, Order, Position, normalize_stock_code


class WebhookBroker(BrokerAdapter):
    """
    通过 HTTP Webhook 接入任意券商 REST API
    支持的 API 风格：
      - 标准 OpenAPI / REST JSON
      - JoinQuant Mini Program API
      - 任意支持 HTTP 的量化接口
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        buy_endpoint: str = "/order/buy",
        sell_endpoint: str = "/order/sell",
        positions_endpoint: str = "/account/positions",
        orders_endpoint: str = "/account/orders",
        cancel_endpoint: str = "/order/cancel",
        balance_endpoint: str = "/account/balance",
        **kwargs,
    ):
        self.base_url = (base_url or os.getenv("WEBHOOK_BASE_URL", "")).rstrip("/")
        self.token = token or os.getenv("WEBHOOK_TOKEN", "")
        self.buy_endpoint = buy_endpoint
        self.sell_endpoint = sell_endpoint
        self.positions_endpoint = positions_endpoint
        self.orders_endpoint = orders_endpoint
        self.cancel_endpoint = cancel_endpoint
        self.balance_endpoint = balance_endpoint
        self.session = requests.Session()
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def _post(self, endpoint: str, payload: dict, timeout: int = 30) -> dict:
        resp = self.session.post(
            f"{self.base_url}{endpoint}",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, endpoint: str, params: dict = None, timeout: int = 30) -> dict:
        resp = self.session.get(
            f"{self.base_url}{endpoint}",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def buy(self, stock_code: str, volume: int, price: Optional[float] = None) -> Order:
        stock_code = normalize_stock_code(stock_code)
        payload = {
            "stock_code": stock_code,
            "volume": volume,
            "price": price,
            "side": "buy",
            "order_id": str(uuid.uuid4())[:8].upper(),
            "timestamp": datetime.now().isoformat(),
        }
        try:
            result = self._post(self.buy_endpoint, payload)
            return self._parse_order_response(result, "buy", price)
        except Exception as e:
            return Order(
                order_id=str(uuid.uuid4())[:8].upper(),
                stock_code=stock_code,
                direction="buy",
                price=price or 0.0,
                volume=volume,
                status="rejected",
                created_at=datetime.now().isoformat(),
            )

    def sell(self, stock_code: str, volume: int, price: Optional[float] = None) -> Order:
        stock_code = normalize_stock_code(stock_code)
        payload = {
            "stock_code": stock_code,
            "volume": volume,
            "price": price,
            "side": "sell",
            "order_id": str(uuid.uuid4())[:8].upper(),
            "timestamp": datetime.now().isoformat(),
        }
        try:
            result = self._post(self.sell_endpoint, payload)
            return self._parse_order_response(result, "sell", price)
        except Exception as e:
            return Order(
                order_id=str(uuid.uuid4())[:8].upper(),
                stock_code=stock_code,
                direction="sell",
                price=price or 0.0,
                volume=volume,
                status="rejected",
                created_at=datetime.now().isoformat(),
            )

    def _parse_order_response(self, result: dict, direction: str, price: Optional[float]) -> Order:
        """解析各券商 API 响应，统一转换为 Order"""
        return Order(
            order_id=str(result.get("order_id", result.get("orderId", "UNKNOWN"))),
            stock_code=str(result.get("stock_code", result.get("securityCode", ""))),
            direction=direction,
            price=price or float(result.get("price", 0)),
            volume=int(result.get("volume", result.get("amount", 0))),
            status=str(result.get("status", "pending")),
            created_at=result.get("created_at", result.get("createdAt", datetime.now().isoformat())),
            filled_at=result.get("filled_at", result.get("filledAt")),
            filled_price=float(result.get("filled_price", result.get("filledPrice", 0) or 0)),
        )

    def get_positions(self) -> list[Position]:
        try:
            result = self._get(self.positions_endpoint)
            data = result if isinstance(result, list) else result.get("data", result.get("positions", []))
            positions = []
            for item in data:
                positions.append(Position(
                    stock_code=str(item.get("stock_code", item.get("securityCode", ""))),
                    stock_name=str(item.get("stock_name", item.get("securityName", ""))),
                    volume=int(item.get("volume", item.get("currentAmount", 0))),
                    avg_cost=float(item.get("avg_cost", item.get("avgCost", 0) or 0)),
                    current_price=float(item.get("current_price", item.get("lastPrice", 0) or 0)),
                    unrealized_pnl=float(item.get("unrealized_pnl", item.get("marketValue", 0) or 0)),
                    pnl_ratio=float(item.get("pnl_ratio", item.get("pnlRatio", 0) or 0)),
                ))
            return positions
        except Exception:
            return []

    def get_orders(self, limit: int = 50) -> list[Order]:
        try:
            result = self._get(self.orders_endpoint, params={"limit": limit})
            data = result if isinstance(result, list) else result.get("data", result.get("orders", []))
            orders = []
            for item in data:
                orders.append(Order(
                    order_id=str(item.get("order_id", item.get("orderId", ""))),
                    stock_code=str(item.get("stock_code", item.get("securityCode", ""))),
                    direction=str(item.get("direction", item.get("side", "buy"))),
                    price=float(item.get("price", 0) or 0),
                    volume=int(item.get("volume", item.get("amount", 0))),
                    status=str(item.get("status", "pending")),
                    created_at=str(item.get("created_at", item.get("createdAt", ""))),
                    filled_at=str(item.get("filled_at", item.get("filledAt", ""))) or None,
                    filled_price=float(item.get("filled_price", item.get("filledPrice", 0) or 0) or 0),
                ))
            return orders
        except Exception:
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._post(self.cancel_endpoint, {"order_id": order_id})
            return True
        except Exception:
            return False

    def get_balance(self) -> dict:
        try:
            result = self._get(self.balance_endpoint)
            if isinstance(result, dict):
                return {
                    "cash": float(result.get("cash", result.get("available", 0) or 0)),
                    "market_value": float(result.get("market_value", result.get("marketValue", 0) or 0)),
                    "total_assets": float(result.get("total_assets", result.get("totalAssets", 0) or 0)),
                    "positions_count": int(result.get("positions_count", result.get("positionCount", 0) or 0)),
                }
            return {"cash": 0, "market_value": 0, "total_assets": 0, "positions_count": 0}
        except Exception:
            return {"cash": 0, "market_value": 0, "total_assets": 0, "positions_count": 0}
