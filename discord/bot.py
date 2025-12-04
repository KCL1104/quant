import discord  
import os  
import dotenv
from discord import app_commands  
import asyncio
from typing import Optional, Dict

# 全域變數，用於與 TradingBot 交互
trading_bot_instance = None

# 全域變數，用於存儲最新的指標數據 (由 main.py 更新)
latest_indicators: dict = {}

# 全域變數，用於存儲最新的訊號準備度數據
latest_signal_readiness: Dict[str, dict] = {}

dotenv.load_dotenv()

# 使用預設 intents，不啟用任何特權 intents  
intents = discord.Intents.default()  
client = discord.Client(intents=intents)  
tree = app_commands.CommandTree(client)  
  
# 目標頻道 ID  
TARGET_CHANNEL_ID = 1445689711921332315  # 替換為實際頻道 ID  
# Guild ID for immediate slash command sync (set to None for global sync only)
# 設置你的 Discord 伺服器 ID 以立即同步斜線指令
GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # 可選: 設置為你的伺服器 ID
channel = None

@client.event
async def on_ready():
    """機器人啟動完成"""
    global channel
    print(f'Discord Bot 已登入身分：{client.user}')
    
    # Sync commands - guild-specific for immediate availability, then global
    try:
        if GUILD_ID:
            # 優先同步到指定伺服器 (立即生效)
            guild = discord.Object(id=int(GUILD_ID))
            tree.copy_global_to(guild=guild)  # 複製全域指令到 guild
            synced = await tree.sync(guild=guild)
            print(f"已同步 {len(synced)} 個指令到伺服器 {GUILD_ID} (立即生效)")
        
        # 全域同步 (可能需要最多 1 小時生效)
        synced = await tree.sync()
        print(f"已全域同步 {len(synced)} 個指令 (可能需要時間生效)")
    except Exception as e:
        print(f"指令同步失敗: {e}")
    
    channel = client.get_channel(TARGET_CHANNEL_ID)
    if not channel:
        print(f"警告: 找不到目標頻道 ID {TARGET_CHANNEL_ID}")

@tree.command()  
async def sendmsg(interaction: discord.Interaction, message: str):  
    """發送訊息到指定頻道"""  
    global channel
    if not channel:
        channel = client.get_channel(TARGET_CHANNEL_ID)
      
    if not channel:  
        await interaction.response.send_message('❌ 找不到目標頻道')  
        return  
      
    await channel.send(f'📨 來自 {interaction.user.name} 的訊息：{message}')  
    await interaction.response.send_message('✅ 訊息已發送')  
  
@tree.command()  
async def stat(interaction: discord.Interaction):  
    """顯示機器人統計資訊"""  
    guild_count = len(client.guilds)  
    member_count = sum(guild.member_count for guild in client.guilds)  
      
    embed = discord.Embed(  
        title="📊 機器人統計",  
        color=discord.Color.blue()  
    )  
    embed.add_field(name="伺服器數量", value=str(guild_count))  
    embed.add_field(name="總成員數", value=str(member_count))  
      
    await interaction.response.send_message(embed=embed)  

