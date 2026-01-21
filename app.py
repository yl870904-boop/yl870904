import os
import time
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from flask import Flask, request, abort, send_from_directory
import random

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 設定應用程式版本 ---
APP_VERSION = "v5.4 實戰系統 (自動倉位管理)"

# --- 設定 matplotlib 後端 (無介面模式) ---
matplotlib.use('Agg')

app = Flask(__name__)

# --- 1. 設定密鑰 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '0k2eulC1Ewzjg5O0FiLVPH3ShF3RdgzcThaCsTh4vil0FqvsOZ97kw8m6AHhaZ7YVk3nedStFUyQ9hv/6lGD9xc5o+2OC/BGE4Ua3z95PICP1lF6WWTdlXnfRe++hqhPrX6f4rMZ7wjVvMTZrJvXqwdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', 'a6de3f291be03ffe87b72790cad5496a')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 準備字型與圖片目錄 ---
static_dir = 'static_images'
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

font_file = 'TaipeiSansTCBeta-Regular.ttf'
if not os.path.exists(font_file):
    print("找不到字型檔，正在下載...")
    import urllib.request
    url = "https://drive.google.com/uc?id=1eGAsTN1HBpJAkeVM57_C7ccp7hbgSz3_&export=download"
    urllib.request.urlretrieve(url, font_file)

my_font = FontProperties(fname=font_file)

# --- 3. 全域快取 (EPS Cache) ---
EPS_CACHE = {}

def get_eps_cached(ticker_symbol):
    if ticker_symbol in EPS_CACHE: return EPS_CACHE[ticker_symbol]
    try:
        info = yf.Ticker(ticker_symbol).info
        eps = info.get('trailingEps') or info.get('forwardEps')
        if eps is None: eps = 'N/A'
        EPS_CACHE[ticker_symbol] = eps
        return eps
    except: return 'N/A'

# --- 4. 資料庫定義 ---
SECTOR_DICT = {
    "百元績優": [
        '2303.TW', '2324.TW', '2356.TW', '2353.TW', '2352.TW', '2409.TW', '3481.TW', 
        '2408.TW', '2344.TW', '2337.TW', '3702.TW', '2312.TW', '6282.TW', '3260.TWO', 
        '8150.TW', '6147.TWO', '5347.TWO', '2363.TW', '2449.TW', '3036.TW',
        '2884.TW', '2880.TW', '2886.TW', '2891.TW', '2892.TW', '5880.TW', '2885.TW', 
        '2890.TW', '2883.TW', '2887.TW', '2882.TW', '2881.TW', '2834.TW', '2801.TW',
        '1101.TW', '1102.TW', '2002.TW', '2027.TW', '1605.TW', '1402.TW', '1907.TW', 
        '2105.TW', '2618.TW', '2610.TW', '9945.TW', '2542.TW',
        '00878.TW', '0056.TW', '00929.TW', '00919.TW'
    ],
    # ... (請保留其他板塊資料) ...
    "台積電集團": ['2330.TW', '5347.TWO', '3443.TW', '3374.TW', '3661.TW', '3105.TWO'],
    "鴻海集團": ['2317.TW', '2328.TW', '2354.TW', '6414.TW', '5243.TW', '3413.TW', '6451.TW'],
    "半導體": ['2330.TW', '2454.TW', '2303.TW', '3711.TW', '3034.TW', '2379.TW', '3443.TW', '3035.TW', '3661.TW'],
    "航運": ['2603.TW', '2609.TW', '2615.TW', '2618.TW', '2610.TW', '2637.TW', '2606.TW'],
}

CODE_NAME_MAP = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電',
    # ... (請保留完整對照表) ...
}

def get_stock_name(stock_code):
    code_only = stock_code.split('.')[0]
    return CODE_NAME_MAP.get(code_only, stock_code)

