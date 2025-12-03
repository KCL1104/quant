"""
簡易回測腳本
用於在歷史數據上測試策略 (支持並行多幣種)
"""
import asyncio
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
from typing import Any, Optional, List
from dataclasses import dataclass

from config import settings, MarketRegime, SignalType, StrategyType
from core import (
    indicators,
    market_detector,
    position_manager,
    IndicatorValues,
)
from strategies import (
    momentum_strategy,
    mean_reversion_strategy,
    Signal,
)


@dataclass
class BacktestTrade:
    """回測交易記錄"""
    entry_time: datetime
    exit_time: datetime
    strategy: StrategyType
    side: str
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    pnl_percent: float
    exit_reason: str


@dataclass
class BacktestResult:
    """回測結果"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_percent: float
    max_drawdown: float
    profit_factor: float
    sharpe_ratio: float
    trades: List[BacktestTrade]
    equity_curve: List[float]


@dataclass
class Position:
    """持倉資訊"""
    symbol: str
    side: str
    entry_price: float
    amount: float
    entry_time: datetime
    signal: Signal
    leverage: float

class PortfolioManager:
    """投資組合管理器 (處理多幣種資金與倉位)"""
    
    def __init__(self, initial_balance: float = 1000.0, max_leverage: float = 2.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.max_leverage = max_leverage
        self.positions: dict[str, Position] = {} # symbol -> Position
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[dict] = [] # [{'time': t, 'equity': e, 'balance': b}]
        
    def update_equity(self, current_prices: dict[str, float], current_time: datetime):
        """更新當前權益 (考慮未實現盈虧)"""
        unrealized_pnl = 0
        
        for symbol, position in self.positions.items():
            if symbol not in current_prices:
                continue
                
            current_price = current_prices[symbol]
            
            if position.side == "LONG":
                pnl = (current_price - position.entry_price) * position.amount
            else:
                pnl = (position.entry_price - current_price) * position.amount
                
            unrealized_pnl += pnl
            
        self.equity = self.balance + unrealized_pnl
        self.equity_curve.append({
            'time': current_time,
            'equity': self.equity,
            'balance': self.balance,
            'unrealized_pnl': unrealized_pnl
        })
        
    def can_open_position(self, symbol: str, required_margin: float) -> bool:
        """檢查是否可以開倉 (資金是否足夠)"""
        # 簡單檢查：已用保證金 + 新倉位保證金 < 總權益 * 最大槓桿
        # 這裡簡化為：當前可用餘額 > required_margin
        # 更嚴謹的應該是計算 Margin Level
        
        used_margin = sum(
            (p.entry_price * p.amount) / p.leverage 
            for p in self.positions.values()
        )
        
        available_equity = self.equity - used_margin
        
        # 保留 10% 緩衝
        return available_equity > required_margin * 1.1

    def open_position(self, symbol: str, signal: Signal, price: float, time: datetime, risk_per_trade: float = 0.02):
        """開倉"""
        if symbol in self.positions:
            return # 已有持倉
            
        # 計算倉位大小
        risk_amount = self.equity * risk_per_trade
        
        if signal.signal_type == SignalType.LONG:
            stop_distance = price - signal.stop_loss
        else:
            stop_distance = signal.stop_loss - price
        
        stop_distance_percent = stop_distance / price if price > 0 else 0.01
        
        if stop_distance_percent <= 0:
            stop_distance_percent = 0.01
            
        # 根據風險計算名義價值
        # risk_amount = equity * risk_per_trade
        # stop_distance_percent = |entry - stop| / entry
        # position_value * stop_distance_percent = risk_amount
        # => position_value = risk_amount / stop_distance_percent
        
        position_value = (risk_amount / stop_distance_percent)
        
        # 限制單一倉位最大為權益的 30% * 槓桿 (從 50% 下調，避免單一倉位過大)
        # 例如：$300 * 2 * 0.3 = $180 (名義價值)
        # 這樣即使全倉開滿也只能開約 3 個倉位 ($180 * 3 = $540 < $600)
        max_position_value = self.equity * self.max_leverage * 0.3
        position_value = min(position_value, max_position_value)
        
        # 另外增加一個硬性限制：單一倉位保證金不超過可用資金的 40%
        # 這樣至少能保證能開 2-3 個倉位
        available_equity = self.equity - sum((p.entry_price * p.amount) / p.leverage for p in self.positions.values())
        max_margin_from_equity = available_equity * 0.4
        max_position_from_margin = max_margin_from_equity * self.max_leverage
        
        position_value = min(position_value, max_position_from_margin)
        
        # 最小開倉金額限制 (假設為 $10)
        if position_value < 10:
            print(f"{time} | {symbol} | 計算倉位過小 (${position_value:.2f})，跳過")
            return

        # 計算所需保證金
        required_margin = position_value / self.max_leverage
        
        if not self.can_open_position(symbol, required_margin):
            print(f"{time} | {symbol} | 資金不足無法開倉 (需 ${required_margin:.2f})")
            return
            
        amount = position_value / price
        
        self.positions[symbol] = Position(
            symbol=symbol,
            side=signal.signal_type.value.upper(),
            entry_price=price,
            amount=amount,
            entry_time=time,
            signal=signal,
            leverage=self.max_leverage
        )
        
        print(f"{time} | OPEN  | {symbol} {signal.signal_type.value} | ${price:.2f} | {amount:.4f} | 預期風險: ${risk_amount:.2f}")

    def close_position(self, symbol: str, price: float, time: datetime, reason: str):
        """平倉"""
        if symbol not in self.positions:
            return
            
        position = self.positions[symbol]
        
        # 計算盈虧
        if position.side == "LONG":
            pnl = (price - position.entry_price) * position.amount
            pnl_percent = (price - position.entry_price) / position.entry_price
        else:
            pnl = (position.entry_price - price) * position.amount
            pnl_percent = (position.entry_price - price) / position.entry_price
            
        # 記錄交易
        trade = BacktestTrade(
            entry_time=position.entry_time,
            exit_time=time,
            strategy=position.signal.strategy,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=price,
            amount=position.amount,
            pnl=pnl,
            pnl_percent=pnl_percent,
            exit_reason=reason
        )
        # 這裡加個 symbol 屬性方便追蹤 (雖然 BacktestTrade 定義沒改，但 Python 動態屬性可用)
        setattr(trade, 'symbol', symbol)
        
        self.trades.append(trade)
        self.balance += pnl
        
        # 移除持倉
        del self.positions[symbol]
        
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        print(f"{time} | CLOSE | {symbol} {position.side} | ${price:.2f} | PnL={pnl_str} ({pnl_percent*100:.2f}%) | {reason}")


class ParallelBacktester:
    """並行回測器 (事件驅動)"""
    
    def __init__(
        self,
        initial_balance: float = 1000.0,
        leverage: float = 2.0,
        risk_per_trade: float = 0.02
    ):
        self.portfolio = PortfolioManager(initial_balance, leverage)
        self.risk_per_trade = risk_per_trade
        self.data_feeds = {} # symbol -> dataframe
        
    def add_data(self, symbol: str, df_fast: pd.DataFrame, df_slow: pd.DataFrame):
        """加入市場數據"""
        self.data_feeds[symbol] = {
            'fast': df_fast,
            'slow': df_slow
        }
        
    def run(self):
        """執行並行回測"""
        print("開始並行回測...")
        print(f"初始資金: ${self.portfolio.initial_balance:.2f}")
        print(f"統一槓桿: {self.portfolio.max_leverage}x")
        print("-" * 80)
        
        # 1. 對齊時間軸
        # 找出所有數據的共同時間範圍或聯集
        timestamps = set()
        for symbol, feed in self.data_feeds.items():
            timestamps.update(feed['fast']['timestamp'].tolist())
            
        sorted_timestamps = sorted(list[Any](timestamps))
        print(f"回測時間點數量: {len(sorted_timestamps)}")
        if sorted_timestamps:
            print(f"範圍: {sorted_timestamps[0]} ~ {sorted_timestamps[-1]}")
        
        # 2. 預計算指標 (為了效能，先算出所有指標)
        # 這裡為了簡化代碼結構，還是每步計算，或者我們可以先緩存指標
        # 為了保持邏輯一致性，我們在主循環中切片計算 (雖然慢一點但準確)
        
        min_periods = max(
            settings.supertrend.period,
            settings.ema.slow_period,
            settings.bollinger.period,
            settings.adx.period,
            settings.rsi.period
        ) + 10
        
        # 3. 事件循環
        # 我們需要一個指針來追蹤每個 symbol 當前的索引，避免每次都 search
        # 但由於時間戳是對齊的，我們可以用時間來索引
        
        # 將所有 dataframe 轉為以 timestamp 為 index，方便查找
        feed_map = {}
        for symbol, feed in self.data_feeds.items():
            df_f = feed['fast'].set_index('timestamp').sort_index()
            df_s = feed['slow'].set_index('timestamp').sort_index()
            feed_map[symbol] = {'fast': df_f, 'slow': df_s}
            
        # 用於記錄每個 symbol 的數據累積 (模擬實時推播)
        # 實際上為了效能，我們直接用 index 切片
        
        # 找出每個 symbol 的起始 index
        # 這裡簡化：我們遍歷 sorted_timestamps
        
        # 為了指標計算，我們需要歷史數據。
        # 策略：對於每個時間點 t
        #   對於每個 symbol:
        #     如果 symbol 在 t 有數據:
        #       獲取 symbol 在 t 及之前的數據 (用於指標)
        #       執行策略邏輯
        
        # 優化：直接遍歷時間戳可能太慢。
        # 我們假設所有市場的 5m K 線大體上是對齊的。
        
        # 我們需要維護每個 symbol 當前的數據窗口
        
        processed_count = 0
        total_steps = len(sorted_timestamps)
        
        for current_time in sorted_timestamps:
            processed_count += 1
            if processed_count < min_periods: # 跳過最初的一段時間用於累積指標
                continue
                
            # 獲取當前所有市場的價格 (用於更新權益)
            current_prices = {}
            
            # 第一階段：更新價格和權益
            for symbol in self.data_feeds.keys():
                df = feed_map[symbol]['fast']
                if current_time in df.index:
                    current_prices[symbol] = df.loc[current_time]['close']
                # 如果當前時間點該幣種沒數據 (例如暫停交易)，沿用上一次價格?
                # 這裡暫時忽略，假設數據連續
            
            self.portfolio.update_equity(current_prices, current_time)
            
            # 第二階段：執行策略
            for symbol in self.data_feeds.keys():
                # 獲取數據
                df_fast_all = feed_map[symbol]['fast']
                df_slow_all = feed_map[symbol]['slow']
                
                # 檢查該時間點是否有數據
                if current_time not in df_fast_all.index:
                    continue
                    
                # 數據切片 (這是效能瓶頸，但在 Python 回測中難免)
                # 優化：只取最近 N 根
                # loc 切片是包含 end 的
                # 我們需要知道 current_time 在 df 中的位置
                
                # 簡單做法：用 loc[:current_time]
                fast_slice = df_fast_all.loc[:current_time].iloc[-min_periods*2:] 
                
                if len(fast_slice) < min_periods:
                    continue
                    
                current_price = fast_slice['close'].iloc[-1]
                
                # 對應慢速數據
                slow_slice = df_slow_all.loc[:current_time].iloc[-min_periods*2:]
                
                if len(slow_slice) < min_periods:
                    continue
                
                # 計算指標
                try:
                    indicator_values = indicators.calculate_all(fast_slice, slow_slice)
                except Exception:
                    continue
                    
                # 判斷市場狀態
                market_state = market_detector.detect(indicator_values)
                
                # 交易邏輯
                # 1. 檢查持倉出場
                if symbol in self.portfolio.positions:
                    position = self.portfolio.positions[symbol]
                    should_exit, exit_reason = self._check_exit(
                        position,
                        indicator_values,
                        current_price,
                        current_time
                    )
                    
                    if should_exit:
                        self.portfolio.close_position(symbol, current_price, current_time, exit_reason)
                
                # 2. 檢查進場 (如果沒持倉)
                else:
                    signal = self._check_entry(indicator_values, market_state)
                    if signal:
                        self.portfolio.open_position(
                            symbol, 
                            signal, 
                            current_price, 
                            current_time,
                            self.risk_per_trade
                        )
            
            if processed_count % 1000 == 0:
                print(f"進度: {processed_count}/{total_steps} ({processed_count/total_steps*100:.1f}%) - Equity: ${self.portfolio.equity:.2f}")

        # 結束時強制平倉
        if sorted_timestamps:
            final_time = sorted_timestamps[-1]
            for symbol in list(self.portfolio.positions.keys()):
                 if symbol in current_prices:
                     self.portfolio.close_position(symbol, current_prices[symbol], final_time, "回測結束")

        return self.portfolio

    def _check_entry(self, indicator_values, market_state) -> Optional[Signal]:
        if market_state.regime == MarketRegime.TRENDING:
            return momentum_strategy.check_entry(indicator_values, market_state)
        elif market_state.regime == MarketRegime.RANGING:
            return mean_reversion_strategy.check_entry(indicator_values, market_state)
        return None

    def _check_exit(self, position: Position, indicator_values, current_price, current_time) -> tuple[bool, str]:
        # 計算當前盈虧百分比
        if position.side == "LONG":
            pnl_percent = (current_price - position.entry_price) / position.entry_price
        else:
            pnl_percent = (position.entry_price - current_price) / position.entry_price
            
        # 止損止盈
        if position.side == "LONG":
            if current_price <= position.signal.stop_loss:
                return True, "止損"
            if current_price >= position.signal.take_profit:
                return True, "止盈"
        else:
            if current_price >= position.signal.stop_loss:
                return True, "止損"
            if current_price <= position.signal.take_profit:
                return True, "止盈"
                
        # 策略出場
        if position.signal.strategy == StrategyType.MOMENTUM:
            return momentum_strategy.check_exit(
                indicator_values,
                position.entry_price,
                position.signal,
                pnl_percent
            )
        else:
            # Mean Reversion 時間止損
            if isinstance(position.entry_time, (int, float)):
                # 處理時間戳格式
                entry_ts = position.entry_time
                current_ts = current_time.timestamp() if isinstance(current_time, datetime) else current_time
                holding_minutes = (current_ts - entry_ts) / 60
            else:
                # 處理 datetime 格式
                holding_minutes = (current_time - position.entry_time).total_seconds() / 60
                
            if holding_minutes > 80:
                return True, "時間止損"
                
            return mean_reversion_strategy.check_exit(
                indicator_values,
                position.entry_price,
                position.signal,
                pnl_percent
            )


def generate_sample_data(days: int = 30, market_id: int = 2, target_count: int = 100000) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    從 API 批量獲取真實數據用於回測
    
    Args:
        days: 回測天數 (用於計算開始時間，已棄用，改用 target_count)
        market_id: 市場 ID
        target_count: 目標獲取的 5m tick 數量 (預設 100000)
        
    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: (5m 數據, 15m 數據)
    """
    print(f"正在從 ZKLighter API 批量下載數據 (Market ID: {market_id}, 目標: {target_count} ticks)...")
    
    headers = {"accept": "application/json"}
    all_candlesticks = []
    
    # API 每次最多返回 2000 個 tick
    batch_size = 2000
    
    # 從當前時間開始往回獲取
    end_timestamp = int(time.time())
    
    # 計算需要請求的批次數
    required_batches = (target_count + batch_size - 1) // batch_size  # 向上取整
    
    print(f"預計需要請求 {required_batches} 批次數據...")
    
    try:
        for batch_num in range(required_batches):
            # 每批次往前推 2000 * 5 分鐘
            # 為了確保數據連續，我們使用上一批次最早的時間戳作為下一批次的結束時間
            if all_candlesticks:
                # 使用已獲取數據中最早的時間戳作為新的結束時間
                end_timestamp = min(candle['timestamp'] for candle in all_candlesticks) // 1000 - 1
            
            # 計算開始時間戳 (往前推更多時間以確保能獲取足夠數據)
            # 5分鐘 = 300秒，2000個tick = 2000 * 300 = 600000秒
            start_timestamp = end_timestamp - (batch_size * 300 * 2)  # 多推一些以防萬一
            
            url = f"https://mainnet.zklighter.elliot.ai/api/v1/candlesticks?market_id={market_id}&resolution=5m&start_timestamp={start_timestamp}&end_timestamp={end_timestamp}&count_back={batch_size}&set_timestamp_to_end=true"
            
            print(f"批次 {batch_num + 1}/{required_batches}: 請求數據 (結束時間: {datetime.fromtimestamp(end_timestamp)})...", end=" ")
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            candlesticks = data.get('candlesticks', [])
            
            if not candlesticks:
                print(f"無數據，停止請求")
                break
            
            print(f"獲取 {len(candlesticks)} 條數據")
            
            # 添加到總列表 (避免重複)
            existing_timestamps = {candle['timestamp'] for candle in all_candlesticks}
            new_candles = [c for c in candlesticks if c['timestamp'] not in existing_timestamps]
            all_candlesticks.extend(new_candles)
            
            print(f"  累計: {len(all_candlesticks)} 條數據")
            
            # 如果已經達到目標數量，停止請求
            if len(all_candlesticks) >= target_count:
                print(f"已達到目標數量 {target_count}，停止請求")
                break
            
            # 如果返回的數據少於請求的數量，說明已經沒有更多歷史數據
            if len(candlesticks) < batch_size:
                print(f"API 返回數據少於請求量，已無更多歷史數據")
                break
            
            # 添加短暫延遲避免請求過快
            time.sleep(0.5)
        
        print(f"\n總共獲取 {len(all_candlesticks)} 條 5m 數據")
        
        if not all_candlesticks:
            raise ValueError("API 返回空數據")
        
        if len(all_candlesticks) < target_count:
            print(f"警告: 僅獲取到 {len(all_candlesticks)} 條數據，少於目標 {target_count} 條")
            
        # 轉換為 DataFrame
        records = []
        for candle in all_candlesticks:
            # timestamp 是毫秒，轉換為 datetime
            ts = datetime.fromtimestamp(candle['timestamp'] / 1000)
            records.append({
                'timestamp': ts,
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
                'volume': float(candle['volume0'])  # 使用 volume0 作為基礎貨幣成交量
            })
            
        df_fast = pd.DataFrame(records)
        
        # 確保數據按時間排序
        df_fast = df_fast.sort_values('timestamp').reset_index(drop=True)
        
        # 去除可能的重複數據
        df_fast = df_fast.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
        
        print(f"去重後剩餘 {len(df_fast)} 條數據")
        print(f"數據時間範圍: {df_fast['timestamp'].min()} ~ {df_fast['timestamp'].max()}")
        
        # 15 分鐘 K 線 (從 5 分鐘聚合)
        df_slow = df_fast.copy()
        # 設置 timestamp 為索引以便重採樣
        df_slow.set_index('timestamp', inplace=True)
        
        # 重採樣為 15 分鐘
        df_slow = df_slow.resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna().reset_index()
        
        print(f"生成 {len(df_slow)} 條 15m 數據\n")
        
        return df_fast, df_slow
        
    except Exception as e:
        print(f"獲取數據失敗: {e}")
        raise e


