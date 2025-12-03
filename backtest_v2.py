"""
改進版回測腳本
使用 V2 策略，添加詳細的風報比和績效分析
"""
import asyncio
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
from typing import Any, Optional, List
from dataclasses import dataclass
from collections import defaultdict

from config import settings, MarketRegime, SignalType, StrategyType
from core import (
    indicators,
    market_detector,
    IndicatorValues,
)

# 導入 V2 策略
from strategies.momentum_v2 import momentum_strategy_v2
from strategies.mean_reversion_v2 import mean_reversion_strategy_v2
from strategies.base import Signal


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
    # 新增欄位
    planned_stop_loss: float
    planned_take_profit: float
    planned_rr_ratio: float
    actual_rr_ratio: float  # 實際風報比


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


class PortfolioManagerV2:
    """投資組合管理器 V2"""
    
    def __init__(self, initial_balance: float = 1000.0, max_leverage: float = 2.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.max_leverage = max_leverage
        self.positions: dict[str, Position] = {}
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[dict] = []
        
    def update_equity(self, current_prices: dict[str, float], current_time: datetime):
        """更新當前權益"""
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
        """檢查是否可以開倉"""
        used_margin = sum(
            (p.entry_price * p.amount) / p.leverage 
            for p in self.positions.values()
        )
        available_equity = self.equity - used_margin
        return available_equity > required_margin * 1.1

    def open_position(
        self, 
        symbol: str, 
        signal: Signal, 
        price: float, 
        time: datetime, 
        risk_per_trade: float = 0.02
    ):
        """開倉"""
        if symbol in self.positions:
            return
            
        risk_amount = self.equity * risk_per_trade
        
        if signal.signal_type == SignalType.LONG:
            stop_distance = price - signal.stop_loss
        else:
            stop_distance = signal.stop_loss - price
        
        stop_distance_percent = stop_distance / price if price > 0 else 0.01
        
        if stop_distance_percent <= 0:
            stop_distance_percent = 0.01
            
        position_value = (risk_amount / stop_distance_percent)
        max_position_value = self.equity * self.max_leverage * 0.3
        position_value = min(position_value, max_position_value)
        
        available_equity = self.equity - sum(
            (p.entry_price * p.amount) / p.leverage 
            for p in self.positions.values()
        )
        max_margin_from_equity = available_equity * 0.4
        max_position_from_margin = max_margin_from_equity * self.max_leverage
        position_value = min(position_value, max_position_from_margin)
        
        if position_value < 10:
            return

        required_margin = position_value / self.max_leverage
        
        if not self.can_open_position(symbol, required_margin):
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
        
        # 計算計劃的風報比
        if signal.signal_type == SignalType.LONG:
            planned_profit = signal.take_profit - price
            planned_loss = price - signal.stop_loss
        else:
            planned_profit = price - signal.take_profit
            planned_loss = signal.stop_loss - price
            
        planned_rr = planned_profit / planned_loss if planned_loss > 0 else 0
        
        print(
            f"{time} | OPEN  | {symbol} {signal.signal_type.value} | "
            f"${price:.2f} | SL=${signal.stop_loss:.2f} | TP=${signal.take_profit:.2f} | "
            f"RR={planned_rr:.2f}"
        )

    def close_position(self, symbol: str, price: float, time: datetime, reason: str):
        """平倉"""
        if symbol not in self.positions:
            return
            
        position = self.positions[symbol]
        
        if position.side == "LONG":
            pnl = (price - position.entry_price) * position.amount
            pnl_percent = (price - position.entry_price) / position.entry_price
            # 實際風報比計算
            actual_profit = price - position.entry_price
            planned_loss = position.entry_price - position.signal.stop_loss
        else:
            pnl = (position.entry_price - price) * position.amount
            pnl_percent = (position.entry_price - price) / position.entry_price
            actual_profit = position.entry_price - price
            planned_loss = position.signal.stop_loss - position.entry_price
            
        actual_rr = actual_profit / planned_loss if planned_loss > 0 else 0
        
        # 計算計劃的風報比
        if position.side == "LONG":
            planned_profit = position.signal.take_profit - position.entry_price
        else:
            planned_profit = position.entry_price - position.signal.take_profit
        planned_rr = planned_profit / planned_loss if planned_loss > 0 else 0
            
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
            exit_reason=reason,
            planned_stop_loss=position.signal.stop_loss,
            planned_take_profit=position.signal.take_profit,
            planned_rr_ratio=planned_rr,
            actual_rr_ratio=actual_rr
        )
        setattr(trade, 'symbol', symbol)
        
        self.trades.append(trade)
        self.balance += pnl
        
        del self.positions[symbol]
        
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        print(
            f"{time} | CLOSE | {symbol} {position.side} | "
            f"${price:.2f} | PnL={pnl_str} ({pnl_percent*100:.2f}%) | "
            f"ActualRR={actual_rr:.2f} | {reason}"
        )