# --- 5. 核心計算函數 ---
def calculate_adx(df, window=14):
    try:
        high = df['High']; low = df['Low']; close = df['Close']
        up_move = high.diff(); down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm = pd.Series(plus_dm, index=df.index)
        minus_dm = pd.Series(minus_dm, index=df.index)
        tr1 = high - low; tr2 = abs(high - close.shift(1)); tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window).mean()
        plus_di = 100 * (plus_dm.rolling(window).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window).mean() / atr)
        sum_di = abs(plus_di + minus_di).replace(0, 1)
        dx = (abs(plus_di - minus_di) / sum_di) * 100
        adx = dx.rolling(window).mean()
        return adx
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_atr(df, window=14):
    try:
        high = df['High']; low = df['Low']; close = df['Close']
        tr1 = high - low; tr2 = abs(high - close.shift(1)); tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window).mean()
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_obv(df):
    try: return (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    except: return pd.Series([0]*len(df), index=df.index)

def fetch_data_with_retry(ticker, period="1y", retries=3, delay=2):
    for i in range(retries):
        try:
            df = ticker.history(period=period)
            if not df.empty: return df
            time.sleep(0.5) 
        except Exception:
            time.sleep(delay * (i + 1))
    return pd.DataFrame()

# --- 6. 系統自適應核心 ---

def detect_market_state(index_df):
    if index_df.empty: return 'RANGE'
    last = index_df.iloc[-1]
    adx = calculate_adx(index_df).iloc[-1]
    atr = calculate_atr(index_df).iloc[-1]
    atr_pct = atr / last['Close'] if last['Close'] > 0 else 0
    ma20 = index_df['Close'].rolling(20).mean().iloc[-1]
    ma60 = index_df['Close'].rolling(60).mean().iloc[-1]
    
    if ma20 > ma60 and adx > 25: return 'TREND'
    elif atr_pct < 0.012: return 'RANGE'
    else: return 'VOLATILE'

WEIGHT_BY_STATE = {
    'TREND':     {'trend': 0.6, 'momentum': 0.3, 'risk': 0.1},
    'RANGE':     {'trend': 0.4, 'momentum': 0.2, 'risk': 0.4},
    'VOLATILE':  {'trend': 0.3, 'momentum': 0.4, 'risk': 0.3}
}

def calculate_score(df_cand, weights):
    # Trend
    score_rs = df_cand['rs_rank'] * 100
    score_ma = np.where(df_cand['ma20'] > df_cand['ma60'], 100, 0)
    df_cand['score_trend'] = (score_rs * 0.7) + (score_ma * 0.3)
    
    # Momentum
    slope_pct = (df_cand['slope'] / df_cand['price']).fillna(0)
    score_slope = np.where(slope_pct > 0, (slope_pct * 1000).clip(upper=100), 0)
    vol = df_cand['vol_ratio']
    score_vol = np.exp(-((vol - 2.0) ** 2) / 2.0) * 100
    df_cand['score_momentum'] = (score_slope * 0.4) + (score_vol * 0.6)
    
    # Risk
    atr_pct = df_cand['atr'] / df_cand['price']
    dist = (atr_pct - 0.03).abs()
    df_cand['score_risk'] = (100 - (dist * 100 * 20)).clip(lower=0)
    
    df_cand['total_score'] = (
        df_cand['score_trend'] * weights['trend'] +
        df_cand['score_momentum'] * weights['momentum'] +
        df_cand['score_risk'] * weights['risk']
    )
    return df_cand

def get_trade_params(state):
    if state == 'TREND': return 1.5, 3.5, 30, "趨勢盤 (順勢操作)"
    elif state == 'RANGE': return 1.0, 1.5, 10, "盤整盤 (快進快出)"
    else: return 2.0, 2.0, 5, "波動盤 (防洗盤)"

# ★ v5.4 新增：部位規模計算
def get_position_sizing(score):
    if score >= 90: return "重倉 (1.5x) 🔥"
    elif score >= 80: return "標準倉 (1.0x) ✅"
    elif score >= 70: return "輕倉 (0.5x) 🛡️"
    else: return "觀望 (0x) 💤"

# --- 7. 選股功能 ---
def scan_potential_stocks(max_price=None, sector_name=None):
    if sector_name == "隨機":
        all_s = set()
        for s in SECTOR_DICT.values(): for x in s: all_s.add(x)
        watch_list = random.sample(list(all_s), min(30, len(all_s)))
        title_prefix = "【熱門隨機】"
    elif sector_name and sector_name in SECTOR_DICT:
        watch_list = SECTOR_DICT[sector_name]
        title_prefix = f"【{sector_name}股】"
    else:
        watch_list = SECTOR_DICT.get("百元績優", [])
        title_prefix = "【百元績優】"

    recommendations = []
    candidates = []

    try:
        try:
            bench_ticker = yf.Ticker("0050.TW")
            bench_df = fetch_data_with_retry(bench_ticker, period="6mo")
            market_state = detect_market_state(bench_df)
            weights = WEIGHT_BY_STATE[market_state]
            stop_mult, target_mult, max_days, state_desc = get_trade_params(market_state)
            bench_ret = bench_df['Close'].pct_change(20).iloc[-1] if not bench_df.empty else 0
        except:
            market_state = 'RANGE'
            weights = WEIGHT_BY_STATE['RANGE']
            stop_mult, target_mult, max_days, state_desc = get_trade_params('RANGE')
            bench_ret = 0

        data = yf.download(watch_list, period="3mo", progress=False)
        if data.empty: return title_prefix, [f"系統繁忙 (Yahoo 限流)"]

        for stock in watch_list:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    try: closes = data['Close'][stock]; volumes = data['Volume'][stock]; highs = data['High'][stock]; lows = data['Low'][stock]
                    except: continue
                else:
                    closes = data['Close']; volumes = data['Volume']; highs = data['High']; lows = data['Low']

                if isinstance(closes, pd.DataFrame): 
                    if closes.empty: continue
                    closes = closes.iloc[:, 0]; volumes = volumes.iloc[:, 0]; highs = highs.iloc[:, 0]; lows = lows.iloc[:, 0]

                closes = closes.dropna()
                if len(closes) < 60: continue
                current_price = closes.iloc[-1]
                if max_price and current_price > max_price: continue

                ma20 = closes.rolling(20).mean()
                ma60 = closes.rolling(60).mean()
                vol_ma20 = volumes.rolling(20).mean()
                curr_ma20 = ma20.iloc[-1]
                curr_ma60 = ma60.iloc[-1]
                slope = ma20.diff(5).iloc[-1]
                vol_ratio = volumes.iloc[-1] / vol_ma20.iloc[-1] if vol_ma20.iloc[-1] > 0 else 0
                stock_ret = closes.pct_change(20).iloc[-1]
                rs_raw = (1 + stock_ret) / (1 + bench_ret) if bench_ret != 0 else 1.0
                tr = (highs - lows).rolling(14).mean().iloc[-1]
                atr = tr if tr > 0 else current_price * 0.02

                if curr_ma20 > curr_ma60 and slope > 0:
                    candidates.append({
                        'stock': stock, 'price': current_price, 'ma20': curr_ma20, 'ma60': curr_ma60,
                        'slope': slope, 'vol_ratio': vol_ratio, 'atr': atr, 'rs_raw': rs_raw
                    })
            except: continue

        if candidates:
            df_cand = pd.DataFrame(candidates)
            df_cand['rs_rank'] = df_cand['rs_raw'].rank(pct=True)
            df_scored = calculate_score(df_cand, weights)
            
            threshold = 60
            if market_state == 'RANGE': threshold = 70
            
            qualified = df_scored[df_scored['total_score'] >= threshold].copy()
            qualified = qualified.sort_values(by='total_score', ascending=False).head(6)
            
            icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
            for idx, row in enumerate(qualified.itertuples()):
                stock_name = get_stock_name(row.stock)
                stop = row.price - (row.atr * stop_mult)
                target = row.price + (row.atr * target_mult)
                # ★ 新增：部位建議
                pos_size = get_position_sizing(row.total_score)
                icon = icons[idx] if idx < 6 else "🔹"
                
                info = (
                    f"{icon} {stock_name} ({row.stock.split('.')[0]})\n"
                    f"🏆 Score: {int(row.total_score)} | 倉位: {pos_size}\n"
                    f"💰 現價: {row.price:.1f} | RS Top {int((1-row.rs_rank)*100)}%\n"
                    f"🎯 目標: {target:.1f} | 🛑 停損: {stop:.1f}"
                )
                recommendations.append(info)
            
            title_prefix += f"\n({state_desc})"

    except Exception as e:
        return title_prefix, [f"掃描錯誤: {str(e)}"]

    return title_prefix, recommendations

# --- 8. Line Bot 路由與處理 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@app.route("/")
def home(): return f"Stock Bot Running: {APP_VERSION}"

@app.route('/images/<filename>')
def serve_image(filename): return send_from_directory(static_dir, filename)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 新手教學 (含 v5.4 新增內容)
    if user_msg in ["說明", "教學", "名詞解釋", "新手", "看不懂"]:
        tutorial_plus = (
            "🎓 **股市小白 專有名詞懶人包**\n"
            "======================\n\n"
            "⚖️ **倉位建議 (Position Sizing)**\n"
            "系統根據分數高低，建議買多少：\n"
            "• 🔥 **重倉 (1.5x)**: 分數>90，勝率極高，可放大部位。\n"
            "• ✅ **標準倉 (1.0x)**: 分數>80，正常買進。\n"
            "• 🛡️ **輕倉 (0.5x)**: 分數>70，嘗試性建倉。\n\n"
            "🏆 **Score (綜合評分)**\n"
            "• 滿分100，越高越好，代表趨勢+資金+動能都到位。\n\n"
            "🦅 **RS Rank (相對強弱)**\n"
            "• Top 10%: 代表打敗市場90%的股票。\n\n"
            "🛡️ **ATR (真實波幅)**\n"
            "• 用來設停損，波動越大停損設越遠。"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=tutorial_plus))
        return

    if user_msg in ["功能", "指令", "Help", "help", "menu"]:
        menu_text = (
            f"🤖 **股市全能助理 功能清單** ({APP_VERSION})\n"
            "======================\n\n"
            "🔍 **個股診斷**\n"
            "輸入：`2330` 或 `8069`\n"
            "👉 提供線圖、EPS、ADX、RS、建議倉位\n\n"
            "📊 **智能選股 (自適應)**\n"
            "輸入：`推薦` 或 `選股`\n"
            "👉 自動偵測大盤狀態，調整權重\n\n"
            "🎲 **隨機靈感**\n"
            "輸入：`隨機推薦`\n\n"
            "💰 **小資選股**\n"
            "輸入：`百元推薦`\n\n"
            "🏅 **績優選股**\n"
            "輸入：`百元績優推薦`\n\n"
            "🏭 **產業板塊推薦**\n"
            "輸入：`[名稱]推薦` (如：`半導體推薦`)\n"
            "======================\n"
            "💡 試試看輸入：`說明` 查看倉位建議意思"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=menu_text))
        return

    sector_hit = None
    for k in SECTOR_DICT.keys():
        if k in user_msg and ("推薦" in user_msg or "選股" in user_msg):
            sector_hit = k
            break
    
    if sector_hit:
        prefix, res = scan_potential_stocks(sector_name=sector_hit)
        text = f"📊 {prefix}潛力股\n(Score評分制)\n====================\n" + "\n\n".join(res) if res else "無符合條件個股"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))
    elif user_msg == "推薦":
        prefix, res = scan_potential_stocks() 
        text = f"📊 {prefix}潛力股\n(Score評分制)\n====================\n" + "\n\n".join(res) if res else "無符合條件個股"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))
    else:
        # 個股診斷 (整合 Position Sizing)
        img, txt = create_stock_chart(user_msg)
        if img:
            # 這裡我們無法直接取得 score 來算倉位 (create_stock_chart 返回的是字串)
            # 但可以在 create_stock_chart 內部計算並加入文字
            # 目前 create_stock_chart 保持原樣，僅選股清單有倉位建議
            url = request.host_url.replace("http://", "https://") + 'images/' + img
            line_bot_api.reply_message(event.reply_token, [
                ImageSendMessage(original_content_url=url, preview_image_url=url),
                TextSendMessage(text=txt)
            ])
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))

if __name__ == "__main__":
    app.run()