"""
Lighter Quant Trading Bot
主程式入口
"""
import asyncio
import signal
from datetime import datetime, timedelta
from typing import Optional
import uuid

from config import settings, MarketRegime, SignalType, StrategyType
from core import (
    indicators,
    market_detector,
    RiskManager,
    position_manager,
    IndicatorValues,
    MarketState,
)
from strategies import (
    momentum_strategy,
    mean_reversion_strategy,
    Signal,
)
from exchange import (
    lighter_client,
    data_fetcher,
    Position,
)
from utils import (
    bot_logger as logger,
    log_trade,
    log_signal,
    log_risk,
    metrics_tracker,
)


class TradingBot:
    """
    量化交易機器人
    
    整合所有模組，執行自動交易
    """
    
    def __init__(self):
        self.config = settings
        self.risk_manager: Optional[RiskManager] = None
        
        # 多市場配置 (symbol, market_id)
        self.market_configs = [
            ("BTC", 1),
            ("SOL", 2),
            ("ETH", 0)
        ]
        
        # 每個市場的狀態 (使用 symbol 作為 key)
        self.positions: dict[str, Optional[Position]] = {}
        self.signals: dict[str, Optional[Signal]] = {}
        self.entry_times: dict[str, Optional[datetime]] = {}
        
        # 初始化每個市場的狀態
        for symbol, _ in self.market_configs:
            self.positions[symbol] = None
            self.signals[symbol] = None
            self.entry_times[symbol] = None
        
        # 運行狀態
        self.is_running = False
        self.should_stop = False
        
        # 設置信號處理
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """處理關閉信號"""
        logger.info("收到關閉信號，準備停止...")
        self.should_stop = True
    
    async def initialize(self):
        """初始化機器人"""
        logger.info("=" * 50)
        logger.info("Lighter Quant Trading Bot 啟動（多幣種模式）")
        logger.info("=" * 50)
        
        # 顯示配置
        market_symbols = ", ".join([f"{s}({id})" for s, id in self.market_configs])
        logger.info(f"交易市場: {market_symbols}")
        logger.info(f"時間框架: {self.config.timeframe.fast_tf} / {self.config.timeframe.slow_tf}")
        logger.info(f"模擬模式: {self.config.dry_run}")
        
        # 初始化交易所客戶端
        await lighter_client.initialize()
        await data_fetcher.initialize()

        # 预加载每个市场的历史数据
        logger.info("开始预加载历史数据...")
        for symbol, market_id in self.market_configs:
            logger.info(f"[{symbol}] 预加载市场数据 (ID: {market_id})...")
            success = await data_fetcher.preload_data(market_id=market_id, min_candles=500)
            if success:
                logger.info(f"[{symbol}] 预加载完成")
            else:
                logger.warning(f"[{symbol}] 预加载失败，将使用正常API获取数据")
        
        # 取得帳戶資訊
        account = await lighter_client.get_account_info()
        logger.info(f"帳戶餘額: ${account.balance:.2f}")
        
        # 初始化風險管理器
        self.risk_manager = RiskManager(account.balance)
        
        # 檢查每個市場的現有持倉
        for symbol, market_id in self.market_configs:
            position = await lighter_client.get_position(market_id=market_id)
            if position and position.size != 0:
                logger.warning(f"[{symbol}] 檢測到現有持倉: {position.size:.6f}")
                self.positions[symbol] = position
        
        logger.info("初始化完成")
    
    async def run(self):
        """運行主循環（多市場並行）"""
        await self.initialize()
        
        self.is_running = True
        
        # 計算循環間隔 (快速時間框架的秒數)
        interval_seconds = data_fetcher.TIMEFRAME_SECONDS[self.config.timeframe.fast_tf]
        
        logger.info(f"開始多市場交易循環，間隔: {interval_seconds} 秒")
        logger.info(f"並行交易市場: {len(self.market_configs)} 個")
        
        # 為每個市場創建交易任務
        tasks = []
        for symbol, market_id in self.market_configs:
            task = asyncio.create_task(
                self._market_trading_loop(symbol, market_id, interval_seconds),
                name=f"Trading-{symbol}"
            )
            tasks.append(task)
        
        # 並行運行所有市場的交易循環
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"多市場交易循環錯誤: {e}")
        
        await self.shutdown()
    
    async def _market_trading_loop(self, symbol: str, market_id: int, interval_seconds: int):
        """單一市場的交易循環"""
        logger.info(f"[{symbol}] 開始交易循環")
        
        while not self.should_stop:
            try:
                await self._trading_cycle_for_market(symbol, market_id)
                
                # 等待下一個週期
                await asyncio.sleep(interval_seconds)
                
            except Exception as e:
                logger.error(f"[{symbol}] 交易循環錯誤: {e}")
                await asyncio.sleep(10)  # 錯誤後等待 10 秒
    
    async def _trading_cycle_for_market(self, symbol: str, market_id: int):
        """單一市場的交易循環"""
        
        # 1. 檢查是否可以交易
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            logger.debug(f"[{symbol}] 無法交易: {reason}")
            return
        
        # 2. 檢查緊急停止
        should_stop, stop_reason = self.risk_manager.should_emergency_stop()
        if should_stop:
            logger.error(f"[{symbol}] 緊急停止: {stop_reason}")
            await self._emergency_close_market(symbol, market_id)
            return
        
        # 3. 獲取市場數據
        try:
            fast_df, slow_df = await data_fetcher.get_dual_timeframe_data(market_id=market_id)
        except Exception as e:
            logger.error(f"[{symbol}] 獲取數據失敗: {e}")
            return
        
        if len(fast_df) < self.config.timeframe.candle_count * 0.5:
            logger.debug(f"[{symbol}] 數據不足，跳過本次循環")
            return
        
        # 4. 計算指標
        indicator_values = indicators.calculate_all(fast_df, slow_df)
        
        # 4.5 更新模擬價格 (dry run 模式)
        if self.config.dry_run:
            lighter_client.set_simulated_price(indicator_values.current_price)
        
        # 5. 判斷市場狀態
        market_state = market_detector.detect(indicator_values)
        logger.debug(f"[{symbol}] 市場狀態: {market_state.regime.value} - {market_state.description}")
        
        # 6. 檢查現有持倉
        self.positions[symbol] = await lighter_client.get_position(market_id=market_id)
        has_position = self.positions[symbol] and self.positions[symbol].size != 0
        
        # 7. 如果有持倉，檢查出場條件
        if has_position and self.signals[symbol]:
            should_exit, exit_reason = await self._check_exit_for_market(
                symbol, indicator_values
            )
            if should_exit:
                await self._close_position_for_market(symbol, market_id, exit_reason)
                return
            
            # 檢查時間止損 (Mean Reversion)
            if self.signals[symbol].strategy == StrategyType.MEAN_REVERSION:
                if self.entry_times[symbol]:
                    holding_periods = (datetime.utcnow() - self.entry_times[symbol]).total_seconds()
                    holding_periods /= data_fetcher.TIMEFRAME_SECONDS[self.config.timeframe.fast_tf]
                    
                    if holding_periods > self.config.mean_reversion.max_holding_periods:
                        await self._close_position_for_market(symbol, market_id, "時間止損")
                        return
        
        # 7.5 如果有持倉但沒有訊號記錄（可能是重啟後），記錄警告並使用基本止損檢查
        elif has_position and not self.signals[symbol]:
            logger.warning(f"[{symbol}] 檢測到持倉但無訊號記錄（可能是重啟後），使用基本止損邏輯")
            
            # 基本止損檢查：如果虧損超過 5%，平倉
            if self.positions[symbol].unrealized_pnl < 0:
                entry_value = abs(self.positions[symbol].size) * self.positions[symbol].entry_price
                loss_percent = abs(self.positions[symbol].unrealized_pnl) / entry_value if entry_value > 0 else 0
                
                if loss_percent > 0.05:  # 虧損超過 5%
                    await self._close_position_for_market(
                        symbol, market_id, f"重啟後止損 (虧損 {loss_percent*100:.2f}%)"
                    )
                    return
        
        # 8. 如果沒有持倉，檢查進場條件
        if not has_position:
            signal = await self._check_entry(indicator_values, market_state)
            if signal:
                await self._open_position_for_market(symbol, market_id, signal, indicator_values)
        
        # 9. 更新績效追蹤
        account = await lighter_client.get_account_info()
        self.risk_manager.update_balance(account.balance)
        metrics_tracker.update_equity(account.balance)
    
    async def _check_entry(
        self,
        indicators: IndicatorValues,
        market_state: MarketState
    ) -> Optional[Signal]:
        """檢查進場條件"""
        
        # 根據市場狀態選擇策略
        if market_state.regime == MarketRegime.TRENDING:
            signal = momentum_strategy.check_entry(indicators, market_state)
            if signal:
                log_signal(
                    "MOMENTUM",
                    signal.signal_type.value,
                    signal.entry_price,
                    signal.strength,
                    signal.reason
                )
                return signal
        
        elif market_state.regime == MarketRegime.RANGING:
            signal = mean_reversion_strategy.check_entry(indicators, market_state)
            if signal:
                log_signal(
                    "MEAN_REVERSION",
                    signal.signal_type.value,
                    signal.entry_price,
                    signal.strength,
                    signal.reason
                )
                return signal
        
        return None
    
    async def _check_exit(
        self,
        indicators: IndicatorValues
    ) -> tuple[bool, str]:
        """檢查出場條件"""
        if not self.current_signal or not self.current_position:
            return False, ""
        
        entry_price = self.current_position.entry_price
        current_pnl = self.current_position.unrealized_pnl
        current_pnl_percent = current_pnl / (entry_price * abs(self.current_position.size))
        
        if self.current_signal.strategy == StrategyType.MOMENTUM:
            return momentum_strategy.check_exit(
                indicators,
                entry_price,
                self.current_signal,
                current_pnl_percent
            )
        else:
            return mean_reversion_strategy.check_exit(
                indicators,
                entry_price,
                self.current_signal,
                current_pnl_percent
            )
    
    async def _check_exit_for_market(
        self,
        symbol: str,
        indicators: IndicatorValues
    ) -> tuple[bool, str]:
        """檢查單一市場的出場條件"""
        if not self.signals[symbol] or not self.positions[symbol]:
            return False, ""
        
        entry_price = self.positions[symbol].entry_price
        current_pnl = self.positions[symbol].unrealized_pnl
        current_pnl_percent = current_pnl / (entry_price * abs(self.positions[symbol].size))
        
        if self.signals[symbol].strategy == StrategyType.MOMENTUM:
            return momentum_strategy.check_exit(
                indicators,
                entry_price,
                self.signals[symbol],
                current_pnl_percent
            )
        else:
            return mean_reversion_strategy.check_exit(
                indicators,
                entry_price,
                self.signals[symbol],
                current_pnl_percent
            )
    
    async def _open_position_for_market(
        self,
        symbol: str,
        market_id: int,
        signal: Signal,
        indicators: IndicatorValues
    ):
        """為指定市場開倉"""
        
        # 計算槓桿
        leverage = self.risk_manager.calculate_leverage()
        if leverage <= 0:
            logger.warning(f"[{symbol}] 槓桿為 0，無法開倉")
            return
        
        # 計算倉位大小
        account = await lighter_client.get_account_info()
        position_size = position_manager.calculate_position_size(
            balance=account.available_balance,
            leverage=leverage,
            current_price=signal.entry_price,
            stop_loss_price=signal.stop_loss,
            signal_type=signal.signal_type,
            strength=signal.strength
        )
        
        if position_size.size <= 0:
            logger.warning(f"[{symbol}] 倉位計算為 0，無法開倉")
            return
        
        # 更新槓桿
        await lighter_client.update_leverage(leverage)
        
        logger.info(
            f"[{symbol}] 開倉: {signal.signal_type.value} | "
            f"價格={signal.entry_price:.2f} | "
            f"數量={position_size.base_amount:.6f} | "
            f"槓桿={leverage:.1f}x | "
            f"止損={signal.stop_loss:.2f} | "
            f"止盈={signal.take_profit:.2f}"
        )
        
        # 執行市價單
        result = await lighter_client.create_market_order(
            signal_type=signal.signal_type,
            amount=position_size.base_amount
        )
        
        if result.success:
            self.signals[symbol] = signal
            self.entry_times[symbol] = datetime.utcnow()
            
            # 設置止損止盈單
            await self._set_sl_tp_orders_for_market(symbol, signal, position_size.base_amount)
            
            log_trade(
                action="OPEN",
                symbol=symbol,
                side=signal.signal_type.value,
                amount=position_size.base_amount,
                price=signal.entry_price,
                strategy=signal.strategy.value,
                leverage=leverage
            )
            
            # 記錄風險狀態
            metrics = self.risk_manager.get_metrics()
            log_risk(
                event=f"[{symbol}] POSITION_OPENED",
                leverage=leverage,
                win_rate=metrics.win_rate,
                drawdown=metrics.current_drawdown
            )
        else:
            logger.error(f"[{symbol}] 開倉失敗: {result.message}")
    
    async def _set_sl_tp_orders_for_market(self, symbol: str, signal: Signal, amount: float):
        """為指定市場設置止損止盈單"""
        
        # 止損單
        sl_result = await lighter_client.create_stop_loss_order(
            signal_type=signal.signal_type,
            amount=amount,
            trigger_price=signal.stop_loss
        )
        
        if sl_result.success:
            logger.debug(f"[{symbol}] 止損單設置成功: {signal.stop_loss:.2f}")
        else:
            logger.warning(f"[{symbol}] 止損單設置失敗: {sl_result.message}")
        
        # 止盈單
        tp_result = await lighter_client.create_take_profit_order(
            signal_type=signal.signal_type,
            amount=amount,
            trigger_price=signal.take_profit
        )
        
        if tp_result.success:
            logger.debug(f"[{symbol}] 止盈單設置成功: {signal.take_profit:.2f}")
        else:
            logger.warning(f"[{symbol}] 止盈單設置失敗: {tp_result.message}")
    
    async def _close_position_for_market(self, symbol: str, market_id: int, reason: str):
        """平倉指定市場"""
        if not self.positions[symbol]:
            return
        
        logger.info(f"[{symbol}] 平倉原因: {reason}")
        
        # 取消所有掛單
        await lighter_client.cancel_all_orders(market_id=market_id)
        
        # 市價平倉
        result = await lighter_client.close_position(market_id=market_id)
        
        if result.success:
            # 計算盈虧
            entry_price = self.positions[symbol].entry_price
            exit_price = result.filled_price or self.positions[symbol].entry_price
            pnl = self.positions[symbol].unrealized_pnl
            
            # 記錄交易
            if self.signals[symbol] and self.entry_times[symbol]:
                metrics_tracker.record_trade(
                    trade_id=str(uuid.uuid4()),
                    strategy=self.signals[symbol].strategy,
                    side=self.signals[symbol].signal_type.value,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=abs(self.positions[symbol].size),
                    entry_time=self.entry_times[symbol],
                    exit_time=datetime.utcnow(),
                    exit_reason=reason
                )
            
            # 更新風險管理
            self.risk_manager.record_trade(
                pnl=pnl,
                strategy=self.signals[symbol].strategy.value if self.signals[symbol] else "unknown"
            )
            
            log_trade(
                action="CLOSE",
                symbol=symbol,
                side=self.signals[symbol].signal_type.value if self.signals[symbol] else "UNKNOWN",
                amount=abs(self.positions[symbol].size),
                price=exit_price,
                pnl=pnl,
                reason=reason
            )
            
            # 記錄風險狀態
            metrics = self.risk_manager.get_metrics()
            log_risk(
                event=f"[{symbol}] POSITION_CLOSED",
                leverage=metrics.current_leverage,
                win_rate=metrics.win_rate,
                drawdown=metrics.current_drawdown,
                daily_pnl=f"{metrics.daily_pnl*100:.2f}%"
            )
            
            # 重置狀態
            self.signals[symbol] = None
            self.entry_times[symbol] = None
            self.positions[symbol] = None
        else:
            logger.error(f"[{symbol}] 平倉失敗: {result.message}")
    
    async def _emergency_close_market(self, symbol: str, market_id: int):
        """緊急平倉指定市場"""
        logger.error(f"[{symbol}] 執行緊急平倉!")
        
        await lighter_client.cancel_all_orders(market_id=market_id)
        await lighter_client.close_position(market_id=market_id)

    
    async def _open_position(
        self,
        signal: Signal,
        indicators: IndicatorValues
    ):
        """開倉"""
        
        # 計算槓桿
        leverage = self.risk_manager.calculate_leverage()
        if leverage <= 0:
            logger.warning("槓桿為 0，無法開倉")
            return
        
        # 計算倉位大小
        account = await lighter_client.get_account_info()
        position_size = position_manager.calculate_position_size(
            balance=account.available_balance,
            leverage=leverage,
            current_price=signal.entry_price,
            stop_loss_price=signal.stop_loss,
            signal_type=signal.signal_type,
            strength=signal.strength
        )
        
        if position_size.size <= 0:
            logger.warning("倉位計算為 0，無法開倉")
            return
        
        # 更新槓桿
        await lighter_client.update_leverage(leverage)
        
        logger.info(
            f"開倉: {signal.signal_type.value} | "
            f"價格={signal.entry_price:.2f} | "
            f"數量={position_size.base_amount:.6f} | "
            f"槓桿={leverage:.1f}x | "
            f"止損={signal.stop_loss:.2f} | "
            f"止盈={signal.take_profit:.2f}"
        )
        
        # 執行市價單
        result = await lighter_client.create_market_order(
            signal_type=signal.signal_type,
            amount=position_size.base_amount
        )
        
        if result.success:
            self.current_signal = signal
            self.entry_time = datetime.utcnow()
            
            # 設置止損止盈單
            await self._set_sl_tp_orders(signal, position_size.base_amount)
            
            log_trade(
                action="OPEN",
                symbol=self.config.trading.market_symbol,
                side=signal.signal_type.value,
                amount=position_size.base_amount,
                price=signal.entry_price,
                strategy=signal.strategy.value,
                leverage=leverage
            )
            
            # 記錄風險狀態
            metrics = self.risk_manager.get_metrics()
            log_risk(
                event="POSITION_OPENED",
                leverage=leverage,
                win_rate=metrics.win_rate,
                drawdown=metrics.current_drawdown
            )
        else:
            logger.error(f"開倉失敗: {result.message}")
    
    async def _set_sl_tp_orders(self, signal: Signal, amount: float):
        """設置止損止盈單"""
        
        # 止損單
        sl_result = await lighter_client.create_stop_loss_order(
            signal_type=signal.signal_type,
            amount=amount,
            trigger_price=signal.stop_loss
        )
        
        if sl_result.success:
            logger.debug(f"止損單設置成功: {signal.stop_loss:.2f}")
        else:
            logger.warning(f"止損單設置失敗: {sl_result.message}")
        
        # 止盈單
        tp_result = await lighter_client.create_take_profit_order(
            signal_type=signal.signal_type,
            amount=amount,
            trigger_price=signal.take_profit
        )
        
        if tp_result.success:
            logger.debug(f"止盈單設置成功: {signal.take_profit:.2f}")
        else:
            logger.warning(f"止盈單設置失敗: {tp_result.message}")
    
    async def _close_position(self, reason: str):
        """平倉"""
        if not self.current_position:
            return
        
        logger.info(f"平倉原因: {reason}")
        
        # 取消所有掛單
        await lighter_client.cancel_all_orders()
        
        # 市價平倉
        result = await lighter_client.close_position()
        
        if result.success:
            # 計算盈虧
            entry_price = self.current_position.entry_price
            exit_price = result.filled_price or self.current_position.entry_price
            pnl = self.current_position.unrealized_pnl
            
            # 記錄交易
            if self.current_signal and self.entry_time:
                metrics_tracker.record_trade(
                    trade_id=str(uuid.uuid4()),
                    strategy=self.current_signal.strategy,
                    side=self.current_signal.signal_type.value,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=abs(self.current_position.size),
                    entry_time=self.entry_time,
                    exit_time=datetime.utcnow(),
                    exit_reason=reason
                )
            
            # 更新風險管理
            self.risk_manager.record_trade(
                pnl=pnl,
                strategy=self.current_signal.strategy.value if self.current_signal else "unknown"
            )
            
            log_trade(
                action="CLOSE",
                symbol=self.config.trading.market_symbol,
                side=self.current_signal.signal_type.value if self.current_signal else "UNKNOWN",
                amount=abs(self.current_position.size),
                price=exit_price,
                pnl=pnl,
                reason=reason
            )
            
            # 重置狀態
            self.current_signal = None
            self.entry_time = None
            self.current_position = None
            
            # 記錄風險狀態
            metrics = self.risk_manager.get_metrics()
            log_risk(
                event="POSITION_CLOSED",
                leverage=metrics.current_leverage,
                win_rate=metrics.win_rate,
                drawdown=metrics.current_drawdown,
                daily_pnl=f"{metrics.daily_pnl*100:.2f}%"
            )
        else:
            logger.error(f"平倉失敗: {result.message}")
    
    async def _emergency_close(self):
        """緊急平倉"""
        logger.error("執行緊急平倉!")
        
        await lighter_client.cancel_all_orders()
        await lighter_client.close_position()
        
        self.should_stop = True
    
    async def shutdown(self):
        """關閉機器人"""
        logger.info("正在關閉機器人...")
        
        # 檢查所有市場的持倉
        has_open_positions = False
        for symbol in self.positions.keys():
            if self.positions[symbol] and self.positions[symbol].size != 0:
                has_open_positions = True
                logger.warning(f"警告: [{symbol}] 仍有未平倉位!")
                logger.warning(f"[{symbol}] 持倉: {self.positions[symbol].size:.6f}")
        
        if not has_open_positions:
            logger.info("無未平倉位")
        
        # 關閉連接
        await lighter_client.close()
        await data_fetcher.close()
        
        # 顯示績效摘要
        print(metrics_tracker.get_summary())
        
        logger.info("機器人已關閉")
        self.is_running = False


async def main():
    """主函數"""
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
