import os
import time
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from flask import Flask, request, abort, send_from_directory

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 設定 matplotlib 後端 (無介面模式) ---
matplotlib.use('Agg')

app = Flask(__name__)

# --- 1. 設定密鑰 (Render 環境變數優先，找不到則使用預設值) ---
# 建議：為了安全，之後請在 Render 後台設定這些變數，不要將真實密鑰推送到公開的 GitHub
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '0k2eulC1Ewzjg5O0FiLVPH3ShF3RdgzcThaCsTh4vil0FqvsOZ97kw8m6AHhaZ7YVk3nedStFUyQ9hv/6lGD9xc5o+2OC/BGE4Ua3z95PICP1lF6WWTdlXnfRe++hqhPrX6f4rMZ7wjVvMTZrJvXqwdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', 'a6de3f291be03ffe87b72790cad5496a')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 準備字型與圖片目錄 ---
static_dir = 'static_images'
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

font_file = 'TaipeiSansTCBeta-Regular.ttf'
# 如果本地沒有字型檔，嘗試下載 (防呆機制)
if not os.path.exists(font_file):
    print("找不到字型檔，正在下載...")
    import urllib.request
    url = "https://drive.google.com/uc?id=1eGAsTN1HBpJAkeVM57_C7ccp7hbgSz3_&export=download"
    urllib.request.urlretrieve(url, font_file)

my_font = FontProperties(fname=font_file)

# --- 3. 核心功能 A: 繪圖引擎 (旗艦版) ---
def create_stock_chart(stock_code):
    try:
        target = stock_code.upper().strip()
        if target.isdigit() and len(target) == 4:
            target += ".TW"
        
        # 抓取資料
        ticker = yf.Ticker(target)
        df = ticker.history(period="1y")
        
        if df.empty: return None, "找不到資料或代號錯誤"

        # 計算指標
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()
        std = df['Close'].rolling(window=20).std()
        df['Upper'] = df['MA20'] + (2 * std)
        df['Lower'] = df['MA20'] - (2 * std)
        
        # RSI 計算
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 訊號
        df['Signal'] = np.where(df['MA20'] > df['MA60'], 1.0, 0.0)
        df['Position'] = df['Signal'].diff()
        golden = df[df['Position'] == 1.0]
        death = df[df['Position'] == -1.0]

        # 開始繪圖
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 12), sharex=True, gridspec_kw={'height_ratios': [3, 1, 1]})

        # 主圖：股價 + 布林 + 均線
        ax1.plot(df.index, df['Close'], color='black', alpha=0.6, linewidth=1, label='收盤價')
        ax1.plot(df.index, df['MA20'], color='#FF9900', linestyle='--', label='月線')
        ax1.plot(df.index, df['MA60'], color='#0066CC', linewidth=2, label='季線')
        ax1.fill_between(df.index, df['Upper'], df['Lower'], color='skyblue', alpha=0.2)
        
        # 標記買賣點
        ax1.plot(golden.index, golden['MA20'], '^', color='red', markersize=14, markeredgecolor='black', label='黃金交叉')
        ax1.plot(death.index, death['MA20'], 'v', color='green', markersize=14, markeredgecolor='black', label='死亡交叉')
        
        ax1.set_title(f"{target} 專業分析圖", fontsize=22, fontproperties=my_font, fontweight='bold')
        ax1.legend(loc='upper left', prop=my_font)
        ax1.grid(True, linestyle=':', alpha=0.5)

        # 副圖1：成交量
        colors = ['red' if c >= o else 'green' for c, o in zip(df['Close'], df['Open'])]
        ax2.bar(df.index, df['Volume'], color=colors, alpha=0.8)
        ax2.set_ylabel("成交量", fontproperties=my_font)
        ax2.grid(True, linestyle=':', alpha=0.3)

        # 副圖2：RSI
        ax3.plot(df.index, df['RSI'], color='purple', linewidth=1.5, label='RSI')
        ax3.axhline(70, color='red', linestyle='--', alpha=0.5)
        ax3.axhline(30, color='green', linestyle='--', alpha=0.5)
        ax3.set_ylabel("RSI", fontproperties=my_font)
        ax3.grid(True, linestyle=':', alpha=0.3)
        ax3.set_ylim(0, 100)

        fig.autofmt_xdate()
        
        # 存檔
        filename = f"{target.replace('.', '_')}_{int(time.time())}.png"
        filepath = os.path.join(static_dir, filename)
        plt.savefig(filepath, bbox_inches='tight')
        plt.close()
        
        return filename, "Success"
    except Exception as e:
        print(e)
        return None, str(e)

