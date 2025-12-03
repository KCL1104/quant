"""
策略配置設定
所有參數集中管理，方便調整優化
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional
from enum import Enum


class MarketRegime(str, Enum):
    """市場狀態"""
    TRENDING = "trending"
    RANGING = "ranging"
    UNKNOWN = "unknown"


class SignalType(str, Enum):
    """訊號類型"""
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class StrategyType(str, Enum):
    """策略類型"""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"


class TimeframeConfig(BaseSettings):
    """時間框架配置"""
    fast_tf: str = "5m"           # 快速時間框架
    slow_tf: str = "15m"          # 慢速時間框架
    candle_count: int = 100       # 每個時間框架需要的K線數量


class SupertrendConfig(BaseSettings):
    """Supertrend 指標配置"""
    period: int = 10              # ATR 計算週期
    multiplier: float = 3.0       # ATR 乘數


class EMAConfig(BaseSettings):
    """EMA 指標配置"""
    fast_period: int = 20         # 快速 EMA 週期
    slow_period: int = 50         # 慢速 EMA 週期


class RSIConfig(BaseSettings):
    """RSI 指標配置"""
    period: int = 14              # RSI 週期
    overbought: float = 70.0      # 超買線
    oversold: float = 30.0        # 超賣線


class BollingerConfig(BaseSettings):
    """Bollinger Bands 配置"""
    period: int = 20              # 中軌 SMA 週期
    std_dev: float = 2.0          # 標準差乘數


class ADXConfig(BaseSettings):
    """ADX 指標配置"""
    period: int = 14              # ADX 週期
    threshold: float = 25.0       # 趨勢/震盪分界線


class ATRConfig(BaseSettings):
    """ATR 指標配置"""
    period: int = 14              # ATR 週期
    trending_threshold: float = 0.02    # 趨勢市 ATR% 閾值
    ranging_threshold: float = 0.015    # 震盪市 ATR% 閾值


class LeverageConfig(BaseSettings):
    """槓桿配置"""
    base_leverage: float = 2.0    # 基準槓桿
    max_leverage: float = 5.0     # 最大槓桿
    min_leverage: float = 1.0     # 最小槓桿


class RiskConfig(BaseSettings):
    """風險管理配置"""
    risk_per_trade: float = 0.02          # 單筆風險 2%
    max_daily_loss: float = 0.05          # 日內最大虧損 5%
    max_drawdown: float = 0.15            # 最大回撤 15%
    max_consecutive_losses: int = 5       # 最大連續虧損次數
    max_position_ratio: float = 0.5       # 最大倉位比例 (槓桿額度的 50%)
    
    # 動態槓桿調整
    win_rate_boost_threshold: float = 0.6       # 勝率 > 60% 時增加槓桿
    win_rate_reduce_threshold: float = 0.4      # 勝率 < 40% 時降低槓桿
    win_rate_boost_multiplier: float = 1.2      # 勝率好時的槓桿乘數
    win_rate_reduce_multiplier: float = 0.7     # 勝率差時的槓桿乘數
    
    consecutive_loss_threshold_2: int = 2       # 連虧 2 次觸發
    consecutive_loss_threshold_3: int = 3       # 連虧 3 次觸發
    consecutive_loss_multiplier: float = 0.8    # 連虧時的槓桿乘數
    
    consecutive_win_threshold: int = 3          # 連勝 3 次觸發
    consecutive_win_multiplier: float = 1.1     # 連勝時的槓桿乘數
    
    weekly_profit_protection: float = 0.1       # 週獲利 > 10% 時保護


class MomentumConfig(BaseSettings):
    """Momentum 策略配置"""
    strength_lookback: int = 10           # 動能強度計算回看週期
    min_strength: float = 0.4             # 最小動能強度
    strong_strength: float = 0.7          # 強動能閾值
    
    # 止盈止損
    risk_reward_ratio: float = 2.0        # 風報比 1:2
    
    # 倉位調整
    strong_position_multiplier: float = 1.2   # 強動能倉位乘數
    weak_position_multiplier: float = 0.8     # 弱動能倉位乘數


class MeanReversionConfig(BaseSettings):
    """Mean Reversion 策略配置"""
    # BB Position 閾值
    bb_oversold_position: float = 0.1     # 超賣 BB Position
    bb_overbought_position: float = 0.9   # 超買 BB Position
    
    # 中軌反彈
    mid_band_tolerance: float = 0.1       # 中軌容差 (帶寬的 10%)
    mid_rsi_low: float = 45.0             # 中軌反彈 RSI 低閾值
    mid_rsi_high: float = 55.0            # 中軌反彈 RSI 高閾值
    mid_bb_position_low: float = 0.4      # 中軌反彈 BB Position 低閾值
    mid_bb_position_high: float = 0.6     # 中軌反彈 BB Position 高閾值
    
    # 止損
    stop_loss_bb_multiplier: float = 0.5  # 止損在 BB 下軌下方 50% 帶寬
    
    # 時間止損
    max_holding_periods: int = 16         # 最大持倉週期數 (5m * 16 = 80分鐘)


class TradingConfig(BaseSettings):
    """交易配置"""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

    # Lighter 設定
    api_key: str = Field(default="", validation_alias="LIGHTER_API_KEY")
    private_key: str = Field(default="", validation_alias="LIGHTER_PRIVATE_KEY")
    host: str = Field(default="https://mainnet.zklighter.elliot.ai", validation_alias="LIGHTER_HOST")
    
    # 交易對列表 (格式: "SYMBOL:ID,SYMBOL:ID")
    # 例如: "BTC:1,ETH:0,SOL:2"
    # 注意：這裡使用 MARKETS 作為環境變數名稱，對應 markets_str 字段
    markets_str: str = Field(default="ETH:0,BNB:25", validation_alias="MARKETS")
    
    # 兼容舊配置 (單一市場)
    market_id: int = 0                    
    market_symbol: str = "ETH"        
    
    @property
    def markets(self) -> list[tuple[str, int]]:
        """解析市場配置"""
        try:
            if not self.markets_str:
                return [("ETH", 0), ("BNB", 25)]
                
            result = []
            for m in self.markets_str.split(','):
                if ':' in m:
                    symbol, id_str = m.split(':')
                    result.append((symbol.strip(), int(id_str)))
                else:
                    # 假設只有 symbol，ID 需要另外查找或為預設
                    # 這裡可以擴展查找邏輯，暫時默認為 0
                    symbol = m.strip()
                    if symbol == "ETH": result.append(("ETH", 0))
                    elif symbol == "BTC": result.append(("BTC", 1))
                    elif symbol == "SOL": result.append(("SOL", 2))
                    elif symbol == "BNB": result.append(("BNB", 25))
                    else: result.append((symbol, 0))
            return result
        except Exception:
            return [("ETH", 0), ("BNB", 25)]
    
    # 最小交易金額
    min_trade_amount: float = 10.0        # 最小交易金額 USD
    
    # 訂單設定
    slippage_tolerance: float = 0.005     # 滑點容差 0.5%
    order_timeout: int = 30               # 訂單超時秒數
    
    # 冷卻期
    cooldown_after_loss: int = 300        # 虧損後冷卻期 (秒)
    cooldown_after_consecutive_loss: int = 1800  # 連續虧損後冷卻期 (秒)


class Settings(BaseSettings):
    """主配置類"""
    # 子配置
    timeframe: TimeframeConfig = TimeframeConfig()
    supertrend: SupertrendConfig = SupertrendConfig()
    ema: EMAConfig = EMAConfig()
    rsi: RSIConfig = RSIConfig()
    bollinger: BollingerConfig = BollingerConfig()
    adx: ADXConfig = ADXConfig()
    atr: ATRConfig = ATRConfig()
    leverage: LeverageConfig = LeverageConfig()
    risk: RiskConfig = RiskConfig()
    momentum: MomentumConfig = MomentumConfig()
    mean_reversion: MeanReversionConfig = MeanReversionConfig()
    trading: TradingConfig = TradingConfig()
    
    # 全域設定
    debug: bool = Field(default=False, env="DEBUG")
    dry_run: bool = Field(default=True, env="DRY_RUN")  # 模擬交易模式
    
    class Config:
        # Pydantic V1 style config, kept for compatibility if needed, but V2 uses model_config
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# 全域配置實例
settings = Settings()
