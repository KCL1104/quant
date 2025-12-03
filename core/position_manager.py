"""
倉位管理模組
計算倉位大小、止損止盈價格
"""
from dataclasses import dataclass
from typing import Optional

from config import settings, SignalType, StrategyType


@dataclass
class PositionSize:
    """倉位計算結果"""
    size: float                   # 倉位大小 (USD)
    base_amount: float            # 基礎資產數量
    leverage: float               # 使用的槓桿
    risk_amount: float            # 風險金額
    stop_distance: float          # 止損距離
    stop_distance_percent: float  # 止損距離百分比


@dataclass
class StopLossTarget:
    """止損止盈目標"""
    stop_loss: float              # 止損價格
    take_profit: float            # 止盈價格
    risk_reward_ratio: float      # 實際風報比
    stop_distance_percent: float  # 止損距離百分比
    profit_distance_percent: float  # 止盈距離百分比


class PositionManager:
    """倉位管理器"""
    
    def __init__(self):
        self.config = settings
    
    def calculate_position_size(
        self,
        balance: float,
        leverage: float,
        current_price: float,
        stop_loss_price: float,
        signal_type: SignalType,
        strength: float = 1.0
    ) -> PositionSize:
        """
        計算倉位大小
        
        Args:
            balance: 帳戶餘額
            leverage: 使用的槓桿
            current_price: 當前價格
            stop_loss_price: 止損價格
            signal_type: 訊號類型 (LONG/SHORT)
            strength: 訊號強度 (用於調整倉位)
            
        Returns:
            PositionSize 倉位計算結果
        """
        risk_per_trade = self.config.risk.risk_per_trade
        max_position_ratio = self.config.risk.max_position_ratio
        min_stop_distance_percent = 0.003  # 最小止損距離 0.3%
        
        # Step 1: 確定風險金額
        risk_amount = balance * risk_per_trade
        
        # Step 2: 計算止損距離
        if signal_type == SignalType.LONG:
            stop_distance = current_price - stop_loss_price
        else:
            stop_distance = stop_loss_price - current_price
        
        # 確保止損距離為正數
        stop_distance = abs(stop_distance)
        stop_distance_percent = stop_distance / current_price if current_price > 0 else 0
        
        # Step 3: 檢查最小止損距離
        if stop_distance_percent < min_stop_distance_percent:
            # 止損距離過小，返回空倉位
            return PositionSize(
                size=0,
                base_amount=0,
                leverage=leverage,
                risk_amount=risk_amount,
                stop_distance=stop_distance,
                stop_distance_percent=stop_distance_percent
            )
        
        # Step 4: 計算基礎倉位
        base_position = risk_amount / stop_distance_percent
        
        # Step 5: 根據強度調整
        momentum_config = self.config.momentum
        if strength > momentum_config.strong_strength:
            base_position *= momentum_config.strong_position_multiplier
        elif strength < momentum_config.min_strength:
            base_position *= momentum_config.weak_position_multiplier
        
        # Step 6: 套用槓桿
        leveraged_position = base_position * leverage
        
        # Step 7: 限制最大倉位
        max_position = balance * leverage * max_position_ratio
        final_position = min(leveraged_position, max_position)
        
        # 確保最小交易金額
        if final_position < self.config.trading.min_trade_amount:
            final_position = 0
        
        # 計算基礎資產數量
        base_amount = final_position / current_price if current_price > 0 else 0
        
        return PositionSize(
            size=final_position,
            base_amount=base_amount,
            leverage=leverage,
            risk_amount=risk_amount,
            stop_distance=stop_distance,
            stop_distance_percent=stop_distance_percent
        )
    
    def calculate_momentum_stops(
        self,
        entry_price: float,
        supertrend_value: float,
        signal_type: SignalType
    ) -> StopLossTarget:
        """
        計算 Momentum 策略的止損止盈
        
        止損使用 Supertrend 值
        止盈使用固定風報比
        
        Args:
            entry_price: 進場價格
            supertrend_value: Supertrend 值
            signal_type: 訊號類型
            
        Returns:
            StopLossTarget
        """
        rr_ratio = self.config.momentum.risk_reward_ratio
        
        if signal_type == SignalType.LONG:
            stop_loss = supertrend_value
            stop_distance = entry_price - stop_loss
            take_profit = entry_price + (stop_distance * rr_ratio)
        else:  # SHORT
            stop_loss = supertrend_value
            stop_distance = stop_loss - entry_price
            take_profit = entry_price - (stop_distance * rr_ratio)
        
        stop_distance_percent = abs(stop_distance) / entry_price if entry_price > 0 else 0
        profit_distance = abs(take_profit - entry_price)
        profit_distance_percent = profit_distance / entry_price if entry_price > 0 else 0
        
        actual_rr = profit_distance / abs(stop_distance) if stop_distance != 0 else 0
        
        return StopLossTarget(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=actual_rr,
            stop_distance_percent=stop_distance_percent,
            profit_distance_percent=profit_distance_percent
        )
    
    def calculate_mean_reversion_stops(
        self,
        entry_price: float,
        bb_lower: float,
        bb_middle: float,
        bb_upper: float,
        signal_type: SignalType
    ) -> StopLossTarget:
        """
        計算 Mean Reversion 策略的止損止盈
        
        止損在 BB 軌外
        止盈目標是 BB 中軌
        
        Args:
            entry_price: 進場價格
            bb_lower: BB 下軌
            bb_middle: BB 中軌
            bb_upper: BB 上軌
            signal_type: 訊號類型
            
        Returns:
            StopLossTarget
        """
        mr_config = self.config.mean_reversion
        band_width = bb_upper - bb_lower
        
        if signal_type == SignalType.LONG:
            # 做多：止損在下軌下方，止盈在中軌
            stop_loss = bb_lower - (band_width * mr_config.stop_loss_bb_multiplier)
            take_profit = bb_middle
        else:  # SHORT
            # 做空：止損在上軌上方，止盈在中軌
            stop_loss = bb_upper + (band_width * mr_config.stop_loss_bb_multiplier)
            take_profit = bb_middle
        
        stop_distance = abs(entry_price - stop_loss)
        profit_distance = abs(take_profit - entry_price)
        
        stop_distance_percent = stop_distance / entry_price if entry_price > 0 else 0
        profit_distance_percent = profit_distance / entry_price if entry_price > 0 else 0
        
        actual_rr = profit_distance / stop_distance if stop_distance != 0 else 0
        
        return StopLossTarget(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=actual_rr,
            stop_distance_percent=stop_distance_percent,
            profit_distance_percent=profit_distance_percent
        )
    
    def adjust_for_slippage(
        self,
        price: float,
        is_entry: bool,
        signal_type: SignalType
    ) -> float:
        """
        調整價格以考慮滑點
        
        Args:
            price: 原始價格
            is_entry: 是否為進場
            signal_type: 訊號類型
            
        Returns:
            調整後的價格
        """
        slippage = self.config.trading.slippage_tolerance
        
        if is_entry:
            # 進場時：買入加滑點，賣出減滑點
            if signal_type == SignalType.LONG:
                return price * (1 + slippage)
            else:
                return price * (1 - slippage)
        else:
            # 出場時：相反
            if signal_type == SignalType.LONG:
                return price * (1 - slippage)
            else:
                return price * (1 + slippage)
    
    def validate_stop_loss(
        self,
        entry_price: float,
        stop_loss: float,
        signal_type: SignalType,
        min_distance_percent: float = 0.005
    ) -> tuple[bool, str]:
        """
        驗證止損設置
        
        Args:
            entry_price: 進場價格
            stop_loss: 止損價格
            signal_type: 訊號類型
            min_distance_percent: 最小止損距離百分比
            
        Returns:
            (是否有效, 原因)
        """
        if signal_type == SignalType.LONG:
            if stop_loss >= entry_price:
                return False, "做多止損必須低於進場價"
            
            distance_percent = (entry_price - stop_loss) / entry_price
        else:
            if stop_loss <= entry_price:
                return False, "做空止損必須高於進場價"
            
            distance_percent = (stop_loss - entry_price) / entry_price
        
        if distance_percent < min_distance_percent:
            return False, f"止損距離過小 ({distance_percent*100:.2f}% < {min_distance_percent*100:.2f}%)"
        
        if distance_percent > 0.1:  # 10%
            return False, f"止損距離過大 ({distance_percent*100:.2f}% > 10%)"
        
        return True, "OK"


# 全域倉位管理器
position_manager = PositionManager()
