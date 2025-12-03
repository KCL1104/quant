import discord  
import os  
import dotenv
from discord import app_commands  
import asyncio
from typing import Optional

# 全域變數，用於與 TradingBot 交互
trading_bot_instance = None

dotenv.load_dotenv()

# 使用預設 intents，不啟用任何特權 intents  
intents = discord.Intents.default()  
client = discord.Client(intents=intents)  
tree = app_commands.CommandTree(client)  
  
# 目標頻道 ID  
TARGET_CHANNEL_ID = 1445689711921332315  # 替換為實際頻道 ID  
channel = None

@client.event
async def on_ready():
    """機器人啟動完成"""
    global channel
    print(f'Discord Bot 已登入身分：{client.user}')
    await tree.sync()
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

@tree.command()
async def status(interaction: discord.Interaction):
    """獲取實時交易狀態報告（從 API 獲取最新數據）"""
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

def run_discord_bot(token, bot_instance):
    """運行 Discord 機器人"""
    global trading_bot_instance
    trading_bot_instance = bot_instance
    
    # 在異步循環中運行
    asyncio.create_task(client.start(token))