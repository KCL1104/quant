#!/usr/bin/env python3
"""
測試 TP/SL 設置功能
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from exchange.data_fetcher import DataFetcher
from exchange.lighter_client import lighter_client
from config import SignalType

async def test_data_fetcher():
    """測試 DataFetcher 初始化和價格獲取"""
    print("=" * 60)
    print("測試 DataFetcher")
    print("=" * 60)

    try:
        # 測試 1: 創建 DataFetcher 實例（不傳參數）
        print("\n1. 創建 DataFetcher 實例...")
        data_fetcher = DataFetcher()
        print("   ✅ 成功創建實例")

        # 測試 2: 獲取當前價格
        print("\n2. 獲取 ETH (market_id=0) 當前價格...")
        price = await data_fetcher.get_current_price(market_id=0)
        print(f"   ✅ ETH 當前價格: ${price:.2f}")

        if price <= 0:
            print("   ⚠️  警告: 價格無效")
            return False

        return True

    except Exception as e:
        print(f"   ❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_tpsl_creation():
    """測試 TP/SL 訂單創建（模擬模式）"""
    print("\n" + "=" * 60)
    print("測試 TP/SL 訂單創建（dry_run 模式）")
    print("=" * 60)

    try:
        # 測試止損單
        print("\n3. 測試止損單創建...")
        sl_result = await lighter_client.create_stop_loss_order(
            signal_type=SignalType.LONG,
            amount=0.001,
            trigger_price=3000.0,
            market_id=0
        )

        if sl_result.success:
            print(f"   ✅ 止損單創建成功: {sl_result.message}")
            print(f"      Order ID: {sl_result.order_id}")
        else:
            print(f"   ❌ 止損單失敗: {sl_result.message}")
            return False

        # 測試止盈單
        print("\n4. 測試止盈單創建...")
        tp_result = await lighter_client.create_take_profit_order(
            signal_type=SignalType.LONG,
            amount=0.001,
            trigger_price=3500.0,
            market_id=0
        )

        if tp_result.success:
            print(f"   ✅ 止盈單創建成功: {tp_result.message}")
            print(f"      Order ID: {tp_result.order_id}")
        else:
            print(f"   ❌ 止盈單失敗: {tp_result.message}")
            return False

        return True

    except Exception as e:
        print(f"   ❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """主測試函數"""
    print("\n" + "=" * 60)
    print("TP/SL 功能測試")
    print("=" * 60)

    # 測試 1: DataFetcher
    test1_passed = await test_data_fetcher()

    # 測試 2: TP/SL 創建
    test2_passed = await test_tpsl_creation()

    # 總結
    print("\n" + "=" * 60)
    print("測試總結")
    print("=" * 60)
    print(f"DataFetcher 測試: {'✅ 通過' if test1_passed else '❌ 失敗'}")
    print(f"TP/SL 創建測試: {'✅ 通過' if test2_passed else '❌ 失敗'}")

    if test1_passed and test2_passed:
        print("\n🎉 所有測試通過！TP/SL 功能正常")
        return 0
    else:
        print("\n⚠️  部分測試失敗，請檢查錯誤訊息")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