def print_parallel_result(portfolio: PortfolioManager):
    """印出並行回測結果"""
    print("\n" + "=" * 80)
    print("                    並行回測結果報告")
    print("=" * 80)
    
    total_trades = len(portfolio.trades)
    winning_trades = len([t for t in portfolio.trades if t.pnl > 0])
    losing_trades = len([t for t in portfolio.trades if t.pnl <= 0])
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    
    total_pnl = portfolio.balance - portfolio.initial_balance
    total_pnl_percent = total_pnl / portfolio.initial_balance
    
    # 計算最大回撤 (基於 Equity Curve)
    equities = [e['equity'] for e in portfolio.equity_curve]
    max_dd = 0
    if equities:
        peak = equities[0]
        for e in equities:
            if e > peak:
                peak = e
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
                
    # Sharpe Ratio
    returns = [t.pnl_percent for t in portfolio.trades]
    if len(returns) > 1:
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
    else:
        sharpe = 0
        
    print(f"初始資金:       ${portfolio.initial_balance:.2f}")
    print(f"最終權益:       ${portfolio.equity:.2f}")
    print(f"總盈虧:         ${total_pnl:.2f} ({total_pnl_percent*100:.2f}%)")
    print("-" * 80)
    print(f"總交易次數:     {total_trades}")
    print(f"獲利交易:       {winning_trades}")
    print(f"虧損交易:       {losing_trades}")
    print(f"勝率:           {win_rate*100:.1f}%")
    print(f"最大回撤:       {max_dd*100:.2f}%")
    print(f"Sharpe Ratio:   {sharpe:.2f}")
    print("=" * 80)
    
    # 按幣種統計
    symbols = set(getattr(t, 'symbol', 'Unknown') for t in portfolio.trades)
    print("\n各幣種表現:")
    for sym in symbols:
        sym_trades = [t for t in portfolio.trades if getattr(t, 'symbol', 'Unknown') == sym]
        sym_pnl = sum(t.pnl for t in sym_trades)
        sym_wins = len([t for t in sym_trades if t.pnl > 0])
        if len(sym_trades) > 0:
            win_rate_sym = sym_wins/len(sym_trades)*100
        else:
            win_rate_sym = 0
        print(f"  {sym:<5} | 交易: {len(sym_trades):<3} | PnL: ${sym_pnl:>7.2f} | 勝率: {win_rate_sym:.1f}%")


def main():
    """主函數"""
    
    markets = [
        ("BTC", 1),
        ("SOL", 2),
        ("ETH", 0)
    ]
    
    print(f"初始化並行回測引擎...")
    # 初始資金 300，槓桿 2 倍
    backtester = ParallelBacktester(initial_balance=300.0, leverage=2.0, risk_per_trade=0.02)
    
    for symbol, market_id in markets:
        try:
            print(f"載入數據: {symbol} (ID: {market_id})...")
            # 獲取至少 100000 個 5m tick 的數據
            df_fast, df_slow = generate_sample_data(days=200, market_id=market_id, target_count=10000)
            backtester.add_data(symbol, df_fast, df_slow)
        except Exception as e:
            print(f"載入 {symbol} 失敗: {e}")
            
    # 執行回測
    portfolio = backtester.run()
    
    # 印出結果
    print_parallel_result(portfolio)


if __name__ == "__main__":
    main()
