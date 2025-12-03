"""
整合測試：模擬完整交易循環
"""
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, '/home/claude/lighter-quant-bot')

from config import settings, SignalType, StrategyType, MarketRegime
from core.indicators import indicators, IndicatorValues
from core.market_regime import market_detector
from core.risk_manager import RiskManager
from core.position_manager import position_manager
from strategies.momentum import momentum_strategy
from strategies.mean_reversion import mean_reversion_strategy
from exchange.lighter_client import lighter_client


def generate_trending_data(count: int = 100) -> pd.DataFrame:
    """生成趨勢市場數據"""
    np.random.seed(123)
    
    base_price = 50000.0
    data = []
    current_price = base_price
    current_time = datetime.now(timezone.utc) - timedelta(minutes=count * 5)
    
    for i in range(count):
        # 上升趨勢
        change = np.random.normal(0.002, 0.001) * current_price
        
        open_price = current_price
        close_price = current_price + change
        high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, 0.002)))
        low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, 0.001)))
        
        data.append({
            'timestamp': current_time,
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': abs(np.random.normal(100, 30))
        })
        
        current_price = close_price
        current_time += timedelta(minutes=5)
    
    return pd.DataFrame(data)


def generate_ranging_data(count: int = 100) -> pd.DataFrame:
    """生成震盪市場數據"""
    np.random.seed(456)
    
    base_price = 50000.0
    data = []
    current_price = base_price
    current_time = datetime.now(timezone.utc) - timedelta(minutes=count * 5)
    
    for i in range(count):
        # 震盪：圍繞中心價格波動
        target = base_price + np.sin(i / 10) * 500  # 震幅 ±500
        change = (target - current_price) * 0.1 + np.random.normal(0, 0.001) * current_price
        
        open_price = current_price
        close_price = current_price + change
        high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, 0.001)))
        low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, 0.001)))
        
        data.append({
            'timestamp': current_time,
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': abs(np.random.normal(100, 30))
        })
        
        current_price = close_price
        current_time += timedelta(minutes=5)
    
    return pd.DataFrame(data)


