"""
數據獲取模組
從 Lighter DEX 獲取 K線數據
"""
import asyncio
import pandas as pd
import numpy as np
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from dataclasses import dataclass

from config import settings


@dataclass
class Candle:
    """K線數據"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataFetcher:
    """
    數據獲取器
    
    從 Lighter DEX 獲取 K線數據
    """
    
    # 時間框架對應的秒數
    TIMEFRAME_SECONDS = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400
    }
    
    def __init__(self):
        self.config = settings
        self._api_client = None
        self._initialized = False
        
        # 緩存
        self._candle_cache: Dict[str, pd.DataFrame] = {}
        self._last_fetch_time: Dict[str, datetime] = {}
    
    async def initialize(self):
        """初始化"""
        if self._initialized:
            return

        try:
            from lighter import ApiClient, Configuration

            # 正确初始化 ApiClient
            configuration = Configuration(host=self.config.trading.host)
            self._api_client = ApiClient(configuration=configuration)
            self._initialized = True

        except ImportError:
            raise ImportError("請先安裝 lighter-sdk: pip install lighter-sdk")

    async def preload_data(self, market_id: int = None, min_candles: int = 500):
        """
        预加载足够的历史数据用于计算技术指标

        Args:
            market_id: 市场ID
            min_candles: 最小需要的K线数量（默认500条，约41.7小时的5m数据）
        """
        if market_id is None:
            market_id = self.config.trading.market_id

        try:
            # 直接通过HTTP API预加载更多数据
            import requests
            import time
            from datetime import datetime, timedelta

            headers = {"accept": "application/json"}
            all_candlesticks = []

            # 计算需要的时间范围（获取最近的数据）
            end_timestamp = int(time.time())
            # 获取足够的数据：500条5m线 = 500 * 5 * 60 = 250000秒 = 约69小时
            start_timestamp = end_timestamp - (min_candles * 5 * 60)

            print(f"[Market {market_id}] 开始预加载数据...")
            print(f"  时间范围: {datetime.fromtimestamp(start_timestamp)} 到 {datetime.fromtimestamp(end_timestamp)}")

            # 下载足够的数据
            url = (
                f"https://mainnet.zklighter.elliot.ai/api/v1/candlesticks?"
                f"market_id={market_id}&resolution=5m&start_timestamp={start_timestamp}&"
                f"end_timestamp={end_timestamp}&count_back={min_candles}&set_timestamp_to_end=true"
            )

            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            candlesticks = data.get('candlesticks', [])

            if candlesticks:
                print(f"[Market {market_id}] 从API获取到 {len(candlesticks)} 条原始数据")

                # 转换为DataFrame并存储到缓存
                records = []
                for candle in candlesticks:
                    # 确保时间戳处理正确
                    ts_ms = int(candle['timestamp'])
                    ts = datetime.fromtimestamp(ts_ms / 1000)
                    records.append({
                        'timestamp': ts,
                        'open': float(candle['open']),
                        'high': float(candle['high']),
                        'low': float(candle['low']),
                        'close': float(candle['close']),
                        'volume': float(candle.get('volume0', 0) or 0)
                    })

                df_fast = pd.DataFrame(records)
                df_fast = df_fast.sort_values('timestamp').reset_index(drop=True)

                # 创建15分钟数据
                df_slow = df_fast.copy()
                df_slow.set_index('timestamp', inplace=True)
                df_slow = df_slow.resample('15min').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                }).dropna().reset_index()

                # 存储到缓存
                cache_key_fast = f"{market_id}_5m"
                cache_key_slow = f"{market_id}_15m"
                self._candle_cache[cache_key_fast] = df_fast
                self._candle_cache[cache_key_slow] = df_slow
                self._last_fetch_time[cache_key_fast] = datetime.utcnow()
                self._last_fetch_time[cache_key_slow] = datetime.utcnow()

                # 显示最新价格信息
                if len(df_fast) > 0:
                    latest_5m = df_fast.iloc[-1]
                    print(f"[Market {market_id}] 预加载完成:")
                    print(f"  5m数据: {len(df_fast)} 条 (最新: ${latest_5m['close']:.4f} @ {latest_5m['timestamp'].strftime('%H:%M:%S')})")

                if len(df_slow) > 0:
                    latest_15m = df_slow.iloc[-1]
                    print(f"  15m数据: {len(df_slow)} 条 (最新: ${latest_15m['close']:.4f} @ {latest_15m['timestamp'].strftime('%H:%M:%S')})")

                return True
            else:
                print(f"[Market {market_id}] 预加载失败: 没有获取到数据")
                return False

        except Exception as e:
            print(f"[Market {market_id}] 预加载错误: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def get_candles(
        self,
        timeframe: str,
        count: int = 100,
        market_id: int = None
    ) -> pd.DataFrame:
        """
        獲取 K線數據
        
        Args:
            timeframe: 時間框架 (1m, 5m, 15m, 1h, 4h, 1d)
            count: K線數量
            market_id: 市場 ID
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        await self.initialize()
        
        if market_id is None:
            market_id = self.config.trading.market_id
        
        # 檢查緩存
        cache_key = f"{market_id}_{timeframe}"
        if self._should_use_cache(cache_key, timeframe):
            return self._candle_cache[cache_key].copy()
        
        if settings.dry_run:
            # 模擬模式生成假數據
            return self._generate_mock_candles(count)
        
        try:
            from lighter.api import CandlestickApi
            
            candle_api = CandlestickApi(self._api_client)
            
            # 計算時間範圍
            end_time = int(datetime.utcnow().timestamp())
            start_time = end_time - (self.TIMEFRAME_SECONDS[timeframe] * count)
            
            response = await candle_api.candlesticks(
                market_id=market_id,
                resolution=timeframe,
                start_timestamp=start_time,
                end_timestamp=end_time,
                count_back=count
            )
            
            # 轉換為 DataFrame
            candles = []
            for c in response.candlesticks or []:
                candles.append({
                    'timestamp': datetime.fromtimestamp(c.timestamp),
                    'open': float(c.open),
                    'high': float(c.high),
                    'low': float(c.low),
                    'close': float(c.close),
                    'volume': float(c.volume0 or 0)
                })
            
            df = pd.DataFrame(candles)
            
            if len(df) > 0:
                df = df.sort_values('timestamp').reset_index(drop=True)
            
            # 更新緩存
            self._candle_cache[cache_key] = df
            self._last_fetch_time[cache_key] = datetime.utcnow()
            
            return df
            
        except Exception as e:
            raise Exception(f"獲取 K線數據失敗: {e}")
    
    def _should_use_cache(self, cache_key: str, timeframe: str) -> bool:
        """檢查是否應該使用緩存"""
        if cache_key not in self._candle_cache:
            return False
        
        if cache_key not in self._last_fetch_time:
            return False
        
        # 緩存有效期為時間框架的一半
        cache_duration = self.TIMEFRAME_SECONDS[timeframe] / 2
        elapsed = (datetime.utcnow() - self._last_fetch_time[cache_key]).total_seconds()
        
        return elapsed < cache_duration
    
    def _generate_mock_candles(self, count: int) -> pd.DataFrame:
        """生成模擬 K線數據"""
        np.random.seed(42)
        
        # 基準價格
        base_price = 50000.0
        
        timestamps = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []
        
        current_price = base_price
        current_time = datetime.utcnow() - timedelta(minutes=count * 5)
        
        for i in range(count):
            # 隨機價格變動
            change = np.random.normal(0, 0.002) * current_price
            
            open_price = current_price
            close_price = current_price + change
            
            high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, 0.001)))
            low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, 0.001)))
            
            volume = abs(np.random.normal(100, 30))
            
            timestamps.append(current_time)
            opens.append(open_price)
            highs.append(high_price)
            lows.append(low_price)
            closes.append(close_price)
            volumes.append(volume)
            
            current_price = close_price
            current_time += timedelta(minutes=5)
        
        return pd.DataFrame({
            'timestamp': timestamps,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes
        })
    
    async def get_current_price(self, market_id: int = None) -> float:
        """獲取當前價格"""
        df = await self.get_candles("1m", count=1, market_id=market_id)
        
        if len(df) > 0:
            return df['close'].iloc[-1]
        
        return 0.0
    
    async def get_dual_timeframe_data(
        self,
        market_id: int = None
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        獲取雙時間框架數據

        Returns:
            (fast_df, slow_df) - 快速和慢速時間框架的 DataFrame
        """
        if market_id is None:
            market_id = self.config.trading.market_id

        fast_tf = self.config.timeframe.fast_tf
        slow_tf = self.config.timeframe.slow_tf
        candle_count = self.config.timeframe.candle_count

        # 並行獲取兩個時間框架的數據
        fast_task = self.get_candles(fast_tf, candle_count, market_id)
        slow_task = self.get_candles(slow_tf, candle_count, market_id)

        fast_df, slow_df = await asyncio.gather(fast_task, slow_task)

        # 記錄最新價格到 console
        if len(fast_df) > 0:
            latest_price = fast_df['close'].iloc[-1]
            latest_time = fast_df['timestamp'].iloc[-1]
            print(f"[Market {market_id}] 最新價格: ${latest_price:.4f} (時間: {latest_time.strftime('%H:%M:%S')}) | 5m K線數: {len(fast_df)}條")

            # 記錄 15m 最新價格
            if len(slow_df) > 0:
                latest_15m_price = slow_df['close'].iloc[-1]
                latest_15m_time = slow_df['timestamp'].iloc[-1]
                print(f"[Market {market_id}] 15m 最新價格: ${latest_15m_price:.4f} (時間: {latest_15m_time.strftime('%H:%M:%S')}) | 15m K線數: {len(slow_df)}條")

        return fast_df, slow_df
    
    def clear_cache(self):
        """清除緩存"""
        self._candle_cache.clear()
        self._last_fetch_time.clear()
    
    async def close(self):
        """關閉連接"""
        self._initialized = False


# 全域數據獲取器實例
data_fetcher = DataFetcher()
