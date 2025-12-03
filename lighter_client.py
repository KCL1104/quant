"""Lighter API 客戶端模塊
基於 Lighter Python SDK 實現完整的期貨交易功能
重構版本：直接使用 signer_client.py 中的函數，參考 backpack_futures_client.py 接口設計
"""

import asyncio
import json
import logging
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from decimal import Decimal

# Lighter SDK imports
from lighter import Configuration
from lighter.signer_client import SignerClient
from lighter.api.account_api import AccountApi
from lighter.api.order_api import OrderApi
from lighter.api.transaction_api import TransactionApi
from lighter.ws_client import WsClient
from lighter.api_client import ApiClient
from lighter.models.detailed_accounts import DetailedAccounts
from lighter.models.orders import Orders
from lighter.models.order_books import OrderBooks

# WebSocket imports
import websockets

# 設置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lighter_client")

class LighterClient:
    """Lighter API 客戶端 - 重構版本
    
    直接使用 signer_client.py 中已有的函數，避免重複實現
    參考 backpack_futures_client.py 的接口設計
    """
    
    # 從 SignerClient 繼承常數
    ORDER_TYPE_LIMIT = SignerClient.ORDER_TYPE_LIMIT
    ORDER_TYPE_MARKET = SignerClient.ORDER_TYPE_MARKET
    ORDER_TYPE_STOP_LOSS = SignerClient.ORDER_TYPE_STOP_LOSS
    ORDER_TYPE_STOP_LOSS_LIMIT = SignerClient.ORDER_TYPE_STOP_LOSS_LIMIT
    ORDER_TYPE_TAKE_PROFIT = SignerClient.ORDER_TYPE_TAKE_PROFIT
    ORDER_TYPE_TAKE_PROFIT_LIMIT = SignerClient.ORDER_TYPE_TAKE_PROFIT_LIMIT
    
    TIME_IN_FORCE_IOC = SignerClient.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL
    TIME_IN_FORCE_GTT = SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME
    TIME_IN_FORCE_POST_ONLY = SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY
    
    CANCEL_ALL_TIF_IMMEDIATE = SignerClient.CANCEL_ALL_TIF_IMMEDIATE
    CANCEL_ALL_TIF_SCHEDULED = SignerClient.CANCEL_ALL_TIF_SCHEDULED
    CANCEL_ALL_TIF_ABORT = SignerClient.CANCEL_ALL_TIF_ABORT
    
    def __init__(self, 
                 api_private_key: str,
                 api_key_index: int = 0,
                 account_index: Optional[int] = None,
                 max_api_key_index: int = -1,
                 private_keys: Optional[Dict[int, str]] = None,
                 base_url: str = "https://mainnet.zklighter.elliot.ai"):
        """
        初始化 Lighter 客戶端 - 重構版本
        
        Args:
            api_private_key: API 私鑰（通過 lighter.create_api_key() 生成）
            api_key_index: API 密鑰索引
            account_index: 帳戶索引（如果為 None，將嘗試自動檢測）
            max_api_key_index: 最大 API 密鑰索引
            private_keys: 多個 API 私鑰字典（可選）
            base_url: API 基礎 URL
        """
        self.api_private_key = api_private_key
        self.api_key_index = api_key_index
        self.account_index = account_index
        self.max_api_key_index = max_api_key_index
        self.private_keys = private_keys or {}
        self.base_url = base_url
        
        # 配置 Lighter SDK
        self.configuration = Configuration(host=base_url)
        
        # 直接初始化 SignerClient（核心客戶端）
        self.signer_client = None
        self.account_api = None
        self.order_api = None
        self.transaction_api = None
        self.ws_client = None
        
        # WebSocket 持久連接管理 (用於交易)
        self._ws_connection = None
        self._ws_lock = asyncio.Lock()
        self._ws_url = None
        self._last_ws_error_time = 0
        
        # WebSocket 訂閱管理 (用於數據訂閱) - 重構為持久連接模式
        self._subscription_ws_client = None
        self._subscription_active = False
        self._subscribed_accounts = set()
        self._subscribed_orderbooks = set()
        self._subscription_callbacks = {
            'order_fills': [],
            'order_updates': [],
            'funding_rates': [],
            'orderbook_updates': {}
        }
        
        # 標記客戶端尚未初始化
        self._initialized = False
        
        logger.info(f"Lighter 客戶端創建完成 - 帳戶索引: {self.account_index}")
    
    async def initialize(self):
        """異步初始化客戶端 - 重構版本"""
        if not self._initialized:
            await self._init_clients()
            self._initialized = True
            logger.info(f"Lighter 客戶端初始化完成 - 帳戶索引: {self.account_index}")
    
    def _ensure_initialized(self):
        """確保客戶端已初始化"""
        if not self._initialized:
            raise RuntimeError("客戶端尚未初始化，請先調用 await client.initialize()")
    
    async def _init_clients(self):
        """初始化各種客戶端 - 重構版本，簡化邏輯"""
        try:
            # 如果沒有指定 account_index，嘗試自動檢測
            if self.account_index is None:
                self.account_index = await self._detect_account_index()
                logger.info(f"自動檢測到帳戶索引: {self.account_index}")
            
            # 初始化核心 SignerClient（包含所有交易功能）
            self.signer_client = SignerClient(  
                url="https://mainnet.zklighter.elliot.ai",           # positional url  
                private_key=self.api_private_key,    # positional private_key  
                api_key_index=self.api_key_index,  
                account_index=self.account_index,  
            )
                        
            # 初始化 API 客戶端（用於查詢功能）
            api_client = ApiClient(configuration=self.configuration)
            self.account_api = AccountApi(api_client=api_client)
            self.order_api = OrderApi(api_client=api_client)
            self.transaction_api = TransactionApi(api_client=api_client)
            
            logger.info("客戶端初始化成功")
            
        except Exception as e:
            logger.error(f"初始化客戶端失敗: {e}")
            raise
    
    async def _ensure_websocket_connection(self, max_retries: int = 3, retry_delay: float = 1.0):
        """確保 WebSocket 連接可用，如果連接不存在或已關閉則創建新連接
        
        Args:
            max_retries: 最大重試次數
            retry_delay: 重試間隔（秒）
        
        Returns:
            websocket connection: 可用的 WebSocket 連接
        """
        async with self._ws_lock:
            # 如果連接不存在或已關閉，創建新連接
            connection_needs_reset = False
            
            if self._ws_connection is None:
                connection_needs_reset = True
            else:
                # 安全檢查連接是否已關閉
                try:
                    # 不同的 WebSocket 實現可能有不同的屬性名
                    if hasattr(self._ws_connection, 'closed'):
                        connection_needs_reset = self._ws_connection.closed
                    elif hasattr(self._ws_connection, 'close_code'):
                        # websockets 庫使用 close_code 來檢查連接狀態
                        connection_needs_reset = self._ws_connection.close_code is not None
                    else:
                        # 如果無法檢查狀態，嘗試發送 ping 來測試連接
                        try:
                            if hasattr(self._ws_connection, 'ping'):
                                await asyncio.wait_for(self._ws_connection.ping(), timeout=1.0)
                            connection_needs_reset = False
                        except:
                            connection_needs_reset = True
                except Exception as e:
                    logger.debug(f"檢查 WebSocket 連接狀態時出錯: {e}")
                    connection_needs_reset = True
            
            if connection_needs_reset:
                # 檢查是否需要等待冷卻時間
                current_time = time.time()
                if current_time - self._last_ws_error_time < 2.0:  # 2秒冷卻時間
                    await asyncio.sleep(0.5)  # 短暂等待
                
                # 構建 WebSocket URL
                if self._ws_url is None:
                    self._ws_url = f"{self.base_url.replace('https', 'wss')}/stream"
                
                last_error = None
                for attempt in range(max_retries + 1):
                    try:
                        if attempt > 0:
                            logger.info(f"WebSocket 連接重試 {attempt}/{max_retries}")
                            await asyncio.sleep(retry_delay * attempt)  # 指數退避
                        
                        logger.debug(f"創建新的 WebSocket 連接: {self._ws_url}")
                        self._ws_connection = await websockets.connect(
                            self._ws_url,
                            ping_interval=30,  # 30秒心跳
                            ping_timeout=10,   # 10秒心跳超時
                            close_timeout=10   # 10秒關閉超時
                        )
                        
                        # 接收初始消息
                        initial_msg = await asyncio.wait_for(
                            self._ws_connection.recv(), 
                            timeout=10.0
                        )
                        logger.debug(f"WebSocket 初始消息: {initial_msg}")
                        
                        logger.info(f"WebSocket 持久連接已建立 (嘗試 {attempt + 1}/{max_retries + 1})")
                        break
                        
                    except (websockets.exceptions.ConnectionClosed, 
                            websockets.exceptions.InvalidStatusCode,
                            asyncio.TimeoutError,
                            OSError) as e:
                        last_error = e
                        logger.warning(f"WebSocket 連接嘗試 {attempt + 1} 失敗: {e}")
                        
                        # 清理失敗的連接
                        if self._ws_connection:
                            try:
                                await self._ws_connection.close()
                            except:
                                pass
                            self._ws_connection = None
                        
                        if attempt == max_retries:
                            logger.error(f"WebSocket 連接失敗，已達最大重試次數 {max_retries}")
                            raise Exception(f"WebSocket 連接失敗: {last_error}")
                    
                    except Exception as e:
                        last_error = e
                        logger.error(f"WebSocket 連接出現未預期錯誤: {e}")
                        self._ws_connection = None
                        if attempt == max_retries:
                            raise
            
            return self._ws_connection
    
    async def _is_websocket_connected(self) -> bool:
        """檢查 WebSocket 連接是否有效
        
        Returns:
            bool: 連接是否有效
        """
        if self._ws_connection is None:
            return False
        
        try:
            # 安全檢查連接狀態
            if hasattr(self._ws_connection, 'closed'):
                return not self._ws_connection.closed
            elif hasattr(self._ws_connection, 'close_code'):
                return self._ws_connection.close_code is None
            else:
                # 如果無法檢查狀態，假設連接有效
                return True
        except Exception as e:
            logger.debug(f"檢查 WebSocket 連接狀態時出錯: {e}")
            return False
    
    async def _close_websocket_connection(self):
        """安全關閉 WebSocket 連接"""
        async with self._ws_lock:
            if self._ws_connection:
                try:
                    # 安全檢查連接是否需要關閉
                    should_close = True
                    if hasattr(self._ws_connection, 'closed'):
                        should_close = not self._ws_connection.closed
                    elif hasattr(self._ws_connection, 'close_code'):
                        should_close = self._ws_connection.close_code is None
                    
                    if should_close and hasattr(self._ws_connection, 'close'):
                        await self._ws_connection.close()
                        logger.debug("WebSocket 連接已關閉")
                    else:
                        logger.debug("WebSocket 連接已經關閉或無法關閉")
                        
                except Exception as e:
                    logger.warning(f"關閉 WebSocket 連接時出錯: {e}")
                finally:
                    self._ws_connection = None
    
    # ==================== WebSocket 會話管理 (重構版本) ====================
    
    async def start_websocket_session(self) -> Dict:
        """啟動 WebSocket 會話 - 在會話開始時調用一次
        
        這個方法會建立持久的 WebSocket 連接用於數據訂閱，
        並且會在整個會話期間保持連接活躍。
        
        Returns:
            Dict: 啟動結果
        """
        try:
            if self._subscription_active:
                logger.warning("WebSocket 會話已經啟動")
                return {"success": True, "message": "WebSocket 會話已經啟動"}
            
            logger.info("啟動 WebSocket 會話...")
            
            # 初始化訂閱 WebSocket 客戶端
            # 注意: WsClient 要求至少有一個訂閱，所以預設訂閱當前帳戶
            host = self.base_url.replace("https://", "").replace("http://", "")
            
            try:
                self._subscription_ws_client = WsClient(
                    host=host,
                    path="/stream",
                    account_ids=[self.account_index],  # 預設訂閱當前帳戶
                    order_book_ids=[],  # 開始時空的，稍後動態添加
                    on_account_update=self._handle_account_update,
                    on_order_book_update=self._handle_orderbook_update
                )
                
                # 記錄已訂閱的帳戶
                self._subscribed_accounts.add(self.account_index)
                
            except Exception as ws_error:
                # 如果 WebSocket 初始化失敗，記錄錯誤但不阻塞會話啟動
                logger.warning(f"WebSocket 客戶端初始化失敗: {ws_error}")
                self._subscription_ws_client = None
            
            # 啟動 WebSocket 連接 (在背景中運行)
            # 注意: 這裡我們不等待 run_async()，因為它會阻塞
            # 而是在需要時才啟動連接
            
            self._subscription_active = True
            
            result = {
                "success": True,
                "message": "WebSocket 會話啟動成功",
                "account_index": self.account_index
            }
            
            logger.info("✅ WebSocket 會話啟動成功")
            return result
            
        except Exception as e:
            return self._handle_api_error("啟動 WebSocket 會話", e)
    
    async def stop_websocket_session(self) -> Dict:
        """停止 WebSocket 會話 - 在會話結束時調用一次
        
        這個方法會安全關閉所有 WebSocket 連接並清理資源。
        
        Returns:
            Dict: 停止結果
        """
        try:
            if not self._subscription_active:
                logger.info("WebSocket 會話未啟動")
                return {"success": True, "message": "WebSocket 會話未啟動"}
            
            logger.info("停止 WebSocket 會話...")
            
            # 清理訂閱客戶端
            if self._subscription_ws_client:
                # WsClient 沒有 close 方法，直接設為 None
                self._subscription_ws_client = None
            
            # 清理狀態
            self._subscription_active = False
            self._subscribed_accounts.clear()
            self._subscribed_orderbooks.clear()
            
            # 清理回調
            self._subscription_callbacks = {
                'order_fills': [],
                'order_updates': [],
                'funding_rates': [],
                'orderbook_updates': {}
            }
            
            result = {
                "success": True,
                "message": "WebSocket 會話已停止"
            }
            
            logger.info("✅ WebSocket 會話已停止")
            return result
            
        except Exception as e:
            return self._handle_api_error("停止 WebSocket 會話", e)
    
    def _handle_account_update(self, account_id, update_data):
        """帳戶更新的統一處理函數"""
        try:
            logger.debug(f"收到帳戶更新 - 帳戶ID: {account_id}")
            
            # 處理掉單成交通知
            for callback in self._subscription_callbacks['order_fills']:
                try:
                    processed_data = {
                        "type": "order_fill",
                        "account_id": account_id,
                        "account_index": self.account_index,
                        "timestamp": time.time(),
                        "data": update_data
                    }
                    callback(processed_data)
                except Exception as cb_error:
                    logger.error(f"處理掉單成交回調時出錯: {cb_error}")
            
            # 處理訂單更新通知
            for callback in self._subscription_callbacks['order_updates']:
                try:
                    processed_data = {
                        "type": "order_update",
                        "account_id": account_id,
                        "account_index": self.account_index,
                        "timestamp": time.time(),
                        "data": update_data
                    }
                    callback(processed_data)
                except Exception as cb_error:
                    logger.error(f"處理訂單更新回調時出錯: {cb_error}")
            
        except Exception as e:
            logger.error(f"處理帳戶更新時出錯: {e}")
    
    def _handle_orderbook_update(self, order_book_id, update_data):
        """訂單簿更新的統一處理函數"""
        try:
            logger.debug(f"收到訂單簿更新 - 訂單簿ID: {order_book_id}")
            
            # 處理資金費率更新
            for callback in self._subscription_callbacks['funding_rates']:
                try:
                    processed_data = {
                        "type": "funding_rate_update",
                        "market_id": int(order_book_id),
                        "order_book_id": order_book_id,
                        "timestamp": time.time(),
                        "data": update_data
                    }
                    callback(processed_data)
                except Exception as cb_error:
                    logger.error(f"處理資金費率更新回調時出錯: {cb_error}")
            
            # 處理特定市場的訂單簿更新
            market_id = int(order_book_id)
            if market_id in self._subscription_callbacks['orderbook_updates']:
                for callback in self._subscription_callbacks['orderbook_updates'][market_id]:
                    try:
                        processed_data = {
                            "type": "orderbook_update",
                            "market_id": market_id,
                            "order_book_id": order_book_id,
                            "timestamp": time.time(),
                            "data": update_data
                        }
                        callback(processed_data)
                    except Exception as cb_error:
                        logger.error(f"處理訂單簿更新回調時出錯: {cb_error}")
            
        except Exception as e:
            logger.error(f"處理訂單簿更新時出錯: {e}")
    
    def _ensure_websocket_session_active(self):
        """確保 WebSocket 會話已啟動"""
        if not self._subscription_active or not self._subscription_ws_client:
            raise RuntimeError(
                "WebSocket 會話未啟動。請先調用 start_websocket_session() 啟動會話。"
            )
    
    async def _add_account_subscription(self, account_index: int) -> bool:
        """動態添加帳戶訂閱
        
        Args:
            account_index: 帳戶索引
            
        Returns:
            bool: 是否成功添加
        """
        try:
            if account_index not in self._subscribed_accounts:
                # 更新 WsClient 的訂閱清單
                if self._subscription_ws_client:
                    self._subscription_ws_client.subscriptions["accounts"].append(account_index)
                    self._subscribed_accounts.add(account_index)
                    logger.debug(f"添加帳戶訂閱: {account_index}")
                    return True
            else:
                # 帳戶已經訂閱，直接返回成功
                logger.debug(f"帳戶 {account_index} 已經訂閱")
            return True
        except Exception as e:
            logger.error(f"添加帳戶訂閱時出錯: {e}")
            return False
    
    async def _add_orderbook_subscription(self, market_id: int) -> bool:
        """動態添加訂單簿訂閱
        
        Args:
            market_id: 市場ID
            
        Returns:
            bool: 是否成功添加
        """
        try:
            if market_id not in self._subscribed_orderbooks:
                # 更新 WsClient 的訂閱清單
                if self._subscription_ws_client:
                    self._subscription_ws_client.subscriptions["order_books"].append(market_id)
                    self._subscribed_orderbooks.add(market_id)
                    logger.debug(f"添加訂單簿訂閱: {market_id}")
                    return True
            return True
        except Exception as e:
            logger.error(f"添加訂單簿訂閱時出錯: {e}")
            return False
    
    async def _detect_account_index(self) -> int:
        """自動檢測有效的帳戶索引"""
        import aiohttp
        
        # 嘗試不同的帳戶索引
        async with aiohttp.ClientSession() as session:
            for account_idx in range(10):  # 嘗試 0-9
                try:
                    url = f"{self.base_url}/api/v1/nextNonce"
                    params = {
                        "account_index": account_idx,
                        "api_key_index": self.api_key_index
                    }
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status == 200:
                            logger.info(f"找到有效的帳戶索引: {account_idx}")
                            return account_idx
                        # 忽略錯誤日誌，除非是預期之外的錯誤
                        elif response.status != 400:
                            pass
                                
                except Exception as e:
                    # 忽略連接錯誤
                    continue
        
        # 如果都沒找到，拋出異常
        raise Exception("無法找到有效的帳戶索引。請確認您的 API 私鑰是否正確，或手動指定 account_index。")
    
    def _handle_api_error(self, operation: str, error: Exception) -> Dict:
        """處理 API 錯誤 - 統一錯誤處理格式"""
        error_msg = f"{operation} 失敗: {str(error)}"
        logger.error(error_msg)
        return {"error": error_msg, "success": False}
    
    def _format_response(self, operation: str, created_tx, tx_hash, error, **extra_info) -> Dict:
        """
        統一格式化 API 回應
        
        Args:
            operation: 操作名稱
            created_tx: 創建的交易對象
            tx_hash: 交易哈希對象 (RespSendTx)
            error: 錯誤信息
            **extra_info: 額外信息
            
        Returns:
            Dict: 統一格式的回應
        """
        if error is not None:
            return self._handle_api_error(operation, Exception(str(error)))
        
        if tx_hash is None:
            return self._handle_api_error(operation, Exception("API回應無效：tx_hash為空"))
        
        # 安全地提取 tx_hash 值 - tx_hash 是 RespSendTx 對象
        tx_hash_value = None
        if tx_hash:
            if hasattr(tx_hash, 'tx_hash'):
                tx_hash_value = tx_hash.tx_hash
            elif hasattr(tx_hash, 'hash'):
                tx_hash_value = tx_hash.hash  
            else:
                tx_hash_value = str(tx_hash)
        
        result = {
            "success": True,
            "tx_hash": tx_hash_value,
            **extra_info
        }
        
        logger.info(f"{operation}成功 - TX Hash: {result['tx_hash']}")
        return result
    
    def _format_price(self, price: float) -> int:
        """格式化價格 - 轉換為 Lighter API 要求的整數格式
        
        根據 Lighter API 規範和範例：
        - 價格需要乘以 100000 轉換為整數
        - 例如：$40.50 -> 4050000
        - 例如：$0.004 -> 400
        - 使用 round() 避免浮點數精度問題
        """
        return round(price * 100000)
    
    def _format_amount(self, amount: float, market_index: int = None) -> int:
        """格式化數量 - 根據市場精度轉換為 Lighter API 要求的整數格式
        
        Args:
            amount: 原始數量
            market_index: 市場索引 (可選，用於獲取精度信息)
            
        Returns:
            int: 格式化後的整數數量
            
        根據市場數據：
        - ETH (market_id=0): size_decimals=4, 需要乘以 10^4 = 10000
        - PUMP (market_id=45): size_decimals=0, 不需要乘數 (乘以 10^0 = 1)
        - 默認使用 10000 倍數以保持向後兼容性
        """
        if market_index is not None:
            # 嘗試從市場數據獲取精度信息
            multiplier = self._get_amount_multiplier(market_index)
            return round(amount * multiplier)
        else:
            # 默認使用 10000 倍數 (向後兼容)
            return round(amount * 10000)
    
    def _get_amount_multiplier(self, market_index: int) -> int:
        """根據市場索引獲取數量乘數
        
        Args:
            market_index: 市場索引
            
        Returns:
            int: 數量乘數 (10^size_decimals)
        """
        try:
            # 讀取市場數據
            with open('market.json', 'r') as f:
                import json
                markets = json.load(f)
            
            market_data = markets.get(str(market_index), {})
            size_decimals = market_data.get('size_decimals', 4)  # 默認 4 位小數
            
            # 計算乘數: 10^size_decimals
            multiplier = 10 ** size_decimals
            logger.debug(f"市場 {market_index} 數量乘數: {multiplier} (size_decimals: {size_decimals})")
            
            return multiplier
            
        except Exception as e:
            logger.warning(f"獲取市場 {market_index} 精度信息失敗: {e}，使用默認乘數 10000")
            return 10000  # 默認乘數
    
    def _symbol_to_market_index(self, symbol: str) -> int:
        """將交易對符號轉換為市場索引
        
        Args:
            symbol: 交易對符號 (例如: 'ETH', 'BTC', 'PUMP') 或數字字符串 (例如: '0', '45')
            
        Returns:
            int: 市場索引
            
        支持的格式：
        - Ticker符號: 'ETH' -> 0, 'PUMP' -> 45, 'BTC' -> 1
        - 數字字符串: '0' -> 0, '45' -> 45 (向後兼容)
        """
        # 完整的Ticker到Market ID映射 (從API獲取)
        TICKER_TO_MARKET_ID = {
            "1000BONK": 18, "1000FLOKI": 19, "1000PEPE": 4, "1000SHIB": 17,
            "AAVE": 27, "ADA": 39, "AERO": 65, "AI16Z": 22, "APT": 31, "ARB": 50,
            "AVAX": 9, "BCH": 58, "BERA": 20, "BNB": 25, "BTC": 1, "CRO": 73,
            "CRV": 36, "DOGE": 3, "DOLO": 75, "DOT": 11, "DYDX": 62, "EIGEN": 49,
            "ENA": 29, "ETH": 0, "ETHFI": 64, "FARTCOIN": 21, "GMX": 61, "GRASS": 52,
            "HBAR": 59, "HYPE": 24, "IP": 34, "JUP": 26, "KAITO": 33, "LAUNCHCOIN": 54,
            "LDO": 46, "LINEA": 76, "LINK": 8, "LTC": 35, "MKR": 28, "MNT": 63,
            "MORPHO": 68, "NEAR": 10, "NMR": 74, "ONDO": 38, "OP": 55, "PAXG": 48,
            "PENDLE": 37, "PENGU": 47, "POL": 14, "POPCAT": 23, "PROVE": 57, "PUMP": 45,
            "RESOLV": 51, "S": 40, "SEI": 32, "SOL": 2, "SPX": 42, "SUI": 16,
            "SYRUP": 44, "TAO": 13, "TIA": 67, "TON": 12, "TRUMP": 15, "TRX": 43,
            "UNI": 30, "USELESS": 66, "VIRTUAL": 41, "VVV": 69, "WIF": 5, "WLD": 6,
            "WLFI": 72, "XPL": 71, "XRP": 7, "YZY": 70, "ZK": 56, "ZORA": 53, "ZRO": 60,
        }
        
        # 轉換為大寫以確保匹配
        symbol_upper = symbol.upper()
        
        # 首先檢查是否為已知的ticker符號
        if symbol_upper in TICKER_TO_MARKET_ID:
            market_id = TICKER_TO_MARKET_ID[symbol_upper]
            logger.debug(f"Ticker映射: {symbol} -> Market ID {market_id}")
            return market_id
        
        # 如果不是ticker符號，嘗試解析為數字 (向後兼容)
        try:
            market_id = int(symbol)
            logger.debug(f"數字映射: {symbol} -> Market ID {market_id}")
            return market_id
        except ValueError:
            # 提供有用的錯誤信息，包含可用的ticker列表
            available_tickers = sorted(TICKER_TO_MARKET_ID.keys())[:10]  # 顯示前10個
            raise ValueError(
                f"無法識別的交易對符號: '{symbol}'\n"
                f"支持的ticker符號包括: {', '.join(available_tickers)}...\n"
                f"或直接使用market_id數字 (0-76)"
            )
    
    def _market_index_to_symbol(self, market_index: int) -> str:
        """將市場索引轉換為交易對符號 (反向映射)
        
        Args:
            market_index: 市場索引 (例如: 0, 45, 1)
            
        Returns:
            str: Ticker符號 (例如: 'ETH', 'PUMP', 'BTC')
        """
        # 反向映射字典
        MARKET_ID_TO_TICKER = {
            0: "ETH", 1: "BTC", 2: "SOL", 3: "DOGE", 4: "1000PEPE", 5: "WIF",
            6: "WLD", 7: "XRP", 8: "LINK", 9: "AVAX", 10: "NEAR", 11: "DOT",
            12: "TON", 13: "TAO", 14: "POL", 15: "TRUMP", 16: "SUI", 17: "1000SHIB",
            18: "1000BONK", 19: "1000FLOKI", 20: "BERA", 21: "FARTCOIN", 22: "AI16Z",
            23: "POPCAT", 24: "HYPE", 25: "BNB", 26: "JUP", 27: "AAVE", 28: "MKR",
            29: "ENA", 30: "UNI", 31: "APT", 32: "SEI", 33: "KAITO", 34: "IP",
            35: "LTC", 36: "CRV", 37: "PENDLE", 38: "ONDO", 39: "ADA", 40: "S",
            41: "VIRTUAL", 42: "SPX", 43: "TRX", 44: "SYRUP", 45: "PUMP", 46: "LDO",
            47: "PENGU", 48: "PAXG", 49: "EIGEN", 50: "ARB", 51: "RESOLV", 52: "GRASS",
            53: "ZORA", 54: "LAUNCHCOIN", 55: "OP", 56: "ZK", 57: "PROVE", 58: "BCH",
            59: "HBAR", 60: "ZRO", 61: "GMX", 62: "DYDX", 63: "MNT", 64: "ETHFI",
            65: "AERO", 66: "USELESS", 67: "TIA", 68: "MORPHO", 69: "VVV", 70: "YZY",
            71: "XPL", 72: "WLFI", 73: "CRO", 74: "NMR", 75: "DOLO", 76: "LINEA",
        }
        
        if market_index in MARKET_ID_TO_TICKER:
            ticker = MARKET_ID_TO_TICKER[market_index]
            logger.debug(f"反向映射: Market ID {market_index} -> {ticker}")
            return ticker
        else:
            logger.warning(f"未知的Market ID: {market_index}")
            return f"UNKNOWN_{market_index}"
    
    def _ensure_initialized(self):
        """確保客戶端已初始化"""
        if not self._initialized:
            raise Exception("客戶端尚未初始化，請先調用 initialize() 方法")
        if not self.signer_client:
            raise Exception("SignerClient 未初始化")
    
    # ==================== 訂單功能 - 重構版本 ====================
    
    async def execute_order(self, order_data: Dict) -> Dict:
        """執行訂單 - 參考 backpack_futures_client.py 接口
        
        Args:
            order_data: 訂單數據，包含以下字段：
                - symbol: 交易對符號（將轉換為 market_index）
                - side: 訂單方向 ('buy' 或 'sell')
                - orderType: 訂單類型 ('limit' 或 'market')
                - quantity: 數量
                - price: 價格（限價訂單必需）
                - timeInForce: 時效（可選，默認 'GTC'）
                - reduceOnly: 是否僅減倉（可選，默認 False）
                
        Returns:
            Dict: 訂單執行結果
        """
        try:
            self._ensure_initialized()
            
            # 解析訂單數據
            symbol = order_data.get('symbol')
            side = order_data.get('side').lower()
            order_type = order_data.get('orderType').lower()
            quantity = float(order_data.get('quantity'))
            price = float(order_data.get('price', 0))
            reduce_only = order_data.get('reduceOnly', False)
            
            # 轉換參數
            market_index = self._symbol_to_market_index(symbol)
            client_order_index = int(time.time() * 1000) % 1000000  # 生成唯一訂單索引
            is_ask = (side == 'sell')
            
            logger.info(f"執行訂單 - 交易對: {symbol}, 數量: {quantity}, 價格: {price}, 方向: {side}, 類型: {order_type}")
            
            if order_type == 'limit':
                return await self.create_limit_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=quantity,
                    price=price,
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )
            elif order_type == 'market':
                return await self.create_market_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=quantity,
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )
            else:
                return self._handle_api_error("執行訂單", Exception(f"不支持的訂單類型: {order_type}"))
                
        except Exception as e:
            return self._handle_api_error("執行訂單", e)
    
    async def create_limit_order(self,
                               market_index: int,
                               client_order_index: int,
                               base_amount: float,
                               price: float,
                               is_ask: bool,
                               reduce_only: bool = False,
                               time_in_force: int = None,
                               order_expiry: int = -1) -> Dict:
        """
        創建限價訂單 - 重構版本，直接使用 signer_client
        
        Args:
            market_index: 市場索引
            client_order_index: 客戶端訂單索引
            base_amount: 基礎數量
            price: 價格
            is_ask: 是否為賣單 (True=賣, False=買)
            reduce_only: 是否僅減倉
            time_in_force: 訂單時效
            order_expiry: 訂單過期時間
            
        Returns:
            Dict: 訂單創建結果
        """
        try:
            self._ensure_initialized()
            
            # 設置默認時效
            if time_in_force is None:
                time_in_force = self.TIME_IN_FORCE_GTT
            
            logger.info(f"創建限價訂單 - 市場: {market_index}, 數量: {base_amount}, 價格: {price}, 方向: {'賣' if is_ask else '買'}")
            
            # 格式化參數
            base_amount_formatted = self._format_amount(base_amount, market_index)
            price_formatted = self._format_price(price)
            
            # 直接調用 signer_client 的 create_order 方法
            logger.debug(f"限價訂單參數 - 數量: {base_amount_formatted}, 價格: {price_formatted}")
            created_tx, tx_hash, error = await self.signer_client.create_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount_formatted,
                price=price_formatted,
                is_ask=is_ask,
                order_type=self.ORDER_TYPE_LIMIT,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                order_expiry=order_expiry
            )
            
            logger.debug(f"限價訂單回應 - created_tx: {created_tx}, tx_hash: {tx_hash}, error: {error}")
            
            # 使用統一的回應格式化
            return self._format_response(
                "創建限價訂單",
                created_tx, tx_hash, error,
                order_info={
                    "market_index": market_index,
                    "client_order_index": client_order_index,
                    "base_amount": base_amount,
                    "price": price,
                    "is_ask": is_ask,
                    "order_type": "limit",
                    "reduce_only": reduce_only
                }
            )
            
        except Exception as e:
            return self._handle_api_error("創建限價訂單", e)
    
    async def create_market_order(self,
                                market_index: int,
                                client_order_index: int,
                                base_amount: float,
                                is_ask: bool,
                                reduce_only: bool = False,
                                max_slippage: Optional[float] = 0.1) -> Dict:
        """
        創建市價訂單 - 重構版本，直接使用 signer_client
        
        Args:
            market_index: 市場索引
            client_order_index: 客戶端訂單索引
            base_amount: 基礎數量
            is_ask: 是否為賣單 (True=賣, False=買)
            reduce_only: 是否僅減倉
            max_slippage: 最大滑點 (可選)
            
        Returns:
            Dict: 訂單創建結果
        """
        try:
            self._ensure_initialized()
            logger.info(f"創建市價訂單 - 市場: {market_index}, 數量: {base_amount}, 方向: {'賣' if is_ask else '買'}")
            
            # 格式化參數
            base_amount_formatted = self._format_amount(base_amount, market_index)
            
            if max_slippage is not None:
                # Check if signer_client has the method create_market_order_limited_slippage
                if hasattr(self.signer_client, 'create_market_order_limited_slippage'):
                    # 使用限制滑點的市價訂單
                    created_tx, tx_hash, error = await self.signer_client.create_market_order_limited_slippage(
                        market_index=market_index,
                        client_order_index=client_order_index,
                        base_amount=base_amount_formatted,
                        max_slippage=max_slippage,
                        is_ask=is_ask,
                        reduce_only=reduce_only
                    )
                else:
                    # Fallback to regular market order if limited slippage method is not available
                    # For market orders, we need to provide a worst acceptable price
                    # This acts as a protection similar to slippage
                    
                    # Get current market price to calculate worst price
                    # Since we don't have easy access to orderbook here, we'll use a very wide range
                    # In a real production system, you should fetch the current price first
                    
                    if is_ask:
                        # Selling: worst price is very low (e.g. 0.1)
                        # Lighter API expects integer price (x100000)
                        worst_price = 1000 # 0.01 * 100000
                    else:
                        # Buying: worst price is very high (e.g. 1,000,000)
                        worst_price = 100000000000 # 1,000,000 * 100000
                        
                    logger.info(f"Falling back to regular create_market_order with worst_price: {worst_price}")
                    
                    # 確保 SignerClient 使用正確的私鑰和帳戶信息
                    if self.signer_client.account_index != self.account_index:
                        logger.warning(f"SignerClient account_index ({self.signer_client.account_index}) 與當前 account_index ({self.account_index}) 不匹配，正在修正...")
                        self.signer_client.account_index = self.account_index
                        
                    if self.signer_client.api_key_index != self.api_key_index:
                        logger.warning(f"SignerClient api_key_index ({self.signer_client.api_key_index}) 與當前 api_key_index ({self.api_key_index}) 不匹配，正在修正...")
                        self.signer_client.api_key_index = self.api_key_index
                    
                    created_tx, tx_hash, error = await self.signer_client.create_market_order(
                        market_index=market_index,
                        client_order_index=client_order_index,
                        base_amount=base_amount_formatted,
                        avg_execution_price=worst_price, # Changed back to 'avg_execution_price' as 'price' caused an error
                        is_ask=is_ask,
                        reduce_only=reduce_only
                        # time_in_force removed as it is not supported for market orders in this SDK version
                    )
            else:
                # 使用普通市價訂單 (需要平均執行價格)
                # 根據範例，使用較高的價格作為 worst acceptable price
                if is_ask:
                    # 賣單：使用較低的價格作為最差可接受價格
                    avg_execution_price = int(1000 * 100)  # $10.00 作為最低價格
                else:
                    # 買單：使用較高的價格作為最差可接受價格  
                    avg_execution_price = int(100000 * 100)  # $1000.00 作為最高價格
                
                # 確保 SignerClient 使用正確的私鑰和帳戶信息
                if self.signer_client.account_index != self.account_index:
                    logger.warning(f"SignerClient account_index ({self.signer_client.account_index}) 與當前 account_index ({self.account_index}) 不匹配，正在修正...")
                    self.signer_client.account_index = self.account_index
                    
                if self.signer_client.api_key_index != self.api_key_index:
                    logger.warning(f"SignerClient api_key_index ({self.signer_client.api_key_index}) 與當前 api_key_index ({self.api_key_index}) 不匹配，正在修正...")
                    self.signer_client.api_key_index = self.api_key_index
                
                created_tx, tx_hash, error = await self.signer_client.create_market_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=base_amount_formatted,
                    avg_execution_price=avg_execution_price, # Changed back to 'avg_execution_price' as 'price' caused an error
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )


            
            # 使用統一的回應格式化
            return self._format_response(
                "創建市價訂單",
                created_tx, tx_hash, error,
                order_info={
                    "market_index": market_index,
                    "client_order_index": client_order_index,
                    "base_amount": base_amount,
                    "is_ask": is_ask,
                    "order_type": "market",
                    "reduce_only": reduce_only,
                    "max_slippage": max_slippage
                }
            )
            
        except Exception as e:
            return self._handle_api_error("創建市價訂單", e)
    
    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """
        取消指定訂單 - 匹配 backpack 接口
        
        Args:
            symbol: 交易對符號，如 "BTC-USDC" 或市場索引字符串
            order_id: 訂單 ID
            
        Returns:
            Dict: 取消結果
        """
        try:
            # 將 symbol 轉換為 market_index
            market_index = self._symbol_to_market_index(symbol)
            # 將 order_id 轉換為整數
            order_index = int(order_id)
            
            return await self.cancel_order_by_market_index(market_index, order_index)
            
        except Exception as e:
            return self._handle_api_error("取消訂單", e)
    
    async def cancel_order_by_market_index(self, market_index: int, order_index: int) -> Dict:
        """
        取消指定訂單 - 原始版本，使用市場索引和訂單索引
        
        Args:
            market_index: 市場索引
            order_index: 訂單索引
            
        Returns:
            Dict: 取消結果
        """
        try:
            self._ensure_initialized()
            logger.info(f"取消訂單 - 市場: {market_index}, 訂單索引: {order_index}")
            
            cancel_tx, tx_hash, error = await self.signer_client.cancel_order(
                market_index=market_index,
                order_index=order_index
            )
            
            # 使用統一的回應格式化
            return self._format_response(
                "取消訂單",
                cancel_tx, tx_hash, error,
                cancelled_order={
                    "market_index": market_index,
                    "order_index": order_index
                }
            )
            
        except Exception as e:
            return self._handle_api_error("取消訂單", e)
    
    async def cancel_all_orders(self, time_in_force: int = None, time: int = 0) -> Dict:
        """
        取消所有訂單 - 重構版本，直接使用 signer_client
        
        Args:
            time_in_force: 取消時效
            time: 時間參數
            
        Returns:
            Dict: 取消結果
        """
        try:
            self._ensure_initialized()
            
            # 設置默認時效
            if time_in_force is None:
                time_in_force = self.CANCEL_ALL_TIF_IMMEDIATE
            
            logger.info("取消所有訂單")
            
            cancel_tx, tx_hash, error = await self.signer_client.cancel_all_orders(
                time_in_force=time_in_force,
                time=time
            )
            
            # 使用統一的回應格式化
            return self._format_response(
                "取消所有訂單",
                cancel_tx, tx_hash, error,
                cancel_info={
                    "time_in_force": time_in_force,
                    "time": time
                }
            )
            
        except Exception as e:
            return self._handle_api_error("取消所有訂單", e)
    
    async def close_position_market(self,
                                  market_index: int,
                                  position_size: float,
                                  is_long_position: Optional[bool] = None,
                                  client_order_index: Optional[int] = None,
                                  max_slippage: Optional[float] = 0.1) -> Dict:
        """
        市價平倉 - 重構版本，參考 backpack_futures_client.py 接口
        
        Args:
            market_index: 市場索引
            position_size: 持倉大小 (絕對值)
            is_long_position: 是否為多頭持倉 (可選，如果不提供則假設為多頭)
            client_order_index: 客戶端訂單索引 (可選，自動生成)
            
        Returns:
            Dict: 平倉結果
        """
        try:
            self._ensure_initialized()
            
            # 自動生成客戶端訂單索引
            if client_order_index is None:
                client_order_index = int(time.time() * 1000) % 1000000
            
            # 如果未指定持倉方向，默認為多頭持倉
            if is_long_position is None:
                is_long_position = True
                logger.warning(f"未指定持倉方向，默認為多頭持倉")
            
            # 平倉需要反向操作：多頭持倉需要賣出，空頭持倉需要買入
            is_ask = is_long_position  # 多頭平倉=賣出, 空頭平倉=買入
            
            logger.info(f"市價平倉 - 市場: {market_index}, 持倉大小: {position_size}, 持倉方向: {'多頭' if is_long_position else '空頭'}")
            
            return await self.create_market_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=position_size,
                is_ask=is_ask,
                reduce_only=True,  # 設置為僅減倉
                max_slippage=max_slippage
            )
            
        except Exception as e:
            return self._handle_api_error("市價平倉", e)
    
    async def close_position(self,
                           market_index: int,
                           position_size: float,
                           is_long_position: Optional[bool] = None,
                           client_order_index: Optional[int] = None) -> Dict:
        """
        平倉（市價平倉的別名方法）
        
        Args:
            market_index: 市場索引
            position_size: 持倉大小 (絕對值)
            is_long_position: 是否為多頭持倉 (可選，默認為多頭)
            client_order_index: 客戶端訂單索引 (可選，自動生成)
            
        Returns:
            Dict: 平倉結果
        """
        return await self.close_position_market(
            market_index=market_index,
            position_size=position_size,
            is_long_position=is_long_position,
            client_order_index=client_order_index
        )
    
    # ==================== 查詢功能 ====================
    
    async def get_account_balance(self) -> Dict:
        """
        查詢帳戶保證金餘額
        
        Returns:
            Dict: 帳戶餘額信息
        """
        try:
            logger.info(f"查詢帳戶餘額 - 帳戶索引: {self.account_index}")
            
            account_info = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            
            if not account_info or not hasattr(account_info, 'accounts') or not account_info.accounts:
                return self._handle_api_error("查詢帳戶餘額", Exception("未找到帳戶信息"))
            
            account = account_info.accounts[0]
            
            result = {
                "success": True,
                "account_index": self.account_index,
                "balance_info": {
                    "total_collateral": float(account.collateral) if account.collateral else 0.0,
                    "available_balance": float(account.available_balance) if hasattr(account, 'available_balance') and account.available_balance else 0.0,
                    "used_margin": float(account.used_margin) if hasattr(account, 'used_margin') and account.used_margin else 0.0,
                    "unrealized_pnl": float(account.unrealized_pnl) if hasattr(account, 'unrealized_pnl') and account.unrealized_pnl else 0.0,
                    "realized_pnl": float(account.realized_pnl) if hasattr(account, 'realized_pnl') and account.realized_pnl else 0.0,
                    "account_status": account.status if hasattr(account, 'status') else "unknown"
                }
            }
            
            logger.info(f"帳戶餘額查詢成功 - 總抵押品: {result['balance_info']['total_collateral']}")
            return result
            
        except Exception as e:
            return self._handle_api_error("查詢帳戶餘額", e)
    
    async def get_account_info(self) -> Dict:
        """get_account_balance的別名，用於測試腳本兼容性"""
        return await self.get_account_balance()
    
    async def get_open_orders(self, symbol: str = None) -> Dict:
        """
        查詢當前掛單狀態 - 匹配 backpack 接口
        
        Args:
            symbol: 交易對符號 (可選，不指定則查詢所有市場)
            
        Returns:
            Dict: 掛單信息
        """
        try:
            # 如果提供了 symbol，轉換為 market_id
            market_id = None
            if symbol:
                try:
                    market_id = self._symbol_to_market_index(symbol)
                except ValueError:
                    logger.warning(f"無法解析交易對符號: {symbol}，將查詢所有市場")
            
            logger.info(f"查詢掛單狀態 - 帳戶索引: {self.account_index}, 交易對: {symbol or '全部'}, 市場ID: {market_id or '全部'}")
            
            # 生成認證令牌
            auth_token, auth_error = self.signer_client.create_auth_token_with_expiry()
            if auth_error:
                raise Exception(f"認證令牌生成失敗: {auth_error}")
            if not auth_token:
                raise Exception("認證令牌為空")
            
            # 使用 order_api 查詢掛單，傳入認證信息
            orders_info = await self.order_api.account_active_orders(
                account_index=self.account_index,
                market_id=market_id or 0,
                auth=auth_token
            )
            
            if not orders_info or not hasattr(orders_info, 'orders'):
                logger.info(f"掛單狀態查詢成功 - 找到 0 個掛單")
                return {
                    "success": True,
                    "account_index": self.account_index,
                    "symbol": symbol,
                    "market_id": market_id,
                    "open_orders": [],
                    "total_orders": 0
                }
            
            open_orders = []
            for order in orders_info.orders:
                order_data = {
                    "order_index": order.order_index if hasattr(order, 'order_index') else None,
                    "market_index": order.market_index if hasattr(order, 'market_index') else None,
                    "client_order_index": order.client_order_index if hasattr(order, 'client_order_index') else None,
                    "base_amount": float(order.base_amount) if hasattr(order, 'base_amount') and order.base_amount else 0.0,
                    "price": float(order.price) if hasattr(order, 'price') and order.price else 0.0,
                    "is_ask": order.is_ask if hasattr(order, 'is_ask') else None,
                    "order_type": "limit" if hasattr(order, 'order_type') and order.order_type == 0 else "market",
                    "reduce_only": order.reduce_only if hasattr(order, 'reduce_only') else False,
                    "created_at": order.created_at if hasattr(order, 'created_at') else None,
                    "status": order.status if hasattr(order, 'status') else "unknown"
                }
                open_orders.append(order_data)
            
            result = {
                "success": True,
                "account_index": self.account_index,
                "symbol": symbol,
                "market_id": market_id,
                "open_orders": open_orders,
                "total_orders": len(open_orders)
            }
            
            logger.info(f"掛單狀態查詢成功 - 找到 {len(open_orders)} 個掛單")
            return result
            
        except Exception as e:
            return self._handle_api_error("查詢掛單狀態", e)
    
    async def get_active_orders(self, market_id: Optional[int] = None) -> Dict:
        """get_open_orders的別名，用於測試腳本兼容性"""
        return await self.get_open_orders(market_id)
    
    async def get_positions(self) -> Dict:
        """
        查詢當前持倉狀況 - 根據官方 Lighter API 文檔修正
        
        Returns:
            Dict: 持倉信息
        """
        self._ensure_initialized()
        try:
            logger.info(f"查詢持倉狀況 - 帳戶索引: {self.account_index}")
            
            account_info = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            
            if not account_info or not hasattr(account_info, 'accounts') or not account_info.accounts:
                return self._handle_api_error("查詢持倉狀況", Exception("未找到帳戶信息"))
            
            account = account_info.accounts[0]
            positions = []
            
            # 記錄原始持倉數量便於排查
            raw_positions_count = len(getattr(account, 'positions', []) or [])
            logger.debug(f"原始持倉條目數: {raw_positions_count}")
            
            # 記錄完整的帳戶信息，以便排查
            logger.debug(f"帳戶信息: {account}")
            
            if hasattr(account, 'positions') and account.positions:
                for position in account.positions:
                    # 根據官方文檔，正確的欄位名稱
                    market_id = getattr(position, 'market_id', None)  # 官方文檔使用 market_id
                    
                    # 兼容處理：如果 market_id 不存在，嘗試 market_index（向後相容）
                    if market_id is None:
                        market_id = getattr(position, 'market_index', None)
                    
                    # 安全轉換為整數
                    try:
                        market_index = int(market_id) if market_id is not None else None
                    except Exception:
                        market_index = None
                    
                    # 根據官方文檔，持倉數量欄位名稱為 "position"（字符串類型）
                    position_str = getattr(position, 'position', None)
                    
                    # 兼容處理：如果 position 不存在，嘗試其他可能的欄位名稱
                    if position_str is None:
                        position_candidates = [
                            getattr(position, 'position_amount', None),
                            getattr(position, 'base_amount', None),
                            getattr(position, 'size', None),
                            getattr(position, 'amount', None),
                        ]
                        for cand in position_candidates:
                            if cand is not None:
                                position_str = cand
                                break
                    
                    # 轉換持倉數量為浮點數
                    position_amount = 0.0
                    if position_str is not None:
                        try:
                            position_amount = float(position_str)
                        except Exception:
                            position_amount = 0.0
                    
                    # 安全轉換其他欄位
                    def safe_float(val):
                        if val is None:
                            return 0.0
                        try:
                            return float(val)
                        except Exception:
                            return 0.0
                    
                    def safe_int(val):
                        if val is None:
                            return 0
                        try:
                            return int(val)
                        except Exception:
                            return 0
                    
                    def safe_str(val):
                        return str(val) if val is not None else ""
                    
                    # 根據官方文檔解析各個欄位
                    symbol = safe_str(getattr(position, 'symbol', ''))
                    initial_margin_fraction = safe_str(getattr(position, 'initial_margin_fraction', '0'))
                    open_order_count = safe_int(getattr(position, 'open_order_count', 0))
                    pending_order_count = safe_int(getattr(position, 'pending_order_count', 0))
                    position_tied_order_count = safe_int(getattr(position, 'position_tied_order_count', 0))
                    sign = safe_int(getattr(position, 'sign', 0))  # 1=多頭, -1=空頭, 0=無持倉
                    
                    # 官方文檔：avg_entry_price 而非 average_entry_price
                    avg_entry_price = safe_float(getattr(position, 'avg_entry_price', None))
                    # 兼容處理
                    if avg_entry_price == 0.0:
                        avg_entry_price = safe_float(getattr(position, 'average_entry_price', None))
                    
                    position_value = safe_float(getattr(position, 'position_value', None))
                    unrealized_pnl = safe_float(getattr(position, 'unrealized_pnl', None))
                    realized_pnl = safe_float(getattr(position, 'realized_pnl', None))
                    liquidation_price = safe_float(getattr(position, 'liquidation_price', None))
                    total_funding_paid_out = safe_str(getattr(position, 'total_funding_paid_out', '0'))
                    margin_mode = safe_int(getattr(position, 'margin_mode', 0))
                    allocated_margin = safe_str(getattr(position, 'allocated_margin', '0'))
                    
                    # 判斷多空方向
                    is_long = sign > 0
                    is_short = sign < 0
                    
                    # 如果 sign 為 0 但 position_amount 不為 0，根據數量判斷方向
                    if sign == 0 and position_amount != 0.0:
                        is_long = position_amount > 0
                        is_short = position_amount < 0
                    
                    position_data = {
                        # 保持向後兼容的欄位名稱
                        "market_index": market_index,
                        "position_amount": position_amount,
                        "average_entry_price": avg_entry_price,  # 映射到兼容名稱
                        "position_value": position_value,
                        "unrealized_pnl": unrealized_pnl,
                        "realized_pnl": realized_pnl,
                        "open_order_count": open_order_count,
                        "sign": sign,
                        "is_long": is_long,
                        "is_short": is_short,
                        
                        # 額外的官方欄位
                        "market_id": market_index,  # 官方欄位名稱
                        "symbol": symbol,
                        "initial_margin_fraction": initial_margin_fraction,
                        "pending_order_count": pending_order_count,
                        "position_tied_order_count": position_tied_order_count,
                        "avg_entry_price": avg_entry_price,  # 官方欄位名稱
                        "liquidation_price": liquidation_price,
                        "total_funding_paid_out": total_funding_paid_out,
                        "margin_mode": margin_mode,
                        "allocated_margin": allocated_margin,
                    }
                    
                    # 重要：不過濾任何持倉，即使數量為 0
                    # 因為 API 可能會返回處於特殊狀態的持倉
                    # 讓調用者決定如何處理
                    positions.append(position_data)
                    
                    # 為調試目的記錄持倉信息
                    if abs(position_amount) > 1e-9:
                        logger.debug(f"找到活躍持倉 - 市場: {market_index}, 數量: {position_amount}, 方向: {sign}")
                    else:
                        logger.debug(f"找到零持倉 - 市場: {market_index}, 數量: {position_amount}, 方向: {sign}")
            
            # 統計活躍持倉數量
            active_positions = [p for p in positions if abs(p.get('position_amount', 0)) > 1e-9]
            
            result = {
                "success": True,
                "account_index": self.account_index,
                "positions": positions,  # 返回所有持倉，包括零持倉
                "total_positions": len(positions),
                "active_positions": len(active_positions)
            }
            
            logger.info(f"持倉狀況查詢成功 - 總共 {len(positions)} 個持倉, 活躍 {len(active_positions)} 個 (原始 {raw_positions_count} 條)")
            return result
            
        except Exception as e:
            return self._handle_api_error("查詢持倉狀況", e)
    
    async def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """
        獲取特定交易對的持倉
        
        Args:
            symbol: 交易對符號，如 "BTC-USDC" 或市場索引字符串
            
        Returns:
            Optional[Dict]: 持倉信息，如果沒有持倉則返回 None
        """
        try:
            # 將 symbol 轉換為 market_index
            market_index = self._symbol_to_market_index(symbol)
            
            positions_result = await self.get_positions()
            
            if not positions_result.get("success") or "error" in positions_result:
                logger.error(f"獲取持倉失敗: {positions_result.get('error', '未知錯誤')}")
                return None
            
            for position in positions_result.get("positions", []):
                if position.get("market_index") == market_index:
                    return position
            
            return None
            
        except Exception as e:
            logger.error(f"獲取特定交易對持倉時出錯: {e}")
            return None
    
    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """
        獲取資金費率
        
        Args:
            symbol: 交易對符號，如 "BTC-USDC" 或市場索引字符串
            
        Returns:
            Optional[float]: 資金費率，如果獲取失敗則返回 None
        """
        try:
            # 將 symbol 轉換為 market_index
            market_index = self._symbol_to_market_index(symbol)
            logger.info(f"獲取資金費率 - 交易對: {symbol} (市場索引: {market_index})")
            
            # 通過市場信息獲取資金費率
            market_info_result = await self.get_market_info(market_index)
            
            if not market_info_result.get("success"):
                logger.error(f"獲取市場信息失敗: {market_info_result.get('error', '未知錯誤')}")
                return None
            
            market_data = market_info_result.get("market_info", {})
            
            # 嘗試從市場數據中提取資金費率
            if isinstance(market_data, dict):
                funding_rate = market_data.get("funding_rate") or market_data.get("fundingRate")
                if funding_rate is not None:
                    return float(funding_rate)
            
            logger.warning(f"未找到交易對 {symbol} 的資金費率信息")
            return 0.0
            
        except Exception as e:
            logger.error(f"獲取資金費率時出錯: {e}")
            return None
    
    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """
        獲取標記價格
        
        Args:
            symbol: 交易對符號，如 "BTC-USDC" 或市場索引字符串
            
        Returns:
            Optional[float]: 標記價格，如果獲取失敗則返回 None
        """
        try:
            # 將 symbol 轉換為 market_index
            market_index = self._symbol_to_market_index(symbol)
            logger.info(f"獲取標記價格 - 交易對: {symbol} (市場索引: {market_index})")
            
            # 通過市場信息獲取標記價格
            market_info_result = await self.get_market_info(market_index)
            
            if not market_info_result.get("success"):
                logger.error(f"獲取市場信息失敗: {market_info_result.get('error', '未知錯誤')}")
                return None
            
            market_data = market_info_result.get("market_info", {})
            
            # 嘗試從市場數據中提取標記價格
            if isinstance(market_data, dict):
                mark_price = market_data.get("mark_price") or market_data.get("markPrice")
                if mark_price is not None:
                    return float(mark_price)
            
            logger.warning(f"未找到交易對 {symbol} 的標記價格信息")
            return None
            
        except Exception as e:
            logger.error(f"獲取標記價格時出錯: {e}")
            return None
    
    async def get_index_price(self, symbol: str) -> Optional[float]:
        """
        獲取指數價格
        
        Args:
            symbol: 交易對符號，如 "BTC-USDC" 或市場索引字符串
            
        Returns:
            Optional[float]: 指數價格，如果獲取失敗則返回 None
        """
        try:
            # 將 symbol 轉換為 market_index
            market_index = self._symbol_to_market_index(symbol)
            logger.info(f"獲取指數價格 - 交易對: {symbol} (市場索引: {market_index})")
            
            # 通過市場信息獲取指數價格
            market_info_result = await self.get_market_info(market_index)
            
            if not market_info_result.get("success"):
                logger.error(f"獲取市場信息失敗: {market_info_result.get('error', '未知錯誤')}")
                return None
            
            market_data = market_info_result.get("market_info", {})
            
            # 嘗試從市場數據中提取指數價格
            if isinstance(market_data, dict):
                index_price = market_data.get("index_price") or market_data.get("indexPrice")
                if index_price is not None:
                    return float(index_price)
            
            logger.warning(f"未找到交易對 {symbol} 的指數價格信息")
            return None
            
        except Exception as e:
            logger.error(f"獲取指數價格時出錯: {e}")
            return None
    
    async def get_funding_payments(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        獲取資金費用支付記錄 - 匹配 backpack 接口
        
        Args:
            symbol: 交易對符號，如果為 None 則獲取所有市場的記錄
            limit: 返回記錄數量限制
            
        Returns:
            List[Dict]: 資金費用支付記錄列表
        """
        try:
            # 如果提供了 symbol，轉換為 market_index  
            market_index = None
            if symbol:
                try:
                    market_index = self._symbol_to_market_index(symbol)
                except ValueError:
                    logger.warning(f"無法解析交易對符號: {symbol}")
            
            logger.info(f"獲取資金費用支付記錄 - 交易對: {symbol}, 市場索引: {market_index}, 限制: {limit}")
            
            # 由於 Lighter API 可能沒有直接的資金費用支付記錄接口
            # 這裡返回空列表，實際實現需要根據 API 文檔調整
            return []
            
        except Exception as e:
            logger.error(f"獲取資金費用支付記錄時出錯: {e}")
            return []
    
    async def get_open_interest(self, symbol: str) -> Optional[float]:
        """
        獲取未平倉合約數量
        
        Args:
            symbol: 交易對符號，如 "BTC-USDC" 或市場索引字符串
            
        Returns:
            Optional[float]: 未平倉合約數量，如果獲取失敗則返回 None
        """
        try:
            # 將 symbol 轉換為 market_index
            market_index = self._symbol_to_market_index(symbol)
            logger.info(f"獲取未平倉合約數量 - 交易對: {symbol} (市場索引: {market_index})")
            
            # 通過市場信息獲取未平倉合約數量
            market_info_result = await self.get_market_info(market_index)
            
            if not market_info_result.get("success"):
                logger.error(f"獲取市場信息失敗: {market_info_result.get('error', '未知錯誤')}")
                return None
            
            market_data = market_info_result.get("market_info", {})
            
            # 嘗試從市場數據中提取未平倉合約數量
            if isinstance(market_data, dict):
                open_interest = market_data.get("open_interest") or market_data.get("openInterest")
                if open_interest is not None:
                    return float(open_interest)
            
            logger.warning(f"未找到交易對 {symbol} 的未平倉合約數量信息")
            return None
            
        except Exception as e:
            logger.error(f"獲取未平倉合約數量時出錯: {e}")
            return None
    
    async def get_order_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        獲取訂單歷史記錄 - 匹配 backpack 接口
        
        Args:
            symbol: 交易對符號，如果為 None 則獲取所有市場的記錄
            limit: 返回記錄數量限制
            
        Returns:
            List[Dict]: 訂單歷史記錄列表
        """
        try:
            # 如果提供了 symbol，轉換為 market_index  
            market_index = None
            if symbol:
                try:
                    market_index = self._symbol_to_market_index(symbol)
                except ValueError:
                    logger.warning(f"無法解析交易對符號: {symbol}")
            
            logger.info(f"獲取訂單歷史記錄 - 交易對: {symbol}, 市場索引: {market_index}, 限制: {limit}")
            
            # 由於 Lighter API 可能沒有直接的訂單歷史記錄接口
            # 這裡返回空列表，實際實現需要根據 API 文檔調整
            return []
            
        except Exception as e:
            logger.error(f"獲取訂單歷史記錄時出錯: {e}")
            return []
    
    async def get_fill_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        獲取成交歷史記錄 - 匹配 backpack 接口
        
        Args:
            symbol: 交易對符號，如果為 None 則獲取所有市場的記錄
            limit: 返回記錄數量限制
            
        Returns:
            List[Dict]: 成交歷史記錄列表
        """
        try:
            # 如果提供了 symbol，轉換為 market_index  
            market_index = None
            if symbol:
                try:
                    market_index = self._symbol_to_market_index(symbol)
                except ValueError:
                    logger.warning(f"無法解析交易對符號: {symbol}")
            
            logger.info(f"獲取成交歷史記錄 - 交易對: {symbol}, 市場索引: {market_index}, 限制: {limit}")
            
            # 由於 Lighter API 可能沒有直接的成交歷史記錄接口
            # 這裡返回空列表，實際實現需要根據 API 文檔調整
            return []
            
        except Exception as e:
            logger.error(f"獲取成交歷史記錄時出錯: {e}")
            return []
    
    async def execute_order(self, order_type: str, market_index: int, amount: float, 
                           price: Optional[float] = None, **kwargs) -> Dict:
        """
        執行訂單（通用訂單執行方法）
        
        Args:
            order_type: 訂單類型 ('limit', 'market')
            market_index: 市場索引
            amount: 訂單數量（正數為買入，負數為賣出）
            price: 價格（限價單必須提供）
            **kwargs: 其他參數
            
        Returns:
            Dict: 訂單執行結果
        """
        try:
            logger.info(f"執行訂單 - 類型: {order_type}, 市場: {market_index}, 數量: {amount}, 價格: {price}")
            
            # 確定訂單方向：正數為買入(False)，負數為賣出(True)
            is_ask = amount < 0
            base_amount = abs(amount)  # 使用絕對值作為數量
            
            # 生成客戶端訂單索引
            client_order_index = int(time.time() * 1000) % 1000000
            
            if order_type.lower() == 'limit':
                if price is None:
                    return {
                        "success": False,
                        "error": "限價單必須提供價格"
                    }
                return await self.create_limit_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=base_amount,
                    price=price,
                    is_ask=is_ask,
                    **kwargs
                )
            
            elif order_type.lower() == 'market':
                return await self.create_market_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=base_amount,
                    is_ask=is_ask,
                    **kwargs
                )
            
            else:
                return {
                    "success": False,
                    "error": f"不支持的訂單類型: {order_type}"
                }
                
        except Exception as e:
            return self._handle_api_error("執行訂單", e)
    
    # ==================== 餘額查詢功能 ====================
    
    async def get_balance(self, asset: str = "USDC") -> Dict:
        """獲取帳戶餘額
        
        Args:
            asset: 資產類型，默認為 USDC
            
        Returns:
            Dict: 餘額信息
        """
        try:
            self._ensure_initialized()
            logger.info(f"獲取餘額 - 資產: {asset}")
            
            # 使用現有的 get_account_balance 方法
            account_balance_result = await self.get_account_balance()
            
            if account_balance_result.get("success"):
                balance_info = account_balance_result.get("balance_info", {})
                
                result = {
                    "success": True,
                    "asset": asset,
                    "account_index": self.account_index,
                    "balance_info": balance_info,
                    "total_collateral": balance_info.get("total_collateral", 0.0),
                    "available_balance": balance_info.get("available_balance", 0.0)
                }
                
                logger.info(f"餘額獲取成功 - 資產: {asset}, 可用餘額: {result['available_balance']}")
                return result
            else:
                return {
                    "success": False,
                    "error": account_balance_result.get("error", "無法獲取帳戶餘額"),
                    "asset": asset,
                    "account_index": self.account_index
                }
            
        except Exception as e:
            return self._handle_api_error("獲取餘額", e)
    
    # ==================== WebSocket 即時訂閱功能 ====================
    
    def _default_account_handler(self, message):
        """默認帳戶更新處理函數"""
        logger.debug(f"收到帳戶更新: {message}")
    
    async def subscribe_order_fills(self, callback: Callable[[Dict], None]) -> Dict:
        """訂閱掛單成交通知 - 重構版本，使用會話式持久連接模式
        
        Args:
            callback: 回調函數，接收成交數據
            
        Returns:
            Dict: 訂閱結果
        """
        try:
            logger.info("訂閱掛單成交通知")
            
            # 確保 WebSocket 會話已啟動
            self._ensure_websocket_session_active()
            
            # 添加帳戶訂閱 (如果需要)
            success = await self._add_account_subscription(self.account_index)
            if not success:
                return self._handle_api_error("訂閱掛單成交", Exception("添加帳戶訂閱失敗"))
            
            # 添加回調函數
            self._subscription_callbacks['order_fills'].append(callback)
            
            result = {
                "success": True,
                "subscription_type": "order_fills",
                "account_index": self.account_index,
                "message": "掛單成交訂閱成功"
            }
            
            logger.info("掛單成交訂閱成功 - 會話式持久連接模式")
            return result
            
        except Exception as e:
            return self._handle_api_error("訂閱掛單成交", e)
    
    def subscribe_funding_rates(self, market_ids: List[int], callback: Callable[[Dict], None]) -> Dict:
        """訂閱資金費率更新
        
        Args:
            market_ids: 市場ID列表
            callback: 回調函數，接收資金費率數據
            
        Returns:
            Dict: 訂閱結果
        """
        try:
            logger.info(f"訂閱資金費率更新 - 市場ID: {market_ids}")
            
            # 設置資金費率更新回調
            def funding_rate_handler(market_id, update_data):
                try:
                    # 處理資金費率數據
                    processed_data = {
                        "type": "funding_rate_update",
                        "market_id": market_id,
                        "timestamp": time.time(),
                        "data": update_data
                    }
                    callback(processed_data)
                except Exception as e:
                    logger.error(f"處理資金費率回調時出錯: {e}")
            
            # 初始化 WebSocket 客戶端並設置回調
            self._init_websocket(market_ids)
            self.ws_client.on_order_book_update = funding_rate_handler
            
            result = {
                "success": True,
                "subscription_type": "funding_rates",
                "market_ids": market_ids,
                "message": "資金費率訂閱設置成功，請調用 start_websocket() 或 start_websocket_async() 啟動連接"
            }
            
            logger.info(f"資金費率訂閱設置成功 - 市場ID: {market_ids}")
            return result
            
        except Exception as e:
            return self._handle_api_error("訂閱資金費率", e)
    
    def _default_orderbook_handler(self, message):
        """默認訂單簿更新處理函數"""
        logger.debug(f"收到訂單簿更新: {message}")
    
    def _init_websocket(self, order_book_ids=None):
        """初始化 WebSocket 客戶端"""
        if self.ws_client is None:
            try:
                # 從 base_url 提取 host
                host = self.base_url.replace("https://", "").replace("http://", "")
                
                # 設置訂閱的訂單簿ID
                if order_book_ids is None:
                    order_book_ids = []
                
                self.ws_client = WsClient(
                    host=host,
                    path="/stream",
                    account_ids=[self.account_index],  # 訂閱帳戶更新
                    order_book_ids=order_book_ids,  # 根據需求訂閱訂單簿
                    on_account_update=self._default_account_handler,
                    on_order_book_update=self._default_orderbook_handler
                )
                logger.info("WebSocket 客戶端初始化成功")
            except Exception as e:
                logger.error(f"WebSocket 客戶端初始化失敗: {e}")
                raise
    
    def start_websocket(self, order_book_ids=None):
        """啟動 WebSocket 連接（同步模式）"""
        try:
            self._init_websocket(order_book_ids)
            logger.info("啟動 WebSocket 連接")
            self.ws_client.run()
        except Exception as e:
            logger.error(f"啟動 WebSocket 連接失敗: {e}")
            raise
    
    async def start_websocket_async(self, order_book_ids=None):
        """啟動 WebSocket 連接（異步模式）"""
        try:
            self._init_websocket(order_book_ids)
            logger.info("啟動 WebSocket 異步連接")
            await self.ws_client.run_async()
        except Exception as e:
            logger.error(f"啟動 WebSocket 異步連接失敗: {e}")
            raise
    
    async def subscribe_order_updates(self, callback: Callable[[Dict], None]) -> Dict:
        """
        訂閱掛單成交狀態更新 - 重構版本，使用會話式持久連接模式
        
        Args:
            callback: 回調函數，接收訂單更新數據
            
        Returns:
            Dict: 訂閱結果
        """
        try:
            logger.info(f"訂閱掛單成交狀態 - 帳戶索引: {self.account_index}")
            
            # 確保 WebSocket 會話已啟動
            self._ensure_websocket_session_active()
            
            # 添加帳戶訂閱 (如果需要)
            success = await self._add_account_subscription(self.account_index)
            if not success:
                return self._handle_api_error("訂閱訂單更新", Exception("添加帳戶訂閱失敗"))
            
            # 添加回調函數
            self._subscription_callbacks['order_updates'].append(callback)
            
            result = {
                "success": True,
                "subscription_type": "order_updates",
                "account_index": self.account_index,
                "message": "掛單成交狀態訂閱成功"
            }
            
            logger.info("掛單成交狀態訂閱成功 - 會話式持久連接模式")
            return result
            
        except Exception as e:
            return self._handle_api_error("訂閱掛單成交狀態", e)
    
    def subscribe_funding_rate_updates(self, market_id: int, callback: Callable[[Dict], None]) -> Dict:
        """
        訂閱資金費率變動
        
        Args:
            market_id: 市場ID
            callback: 回調函數，接收資金費率更新數據
            
        Returns:
            Dict: 訂閱結果
        """
        try:
            logger.info(f"訂閱資金費率變動 - 市場ID: {market_id}")
            
            # 設置資金費率更新回調
            def funding_rate_handler(order_book_id, update_data):
                try:
                    # 處理資金費率更新數據
                    processed_data = {
                        "type": "funding_rate_update",
                        "market_id": market_id,
                        "order_book_id": order_book_id,
                        "timestamp": time.time(),
                        "data": update_data
                    }
                    callback(processed_data)
                except Exception as e:
                    logger.error(f"處理資金費率更新回調時出錯: {e}")
            
            # 初始化 WebSocket 客戶端並設置回調
            self._init_websocket([market_id])
            self.ws_client.on_order_book_update = funding_rate_handler
            
            result = {
                "success": True,
                "subscription_type": "funding_rate_updates",
                "market_id": market_id,
                "message": "資金費率變動訂閱設置成功，請調用 start_websocket() 或 start_websocket_async() 啟動連接"
            }
            
            logger.info(f"資金費率變動訂閱設置成功 - 市場ID: {market_id}")
            return result
            
        except Exception as e:
            return self._handle_api_error("訂閱資金費率變動", e)
    
    async def subscribe_orderbook_updates(self, market_id: int, callback: Callable[[Dict], None]) -> Dict:
        """
        訂閱指定交易對訂單簿深度 - 重構版本，使用會話式持久連接模式
        
        Args:
            market_id: 市場ID
            callback: 回調函數，接收訂單簿更新數據
            
        Returns:
            Dict: 訂閱結果
        """
        try:
            logger.info(f"訂閱訂單簿深度 - 市場ID: {market_id}")
            
            # 確保 WebSocket 會話已啟動
            self._ensure_websocket_session_active()
            
            # 添加訂單簿訂閱 (如果需要)
            success = await self._add_orderbook_subscription(market_id)
            if not success:
                return self._handle_api_error("訂閱訂單簿深度", Exception("添加訂單簿訂閱失敗"))
            
            # 添加回調函數到對應的市場
            if market_id not in self._subscription_callbacks['orderbook_updates']:
                self._subscription_callbacks['orderbook_updates'][market_id] = []
            self._subscription_callbacks['orderbook_updates'][market_id].append(callback)
            
            result = {
                "success": True,
                "subscription_type": "orderbook_updates",
                "market_id": market_id,
                "message": "訂單簿深度訂閱成功"
            }
            
            logger.info(f"訂單簿深度訂閱成功 - 市場ID: {market_id} - 會話式持久連接模式")
            return result
            
        except Exception as e:
            return self._handle_api_error("訂閱訂單簿深度", e)
    
    async def unsubscribe_all(self) -> Dict:
        """
        取消所有 WebSocket 訂閱
        
        Returns:
            Dict: 取消訂閱結果
        """
        try:
            logger.info("取消所有 WebSocket 訂閱")
            
            if self.ws_client:
                # WsClient 沒有 close 方法，直接設為 None
                self.ws_client = None
            
            result = {
                "success": True,
                "message": "所有 WebSocket 訂閱已取消"
            }
            
            logger.info("所有 WebSocket 訂閱已取消")
            return result
            
        except Exception as e:
            return self._handle_api_error("取消 WebSocket 訂閱", e)
    
    # ==================== 輔助功能 ====================
    
    async def get_market_info(self, market_id: Optional[int] = None) -> Dict:
        """
        獲取市場信息
        
        Args:
            market_id: 市場ID (可選，不指定則獲取所有市場)
            
        Returns:
            Dict: 市場信息
        """
        try:
            logger.info(f"獲取市場信息 - 市場ID: {market_id or '全部'}")
            
            if market_id:
                market_info = await self.order_api.order_book_details(market_id=market_id)
            else:
                market_info = await self.order_api.order_books()
            
            result = {
                "success": True,
                "market_id": market_id,
                "market_info": market_info.model_dump() if hasattr(market_info, 'model_dump') else str(market_info)
            }
            
            logger.info(f"市場信息獲取成功 - 市場ID: {market_id or '全部'}")
            return result
            
        except Exception as e:
            return self._handle_api_error("獲取市場信息", e)
    
    # ==================== WebSocket 交易功能 ====================
    
    async def _ws_send_transaction(self, tx_type: int, tx_info: str, operation_name: str) -> Dict:
        """
        通過 WebSocket 發送交易 - 使用持久連接重構版本
        
        Args:
            tx_type: 交易類型（來自 SignerClient 常量）
            tx_info: 已簽名的交易信息
            operation_name: 操作名稱（用於日誌）
            
        Returns:
            Dict: 交易發送結果
        """
        try:
            logger.info(f"通過 WebSocket 發送交易 - 操作: {operation_name}, TX類型: {tx_type}")
            
            # 確保 WebSocket 連接可用
            ws = await self._ensure_websocket_connection()
            
            # 發送交易
            tx_message = {
                "type": "jsonapi/sendtx",
                "data": {
                    "tx_type": tx_type,
                    "tx_info": json.loads(tx_info),
                },
            }
            
            logger.debug(f"發送交易消息: {json.dumps(tx_message, indent=2)}")
            await ws.send(json.dumps(tx_message))
            
            # 接收回應
            response_msg = await ws.recv()
            logger.debug(f"收到回應: {response_msg}")
            
            # 解析回應
            try:
                response_data = json.loads(response_msg)
                
                # 檢查是否成功
                if "success" in response_data or "tx_hash" in response_data:
                    result = {
                        "success": True,
                        "tx_hash": response_data.get("tx_hash", "unknown"),
                        "response": response_data,
                        "method": "websocket_persistent",
                        "operation": operation_name
                    }
                    logger.info(f"{operation_name} 通過持久 WebSocket 成功 - TX Hash: {result['tx_hash']}")
                    return result
                else:
                    error_msg = response_data.get("error", response_msg)
                    return self._handle_api_error(f"{operation_name} (WebSocket)", Exception(f"WebSocket 回應錯誤: {error_msg}"))
                    
            except json.JSONDecodeError:
                # 如果不是 JSON，直接使用回應內容
                result = {
                    "success": True,
                    "tx_hash": "websocket_response",
                    "response": response_msg,
                    "method": "websocket_persistent",
                    "operation": operation_name
                }
                logger.info(f"{operation_name} 通過持久 WebSocket 完成 - 回應: {response_msg}")
                return result
                
        except Exception as e:
            # 如果連接出錯，嘗試關閉並重置連接
            error_str = str(e).lower()
            if ("connection" in error_str or 
                "websocket" in error_str or 
                "closed" in error_str or
                "attribute" in error_str):
                logger.warning(f"WebSocket 連接異常，將重置連接: {e}")
                self._last_ws_error_time = time.time()  # 記錄錯誤時間
                try:
                    await self._close_websocket_connection()
                except Exception as close_error:
                    logger.debug(f"重置 WebSocket 連接時出錯: {close_error}")
            return self._handle_api_error(f"{operation_name} (WebSocket)", e)
    
    async def ws_create_market_order(self,
                                   market_index: int,
                                   client_order_index: int,
                                   base_amount: float,
                                   is_ask: bool,
                                   reduce_only: bool = False) -> Dict:
        """
        通過 WebSocket 創建市價訂單
        
        Args:
            market_index: 市場索引
            client_order_index: 客戶端訂單索引
            base_amount: 基礎數量
            is_ask: 是否為賣單 (True=賣, False=買)
            reduce_only: 是否僅減倉
            
        Returns:
            Dict: 訂單創建結果
        """
        try:
            self._ensure_initialized()
            logger.info(f"WS 創建市價訂單 - 市場: {market_index}, 數量: {base_amount}, 方向: {'賣' if is_ask else '買'}")
            
            # 格式化參數
            base_amount_formatted = self._format_amount(base_amount, market_index)
            
            # 獲取當前價格作為執行價格
            if is_ask:
                # 賣單：使用較低的價格作為最差可接受價格
                avg_execution_price = int(1000 * 100)  # $10.00 作為最低價格
            else:
                # 買單：使用較高的價格作為最差可接受價格  
                avg_execution_price = int(100000 * 100)  # $1000.00 作為最高價格
            
            # 獲取下一個 nonce
            # 重要: 這裡必須使用與 sign_create_order 相同的 nonce
            # 並且要確保在多線程/異步環境下不會有競爭條件
            # Lighter API 要求 nonce 嚴格遞增
            
            # 暫時修改: 直接調用 self.transaction_api.next_nonce 來獲取
            next_nonce_response = await self.transaction_api.next_nonce(
                account_index=self.account_index, 
                api_key_index=self.api_key_index
            )
            nonce_value = next_nonce_response.nonce
            
            # 確保 SignerClient 使用正確的私鑰和帳戶信息
            # 有時候初始化可能會有問題，這裡再次確認
            if self.signer_client.account_index != self.account_index:
                logger.warning(f"SignerClient account_index ({self.signer_client.account_index}) 與當前 account_index ({self.account_index}) 不匹配，正在修正...")
                self.signer_client.account_index = self.account_index
                
            if self.signer_client.api_key_index != self.api_key_index:
                logger.warning(f"SignerClient api_key_index ({self.signer_client.api_key_index}) 與當前 api_key_index ({self.api_key_index}) 不匹配，正在修正...")
                self.signer_client.api_key_index = self.api_key_index
            
            # 簽署市價訂單

            tx_info, error = self.signer_client.sign_create_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount_formatted,
                price=avg_execution_price,
                is_ask=is_ask,
                order_type=self.ORDER_TYPE_MARKET,
                time_in_force=self.TIME_IN_FORCE_IOC,
                reduce_only=int(reduce_only),
                trigger_price=0,
                order_expiry=SignerClient.DEFAULT_IOC_EXPIRY,
                nonce=nonce_value
            )
            
            if error is not None:
                return self._handle_api_error("WS 創建市價訂單 - 簽署", Exception(f"簽署訂單失敗: {error}"))
            
            # 通過 WebSocket 發送交易
            result = await self._ws_send_transaction(
                tx_type=SignerClient.TX_TYPE_CREATE_ORDER,
                tx_info=tx_info,
                operation_name="WS 創建市價訂單"
            )
            
            if result.get("success"):
                result["order_info"] = {
                    "market_index": market_index,
                    "client_order_index": client_order_index,
                    "base_amount": base_amount,
                    "is_ask": is_ask,
                    "order_type": "market",
                    "reduce_only": reduce_only
                }
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 創建市價訂單", e)
    
    async def ws_create_stop_loss_order(self,
                                      market_index: int,
                                      client_order_index: int,
                                      base_amount: float,
                                      trigger_price: float,
                                      is_ask: bool,
                                      reduce_only: bool = True) -> Dict:
        """
        通過 WebSocket 創建止損訂單 (Stop Market)
        """
        try:
            self._ensure_initialized()
            logger.info(f"WS 創建止損訂單 - 市場: {market_index}, 數量: {base_amount}, 觸發價: {trigger_price}, 方向: {'賣' if is_ask else '買'}")
            
            base_amount_formatted = self._format_amount(base_amount, market_index)
            trigger_price_formatted = self._format_price(trigger_price)

            # 止損單 (Stop Market) 觸發後的執行價格
            # 使用觸發價格加上合理的滑點（1%）作為最差可接受價格
            max_slippage = 0.01  # 1% 滑點

            if is_ask:
                # 賣單：觸發後最多下跌 1%
                execution_price = trigger_price * (1 - max_slippage)
                price_formatted = self._format_price(max(execution_price, 1.0))  # 確保至少 $1
            else:
                # 買單：觸發後最多上漲 1%
                execution_price = trigger_price * (1 + max_slippage)
                price_formatted = self._format_price(execution_price)
            
            # 獲取下一個 nonce
            next_nonce_response = await self.transaction_api.next_nonce(
                account_index=self.account_index, 
                api_key_index=self.api_key_index
            )
            nonce_value = next_nonce_response.nonce
            
            # 簽署訂單
            tx_info, error = self.signer_client.sign_create_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount_formatted,
                price=price_formatted,
                is_ask=is_ask,
                order_type=self.ORDER_TYPE_STOP_LOSS,
                time_in_force=self.TIME_IN_FORCE_GTT,
                reduce_only=int(reduce_only),
                trigger_price=trigger_price_formatted,
                order_expiry=int(time.time() * 1000) + 30 * 24 * 3600 * 1000, # 30 days
                nonce=nonce_value
            )
            
            if error is not None:
                return self._handle_api_error("WS 創建止損訂單 - 簽署", Exception(f"簽署訂單失敗: {error}"))
            
            # 通過 WebSocket 發送交易
            result = await self._ws_send_transaction(
                tx_type=SignerClient.TX_TYPE_CREATE_ORDER,
                tx_info=tx_info,
                operation_name="WS 創建止損訂單"
            )
            
            if result.get("success"):
                result["order_info"] = {
                    "market_index": market_index,
                    "client_order_index": client_order_index,
                    "base_amount": base_amount,
                    "trigger_price": trigger_price,
                    "is_ask": is_ask,
                    "order_type": "stop_loss",
                    "reduce_only": reduce_only
                }
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 創建止損訂單", e)

    async def ws_create_take_profit_order(self,
                                        market_index: int,
                                        client_order_index: int,
                                        base_amount: float,
                                        trigger_price: float,
                                        is_ask: bool,
                                        reduce_only: bool = True) -> Dict:
        """
        通過 WebSocket 創建止盈訂單 (Take Profit Market)
        """
        try:
            self._ensure_initialized()
            logger.info(f"WS 創建止盈訂單 - 市場: {market_index}, 數量: {base_amount}, 觸發價: {trigger_price}, 方向: {'賣' if is_ask else '買'}")
            
            base_amount_formatted = self._format_amount(base_amount, market_index)
            trigger_price_formatted = self._format_price(trigger_price)

            # 止盈單 (Take Profit Market) 觸發後的執行價格
            # 使用觸發價格加上合理的滑點（1%）作為最差可接受價格
            max_slippage = 0.01  # 1% 滑點

            if is_ask:
                # 賣單：觸發後最多下跌 1%
                execution_price = trigger_price * (1 - max_slippage)
                price_formatted = self._format_price(max(execution_price, 1.0))  # 確保至少 $1
            else:
                # 買單：觸發後最多上漲 1%
                execution_price = trigger_price * (1 + max_slippage)
                price_formatted = self._format_price(execution_price)
            
            # 獲取下一個 nonce
            next_nonce_response = await self.transaction_api.next_nonce(
                account_index=self.account_index, 
                api_key_index=self.api_key_index
            )
            nonce_value = next_nonce_response.nonce
            
            # 簽署訂單
            tx_info, error = self.signer_client.sign_create_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount_formatted,
                price=price_formatted,
                is_ask=is_ask,
                order_type=self.ORDER_TYPE_TAKE_PROFIT,
                time_in_force=self.TIME_IN_FORCE_GTT,
                reduce_only=int(reduce_only),
                trigger_price=trigger_price_formatted,
                order_expiry=int(time.time() * 1000) + 30 * 24 * 3600 * 1000, # 30 days
                nonce=nonce_value
            )
            
            if error is not None:
                return self._handle_api_error("WS 創建止盈訂單 - 簽署", Exception(f"簽署訂單失敗: {error}"))
            
            # 通過 WebSocket 發送交易
            result = await self._ws_send_transaction(
                tx_type=SignerClient.TX_TYPE_CREATE_ORDER,
                tx_info=tx_info,
                operation_name="WS 創建止盈訂單"
            )
            
            if result.get("success"):
                result["order_info"] = {
                    "market_index": market_index,
                    "client_order_index": client_order_index,
                    "base_amount": base_amount,
                    "trigger_price": trigger_price,
                    "is_ask": is_ask,
                    "order_type": "take_profit",
                    "reduce_only": reduce_only
                }
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 創建止盈訂單", e)

    async def ws_create_limit_order(self,
                                  market_index: int,
                                  client_order_index: int,
                                  base_amount: float,
                                  price: float,
                                  is_ask: bool,
                                  reduce_only: bool = False,
                                  time_in_force: int = None,
                                  order_expiry: int = -1) -> Dict:
        """
        通過 WebSocket 創建限價訂單
        
        Args:
            market_index: 市場索引
            client_order_index: 客戶端訂單索引
            base_amount: 基礎數量
            price: 價格
            is_ask: 是否為賣單 (True=賣, False=買)
            reduce_only: 是否僅減倉
            time_in_force: 訂單時效
            order_expiry: 訂單過期時間
            
        Returns:
            Dict: 訂單創建結果
        """
        try:
            self._ensure_initialized()
            
            # 設置默認時效
            if time_in_force is None:
                time_in_force = self.TIME_IN_FORCE_GTT
            
            logger.info(f"WS 創建限價訂單 - 市場: {market_index}, 數量: {base_amount}, 價格: {price}, 方向: {'賣' if is_ask else '買'}")
            
            # 格式化參數
            base_amount_formatted = self._format_amount(base_amount, market_index)
            price_formatted = self._format_price(price)
            
            # 獲取下一個 nonce
            next_nonce_response = await self.transaction_api.next_nonce(
                account_index=self.account_index, 
                api_key_index=self.api_key_index
            )
            nonce_value = next_nonce_response.nonce
            
            # 簽署限價訂單
            tx_info, error = self.signer_client.sign_create_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount_formatted,
                price=price_formatted,
                is_ask=is_ask,
                order_type=self.ORDER_TYPE_LIMIT,
                time_in_force=time_in_force,
                reduce_only=int(reduce_only),
                trigger_price=0,
                order_expiry=order_expiry,
                nonce=nonce_value
            )
            
            if error is not None:
                return self._handle_api_error("WS 創建限價訂單 - 簽署", Exception(f"簽署訂單失敗: {error}"))
            
            # 通過 WebSocket 發送交易
            result = await self._ws_send_transaction(
                tx_type=SignerClient.TX_TYPE_CREATE_ORDER,
                tx_info=tx_info,
                operation_name="WS 創建限價訂單"
            )
            
            if result.get("success"):
                result["order_info"] = {
                    "market_index": market_index,
                    "client_order_index": client_order_index,
                    "base_amount": base_amount,
                    "price": price,
                    "is_ask": is_ask,
                    "order_type": "limit",
                    "reduce_only": reduce_only
                }
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 創建限價訂單", e)
    
    async def ws_close_market_order(self,
                                  market_index: int,
                                  position_size: float,
                                  is_long_position: bool,
                                  client_order_index: Optional[int] = None) -> Dict:
        """
        通過 WebSocket 市價平倉
        
        Args:
            market_index: 市場索引
            position_size: 持倉大小 (絕對值)
            is_long_position: 是否為多頭持倉
            client_order_index: 客戶端訂單索引 (可選，自動生成)
            
        Returns:
            Dict: 平倉結果
        """
        try:
            self._ensure_initialized()
            
            # 自動生成客戶端訂單索引
            if client_order_index is None:
                client_order_index = int(time.time() * 1000) % 1000000
            
            # 平倉需要反向操作：多頭持倉需要賣出，空頭持倉需要買入
            is_ask = is_long_position  # 多頭平倉=賣出, 空頭平倉=買入
            
            logger.info(f"WS 市價平倉 - 市場: {market_index}, 持倉大小: {position_size}, 持倉方向: {'多頭' if is_long_position else '空頭'}")
            
            return await self.ws_create_market_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=position_size,
                is_ask=is_ask,
                reduce_only=True  # 設置為僅減倉
            )
            
        except Exception as e:
            return self._handle_api_error("WS 市價平倉", e)
    
    async def ws_close_limit_order(self,
                                 market_index: int,
                                 position_size: float,
                                 price: float,
                                 is_long_position: bool,
                                 client_order_index: Optional[int] = None,
                                 time_in_force: int = None,
                                 order_expiry: int = -1) -> Dict:
        """
        通過 WebSocket 限價平倉
        
        Args:
            market_index: 市場索引
            position_size: 持倉大小 (絕對值)
            price: 限價價格
            is_long_position: 是否為多頭持倉
            client_order_index: 客戶端訂單索引 (可選，自動生成)
            time_in_force: 訂單時效
            order_expiry: 訂單過期時間
            
        Returns:
            Dict: 平倉結果
        """
        try:
            self._ensure_initialized()
            
            # 自動生成客戶端訂單索引
            if client_order_index is None:
                client_order_index = int(time.time() * 1000) % 1000000
            
            # 平倉需要反向操作：多頭持倉需要賣出，空頭持倉需要買入
            is_ask = is_long_position  # 多頭平倉=賣出, 空頭平倉=買入
            
            logger.info(f"WS 限價平倉 - 市場: {market_index}, 持倉大小: {position_size}, 價格: {price}, 持倉方向: {'多頭' if is_long_position else '空頭'}")
            
            return await self.ws_create_limit_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=position_size,
                price=price,
                is_ask=is_ask,
                reduce_only=True,  # 設置為僅減倉
                time_in_force=time_in_force,
                order_expiry=order_expiry
            )
            
        except Exception as e:
            return self._handle_api_error("WS 限價平倉", e)
    
    async def ws_cancel_order(self, market_index: int, order_index: int) -> Dict:
        """
        通過 WebSocket 取消訂單
        
        Args:
            market_index: 市場索引
            order_index: 訂單索引
            
        Returns:
            Dict: 取消結果
        """
        try:
            self._ensure_initialized()
            logger.info(f"WS 取消訂單 - 市場: {market_index}, 訂單索引: {order_index}")
            
            # 獲取下一個 nonce
            next_nonce_response = await self.transaction_api.next_nonce(
                account_index=self.account_index, 
                api_key_index=self.api_key_index
            )
            nonce_value = next_nonce_response.nonce
            
            # 簽署取消訂單
            tx_info, error = self.signer_client.sign_cancel_order(
                market_index=market_index,
                order_index=order_index,
                nonce=nonce_value
            )
            
            if error is not None:
                return self._handle_api_error("WS 取消訂單 - 簽署", Exception(f"簽署取消訂單失敗: {error}"))
            
            # 通過 WebSocket 發送交易
            result = await self._ws_send_transaction(
                tx_type=SignerClient.TX_TYPE_CANCEL_ORDER,
                tx_info=tx_info,
                operation_name="WS 取消訂單"
            )
            
            if result.get("success"):
                result["cancelled_order"] = {
                    "market_index": market_index,
                    "order_index": order_index
                }
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 取消訂單", e)
    
    async def ws_cancel_all_orders(self, time_in_force: int = None, time: int = 0) -> Dict:
        """
        通過 WebSocket 取消所有訂單
        
        Args:
            time_in_force: 取消時效
            time: 時間參數
            
        Returns:
            Dict: 取消結果
        """
        try:
            self._ensure_initialized()
            
            # 設置默認時效
            if time_in_force is None:
                time_in_force = self.CANCEL_ALL_TIF_IMMEDIATE
            
            logger.info("WS 取消所有訂單")
            
            # 獲取下一個 nonce
            next_nonce_response = await self.transaction_api.next_nonce(
                account_index=self.account_index, 
                api_key_index=self.api_key_index
            )
            nonce_value = next_nonce_response.nonce
            
            # 簽署取消所有訂單
            tx_info, error = self.signer_client.sign_cancel_all_orders(
                time_in_force=time_in_force,
                time=time,
                nonce=nonce_value
            )
            
            if error is not None:
                return self._handle_api_error("WS 取消所有訂單 - 簽署", Exception(f"簽署取消所有訂單失敗: {error}"))
            
            # 通過 WebSocket 發送交易
            result = await self._ws_send_transaction(
                tx_type=SignerClient.TX_TYPE_CANCEL_ALL_ORDERS,
                tx_info=tx_info,
                operation_name="WS 取消所有訂單"
            )
            
            if result.get("success"):
                result["cancel_info"] = {
                    "time_in_force": time_in_force,
                    "time": time
                }
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 取消所有訂單", e)
    
    async def ws_cancel_all_position(self) -> Dict:
        """
        通過 WebSocket 平倉所有持倉
        
        該功能會：
        1. 先獲取當前所有持倉
        2. 為每個有效持倉創建市價平倉訂單
        3. 返回所有平倉操作的結果
        
        Returns:
            Dict: 平倉結果，包含每個市場的平倉狀況
        """
        try:
            self._ensure_initialized()
            logger.info("WS 開始平倉所有持倉")
            
            # 先獲取當前所有持倉
            positions_result = await self.get_positions()
            if not positions_result.get("success"):
                return self._handle_api_error("WS 平倉所有持倉 - 獲取持倉", Exception(f"無法獲取持倉信息: {positions_result.get('error', '未知錯誤')}"))
            
            positions = positions_result.get("positions", [])
            if not positions:
                logger.info("WS 平倉所有持倉 - 沒有發現任何持倉")
                return {
                    "success": True,
                    "message": "沒有需要平倉的持倉",
                    "total_positions": 0,
                    "closed_positions": []
                }
            
            logger.info(f"WS 平倉所有持倉 - 發現 {len(positions)} 個持倉需要平倉")
            
            # 存储平倉結果
            close_results = []
            success_count = 0
            
            for position in positions:
                try:
                    market_index = position.get("market_index")
                    position_amount = position.get("position_amount", 0.0)
                    is_long = position.get("is_long", False)
                    
                    # 跳過沒有持倉的市場
                    if abs(position_amount) == 0.0:
                        logger.debug(f"跳過市場 {market_index} - 無持倉")
                        continue
                    
                    market_symbol = self._market_index_to_symbol(market_index)
                    logger.info(f"WS 平倉持倉 - 市場: {market_symbol} ({market_index}), 持倉: {position_amount}, 方向: {'多頭' if is_long else '空頭'}")
                    
                    # 執行市價平倉
                    close_result = await self.ws_close_market_order(
                        market_index=market_index,
                        position_size=abs(position_amount),
                        is_long_position=is_long
                    )
                    
                    close_results.append({
                        "market_index": market_index,
                        "market_symbol": market_symbol,
                        "position_amount": position_amount,
                        "is_long": is_long,
                        "close_result": close_result,
                        "success": close_result.get("success", False),
                        "tx_hash": close_result.get("tx_hash", None),
                        "error": close_result.get("error", None)
                    })
                    
                    if close_result.get("success"):
                        success_count += 1
                        logger.info(f"✅ 市場 {market_symbol} ({market_index}) 平倉成功 - TX: {close_result.get('tx_hash')}")
                    else:
                        logger.error(f"❌ 市場 {market_symbol} ({market_index}) 平倉失敗 - {close_result.get('error', '未知錯誤')}")
                    
                    # 每次平倉後等待1秒，避免請求過於頻繁
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"處理市場 {market_index} 持倉時出錯: {e}")
                    close_results.append({
                        "market_index": market_index,
                        "market_symbol": self._market_index_to_symbol(market_index) if market_index else "unknown",
                        "position_amount": position.get("position_amount", 0.0),
                        "is_long": position.get("is_long", False),
                        "close_result": {"success": False, "error": str(e)},
                        "success": False,
                        "tx_hash": None,
                        "error": str(e)
                    })
            
            # 整理最終結果
            result = {
                "success": success_count > 0,  # 只要有一個成功就算成功
                "total_positions": len(positions),
                "attempted_positions": len(close_results),
                "successful_closes": success_count,
                "failed_closes": len(close_results) - success_count,
                "close_results": close_results,
                "method": "websocket",
                "operation": "WS 平倉所有持倉"
            }
            
            if success_count == len(close_results):
                result["message"] = f"所有 {success_count} 個持倉均成功平倉"
                logger.info(f"✅ WS 平倉所有持倉完成 - 全部成功 ({success_count}/{len(close_results)})")
            elif success_count > 0:
                result["message"] = f"部分持倉平倉成功 ({success_count}/{len(close_results)})"
                logger.warning(f"⚠️ WS 平倉所有持倉完成 - 部分成功 ({success_count}/{len(close_results)})")
            else:
                result["message"] = f"所有持倉平倉均失敗 (0/{len(close_results)})"
                logger.error(f"❌ WS 平倉所有持倉完成 - 全部失敗 (0/{len(close_results)})")
            
            return result
            
        except Exception as e:
            return self._handle_api_error("WS 平倉所有持倉", e)

    
    
    async def close(self):
        """
        關閉客戶端連接 - 重構版本，增強錯誤處理並添加 WebSocket 會話清理
        """
        try:
            logger.info("開始關閉 Lighter 客戶端...")
            
            # 停止 WebSocket 會話 (新增)
            try:
                if self._subscription_active:
                    await self.stop_websocket_session()
                    logger.debug("WebSocket 會話已停止")
            except Exception as e:
                logger.warning(f"停止 WebSocket 會話時出錯: {e}")
            
            # 安全關閉 WebSocket 持久連接
            try:
                if hasattr(self, '_ws_connection') and self._ws_connection:
                    await self._close_websocket_connection()
                    logger.debug("WebSocket 持久連接已關閉")
            except Exception as e:
                logger.warning(f"關閉 WebSocket 持久連接時出錯: {e}")
            
            # 安全關閉 WebSocket 客戶端 (舊版本兼容)
            try:
                if hasattr(self, 'ws_client') and self.ws_client:
                    # WsClient 沒有 close 方法，直接設為 None
                    self.ws_client = None
                    logger.debug("WebSocket 客戶端已關閉")
            except Exception as e:
                logger.warning(f"關閉 WebSocket 客戶端時出錯: {e}")
            
            # 安全關閉 signer_client 的會話
            try:
                if hasattr(self, 'signer_client') and self.signer_client:
                    # SignerClient 有 close() 方法來關閉內部的 aiohttp session
                    if hasattr(self.signer_client, 'close'):
                        try:
                            await self.signer_client.close()
                            logger.debug("SignerClient 會話已關閉")
                        except Exception as close_error:
                            logger.warning(f"SignerClient.close() 失敗: {close_error}")
                    # 額外嘗試關閉可能存在的內部 session
                    if hasattr(self.signer_client, 'session') and self.signer_client.session:
                        try:
                            await self.signer_client.session.close()
                            logger.debug("SignerClient 內部 session 已關閉")
                        except Exception as session_error:
                            logger.warning(f"關閉 SignerClient.session 失敗: {session_error}")
            except Exception as e:
                logger.warning(f"關閉 SignerClient 會話時出錯: {e}")
            
            # 安全關閉 account_api 的 session
            try:
                if hasattr(self, 'account_api') and self.account_api and hasattr(self.account_api, 'api_client'):
                    api_client = self.account_api.api_client
                    if hasattr(api_client, 'rest_client') and api_client.rest_client:
                        await api_client.rest_client.close()
                    if hasattr(api_client, '_session') and api_client._session:
                        await api_client._session.close()
                    logger.debug("AccountAPI 會話已關閉")
            except Exception as e:
                logger.warning(f"關閉 AccountAPI 會話時出錯: {e}")
            
            # 安全關閉 order_api 的 session
            try:
                if hasattr(self, 'order_api') and self.order_api and hasattr(self.order_api, 'api_client'):
                    api_client = self.order_api.api_client
                    if hasattr(api_client, 'rest_client') and api_client.rest_client:
                        await api_client.rest_client.close()
                    if hasattr(api_client, '_session') and api_client._session:
                        await api_client._session.close()
                    logger.debug("OrderAPI 會話已關閉")
            except Exception as e:
                logger.warning(f"關閉 OrderAPI 會話時出錯: {e}")
            
            logger.info("Lighter 客戶端已安全關閉")
            
        except Exception as e:
            logger.error(f"關閉客戶端時發生未預期錯誤: {e}")
            # 即使出錯也不拋出異常，確保程序能正常退出
    
    def __del__(self):
        """析構函數"""
        try:
            if hasattr(self, 'ws_client') and self.ws_client:
                self.ws_client = None
            logger.info("Lighter 客戶端資源已清理")
        except Exception as e:
            logger.error(f"清理客戶端資源時出錯: {e}")