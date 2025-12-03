"""
Momentum 策略
適用於趨勢市場，使用雙 Supertrend + EMA 確認
"""
from typing import Optional
import numpy as np

from config import settings, SignalType, StrategyType, MarketRegime
from core.indicators import IndicatorValues, TrendDirection, indicators
from core.market_regime import MarketState
from core.position_manager import position_manager, StopLossTarget
from strategies.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """
    Momentum 策略
    
    進場條件 (做多):
    1. Fast Supertrend = UP
    2. Slow Supertrend = UP
    3. 價格 > EMA20
    4. EMA20 > EMA50
    5. 價格突破前高
    
    進場條件 (做空):
    1. Fast Supertrend = DOWN
    2. Slow Supertrend = DOWN
    3. 價格 < EMA20
    4. EMA20 < EMA50
    5. 價格跌破前低
    """
    
    def __init__(self):
        super().__init__(StrategyType.MOMENTUM)
        self.prev_high: Optional[float] = None
        self.prev_low: Optional[float] = None
        self.lookback_highs: list = []  # 追蹤最近的高點
        self.lookback_lows: list = []   # 追蹤最近的低點
        self.lookback_period: int = 20  # 回看週期
    
    def is_applicable(self, market_state: MarketState) -> bool:
        """Momentum 只在趨勢市場適用"""
        return market_state.regime == MarketRegime.TRENDING
    
    def check_entry(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> Optional[Signal]:
        """檢查進場條件"""
        
        # 檢查市場狀態
        if not self.is_applicable(market_state):
            return None
        
        # 取得指標值
        st_fast = indicators.supertrend_fast
        st_slow = indicators.supertrend_slow
        ema_fast = indicators.ema_fast
        ema_slow = indicators.ema_slow
        current_price = indicators.current_price
        high = indicators.high
        low = indicators.low
        
        # 檢查做多條件
        long_signal = self._check_long_conditions(
            st_fast, st_slow, ema_fast, ema_slow, current_price, high
        )
        
        if long_signal:
            return long_signal
        
        # 檢查做空條件
        short_signal = self._check_short_conditions(
            st_fast, st_slow, ema_fast, ema_slow, current_price, low
        )
        
        if short_signal:
            return short_signal
        
        # 更新前高前低
        self._update_prev_high_low(high, low)
        
        return None
    
    def _check_long_conditions(
        self,
        st_fast,
        st_slow,
        ema_fast: float,
        ema_slow: float,
        current_price: float,
        high: float
    ) -> Optional[Signal]:
        """檢查做多條件"""
        
        # 條件 1: Fast Supertrend = UP
        if st_fast.direction != TrendDirection.UP:
            return None
        
        # 條件 2: Slow Supertrend = UP
        if st_slow.direction != TrendDirection.UP:
            return None
        
        # 條件 3: 價格 > EMA20
        if current_price <= ema_fast:
            return None
        
        # 條件 4: EMA20 > EMA50 (黃金交叉狀態)
        if ema_fast <= ema_slow:
            return None
        
        # 條件 5: 價格突破前高 (如果有前高)
        if self.prev_high is not None and current_price <= self.prev_high:
            return None
        
        # 計算動能強度
        # 這裡簡化處理，實際應該傳入 close 數組
        ema_diff = (ema_fast - ema_slow) / ema_slow if ema_slow != 0 else 0
        price_above_ema = (current_price - ema_fast) / ema_fast if ema_fast != 0 else 0
        strength = min(1.0, (ema_diff + price_above_ema) * 10)
        
        # 檢查最小強度
        if strength < self.config.momentum.min_strength:
            return None
        
        # 計算止損止盈
        stops = self.calculate_stops(
            None,  # indicators 不需要在這裡
            SignalType.LONG,
            current_price,
            st_fast.lower_band  # 使用 Supertrend 下軌作為止損
        )
        
        # 計算信心度
        confidence = min(1.0, strength * 0.7 + 0.3)
        
        reason = (
            f"Momentum 做多: ST快={st_fast.direction.name}, ST慢={st_slow.direction.name}, "
            f"EMA黃金交叉, 價格突破前高"
        )
        
        return self.create_signal(
            signal_type=SignalType.LONG,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=confidence,
            reason=reason
        )
    
    def _check_short_conditions(
        self,
        st_fast,
        st_slow,
        ema_fast: float,
        ema_slow: float,
        current_price: float,
        low: float
    ) -> Optional[Signal]:
        """檢查做空條件"""
        
        # 條件 1: Fast Supertrend = DOWN
        if st_fast.direction != TrendDirection.DOWN:
            return None
        
        # 條件 2: Slow Supertrend = DOWN
        if st_slow.direction != TrendDirection.DOWN:
            return None
        
        # 條件 3: 價格 < EMA20
        if current_price >= ema_fast:
            return None
        
        # 條件 4: EMA20 < EMA50 (死亡交叉狀態)
        if ema_fast >= ema_slow:
            return None
        
        # 條件 5: 價格跌破前低 (如果有前低)
        if self.prev_low is not None and current_price >= self.prev_low:
            return None
        
        # 計算動能強度
        ema_diff = (ema_slow - ema_fast) / ema_slow if ema_slow != 0 else 0
        price_below_ema = (ema_fast - current_price) / ema_fast if ema_fast != 0 else 0
        strength = min(1.0, (ema_diff + price_below_ema) * 10)
        
        # 檢查最小強度
        if strength < self.config.momentum.min_strength:
            return None
        
        # 計算止損止盈
        stops = self.calculate_stops(
            None,
            SignalType.SHORT,
            current_price,
            st_fast.upper_band  # 使用 Supertrend 上軌作為止損
        )
        
        # 計算信心度
        confidence = min(1.0, strength * 0.7 + 0.3)
        
        reason = (
            f"Momentum 做空: ST快={st_fast.direction.name}, ST慢={st_slow.direction.name}, "
            f"EMA死亡交叉, 價格跌破前低"
        )
        
        return self.create_signal(
            signal_type=SignalType.SHORT,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=confidence,
            reason=reason
        )
    
    def _update_prev_high_low(self, high: float, low: float):
        """
        更新前高前低 (使用滑動窗口)
        
        使用最近 N 根 K 線的最高/最低點，
        而不是累積所有歷史數據
        """
        # 添加新數據
        self.lookback_highs.append(high)
        self.lookback_lows.append(low)
        
        # 保持窗口大小
        if len(self.lookback_highs) > self.lookback_period:
            self.lookback_highs.pop(0)
        if len(self.lookback_lows) > self.lookback_period:
            self.lookback_lows.pop(0)
        
        # 計算前高前低 (排除最新一根)
        if len(self.lookback_highs) > 1:
            self.prev_high = max(self.lookback_highs[:-1])
            self.prev_low = min(self.lookback_lows[:-1])
        else:
            self.prev_high = None
            self.prev_low = None
    
    def check_exit(
        self,
        indicators: IndicatorValues,
        entry_price: float,
        entry_signal: Signal,
        current_pnl_percent: float
    ) -> tuple[bool, str]:
        """
        檢查出場條件
        
        出場條件:
        1. 觸發止損
        2. 觸發止盈
        3. Supertrend 翻轉
        """
        current_price = indicators.current_price
        st_fast = indicators.supertrend_fast
        
        # 做多出場
        if entry_signal.signal_type == SignalType.LONG:
            # 止損
            if current_price <= entry_signal.stop_loss:
                return True, f"止損觸發 @ {current_price:.2f}"
            
            # 止盈
            if current_price >= entry_signal.take_profit:
                return True, f"止盈觸發 @ {current_price:.2f}"
            
            # Supertrend 翻轉
            if st_fast.direction == TrendDirection.DOWN:
                return True, "Supertrend 翻轉為下降"
        
        # 做空出場
        elif entry_signal.signal_type == SignalType.SHORT:
            # 止損
            if current_price >= entry_signal.stop_loss:
                return True, f"止損觸發 @ {current_price:.2f}"
            
            # 止盈
            if current_price <= entry_signal.take_profit:
                return True, f"止盈觸發 @ {current_price:.2f}"
            
            # Supertrend 翻轉
            if st_fast.direction == TrendDirection.UP:
                return True, "Supertrend 翻轉為上升"
        
        return False, ""
    
    def calculate_stops(
        self,
        indicators: Optional[IndicatorValues],
        signal_type: SignalType,
        entry_price: float,
        supertrend_value: float = None
    ) -> StopLossTarget:
        """計算止損止盈"""
        
        # 如果沒有提供 supertrend_value，從 indicators 獲取
        if supertrend_value is None and indicators is not None:
            if signal_type == SignalType.LONG:
                supertrend_value = indicators.supertrend_fast.lower_band
            else:
                supertrend_value = indicators.supertrend_fast.upper_band
        
        return position_manager.calculate_momentum_stops(
            entry_price=entry_price,
            supertrend_value=supertrend_value,
            signal_type=signal_type
        )
    
    def reset(self):
        """重置策略狀態"""
        super().reset()
        self.prev_high = None
        self.prev_low = None
        self.lookback_highs = []
        self.lookback_lows = []


# 全域 Momentum 策略實例
momentum_strategy = MomentumStrategy()
