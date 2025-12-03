"""
績效追蹤模組
記錄和計算策略績效指標
"""
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
import numpy as np

from config import StrategyType


@dataclass
class TradeMetric:
    """單筆交易指標"""
    trade_id: str
    timestamp: datetime
    strategy: StrategyType
    side: str                     # LONG/SHORT
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    pnl_percent: float
    holding_time: int             # 秒
    exit_reason: str


@dataclass
class PerformanceMetrics:
    """績效指標"""
    # 基本統計
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    
    # 盈虧
    total_pnl: float
    total_pnl_percent: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    
    # 風報比
    avg_rr_ratio: float
    
    # 回撤
    max_drawdown: float
    max_drawdown_duration: int    # 天數
    
    # 其他
    sharpe_ratio: float
    sortino_ratio: float
    avg_holding_time: float       # 小時
    
    # 按策略分類
    by_strategy: Dict[str, dict]


class MetricsTracker:
    """績效追蹤器"""
    
    def __init__(self, save_path: str = "data/metrics.json"):
        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.trades: List[TradeMetric] = []
        self.equity_curve: List[tuple[datetime, float]] = []
        
        # 載入歷史數據
        self._load()
    
    def record_trade(
        self,
        trade_id: str,
        strategy: StrategyType,
        side: str,
        entry_price: float,
        exit_price: float,
        amount: float,
        entry_time: datetime,
        exit_time: datetime,
        exit_reason: str
    ):
        """記錄交易"""
        # 計算盈虧
        if side == "LONG":
            pnl = (exit_price - entry_price) * amount
            pnl_percent = (exit_price - entry_price) / entry_price
        else:
            pnl = (entry_price - exit_price) * amount
            pnl_percent = (entry_price - exit_price) / entry_price
        
        holding_time = int((exit_time - entry_time).total_seconds())
        
        trade = TradeMetric(
            trade_id=trade_id,
            timestamp=exit_time,
            strategy=strategy,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            amount=amount,
            pnl=pnl,
            pnl_percent=pnl_percent,
            holding_time=holding_time,
            exit_reason=exit_reason
        )
        
        self.trades.append(trade)
        self._save()
    
    def update_equity(self, equity: float):
        """更新權益曲線"""
        self.equity_curve.append((datetime.utcnow(), equity))
        
        # 只保留最近 30 天
        cutoff = datetime.utcnow() - timedelta(days=30)
        self.equity_curve = [
            (t, e) for t, e in self.equity_curve if t > cutoff
        ]
    
    def calculate_metrics(self, days: int = None) -> PerformanceMetrics:
        """
        計算績效指標
        
        Args:
            days: 計算最近 N 天的指標，None 表示全部
        """
        if days:
            cutoff = datetime.utcnow() - timedelta(days=days)
            trades = [t for t in self.trades if t.timestamp > cutoff]
        else:
            trades = self.trades
        
        if not trades:
            return self._empty_metrics()
        
        # 基本統計
        total_trades = len(trades)
        winning_trades = len([t for t in trades if t.pnl > 0])
        losing_trades = len([t for t in trades if t.pnl <= 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        # 盈虧
        total_pnl = sum(t.pnl for t in trades)
        total_pnl_percent = sum(t.pnl_percent for t in trades)
        
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [abs(t.pnl) for t in trades if t.pnl <= 0]
        
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        
        total_wins = sum(wins)
        total_losses = sum(losses)
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # 風報比
        avg_rr_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        
        # 回撤
        max_dd, max_dd_duration = self._calculate_drawdown()
        
        # Sharpe 和 Sortino
        returns = [t.pnl_percent for t in trades]
        sharpe = self._calculate_sharpe(returns)
        sortino = self._calculate_sortino(returns)
        
        # 平均持倉時間
        avg_holding_time = np.mean([t.holding_time for t in trades]) / 3600  # 轉換為小時
        
        # 按策略分類
        by_strategy = self._calculate_by_strategy(trades)
        
        return PerformanceMetrics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            avg_rr_ratio=avg_rr_ratio,
            max_drawdown=max_dd,
            max_drawdown_duration=max_dd_duration,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            avg_holding_time=avg_holding_time,
            by_strategy=by_strategy
        )
    
    def _calculate_drawdown(self) -> tuple[float, int]:
        """計算最大回撤"""
        if len(self.equity_curve) < 2:
            return 0, 0
        
        equities = [e for _, e in self.equity_curve]
        
        peak = equities[0]
        max_dd = 0
        dd_start = None
        max_dd_duration = 0
        
        for i, equity in enumerate(equities):
            if equity > peak:
                peak = equity
                dd_start = None
            else:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
                
                if dd_start is None:
                    dd_start = i
        
        # 計算回撤持續時間 (簡化版)
        if dd_start is not None:
            max_dd_duration = len(equities) - dd_start
        
        return max_dd, max_dd_duration
    
    def _calculate_sharpe(self, returns: List[float], risk_free: float = 0) -> float:
        """計算 Sharpe Ratio"""
        if len(returns) < 2:
            return 0
        
        excess_returns = [r - risk_free for r in returns]
        mean_return = np.mean(excess_returns)
        std_return = np.std(excess_returns)
        
        if std_return == 0:
            return 0
        
        # 年化 (假設每天一筆交易)
        return (mean_return / std_return) * np.sqrt(252)
    
    def _calculate_sortino(self, returns: List[float], risk_free: float = 0) -> float:
        """計算 Sortino Ratio"""
        if len(returns) < 2:
            return 0
        
        excess_returns = [r - risk_free for r in returns]
        mean_return = np.mean(excess_returns)
        
        # 只計算負報酬的標準差
        negative_returns = [r for r in excess_returns if r < 0]
        if not negative_returns:
            return float('inf')
        
        downside_std = np.std(negative_returns)
        
        if downside_std == 0:
            return float('inf')
        
        return (mean_return / downside_std) * np.sqrt(252)
    
    def _calculate_by_strategy(self, trades: List[TradeMetric]) -> Dict[str, dict]:
        """按策略計算指標"""
        by_strategy = defaultdict(list)
        
        for trade in trades:
            by_strategy[trade.strategy.value].append(trade)
        
        result = {}
        for strategy, strategy_trades in by_strategy.items():
            wins = len([t for t in strategy_trades if t.pnl > 0])
            total = len(strategy_trades)
            
            result[strategy] = {
                "total_trades": total,
                "win_rate": wins / total if total > 0 else 0,
                "total_pnl": sum(t.pnl for t in strategy_trades),
                "avg_pnl": np.mean([t.pnl for t in strategy_trades]) if strategy_trades else 0
            }
        
        return result
    
    def _empty_metrics(self) -> PerformanceMetrics:
        """返回空的績效指標"""
        return PerformanceMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            total_pnl=0,
            total_pnl_percent=0,
            avg_win=0,
            avg_loss=0,
            profit_factor=0,
            avg_rr_ratio=0,
            max_drawdown=0,
            max_drawdown_duration=0,
            sharpe_ratio=0,
            sortino_ratio=0,
            avg_holding_time=0,
            by_strategy={}
        )
    
    def get_summary(self) -> str:
        """取得績效摘要"""
        metrics = self.calculate_metrics()
        
        summary = f"""
╔══════════════════════════════════════╗
║         績效摘要                      ║
╠══════════════════════════════════════╣
║ 總交易次數: {metrics.total_trades:>20} ║
║ 勝率:      {metrics.win_rate*100:>19.1f}% ║
║ 獲利因子:  {metrics.profit_factor:>20.2f} ║
║ 總盈虧:    ${metrics.total_pnl:>18.2f} ║
║ 最大回撤:  {metrics.max_drawdown*100:>19.2f}% ║
║ Sharpe:    {metrics.sharpe_ratio:>20.2f} ║
╚══════════════════════════════════════╝
"""
        return summary
    
    def _save(self):
        """保存數據"""
        data = {
            "trades": [
                {
                    **asdict(t),
                    "timestamp": t.timestamp.isoformat(),
                    "strategy": t.strategy.value
                }
                for t in self.trades[-1000:]  # 只保留最近 1000 筆
            ],
            "equity_curve": [
                (t.isoformat(), e) for t, e in self.equity_curve
            ]
        }
        
        with open(self.save_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _load(self):
        """載入數據"""
        if not self.save_path.exists():
            return
        
        try:
            with open(self.save_path, 'r') as f:
                data = json.load(f)
            
            self.trades = [
                TradeMetric(
                    trade_id=t["trade_id"],
                    timestamp=datetime.fromisoformat(t["timestamp"]),
                    strategy=StrategyType(t["strategy"]),
                    side=t["side"],
                    entry_price=t["entry_price"],
                    exit_price=t["exit_price"],
                    amount=t["amount"],
                    pnl=t["pnl"],
                    pnl_percent=t["pnl_percent"],
                    holding_time=t["holding_time"],
                    exit_reason=t["exit_reason"]
                )
                for t in data.get("trades", [])
            ]
            
            self.equity_curve = [
                (datetime.fromisoformat(t), e)
                for t, e in data.get("equity_curve", [])
            ]
            
        except Exception:
            pass


# 全域績效追蹤器
metrics_tracker = MetricsTracker()
