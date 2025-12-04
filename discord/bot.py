import discord  
import os  
import dotenv
from discord import app_commands  
import asyncio
from typing import Optional

# 全域變數，用於與 TradingBot 交互
trading_bot_instance = None

# 全域變數，用於存儲最新的指標數據 (由 main.py 更新)
latest_indicators: dict = {}

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


def run_discord_bot(token, bot_instance):
    """運行 Discord 機器人"""
    global trading_bot_instance
    trading_bot_instance = bot_instance
    
    # 在異步循環中運行
    asyncio.create_task(client.start(token))
    
    # 打印確認信息
    print(f"[Discord Bot] trading_bot_instance 已設置: {trading_bot_instance is not None}")