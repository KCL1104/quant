"""
Mean Reversion 策略
適用於震盪市場，使用 RSI + Bollinger Bands
"""
from typing import Optional
from datetime import datetime

from config import settings, SignalType, StrategyType, MarketRegime
from core.indicators import IndicatorValues
from core.market_regime import MarketState
from core.position_manager import position_manager, StopLossTarget
from strategies.base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion 策略
    
    進場條件 (做多 - 超賣反彈):
    1. RSI < 30 (超賣)
    2. 價格 < BB下軌 * 0.99
    3. BB Position < 0.1
    
    進場條件 (做空 - 超買回落):
    1. RSI > 70 (超買)
    2. 價格 > BB上軌 * 1.01
    3. BB Position > 0.9
    
    中軌反彈:
    - 價格接近中軌 (±10% 帶寬)
    - RSI 和 BB Position 配合
    """
    
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
        """檢查進場條件"""
        
        # 檢查市場狀態
        if not self.is_applicable(market_state):
            return None
        
        # 取得指標值
        rsi = indicators.rsi
        bb = indicators.bollinger
        current_price = indicators.current_price
        
        # 檢查超賣反彈 (做多)
        oversold_signal = self._check_oversold_bounce(rsi, bb, current_price)
        if oversold_signal:
            return oversold_signal
        
        # 檢查超買回落 (做空)
        overbought_signal = self._check_overbought_pullback(rsi, bb, current_price)
        if overbought_signal:
            return overbought_signal
        
        # 檢查中軌反彈
        mid_band_signal = self._check_mid_band_bounce(rsi, bb, current_price)
        if mid_band_signal:
            return mid_band_signal
        
        return None
    
    def _check_oversold_bounce(
        self,
        rsi: float,
        bb,
        current_price: float
    ) -> Optional[Signal]:
        """檢查超賣反彈做多條件"""
        rsi_config = self.config.rsi
        mr_config = self.config.mean_reversion
        
        # 條件 1: RSI < 30 (超賣)
        if rsi >= rsi_config.oversold:
            return None
        
        # 條件 2: 價格 < BB下軌 * 0.99
        if current_price >= bb.lower * 0.99:
            return None
        
        # 條件 3: BB Position < 0.1
        if bb.position >= mr_config.bb_oversold_position:
            return None
        
        # 計算強度 (RSI 越低越強)
        rsi_strength = (rsi_config.oversold - rsi) / rsi_config.oversold
        position_strength = (mr_config.bb_oversold_position - bb.position) / mr_config.bb_oversold_position
        strength = (rsi_strength + position_strength) / 2
        
        # 計算止損止盈
        stops = self.calculate_stops(
            None,
            SignalType.LONG,
            current_price,
            bb_lower=bb.lower,
            bb_middle=bb.middle,
            bb_upper=bb.upper
        )
        
        confidence = min(1.0, strength * 0.8 + 0.2)
        
        reason = (
            f"MR 超賣反彈做多: RSI={rsi:.1f}, BB Position={bb.position:.2f}, "
            f"價格跌破下軌"
        )
        
        return self.create_signal(
            signal_type=SignalType.LONG,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=confidence,
            reason=reason
        )
    
    def _check_overbought_pullback(
        self,
        rsi: float,
        bb,
        current_price: float
    ) -> Optional[Signal]:
        """檢查超買回落做空條件"""
        rsi_config = self.config.rsi
        mr_config = self.config.mean_reversion
        
        # 條件 1: RSI > 70 (超買)
        if rsi <= rsi_config.overbought:
            return None
        
        # 條件 2: 價格 > BB上軌 * 1.01
        if current_price <= bb.upper * 1.01:
            return None
        
        # 條件 3: BB Position > 0.9
        if bb.position <= mr_config.bb_overbought_position:
            return None
        
        # 計算強度 (RSI 越高越強)
        rsi_strength = (rsi - rsi_config.overbought) / (100 - rsi_config.overbought)
        position_strength = (bb.position - mr_config.bb_overbought_position) / (1 - mr_config.bb_overbought_position)
        strength = (rsi_strength + position_strength) / 2
        
        # 計算止損止盈
        stops = self.calculate_stops(
            None,
            SignalType.SHORT,
            current_price,
            bb_lower=bb.lower,
            bb_middle=bb.middle,
            bb_upper=bb.upper
        )
        
        confidence = min(1.0, strength * 0.8 + 0.2)
        
        reason = (
            f"MR 超買回落做空: RSI={rsi:.1f}, BB Position={bb.position:.2f}, "
            f"價格突破上軌"
        )
        
        return self.create_signal(
            signal_type=SignalType.SHORT,
            entry_price=current_price,
            stops=stops,
            strength=strength,
            confidence=confidence,
            reason=reason
        )
    
    def _check_mid_band_bounce(
        self,
        rsi: float,
        bb,
        current_price: float
    ) -> Optional[Signal]:
        """
        檢查中軌反彈
        
        修正邏輯：價格從極端區域回歸中軌時進場
        - 從下方反彈：價格剛離開下軌區域，向中軌移動
        - 從上方回落：價格剛離開上軌區域，向中軌移動
        """
        mr_config = self.config.mean_reversion
        
        # 從下方反彈做多：
        # - 價格在下半部 (BB Position 0.2-0.45)
        # - RSI 開始從超賣區回升 (30-45)
        if 0.2 < bb.position < 0.45:
            if 30 < rsi < 45:
                strength = 0.5  # 中軌反彈強度較低
                
                stops = self.calculate_stops(
                    None,
                    SignalType.LONG,
                    current_price,
                    bb_lower=bb.lower,
                    bb_middle=bb.middle,
                    bb_upper=bb.upper
                )
                # 中軌反彈止盈設在中軌
                stops = StopLossTarget(
                    stop_loss=stops.stop_loss,
                    take_profit=bb.middle,
                    risk_reward_ratio=stops.risk_reward_ratio,
                    stop_distance_percent=stops.stop_distance_percent,
                    profit_distance_percent=(bb.middle - current_price) / current_price if current_price > 0 else 0
                )
                
                reason = f"MR 下方反彈做多: RSI={rsi:.1f}, BB Position={bb.position:.2f}"
                
                return self.create_signal(
                    signal_type=SignalType.LONG,
                    entry_price=current_price,
                    stops=stops,
                    strength=strength,
                    confidence=0.5,
                    reason=reason
                )
        
        # 從上方回落做空：
        # - 價格在上半部 (BB Position 0.55-0.8)
        # - RSI 開始從超買區回落 (55-70)
        elif 0.55 < bb.position < 0.8:
            if 55 < rsi < 70:
                strength = 0.5
                
                stops = self.calculate_stops(
                    None,
                    SignalType.SHORT,
                    current_price,
                    bb_lower=bb.lower,
                    bb_middle=bb.middle,
                    bb_upper=bb.upper
                )
                # 中軌反彈止盈設在中軌
                stops = StopLossTarget(
                    stop_loss=stops.stop_loss,
                    take_profit=bb.middle,
                    risk_reward_ratio=stops.risk_reward_ratio,
                    stop_distance_percent=stops.stop_distance_percent,
                    profit_distance_percent=(current_price - bb.middle) / current_price if current_price > 0 else 0
                )
                
                reason = f"MR 上方回落做空: RSI={rsi:.1f}, BB Position={bb.position:.2f}"
                
                return self.create_signal(
                    signal_type=SignalType.SHORT,
                    entry_price=current_price,
                    stops=stops,
                    strength=strength,
                    confidence=0.5,
                    reason=reason
                )
        
        return None
    
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
        3. RSI 回到中性
        4. 到達中軌
        5. 時間止損
        """
        current_price = indicators.current_price
        rsi = indicators.rsi
        bb = indicators.bollinger
        
        # 做多出場
        if entry_signal.signal_type == SignalType.LONG:
            # 止損
            if current_price <= entry_signal.stop_loss:
                return True, f"止損觸發 @ {current_price:.2f}"
            
            # 止盈
            if current_price >= entry_signal.take_profit:
                return True, f"止盈觸發 @ {current_price:.2f}"
            
            # RSI 回到中性 (> 55)
            if rsi > 55:
                return True, f"RSI 回到中性 ({rsi:.1f})"
            
            # 到達中軌
            if current_price >= bb.middle:
                return True, f"到達 BB 中軌 @ {current_price:.2f}"
        
        # 做空出場
        elif entry_signal.signal_type == SignalType.SHORT:
            # 止損
            if current_price >= entry_signal.stop_loss:
                return True, f"止損觸發 @ {current_price:.2f}"
            
            # 止盈
            if current_price <= entry_signal.take_profit:
                return True, f"止盈觸發 @ {current_price:.2f}"
            
            # RSI 回到中性 (< 45)
            if rsi < 45:
                return True, f"RSI 回到中性 ({rsi:.1f})"
            
            # 到達中軌
            if current_price <= bb.middle:
                return True, f"到達 BB 中軌 @ {current_price:.2f}"
        
        # 時間止損 (在主程式中處理)
        
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
        """計算止損止盈"""
        
        # 如果沒有提供 BB 值，從 indicators 獲取
        if bb_lower is None and indicators is not None:
            bb_lower = indicators.bollinger.lower
            bb_middle = indicators.bollinger.middle
            bb_upper = indicators.bollinger.upper
        
        return position_manager.calculate_mean_reversion_stops(
            entry_price=entry_price,
            bb_lower=bb_lower,
            bb_middle=bb_middle,
            bb_upper=bb_upper,
            signal_type=signal_type
        )
    
    def reset(self):
        """重置策略狀態"""
        super().reset()
        self.entry_time = None


# 全域 Mean Reversion 策略實例
mean_reversion_strategy = MeanReversionStrategy()