# --- 4. 核心功能 B: 智能選股 (含交易策略) ---
def scan_potential_stocks():
    # 觀察名單
    watch_list = [
        '2303.TW', '2353.TW', '2324.TW', '2356.TW', '2409.TW', '3481.TW', 
        '2603.TW', '2609.TW', '2615.TW', '2618.TW', '2610.TW', '2606.TW',
        '2884.TW', '2885.TW', '2886.TW', '2890.TW', '2891.TW', '2892.TW', 
        '2002.TW', '2014.TW', '1605.TW', '1904.TW', '1314.TW',
        '3231.TW', '2382.TW', '2376.TW', '2312.TW', '1101.TW'
    ]
    
    recommendations = []
    
    try:
        # 批次下載以節省時間
        data = yf.download(watch_list, period="3mo")
        
        for stock in watch_list:
            try:
                # 處理資料格式 (單一股票 vs 多股票)
                if isinstance(data.columns, pd.MultiIndex):
                    closes = data['Close'][stock]
                else:
                    closes = data['Close']
                
                closes = closes.dropna()
                if len(closes) < 60: continue

                current_price = closes.iloc[-1]
                
                # 篩選條件：股價<100, 且為多頭排列
                if current_price > 100: continue
                
                ma20 = closes.rolling(20).mean().iloc[-1]
                ma60 = closes.rolling(60).mean().iloc[-1]
                std = closes.rolling(20).std().iloc[-1]
                
                if ma20 > ma60 and current_price > ma20:
                    bias = (current_price - ma20) / ma20 * 100
                    
                    if bias < 10: # 乖離率小於 10%
                        # 計算策略
                        stop_loss = ma20 * 0.99
                        upper_band = ma20 + (2 * std)
                        target_price = max(upper_band, current_price * 1.05)
                        stock_name = stock.replace('.TW','')
                        
                        info = (
                            f"📌 {stock_name}\n"
                            f"💰 現價: {current_price:.1f}\n"
                            f"🎯 目標: {target_price:.1f}\n"
                            f"🛑 停損: {stop_loss:.1f}"
                        )
                        recommendations.append(info)
            except:
                continue
    except Exception as e:
        return [f"掃描發生錯誤: {str(e)}"]

    return recommendations[:6]

# --- 5. Flask 路由設定 ---

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "Hello, Stock Bot is Running!"

@app.route('/images/<filename>')
def serve_image(filename):
    return send_from_directory(static_dir, filename)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    if user_msg == "推薦" or user_msg == "選股":
        results = scan_potential_stocks()
        if results:
            reply_text = "📊 【百元潛力股交易計畫】\n(純屬演算法分析，非投資建議)\n====================\n"
            reply_text += "\n\n".join(results)
            reply_text += "\n====================\n💡 建議策略：\n接近月線買進，破停損賣出，\n到目標價分批獲利。"
        else:
            reply_text = "目前市場震盪，無符合高勝率條件的個股，建議觀望。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    else:
        # 產生圖片網址 (使用 Render 的 URL)
        img_filename, err_msg = create_stock_chart(user_msg)
        if img_filename:
            # 修正: 強制使用 https (Render 有時會回傳 http，但 Line 需要 https)
            root_url = request.host_url.replace("http://", "https://")
            img_url = root_url + 'images/' + img_filename
            
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(original_content_url=img_url, preview_image_url=img_url)
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"請輸入代號查詢，或輸入「推薦」獲取交易策略。\n(錯誤: {err_msg})")
            )

if __name__ == "__main__":
    app.run()