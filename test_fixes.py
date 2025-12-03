"""
測試腳本：驗證 bug 修復
"""
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# 設置環境
import sys
sys.path.insert(0, '/home/claude/lighter-quant-bot')

from config import settings, SignalType, StrategyType, MarketRegime


def generate_mock_data(count: int = 100, trend: str = "up") -> pd.DataFrame:
    """生成模擬 K 線數據"""
    np.random.seed(42)
    
    base_price = 50000.0
    timestamps = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    
    current_price = base_price
    current_time = datetime.utcnow() - timedelta(minutes=count * 5)
    
    for i in range(count):
        if trend == "up":
            change = np.random.normal(0.001, 0.002) * current_price
        elif trend == "down":
            change = np.random.normal(-0.001, 0.002) * current_price
        else:
            change = np.random.normal(0, 0.002) * current_price
        
        open_price = current_price
        close_price = current_price + change
        high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, 0.001)))
        low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, 0.001)))
        volume = abs(np.random.normal(100, 30))
        
        timestamps.append(current_time)
        opens.append(open_price)
        highs.append(high_price)
        lows.append(low_price)
        closes.append(close_price)
        volumes.append(volume)
        
        current_price = close_price
        current_time += timedelta(minutes=5)
    
    return pd.DataFrame({
        'timestamp': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })


def test_indicators():
    """測試指標計算"""
    print("\n" + "=" * 50)
    print("測試 1: 指標計算 (NaN 處理)")
    print("=" * 50)
    
    from core.indicators import Indicators
    
    ind = Indicators()
    
    # 測試正常數據
    df_fast = generate_mock_data(100)
    df_slow = generate_mock_data(100)
    
    try:
        result = ind.calculate_all(df_fast, df_slow)
        print(f"✅ 正常數據測試通過")
        print(f"   - 當前價格: {result.current_price:.2f}")
        print(f"   - RSI: {result.rsi:.2f}")
        print(f"   - ADX: {result.adx:.2f}")
        print(f"   - Supertrend Fast: {result.supertrend_fast.direction.name}")
    except Exception as e:
        print(f"❌ 正常數據測試失敗: {e}")
    
    # 測試數據不足
    df_short = generate_mock_data(10)
    try:
        result = ind.calculate_all(df_short, df_short)
        print(f"❌ 數據不足測試失敗: 應該拋出異常")
    except ValueError as e:
        print(f"✅ 數據不足測試通過: {e}")
    except Exception as e:
        print(f"❌ 數據不足測試失敗: 未預期的異常 {e}")


def test_momentum_strategy():
    """測試 Momentum 策略"""
    print("\n" + "=" * 50)
    print("測試 2: Momentum 策略 (前高前低邏輯)")
    print("=" * 50)
    
    from strategies.momentum import MomentumStrategy
    
    strategy = MomentumStrategy()
    
    # 測試前高前低更新
    strategy._update_prev_high_low(100, 90)
    strategy._update_prev_high_low(105, 92)
    strategy._update_prev_high_low(103, 91)
    
    print(f"   - lookback_highs 長度: {len(strategy.lookback_highs)}")
    print(f"   - lookback_lows 長度: {len(strategy.lookback_lows)}")
    print(f"   - prev_high: {strategy.prev_high}")
    print(f"   - prev_low: {strategy.prev_low}")
    
    # 確保前高前低是基於滑動窗口
    if strategy.prev_high == 105 and strategy.prev_low == 90:
        print(f"✅ 前高前低計算正確 (排除最新值)")
    else:
        print(f"❌ 前高前低計算錯誤")
    
    # 測試重置
    strategy.reset()
    if strategy.prev_high is None and len(strategy.lookback_highs) == 0:
        print(f"✅ 重置功能正常")
    else:
        print(f"❌ 重置功能異常")


def test_position_manager():
    """測試倉位管理"""
    print("\n" + "=" * 50)
    print("測試 3: 倉位管理 (最小止損距離)")
    print("=" * 50)
    
    from core.position_manager import PositionManager
    
    pm = PositionManager()
    
    # 測試正常止損距離
    result = pm.calculate_position_size(
        balance=1000,
        leverage=2,
        current_price=50000,
        stop_loss_price=49000,  # 2% 止損
        signal_type=SignalType.LONG,
        strength=0.5
    )
    
    if result.size > 0:
        print(f"✅ 正常止損距離測試通過")
        print(f"   - 倉位大小: ${result.size:.2f}")
        print(f"   - 止損距離: {result.stop_distance_percent*100:.2f}%")
    else:
        print(f"❌ 正常止損距離測試失敗: 倉位為 0")
    
    # 測試過小止損距離
    result = pm.calculate_position_size(
        balance=1000,
        leverage=2,
        current_price=50000,
        stop_loss_price=49990,  # 0.02% 止損 (太小)
        signal_type=SignalType.LONG,
        strength=0.5
    )
    
    if result.size == 0:
        print(f"✅ 過小止損距離測試通過: 正確拒絕")
    else:
        print(f"❌ 過小止損距離測試失敗: 應該返回 0")


