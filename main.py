"""
Lighter Quant Trading Bot
主程式入口
"""
import asyncio
import signal
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import os
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

from config import settings, MarketRegime, SignalType, StrategyType
from core import (
    indicators,
    market_detector,
    get_market_detector,
    RiskManager,
    position_manager,
    IndicatorValues,
    MarketState,
    signal_readiness_checker,
)
from strategies import (
    momentum_v2,
    mean_reversion_v2,
    Signal,
)

# 定義策略實例
momentum_strategy = momentum_v2.momentum_strategy_v2
mean_reversion_strategy = mean_reversion_v2.mean_reversion_strategy_v2
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
        # 使用配置中的市場設置，而不是硬編碼
        self.market_configs = self.config.trading.markets
        if not self.market_configs:
            # 回退到默認
            self.market_configs = [
                ("ETH", 0),
                ("BNB", 25)
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
        # 使用 SIGUSR1 (在 Linux/Unix 上) 來觸發報告
        if hasattr(signal, 'SIGUSR1'):
            signal.signal(signal.SIGUSR1, self._handle_report_signal)
    
    def _handle_report_signal(self, signum, frame):
        """處理報告請求信號"""
        logger.info("收到報告請求信號，正在生成當前交易報告...")
        # 由於這是信號處理程序，最好異步調用或安排任務
        # 這裡我們簡單地打印到控制台
        self._print_current_status_report()

    def _print_current_status_report(self):
        """打印當前狀態報告"""
        print("\n" + "=" * 80)
        print(f"                    實時交易狀態報告 ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC)")
        print("=" * 80)
        
        # 1. 帳戶概況
        print(f"\n【帳戶概況】")
        if self.risk_manager:
            metrics = self.risk_manager.get_metrics()
            print(f"當前餘額:       ${self.risk_manager.current_balance:.2f}")
            print(f"初始餘額:       ${self.risk_manager.initial_balance:.2f}")
            print(f"總盈虧:         ${metrics.total_pnl:.2f} ({(metrics.total_pnl/self.risk_manager.initial_balance)*100:.2f}%)")
            print(f"當前最大回撤:   {metrics.current_drawdown*100:.2f}%")
            print(f"勝率:           {metrics.win_rate*100:.1f}%")
        else:
            print("風險管理器未初始化")
            
        # 2. 持倉狀態
        print(f"\n【持倉狀態】")
        has_positions = False
        for symbol, position in self.positions.items():
            if position and position.size != 0:
                has_positions = True
                pnl_percent = (position.unrealized_pnl / (position.entry_price * abs(position.size))) * 100 if position.entry_price else 0
                print(f"  {symbol:<5} | 方向: {position.side:<5} | 數量: {position.size:.6f} | "
                      f"入場: ${position.entry_price:.2f} | PnL: ${position.unrealized_pnl:.2f} ({pnl_percent:.2f}%)")
                
                # 如果有相關信號信息
                if self.signals.get(symbol):
                    sig = self.signals[symbol]
                    print(f"        策略: {sig.strategy.value} | SL: ${sig.stop_loss:.2f} | TP: ${sig.take_profit:.2f}")
        
        if not has_positions:
            print("  目前無持倉")
            
        # 3. 市場監控
        print(f"\n【監控市場】")
        for symbol, market_id in self.market_configs:
            status = "監控中"
            if self.signals.get(symbol):
                status = f"已開倉 ({self.signals[symbol].strategy.value})"
            elif self.positions.get(symbol):
                 status = "持有倉位 (無信號)"
            print(f"  {symbol:<5} (ID: {market_id:<2}) | 狀態: {status}")
            
        print("\n" + "=" * 80 + "\n")
        
    async def get_status_report_dict(self, fetch_realtime: bool = False):
        """
        獲取結構化的狀態報告數據 (供 Discord Bot 使用)

        Args:
            fetch_realtime: 是否從 API 獲取實時數據 (True) 或使用內存數據 (False)
        """

        # 1. 帳戶概況
        account_data = {}

        if fetch_realtime:
            # 從 API 獲取實時數據
            try:
                from exchange import lighter_client as lc_module
                account_info = await lc_module.get_account_info()

                # 計算盈虧
                initial_balance = self.risk_manager.initial_balance if self.risk_manager else account_info.balance
                total_pnl = account_info.total_asset_value - initial_balance
                pnl_percent = (total_pnl / initial_balance * 100) if initial_balance > 0 else 0

                # 獲取風險指標
                metrics = self.risk_manager.get_metrics() if self.risk_manager else None

                account_data = {
                    "current_balance": account_info.balance,
                    "initial_balance": initial_balance,
                    "total_asset_value": account_info.total_asset_value,
                    "available_balance": account_info.available_balance,
                    "total_pnl": total_pnl,
                    "pnl_percent": pnl_percent,
                    "drawdown": metrics.current_drawdown*100 if metrics else 0,
                    "win_rate": metrics.win_rate*100 if metrics else 0,
                    "leverage": account_info.leverage
                }
            except Exception as e:
                logger.error(f"獲取實時帳戶數據失敗: {e}")
                # 降級到內存數據
                fetch_realtime = False

        if not fetch_realtime and self.risk_manager:
            # 使用內存數據
            metrics = self.risk_manager.get_metrics()
            account_data = {
                "current_balance": self.risk_manager.current_balance,
                "initial_balance": self.risk_manager.initial_balance,
                "total_pnl": metrics.total_pnl,
                "pnl_percent": (metrics.total_pnl/self.risk_manager.initial_balance)*100,
                "drawdown": metrics.current_drawdown*100,
                "win_rate": metrics.win_rate*100
            }

        # 2. 持倉狀態
        positions_data = []

        if fetch_realtime:
            # 從 API 獲取實時持倉
            try:
                from exchange import lighter_client as lc_module
                for symbol, market_id in self.market_configs:
                    position = await lc_module.get_position(market_id=market_id)
                    if position and position.size != 0:
                        side = "LONG" if position.size > 0 else "SHORT"
                        pnl_percent = (position.unrealized_pnl / (position.entry_price * abs(position.size))) * 100 if position.entry_price else 0

                        pos_info = {
                            "symbol": symbol,
                            "side": side,
                            "size": abs(position.size),
                            "entry_price": position.entry_price,
                            "pnl": position.unrealized_pnl,
                            "pnl_percent": pnl_percent,
                            "liquidation_price": position.liquidation_price,
                            "leverage": position.leverage
                        }

                        # 如果有相關信號信息
                        if self.signals.get(symbol):
                            sig = self.signals[symbol]
                            pos_info.update({
                                "strategy": sig.strategy.value,
                                "sl": sig.stop_loss,
                                "tp": sig.take_profit
                            })

                        positions_data.append(pos_info)
            except Exception as e:
                logger.error(f"獲取實時持倉數據失敗: {e}")
                # 降級到內存數據
                fetch_realtime = False

        if not fetch_realtime:
            # 使用內存數據
            for symbol, position in self.positions.items():
                if position and position.size != 0:
                    pnl_percent = (position.unrealized_pnl / (position.entry_price * abs(position.size))) * 100 if position.entry_price else 0

                    pos_info = {
                        "symbol": symbol,
                        "side": position.side,
                        "size": abs(position.size),
                        "entry_price": position.entry_price,
                        "pnl": position.unrealized_pnl,
                        "pnl_percent": pnl_percent
                    }

                    # 如果有相關信號信息
                    if self.signals.get(symbol):
                        sig = self.signals[symbol]
                        pos_info.update({
                            "strategy": sig.strategy.value,
                            "sl": sig.stop_loss,
                            "tp": sig.take_profit
                        })

                    positions_data.append(pos_info)

        # 3. 市場監控
        markets_data = []
        for symbol, market_id in self.market_configs:
            status = "監控中"
            if self.signals.get(symbol):
                status = f"已開倉 ({self.signals[symbol].strategy.value})"
            elif self.positions.get(symbol):
                 status = "持有倉位 (無信號)"

            markets_data.append({
                "symbol": symbol,
                "id": market_id,
                "status": status
            })

        return {
            "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            "data_source": "實時 API" if fetch_realtime else "內存快取",
            "account": account_data,
            "positions": positions_data,
            "markets": markets_data
        }
        
    async def _send_discord_notification(self, message: str):
        """發送 Discord 通知（安全版本，不會拋出異常）"""
        try:
            from discord.bot import send_notification
            await send_notification(message)
        except ImportError:
            # Discord 模組未安裝或未配置
            pass
        except Exception as e:
            logger.error(f"發送 Discord 通知失敗: {e}")
    
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

        # 為每個市場初始化槓桿
        base_leverage = self.config.leverage.base_leverage
        margin_mode = self.config.leverage.margin_mode
        logger.info(f"初始化槓桿設定: {base_leverage}x, 模式: {'全倉' if margin_mode == 0 else '逐倉'}")

        for symbol, market_id in self.market_configs:
            try:
                result = await lighter_client.update_leverage(
                    leverage=base_leverage,
                    market_id=market_id,
                    margin_mode=margin_mode
                )
                if result.success:
                    logger.info(f"[{symbol}] 槓桿初始化成功: {base_leverage}x")
                else:
                    logger.warning(f"[{symbol}] 槓桿初始化失敗: {result.message}")
            except Exception as e:
                logger.error(f"[{symbol}] 槓桿初始化異常: {e}")
            
            # 避免 API 速率限制 (1 request per second)
            await asyncio.sleep(1.2)

        logger.info("初始化完成")
    
    async def run(self):
        """運行主循環（多市場並行）"""
        await self.initialize()
        
        # 啟動 Discord Bot
        discord_token = os.getenv("DISCORD_TOKEN")
        if discord_token:
            try:
                # 設置 discord logger 級別為 WARNING，隱藏不必要的日誌
                import logging
                logging.getLogger("discord").setLevel(logging.WARNING)
                logging.getLogger("discord.http").setLevel(logging.WARNING)
                logging.getLogger("discord.gateway").setLevel(logging.WARNING)
                logging.getLogger("discord.client").setLevel(logging.WARNING)
                logging.getLogger("discord.webhook").setLevel(logging.WARNING)
                
                from discord.bot import run_discord_bot, send_notification
                run_discord_bot(discord_token, self)
                logger.info("Discord Bot 已啟動")
                
                # 發送啟動通知
                start_msg = (
                    f"🚀 **Lighter Quant Bot 已啟動**\n"
                    f"時間: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                    f"模式: {'模擬交易 (Dry Run)' if self.config.dry_run else '實盤交易'}\n"
                    f"交易市場: {', '.join([s for s, _ in self.market_configs])}\n"
                    f"帳戶餘額: ${self.risk_manager.initial_balance:.2f}"
                )
                # 等待一小段時間讓 Discord Bot 連接成功
                # 使用 create_task 來發送通知，避免阻塞主線程
                # 添加錯誤處理以防止未處理的異常導致事件循環崩潰
                async def send_start_notification():
                    await asyncio.sleep(5)
                    try:
                        await send_notification(start_msg)
                        logger.info("Discord 啟動通知已發送")
                    except Exception as e:
                        logger.error(f"發送 Discord 啟動通知失敗: {e}")
                
                asyncio.create_task(send_start_notification())
                
            except Exception as e:
                logger.error(f"Discord Bot 啟動失敗: {e}")
        else:
            logger.warning("未設置 DISCORD_TOKEN，Discord Bot 未啟動")
        
        self.is_running = True
        
        # 計算循環間隔 (快速時間框架的秒數)
        interval_seconds = data_fetcher.TIMEFRAME_SECONDS[self.config.timeframe.fast_tf]
        
        logger.info(f"開始多市場交易循環，間隔: {interval_seconds} 秒")
        logger.info(f"並行交易市場: {len(self.market_configs)} 個")

        # 創建後台任務
        tasks = []

        # 1. 帳戶同步任務
        sync_interval = self.config.trading.account_sync_interval
        logger.info(f"啟動帳戶同步任務，間隔: {sync_interval} 秒")
        sync_task = asyncio.create_task(
            self._account_sync_loop(sync_interval),
            name="AccountSync"
        )
        tasks.append(sync_task)

        # 2. 為每個市場創建交易任務
        for symbol, market_id in self.market_configs:
            task = asyncio.create_task(
                self._market_trading_loop(symbol, market_id, interval_seconds),
                name=f"Trading-{symbol}"
            )
            tasks.append(task)

        # 並行運行所有任務
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # 檢查並記錄任何異常
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    task_name = tasks[i].get_name() if hasattr(tasks[i], 'get_name') else f"Task-{i}"
                    logger.error(f"任務 {task_name} 發生錯誤: {result}")
        except Exception as e:
            logger.error(f"交易系統錯誤: {e}")
        
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

    async def _account_sync_loop(self, interval_seconds: int):
        """
        帳戶數據同步循環
        定期從 API 獲取最新的帳戶數據並更新內存狀態
        """
        logger.info(f"帳戶同步循環已啟動")

        while not self.should_stop:
            try:
                # 獲取實時帳戶數據
                account_info = await lighter_client.get_account_info()

                # 更新風險管理器的餘額
                if self.risk_manager:
                    old_balance = self.risk_manager.current_balance
                    self.risk_manager.update_balance(account_info.balance)

                    # 如果餘額有顯著變化，記錄日誌
                    balance_change = account_info.balance - old_balance
                    if abs(balance_change) > 0.01:  # 變化超過 0.01 USDC
                        logger.debug(f"帳戶餘額更新: ${old_balance:.2f} -> ${account_info.balance:.2f} (Δ{balance_change:+.2f})")

                # 更新績效追蹤器
                metrics_tracker.update_equity(account_info.balance)

                # 同步各市場的持倉數據
                for symbol, market_id in self.market_configs:
                    # 從 API 獲取實時持倉
                    api_position = await lighter_client.get_position(market_id=market_id)

                    # 更新內存中的持倉
                    if api_position and api_position.size != 0:
                        # 如果 API 有持倉但內存中沒有，說明可能錯過了某些事件
                        if not self.positions.get(symbol) or self.positions[symbol].size == 0:
                            logger.warning(f"[{symbol}] 檢測到 API 持倉但內存中無記錄，同步中...")

                        self.positions[symbol] = api_position
                    else:
                        # API 無持倉，清空內存記錄
                        if self.positions.get(symbol) and self.positions[symbol].size != 0:
                            logger.warning(f"[{symbol}] API 無持倉但內存中有記錄，已清除")
                            self.positions[symbol] = None

                # 等待下一個同步週期
                await asyncio.sleep(interval_seconds)

            except Exception as e:
                logger.error(f"帳戶同步錯誤: {e}")
                # 錯誤後等待較短時間重試
                await asyncio.sleep(min(10, interval_seconds))

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
        
        # 4.1 更新 Discord Bot 的指標數據 (用於價格通知)
        try:
            from discord.bot import update_indicators
            update_indicators(symbol, indicator_values)
        except ImportError:
            pass  # Discord 模組未安裝
        
        # 4.5 更新模擬價格 (dry run 模式)
        if self.config.dry_run:
            lighter_client.set_simulated_price(indicator_values.current_price)
        
        # 5. 判斷市場狀態 (使用該市場專屬的檢測器，避免多市場狀態污染)
        detector = get_market_detector(market_id)
        market_state = detector.detect(indicator_values)
        logger.debug(f"[{symbol}] 市場狀態: {market_state.regime.value} - {market_state.description}")
        
        # 5.5 計算並更新訊號準備度 (用於 Discord 通知)
        try:
            readiness_data = signal_readiness_checker.get_all_readiness(indicator_values, market_state)
            from discord.bot import update_signal_readiness
            update_signal_readiness(symbol, readiness_data)
        except ImportError:
            pass  # Discord 模組未安裝
        except Exception as e:
            logger.debug(f"[{symbol}] 訊號準備度更新失敗: {e}")
        
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
                    holding_periods = (datetime.now(timezone.utc) - self.entry_times[symbol]).total_seconds()
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
        
        # 更新槓桿 (使用配置中的保證金模式)
        await lighter_client.update_leverage(
            leverage,
            market_id=market_id,
            margin_mode=self.config.leverage.margin_mode
        )

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
            amount=position_size.base_amount,
            market_id=market_id,
            current_price=signal.entry_price
        )
        
        if result.success:
            self.signals[symbol] = signal
            self.entry_times[symbol] = datetime.now(timezone.utc)
            
            # 設置止損止盈單
            await self._set_sl_tp_orders_for_market(symbol, market_id, signal, position_size.base_amount)
            
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
            
            # 發送 Discord 通知
            msg = (
                f"🟢 **開倉通知** - {symbol}\n"
                f"方向: {signal.signal_type.value}\n"
                f"策略: {signal.strategy.value}\n"
                f"價格: ${signal.entry_price:.2f}\n"
                f"數量: {position_size.base_amount:.6f}\n"
                f"止損: ${signal.stop_loss:.2f} | 止盈: ${signal.take_profit:.2f}\n"
                f"原因: {signal.reason}"
            )
            await self._send_discord_notification(msg)
        else:
            logger.error(f"[{symbol}] 開倉失敗: {result.message}")
    
    async def _set_sl_tp_orders_for_market(self, symbol: str, market_id: int, signal: Signal, amount: float):
        """為指定市場設置止損止盈單 - 使用 OCO 組合訂單"""

        # 使用 OCO 組合訂單同時設置止損和止盈
        # 這樣當一個被觸發時，另一個會自動取消
        result = await lighter_client.create_sl_tp_orders(
            signal_type=signal.signal_type,
            amount=amount,
            stop_loss_price=signal.stop_loss,
            take_profit_price=signal.take_profit,
            market_id=market_id
        )

        if result.success:
            logger.debug(
                f"[{symbol}] 止盈止損 OCO 訂單設置成功 - "
                f"止損: {signal.stop_loss:.2f}, 止盈: {signal.take_profit:.2f}"
            )
        else:
            logger.warning(f"[{symbol}] 止盈止損 OCO 訂單設置失敗: {result.message}")
    
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
                    exit_time=datetime.now(timezone.utc),
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
            
            # 發送 Discord 通知
            position = self.positions[symbol]
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            pnl_percent = (pnl / (entry_price * abs(position.size))) * 100 if entry_price else 0
            
            msg = (
                f"🔴 **平倉通知** - {symbol}\n"
                f"原因: {reason}\n"
                f"數量: {abs(position.size):.6f}\n"
                f"盈虧: {pnl_emoji} ${pnl:.2f} ({pnl_percent:.2f}%)"
            )
            await self._send_discord_notification(msg)
            
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
        
        # 更新槓桿 (使用配置中的保證金模式)
        await lighter_client.update_leverage(
            leverage,
            margin_mode=self.config.leverage.margin_mode
        )
        
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
            amount=position_size.base_amount,
            current_price=signal.entry_price
        )

        if result.success:
            self.current_signal = signal
            self.entry_time = datetime.now(timezone.utc)
            
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
                    exit_time=datetime.now(timezone.utc),
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