class ParallelBacktesterV2:
    """並行回測器 V2"""
    
    def __init__(
        self,
        initial_balance: float = 1000.0,
        leverage: float = 2.0,
        risk_per_trade: float = 0.02,
        use_v2_strategies: bool = True
    ):
        self.portfolio = PortfolioManagerV2(initial_balance, leverage)
        self.risk_per_trade = risk_per_trade
        self.data_feeds = {}
        self.use_v2_strategies = use_v2_strategies
        
    def add_data(self, symbol: str, df_fast: pd.DataFrame, df_slow: pd.DataFrame):
        """加入市場數據"""
        self.data_feeds[symbol] = {
            'fast': df_fast,
            'slow': df_slow
        }
        
    def run(self):
        """執行回測"""
        print("=" * 80)
        print(f"開始回測 (V2 策略: {self.use_v2_strategies})")
        print(f"初始資金: ${self.portfolio.initial_balance:.2f}")
        print(f"槓桿: {self.portfolio.max_leverage}x")
        print("=" * 80)
        
        # 對齊時間軸
        timestamps = set()
        for symbol, feed in self.data_feeds.items():
            timestamps.update(feed['fast']['timestamp'].tolist())
            
        sorted_timestamps = sorted(list[Any](timestamps))
        print(f"回測時間點: {len(sorted_timestamps)}")
        
        min_periods = max(
            settings.supertrend.period,
            settings.ema.slow_period,
            settings.bollinger.period,
            settings.adx.period,
            settings.rsi.period
        ) + 10
        
        feed_map = {}
        for symbol, feed in self.data_feeds.items():
            df_f = feed['fast'].set_index('timestamp').sort_index()
            df_s = feed['slow'].set_index('timestamp').sort_index()
            feed_map[symbol] = {'fast': df_f, 'slow': df_s}
            
        processed_count = 0
        total_steps = len(sorted_timestamps)
        
        for current_time in sorted_timestamps:
            processed_count += 1
            if processed_count < min_periods:
                continue
                
            current_prices = {}
            
            for symbol in self.data_feeds.keys():
                df = feed_map[symbol]['fast']
                if current_time in df.index:
                    current_prices[symbol] = df.loc[current_time]['close']
            
            self.portfolio.update_equity(current_prices, current_time)
            
            for symbol in self.data_feeds.keys():
                df_fast_all = feed_map[symbol]['fast']
                df_slow_all = feed_map[symbol]['slow']
                
                if current_time not in df_fast_all.index:
                    continue
                    
                fast_slice = df_fast_all.loc[:current_time].iloc[-min_periods*2:] 
                
                if len(fast_slice) < min_periods:
                    continue
                    
                current_price = fast_slice['close'].iloc[-1]
                slow_slice = df_slow_all.loc[:current_time].iloc[-min_periods*2:]
                
                if len(slow_slice) < min_periods:
                    continue
                
                try:
                    indicator_values = indicators.calculate_all(fast_slice, slow_slice)
                except Exception:
                    continue
                    
                market_state = market_detector.detect(indicator_values)
                
                # 檢查持倉出場
                if symbol in self.portfolio.positions:
                    position = self.portfolio.positions[symbol]
                    should_exit, exit_reason = self._check_exit(
                        position,
                        indicator_values,
                        current_price,
                        current_time
                    )
                    
                    if should_exit:
                        self.portfolio.close_position(
                            symbol, current_price, current_time, exit_reason
                        )
                
                # 檢查進場
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
            
            if processed_count % 2000 == 0:
                print(
                    f"進度: {processed_count}/{total_steps} "
                    f"({processed_count/total_steps*100:.1f}%) - "
                    f"Equity: ${self.portfolio.equity:.2f}"
                )

        # 結束時強制平倉
        if sorted_timestamps:
            final_time = sorted_timestamps[-1]
            for symbol in list(self.portfolio.positions.keys()):
                 if symbol in current_prices:
                     self.portfolio.close_position(
                         symbol, current_prices[symbol], final_time, "回測結束"
                     )

        return self.portfolio

    def _check_entry(self, indicator_values, market_state) -> Optional[Signal]:
        """檢查進場條件"""
        if self.use_v2_strategies:
            # 使用 V2 策略
            if market_state.regime == MarketRegime.TRENDING:
                return momentum_strategy_v2.check_entry(indicator_values, market_state)
            elif market_state.regime == MarketRegime.RANGING:
                return mean_reversion_strategy_v2.check_entry(indicator_values, market_state)
        else:
            # 使用原版策略
            from strategies import momentum_strategy, mean_reversion_strategy
            if market_state.regime == MarketRegime.TRENDING:
                return momentum_strategy.check_entry(indicator_values, market_state)
            elif market_state.regime == MarketRegime.RANGING:
                return mean_reversion_strategy.check_entry(indicator_values, market_state)
        return None

    def _check_exit(
        self, 
        position: Position, 
        indicator_values, 
        current_price, 
        current_time
    ) -> tuple[bool, str]:
        """檢查出場條件"""
        
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
        
        # 使用策略的出場邏輯
        if self.use_v2_strategies:
            if position.signal.strategy == StrategyType.MOMENTUM:
                return momentum_strategy_v2.check_exit(
                    indicator_values,
                    position.entry_price,
                    position.signal,
                    pnl_percent
                )
            else:
                return mean_reversion_strategy_v2.check_exit(
                    indicator_values,
                    position.entry_price,
                    position.signal,
                    pnl_percent
                )
        else:
            from strategies import momentum_strategy, mean_reversion_strategy
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
                    entry_ts = position.entry_time
                    current_ts = current_time.timestamp() if isinstance(current_time, datetime) else current_time
                    holding_minutes = (current_ts - entry_ts) / 60
                else:
                    holding_minutes = (current_time - position.entry_time).total_seconds() / 60
                    
                if holding_minutes > 80:
                    return True, "時間止損"
                    
                return mean_reversion_strategy.check_exit(
                    indicator_values,
                    position.entry_price,
                    position.signal,
                    pnl_percent
                )


