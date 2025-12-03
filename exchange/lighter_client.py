"""
Lighter DEX 客戶端適配器
將已封裝好的 LighterClient 適配成量化交易機器人需要的接口
"""
import os
import time
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

from config import settings, SignalType


@dataclass
class OrderResult:
    """訂單結果"""
    success: bool
    order_id: Optional[str]
    filled_price: Optional[float]
    filled_amount: Optional[float]
    message: str
    timestamp: datetime


@dataclass
class Position:
    """持倉資訊"""
    market_id: int
    size: float                   # 倉位大小 (正數=多, 負數=空)
    entry_price: float            # 平均進場價
    unrealized_pnl: float         # 未實現盈虧
    realized_pnl: float           # 已實現盈虧
    leverage: float               # 槓桿
    liquidation_price: Optional[float]  # 強平價格
    side: str = "LONG"            # 方向 (LONG/SHORT) - 默認為 LONG，初始化後會根據 size 更新

    def __post_init__(self):
        """初始化後處理"""
        if self.size < 0:
            self.side = "SHORT"
        else:
            self.side = "LONG"


@dataclass
class AccountInfo:
    """帳戶資訊"""
    balance: float                # 總餘額
    available_balance: float      # 可用餘額
    collateral: float             # 抵押品
    total_asset_value: float      # 總資產價值
    positions: list[Position]     # 持倉列表
    leverage: float               # 當前槓桿


