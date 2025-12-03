"""
日誌模組
使用 loguru 提供結構化日誌
"""
import sys
from pathlib import Path
from datetime import datetime
from loguru import logger

from config import settings


def setup_logger():
    """設置日誌"""
    
    # 移除預設 handler
    logger.remove()
    
    # 日誌格式
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    
    # 控制台輸出
    logger.add(
        sys.stdout,
        format=log_format,
        level="DEBUG" if settings.debug else "INFO",
        colorize=True
    )
    
    # 確保日誌目錄存在
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # 一般日誌文件
    logger.add(
        log_dir / "bot_{time:YYYY-MM-DD}.log",
        format=log_format,
        level="INFO",
        rotation="00:00",
        retention="30 days",
        compression="gz"
    )
    
    # 錯誤日誌文件
    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        format=log_format,
        level="ERROR",
        rotation="00:00",
        retention="30 days",
        compression="gz"
    )
    
    # 交易日誌文件
    logger.add(
        log_dir / "trades_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        level="INFO",
        filter=lambda record: record["extra"].get("trade", False),
        rotation="00:00",
        retention="90 days"
    )
    
    return logger


def log_trade(
    action: str,
    symbol: str,
    side: str,
    amount: float,
    price: float,
    pnl: float = None,
    **kwargs
):
    """
    記錄交易日誌
    
    Args:
        action: 動作 (OPEN/CLOSE/SL/TP)
        symbol: 交易對
        side: 方向 (LONG/SHORT)
        amount: 數量
        price: 價格
        pnl: 盈虧 (平倉時)
    """
    extra_info = " | ".join([f"{k}={v}" for k, v in kwargs.items()])
    
    if pnl is not None:
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        message = f"{action} | {symbol} | {side} | amount={amount:.6f} | price={price:.2f} | PnL={pnl_str}"
    else:
        message = f"{action} | {symbol} | {side} | amount={amount:.6f} | price={price:.2f}"
    
    if extra_info:
        message += f" | {extra_info}"
    
    logger.bind(trade=True).info(message)


def log_signal(
    strategy: str,
    signal_type: str,
    price: float,
    strength: float,
    reason: str
):
    """記錄訊號日誌"""
    logger.info(
        f"SIGNAL | {strategy} | {signal_type} | price={price:.2f} | "
        f"strength={strength:.2f} | {reason}"
    )


def log_risk(
    event: str,
    leverage: float,
    win_rate: float,
    drawdown: float,
    **kwargs
):
    """記錄風險日誌"""
    extra_info = " | ".join([f"{k}={v}" for k, v in kwargs.items()])
    message = (
        f"RISK | {event} | leverage={leverage:.2f}x | "
        f"win_rate={win_rate*100:.1f}% | drawdown={drawdown*100:.2f}%"
    )
    if extra_info:
        message += f" | {extra_info}"
    logger.info(message)


# 初始化日誌
bot_logger = setup_logger()
