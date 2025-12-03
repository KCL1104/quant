"""
技術指標計算模組
使用 TA-Lib 計算標準指標，自行實現 Supertrend
"""
import numpy as np
import pandas as pd
import talib
from dataclasses import dataclass
from typing import Tuple, Optional
from enum import Enum

from config import settings


class TrendDirection(Enum):
    """趨勢方向"""
    UP = 1
    DOWN = -1
    NEUTRAL = 0


@dataclass
class SupertrendResult:
    """Supertrend 計算結果"""
    value: float              # Supertrend 值
    direction: TrendDirection # 趨勢方向
    upper_band: float         # 上軌
    lower_band: float         # 下軌


@dataclass
class BollingerResult:
    """Bollinger Bands 計算結果"""
    upper: float              # 上軌
    middle: float             # 中軌
    lower: float              # 下軌
    width: float              # 帶寬 (upper - lower) / middle
    position: float           # 價格在帶內的位置 (0-1)


@dataclass
class IndicatorValues:
    """所有指標值的集合"""
    # Supertrend
    supertrend_fast: SupertrendResult
    supertrend_slow: SupertrendResult
    
    # EMA
    ema_fast: float
    ema_slow: float
    
    # RSI
    rsi: float
    
    # Bollinger Bands
    bollinger: BollingerResult
    
    # ADX
    adx: float
    plus_di: float
    minus_di: float
    
    # ATR
    atr: float
    atr_percent: float
    
    # 價格資訊
    current_price: float
    high: float
    low: float


