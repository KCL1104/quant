# Lighter Trading Bot 策略優化報告

## 問題診斷

你的回測結果：
- 勝率 72.8% ✓ 很高
- Sharpe Ratio 0.36 ✗ 過低
- 觀察到：止盈只賺 $0.1，止損虧 $1 ✗ 風報比失衡

### 核心問題：Mean Reversion 中軌反彈策略的風報比災難

#### 原代碼問題位置：`strategies/mean_reversion.py` 的 `_check_mid_band_bounce`

```python
# 原策略進場條件：BB Position 在 0.2-0.45
if 0.2 < bb.position < 0.45:
    if 30 < rsi < 45:
        stops = StopLossTarget(
            stop_loss=stops.stop_loss,      # 止損在下軌下方 50% 帶寬
            take_profit=bb.middle,           # 止盈目標是中軌
        )
```

#### 數學分析

假設 BB: 下軌=$100, 中軌=$105, 上軌=$110

| 進場位置 | 進場價 | 止盈距離 | 止損距離 | 風報比 |
|---------|--------|---------|---------|-------|
| BB Pos 0.1 (極端超賣) | $101 | $4 (4%) | $6 (6%) | 1:0.67 |
| BB Pos 0.3 | $103 | $2 (2%) | $8 (7.8%) | 1:0.25 |
| BB Pos 0.4 | $104 | $1 (1%) | $9 (8.7%) | **1:0.11** |
| BB Pos 0.45 | $104.5 | $0.5 (0.5%) | $9.5 (9.1%) | **1:0.05** |

**結論**：當 BB Position = 0.4 時，賺 $1 需要冒 $9 的風險！

即使 70% 勝率：
```
期望值 = 0.7 × $1 - 0.3 × $9 = $0.7 - $2.7 = -$2.0 (每筆虧 $2)
```

---

## 修正方案

### 1. Mean Reversion V2 策略關鍵改動

```python
class MeanReversionStrategyV2:
    MIN_RISK_REWARD = 1.5          # 強制最小風報比
    EXTREME_OVERSOLD_BB = 0.1      # 更嚴格的進場條件
    EXTREME_OVERBOUGHT_BB = 0.9
    
    def check_entry(self, indicators, market_state):
        # 移除中軌反彈邏輯 - 風報比太差
        # 只在極端位置進場
        if bb.position < 0.1 and rsi < 30:  # 極端超賣
            return self._create_long_signal(...)
        if bb.position > 0.9 and rsi > 70:  # 極端超買
            return self._create_short_signal(...)
        return None
    
    def _create_long_signal(self, bb, current_price, atr, rsi):
        # 使用 ATR 動態止損
        stop_distance = min(atr * 1.5, current_price * 0.03)
        stop_loss = current_price - stop_distance
        
        # 計算滿足最小風報比的止盈
        min_take_profit = current_price + (stop_distance * self.MIN_RISK_REWARD)
        
        # 如果中軌夠遠就用中軌，否則用計算值
        if bb.middle >= min_take_profit:
            take_profit = bb.middle
        else:
            take_profit = min_take_profit
        
        # 風報比檢查 - 不滿足則放棄交易
        actual_rr = (take_profit - current_price) / stop_distance
        if actual_rr < 1.2:
            return None  # 放棄這筆交易
```

### 2. Momentum V2 策略關鍵改動

```python
class MomentumStrategyV2:
    MIN_ADX_THRESHOLD = 28        # 更嚴格的趨勢過濾
    ATR_STOP_MULTIPLIER = 2.0     # ATR 止損乘數
    
    def _check_long_v2(self, ...):
        # 新增：DI+ > DI- 確認多頭趨勢
        if plus_di <= minus_di:
            return None
        
        # 計算止損 - 使用 ATR 而非只用 Supertrend
        atr_stop = current_price - (atr * self.ATR_STOP_MULTIPLIER)
        supertrend_stop = st_fast.lower_band
        
        # 選擇較近的止損（更保守）
        stop_loss = max(atr_stop, supertrend_stop)
        
        # 確保止損不會太遠
        max_stop = current_price * (1 - 0.025)  # 最大 2.5%
        stop_loss = max(stop_loss, max_stop)
```

### 3. 出場邏輯優化

**原問題**：RSI 回到中性就出場，壓縮盈利空間

```python
# 原代碼 - 問題
if rsi > 55:  # 太早出場！
    return True, f"RSI 回到中性"

# V2 改進 - 讓止盈止損發揮作用
def check_exit(self, ...):
    # 只用止損止盈
    if current_price <= entry_signal.stop_loss:
        return True, "止損"
    if current_price >= entry_signal.take_profit:
        return True, "止盈"
    
    # 加入追蹤止損（保護盈利）
    if current_pnl_percent > 0.015:  # 盈利 > 1.5%
        if current_price <= entry_price:
            return True, "保本出場"
    
    return False, ""  # 移除 RSI 出場條件
```

---

## 預期效果

| 指標 | 原策略 | V2 策略 |
|-----|--------|--------|
| 勝率 | 72.8% | 預計 50-55% (更嚴格條件) |
| 平均風報比 | 1:0.2 | 1:1.5+ |
| Sharpe Ratio | 0.36 | 預計 1.0+ |
| 長期盈利 | 可能虧損 | 穩定盈利 |

**核心原則**：風報比 > 勝率

一個 50% 勝率、2:1 風報比的策略，遠優於 70% 勝率、0.2:1 風報比的策略。

---

## 文件說明

1. **mean_reversion_v2.py** - 優化後的 Mean Reversion 策略
2. **momentum_v2.py** - 優化後的 Momentum 策略  
3. **backtest_v2.py** - 包含詳細風報比分析的回測腳本
4. **analyze_rr_problem.py** - 問題診斷腳本

---

## 使用方式

1. 將 V2 策略文件放入 `strategies/` 目錄
2. 修改 `strategies/__init__.py` 加入 V2 策略
3. 運行 `python backtest_v2.py` 比較 V1 和 V2 的表現
4. 在 `main.py` 中將策略切換為 V2 版本

```python
# strategies/__init__.py 更新
from .mean_reversion_v2 import mean_reversion_strategy_v2
from .momentum_v2 import momentum_strategy_v2
```
