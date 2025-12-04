"""
Signal Readiness Checker
Evaluates individual entry conditions and provides a summary of met/unmet conditions
"""
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum

from config import settings, MarketRegime, SignalType
from core.indicators import IndicatorValues, TrendDirection
from core.market_regime import MarketState, MarketRegimeDetector


class ConditionStatus(str, Enum):
    """Condition evaluation status"""
    MET = "met"
    NOT_MET = "not_met"
    NEUTRAL = "neutral"  # For informational conditions


@dataclass
class ConditionResult:
    """Result of a single condition evaluation"""
    name: str
    status: ConditionStatus
    current_value: str
    required_value: str
    emoji: str = ""
    
    def __post_init__(self):
        if not self.emoji:
            self.emoji = "✅" if self.status == ConditionStatus.MET else "❌" if self.status == ConditionStatus.NOT_MET else "⚪"


@dataclass
class SignalReadiness:
    """Complete signal readiness evaluation"""
    signal_type: SignalType  # LONG, SHORT, or NONE (checking both)
    strategy: str  # "momentum" or "mean_reversion"
    conditions: List[ConditionResult] = field(default_factory=list)
    
    @property
    def met_count(self) -> int:
        return sum(1 for c in self.conditions if c.status == ConditionStatus.MET)
    
    @property
    def total_count(self) -> int:
        return sum(1 for c in self.conditions if c.status != ConditionStatus.NEUTRAL)
    
    @property
    def readiness_percent(self) -> float:
        if self.total_count == 0:
            return 0.0
        return (self.met_count / self.total_count) * 100
    
    @property
    def is_ready(self) -> bool:
        """Check if all required conditions are met"""
        return self.met_count == self.total_count and self.total_count > 0


