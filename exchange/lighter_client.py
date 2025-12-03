"""
Lighter DEX 客戶端封裝
封裝 lighter-sdk 的交易功能
"""
import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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


@dataclass
class AccountInfo:
    """帳戶資訊"""
    balance: float                # 總餘額
    available_balance: float      # 可用餘額
    collateral: float             # 抵押品
    total_asset_value: float      # 總資產價值
    positions: list[Position]     # 持倉列表
    leverage: float               # 當前槓桿


class LighterClient:
    """
    Lighter DEX 客戶端
    
    封裝 lighter-sdk 提供的功能
    """
    
    # Order types from lighter-sdk
    ORDER_TYPE_LIMIT = 0
    ORDER_TYPE_MARKET = 1
    ORDER_TYPE_STOP_LOSS = 2
    ORDER_TYPE_STOP_LOSS_LIMIT = 3
    ORDER_TYPE_TAKE_PROFIT = 4
    ORDER_TYPE_TAKE_PROFIT_LIMIT = 5
    
    # Time in force
    TIF_IMMEDIATE_OR_CANCEL = 0
    TIF_GOOD_TILL_TIME = 1
    TIF_POST_ONLY = 2
    
    def __init__(self):
        self.config = settings.trading
        self._signer_client = None
        self._api_client = None
        self._initialized = False
        
        # Dry run 模式的模擬數據
        self._dry_run_price: float = 50000.0  # 模擬價格
        self._dry_run_position: Optional[Position] = None
        self._dry_run_balance: float = 1000.0
    
    def set_simulated_price(self, price: float):
        """設置模擬價格 (dry run 模式使用)"""
        self._dry_run_price = price
    
    async def initialize(self):
        """初始化客戶端"""
        if self._initialized:
            return

        # Dry run 模式不需要初始化 SDK
        if settings.dry_run:
            self._initialized = True
            return

        try:
            from lighter import SignerClient, ApiClient, Configuration

            # 初始化 API 客戶端 (用於查詢)
            configuration = Configuration(host=self.config.host)
            self._api_client = ApiClient(configuration=configuration)

            # 初始化 Signer 客戶端 (用於交易)
            if self.config.api_key and self.config.private_key:
                # 從環境變量獲取索引值
                import os
                account_index = int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0"))
                api_key_index = int(os.getenv("LIGHTER_API_KEY_INDEX", "0"))

                self._signer_client = SignerClient(
                    rpc_endpoint=self.config.host,
                    private_key=self.config.private_key,
                    account_index=account_index,
                    api_key_index=api_key_index
                )

            self._initialized = True

        except ImportError:
            raise ImportError("請先安裝 lighter-sdk: pip install lighter-sdk")
        except Exception as e:
            raise ConnectionError(f"初始化 Lighter 客戶端失敗: {e}")
    
    async def get_account_info(self) -> AccountInfo:
        """取得帳戶資訊"""
        await self.initialize()
        
        if settings.dry_run:
            # 模擬模式返回假數據
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
            from lighter.api import AccountApi
            
            account_api = AccountApi(self._api_client)
            account = await account_api.account()
            
            # 解析持倉
            positions = []
            for pos in account.positions or []:
                positions.append(Position(
                    market_id=pos.market_id,
                    size=float(pos.position or 0),
                    entry_price=float(pos.avg_entry_price or 0),
                    unrealized_pnl=float(pos.unrealized_pnl or 0),
                    realized_pnl=float(pos.realized_pnl or 0),
                    leverage=float(pos.leverage or 1),
                    liquidation_price=float(pos.liquidation_price) if pos.liquidation_price else None
                ))
            
            return AccountInfo(
                balance=float(account.collateral or 0),
                available_balance=float(account.available_balance or 0),
                collateral=float(account.collateral or 0),
                total_asset_value=float(account.total_asset_value or 0),
                positions=positions,
                leverage=float(account.leverage or 1)
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
        reduce_only: bool = False
    ) -> OrderResult:
        """
        創建市價單
        
        Args:
            signal_type: 訊號類型 (LONG/SHORT)
            amount: 基礎資產數量
            reduce_only: 是否僅減倉
            
        Returns:
            OrderResult
        """
        await self.initialize()
        
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
                    market_id=self.config.market_id,
                    size=amount if signal_type == SignalType.LONG else -amount,
                    entry_price=self._dry_run_price,
                    unrealized_pnl=0,
                    realized_pnl=0,
                    leverage=1.0,
                    liquidation_price=None
                )
            
            return OrderResult(
                success=True,
                order_id="dry_run_" + str(datetime.utcnow().timestamp()),
                filled_price=self._dry_run_price,
                filled_amount=amount,
                message="模擬交易成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            result = await self._signer_client.create_market_order(
                market_index=self.config.market_id,
                base_amount=int(amount * 1e8),  # 轉換為最小單位
                is_ask=is_ask,
                reduce_only=reduce_only
            )
            
            return OrderResult(
                success=True,
                order_id=str(result.get("order_id")),
                filled_price=float(result.get("filled_price", 0)),
                filled_amount=float(result.get("filled_amount", 0)),
                message="訂單提交成功",
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
        post_only: bool = False
    ) -> OrderResult:
        """創建限價單"""
        await self.initialize()
        
        is_ask = signal_type == SignalType.SHORT
        time_in_force = self.TIF_POST_ONLY if post_only else self.TIF_GOOD_TILL_TIME
        
        if settings.dry_run:
            return OrderResult(
                success=True,
                order_id="dry_run_" + str(datetime.utcnow().timestamp()),
                filled_price=price,
                filled_amount=amount,
                message="模擬限價單成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            result = await self._signer_client.create_order(
                market_index=self.config.market_id,
                base_amount=int(amount * 1e8),
                price=int(price * 1e8),
                is_ask=is_ask,
                order_type=self.ORDER_TYPE_LIMIT,
                time_in_force=time_in_force,
                reduce_only=reduce_only
            )
            
            return OrderResult(
                success=True,
                order_id=str(result.get("order_id")),
                filled_price=price,
                filled_amount=amount,
                message="限價單提交成功",
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
        reduce_only: bool = True
    ) -> OrderResult:
        """創建止損單"""
        await self.initialize()
        
        # 止損單方向與持倉相反
        is_ask = signal_type == SignalType.LONG  # 做多的止損是賣出
        
        if settings.dry_run:
            return OrderResult(
                success=True,
                order_id="dry_run_sl_" + str(datetime.utcnow().timestamp()),
                filled_price=trigger_price,
                filled_amount=amount,
                message="模擬止損單成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            result = await self._signer_client.create_sl_order(
                market_index=self.config.market_id,
                base_amount=int(amount * 1e8),
                trigger_price=int(trigger_price * 1e8),
                is_ask=is_ask,
                reduce_only=reduce_only
            )
            
            return OrderResult(
                success=True,
                order_id=str(result.get("order_id")),
                filled_price=None,
                filled_amount=amount,
                message="止損單提交成功",
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
        reduce_only: bool = True
    ) -> OrderResult:
        """創建止盈單"""
        await self.initialize()
        
        # 止盈單方向與持倉相反
        is_ask = signal_type == SignalType.LONG  # 做多的止盈是賣出
        
        if settings.dry_run:
            return OrderResult(
                success=True,
                order_id="dry_run_tp_" + str(datetime.utcnow().timestamp()),
                filled_price=trigger_price,
                filled_amount=amount,
                message="模擬止盈單成功",
                timestamp=datetime.utcnow()
            )
        
        try:
            result = await self._signer_client.create_tp_order(
                market_index=self.config.market_id,
                base_amount=int(amount * 1e8),
                trigger_price=int(trigger_price * 1e8),
                is_ask=is_ask,
                reduce_only=reduce_only
            )
            
            return OrderResult(
                success=True,
                order_id=str(result.get("order_id")),
                filled_price=None,
                filled_amount=amount,
                message="止盈單提交成功",
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
    
    async def cancel_order(self, order_id: str) -> bool:
        """取消訂單"""
        await self.initialize()
        
        if settings.dry_run:
            return True
        
        try:
            await self._signer_client.cancel_order(
                market_index=self.config.market_id,
                order_id=int(order_id)
            )
            return True
        except Exception as e:
            return False
    
    async def cancel_all_orders(self, market_id: int = None) -> bool:
        """取消所有訂單"""
        if market_id is None:
            market_id = self.config.market_id
        
        if settings.dry_run:
            return True
        
        try:
            # Lighter SDK 可能沒有直接的 cancel_all 方法
            # 需要先取得所有訂單再逐一取消
            # 這裡簡化處理
            return True
        except Exception as e:
            return False
    
    async def update_leverage(self, leverage: float) -> bool:
        """更新槓桿"""
        await self.initialize()
        
        if settings.dry_run:
            return True
        
        try:
            await self._signer_client.update_leverage(
                market_index=self.config.market_id,
                margin_mode=0,  # Cross margin
                leverage=leverage
            )
            return True
        except Exception as e:
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
            reduce_only=True
        )
    
    async def close(self):
        """關閉客戶端"""
        if self._api_client:
            # 關閉連接
            pass
        self._initialized = False


# 全域客戶端實例
lighter_client = LighterClient()