def generate_sample_data(
    days: int = 30, 
    market_id: int = 2, 
    target_count: int = 10000
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """從 API 獲取數據"""
    print(f"下載數據 (Market ID: {market_id}, 目標: {target_count} ticks)...")
    
    headers = {"accept": "application/json"}
    all_candlesticks = []
    batch_size = 2000
    end_timestamp = int(time.time())
    required_batches = (target_count + batch_size - 1) // batch_size
    
    try:
        for batch_num in range(required_batches):
            if all_candlesticks:
                end_timestamp = min(candle['timestamp'] for candle in all_candlesticks) // 1000 - 1
            
            start_timestamp = end_timestamp - (batch_size * 300 * 2)
            
            url = (
                f"https://mainnet.zklighter.elliot.ai/api/v1/candlesticks?"
                f"market_id={market_id}&resolution=5m&start_timestamp={start_timestamp}&"
                f"end_timestamp={end_timestamp}&count_back={batch_size}&set_timestamp_to_end=true"
            )
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            candlesticks = data.get('candlesticks', [])
            
            if not candlesticks:
                break
            
            existing_timestamps = {candle['timestamp'] for candle in all_candlesticks}
            new_candles = [c for c in candlesticks if c['timestamp'] not in existing_timestamps]
            all_candlesticks.extend(new_candles)
            
            if len(all_candlesticks) >= target_count:
                break
            
            if len(candlesticks) < batch_size:
                break
            
            time.sleep(0.3)
        
        print(f"獲取 {len(all_candlesticks)} 條數據")
        
        records = []
        for candle in all_candlesticks:
            ts = datetime.fromtimestamp(candle['timestamp'] / 1000)
            records.append({
                'timestamp': ts,
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
                'volume': float(candle['volume0'])
            })
            
        df_fast = pd.DataFrame(records)
        df_fast = df_fast.sort_values('timestamp').reset_index(drop=True)
        df_fast = df_fast.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
        
        # 15m K線
        df_slow = df_fast.copy()
        df_slow.set_index('timestamp', inplace=True)
        df_slow = df_slow.resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna().reset_index()
        
        return df_fast, df_slow
        
    except Exception as e:
        print(f"獲取數據失敗: {e}")
        raise e


def print_detailed_analysis(portfolio: PortfolioManagerV2):
    """詳細分析報告"""
    print("\n" + "=" * 80)
    print("                    詳細回測分析報告")
    print("=" * 80)
    
    trades = portfolio.trades
    total_trades = len(trades)
    
    if total_trades == 0:
        print("沒有交易記錄")
        return
    
    winning_trades = [t for t in trades if t.pnl > 0]
    losing_trades = [t for t in trades if t.pnl <= 0]
    win_rate = len(winning_trades) / total_trades
    
    total_pnl = portfolio.balance - portfolio.initial_balance
    total_pnl_percent = total_pnl / portfolio.initial_balance
    
    # 計算最大回撤
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
    returns = [t.pnl_percent for t in trades]
    if len(returns) > 1:
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
    else:
        sharpe = 0
    
    print(f"\n【基本統計】")
    print(f"初始資金:       ${portfolio.initial_balance:.2f}")
    print(f"最終權益:       ${portfolio.equity:.2f}")
    print(f"總盈虧:         ${total_pnl:.2f} ({total_pnl_percent*100:.2f}%)")
    print(f"總交易次數:     {total_trades}")
    print(f"獲利交易:       {len(winning_trades)}")
    print(f"虧損交易:       {len(losing_trades)}")
    print(f"勝率:           {win_rate*100:.1f}%")
    print(f"最大回撤:       {max_dd*100:.2f}%")
    print(f"Sharpe Ratio:   {sharpe:.2f}")
    
    # 風報比分析
    print(f"\n【風報比分析】")
    planned_rrs = [t.planned_rr_ratio for t in trades if t.planned_rr_ratio > 0]
    actual_rrs = [t.actual_rr_ratio for t in trades]
    
    print(f"平均計劃風報比: {np.mean(planned_rrs):.2f}" if planned_rrs else "N/A")
    print(f"平均實際風報比: {np.mean(actual_rrs):.2f}")
    
    # 獲利交易的實際風報比
    winning_rrs = [t.actual_rr_ratio for t in winning_trades]
    losing_rrs = [t.actual_rr_ratio for t in losing_trades]
    
    print(f"獲利交易平均RR: {np.mean(winning_rrs):.2f}" if winning_rrs else "N/A")
    print(f"虧損交易平均RR: {np.mean(losing_rrs):.2f}" if losing_rrs else "N/A")
    
    # 平均盈虧
    avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
    avg_loss = np.mean([abs(t.pnl) for t in losing_trades]) if losing_trades else 0
    
    print(f"\n【盈虧分析】")
    print(f"平均獲利: ${avg_win:.2f}")
    print(f"平均虧損: ${avg_loss:.2f}")
    print(f"盈虧比:   {avg_win/avg_loss:.2f}" if avg_loss > 0 else "N/A")
    
    # Profit Factor
    total_wins = sum(t.pnl for t in winning_trades)
    total_losses = sum(abs(t.pnl) for t in losing_trades)
    pf = total_wins / total_losses if total_losses > 0 else float('inf')
    print(f"Profit Factor: {pf:.2f}")
    
    # 按策略分析
    print(f"\n【按策略分析】")
    by_strategy = defaultdict(list)
    for t in trades:
        by_strategy[t.strategy.value].append(t)
    
    for strategy, strategy_trades in by_strategy.items():
        wins = len([t for t in strategy_trades if t.pnl > 0])
        total = len(strategy_trades)
        wr = wins/total*100 if total > 0 else 0
        pnl = sum(t.pnl for t in strategy_trades)
        avg_rr = np.mean([t.actual_rr_ratio for t in strategy_trades])
        
        print(f"  {strategy:<15} | 交易: {total:<3} | 勝率: {wr:>5.1f}% | "
              f"PnL: ${pnl:>8.2f} | 平均RR: {avg_rr:>5.2f}")
    
    # 按幣種分析
    print(f"\n【按幣種分析】")
    symbols = set(getattr(t, 'symbol', 'Unknown') for t in trades)
    for sym in symbols:
        sym_trades = [t for t in trades if getattr(t, 'symbol', 'Unknown') == sym]
        sym_pnl = sum(t.pnl for t in sym_trades)
        sym_wins = len([t for t in sym_trades if t.pnl > 0])
        wr = sym_wins/len(sym_trades)*100 if len(sym_trades) > 0 else 0
        print(f"  {sym:<5} | 交易: {len(sym_trades):<3} | 勝率: {wr:>5.1f}% | "
              f"PnL: ${sym_pnl:>8.2f}")
    
    # 出場原因分析
    print(f"\n【出場原因分析】")
    exit_reasons = defaultdict(list)
    for t in trades:
        exit_reasons[t.exit_reason].append(t)
    
    for reason, reason_trades in sorted(exit_reasons.items(), 
                                        key=lambda x: len(x[1]), reverse=True):
        count = len(reason_trades)
        pnl = sum(t.pnl for t in reason_trades)
        wins = len([t for t in reason_trades if t.pnl > 0])
        wr = wins/count*100 if count > 0 else 0
        print(f"  {reason:<20} | 次數: {count:<4} | 勝率: {wr:>5.1f}% | "
              f"PnL: ${pnl:>8.2f}")


def main():
    """主函數"""
    markets = [
        ("BNB", 25),
        ("ETH", 0)
    ]
    
    # 測試兩個版本
    for use_v2 in [True]:
        version = "V2" if use_v2 else "V1(原版)"
        print(f"\n{'#' * 80}")
        print(f"# 測試策略版本: {version}")
        print(f"{'#' * 80}")
        
        backtester = ParallelBacktesterV2(
            initial_balance=300.0, 
            leverage=5.0, 
            risk_per_trade=0.05,
            use_v2_strategies=use_v2
        )
        
        for symbol, market_id in markets:
            try:
                print(f"\n載入數據: {symbol} (ID: {market_id})...")
                df_fast, df_slow = generate_sample_data(
                    days=300, market_id=market_id, target_count=100000
                )
                backtester.add_data(symbol, df_fast, df_slow)
            except Exception as e:
                print(f"載入 {symbol} 失敗: {e}")
                
        portfolio = backtester.run()
        print_detailed_analysis(portfolio)


if __name__ == "__main__":
    main()