class SignalReadinessChecker:
    """
    Evaluates all entry conditions for trading signals
    and provides a summary of which conditions are met/not met
    """
    
    # Momentum Strategy Thresholds
    MIN_ADX_THRESHOLD = 20
    MIN_DI_SPREAD = 5
    MIN_STOP_DISTANCE_PERCENT = 0.003
    
    # Mean Reversion Strategy Thresholds
    EXTREME_OVERSOLD_BB = 0.25
    EXTREME_OVERBOUGHT_BB = 0.75
    EXTREME_OVERSOLD_RSI = 25
    EXTREME_OVERBOUGHT_RSI = 75
    
    def __init__(self):
        self.config = settings
    
    def check_momentum_long(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> SignalReadiness:
        """Check all conditions for Momentum LONG entry"""
        readiness = SignalReadiness(
            signal_type=SignalType.LONG,
            strategy="Momentum"
        )
        
        # 1. Market Regime (ADX > threshold)
        adx_threshold = self.config.adx.threshold
        is_trending = market_state.regime == MarketRegime.TRENDING
        readiness.conditions.append(ConditionResult(
            name="Market Regime",
            status=ConditionStatus.MET if is_trending else ConditionStatus.NOT_MET,
            current_value=f"{market_state.regime.value} (ADX={indicators.adx:.1f})",
            required_value=f"TRENDING (ADX>{adx_threshold})"
        ))
        
        # 2. Strategy ADX threshold
        adx_ok = indicators.adx > self.MIN_ADX_THRESHOLD
        readiness.conditions.append(ConditionResult(
            name="ADX Strength",
            status=ConditionStatus.MET if adx_ok else ConditionStatus.NOT_MET,
            current_value=f"{indicators.adx:.1f}",
            required_value=f">{self.MIN_ADX_THRESHOLD}"
        ))
        
        # 3. Supertrend Fast UP
        st_fast_up = indicators.supertrend_fast.direction == TrendDirection.UP
        readiness.conditions.append(ConditionResult(
            name="Supertrend Fast",
            status=ConditionStatus.MET if st_fast_up else ConditionStatus.NOT_MET,
            current_value=indicators.supertrend_fast.direction.name,
            required_value="UP"
        ))
        
        # 4. Supertrend Slow UP
        st_slow_up = indicators.supertrend_slow.direction == TrendDirection.UP
        readiness.conditions.append(ConditionResult(
            name="Supertrend Slow",
            status=ConditionStatus.MET if st_slow_up else ConditionStatus.NOT_MET,
            current_value=indicators.supertrend_slow.direction.name,
            required_value="UP"
        ))
        
        # 5. Price > EMA Fast
        price_above_ema = indicators.current_price > indicators.ema_fast
        readiness.conditions.append(ConditionResult(
            name="Price vs EMA Fast",
            status=ConditionStatus.MET if price_above_ema else ConditionStatus.NOT_MET,
            current_value=f"${indicators.current_price:.2f}",
            required_value=f">${indicators.ema_fast:.2f}"
        ))
        
        # 6. EMA Fast > EMA Slow
        ema_aligned = indicators.ema_fast > indicators.ema_slow
        readiness.conditions.append(ConditionResult(
            name="EMA Alignment",
            status=ConditionStatus.MET if ema_aligned else ConditionStatus.NOT_MET,
            current_value=f"Fast={indicators.ema_fast:.2f}",
            required_value=f">Slow={indicators.ema_slow:.2f}"
        ))
        
        # 7. DI+ > DI- by at least 5
        di_spread = indicators.plus_di - indicators.minus_di
        di_ok = di_spread >= self.MIN_DI_SPREAD
        readiness.conditions.append(ConditionResult(
            name="DI Spread (DI+ - DI-)",
            status=ConditionStatus.MET if di_ok else ConditionStatus.NOT_MET,
            current_value=f"{di_spread:.1f} (DI+={indicators.plus_di:.1f}, DI-={indicators.minus_di:.1f})",
            required_value=f"≥{self.MIN_DI_SPREAD}"
        ))
        
        return readiness
    
    def check_momentum_short(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> SignalReadiness:
        """Check all conditions for Momentum SHORT entry"""
        readiness = SignalReadiness(
            signal_type=SignalType.SHORT,
            strategy="Momentum"
        )
        
        # 1. Market Regime
        adx_threshold = self.config.adx.threshold
        is_trending = market_state.regime == MarketRegime.TRENDING
        readiness.conditions.append(ConditionResult(
            name="Market Regime",
            status=ConditionStatus.MET if is_trending else ConditionStatus.NOT_MET,
            current_value=f"{market_state.regime.value} (ADX={indicators.adx:.1f})",
            required_value=f"TRENDING (ADX>{adx_threshold})"
        ))
        
        # 2. Strategy ADX threshold
        adx_ok = indicators.adx > self.MIN_ADX_THRESHOLD
        readiness.conditions.append(ConditionResult(
            name="ADX Strength",
            status=ConditionStatus.MET if adx_ok else ConditionStatus.NOT_MET,
            current_value=f"{indicators.adx:.1f}",
            required_value=f">{self.MIN_ADX_THRESHOLD}"
        ))
        
        # 3. Supertrend Fast DOWN
        st_fast_down = indicators.supertrend_fast.direction == TrendDirection.DOWN
        readiness.conditions.append(ConditionResult(
            name="Supertrend Fast",
            status=ConditionStatus.MET if st_fast_down else ConditionStatus.NOT_MET,
            current_value=indicators.supertrend_fast.direction.name,
            required_value="DOWN"
        ))
        
        # 4. Supertrend Slow DOWN
        st_slow_down = indicators.supertrend_slow.direction == TrendDirection.DOWN
        readiness.conditions.append(ConditionResult(
            name="Supertrend Slow",
            status=ConditionStatus.MET if st_slow_down else ConditionStatus.NOT_MET,
            current_value=indicators.supertrend_slow.direction.name,
            required_value="DOWN"
        ))
        
        # 5. Price < EMA Fast
        price_below_ema = indicators.current_price < indicators.ema_fast
        readiness.conditions.append(ConditionResult(
            name="Price vs EMA Fast",
            status=ConditionStatus.MET if price_below_ema else ConditionStatus.NOT_MET,
            current_value=f"${indicators.current_price:.2f}",
            required_value=f"<${indicators.ema_fast:.2f}"
        ))
        
        # 6. EMA Fast < EMA Slow
        ema_aligned = indicators.ema_fast < indicators.ema_slow
        readiness.conditions.append(ConditionResult(
            name="EMA Alignment",
            status=ConditionStatus.MET if ema_aligned else ConditionStatus.NOT_MET,
            current_value=f"Fast={indicators.ema_fast:.2f}",
            required_value=f"<Slow={indicators.ema_slow:.2f}"
        ))
        
        # 7. DI- > DI+ by at least 5
        di_spread = indicators.minus_di - indicators.plus_di
        di_ok = di_spread >= self.MIN_DI_SPREAD
        readiness.conditions.append(ConditionResult(
            name="DI Spread (DI- - DI+)",
            status=ConditionStatus.MET if di_ok else ConditionStatus.NOT_MET,
            current_value=f"{di_spread:.1f} (DI-={indicators.minus_di:.1f}, DI+={indicators.plus_di:.1f})",
            required_value=f"≥{self.MIN_DI_SPREAD}"
        ))
        
        return readiness
    
    def check_mean_reversion_long(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> SignalReadiness:
        """Check all conditions for Mean Reversion LONG entry"""
        readiness = SignalReadiness(
            signal_type=SignalType.LONG,
            strategy="Mean Reversion"
        )
        
        # 1. Market Regime (RANGING)
        adx_threshold = self.config.adx.threshold
        is_ranging = market_state.regime == MarketRegime.RANGING
        readiness.conditions.append(ConditionResult(
            name="Market Regime",
            status=ConditionStatus.MET if is_ranging else ConditionStatus.NOT_MET,
            current_value=f"{market_state.regime.value} (ADX={indicators.adx:.1f})",
            required_value=f"RANGING (ADX<{adx_threshold})"
        ))
        
        # 2. BB Position (extreme oversold)
        bb_pos = indicators.bollinger.position
        bb_oversold = bb_pos < self.EXTREME_OVERSOLD_BB
        readiness.conditions.append(ConditionResult(
            name="BB Position (Oversold)",
            status=ConditionStatus.MET if bb_oversold else ConditionStatus.NOT_MET,
            current_value=f"{bb_pos:.2f} ({bb_pos*100:.0f}%)",
            required_value=f"<{self.EXTREME_OVERSOLD_BB} ({self.EXTREME_OVERSOLD_BB*100:.0f}%)"
        ))
        
        # 3. RSI (extreme oversold)
        rsi_oversold = indicators.rsi < self.EXTREME_OVERSOLD_RSI
        readiness.conditions.append(ConditionResult(
            name="RSI (Oversold)",
            status=ConditionStatus.MET if rsi_oversold else ConditionStatus.NOT_MET,
            current_value=f"{indicators.rsi:.1f}",
            required_value=f"<{self.EXTREME_OVERSOLD_RSI}"
        ))
        
        return readiness
    
    def check_mean_reversion_short(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> SignalReadiness:
        """Check all conditions for Mean Reversion SHORT entry"""
        readiness = SignalReadiness(
            signal_type=SignalType.SHORT,
            strategy="Mean Reversion"
        )
        
        # 1. Market Regime (RANGING)
        adx_threshold = self.config.adx.threshold
        is_ranging = market_state.regime == MarketRegime.RANGING
        readiness.conditions.append(ConditionResult(
            name="Market Regime",
            status=ConditionStatus.MET if is_ranging else ConditionStatus.NOT_MET,
            current_value=f"{market_state.regime.value} (ADX={indicators.adx:.1f})",
            required_value=f"RANGING (ADX<{adx_threshold})"
        ))
        
        # 2. BB Position (extreme overbought)
        bb_pos = indicators.bollinger.position
        bb_overbought = bb_pos > self.EXTREME_OVERBOUGHT_BB
        readiness.conditions.append(ConditionResult(
            name="BB Position (Overbought)",
            status=ConditionStatus.MET if bb_overbought else ConditionStatus.NOT_MET,
            current_value=f"{bb_pos:.2f} ({bb_pos*100:.0f}%)",
            required_value=f">{self.EXTREME_OVERBOUGHT_BB} ({self.EXTREME_OVERBOUGHT_BB*100:.0f}%)"
        ))
        
        # 3. RSI (extreme overbought)
        rsi_overbought = indicators.rsi > self.EXTREME_OVERBOUGHT_RSI
        readiness.conditions.append(ConditionResult(
            name="RSI (Overbought)",
            status=ConditionStatus.MET if rsi_overbought else ConditionStatus.NOT_MET,
            current_value=f"{indicators.rsi:.1f}",
            required_value=f">{self.EXTREME_OVERBOUGHT_RSI}"
        ))
        
        return readiness
    
    def get_all_readiness(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> dict:
        """
        Get readiness for all strategies and directions
        
        Returns:
            dict with keys: 'momentum_long', 'momentum_short', 'mr_long', 'mr_short'
        """
        return {
            'momentum_long': self.check_momentum_long(indicators, market_state),
            'momentum_short': self.check_momentum_short(indicators, market_state),
            'mr_long': self.check_mean_reversion_long(indicators, market_state),
            'mr_short': self.check_mean_reversion_short(indicators, market_state),
        }
    
    def get_best_opportunity(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> Optional[SignalReadiness]:
        """
        Get the best trading opportunity based on current conditions
        Returns the readiness with highest percentage
        """
        all_readiness = self.get_all_readiness(indicators, market_state)
        
        best = None
        best_pct = 0
        
        for readiness in all_readiness.values():
            if readiness.readiness_percent > best_pct:
                best_pct = readiness.readiness_percent
                best = readiness
        
        return best


# Global instance
signal_readiness_checker = SignalReadinessChecker()
