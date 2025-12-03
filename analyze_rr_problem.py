"""
風報比問題分析腳本
直接計算並展示原策略的風報比問題
"""
import numpy as np

def analyze_mean_reversion_rr():
    """分析 Mean Reversion 策略的風報比問題"""
    
    print("=" * 70)
    print("Mean Reversion 策略風報比分析")
    print("=" * 70)
    
    # 假設 Bollinger Bands 參數
    bb_lower = 100.0
    bb_middle = 105.0
    bb_upper = 110.0
    band_width = bb_upper - bb_lower  # = 10
    
    print(f"\n假設 Bollinger Bands:")
    print(f"  下軌: ${bb_lower}")
    print(f"  中軌: ${bb_middle}")
    print(f"  上軌: ${bb_upper}")
    print(f"  帶寬: ${band_width}")
    
    # 原策略配置
    stop_loss_bb_multiplier = 0.5  # 止損在下軌下方 50% 帶寬
    
    print("\n" + "-" * 70)
    print("場景 1: 極端超賣反彈 (價格跌破下軌)")
    print("-" * 70)
    
    # 價格在下軌下方進場
    entry_price_1 = bb_lower * 0.99  # = 99
    stop_loss_1 = bb_lower - (band_width * stop_loss_bb_multiplier)  # = 100 - 5 = 95
    take_profit_1 = bb_middle  # = 105
    
    stop_distance_1 = entry_price_1 - stop_loss_1  # = 99 - 95 = 4
    profit_distance_1 = take_profit_1 - entry_price_1  # = 105 - 99 = 6
    rr_1 = profit_distance_1 / stop_distance_1  # = 6/4 = 1.5
    
    print(f"  進場價: ${entry_price_1:.2f}")
    print(f"  止損價: ${stop_loss_1:.2f} (距離: ${stop_distance_1:.2f}, {stop_distance_1/entry_price_1*100:.2f}%)")
    print(f"  止盈價: ${take_profit_1:.2f} (距離: ${profit_distance_1:.2f}, {profit_distance_1/entry_price_1*100:.2f}%)")
    print(f"  風報比: 1:{rr_1:.2f} ✓ 看起來還行")
    
    print("\n" + "-" * 70)
    print("場景 2: 中軌反彈 (BB Position = 0.4)")
    print("-" * 70)
    
    # BB Position 0.4 表示價格在 40% 位置
    bb_position_2 = 0.4
    entry_price_2 = bb_lower + (band_width * bb_position_2)  # = 100 + 4 = 104
    stop_loss_2 = bb_lower - (band_width * stop_loss_bb_multiplier)  # = 95
    take_profit_2 = bb_middle  # = 105
    
    stop_distance_2 = entry_price_2 - stop_loss_2  # = 104 - 95 = 9
    profit_distance_2 = take_profit_2 - entry_price_2  # = 105 - 104 = 1
    rr_2 = profit_distance_2 / stop_distance_2  # = 1/9 = 0.11
    
    print(f"  進場價: ${entry_price_2:.2f} (BB Position = {bb_position_2})")
    print(f"  止損價: ${stop_loss_2:.2f} (距離: ${stop_distance_2:.2f}, {stop_distance_2/entry_price_2*100:.2f}%)")
    print(f"  止盈價: ${take_profit_2:.2f} (距離: ${profit_distance_2:.2f}, {profit_distance_2/entry_price_2*100:.2f}%)")
    print(f"  風報比: 1:{rr_2:.2f} ✗✗✗ 嚴重問題！賺1塊要冒9塊風險！")
    
    print("\n" + "-" * 70)
    print("場景 3: 中軌反彈 (BB Position = 0.45 - 邊界情況)")
    print("-" * 70)
    
    bb_position_3 = 0.45
    entry_price_3 = bb_lower + (band_width * bb_position_3)  # = 100 + 4.5 = 104.5
    stop_loss_3 = bb_lower - (band_width * stop_loss_bb_multiplier)  # = 95
    take_profit_3 = bb_middle  # = 105
    
    stop_distance_3 = entry_price_3 - stop_loss_3  # = 104.5 - 95 = 9.5
    profit_distance_3 = take_profit_3 - entry_price_3  # = 105 - 104.5 = 0.5
    rr_3 = profit_distance_3 / stop_distance_3
    
    print(f"  進場價: ${entry_price_3:.2f} (BB Position = {bb_position_3})")
    print(f"  止損價: ${stop_loss_3:.2f} (距離: ${stop_distance_3:.2f}, {stop_distance_3/entry_price_3*100:.2f}%)")
    print(f"  止盈價: ${take_profit_3:.2f} (距離: ${profit_distance_3:.2f}, {profit_distance_3/entry_price_3*100:.2f}%)")
    print(f"  風報比: 1:{rr_3:.2f} ✗✗✗ 更糟！賺0.5塊要冒9.5塊風險！")
    
    print("\n" + "=" * 70)
    print("問題總結")
    print("=" * 70)
    print("""
原策略中軌反彈的進場條件是 BB Position 在 0.2-0.45 之間。
這意味著進場時價格可能已經非常接近中軌（目標止盈點）。

問題核心：
1. 止盈目標固定為中軌，無論進場位置在哪
2. 止損固定在下軌下方 50% 帶寬
3. 當 BB Position = 0.4 時，止盈空間只有 1%，但止損空間有 9%

這導致：
- 風報比可能低至 1:0.1（賺1塊冒10塊風險）
- 即使勝率 90%，長期依然虧損
- 這就是你觀察到「止盈賺 $0.1，止損虧 $1」的原因
""")
    
    print("\n" + "=" * 70)
    print("模擬長期表現")
    print("=" * 70)
    
    # 模擬 100 筆交易，假設原策略參數
    np.random.seed(42)
    
    # 場景：大部分進場在 BB Position 0.3-0.45
    trades_original = []
    for _ in range(100):
        bb_pos = np.random.uniform(0.2, 0.45)  # 原策略進場範圍
        entry = bb_lower + band_width * bb_pos
        stop_loss = bb_lower - band_width * 0.5
        take_profit = bb_middle
        
        stop_dist = entry - stop_loss
        profit_dist = take_profit - entry
        
        # 假設 70% 勝率（原策略表現）
        if np.random.random() < 0.70:
            pnl = profit_dist
        else:
            pnl = -stop_dist
        
        trades_original.append(pnl)
    
    print(f"\n原策略模擬 (100 筆交易, 70% 勝率):")
    print(f"  總盈虧: ${sum(trades_original):.2f}")
    print(f"  平均每筆: ${np.mean(trades_original):.2f}")
    print(f"  勝率: {len([t for t in trades_original if t > 0]):.0f}%")
    
    # V2 策略：確保最小 1:1.5 風報比
    trades_v2 = []
    skipped = 0
    for _ in range(100):
        bb_pos = np.random.uniform(0.05, 0.15)  # V2 只在極端位置進場
        entry = bb_lower + band_width * bb_pos
        
        # V2 使用 ATR 動態止損
        atr = entry * 0.02  # 假設 ATR 為價格的 2%
        stop_loss = entry - atr * 1.5
        
        # 止盈要滿足最小 1.5 風報比
        stop_dist = entry - stop_loss
        min_take_profit = entry + stop_dist * 1.5
        
        if min_take_profit > bb_middle:
            take_profit = min_take_profit
        else:
            take_profit = bb_middle
            
        profit_dist = take_profit - entry
        rr = profit_dist / stop_dist
        
        # 風報比檢查
        if rr < 1.2:
            skipped += 1
            continue
        
        # 假設勝率降到 50%（更嚴格的條件）
        if np.random.random() < 0.50:
            pnl = profit_dist
        else:
            pnl = -stop_dist
        
        trades_v2.append(pnl)
    
    print(f"\nV2 策略模擬 ({len(trades_v2)} 筆交易, 50% 勝率):")
    print(f"  跳過低風報比交易: {skipped} 筆")
    print(f"  總盈虧: ${sum(trades_v2):.2f}")
    print(f"  平均每筆: ${np.mean(trades_v2):.2f}" if trades_v2 else "N/A")
    
    print("\n" + "=" * 70)
    print("結論")
    print("=" * 70)
    print("""
即使 V2 策略的勝率從 70% 降到 50%，由於風報比改善，
長期表現反而更好。這證明了：

風報比 > 勝率

修正建議：
1. 移除中軌反彈邏輯（風報比太差）
2. 只在極端超賣/超買位置進場
3. 使用 ATR 動態止損，確保最小 1:1.5 風報比
4. 移除過早出場條件（RSI 回中性）
""")


if __name__ == "__main__":
    analyze_mean_reversion_rr()
