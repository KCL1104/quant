"""
市場狀態判斷模組
根據 ADX、ATR、BB 等指標判斷當前市場是趨勢市還是震盪市
"""
from dataclasses import dataclass
from typing import Optional, Dict

from config import settings, MarketRegime
from core.indicators import IndicatorValues


@dataclass
class MarketState:
    """市場狀態"""
    regime: MarketRegime          # 市場狀態
    adx_value: float              # ADX 值
    atr_percent: float            # ATR 百分比
    bb_width: float               # BB 帶寬
    bb_position: float            # 價格在 BB 中的位置
    confidence: float             # 判斷信心度 (0-1)
    description: str              # 狀態描述


class MarketRegimeDetector:
    """市場狀態檢測器
    
    注意：每個市場應該有自己的檢測器實例，以避免狀態污染。
    使用 create_detector() 工廠方法創建新實例。
    """
    
    def __init__(self, market_id: int = None):
        self.config = settings
        self.market_id = market_id  # 可選的市場標識
        self._last_regime = MarketRegime.UNKNOWN
        self._regime_count = 0  # 連續相同狀態的次數
    
    def detect(self, indicators: IndicatorValues) -> MarketState:
        """
        檢測市場狀態
        
        Args:
            indicators: 指標值集合
            
        Returns:
            MarketState 市場狀態
        """
        adx = indicators.adx
        atr_percent = indicators.atr_percent
        bb_position = indicators.bollinger.position
        bb_width = indicators.bollinger.width
        
        # 計算趨勢和震盪條件
        is_trending = self._check_trending(adx, atr_percent)
        is_ranging = self._check_ranging(adx, atr_percent, bb_position)
        
        # 判斷狀態
        if is_trending and not is_ranging:
            regime = MarketRegime.TRENDING
            confidence = self._calculate_trending_confidence(adx, atr_percent)
            description = self._get_trending_description(adx, atr_percent)
        elif is_ranging and not is_trending:
            regime = MarketRegime.RANGING
            confidence = self._calculate_ranging_confidence(adx, atr_percent, bb_position)
            description = self._get_ranging_description(adx, bb_position)
        else:
            regime = MarketRegime.UNKNOWN
            confidence = 0.0
            description = "市場狀態不明確，建議等待"
        
        # 更新狀態連續性
        if regime == self._last_regime:
            self._regime_count += 1
        else:
            self._regime_count = 1
            self._last_regime = regime
        
        return MarketState(
            regime=regime,
            adx_value=adx,
            atr_percent=atr_percent,
            bb_width=bb_width,
            bb_position=bb_position,
            confidence=confidence,
            description=description
        )
    
    def _check_trending(self, adx: float, atr_percent: float) -> bool:
        """檢查是否為趨勢市（放寬條件：只需 ADX 或 ATR 其一滿足）"""
        adx_threshold = self.config.adx.threshold
        atr_threshold = self.config.atr.trending_threshold
        
        # 放寬：只要 ADX 超過閾值即可，或 ATR 較高即可
        return (adx > adx_threshold) or (atr_percent > atr_threshold)
    
    def _check_ranging(
        self,
        adx: float,
        atr_percent: float,
        bb_position: float
    ) -> bool:
        """檢查是否為震盪市（放寬條件）"""
        adx_threshold = self.config.adx.threshold
        atr_threshold = self.config.atr.ranging_threshold
        
        # ADX 低於閾值
        low_adx = adx < adx_threshold
        
        # ATR 較低
        low_atr = atr_percent < atr_threshold * 1.2  # 稍微放寬 ATR 限制
        
        # 價格在 BB 任何區域都可以（移除位置限制）
        # 原本要求在中間區域太嚴格
        
        # 放寬：只要 ADX 低即可判定為震盪市
        return low_adx
    
    def _calculate_trending_confidence(
        self,
        adx: float,
        atr_percent: float
    ) -> float:
        """計算趨勢市的信心度"""
        adx_threshold = self.config.adx.threshold
        atr_threshold = self.config.atr.trending_threshold
        
        # ADX 貢獻 (越高越好，最高到 50)
        adx_score = min((adx - adx_threshold) / (50 - adx_threshold), 1.0)
        
        # ATR 貢獻 (越高越好，最高到 5%)
        atr_score = min((atr_percent - atr_threshold) / (0.05 - atr_threshold), 1.0)
        
        # 加權平均
        confidence = (adx_score * 0.6 + atr_score * 0.4)
        return max(0, min(1, confidence))
    
    def _calculate_ranging_confidence(
        self,
        adx: float,
        atr_percent: float,
        bb_position: float
    ) -> float:
        """計算震盪市的信心度"""
        adx_threshold = self.config.adx.threshold
        
        # ADX 越低越好
        adx_score = max(0, (adx_threshold - adx) / adx_threshold)
        
        # 價格越接近中軌越好
        position_score = 1 - abs(bb_position - 0.5) * 2
        
        # 加權平均
        confidence = (adx_score * 0.5 + position_score * 0.5)
        return max(0, min(1, confidence))
    
    def _get_trending_description(self, adx: float, atr_percent: float) -> str:
        """取得趨勢市描述"""
        if adx > 50:
            strength = "極強"
        elif adx > 40:
            strength = "強"
        elif adx > 30:
            strength = "中等"
        else:
            strength = "弱"
        
        return f"趨勢市場 - ADX={adx:.1f} ({strength}趨勢), ATR={atr_percent*100:.2f}%"
    
    def _get_ranging_description(self, adx: float, bb_position: float) -> str:
        """取得震盪市描述"""
        if bb_position < 0.3:
            position = "接近下軌"
        elif bb_position > 0.7:
            position = "接近上軌"
        else:
            position = "中間區域"
        
        return f"震盪市場 - ADX={adx:.1f}, 價格{position} (BB Position={bb_position:.2f})"
    
    def is_regime_stable(self, min_count: int = 3) -> bool:
        """
        檢查市場狀態是否穩定
        
        Args:
            min_count: 最少需要連續出現的次數
            
        Returns:
            是否穩定
        """
        return self._regime_count >= min_count
    
    def get_regime_duration(self) -> int:
        """取得當前狀態持續的次數"""
        return self._regime_count
    
    def reset(self):
        """重置狀態"""
        self._last_regime = MarketRegime.UNKNOWN
        self._regime_count = 0


# 全域市場狀態檢測器（用於單市場模式或向後兼容）
market_detector = MarketRegimeDetector()

# 市場檢測器緩存（用於多市場模式）
_market_detectors: Dict[int, MarketRegimeDetector] = {}


def get_market_detector(market_id: int) -> MarketRegimeDetector:
    """
    獲取指定市場的檢測器實例
    
    多市場模式下，每個市場應該有自己的檢測器以避免狀態污染。
    
    Args:
        market_id: 市場 ID
        
    Returns:
        該市場的 MarketRegimeDetector 實例
    """
    if market_id not in _market_detectors:
        _market_detectors[market_id] = MarketRegimeDetector(market_id=market_id)
    return _market_detectors[market_id]


def create_detector(market_id: int = None) -> MarketRegimeDetector:
    """
    創建新的市場狀態檢測器實例
    
    Args:
        market_id: 可選的市場 ID
        
    Returns:
        新的 MarketRegimeDetector 實例
    """
    return MarketRegimeDetector(market_id=market_id)