@tree.command(name="status_now")
async def status_now(interaction: discord.Interaction):
    """獲取實時交易狀態報告（從 API 獲取最新數據）"""
    global trading_bot_instance
    
    # Debug: 打印狀態
    print(f"[Discord Bot] /status_now 被觸發, trading_bot_instance={trading_bot_instance is not None}")
    
    if not trading_bot_instance:
        await interaction.response.send_message("❌ 交易機器人未連接")
        return

    await interaction.response.defer()  # 延遲回應，因為 API 請求需要時間

    try:
        # 從 API 獲取實時數據
        report = await trading_bot_instance.get_status_report_dict(fetch_realtime=True)
        
        embed = discord.Embed(
            title=f"📊 實時交易狀態報告",
            description=f"時間: {report['timestamp']}\n數據來源: **{report['data_source']}**",
            color=discord.Color.green()
        )

        # 帳戶概況
        acc = report['account']
        account_text = f"""
            當前餘額: ${acc['current_balance']:.2f}
            初始餘額: ${acc['initial_balance']:.2f}
            總盈虧: ${acc['total_pnl']:.2f} ({acc['pnl_percent']:.2f}%)
            最大回撤: {acc['drawdown']:.2f}%
            勝率: {acc['win_rate']:.1f}%
            """
        # 如果有額外字段（實時數據）
        if 'total_asset_value' in acc:
            account_text += f"總資產: ${acc['total_asset_value']:.2f}\n"
        if 'available_balance' in acc:
            account_text += f"可用餘額: ${acc['available_balance']:.2f}\n"
        if 'leverage' in acc:
            account_text += f"槓桿: {acc['leverage']:.1f}x\n"

        embed.add_field(name="💰 帳戶概況", value=account_text, inline=False)
        
        # 持倉狀態
        if report['positions']:
            pos_text = ""
            for p in report['positions']:
                pos_text += f"**{p['symbol']}** ({p['side']})\n"
                pos_text += f"數量: {p['size']:.6f} @ ${p['entry_price']:.2f}\n"
                pos_text += f"PnL: ${p['pnl']:.2f} ({p['pnl_percent']:.2f}%)\n"

                # 策略信息
                if p.get('strategy'):
                    pos_text += f"策略: {p['strategy']} | SL: ${p['sl']:.2f} | TP: ${p['tp']:.2f}\n"

                # 實時數據額外字段
                if p.get('liquidation_price'):
                    pos_text += f"清算價: ${p['liquidation_price']:.2f}\n"
                if p.get('leverage'):
                    pos_text += f"槓桿: {p['leverage']:.1f}x\n"

                pos_text += "---\n"
            embed.add_field(name="📈 持倉狀態", value=pos_text, inline=False)
        else:
            embed.add_field(name="📈 持倉狀態", value="目前無持倉", inline=False)
            
        # 市場監控
        market_text = ""
        for m in report['markets']:
            market_text += f"`{m['symbol']:<5}` (ID: {m['id']}) | {m['status']}\n"
        embed.add_field(name="👀 市場監控", value=market_text, inline=False)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ 獲取報告失敗: {str(e)}")