async def test_full_trading_cycle():
    """測試完整交易循環"""
    print("\n" + "=" * 60)
    print("整合測試：完整交易循環")
    print("=" * 60)
    
    # 初始化
    risk_manager = RiskManager(initial_balance=1000.0)
    
    print("\n1. 測試趨勢市場 (Momentum 策略)")
    print("-" * 40)
    
    # 生成趨勢數據
    df_fast = generate_trending_data(100)
    df_slow = generate_trending_data(100)
    
    # 計算指標
    try:
        indicator_values = indicators.calculate_all(df_fast, df_slow)
        print(f"   ✅ 指標計算成功")
        print(f"      - 當前價格: ${indicator_values.current_price:.2f}")
        print(f"      - RSI: {indicator_values.rsi:.2f}")
        print(f"      - ADX: {indicator_values.adx:.2f}")
        print(f"      - ATR%: {indicator_values.atr_percent*100:.2f}%")
    except Exception as e:
        print(f"   ❌ 指標計算失敗: {e}")
        return
    
    # 判斷市場狀態
    market_state = market_detector.detect(indicator_values)
    print(f"   市場狀態: {market_state.regime.value}")
    print(f"   狀態描述: {market_state.description}")
    
    # 檢查風險
    can_trade, reason = risk_manager.can_trade()
    print(f"   可以交易: {can_trade}")
    if not can_trade:
        print(f"   原因: {reason}")
    
    # 計算槓桿
    leverage = risk_manager.calculate_leverage()
    print(f"   動態槓桿: {leverage:.2f}x")
    
    # 檢查 Momentum 訊號
    momentum_strategy.reset()
    for i in range(5):  # 更新幾次前高前低
        momentum_strategy._update_prev_high_low(
            df_fast['high'].iloc[-(i+2)],
            df_fast['low'].iloc[-(i+2)]
        )
    
    signal = momentum_strategy.check_entry(indicator_values, market_state)
    if signal:
        print(f"   ✅ Momentum 訊號: {signal.signal_type.value}")
        print(f"      - 進場價: ${signal.entry_price:.2f}")
        print(f"      - 止損價: ${signal.stop_loss:.2f}")
        print(f"      - 止盈價: ${signal.take_profit:.2f}")
        print(f"      - 強度: {signal.strength:.2f}")
        
        # 計算倉位
        pos_size = position_manager.calculate_position_size(
            balance=1000,
            leverage=leverage,
            current_price=signal.entry_price,
            stop_loss_price=signal.stop_loss,
            signal_type=signal.signal_type,
            strength=signal.strength
        )
        print(f"      - 建議倉位: ${pos_size.size:.2f}")
    else:
        print(f"   ⚠️ 無 Momentum 訊號 (市場狀態: {market_state.regime.value})")
    
    print("\n2. 測試震盪市場 (Mean Reversion 策略)")
    print("-" * 40)
    
    # 生成震盪數據
    df_fast_range = generate_ranging_data(100)
    df_slow_range = generate_ranging_data(100)
    
    # 計算指標
    try:
        indicator_values_range = indicators.calculate_all(df_fast_range, df_slow_range)
        print(f"   ✅ 指標計算成功")
        print(f"      - 當前價格: ${indicator_values_range.current_price:.2f}")
        print(f"      - RSI: {indicator_values_range.rsi:.2f}")
        print(f"      - BB Position: {indicator_values_range.bollinger.position:.2f}")
    except Exception as e:
        print(f"   ❌ 指標計算失敗: {e}")
        return
    
    # 判斷市場狀態
    market_state_range = market_detector.detect(indicator_values_range)
    print(f"   市場狀態: {market_state_range.regime.value}")
    print(f"   狀態描述: {market_state_range.description}")
    
    # 檢查 Mean Reversion 訊號
    mean_reversion_strategy.reset()
    signal_mr = mean_reversion_strategy.check_entry(indicator_values_range, market_state_range)
    if signal_mr:
        print(f"   ✅ Mean Reversion 訊號: {signal_mr.signal_type.value}")
        print(f"      - 進場價: ${signal_mr.entry_price:.2f}")
        print(f"      - 止損價: ${signal_mr.stop_loss:.2f}")
        print(f"      - 止盈價: ${signal_mr.take_profit:.2f}")
    else:
        print(f"   ⚠️ 無 Mean Reversion 訊號 (可能未達到極端條件)")
    
    print("\n3. 測試 Dry Run 交易流程")
    print("-" * 40)
    
    # 設置模擬價格
    lighter_client.set_simulated_price(50000)
    
    # 開倉
    result = await lighter_client.create_market_order(
        signal_type=SignalType.LONG,
        amount=0.02,
        reduce_only=False
    )
    print(f"   開倉結果: {'✅ 成功' if result.success else '❌ 失敗'}")
    print(f"   成交價: ${result.filled_price:.2f}")
    
    # 檢查持倉
    account = await lighter_client.get_account_info()
    print(f"   當前餘額: ${account.balance:.2f}")
    print(f"   持倉數量: {account.positions[0].size if account.positions else 0}")
    
    # 模擬價格變動
    lighter_client.set_simulated_price(51000)
    
    # 檢查未實現盈虧
    account = await lighter_client.get_account_info()
    if account.positions:
        print(f"   未實現盈虧: ${account.positions[0].unrealized_pnl:.2f}")
    
    # 平倉
    result = await lighter_client.close_position()
    print(f"   平倉結果: {'✅ 成功' if result.success else '❌ 失敗'}")
    
    # 最終餘額
    account = await lighter_client.get_account_info()
    print(f"   最終餘額: ${account.balance:.2f}")
    print(f"   盈虧: ${account.balance - 1000:.2f}")
    
    print("\n4. 測試風險管理")
    print("-" * 40)
    
    # 模擬幾筆交易
    risk_manager.record_trade(pnl=20, strategy="momentum")
    risk_manager.record_trade(pnl=-10, strategy="momentum")
    risk_manager.record_trade(pnl=15, strategy="mean_reversion")
    
    metrics = risk_manager.get_metrics()
    print(f"   總交易次數: {metrics.total_trades}")
    print(f"   勝率: {metrics.win_rate*100:.1f}%")
    print(f"   當前槓桿: {metrics.current_leverage:.2f}x")
    print(f"   當前回撤: {metrics.current_drawdown*100:.2f}%")
    print(f"   日內盈虧: {metrics.daily_pnl*100:.2f}%")
    
    # 模擬連續虧損
    print("\n   模擬連續虧損...")
    risk_manager.record_trade(pnl=-30, strategy="momentum")
    risk_manager.record_trade(pnl=-25, strategy="momentum")
    
    metrics = risk_manager.get_metrics()
    print(f"   連續虧損次數: {metrics.consecutive_losses}")
    print(f"   調整後槓桿: {metrics.current_leverage:.2f}x")
    
    can_trade, reason = risk_manager.can_trade()
    if not can_trade:
        print(f"   ⚠️ 交易限制: {reason}")
    
    print("\n" + "=" * 60)
    print("整合測試完成!")
    print("=" * 60)


def main():
    asyncio.run(test_full_trading_cycle())


if __name__ == "__main__":
    main()