def test_lighter_client_dry_run():
    """測試 Lighter 客戶端 dry run 模式"""
    print("\n" + "=" * 50)
    print("測試 4: Lighter 客戶端 (Dry Run 模式)")
    print("=" * 50)
    
    async def run_test():
        from exchange.lighter_client import LighterClient
        
        client = LighterClient()
        
        # 設置模擬價格
        client.set_simulated_price(50000)
        
        # 測試開倉
        result = await client.create_market_order(
            signal_type=SignalType.LONG,
            amount=0.01,
            reduce_only=False
        )
        
        if result.success and result.filled_price == 50000:
            print(f"✅ 開倉測試通過")
            print(f"   - filled_price: {result.filled_price}")
        else:
            print(f"❌ 開倉測試失敗")
        
        # 檢查持倉
        account = await client.get_account_info()
        if len(account.positions) == 1 and account.positions[0].size == 0.01:
            print(f"✅ 持倉記錄正確")
        else:
            print(f"❌ 持倉記錄異常")
        
        # 模擬價格上漲
        client.set_simulated_price(51000)
        
        # 測試平倉
        result = await client.close_position()
        
        if result.success and result.filled_price == 51000:
            print(f"✅ 平倉測試通過")
        else:
            print(f"❌ 平倉測試失敗")
        
        # 檢查餘額更新
        account = await client.get_account_info()
        # 盈利 = (51000 - 50000) * 0.01 = 10
        expected_balance = 1000 + 10
        if abs(account.balance - expected_balance) < 0.01:
            print(f"✅ 餘額更新正確: ${account.balance:.2f}")
        else:
            print(f"❌ 餘額更新異常: 預期 ${expected_balance:.2f}, 實際 ${account.balance:.2f}")
    
    asyncio.run(run_test())


def test_mean_reversion_logic():
    """測試 Mean Reversion 中軌反彈邏輯"""
    print("\n" + "=" * 50)
    print("測試 5: Mean Reversion 中軌反彈邏輯")
    print("=" * 50)
    
    from strategies.mean_reversion import MeanReversionStrategy
    from core.indicators import BollingerResult
    
    strategy = MeanReversionStrategy()
    
    # 模擬從下方反彈的情況
    bb = BollingerResult(
        upper=51000,
        middle=50000,
        lower=49000,
        width=0.04,
        position=0.35  # 在下半部
    )
    
    rsi = 38  # RSI 在 30-45 之間
    current_price = 49700
    
    signal = strategy._check_mid_band_bounce(rsi, bb, current_price)
    
    if signal is not None and signal.signal_type == SignalType.LONG:
        print(f"✅ 下方反彈做多測試通過")
        print(f"   - 信號類型: {signal.signal_type.value}")
        print(f"   - 止盈目標: {signal.take_profit:.2f} (中軌)")
    else:
        print(f"❌ 下方反彈做多測試失敗")
    
    # 模擬從上方回落的情況
    bb2 = BollingerResult(
        upper=51000,
        middle=50000,
        lower=49000,
        width=0.04,
        position=0.65  # 在上半部
    )
    
    rsi2 = 62  # RSI 在 55-70 之間
    current_price2 = 50300
    
    signal2 = strategy._check_mid_band_bounce(rsi2, bb2, current_price2)
    
    if signal2 is not None and signal2.signal_type == SignalType.SHORT:
        print(f"✅ 上方回落做空測試通過")
        print(f"   - 信號類型: {signal2.signal_type.value}")
        print(f"   - 止盈目標: {signal2.take_profit:.2f} (中軌)")
    else:
        print(f"❌ 上方回落做空測試失敗")


def main():
    """運行所有測試"""
    print("\n" + "=" * 60)
    print("Lighter Quant Bot - Bug 修復測試")
    print("=" * 60)
    
    test_indicators()
    test_momentum_strategy()
    test_position_manager()
    test_lighter_client_dry_run()
    test_mean_reversion_logic()
    
    print("\n" + "=" * 60)
    print("測試完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