@tree.command(name="signals")
async def signals(interaction: discord.Interaction):
    """獲取所有市場的訊號準備度"""
    global trading_bot_instance, latest_signal_readiness
    
    if not trading_bot_instance:
        await interaction.response.send_message("❌ 交易機器人未連接")
        return
    
    await interaction.response.defer()
    
    try:
        # 從 trading_bot 獲取市場配置
        market_configs = trading_bot_instance.market_configs
        
        embed = discord.Embed(
            title="📊 訊號準備度報告",
            description="各市場進場條件準備狀態",
            color=discord.Color.blue()
        )
        
        for symbol, market_id in market_configs:
            if symbol in latest_signal_readiness:
                data = latest_signal_readiness[symbol]
                field_value = _format_signal_embed_field(data)
            else:
                field_value = "⚠️ 無數據 - 等待下一次計算"
            
            embed.add_field(
                name=f"💹 {symbol}",
                value=field_value,
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ 獲取訊號準備度失敗: {str(e)}")


def _format_signal_embed_field(readiness_data: dict) -> str:
    """格式化單一市場的訊號準備度為 embed field"""
    momentum_long = readiness_data.get('momentum_long')
    momentum_short = readiness_data.get('momentum_short')
    mr_long = readiness_data.get('mr_long')
    mr_short = readiness_data.get('mr_short')
    
    # 判斷市場狀態
    if momentum_long and momentum_long.conditions:
        market_regime_cond = momentum_long.conditions[0]
        is_trending = market_regime_cond.status.value == "met"
    else:
        is_trending = False
    
    if is_trending:
        strategy = "📈 Momentum"
        long_r = momentum_long
        short_r = momentum_short
    else:
        strategy = "⇄ Mean Reversion"
        long_r = mr_long
        short_r = mr_short
    
    result = f"**{strategy}**\n"
    
    # Long
    if long_r:
        pct = long_r.readiness_percent
        met = long_r.met_count
        total = long_r.total_count
        status = "🟢" if pct == 100 else "🟡" if pct >= 70 else "🟠" if pct >= 40 else "🔴"
        result += f"{status} LONG: **{met}/{total}** ({pct:.0f}%)\n"
    else:
        result += "⚪ LONG: N/A\n"
    
    # Short
    if short_r:
        pct = short_r.readiness_percent
        met = short_r.met_count
        total = short_r.total_count
        status = "🟢" if pct == 100 else "🟡" if pct >= 70 else "🟠" if pct >= 40 else "🔴"
        result += f"{status} SHORT: **{met}/{total}** ({pct:.0f}%)"
    else:
        result += "⚪ SHORT: N/A"
    
    return result


@tree.command(name="signal_detail")
async def signal_detail(interaction: discord.Interaction, symbol: str):
    """獲取指定市場的詳細訊號準備度"""
    global latest_signal_readiness
    
    symbol = symbol.upper()
    
    if symbol not in latest_signal_readiness:
        await interaction.response.send_message(f"❌ 找不到 {symbol} 的訊號數據")
        return
    
    data = latest_signal_readiness[symbol]
    msg = format_signal_readiness_message(symbol, data)
    
    await interaction.response.send_message(msg)

async def send_notification(message: str):
    """發送通知到 Discord"""
    global channel
    if not channel:
        channel = client.get_channel(TARGET_CHANNEL_ID)
    
    if channel:
        await channel.send(message)


def update_indicators(symbol: str, indicator_values):
    """
    更新指定市場的最新指標數據
    
    Args:
        symbol: 市場符號 (e.g., "ETH", "BNB")
        indicator_values: IndicatorValues 實例
    """
    global latest_indicators
    latest_indicators[symbol] = indicator_values


def get_indicator_message(symbol: str) -> str:
    """
    獲取指定市場的指標訊息字串
    
    Args:
        symbol: 市場符號
        
    Returns:
        格式化的指標訊息
    """
    if symbol not in latest_indicators:
        return ""
    
    ind = latest_indicators[symbol]
    
    # Supertrend 方向
    st_fast_dir = "🟢 UP" if ind.supertrend_fast.direction.value == 1 else "🔴 DOWN"
    st_slow_dir = "🟢 UP" if ind.supertrend_slow.direction.value == 1 else "🔴 DOWN"
    
    # RSI 狀態
    if ind.rsi >= 70:
        rsi_status = "🔴 超買"
    elif ind.rsi <= 30:
        rsi_status = "🟢 超賣"
    else:
        rsi_status = "⚪ 中性"
    
    # 市場狀態 (ADX)
    if ind.adx >= 25:
        market_status = "📊 趨勢市" if ind.plus_di > ind.minus_di else "📊 趨勢市 (空)"
    else:
        market_status = "⇄ 震盪市"
    
    # BB Position
    if ind.bollinger.position >= 0.9:
        bb_status = "‼️ 近上軌"
    elif ind.bollinger.position <= 0.1:
        bb_status = "‼️ 近下軌"
    else:
        bb_status = f"{ind.bollinger.position:.0%}"
    
    msg = f"\n📈 **技術指標**\n"
    msg += f"└ Supertrend: 5m {st_fast_dir} | 15m {st_slow_dir}\n"
    msg += f"└ RSI({ind.rsi:.1f}): {rsi_status}\n"
    msg += f"└ ADX({ind.adx:.1f}): {market_status}\n"
    msg += f"└ BB Position: {bb_status}\n"
    msg += f"└ ATR: {ind.atr:.4f} ({ind.atr_percent*100:.2f}%)"
    
    return msg


def update_signal_readiness(symbol: str, readiness_data: dict):
    """
    更新指定市場的訊號準備度數據
    
    Args:
        symbol: 市場符號 (e.g., "ETH", "BNB")
        readiness_data: 訊號準備度數據字典
    """
    global latest_signal_readiness
    latest_signal_readiness[symbol] = readiness_data


def format_signal_readiness_message(symbol: str, readiness_data: dict) -> str:
    """
    格式化訊號準備度為 Discord 訊息
    
    Args:
        symbol: 市場符號
        readiness_data: 包含 'momentum_long', 'momentum_short', 'mr_long', 'mr_short' 的字典
    
    Returns:
        格式化的訊息字串
    """
    msg = f"📊 **{symbol} 訊號準備度**\n"
    msg += "━" * 25 + "\n\n"
    
    # 根據市場狀態顯示適用的策略
    momentum_long = readiness_data.get('momentum_long')
    momentum_short = readiness_data.get('momentum_short')
    mr_long = readiness_data.get('mr_long')
    mr_short = readiness_data.get('mr_short')
    
    # 判斷當前適用的策略 (基於市場狀態)
    # 趨勢市 -> Momentum, 震盪市 -> Mean Reversion
    if momentum_long:
        # 先檢查市場狀態
        market_regime_cond = momentum_long.conditions[0] if momentum_long.conditions else None
        is_trending = market_regime_cond and market_regime_cond.status.value == "met"
        
        if is_trending:
            msg += "**📈 趨勢市 - Momentum 策略**\n\n"
            msg += _format_single_readiness(momentum_long, "🟢 LONG")
            msg += "\n"
            msg += _format_single_readiness(momentum_short, "🔴 SHORT")
        else:
            msg += "**⇄ 震盪市 - Mean Reversion 策略**\n\n"
            msg += _format_single_readiness(mr_long, "🟢 LONG")
            msg += "\n"
            msg += _format_single_readiness(mr_short, "🔴 SHORT")
    
    return msg


def _format_single_readiness(readiness, direction_label: str) -> str:
    """
    格式化單一方向的準備度
    """
    if not readiness:
        return f"{direction_label}: 無數據\n"
    
    met = readiness.met_count
    total = readiness.total_count
    pct = readiness.readiness_percent
    
    # 準備度顏色
    if pct == 100:
        status_emoji = "🟢"
    elif pct >= 70:
        status_emoji = "🟡"
    elif pct >= 40:
        status_emoji = "🟠"
    else:
        status_emoji = "🔴"
    
    msg = f"{direction_label} ({readiness.strategy})\n"
    msg += f"{status_emoji} **{met}/{total}** 條件達成 ({pct:.0f}%)\n"
    
    # 條件詳情
    for cond in readiness.conditions:
        emoji = "✅" if cond.status.value == "met" else "❌"
        msg += f"  {emoji} {cond.name}\n"
        msg += f"      現值: `{cond.current_value}`\n"
        msg += f"      需要: `{cond.required_value}`\n"
    
    return msg


def get_signal_summary_message(symbol: str) -> str:
    """
    獲取簡短的訊號摘要訊息 (用於定期通知)
    
    Args:
        symbol: 市場符號
    
    Returns:
        簡短的訊號摘要
    """
    if symbol not in latest_signal_readiness:
        return f"{symbol}: 無訊號數據"
    
    data = latest_signal_readiness[symbol]
    
    # 取得所有準備度
    results = []
    
    momentum_long = data.get('momentum_long')
    momentum_short = data.get('momentum_short')
    mr_long = data.get('mr_long')
    mr_short = data.get('mr_short')
    
    # 找出最佳機會
    best = None
    best_pct = 0
    
    for name, readiness in [('MOM LONG', momentum_long), ('MOM SHORT', momentum_short), 
                            ('MR LONG', mr_long), ('MR SHORT', mr_short)]:
        if readiness and readiness.readiness_percent > best_pct:
            best_pct = readiness.readiness_percent
            best = (name, readiness)
    
    if best:
        name, readiness = best
        met = readiness.met_count
        total = readiness.total_count
        
        if best_pct == 100:
            status = "🟢 READY"
        elif best_pct >= 70:
            status = "🟡 ALMOST"
        else:
            status = "🔴 WAITING"
        
        return f"`{symbol}` {status} | 最佳: {name} ({met}/{total})"
    
    return f"`{symbol}` 🔴 無交易機會"


async def send_signal_readiness_notification(symbol: str, readiness_data: dict):
    """
    發送訊號準備度通知到 Discord
    """
    global channel
    if not channel:
        channel = client.get_channel(TARGET_CHANNEL_ID)
    
    if channel:
        msg = format_signal_readiness_message(symbol, readiness_data)
        await channel.send(msg)


def run_discord_bot(token, bot_instance):
    """運行 Discord 機器人"""
    global trading_bot_instance
    trading_bot_instance = bot_instance
    
    # 在異步循環中運行
    asyncio.create_task(client.start(token))
    
    # 打印確認信息
    print(f"[Discord Bot] trading_bot_instance 已設置: {trading_bot_instance is not None}")