class Indicators:
    """技術指標計算器"""
    
    def __init__(self):
        self.config = settings
    
    def calculate_supertrend(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = None,
        multiplier: float = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        計算 Supertrend 指標
        
        Args:
            high: 最高價數組
            low: 最低價數組
            close: 收盤價數組
            period: ATR 週期
            multiplier: ATR 乘數
            
        Returns:
            (supertrend, direction, upper_band, lower_band)
        """
        if period is None:
            period = self.config.supertrend.period
        if multiplier is None:
            multiplier = self.config.supertrend.multiplier
        
        # 計算 ATR
        atr = talib.ATR(high, low, close, timeperiod=period)
        
        # 計算基礎線 (HL2)
        hl2 = (high + low) / 2
        
        # 計算上下軌
        upper_band = hl2 + (multiplier * atr)
        lower_band = hl2 - (multiplier * atr)
        
        # 初始化結果數組
        n = len(close)
        supertrend = np.zeros(n)
        direction = np.zeros(n)
        final_upper = np.zeros(n)
        final_lower = np.zeros(n)
        
        # 初始值
        final_upper[period-1] = upper_band[period-1]
        final_lower[period-1] = lower_band[period-1]
        supertrend[period-1] = lower_band[period-1]  # 預設為上升趨勢
        direction[period-1] = 1
        
        for i in range(period, n):
            # 更新上軌
            if upper_band[i] < final_upper[i-1] or close[i-1] > final_upper[i-1]:
                final_upper[i] = upper_band[i]
            else:
                final_upper[i] = final_upper[i-1]
            
            # 更新下軌
            if lower_band[i] > final_lower[i-1] or close[i-1] < final_lower[i-1]:
                final_lower[i] = lower_band[i]
            else:
                final_lower[i] = final_lower[i-1]
            
            # 判斷趨勢方向
            if direction[i-1] == 1:  # 之前是上升趨勢
                if close[i] < final_lower[i]:
                    direction[i] = -1  # 轉為下降
                    supertrend[i] = final_upper[i]
                else:
                    direction[i] = 1
                    supertrend[i] = final_lower[i]
            else:  # 之前是下降趨勢或初始
                if close[i] > final_upper[i]:
                    direction[i] = 1  # 轉為上升
                    supertrend[i] = final_lower[i]
                else:
                    direction[i] = -1
                    supertrend[i] = final_upper[i]
        
        return supertrend, direction, final_upper, final_lower
    
    def get_supertrend_result(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = None,
        multiplier: float = None
    ) -> SupertrendResult:
        """取得最新的 Supertrend 結果"""
        st, direction, upper, lower = self.calculate_supertrend(
            high, low, close, period, multiplier
        )
        
        trend_dir = TrendDirection.UP if direction[-1] == 1 else TrendDirection.DOWN
        
        return SupertrendResult(
            value=st[-1],
            direction=trend_dir,
            upper_band=upper[-1],
            lower_band=lower[-1]
        )
    
    def calculate_ema(
        self,
        close: np.ndarray,
        period: int
    ) -> np.ndarray:
        """計算 EMA"""
        return talib.EMA(close, timeperiod=period)
    
    def calculate_rsi(
        self,
        close: np.ndarray,
        period: int = None
    ) -> np.ndarray:
        """計算 RSI"""
        if period is None:
            period = self.config.rsi.period
        return talib.RSI(close, timeperiod=period)
    
    def calculate_bollinger(
        self,
        close: np.ndarray,
        period: int = None,
        std_dev: float = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        計算 Bollinger Bands
        
        Returns:
            (upper, middle, lower)
        """
        if period is None:
            period = self.config.bollinger.period
        if std_dev is None:
            std_dev = self.config.bollinger.std_dev
            
        upper, middle, lower = talib.BBANDS(
            close,
            timeperiod=period,
            nbdevup=std_dev,
            nbdevdn=std_dev,
            matype=0  # SMA
        )
        return upper, middle, lower
    
    def get_bollinger_result(
        self,
        close: np.ndarray,
        current_price: float,
        period: int = None,
        std_dev: float = None
    ) -> BollingerResult:
        """取得最新的 Bollinger Bands 結果"""
        upper, middle, lower = self.calculate_bollinger(close, period, std_dev)
        
        # 計算帶寬
        width = (upper[-1] - lower[-1]) / middle[-1] if middle[-1] != 0 else 0
        
        # 計算價格位置 (0 = 下軌, 1 = 上軌)
        band_range = upper[-1] - lower[-1]
        if band_range != 0:
            position = (current_price - lower[-1]) / band_range
        else:
            position = 0.5
        
        return BollingerResult(
            upper=upper[-1],
            middle=middle[-1],
            lower=lower[-1],
            width=width,
            position=position
        )
    
    def calculate_adx(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        計算 ADX 和 DI
        
        Returns:
            (adx, plus_di, minus_di)
        """
        if period is None:
            period = self.config.adx.period
            
        adx = talib.ADX(high, low, close, timeperiod=period)
        plus_di = talib.PLUS_DI(high, low, close, timeperiod=period)
        minus_di = talib.MINUS_DI(high, low, close, timeperiod=period)
        
        return adx, plus_di, minus_di
    
    def calculate_atr(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = None
    ) -> np.ndarray:
        """計算 ATR"""
        if period is None:
            period = self.config.atr.period
        return talib.ATR(high, low, close, timeperiod=period)
    
    def calculate_all(
        self,
        df_fast: pd.DataFrame,
        df_slow: pd.DataFrame
    ) -> IndicatorValues:
        """
        計算所有指標
        
        Args:
            df_fast: 快速時間框架 K線 DataFrame (columns: open, high, low, close, volume)
            df_slow: 慢速時間框架 K線 DataFrame
            
        Returns:
            IndicatorValues 包含所有指標值
            
        Raises:
            ValueError: 如果數據不足或包含無效值
        """
        # 驗證數據
        min_required = max(
            self.config.supertrend.period,
            self.config.ema.slow_period,
            self.config.bollinger.period,
            self.config.adx.period,
            self.config.atr.period
        ) + 10  # 額外緩衝
        
        if len(df_fast) < min_required:
            raise ValueError(f"快速時間框架數據不足: {len(df_fast)} < {min_required}")
        if len(df_slow) < min_required:
            raise ValueError(f"慢速時間框架數據不足: {len(df_slow)} < {min_required}")
        
        # 從快速時間框架取得價格數據
        high_fast = df_fast['high'].values.astype(np.float64)
        low_fast = df_fast['low'].values.astype(np.float64)
        close_fast = df_fast['close'].values.astype(np.float64)
        
        # 從慢速時間框架取得價格數據
        high_slow = df_slow['high'].values.astype(np.float64)
        low_slow = df_slow['low'].values.astype(np.float64)
        close_slow = df_slow['close'].values.astype(np.float64)
        
        current_price = close_fast[-1]
        
        # 檢查價格有效性
        if np.isnan(current_price) or current_price <= 0:
            raise ValueError(f"無效的當前價格: {current_price}")
        
        # Supertrend - 快速 (使用快速 TF)
        st_fast = self.get_supertrend_result(high_fast, low_fast, close_fast)
        
        # Supertrend - 慢速 (使用慢速 TF)
        st_slow = self.get_supertrend_result(high_slow, low_slow, close_slow)
        
        # EMA (使用快速 TF)
        ema_fast_arr = self.calculate_ema(close_fast, self.config.ema.fast_period)
        ema_slow_arr = self.calculate_ema(close_fast, self.config.ema.slow_period)
        
        # RSI (使用快速 TF)
        rsi_arr = self.calculate_rsi(close_fast)
        
        # Bollinger Bands (使用快速 TF)
        bb_result = self.get_bollinger_result(close_fast, current_price)
        
        # ADX (使用慢速 TF 來判斷大趨勢)
        adx_arr, plus_di_arr, minus_di_arr = self.calculate_adx(
            high_slow, low_slow, close_slow
        )
        
        # ATR (使用快速 TF)
        atr_arr = self.calculate_atr(high_fast, low_fast, close_fast)
        
        # 取得最新值並處理 NaN
        ema_fast = self._safe_get_last(ema_fast_arr, current_price)
        ema_slow = self._safe_get_last(ema_slow_arr, current_price)
        rsi = self._safe_get_last(rsi_arr, 50.0)  # RSI 預設為中性
        adx = self._safe_get_last(adx_arr, 20.0)  # ADX 預設為低趨勢
        plus_di = self._safe_get_last(plus_di_arr, 20.0)
        minus_di = self._safe_get_last(minus_di_arr, 20.0)
        atr = self._safe_get_last(atr_arr, current_price * 0.02)  # ATR 預設為 2%
        
        atr_percent = atr / current_price if current_price != 0 else 0
        
        return IndicatorValues(
            supertrend_fast=st_fast,
            supertrend_slow=st_slow,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            bollinger=bb_result,
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            atr=atr,
            atr_percent=atr_percent,
            current_price=current_price,
            high=high_fast[-1],
            low=low_fast[-1]
        )
    
    def _safe_get_last(self, arr: np.ndarray, default: float) -> float:
        """安全取得數組最後一個值，處理 NaN"""
        if len(arr) == 0:
            return default
        
        val = arr[-1]
        if np.isnan(val):
            # 嘗試找到最後一個非 NaN 值
            for i in range(len(arr) - 2, -1, -1):
                if not np.isnan(arr[i]):
                    return arr[i]
            return default
        return val
    
    def calculate_momentum_strength(
        self,
        close: np.ndarray,
        direction: TrendDirection,
        lookback: int = None
    ) -> float:
        """
        計算動能強度
        
        Args:
            close: 收盤價數組
            direction: 當前趨勢方向
            lookback: 回看週期
            
        Returns:
            動能強度 (0-1)
        """
        if lookback is None:
            lookback = self.config.momentum.strength_lookback
        
        if len(close) < lookback:
            return 0.0
        
        recent_close = close[-lookback:]
        trend_count = 0
        
        for i in range(len(recent_close) - 1):
            if direction == TrendDirection.UP:
                if recent_close[i + 1] > recent_close[i]:
                    trend_count += 1
            else:
                if recent_close[i + 1] < recent_close[i]:
                    trend_count += 1
        
        strength = trend_count / (lookback - 1) if lookback > 1 else 0
        return strength
    
    def get_previous_high_low(
        self,
        high: np.ndarray,
        low: np.ndarray,
        lookback: int = 20
    ) -> Tuple[float, float]:
        """
        取得前期高低點
        
        Returns:
            (previous_high, previous_low)
        """
        if len(high) < lookback:
            lookback = len(high)
        
        prev_high = np.max(high[-lookback:-1]) if len(high) > 1 else high[-1]
        prev_low = np.min(low[-lookback:-1]) if len(low) > 1 else low[-1]
        
        return prev_high, prev_low


# 全域指標計算器實例
indicators = Indicators()
