"""
快速測試腳本
驗證指標計算和策略邏輯是否正常
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# 測試配置載入
print("1. 測試配置載入...")
from config import settings, MarketRegime, SignalType, StrategyType
print(f"   快速 TF: {settings.timeframe.fast_tf}")
print(f"   慢速 TF: {settings.timeframe.slow_tf}")
print(f"   Supertrend Period: {settings.supertrend.period}")
print(f"   基礎槓桿: {settings.leverage.base_leverage}x")
print("   ✅ 配置載入成功\n")

# 生成測試數據
print("2. 生成測試數據...")
np.random.seed(42)
n = 150

timestamps = pd.date_range(end=datetime.utcnow(), periods=n, freq='5min')
base_price = 50000

# 生成帶趨勢的價格
prices = [base_price]
for i in range(n - 1):
    change = np.random.normal(0.0001, 0.002) * prices[-1]
    prices.append(prices[-1] + change)

df_fast = pd.DataFrame({
    'timestamp': timestamps,
    'open': prices,
    'high': [p * (1 + abs(np.random.normal(0, 0.001))) for p in prices],
    'low': [p * (1 - abs(np.random.normal(0, 0.001))) for p in prices],
    'close': prices,
    'volume': [abs(np.random.normal(100, 30)) for _ in range(n)]
})

# 15分鐘數據
df_slow = df_fast.copy()
df_slow['group'] = df_slow.index // 3
df_slow = df_slow.groupby('group').agg({
    'timestamp': 'first',
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
}).reset_index(drop=True)

print(f"   5m K線數量: {len(df_fast)}")
print(f"   15m K線數量: {len(df_slow)}")
print("   ✅ 測試數據生成成功\n")

# 測試指標計算
print("3. 測試指標計算...")
from core import indicators, TrendDirection

high = df_fast['high'].values
low = df_fast['low'].values
close = df_fast['close'].values

# Supertrend
st_result = indicators.get_supertrend_result(high, low, close)
print(f"   Supertrend 值: {st_result.value:.2f}")
print(f"   Supertrend 方向: {st_result.direction.name}")
print(f"   上軌: {st_result.upper_band:.2f}")
print(f"   下軌: {st_result.lower_band:.2f}")

# EMA
ema20 = indicators.calculate_ema(close, 20)
ema50 = indicators.calculate_ema(close, 50)
print(f"   EMA20: {ema20[-1]:.2f}")
print(f"   EMA50: {ema50[-1]:.2f}")

# RSI
rsi = indicators.calculate_rsi(close)
print(f"   RSI: {rsi[-1]:.2f}")

# Bollinger Bands
bb_result = indicators.get_bollinger_result(close, close[-1])
print(f"   BB 上軌: {bb_result.upper:.2f}")
print(f"   BB 中軌: {bb_result.middle:.2f}")
print(f"   BB 下軌: {bb_result.lower:.2f}")
print(f"   BB Position: {bb_result.position:.2f}")

# ADX
high_slow = df_slow['high'].values
low_slow = df_slow['low'].values
close_slow = df_slow['close'].values
adx, plus_di, minus_di = indicators.calculate_adx(high_slow, low_slow, close_slow)
print(f"   ADX: {adx[-1]:.2f}")
print(f"   +DI: {plus_di[-1]:.2f}")
print(f"   -DI: {minus_di[-1]:.2f}")

# ATR
atr = indicators.calculate_atr(high, low, close)
atr_percent = atr[-1] / close[-1] * 100
print(f"   ATR: {atr[-1]:.2f} ({atr_percent:.2f}%)")

print("   ✅ 指標計算成功\n")

# 測試完整指標計算
print("4. 測試完整指標計算...")
indicator_values = indicators.calculate_all(df_fast, df_slow)
print(f"   當前價格: {indicator_values.current_price:.2f}")
print(f"   快速 Supertrend: {indicator_values.supertrend_fast.direction.name}")
print(f"   慢速 Supertrend: {indicator_values.supertrend_slow.direction.name}")
print("   ✅ 完整指標計算成功\n")

# 測試市場狀態判斷
print("5. 測試市場狀態判斷...")
from core import market_detector

market_state = market_detector.detect(indicator_values)
print(f"   市場狀態: {market_state.regime.value}")
print(f"   ADX: {market_state.adx_value:.2f}")
print(f"   ATR%: {market_state.atr_percent*100:.2f}%")
print(f"   信心度: {market_state.confidence:.2f}")
print(f"   描述: {market_state.description}")
print("   ✅ 市場狀態判斷成功\n")

# 測試風險管理
print("6. 測試風險管理...")
from core import RiskManager

risk_manager = RiskManager(initial_balance=1000.0)

# 模擬一些交易
risk_manager.record_trade(pnl=20.0, strategy="momentum")
risk_manager.record_trade(pnl=-15.0, strategy="momentum")
risk_manager.record_trade(pnl=30.0, strategy="mean_reversion")

metrics = risk_manager.get_metrics()
print(f"   總交易次數: {metrics.total_trades}")
print(f"   勝率: {metrics.win_rate*100:.1f}%")
print(f"   當前槓桿: {metrics.current_leverage:.2f}x")
print(f"   連勝: {metrics.consecutive_wins}")
print(f"   連虧: {metrics.consecutive_losses}")
print(f"   可交易: {metrics.can_trade}")
print("   ✅ 風險管理測試成功\n")

# 測試倉位計算
print("7. 測試倉位計算...")
from core import position_manager

position_size = position_manager.calculate_position_size(
    balance=1000.0,
    leverage=2.0,
    current_price=50000.0,
    stop_loss_price=49000.0,
    signal_type=SignalType.LONG,
    strength=0.7
)
print(f"   倉位大小: ${position_size.size:.2f}")
print(f"   基礎資產數量: {position_size.base_amount:.6f}")
print(f"   風險金額: ${position_size.risk_amount:.2f}")
print(f"   止損距離: {position_size.stop_distance_percent*100:.2f}%")
print("   ✅ 倉位計算測試成功\n")

# 測試策略
print("8. 測試策略訊號...")
from strategies import momentum_strategy, mean_reversion_strategy

# Momentum 策略
mom_signal = momentum_strategy.check_entry(indicator_values, market_state)
if mom_signal:
    print(f"   Momentum 訊號: {mom_signal.signal_type.value}")
    print(f"   進場價: {mom_signal.entry_price:.2f}")
    print(f"   止損: {mom_signal.stop_loss:.2f}")
    print(f"   止盈: {mom_signal.take_profit:.2f}")
else:
    print("   Momentum: 無訊號")

# Mean Reversion 策略
mr_signal = mean_reversion_strategy.check_entry(indicator_values, market_state)
if mr_signal:
    print(f"   Mean Reversion 訊號: {mr_signal.signal_type.value}")
    print(f"   進場價: {mr_signal.entry_price:.2f}")
    print(f"   止損: {mr_signal.stop_loss:.2f}")
    print(f"   止盈: {mr_signal.take_profit:.2f}")
else:
    print("   Mean Reversion: 無訊號")

print("   ✅ 策略測試完成\n")

# 測試日誌
print("9. 測試日誌系統...")
from utils import bot_logger, log_trade, log_signal, log_risk

log_signal(
    strategy="MOMENTUM",
    signal_type="LONG",
    price=50000.0,
    strength=0.8,
    reason="測試訊號"
)
print("   ✅ 日誌系統測試成功\n")

print("=" * 60)
print("所有測試通過! ✅")
print("=" * 60)
print("\n下一步:")
print("1. 複製 .env.example 為 .env 並填入 API Key")
print("2. 運行 python backtest.py 進行回測")
print("3. 確認無誤後運行 python main.py 開始交易")
