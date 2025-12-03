"""
市場數據 WebSocket 管理器
訂閱並實時更新市場統計數據
"""
import asyncio
import json
import websockets
from typing import Dict, Optional, Callable
from datetime import datetime
from dataclasses import dataclass
from utils import bot_logger as logger


@dataclass
class MarketStats:
    """市場統計數據"""
    market_id: int
    index_price: float          # 指數價格
    mark_price: float           # 標記價格
    last_trade_price: float     # 最後成交價
    open_interest: float        # 未平倉量
    current_funding_rate: float # 當前資金費率
    funding_rate: float         # 預測資金費率
    funding_timestamp: int      # 資金費率時間戳
    daily_volume_base: float    # 24h 基礎代幣成交量
    daily_volume_quote: float   # 24h 報價代幣成交量
    daily_price_low: float      # 24h 最低價
    daily_price_high: float     # 24h 最高價
    daily_price_change: float   # 24h 價格變化百分比
    last_update: datetime       # 最後更新時間

    @property
    def current_price(self) -> float:
        """獲取當前價格（優先使用標記價格）"""
        return self.mark_price if self.mark_price > 0 else self.last_trade_price


class MarketDataWebSocket:
    """
    市場數據 WebSocket 管理器

    訂閱市場統計並維護實時價格緩存
    """

    def __init__(self, ws_url: str = "wss://mainnet.zklighter.elliot.ai/ws"):
        self.ws_url = ws_url
        self.ws = None
        self.is_running = False
        self.should_stop = False

        # 市場統計緩存 {market_id: MarketStats}
        self.market_stats: Dict[int, MarketStats] = {}

        # 訂閱的市場列表
        self.subscribed_markets: set[int] = set()

        # 價格更新回調函數
        self.on_price_update: Optional[Callable[[int, MarketStats], None]] = None

        # 重連設置
        self.reconnect_delay = 5  # 秒
        self.max_reconnect_attempts = 10

    async def connect(self):
        """連接 WebSocket"""
        try:
            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10
            )
            logger.info(f"市場數據 WebSocket 已連接: {self.ws_url}")
            return True
        except Exception as e:
            logger.error(f"WebSocket 連接失敗: {e}")
            return False

    async def subscribe_market(self, market_id: int):
        """訂閱市場統計"""
        if not self.ws:
            logger.error("WebSocket 未連接，無法訂閱")
            return False

        try:
            subscribe_msg = {
                "type": "subscribe",
                "channel": f"market_stats/{market_id}"
            }
            await self.ws.send(json.dumps(subscribe_msg))
            self.subscribed_markets.add(market_id)
            logger.info(f"已訂閱市場統計: market_id={market_id}")
            return True
        except Exception as e:
            logger.error(f"訂閱市場 {market_id} 失敗: {e}")
            return False

    async def unsubscribe_market(self, market_id: int):
        """取消訂閱市場統計"""
        if not self.ws:
            return False

        try:
            unsubscribe_msg = {
                "type": "unsubscribe",
                "channel": f"market_stats/{market_id}"
            }
            await self.ws.send(json.dumps(unsubscribe_msg))
            self.subscribed_markets.discard(market_id)
            logger.info(f"已取消訂閱市場統計: market_id={market_id}")
            return True
        except Exception as e:
            logger.error(f"取消訂閱市場 {market_id} 失敗: {e}")
            return False

    def _parse_market_stats(self, data: dict) -> Optional[MarketStats]:
        """解析市場統計數據"""
        try:
            stats_data = data.get("market_stats", {})

            return MarketStats(
                market_id=int(stats_data.get("market_id", 0)),
                index_price=float(stats_data.get("index_price", 0)),
                mark_price=float(stats_data.get("mark_price", 0)),
                last_trade_price=float(stats_data.get("last_trade_price", 0)),
                open_interest=float(stats_data.get("open_interest", 0)),
                current_funding_rate=float(stats_data.get("current_funding_rate", 0)),
                funding_rate=float(stats_data.get("funding_rate", 0)),
                funding_timestamp=int(stats_data.get("funding_timestamp", 0)),
                daily_volume_base=float(stats_data.get("daily_base_token_volume", 0)),
                daily_volume_quote=float(stats_data.get("daily_quote_token_volume", 0)),
                daily_price_low=float(stats_data.get("daily_price_low", 0)),
                daily_price_high=float(stats_data.get("daily_price_high", 0)),
                daily_price_change=float(stats_data.get("daily_price_change", 0)),
                last_update=datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"解析市場統計數據失敗: {e}")
            return None

    async def _handle_message(self, message: str):
        """處理 WebSocket 消息"""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "update/market_stats":
                # 市場統計更新
                market_stats = self._parse_market_stats(data)
                if market_stats:
                    market_id = market_stats.market_id

                    # 更新緩存
                    old_price = self.market_stats[market_id].current_price if market_id in self.market_stats else 0
                    self.market_stats[market_id] = market_stats

                    # 只在價格有顯著變化時記錄
                    price_change = abs(market_stats.current_price - old_price) if old_price > 0 else 0
                    if price_change > 0.1 or old_price == 0:  # 變化超過 0.1 或首次更新
                        logger.debug(
                            f"市場 {market_id} 價格更新: "
                            f"${market_stats.current_price:.2f} "
                            f"(24h: {market_stats.daily_price_change:+.2f}%)"
                        )

                    # 調用回調函數
                    if self.on_price_update:
                        try:
                            self.on_price_update(market_id, market_stats)
                        except Exception as e:
                            logger.error(f"價格更新回調失敗: {e}")

            elif msg_type == "subscribed":
                logger.debug(f"訂閱成功: {data.get('channel', 'unknown')}")

            elif msg_type == "unsubscribed":
                logger.debug(f"取消訂閱成功: {data.get('channel', 'unknown')}")

            elif msg_type == "error":
                logger.error(f"WebSocket 錯誤: {data.get('message', 'unknown error')}")

        except json.JSONDecodeError:
            logger.error(f"無效的 JSON 消息: {message}")
        except Exception as e:
            logger.error(f"處理消息失敗: {e}")

    async def _listen_loop(self):
        """監聽 WebSocket 消息"""
        try:
            async for message in self.ws:
                if self.should_stop:
                    break
                await self._handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket 連接已關閉")
        except Exception as e:
            logger.error(f"監聽循環錯誤: {e}")

    async def start(self, market_ids: list[int]):
        """
        啟動市場數據 WebSocket

        Args:
            market_ids: 要訂閱的市場 ID 列表
        """
        self.is_running = True
        reconnect_attempts = 0

        while not self.should_stop and reconnect_attempts < self.max_reconnect_attempts:
            try:
                # 連接 WebSocket
                if not await self.connect():
                    reconnect_attempts += 1
                    logger.warning(f"重連嘗試 {reconnect_attempts}/{self.max_reconnect_attempts}")
                    await asyncio.sleep(self.reconnect_delay)
                    continue

                # 重置重連計數
                reconnect_attempts = 0

                # 訂閱所有市場
                for market_id in market_ids:
                    await self.subscribe_market(market_id)
                    await asyncio.sleep(0.1)  # 避免訂閱過快

                # 開始監聽
                await self._listen_loop()

                # 如果正常退出則不重連
                if self.should_stop:
                    break

                # 連接斷開，準備重連
                logger.info(f"準備在 {self.reconnect_delay} 秒後重連...")
                await asyncio.sleep(self.reconnect_delay)

            except Exception as e:
                logger.error(f"WebSocket 運行錯誤: {e}")
                reconnect_attempts += 1
                await asyncio.sleep(self.reconnect_delay)

        if reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"達到最大重連次數 ({self.max_reconnect_attempts})，停止重連")

        self.is_running = False

    async def stop(self):
        """停止 WebSocket"""
        logger.info("正在停止市場數據 WebSocket...")
        self.should_stop = True

        # 取消所有訂閱
        if self.ws:
            for market_id in list(self.subscribed_markets):
                try:
                    await self.unsubscribe_market(market_id)
                except:
                    pass

        # 關閉連接
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass

        logger.info("市場數據 WebSocket 已停止")

    def get_current_price(self, market_id: int) -> Optional[float]:
        """
        獲取市場當前價格

        Args:
            market_id: 市場 ID

        Returns:
            當前價格，如果沒有數據則返回 None
        """
        stats = self.market_stats.get(market_id)
        return stats.current_price if stats else None

    def get_market_stats(self, market_id: int) -> Optional[MarketStats]:
        """
        獲取完整的市場統計數據

        Args:
            market_id: 市場 ID

        Returns:
            MarketStats 對象，如果沒有數據則返回 None
        """
        return self.market_stats.get(market_id)

    def get_all_prices(self) -> Dict[int, float]:
        """
        獲取所有市場的當前價格

        Returns:
            {market_id: price} 字典
        """
        return {
            market_id: stats.current_price
            for market_id, stats in self.market_stats.items()
        }


# 全局單例
market_data_ws: Optional[MarketDataWebSocket] = None


def get_market_data_ws() -> MarketDataWebSocket:
    """獲取市場數據 WebSocket 單例"""
    global market_data_ws
    if market_data_ws is None:
        market_data_ws = MarketDataWebSocket()
    return market_data_ws