class LighterClientAdapter:
    """
    Lighter DEX 客戶端適配器
    
    封裝已有的 LighterClient，提供統一的接口給量化交易機器人使用
    """
    
    def __init__(self):
        self.config = settings.trading
        self._client = None
        self._initialized = False
        
        # Dry run 模式的模擬數據
        self._dry_run_price: float = 50000.0
        self._dry_run_position: Optional[Position] = None
        self._dry_run_balance: float = 1000.0
    
    def set_simulated_price(self, price: float):
        """設置模擬價格 (dry run 模式使用)"""
        self._dry_run_price = price
    
    async def initialize(self):
        """初始化客戶端"""
        if self._initialized:
            return
        
        # Dry run 模式不需要初始化實際客戶端
        if settings.dry_run:
            self._initialized = True
            return
        
        try:
            # 動態導入用戶的 LighterClient
            from lighter_client import LighterClient
            
            # 從環境變數獲取配置
            api_private_key = os.getenv("LIGHTER_PRIVATE_KEY")
            api_key_index = int(os.getenv("LIGHTER_API_KEY_INDEX"))
            account_index = int(os.getenv("LIGHTER_ACCOUNT_INDEX"))
            base_url = os.getenv("LIGHTER_HOST")
            
            # 初始化實際的 LighterClient
            self._client = LighterClient(
                api_private_key=api_private_key,
                api_key_index=api_key_index,
                account_index=account_index,
                base_url=base_url
            )
            
            await self._client.initialize()
            self._initialized = True
            
        except ImportError:
            raise ImportError(
                "找不到 lighter_client 模組。請確保 lighter_client.py 在專案根目錄或 PYTHONPATH 中。"
            )
        except Exception as e:
            raise ConnectionError(f"初始化 Lighter 客戶端失敗: {e}")
    
    async def get_account_info(self) -> AccountInfo:
        """取得帳戶資訊"""
        await self.initialize()
        
        if settings.dry_run:
            positions = []
            if self._dry_run_position and self._dry_run_position.size != 0:
                # 計算未實現盈虧
                if self._dry_run_position.size > 0:  # Long
                    unrealized_pnl = (self._dry_run_price - self._dry_run_position.entry_price) * abs(self._dry_run_position.size)
                else:  # Short
                    unrealized_pnl = (self._dry_run_position.entry_price - self._dry_run_price) * abs(self._dry_run_position.size)
                
                self._dry_run_position = Position(
                    market_id=self._dry_run_position.market_id,
                    size=self._dry_run_position.size,
                    entry_price=self._dry_run_position.entry_price,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=self._dry_run_position.realized_pnl,
                    leverage=self._dry_run_position.leverage,
                    liquidation_price=self._dry_run_position.liquidation_price
                )
                positions.append(self._dry_run_position)
            
            return AccountInfo(
                balance=self._dry_run_balance,
                available_balance=self._dry_run_balance,
                collateral=self._dry_run_balance,
                total_asset_value=self._dry_run_balance + sum(p.unrealized_pnl for p in positions),
                positions=positions,
                leverage=1.0
            )
        
        try:
            # 使用實際客戶端獲取帳戶資訊
            result = await self._client.get_account_balance()
            
            if not result.get("success"):
                raise Exception(result.get("error", "獲取帳戶資訊失敗"))
            
            balance_info = result.get("balance_info", {})
            
            # 獲取持倉列表
            positions_result = await self._client.get_positions()
            positions = []
            
            if positions_result.get("success"):
                for pos in positions_result.get("positions", []):
                    position_amount = pos.get("position_amount", 0)
                    if abs(position_amount) > 1e-9:  # 只包含有效持倉
                        positions.append(Position(
                            market_id=pos.get("market_index", 0),
                            size=position_amount,
                            entry_price=pos.get("average_entry_price", 0),
                            unrealized_pnl=pos.get("unrealized_pnl", 0),
                            realized_pnl=pos.get("realized_pnl", 0),
                            leverage=1.0,  # Lighter 使用帳戶級別槓桿
                            liquidation_price=pos.get("liquidation_price")
                        ))
            
            total_collateral = balance_info.get("total_collateral", 0)
            available = balance_info.get("available_balance", total_collateral)
            
            return AccountInfo(
                balance=total_collateral,
                available_balance=available,
                collateral=total_collateral,
                total_asset_value=total_collateral + sum(p.unrealized_pnl for p in positions),
                positions=positions,
                leverage=1.0
            )
            
        except Exception as e:
            raise Exception(f"取得帳戶資訊失敗: {e}")
    
    async def get_position(self, market_id: int = None) -> Optional[Position]:
        """取得特定市場的持倉"""
        if market_id is None:
            market_id = self.config.market_id
        
        account = await self.get_account_info()
        
        for pos in account.positions:
            if pos.market_id == market_id:
                return pos
        
        return None
    
    async def create_market_order(
        self,
        signal_type: SignalType,
        amount: float,
        reduce_only: bool = False,
        market_id: int = None
    ) -> OrderResult:
        """
        創建市價單
        
        Args:
            signal_type: 訊號類型 (LONG/SHORT)
            amount: 基礎資產數量
            reduce_only: 是否僅減倉
            market_id: 市場 ID（可選）
            
        Returns:
            OrderResult
        """
        await self.initialize()
        
        if market_id is None:
            market_id = self.config.market_id
        
        is_ask = signal_type == SignalType.SHORT
        
        if settings.dry_run:
            if reduce_only:
                # 平倉：計算已實現盈虧並清除持倉
                if self._dry_run_position:
                    if self._dry_run_position.size > 0:  # 原本是 Long
                        realized_pnl = (self._dry_run_price - self._dry_run_position.entry_price) * abs(self._dry_run_position.size)
                    else:  # 原本是 Short
                        realized_pnl = (self._dry_run_position.entry_price - self._dry_run_price) * abs(self._dry_run_position.size)
                    
                    self._dry_run_balance += realized_pnl
                    self._dry_run_position = None
            else:
                # 開倉：創建新持倉
                self._dry_run_position = Position(
                    market_id=market_id,
                    size=amount if signal_type == SignalType.LONG else -amount,
                    entry_price=self._dry_run_price,
                    unrealized_pnl=0,
                    realized_pnl=0,
                    leverage=1.0,
                    liquidation_price=None
                )
            
            return OrderResult(
                success=True,
                order_id="dry_run_" + str(int(time.time() * 1000)),
                filled_price=self._dry_run_price,
                filled_amount=amount,
                message="模擬交易成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            # 使用實際客戶端創建市價單
            client_order_index = int(time.time() * 1000) % 1000000
            
            result = await self._client.create_market_order(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=amount,
                is_ask=is_ask,
                reduce_only=reduce_only
            )
            
            if result.get("success"):
                return OrderResult(
                    success=True,
                    order_id=result.get("tx_hash"),
                    filled_price=None,  # 市價單無法預知成交價
                    filled_amount=amount,
                    message="訂單提交成功",
                    timestamp=datetime.utcnow()
                )
            else:
                return OrderResult(
                    success=False,
                    order_id=None,
                    filled_price=None,
                    filled_amount=None,
                    message=result.get("error", "訂單失敗"),
                    timestamp=datetime.utcnow()
                )
                
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                filled_price=None,
                filled_amount=None,
                message=f"訂單失敗: {e}",
                timestamp=datetime.utcnow()
            )
    
    async def create_limit_order(
        self,
        signal_type: SignalType,
        amount: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = False,
        market_id: int = None
    ) -> OrderResult:
        """創建限價單"""
        await self.initialize()
        
        if market_id is None:
            market_id = self.config.market_id
        
        is_ask = signal_type == SignalType.SHORT
        
        if settings.dry_run:
            return OrderResult(
                success=True,
                order_id="dry_run_limit_" + str(int(time.time() * 1000)),
                filled_price=price,
                filled_amount=amount,
                message="模擬限價單成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            client_order_index = int(time.time() * 1000) % 1000000
            
            # 設置 time_in_force
            if post_only:
                time_in_force = self._client.TIME_IN_FORCE_POST_ONLY
            else:
                time_in_force = self._client.TIME_IN_FORCE_GTT
            
            result = await self._client.create_limit_order(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=amount,
                price=price,
                is_ask=is_ask,
                reduce_only=reduce_only,
                time_in_force=time_in_force
            )
            
            if result.get("success"):
                return OrderResult(
                    success=True,
                    order_id=result.get("tx_hash"),
                    filled_price=price,
                    filled_amount=amount,
                    message="限價單提交成功",
                    timestamp=datetime.utcnow()
                )
            else:
                return OrderResult(
                    success=False,
                    order_id=None,
                    filled_price=None,
                    filled_amount=None,
                    message=result.get("error", "限價單失敗"),
                    timestamp=datetime.utcnow()
                )
                
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                filled_price=None,
                filled_amount=None,
                message=f"限價單失敗: {e}",
                timestamp=datetime.utcnow()
            )
    
    async def create_stop_loss_order(
        self,
        signal_type: SignalType,
        amount: float,
        trigger_price: float,
        reduce_only: bool = True,
        market_id: int = None
    ) -> OrderResult:
        """創建止損單"""
        await self.initialize()
        
        if market_id is None:
            market_id = self.config.market_id
        
        # 止損單方向與持倉相反
        is_ask = signal_type == SignalType.LONG  # 做多的止損是賣出
        
        if settings.dry_run:
            return OrderResult(
                success=True,
                order_id="dry_run_sl_" + str(int(time.time() * 1000)),
                filled_price=trigger_price,
                filled_amount=amount,
                message="模擬止損單成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            client_order_index = int(time.time() * 1000) % 1000000
            
            # 使用 WebSocket 方式創建止損單（如果可用）
            if hasattr(self._client, 'ws_create_stop_loss_order'):
                result = await self._client.ws_create_stop_loss_order(
                    market_index=market_id,
                    client_order_index=client_order_index,
                    base_amount=amount,
                    trigger_price=trigger_price,
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )
            else:
                # 回退到普通限價單（以止損價格）
                result = await self._client.create_limit_order(
                    market_index=market_id,
                    client_order_index=client_order_index,
                    base_amount=amount,
                    price=trigger_price,
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )
            
            if result.get("success"):
                return OrderResult(
                    success=True,
                    order_id=result.get("tx_hash"),
                    filled_price=None,
                    filled_amount=amount,
                    message="止損單提交成功",
                    timestamp=datetime.utcnow()
                )
            else:
                return OrderResult(
                    success=False,
                    order_id=None,
                    filled_price=None,
                    filled_amount=None,
                    message=result.get("error", "止損單失敗"),
                    timestamp=datetime.utcnow()
                )
                
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                filled_price=None,
                filled_amount=None,
                message=f"止損單失敗: {e}",
                timestamp=datetime.utcnow()
            )
    
    async def create_take_profit_order(
        self,
        signal_type: SignalType,
        amount: float,
        trigger_price: float,
        reduce_only: bool = True,
        market_id: int = None
    ) -> OrderResult:
        """創建止盈單"""
        await self.initialize()
        
        if market_id is None:
            market_id = self.config.market_id
        
        # 止盈單方向與持倉相反
        is_ask = signal_type == SignalType.LONG  # 做多的止盈是賣出
        
        if settings.dry_run:
            return OrderResult(
                success=True,
                order_id="dry_run_tp_" + str(int(time.time() * 1000)),
                filled_price=trigger_price,
                filled_amount=amount,
                message="模擬止盈單成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            client_order_index = int(time.time() * 1000) % 1000000
            
            # 使用 WebSocket 方式創建止盈單（如果可用）
            if hasattr(self._client, 'ws_create_take_profit_order'):
                result = await self._client.ws_create_take_profit_order(
                    market_index=market_id,
                    client_order_index=client_order_index,
                    base_amount=amount,
                    trigger_price=trigger_price,
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )
            else:
                # 回退到普通限價單（以止盈價格）
                result = await self._client.create_limit_order(
                    market_index=market_id,
                    client_order_index=client_order_index,
                    base_amount=amount,
                    price=trigger_price,
                    is_ask=is_ask,
                    reduce_only=reduce_only
                )
            
            if result.get("success"):
                return OrderResult(
                    success=True,
                    order_id=result.get("tx_hash"),
                    filled_price=None,
                    filled_amount=amount,
                    message="止盈單提交成功",
                    timestamp=datetime.utcnow()
                )
            else:
                return OrderResult(
                    success=False,
                    order_id=None,
                    filled_price=None,
                    filled_amount=None,
                    message=result.get("error", "止盈單失敗"),
                    timestamp=datetime.utcnow()
                )
                
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                filled_price=None,
                filled_amount=None,
                message=f"止盈單失敗: {e}",
                timestamp=datetime.utcnow()
            )
    
    async def cancel_order(self, order_id: str, market_id: int = None) -> bool:
        """取消訂單"""
        await self.initialize()
        
        if settings.dry_run:
            return True
        
        try:
            if market_id is None:
                market_id = self.config.market_id
            
            result = await self._client.cancel_order_by_market_index(
                market_index=market_id,
                order_index=int(order_id)
            )
            return result.get("success", False)
        except Exception:
            return False
    
    async def cancel_all_orders(self, market_id: int = None) -> bool:
        """取消所有訂單"""
        await self.initialize()

        if settings.dry_run:
            return True

        try:
            # 如果指定了 market_id，只取消該市場的訂單
            if market_id is not None:
                if hasattr(self._client, 'cancel_orders_by_market'):
                    result = await self._client.cancel_orders_by_market(market_id)
                elif hasattr(self._client, 'cancel_all_orders_for_market'):
                    result = await self._client.cancel_all_orders_for_market(market_id)
                else:
                    # 如果 SDK 不支持按市場取消，獲取該市場的所有訂單並逐一取消
                    from utils import bot_logger as logger
                    logger.warning(f"SDK 不支持按市場取消訂單，將取消所有市場的訂單")
                    result = await self._client.cancel_all_orders()
            else:
                # 沒有指定 market_id，取消所有訂單
                result = await self._client.cancel_all_orders()
            return result.get("success", False)
        except Exception as e:
            from utils import bot_logger as logger
            logger.error(f"取消訂單失敗: {e}")
            return False
    
    async def update_leverage(self, leverage: float, market_id: int = None) -> bool:
        """更新槓桿"""
        await self.initialize()
        
        if settings.dry_run:
            return True
        
        try:
            # Lighter 的槓桿是帳戶級別的，不是市場級別的
            # 如果實際客戶端有 update_leverage 方法，則調用它
            if hasattr(self._client, 'update_leverage'):
                result = await self._client.update_leverage(
                    market_index=market_id or self.config.market_id,
                    leverage=leverage
                )
                return result.get("success", True)
            return True
        except Exception:
            return False
    
    async def close_position(self, market_id: int = None) -> OrderResult:
        """平倉"""
        if market_id is None:
            market_id = self.config.market_id
        
        position = await self.get_position(market_id)
        
        if position is None or position.size == 0:
            return OrderResult(
                success=True,
                order_id=None,
                filled_price=None,
                filled_amount=None,
                message="沒有持倉需要平倉",
                timestamp=datetime.utcnow()
            )
        
        # 根據持倉方向決定平倉方向
        signal_type = SignalType.SHORT if position.size > 0 else SignalType.LONG
        amount = abs(position.size)
        
        return await self.create_market_order(
            signal_type=signal_type,
            amount=amount,
            reduce_only=True,
            market_id=market_id
        )
    
    async def close_all_positions(self) -> dict:
        """平倉所有持倉"""
        await self.initialize()
        
        if settings.dry_run:
            if self._dry_run_position:
                self._dry_run_balance += self._dry_run_position.unrealized_pnl
                self._dry_run_position = None
            return {"success": True, "message": "模擬平倉成功"}
        
        try:
            if hasattr(self._client, 'ws_close_all_positions'):
                return await self._client.ws_close_all_positions()
            else:
                # 手動平倉每個持倉
                account = await self.get_account_info()
                results = []
                for pos in account.positions:
                    if abs(pos.size) > 1e-9:
                        result = await self.close_position(pos.market_id)
                        results.append(result)
                
                return {
                    "success": all(r.success for r in results),
                    "results": results
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def close(self):
        """關閉客戶端"""
        if self._client:
            await self._client.close()
        self._initialized = False


# 創建兼容的類別名稱
class LighterClient(LighterClientAdapter):
    """LighterClient 的別名，保持向後兼容"""
    pass


# 全域客戶端實例
lighter_client = LighterClient()
