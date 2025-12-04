"""
風險管理模組
處理動態槓桿、風險控制、回撤保護等
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from collections import deque

from config import settings


@dataclass
class TradeRecord:
    """交易記錄"""
    timestamp: datetime
    pnl: float                    # 盈虧金額
    pnl_percent: float            # 盈虧百分比
    is_win: bool                  # 是否獲利
    strategy: str                 # 使用的策略


@dataclass
class RiskMetrics:
    """風險指標"""
    # 當前狀態
    current_leverage: float       # 當前槓桿
    available_leverage: float     # 可用槓桿
    
    # 績效統計
    total_trades: int             # 總交易次數
    win_rate: float               # 勝率
    consecutive_wins: int         # 連勝次數
    consecutive_losses: int       # 連虧次數
    
    # 損益
    daily_pnl: float              # 日內損益
    weekly_pnl: float             # 週損益
    total_pnl: float              # 總損益
    
    # 回撤
    peak_balance: float           # 峰值餘額
    current_drawdown: float       # 當前回撤
    max_drawdown: float           # 最大回撤
    
    # 狀態
    can_trade: bool               # 是否可以交易
    stop_reason: Optional[str]    # 停止交易原因
    cooldown_until: Optional[datetime]  # 冷卻期結束時間


class RiskManager:
    """風險管理器"""
    
    def __init__(self, initial_balance: float):
        self.config = settings
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.peak_balance = initial_balance
        
        # 交易記錄 (保留最近 100 筆)
        self.trade_history: deque = deque(maxlen=100)
        
        # 連續統計
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        
        # 日/週統計
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.daily_trades = 0
        self.weekly_trades = 0
        
        # 時間追蹤
        self.last_trade_time: Optional[datetime] = None
        self.day_start: datetime = self._get_day_start()
        self.week_start: datetime = self._get_week_start()
        
        # 冷卻期
        self.cooldown_until: Optional[datetime] = None
        
        # 最大回撤追蹤
        self.max_drawdown = 0.0
    
    def _get_day_start(self) -> datetime:
        """取得今天 UTC 0:00"""
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    
    def _get_week_start(self) -> datetime:
        """取得本週一 UTC 0:00"""
        now = datetime.now(timezone.utc)
        days_since_monday = now.weekday()
        week_start = now - timedelta(days=days_since_monday)
        return datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc)
    
    def _check_reset_periods(self):
        """檢查是否需要重置日/週統計"""
        now = datetime.now(timezone.utc)
        
        # 日重置
        current_day_start = self._get_day_start()
        if current_day_start > self.day_start:
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.day_start = current_day_start
        
        # 週重置
        current_week_start = self._get_week_start()
        if current_week_start > self.week_start:
            self.weekly_pnl = 0.0
            self.weekly_trades = 0
            self.week_start = current_week_start
    
    def update_balance(self, new_balance: float):
        """更新餘額並追蹤峰值"""
        self.current_balance = new_balance
        
        if new_balance > self.peak_balance:
            self.peak_balance = new_balance
    
    def record_trade(self, pnl: float, strategy: str):
        """
        記錄交易結果
        
        Args:
            pnl: 盈虧金額
            strategy: 使用的策略
        """
        self._check_reset_periods()
        
        pnl_percent = pnl / self.current_balance if self.current_balance > 0 else 0
        is_win = pnl > 0
        
        record = TradeRecord(
            timestamp=datetime.now(timezone.utc),
            pnl=pnl,
            pnl_percent=pnl_percent,
            is_win=is_win,
            strategy=strategy
        )
        
        self.trade_history.append(record)
        
        # 更新連續統計
        if is_win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
        
        # 更新日/週統計
        self.daily_pnl += pnl_percent
        self.weekly_pnl += pnl_percent
        self.daily_trades += 1
        self.weekly_trades += 1
        
        # 更新餘額
        self.update_balance(self.current_balance + pnl)
        
        # 更新最大回撤
        current_dd = self.get_current_drawdown()
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd
        
        # 設置冷卻期
        self._set_cooldown_if_needed(pnl)
        
        self.last_trade_time = datetime.now(timezone.utc)
    
    def _set_cooldown_if_needed(self, pnl: float):
        """根據虧損設置冷卻期"""
        if pnl < 0:
            if self.consecutive_losses >= self.config.risk.consecutive_loss_threshold_3:
                # 連虧 3 次以上，長冷卻
                cooldown_seconds = self.config.trading.cooldown_after_consecutive_loss
            else:
                # 一般虧損，短冷卻
                cooldown_seconds = self.config.trading.cooldown_after_loss
            
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
    
    def get_win_rate(self, lookback: int = 20) -> float:
        """
        計算勝率
        
        Args:
            lookback: 回看交易筆數
            
        Returns:
            勝率 (0-1)
        """
        if len(self.trade_history) == 0:
            return 0.5  # 預設 50%
        
        recent_trades = list(self.trade_history)[-lookback:]
        wins = sum(1 for t in recent_trades if t.is_win)
        
        return wins / len(recent_trades)
    
    def get_current_drawdown(self) -> float:
        """計算當前回撤"""
        if self.peak_balance <= 0:
            return 0.0
        
        drawdown = (self.peak_balance - self.current_balance) / self.peak_balance
        return max(0, drawdown)
    
    def calculate_leverage(self) -> float:
        """
        計算動態槓桿
        
        Returns:
            計算後的槓桿倍數
        """
        self._check_reset_periods()
        
        leverage = self.config.leverage.base_leverage
        risk_config = self.config.risk
        
        # Step 1: 勝率調整
        win_rate = self.get_win_rate()
        if win_rate > risk_config.win_rate_boost_threshold:
            leverage *= risk_config.win_rate_boost_multiplier
        elif win_rate < risk_config.win_rate_reduce_threshold:
            leverage *= risk_config.win_rate_reduce_multiplier
        
        # Step 2: 連虧保護 (優先級最高)
        if self.consecutive_losses >= risk_config.consecutive_loss_threshold_3:
            return self.config.leverage.min_leverage
        elif self.consecutive_losses >= risk_config.consecutive_loss_threshold_2:
            leverage *= risk_config.consecutive_loss_multiplier
        
        # Step 3: 連勝獎勵
        if self.consecutive_wins >= risk_config.consecutive_win_threshold:
            leverage *= risk_config.consecutive_win_multiplier
        
        # Step 4: 日內虧損保護
        if self.daily_pnl < -risk_config.max_daily_loss:
            return 0  # 停止交易
        elif self.daily_pnl < -risk_config.max_daily_loss * 0.5:
            leverage *= 0.5
        
        # Step 5: 回撤保護
        current_dd = self.get_current_drawdown()
        if current_dd > risk_config.max_drawdown:
            return 0  # 停止交易
        elif current_dd > risk_config.max_drawdown * 0.7:
            leverage *= 0.6
        
        # Step 6: 週盈利保護
        if self.weekly_pnl > risk_config.weekly_profit_protection:
            leverage = min(leverage, self.config.leverage.base_leverage)
        
        # Step 7: 範圍限制
        leverage = max(
            self.config.leverage.min_leverage,
            min(leverage, self.config.leverage.max_leverage)
        )
        
        return leverage
    
    def can_trade(self) -> tuple[bool, Optional[str]]:
        """
        檢查是否可以交易
        
        Returns:
            (是否可交易, 原因)
        """
        self._check_reset_periods()
        
        # 檢查冷卻期
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now(timezone.utc)).seconds
            return False, f"冷卻期中，剩餘 {remaining} 秒"
        
        # 檢查日內虧損
        if self.daily_pnl < -self.config.risk.max_daily_loss:
            return False, f"日內虧損達到上限 ({self.daily_pnl*100:.2f}%)"
        
        # 檢查回撤
        current_dd = self.get_current_drawdown()
        if current_dd > self.config.risk.max_drawdown:
            return False, f"回撤達到上限 ({current_dd*100:.2f}%)"
        
        # 檢查連續虧損
        if self.consecutive_losses >= self.config.risk.max_consecutive_losses:
            return False, f"連續虧損 {self.consecutive_losses} 次，需要暫停檢討"
        
        # 計算槓桿，如果為 0 則不能交易
        leverage = self.calculate_leverage()
        if leverage <= 0:
            return False, "槓桿計算為 0，風險保護觸發"
        
        return True, None
    
    def should_emergency_stop(self) -> tuple[bool, Optional[str]]:
        """
        檢查是否應該緊急停止
        
        Returns:
            (是否停止, 原因)
        """
        # 日內暴虧
        if self.daily_pnl < -0.1:
            return True, "日內暴虧超過 10%"
        
        # 帳戶權益過低
        if self.current_balance < self.initial_balance * 0.5:
            return True, "帳戶權益低於初始資金 50%"
        
        # 連續快速虧損
        if self.consecutive_losses >= 3 and self.last_trade_time:
            time_since_last = (datetime.now(timezone.utc) - self.last_trade_time).seconds
            if time_since_last < 1800:  # 30 分鐘內
                return True, "30 分鐘內連續虧損 3 次"
        
        return False, None
    
    def get_metrics(self) -> RiskMetrics:
        """取得風險指標"""
        self._check_reset_periods()
        
        can_trade, stop_reason = self.can_trade()
        leverage = self.calculate_leverage() if can_trade else 0
        
        return RiskMetrics(
            current_leverage=leverage,
            available_leverage=self.config.leverage.max_leverage,
            total_trades=len(self.trade_history),
            win_rate=self.get_win_rate(),
            consecutive_wins=self.consecutive_wins,
            consecutive_losses=self.consecutive_losses,
            daily_pnl=self.daily_pnl,
            weekly_pnl=self.weekly_pnl,
            total_pnl=(self.current_balance - self.initial_balance) / self.initial_balance,
            peak_balance=self.peak_balance,
            current_drawdown=self.get_current_drawdown(),
            max_drawdown=self.max_drawdown,
            can_trade=can_trade,
            stop_reason=stop_reason,
            cooldown_until=self.cooldown_until
        )
    
    def reset_daily(self):
        """重置日統計"""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.day_start = self._get_day_start()
    
    def reset_weekly(self):
        """重置週統計"""
        self.weekly_pnl = 0.0
        self.weekly_trades = 0
        self.week_start = self._get_week_start()
    
    def reset_all(self):
        """完全重置"""
        self.trade_history.clear()
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.daily_trades = 0
        self.weekly_trades = 0
        self.peak_balance = self.current_balance
        self.max_drawdown = 0.0
        self.cooldown_until = None
