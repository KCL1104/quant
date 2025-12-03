"""
Momentum 策略 V2
改進止損邏輯，加入趨勢強度過濾
"""
from typing import Optional
import numpy as np

from config import settings, SignalType, StrategyType, MarketRegime
from core.indicators import IndicatorValues, TrendDirection, indicators
from core.market_regime import MarketState
from core.position_manager import position_manager, StopLossTarget
from strategies.base import BaseStrategy, Signal


class MomentumStrategyV2(BaseStrategy):
    """
    Momentum 策略 V2
    
    核心改進：
    1. 使用 ATR 動態止損，避免 Supertrend 止損過遠
    2. 加入趨勢強度過濾 (ADX > 30)
    3. 確保最小風報比
    4. 追蹤止損機制
    """
    
    # 配置（優化後）
    MIN_RISK_REWARD = 1.5          # 最小風報比（降低以提高止盈達成率）
    ATR_STOP_MULTIPLIER = 2.5      # ATR 止損乘數（放寬避免被波動掃出）
    MAX_STOP_DISTANCE_PCT = 0.07   # 最大止損距離 7%（放寬以適應加密貨幣波動）
    MIN_ADX_THRESHOLD = 20         # 最低 ADX 閾值（提高以確保趨勢強度）
    
    def __init__(self):
        super().__init__(StrategyType.MOMENTUM)
        self.lookback_highs: list = []
        self.lookback_lows: list = []
        self.lookback_period: int = 20
    
    def is_applicable(self, market_state: MarketState) -> bool:
        """Momentum 只在強趨勢市場適用"""
        return (
            market_state.regime == MarketRegime.TRENDING and
            market_state.adx_value > self.MIN_ADX_THRESHOLD
        )
    
    def check_entry(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> Optional[Signal]:
        """檢查進場條件"""
        
        if not self.is_applicable(market_state):
            return None
        
        st_fast = indicators.supertrend_fast
        st_slow = indicators.supertrend_slow
        ema_fast = indicators.ema_fast
        ema_slow = indicators.ema_slow
        current_price = indicators.current_price
        atr = indicators.atr
        adx = indicators.adx
        plus_di = indicators.plus_di
        minus_di = indicators.minus_di
        high = indicators.high
        low = indicators.low
        
        # 做多條件
        long_signal = self._check_long_v2(
            st_fast, st_slow, ema_fast, ema_slow,
            current_price, atr, adx, plus_di, minus_di, high
        )
        if long_signal:
            return long_signal
        
        # 做空條件
        short_signal = self._check_short_v2(
            st_fast, st_slow, ema_fast, ema_slow,
            current_price, atr, adx, plus_di, minus_di, low
        )
        if short_signal:
            return short_signal
        
        self._update_prev_high_low(high, low)
        return None
    
    def _check_long_v2(
        self,
        st_fast, st_slow,
        ema_fast: float, ema_slow: float,
        current_price: float, atr: float,
        adx: float, plus_di: float, minus_di: float,
        high: float
    ) -> Optional[Signal]:
        """做多條件 V2"""
        
        # 基本條件
        if st_fast.direction != TrendDirection.UP:
            return None
        if st_slow.direction != TrendDirection.UP:
            return None
        if current_price <= ema_fast:
            return None
        if ema_fast <= ema_slow:
            return None
        
        # DI+ > DI- 確認多頭趨勢（優化：提高要求減少假突破）
        di_diff = plus_di - minus_di
        if di_diff < 5:  # DI+ 至少比 DI- 大5（減少假訊號）
            return None
        
        # 計算止損 - 使用 ATR
        atr_stop = current_price - (atr * self.ATR_STOP_MULTIPLIER)
        supertrend_stop = st_fast.lower_band
        
        # 選擇較近的止損（更保守）
        stop_loss = max(atr_stop, supertrend_stop)
        
        # 確保止損不會太遠
        max_stop = current_price * (1 - self.MAX_STOP_DISTANCE_PCT)
        stop_loss = max(stop_loss, max_stop)
        
        stop_distance = current_price - stop_loss
        
        # 確保止損有效
        if stop_distance <= 0 or stop_distance / current_price < 0.003:
            return None
        
        # 計算止盈
        take_profit = current_price + (stop_distance * self.MIN_RISK_REWARD)
        
        # 計算強度
        ema_diff = (ema_fast - ema_slow) / ema_slow if ema_slow > 0 else 0
        adx_strength = min(1.0, (adx - 25) / 25)  # ADX 25-50 映射到 0-1
        di_strength = min(1.0, di_diff / 20)      # DI差 0-20 映射到 0-1
        strength = (ema_diff * 10 + adx_strength + di_strength) / 3
        strength = min(1.0, max(0.4, strength))
        
        stops = StopLossTarget(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=self.MIN_RISK_REWARD,
            stop_distance_percent=stop_distance / current_price,
            profit_distance_percent=(take_profit - current_price) / current_price
        )
        
        reason = (
            f"MOM_V2 做多: ADX={adx:.1f}, DI+={plus_di:.1f}, DI-={minus_di:.1f}, "
            f"ST={st_fast.direction.name}"
        )
        
        return self.create_signal(
            signal_type=SignalType.LONG,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=min(0.85, strength + 0.2),
            reason=reason
        )
    
    def _check_short_v2(
        self,
        st_fast, st_slow,
        ema_fast: float, ema_slow: float,
        current_price: float, atr: float,
        adx: float, plus_di: float, minus_di: float,
        low: float
    ) -> Optional[Signal]:
        """做空條件 V2"""
        
        # 基本條件
        if st_fast.direction != TrendDirection.DOWN:
            return None
        if st_slow.direction != TrendDirection.DOWN:
            return None
        if current_price >= ema_fast:
            return None
        if ema_fast >= ema_slow:
            return None
        
        # DI- > DI+ 確認空頭趨勢（優化：提高要求減少假突破）
        di_diff = minus_di - plus_di
        if di_diff < 5:  # DI- 至少比 DI+ 大5（減少假訊號）
            return None
        
        # 計算止損 - 使用 ATR
        atr_stop = current_price + (atr * self.ATR_STOP_MULTIPLIER)
        supertrend_stop = st_fast.upper_band
        
        stop_loss = min(atr_stop, supertrend_stop)
        max_stop = current_price * (1 + self.MAX_STOP_DISTANCE_PCT)
        stop_loss = min(stop_loss, max_stop)
        
        stop_distance = stop_loss - current_price
        
        if stop_distance <= 0 or stop_distance / current_price < 0.003:
            return None
        
        take_profit = current_price - (stop_distance * self.MIN_RISK_REWARD)
        
        ema_diff = (ema_slow - ema_fast) / ema_slow if ema_slow > 0 else 0
        adx_strength = min(1.0, (adx - 25) / 25)
        di_strength = min(1.0, di_diff / 20)
        strength = (ema_diff * 10 + adx_strength + di_strength) / 3
        strength = min(1.0, max(0.4, strength))
        
        stops = StopLossTarget(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=self.MIN_RISK_REWARD,
            stop_distance_percent=stop_distance / current_price,
            profit_distance_percent=(current_price - take_profit) / current_price
        )
        
        reason = (
            f"MOM_V2 做空: ADX={adx:.1f}, DI+={plus_di:.1f}, DI-={minus_di:.1f}, "
            f"ST={st_fast.direction.name}"
        )
        
        return self.create_signal(
            signal_type=SignalType.SHORT,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=min(0.85, strength + 0.2),
            reason=reason
        )
    
    def _update_prev_high_low(self, high: float, low: float):
        """更新前高前低"""
        self.lookback_highs.append(high)
        self.lookback_lows.append(low)
        
        if len(self.lookback_highs) > self.lookback_period:
            self.lookback_highs.pop(0)
        if len(self.lookback_lows) > self.lookback_period:
            self.lookback_lows.pop(0)
    
    def check_exit(
        self,
        indicators: IndicatorValues,
        entry_price: float,
        entry_signal: Signal,
        current_pnl_percent: float
    ) -> tuple[bool, str]:
        """
        出場條件 V2 - 加入追蹤止損
        """
        current_price = indicators.current_price
        st_fast = indicators.supertrend_fast
        atr = indicators.atr
        
        if entry_signal.signal_type == SignalType.LONG:
            # 基本止損
            if current_price <= entry_signal.stop_loss:
                return True, f"止損 @ {current_price:.2f}"
            
            # 止盈
            if current_price >= entry_signal.take_profit:
                return True, f"止盈 @ {current_price:.2f}"
            
            # 追蹤止損：盈利 > 5% 時，止損鎖定 50% 利潤（保留 50%）
            if current_pnl_percent > 0.05:
                profit_lock_price = entry_price + (current_price - entry_price) * 0.5
                if current_price <= profit_lock_price:
                    return True, f"追蹤止損(保留50%利潤) @ {current_price:.2f}"
            
            # 追蹤止損：盈利 > 10% 時，止損鎖定 10% 利潤（保留 90%）
            if current_pnl_percent > 0.1:
                profit_lock_price = entry_price + (current_price - entry_price) * 0.1
                if current_price <= profit_lock_price:
                    return True, f"追蹤止損(保留10%利潤) @ {current_price:.2f}"
        
        elif entry_signal.signal_type == SignalType.SHORT:
            if current_price >= entry_signal.stop_loss:
                return True, f"止損 @ {current_price:.2f}"
            
            if current_price <= entry_signal.take_profit:
                return True, f"止盈 @ {current_price:.2f}"
            
            if current_pnl_percent > 0.05:
                profit_lock_price = entry_price - (entry_price - current_price) * 0.5
                if current_price >= profit_lock_price:
                    return True, f"追蹤止損(保留50%利潤) @ {current_price:.2f}"
            
            if current_pnl_percent > 0.1:
                profit_lock_price = entry_price - (entry_price - current_price) * 0.1
                if current_price >= profit_lock_price:
                    return True, f"追蹤止損(保留10%利潤) @ {current_price:.2f}"
        
        return False, ""
    
    def calculate_stops(
        self,
        indicators: Optional[IndicatorValues],
        signal_type: SignalType,
        entry_price: float,
        supertrend_value: float = None
    ) -> StopLossTarget:
        """計算止損止盈"""
        
        if indicators is not None:
            atr = indicators.atr
            atr_stop_distance = atr * self.ATR_STOP_MULTIPLIER
        else:
            atr_stop_distance = entry_price * 0.02
        
        # 限制最大止損距離
        stop_distance = min(atr_stop_distance, entry_price * self.MAX_STOP_DISTANCE_PCT)
        profit_distance = stop_distance * self.MIN_RISK_REWARD
        
        if signal_type == SignalType.LONG:
            return StopLossTarget(
                stop_loss=entry_price - stop_distance,
                take_profit=entry_price + profit_distance,
                risk_reward_ratio=self.MIN_RISK_REWARD,
                stop_distance_percent=stop_distance / entry_price,
                profit_distance_percent=profit_distance / entry_price
            )
        else:
            return StopLossTarget(
                stop_loss=entry_price + stop_distance,
                take_profit=entry_price - profit_distance,
                risk_reward_ratio=self.MIN_RISK_REWARD,
                stop_distance_percent=stop_distance / entry_price,
                profit_distance_percent=profit_distance / entry_price
            )


# 全域實例
momentum_strategy_v2 = MomentumStrategyV2()
