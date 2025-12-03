"""
策略基類
定義所有策略的通用接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

from config import settings, SignalType, StrategyType, MarketRegime
from core.indicators import IndicatorValues
from core.market_regime import MarketState
from core.position_manager import StopLossTarget


@dataclass
class Signal:
    """交易訊號"""
    signal_type: SignalType       # 訊號類型 (LONG/SHORT/NONE)
    strategy: StrategyType        # 策略類型
    strength: float               # 訊號強度 (0-1)
    entry_price: float            # 建議進場價格
    stop_loss: float              # 止損價格
    take_profit: float            # 止盈價格
    confidence: float             # 信心度 (0-1)
    reason: str                   # 訊號原因
    timestamp: datetime           # 訊號時間


class BaseStrategy(ABC):
    """策略基類"""
    
    def __init__(self, strategy_type: StrategyType):
        self.strategy_type = strategy_type
        self.config = settings
        self.last_signal: Optional[Signal] = None
        self.signal_count = 0
    
    @abstractmethod
    def check_entry(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> Optional[Signal]:
        """
        檢查進場條件
        
        Args:
            indicators: 指標值
            market_state: 市場狀態
            
        Returns:
            Signal 如果有訊號，None 如果沒有
        """
        pass
    
    @abstractmethod
    def check_exit(
        self,
        indicators: IndicatorValues,
        entry_price: float,
        entry_signal: Signal,
        current_pnl_percent: float
    ) -> tuple[bool, str]:
        """
        檢查出場條件
        
        Args:
            indicators: 指標值
            entry_price: 進場價格
            entry_signal: 進場時的訊號
            current_pnl_percent: 當前盈虧百分比
            
        Returns:
            (是否出場, 原因)
        """
        pass
    
    @abstractmethod
    def calculate_stops(
        self,
        indicators: IndicatorValues,
        signal_type: SignalType,
        entry_price: float
    ) -> StopLossTarget:
        """
        計算止損止盈
        
        Args:
            indicators: 指標值
            signal_type: 訊號類型
            entry_price: 進場價格
            
        Returns:
            StopLossTarget
        """
        pass
    
    def is_applicable(self, market_state: MarketState) -> bool:
        """
        檢查策略是否適用於當前市場狀態
        
        Args:
            market_state: 市場狀態
            
        Returns:
            是否適用
        """
        # 子類可以覆寫此方法
        return True
    
    def create_signal(
        self,
        signal_type: SignalType,
        entry_price: float,
        stops: StopLossTarget,
        strength: float,
        confidence: float,
        reason: str
    ) -> Signal:
        """創建訊號"""
        signal = Signal(
            signal_type=signal_type,
            strategy=self.strategy_type,
            strength=strength,
            entry_price=entry_price,
            stop_loss=stops.stop_loss,
            take_profit=stops.take_profit,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.utcnow()
        )
        
        self.last_signal = signal
        self.signal_count += 1
        
        return signal
    
    def reset(self):
        """重置策略狀態"""
        self.last_signal = None
        self.signal_count = 0
