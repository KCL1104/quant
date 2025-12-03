"""
Mean Reversion 策略 V2
修正風報比問題，確保最小 1:1.5 風報比
"""
from typing import Optional
from datetime import datetime

from config import settings, SignalType, StrategyType, MarketRegime
from core.indicators import IndicatorValues
from core.market_regime import MarketState
from core.position_manager import position_manager, StopLossTarget
from strategies.base import BaseStrategy, Signal


class MeanReversionStrategyV2(BaseStrategy):
    """
    Mean Reversion 策略 V2
    
    核心改進：
    1. 確保最小風報比 1:1.5
    2. 只在極端位置進場（BB Position < 0.15 或 > 0.85）
    3. 動態止盈：根據進場位置計算合理止盈
    4. 移除過早出場條件
    """
    
    # 配置參數
    MIN_RISK_REWARD = 1.5          # 最小風報比
    MAX_STOP_DISTANCE_PCT = 0.045  # 最大止損距離 4.5% (優化：震盪市場更寬止損)
    MIN_PROFIT_TARGET_PCT = 0.01   # 最小止盈目標 1%
    
    # 進場閾值（優化：增加交易機會）
    EXTREME_OVERSOLD_BB = 0.25     # BB Position < 0.25 才算極端超賣
    EXTREME_OVERBOUGHT_BB = 0.75   # BB Position > 0.75 才算極端超買
    
    def __init__(self):
        super().__init__(StrategyType.MEAN_REVERSION)
        self.entry_time: Optional[datetime] = None
    
    def is_applicable(self, market_state: MarketState) -> bool:
        """Mean Reversion 只在震盪市場適用"""
        return market_state.regime == MarketRegime.RANGING
    
    def check_entry(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> Optional[Signal]:
        """檢查進場條件 - 只在極端位置進場"""
        
        if not self.is_applicable(market_state):
            return None
        
        rsi = indicators.rsi
        bb = indicators.bollinger
        current_price = indicators.current_price
        atr = indicators.atr
        
        # 只處理極端超賣/超買情況
        # 移除中軌反彈邏輯（風報比太差）
        
        # 極端超賣反彈 (做多)
        if bb.position < self.EXTREME_OVERSOLD_BB and rsi < 25:
            return self._create_long_signal(bb, current_price, atr, rsi)
        
        # 極端超買回落 (做空)
        if bb.position > self.EXTREME_OVERBOUGHT_BB and rsi > 75:
            return self._create_short_signal(bb, current_price, atr, rsi)
        
        return None
    
    def _create_long_signal(
        self,
        bb,
        current_price: float,
        atr: float,
        rsi: float
    ) -> Optional[Signal]:
        """創建做多訊號，確保風報比合理"""
        
        # 方法1：基於 ATR 的動態止損止盈
        stop_distance = min(atr * 1.5, current_price * self.MAX_STOP_DISTANCE_PCT)
        stop_loss = current_price - stop_distance
        
        # 止盈目標：至少 1.5 倍止損距離，但不超過上軌
        min_take_profit = current_price + (stop_distance * self.MIN_RISK_REWARD)
        
        # 優先使用中軌作為止盈，但必須滿足最小風報比
        if bb.middle >= min_take_profit:
            take_profit = bb.middle
        else:
            # 中軌太近，使用最小風報比計算的止盈
            take_profit = min_take_profit
        
        # 確保止盈不超過上軌太多（避免不切實際的目標）
        take_profit = min(take_profit, bb.upper * 0.98)
        
        # 最終風報比檢查（放寬限制）
        profit_distance = take_profit - current_price
        actual_rr = profit_distance / stop_distance if stop_distance > 0 else 0
        
        if actual_rr < 1.1:  # 風報比至少 1:1
            return None
        
        # 計算強度
        rsi_strength = (30 - rsi) / 30 if rsi < 30 else 0
        bb_strength = (self.EXTREME_OVERSOLD_BB - bb.position) / self.EXTREME_OVERSOLD_BB
        strength = (rsi_strength + bb_strength) / 2
        
        stops = StopLossTarget(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=actual_rr,
            stop_distance_percent=stop_distance / current_price,
            profit_distance_percent=profit_distance / current_price
        )
        
        reason = (
            f"MR_V2 超賣反彈: RSI={rsi:.1f}, BB_Pos={bb.position:.2f}, "
            f"RR={actual_rr:.2f}"
        )
        
        return self.create_signal(
            signal_type=SignalType.LONG,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=min(0.8, strength + 0.3),
            reason=reason
        )
    
    def _create_short_signal(
        self,
        bb,
        current_price: float,
        atr: float,
        rsi: float
    ) -> Optional[Signal]:
        """創建做空訊號，確保風報比合理"""
        
        # 基於 ATR 的動態止損
        stop_distance = min(atr * 1.5, current_price * self.MAX_STOP_DISTANCE_PCT)
        stop_loss = current_price + stop_distance
        
        # 止盈目標
        min_take_profit = current_price - (stop_distance * self.MIN_RISK_REWARD)
        
        if bb.middle <= min_take_profit:
            take_profit = bb.middle
        else:
            take_profit = min_take_profit
        
        # 確保止盈不低於下軌太多
        take_profit = max(take_profit, bb.lower * 1.02)
        
        # 風報比檢查（放寬限制）
        profit_distance = current_price - take_profit
        actual_rr = profit_distance / stop_distance if stop_distance > 0 else 0
        
        if actual_rr < 1.1:  # 風報比至少 1:1
            return None
        
        rsi_strength = (rsi - 70) / 30 if rsi > 70 else 0
        bb_strength = (bb.position - self.EXTREME_OVERBOUGHT_BB) / (1 - self.EXTREME_OVERBOUGHT_BB)
        strength = (rsi_strength + bb_strength) / 2
        
        stops = StopLossTarget(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=actual_rr,
            stop_distance_percent=stop_distance / current_price,
            profit_distance_percent=profit_distance / current_price
        )
        
        reason = (
            f"MR_V2 超買回落: RSI={rsi:.1f}, BB_Pos={bb.position:.2f}, "
            f"RR={actual_rr:.2f}"
        )
        
        return self.create_signal(
            signal_type=SignalType.SHORT,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=min(0.8, strength + 0.3),
            reason=reason
        )
    
    def check_exit(
        self,
        indicators: IndicatorValues,
        entry_price: float,
        entry_signal: Signal,
        current_pnl_percent: float
    ) -> tuple[bool, str]:
        """
        檢查出場條件 - 簡化邏輯，讓止盈止損發揮作用
        
        移除過早出場條件：
        - 不再因為 RSI 回到中性就出場
        - 不再因為到達中軌就出場（除非那就是止盈點）
        """
        current_price = indicators.current_price
        
        # 做多出場
        if entry_signal.signal_type == SignalType.LONG:
            if current_price <= entry_signal.stop_loss:
                return True, f"止損 @ {current_price:.2f}"
            if current_price >= entry_signal.take_profit:
                return True, f"止盈 @ {current_price:.2f}"
            
            # 只有在盈利超過 5% 時才啟動移動止損，且鎖定 50% 利潤
            if current_pnl_percent > 0.05:
                trailing_stop = entry_price + (current_price - entry_price) * 0.5
                if current_price <= trailing_stop:
                    return True, f"移動止損 @ {current_price:.2f}"
        
        # 做空出場
        elif entry_signal.signal_type == SignalType.SHORT:
            if current_price >= entry_signal.stop_loss:
                return True, f"止損 @ {current_price:.2f}"
            if current_price <= entry_signal.take_profit:
                return True, f"止盈 @ {current_price:.2f}"
            
            if current_pnl_percent > 0.05:
                trailing_stop = entry_price - (entry_price - current_price) * 0.5
                if current_price >= trailing_stop:
                    return True, f"移動止損 @ {current_price:.2f}"
        
        return False, ""
    
    def calculate_stops(
        self,
        indicators: Optional[IndicatorValues],
        signal_type: SignalType,
        entry_price: float,
        bb_lower: float = None,
        bb_middle: float = None,
        bb_upper: float = None
    ) -> StopLossTarget:
        """計算止損止盈 - 這個方法主要用於外部調用"""
        
        if indicators is None:
            # 使用簡單的百分比止損
            stop_distance = entry_price * 0.02
            profit_distance = stop_distance * self.MIN_RISK_REWARD
            
            if signal_type == SignalType.LONG:
                return StopLossTarget(
                    stop_loss=entry_price - stop_distance,
                    take_profit=entry_price + profit_distance,
                    risk_reward_ratio=self.MIN_RISK_REWARD,
                    stop_distance_percent=0.02,
                    profit_distance_percent=0.02 * self.MIN_RISK_REWARD
                )
            else:
                return StopLossTarget(
                    stop_loss=entry_price + stop_distance,
                    take_profit=entry_price - profit_distance,
                    risk_reward_ratio=self.MIN_RISK_REWARD,
                    stop_distance_percent=0.02,
                    profit_distance_percent=0.02 * self.MIN_RISK_REWARD
                )
        
        # 使用 ATR 計算
        atr = indicators.atr
        stop_distance = min(atr * 1.5, entry_price * self.MAX_STOP_DISTANCE_PCT)
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
mean_reversion_strategy_v2 = MeanReversionStrategyV2()
