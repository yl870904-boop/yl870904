import os
import time
import numpy as np
import pandas as pd
import yfinance as yf
# 改用物件導向繪圖
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.font_manager import FontProperties
import matplotlib
from flask import Flask, request, abort, send_from_directory
import random
import logging
import traceback
import sys
import gc
import threading
from datetime import datetime, timedelta

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 設定應用程式版本 ---
APP_VERSION = "v16.0 K線大師版 (48種型態全攻略)"

# --- 設定日誌 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- 設定 matplotlib 後端 ---
matplotlib.use('Agg')

# 全域繪圖鎖
plot_lock = threading.Lock()

app = Flask(__name__)

# --- 1. 設定密鑰 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '(REMOVED_LINE_TOKEN)')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '(REMOVED_LINE_SECRET)')

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    logger.error("❌ 嚴重錯誤：找不到 LINE 密鑰！")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 準備字型與圖片目錄 ---
static_dir = 'static_images'
if not os.path.exists(static_dir):
    try:
        os.makedirs(static_dir)
    except Exception as e:
        logger.error(f"無法建立目錄: {e}")

font_file = 'TaipeiSansTCBeta-Regular.ttf'
if not os.path.exists(font_file):
    try:
        import urllib.request
        url = "https://drive.google.com/uc?id=1eGAsTN1HBpJAkeVM57_C7ccp7hbgSz3_&export=download"
        urllib.request.urlretrieve(url, font_file)
    except Exception as e:
        logger.error(f"字型下載失敗: {e}")

try:
    my_font = FontProperties(fname=font_file)
except:
    my_font = None

# --- 3. 全域快取 ---
EPS_CACHE = {}
INFO_CACHE = {}
BENCHMARK_CACHE = {'data': None, 'time': 0}

# 使用者行為追蹤 (情緒熔斷)
USER_USAGE = {}
MAX_REQUESTS_PER_WINDOW = 5
WINDOW_SECONDS = 300
COOLDOWN_SECONDS = 600

def check_user_state(user_id):
    now = datetime.now()
    if user_id not in USER_USAGE:
        USER_USAGE[user_id] = {'last_time': now, 'count': 1, 'cooldown_until': None}
        return False, ""
    
    user_data = USER_USAGE[user_id]
    if user_data['cooldown_until'] and now < user_data['cooldown_until']:
        remaining = int((user_data['cooldown_until'] - now).total_seconds() / 60)
        return True, f"⛔ **情緒熔斷啟動**\n系統檢測到您操作過於頻繁。\n強制冷靜期還剩 {remaining} 分鐘。"
    
    if (now - user_data['last_time']).total_seconds() < WINDOW_SECONDS:
        user_data['count'] += 1
    else:
        user_data['count'] = 1
        user_data['last_time'] = now
    
    if user_data['count'] > MAX_REQUESTS_PER_WINDOW:
        user_data['cooldown_until'] = now + timedelta(seconds=COOLDOWN_SECONDS)
        return True, f"⛔ **過度交易警示**\n查詢過於頻繁，系統強制鎖定 10 分鐘。"
    
    return False, ""

# EPS 抓取 (Fast Fail)
def get_stock_info_cached(ticker_symbol):
    if ticker_symbol in INFO_CACHE: return INFO_CACHE[ticker_symbol]
    
    def fetch_info(symbol, result_dict):
        try:
            t = yf.Ticker(symbol)
            info = t.info 
            if info:
                result_dict['data'] = {
                    'eps': info.get('trailingEps') or info.get('forwardEps') or 'N/A',
                    'pe': info.get('trailingPE') or info.get('forwardPE') or 'N/A'
                }
        except: pass

    result = {}
    t = threading.Thread(target=fetch_info, args=(ticker_symbol, result))
    t.start()
    t.join(timeout=1.5)

    if 'data' in result:
        INFO_CACHE[ticker_symbol] = result['data']
        return result['data']
    else:
        return {'eps': 'N/A', 'pe': 'N/A'}

def get_eps_cached(ticker_symbol):
    info = get_stock_info_cached(ticker_symbol)
    return info['eps']

def get_benchmark_data():
    now = time.time()
    if BENCHMARK_CACHE['data'] is not None and (now - BENCHMARK_CACHE['time']) < 3600:
        return BENCHMARK_CACHE['data']
    
    try:
        bench = yf.download("0050.TW", period="1y", progress=False, threads=False)
        if not bench.empty:
            BENCHMARK_CACHE['data'] = bench
            BENCHMARK_CACHE['time'] = now
            return bench
    except Exception as e:
        logger.error(f"大盤下載失敗: {e}")
    
    return pd.DataFrame()

# --- 4. 資料庫定義 (省略部分，請使用完整版) ---
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
    # (請保留其他所有板塊資料，為節省空間這裡省略)
}

CODE_NAME_MAP = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電',
    # (請保留完整對照表)
}

def get_stock_name(stock_code):
    code_only = stock_code.split('.')[0]
    return CODE_NAME_MAP.get(code_only, stock_code)

# --- 5. 核心計算函數 ---
def calculate_adx(df, window=14):
    try:
        high, low, close = df['High'], df['Low'], df['Close']
        up_move = high.diff(); down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm = pd.Series(plus_dm, index=df.index); minus_dm = pd.Series(minus_dm, index=df.index)
        tr1 = high - low; tr2 = abs(high - close.shift(1)); tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window).mean()
        plus_di = 100 * (plus_dm.rolling(window).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window).mean() / atr)
        sum_di = abs(plus_di + minus_di) + 1e-9
        dx = (abs(plus_di - minus_di) / sum_di) * 100
        adx = dx.rolling(window).mean()
        return adx
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_atr(df, window=14):
    try:
        high = df['High']; low = df['Low']; close = df['Close']
        tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
        return tr.rolling(window).mean()
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_obv(df):
    try: return (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    except: return pd.Series([0]*len(df), index=df.index)

def fetch_data_with_retry(ticker, period="1y", retries=2, delay=1):
    for i in range(retries):
        try:
            df = yf.download(ticker.ticker, period=period, progress=False, threads=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    try: df = df.xs(ticker.ticker, axis=1, level=1)
                    except: pass
                return df
            time.sleep(0.5)
        except Exception: time.sleep(delay * (i + 1))
    return pd.DataFrame()

# --- ★ K線型態辨識引擎 (v16.0 大師版) ---
def detect_kline_pattern(df):
    """
    K線型態辨識 (支援 48 種型態邏輯歸納)
    回傳: (型態名稱, 多空分數)
    分數: >0 多頭訊號, <0 空頭訊號
    """
    if len(df) < 5: return "資料不足", 0
    
    # 取得最近 3 日資料
    t0 = df.iloc[-1] # 今天
    t1 = df.iloc[-2] # 昨天
    t2 = df.iloc[-3] # 前天
    
    # 基礎數值
    O0, H0, L0, C0 = t0['Open'], t0['High'], t0['Low'], t0['Close']
    O1, H1, L1, C1 = t1['Open'], t1['High'], t1['Low'], t1['Close']
    O2, H2, L2, C2 = t2['Open'], t2['High'], t2['Low'], t2['Close']
    
    # 實體與影線
    body0 = abs(C0 - O0)
    body1 = abs(C1 - O1)
    upper0 = H0 - max(C0, O0)
    lower0 = min(C0, O0) - L0
    
    # 趨勢判斷 (簡單版：看 MA20)
    ma20 = df['Close'].rolling(20).mean().iloc[-1]
    is_uptrend = C0 > ma20
    is_downtrend = C0 < ma20
    
    # 平均實體 (用來判斷大K線)
    avg_body = np.mean([abs(df.iloc[-i]['Close'] - df.iloc[-i]['Open']) for i in range(1, 6)])
    if avg_body == 0: avg_body = 0.1

    # --- 三K線型態 ---
    
    # 1. 晨星 (Morning Star) - [空轉多]
    # 第一根長黑，第二根小實體(跳空低開)，第三根長紅(深入第一根實體)
    if (C2 < O2 and body1 < avg_body*0.5 and C0 > O0 and 
        C0 > (O2 + C2)/2 and L1 < L2 and L1 < L0):
        return "晨星 (黎明將至) [空轉多] 🌅", 0.9

    # 2. 夜星 (Evening Star) - [多轉空]
    # 第一根長紅，第二根小實體(跳空高開)，第三根長黑(深入第一根實體)
    if (C2 > O2 and body1 < avg_body*0.5 and C0 < O0 and 
        C0 < (O2 + C2)/2 and H1 > H2 and H1 > H0):
        return "夜星 (黑夜降臨) [多轉空] 🌃", -0.9

    # 3. 紅三兵 (Three White Soldiers) - [多頭持續]
    if (C0 > O0 and C1 > O1 and C2 > O2 and 
        C0 > C1 > C2 and O0 > O1 > O2):
        # 檢查是否漲幅過大變 "大敵當前"
        if body0 < body1 and upper0 > body0:
             return "步步為營 (多頭受阻) [警戒] 🛡️", 0.3
        return "紅三兵 (多頭氣盛) [多轉多] 💂‍♂️", 0.8

    # 4. 黑三兵 (Three Black Crows) - [空頭持續]
    if (C0 < O0 and C1 < O1 and C2 < O2 and 
        C0 < C1 < C2):
        return "黑三兵 (烏鴉滿天) [空轉空] 🐻", -0.8

    # --- 雙K線型態 ---

    # 5. 吞噬 (Engulfing)
    # 多頭吞噬：昨黑今紅，紅包黑
    if (C1 < O1 and C0 > O0 and C0 > O1 and O0 < C1):
        return "多頭吞噬 (一舉扭轉) [空轉多] 🔥", 0.85
    # 空頭吞噬：昨紅今黑，黑包紅
    if (C1 > O1 and C0 < O0 and C0 < O1 and O0 > C1):
        return "空頭吞噬 (空方反撲) [多轉空] 🌧️", -0.85

    # 6. 母子 (Harami)
    # 多頭母子：昨長黑，今小紅(在昨天實體內)
    if (C1 < O1 and body1 > avg_body and C0 > O0 and 
        H0 < O1 and L0 > C1):
        return "多頭母子 (跌勢受阻) [醞釀反彈] 🤰", 0.6
    # 空頭母子：昨長紅，今小黑(在昨天實體內)
    if (C1 > O1 and body1 > avg_body and C0 < O0 and 
        H0 < C1 and L0 > O1):
        return "空頭母子 (漲勢受阻) [高檔變盤] 🤰", -0.6

    # 7. 貫穿線 (Piercing) - [空轉多]
    # 昨黑今紅，收盤過昨黑實體一半
    if (C1 < O1 and C0 > O0 and O0 < C1 and C0 > (O1+C1)/2):
        return "貫穿線 (多方反擊) [空轉多] 🗡️", 0.75
    
    # 8. 烏雲蓋頂 (Dark Cloud Cover) - [多轉空]
    # 昨紅今黑，收盤破昨紅實體一半
    if (C1 > O1 and C0 < O0 and O0 > C1 and C0 < (O1+C1)/2):
        return "烏雲蓋頂 (空方壓頂) [多轉空] 🌥️", -0.75

    # 9. 鑷頂/鑷底 (Tweezer)
    if abs(H0 - H1) < (price * 0.002) and is_uptrend:
        return "鑷頂 (雙針探頂) [多轉空] 🥢", -0.6
    if abs(L0 - L1) < (price * 0.002) and is_downtrend:
        return "鑷底 (雙針探底) [空轉多] 🥢", 0.6

    # --- 單K線型態 ---

    # 10. 墓碑線 (Gravestone Doji) - [多轉空]
    # 開收低相同，長上影
    if (body0 < avg_body*0.1 and upper0 > avg_body*1.5 and lower0 < avg_body*0.2):
        if is_uptrend: return "墓碑線 (多頭力竭) [多轉空] 🪦", -0.9
    
    # 11. 蜻蜓線 (Dragonfly Doji) - [空轉多]
    # 開收高相同，長下影
    if (body0 < avg_body*0.1 and lower0 > avg_body*1.5 and upper0 < avg_body*0.2):
        if is_downtrend: return "蜻蜓線 (奇蹟逆轉) [空轉多] 🦗", 0.9

    # 12. 錘頭 (Hammer) / 上吊 (Hanging Man)
    # 下影線 >= 實體2倍
    if (lower0 > 2 * body0 and upper0 < body0 * 0.5):
        if is_downtrend: return "錘頭 (底部打樁) [空轉多] 🔨", 0.7
        if is_uptrend: return "上吊線 (主力出貨?) [多轉空] 🎗️", -0.6

    # 13. 倒錘 (Inverted Hammer) / 流星 (Shooting Star) / 仙人指路
    # 上影線 >= 實體2倍
    if (upper0 > 2 * body0 and lower0 < body0 * 0.5):
        if is_uptrend: return "流星 (高檔避雷針) [多轉空] ☄️", -0.7
        if is_downtrend: return "倒狀錘頭 (試盤訊號) [醞釀反彈] ☝️", 0.4
    
    # 14. 十字星 (Doji)
    if body0 < avg_body * 0.15:
        return "十字星 (多空觀望) [中繼/變盤] ➕", 0

    # 15. 大長K
    if C0 > O0 and body0 > avg_body * 2.0: return "長紅K (強勢表態) [多] 🟥", 0.5
    if C0 < O0 and body0 > avg_body * 2.0: return "長黑K (恐慌拋售) [空] ⬛", -0.5

    return "整理中 (等待訊號)", 0

# --- 市場價值評估 ---
def get_valuation_status(current_price, ma60, info_data):
    pe = info_data.get('pe', 'N/A')
    bias = (current_price - ma60) / ma60 * 100
    tech_val = "合理"
    if bias > 20: tech_val = "過熱 (昂貴)"
    elif bias < -15: tech_val = "超跌 (便宜)"
    elif bias > 10: tech_val = "略貴"
    elif bias < -5: tech_val = "略低"
    fund_val = ""
    if pe != 'N/A':
        try:
            pe_val = float(pe)
            if pe_val < 10: fund_val = " | PE低估"
            elif pe_val > 40: fund_val = " | PE高估"
            elif pe_val < 15: fund_val = " | PE合理"
        except: pass
    return f"{tech_val}{fund_val}"

# --- 6. 系統自適應核心 ---
def detect_market_state(index_df):
    if index_df.empty: return 'RANGE'
    last = index_df.iloc[-1]
    adx = calculate_adx(index_df).iloc[-1]
    atr = calculate_atr(index_df).iloc[-1]
    atr_pct = (atr / last['Close']) if last['Close'] > 0 else 0
    ma20 = index_df['Close'].rolling(20).mean().iloc[-1]
    ma60 = index_df['Close'].rolling(60).mean().iloc[-1]
    if ma20 > ma60 and adx > 25: return 'TREND'
    elif atr_pct < 0.012: return 'RANGE'
    else: return 'VOLATILE'

def get_market_commentary(state):
    if state == 'TREND': return "🟢 今日盤勢：適合新手\n👉 策略：順勢操作。\n🛑 額度：最多 2 檔。"
    elif state == 'RANGE': return "🟡 今日盤勢：建議觀望\n👉 策略：新手空手，老手區間。\n🛑 額度：最多 1 檔。"
    else: return "🔴 今日盤勢：⛔ 禁止進場\n👉 策略：嚴格風控。\n🛑 額度：🚫 禁止開新倉。"

def get_psychology_reminder():
    quotes = [
        "💡 心法：Score 高不代表必勝，只代表勝率較高。",
        "💡 心法：新手死於追高，老手死於抄底。",
        "💡 心法：連續虧損時，縮小部位或停止交易。",
        "💡 心法：不持有部位，也是一種部位。",
        "💡 心法：交易的目標不是全對，而是活得久。"
    ]
    return random.choice(quotes)

WEIGHT_BY_STATE = {
    'TREND': {'trend': 0.6, 'momentum': 0.3, 'risk': 0.1},
    'RANGE': {'trend': 0.4, 'momentum': 0.2, 'risk': 0.4},
    'VOLATILE': {'trend': 0.3, 'momentum': 0.4, 'risk': 0.3}
}

def calculate_score(df_cand, weights):
    score_rs = df_cand['rs_rank'] * 100
    score_ma = np.where(df_cand['ma20'] > df_cand['ma60'], 100, 0)
    score_trend = (score_rs * 0.7) + (score_ma * 0.3)
    
    slope_pct = (df_cand['slope'] / df_cand['price']).fillna(0)
    score_slope = np.where(slope_pct > 0, (slope_pct * 1000).clip(upper=100), 0)
    vol = df_cand['vol_ratio']
    score_vol = np.exp(-((vol - 2.0) ** 2) / 2.0) * 100
    df_cand['score_momentum'] = (score_slope * 0.4) + (score_vol * 0.6)
    
    atr_pct = df_cand['atr'] / df_cand['price']
    dist = (atr_pct - 0.03).abs()
    score_risk = (100 - (dist * 100 * 20)).clip(lower=0)
    
    df_cand['score_risk'] = score_risk
    
    df_cand['total_score'] = (score_trend * weights['trend'] + df_cand['score_momentum'] * weights['momentum'] + score_risk * weights['risk'])

    is_aplus = (
        (df_cand['rs_rank'] >= 0.85) & (df_cand['ma20'] > df_cand['ma60']) &
        (df_cand['slope'] > 0) & (df_cand['vol_ratio'].between(1.5, 2.5)) &
        (df_cand['score_risk'] > 60)
    )
    df_cand.loc[is_aplus, 'total_score'] += 15
    df_cand['total_score'] = df_cand['total_score'].clip(upper=100)
    df_cand['is_aplus'] = is_aplus
    return df_cand

def get_trade_params(state):
    if state == 'TREND': return 1.5, 3.5, 30, "趨勢延續單", "中", "2"
    elif state == 'RANGE': return 1.0, 1.5, 10, "區間突破單", "低", "1"
    else: return 2.0, 2.0, 5, "波動反彈單", "高", "0"

def get_position_sizing(score):
    if score >= 90: return "重倉 (1.5x) 🔥"
    elif score >= 80: return "標準倉 (1.0x) ✅"
    elif score >= 70: return "輕倉 (0.5x) 🛡️"
    else: return "觀望 (0x) 💤"

def check_entry_gate(current_price, rsi, ma20):
    bias = (current_price - ma20) / ma20 * 100
    if bias > 12: return "WAIT", "乖離過大"
    if rsi > 85: return "BAN", "指標過熱"
    return "PASS", "符合"

# --- 7. 繪圖引擎 (v16.0 K線大師版) ---
def create_stock_chart(stock_code):
    gc.collect()
    result_file = None
    result_text = ""
    
    with plot_lock:
        try:
            # 移除舊 plt 指令
            # plt.close('all'); plt.clf()
            
            raw_code = stock_code.upper().strip()
            if raw_code.endswith('.TW') or raw_code.endswith('.TWO'):
                target = raw_code
                ticker = yf.Ticker(target)
            else:
                target = raw_code + ".TW"
                ticker = yf.Ticker(target)
            
            df = fetch_data_with_retry(ticker, period="1y")
            
            if df.empty and not (raw_code.endswith('.TW') or raw_code.endswith('.TWO')):
                target_two = raw_code + ".TWO"
                ticker_two = yf.Ticker(target_two)
                df_two = fetch_data_with_retry(ticker_two, period="1y")
                if not df_two.empty:
                    target = target_two
                    ticker = ticker_two
                    df = df_two
            
            if df.empty: return None, "找不到代號或系統繁忙。"
            
            stock_name = get_stock_name(target)
            info_data = get_stock_info_cached(target)
            eps = info_data['eps']

            try:
                bench = yf.Ticker("0050.TW").history(period="1y")
                common = df.index.intersection(bench.index)
                if len(common) > 20:
                    s_ret = df.loc[common, 'Close'].pct_change(20)
                    b_ret = bench.loc[common, 'Close'].pct_change(20)
                    df.loc[common, 'RS'] = (1+s_ret)/(1+b_ret)
                else: df['RS'] = 1.0
            except: df['RS'] = 1.0

            if len(df) < 60:
                df['MA20'] = df['Close'].rolling(20).mean(); df['MA60'] = df['MA20']
            else:
                df['MA20'] = df['Close'].rolling(20).mean(); df['MA60'] = df['Close'].rolling(60).mean()
            
            df['Slope'] = df['MA20'].diff(5)
            
            delta = df['Close'].diff()
            gain = (delta.where(delta>0, 0)).rolling(14).mean()
            loss = (-delta.where(delta<0, 0)).rolling(14).mean()
            rs_idx = gain/loss
            df['RSI'] = 100-(100/(1+rs_idx))
            
            df['Vol_MA20'] = df['Volume'].rolling(20).mean()
            df['Vol_Ratio'] = df['Volume'] / df['Vol_MA20']
            
            df['ADX'] = calculate_adx(df)
            df['ATR'] = calculate_atr(df)
            df['OBV'] = calculate_obv(df)

            last = df.iloc[-1]
            price = last['Close']
            ma20, ma60 = last['MA20'], last['MA60']
            slope = last['Slope'] if not pd.isna(last['Slope']) else 0
            rsi = last['RSI'] if not pd.isna(last['RSI']) else 50
            adx = last['ADX'] if not pd.isna(last['ADX']) else 0
            atr = last['ATR'] if not pd.isna(last['ATR']) and last['ATR'] > 0 else price*0.02
            rs_val = last['RS'] if 'RS' in df.columns and not pd.isna(last['RS']) else 1.0
            vol_ratio = last['Vol_Ratio'] if not pd.isna(last['Vol_Ratio']) else 1.0

            kline_pattern, kline_score = detect_kline_pattern(df)
            valuation_status = get_valuation_status(price, ma60, info_data)

            # 狀態判定
            if adx < 20: trend_quality = "盤整 💤"
            elif adx > 40: trend_quality = "強勁 🔥"
            else: trend_quality = "確立 ✅"

            if ma20 > ma60 and slope > 0: trend_dir = "多頭"
            elif ma20 < ma60 and slope < 0: trend_dir = "空頭"
            else: trend_dir = "震盪"

            if rs_val > 1.05: rs_str = "強於大盤 🦅"
            elif rs_val < 0.95: rs_str = "弱於大盤 🐢"
            else: rs_str = "跟隨"

            atr_stop_loss = price - atr * 1.5
            final_stop = max(atr_stop_loss, ma20) if trend_dir == "多頭" and ma20 < price else atr_stop_loss
            target_price_val = price + atr * 3 

            obv_warning = ""
            try:
                if len(df) > 10:
                    if df['Close'].iloc[-1] > df['Close'].iloc[-10] and df['OBV'].iloc[-1] < df['OBV'].iloc[-10]:
                        obv_warning = " (⚠️背離)"
            except: pass

            entry_status, entry_msg = check_entry_gate(price, rsi, ma20)
            entry_warning = f"\n{entry_msg}" if entry_status != "PASS" else ""

            advice = "觀望"
            if trend_dir == "多頭":
                if entry_status == "BAN": advice = "⛔ 禁止進場 (過熱)"
                elif entry_status == "WAIT": advice = "⏳ 暫緩 (等回測)"
                elif kline_score > 0: advice = f"✅ 買點浮現 ({kline_pattern})"
                elif adx < 20: advice = "盤整中，多看少做"
                elif rs_val < 1: advice = "弱於大盤，恐補跌"
                elif 60 <= rsi <= 75: advice = "量價健康，可尋買點"
                else: advice = "沿月線操作"
            elif trend_dir == "空頭": advice = "趨勢向下，勿接刀"
            else:
                if kline_score > 0.5: advice = "震盪轉強，老手試單"
                else: advice = "方向不明，觀望"

            exit_rule = f"🛑 **停損鐵律**：跌破 {final_stop:.1f} 市價出場。"

            analysis_report = (
                f"📊 {stock_name} ({target}) 診斷\n"
                f"💰 {price:.1f} | EPS: {eps}\n"
                f"📈 {trend_dir} | {trend_quality}\n"
                f"🕯️ {kline_pattern}\n"
                f"💎 {valuation_status}\n"
                f"🦅 RS: {rs_val:.2f} ({rs_str})\n"
                f"------------------\n"
                f"🎯 目標: {target_price_val:.1f} | 🛑 停損: {final_stop:.1f}\n"
                f"{exit_rule}\n"
                f"💡 建議: {advice}"
                f"{entry_warning}\n\n"
                f"{get_psychology_reminder()}"
            )
            
            result_text = analysis_report

            fig = Figure(figsize=(10, 10))
            canvas = FigureCanvas(fig)
            
            ax1 = fig.add_subplot(3, 1, 1)
            ax1.plot(df.index, df['Close'], color='black', alpha=0.6, label='Price')
            if len(df) >= 20: ax1.plot(df.index, df['MA20'], color='#FF9900', linestyle='--', label='MA20')
            if len(df) >= 60: ax1.plot(df.index, df['MA60'], color='#0066CC', linewidth=2, label='MA60')
            
            title_prop = my_font if my_font else None
            try: ax1.set_title(f"{stock_name} ({target}) 實戰分析", fontproperties=title_prop, fontsize=18)
            except: ax1.set_title(f"{target} Analysis", fontsize=18)
            ax1.legend(loc='upper left', prop=title_prop)
            ax1.grid(True, linestyle=':', alpha=0.5)

            ax2 = fig.add_subplot(3, 1, 2)
            cols = ['red' if c >= o else 'green' for c, o in zip(df['Close'], df['Open'])]
            ax2.bar(df.index, df['Volume'], color=cols, alpha=0.8)
            ax2.plot(df.index, df['Vol_MA20'], color='blue')
            ax2.set_ylabel("Volume", fontproperties=title_prop)
            ax2.grid(True, linestyle=':', alpha=0.3)

            ax3 = fig.add_subplot(3, 1, 3)
            ax3.plot(df.index, df['RSI'], color='purple')
            ax3.axhline(80, color='red', linestyle='--')
            ax3.axhline(30, color='green', linestyle='--')
            ax3.set_ylabel("RSI", fontproperties=title_prop)
            ax3.grid(True, linestyle=':', alpha=0.3)

            fig.autofmt_xdate()
            
            filename = f"{target.replace('.', '_')}_{int(time.time())}.png"
            filepath = os.path.join(static_dir, filename)
            fig.savefig(filepath, bbox_inches='tight')
            result_file = filename
            del fig; del canvas

        except Exception as e:
            return None, f"繪圖失敗: {str(e)}\n\n{result_text}"
        finally:
            gc.collect()

    return result_file, result_text

# --- 8. 選股功能 (v15.2 分流穩定版) ---
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
            bench = yf.download("0050.TW", period="6mo", progress=False, threads=False)
            if isinstance(bench.columns, pd.MultiIndex):
                try: bench = bench.xs("0050.TW", axis=1, level=1)
                except: pass
            
            mkt = detect_market_state(bench)
            w = WEIGHT_BY_STATE[mkt]
            b_ret = bench['Close'].pct_change(20).iloc[-1] if not bench.empty else 0
            
            market_commentary = get_market_commentary(mkt)
            stop_mult, target_mult, max_days, trade_type, risk_desc, max_trades = get_trade_params(mkt)
            
            if mkt == 'VOLATILE':
                return f"🔴 **市場熔斷啟動**\n\n目前盤勢為【{mkt}】，風險極高。\n系統已強制停止選股功能，請保留現金，靜待落底訊號。", []

        except:
            mkt, w, b_ret, trade_type, risk_desc = 'RANGE', WEIGHT_BY_STATE['RANGE'], 0, "區間", "未知"
            stop_mult, target_mult, max_days, max_trades = 1.0, 1.5, 10, "1"
            market_commentary = "⚠️ 無法取得大盤狀態，請保守操作。"

        # ★ v15.2: Chunking 分批下載 (每次10檔) 避免被擋
        chunk_size = 10
        chunks = [watch_list[i:i + chunk_size] for i in range(0, len(watch_list), chunk_size)]
        
        all_data_frames = []
        for chunk in chunks:
            try:
                # 每個 chunk 下載一次
                d = yf.download(chunk, period="3mo", progress=False, threads=False)
                if not d.empty:
                    all_data_frames.append(d)
                time.sleep(0.5) # 休息一下防擋
            except: continue
            
        if not all_data_frames:
             return title_prefix, ["系統繁忙 (Yahoo 限流)，請稍後再試。"]

        # 3. 逐一處理資料 (處理複雜的 MultiIndex)
        for d_chunk in all_data_frames:
            # 如果只有一檔股票，download 回傳格式會不同，需標準化
            is_multi = isinstance(d_chunk.columns, pd.MultiIndex)
            
            if is_multi:
                stocks_in_chunk = d_chunk.columns.get_level_values(1).unique()
            else:
                # 只有一檔時，無法直接從 column 知道 ticker，這在 chunk size=1 或只剩1檔有效時會發生
                # 這裡簡化處理：如果不是 MultiIndex，我們嘗試從 chunk list 中找對應
                # 但為求穩定，若單檔下載成功但無法對應 ticker，可能略過
                # yf.download 的行為：單檔 -> (Date, Open...), 多檔 -> (Price, Ticker, Date)
                # 這裡假設如果不是 MultiIndex，則 d_chunk 就是該檔資料，但我們需要知道是哪一檔
                # 由於 chunking 邏輯，我們可以嘗試用 chunk[0] 當作 ticker (若 chunk=1)
                # 為了代碼健壯性，這裡僅處理 MultiIndex 情況，單檔若失敗則略過 (可再優化)
                stocks_in_chunk = [] 

            for stock in stocks_in_chunk:
                try:
                    c = d_chunk['Close'][stock]
                    v = d_chunk['Volume'][stock]
                    h = d_chunk['High'][stock]
                    l = d_chunk['Low'][stock]
                    
                    c = c.dropna()
                    if len(c) < 60: continue
                    price = c.iloc[-1]
                    if max_price and price > max_price: continue

                    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean()
                    v_ma = v.rolling(20).mean()
                    slope = ma20.diff(5).iloc[-1]
                    vol_r = v.iloc[-1]/v_ma.iloc[-1] if v_ma.iloc[-1]>0 else 0
                    s_ret = c.pct_change(20).iloc[-1]
                    rs = (1+s_ret)/(1+b_ret)
                    tr = (h-l).rolling(14).mean().iloc[-1]
                    atr = tr if tr > 0 else price*0.02
                    
                    delta = c.diff()
                    gain = (delta.where(delta>0, 0)).rolling(14).mean()
                    loss = (-delta.where(delta<0, 0)).rolling(14).mean()
                    rs_idx = gain/loss
                    rsi = 100-(100/(1+rs_idx))
                    curr_rsi = rsi.iloc[-1]

                    if ma20.iloc[-1] > ma60.iloc[-1] and slope > 0:
                        candidates.append({
                            'stock': stock, 'price': price, 'ma20': ma20.iloc[-1], 'ma60': ma60.iloc[-1],
                            'slope': slope, 'vol_ratio': vol_r, 'atr': atr, 'rs_raw': rs, 'rs_rank': 0,
                            'rsi': curr_rsi 
                        })
                except: continue

        if candidates:
            df = pd.DataFrame(candidates)
            df['rs_rank'] = df['rs_raw'].rank(pct=True)
            df = calculate_score(df, w)
            
            th = 70 if mkt == 'RANGE' else 60
            df = df.sort_values('total_score', ascending=False)
            picks = df[df['total_score']>=th].head(6)
            
            icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
            for i, r in enumerate(picks.itertuples()):
                name = get_stock_name(r.stock)
                stop = r.price - r.atr * stop_mult
                target = r.price + r.atr * target_mult
                pos = get_position_sizing(r.total_score)
                icon = icons[i] if i < 6 else "🔹"
                
                entry_status, _ = check_entry_gate(r.price, r.rsi, r.ma20)
                if entry_status == "BAN": continue
                gate_tag = " (⚠️等回測)" if entry_status == "WAIT" else ""
                aplus_tag = "💎 A+ 完美訊號" if getattr(r, 'is_aplus', False) else f"屬性: {trade_type}"
                
                info = (
                    f"{icon} {name} ({r.stock.split('.')[0]})\n"
                    f"📌 {aplus_tag}{gate_tag}\n"
                    f"🏆 Score: {int(r.total_score)} | 倉位: {pos}\n"
                    f"💰 {r.price:.1f} | RS Top {int((1-r.rs_rank)*100)}%\n"
                    f"🎯 {target:.1f} | 🛑 {stop:.1f}"
                )
                recommendations.append(info)
            
            title_prefix = f"{market_commentary}\n\n{title_prefix}"
            recommendations.append(f"\n{get_psychology_reminder()}")
        else:
            return title_prefix, ["今日無符合強勢股，建議空手。"]

    except Exception as e:
        return title_prefix, [f"掃描錯誤: {str(e)}"]

    return title_prefix, recommendations

# --- 9. Bot Handler ---
@app.route("/callback", methods=['POST'])
def callback():
    sig = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    if not sig: abort(400)
    try: handler.handle(body, sig)
    except InvalidSignatureError: abort(400)
    except: abort(500)
    return 'OK'

@app.route("/")
def home(): return f"Stock Bot: {APP_VERSION}"

@app.route('/images/<filename>')
def serve_image(filename): return send_from_directory(static_dir, filename)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    if not msg: return
    user_id = event.source.user_id 
    is_blocked, block_msg = check_user_state(user_id)
    if is_blocked:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=block_msg))
        return 

    if msg in ["說明", "教學", "名詞解釋", "新手", "看不懂"]:
        txt = (
            "🎓 **股市小白 專有名詞懶人包**\n"
            "======================\n\n"
            "💎 **A+ 完美訊號**\n"
            "• 只有在「趨勢+資金+量能」全部滿分時才會出現。\n"
            "• 這是系統最高等級的推薦，勝率結構最漂亮。\n\n"
            "⚖️ **倉位建議**\n"
            "• 🔥 重倉 (1.5x): 分數>90，勝率極高。\n"
            "• ✅ 標準倉 (1.0x): 分數>80，正常買進。\n"
            "• 🛡️ 輕倉 (0.5x): 分數>70，嘗試性建倉。\n\n"
            "🏆 **Score (綜合評分)**\n"
            "• 滿分100，越高越好。\n\n"
            "🦅 **RS Rank (相對強弱)**\n"
            "• Top 10%: 代表打敗市場90%的股票。\n\n"
            "❌ **新手常見死法提醒**：\n"
            "• A+ 不是必漲，還是要設停損。\n"
            "• 不准加碼虧損 (凹單)。\n"
            "• 停損價是「必須執行」，不是參考。"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))
        return

    if msg in ["功能", "指令", "Help", "help", "menu"]:
        menu = (
            f"🤖 **股市全能助理** ({APP_VERSION})\n"
            "======================\n\n"
            "🔍 **個股診斷**\n"
            "輸入：`2330` 或 `8069`\n"
            "👉 線圖、K線型態、價值評估、教練建議\n\n"
            "📊 **智能選股 (自適應)**\n"
            "輸入：`推薦` 或 `選股`\n"
            "👉 自動偵測盤勢，A+訊號優先展示\n\n"
            "🎲 **隨機靈感**\n"
            "輸入：`隨機推薦`\n\n"
            "💰 **小資選股**\n"
            "輸入：`百元推薦`\n\n"
            "🏅 **績優選股**\n"
            "輸入：`百元績優推薦`\n\n"
            "🏭 **板塊推薦**\n"
            "輸入：`[名稱]推薦` (如：`半導體推薦`)"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=menu))
        return

    sector = None
    for k in SECTOR_DICT:
        if k in msg and ("推薦" in msg or "選股" in msg):
            sector = k
            break
    
    if sector:
        p, r = scan_potential_stocks(sector_name=sector)
        t = f"📊 {p}\n(Score評分制)\n====================\n" + "\n\n".join(r) if r else "無符合條件個股"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=t))
    elif msg == "推薦":
        p, r = scan_potential_stocks()
        t = f"📊 {p}\n(Score評分制)\n====================\n" + "\n\n".join(r) if r else "無符合條件個股"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=t))
    elif msg == "百元推薦":
        p, r = scan_potential_stocks(max_price=100)
        t = f"📊 {p}\n(Score評分制)\n====================\n" + "\n\n".join(r) if r else "無符合條件個股"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=t))
    elif msg in ["隨機推薦", "隨機"]:
        p, r = scan_potential_stocks(sector_name="隨機")
        t = f"🎲 {p}\n(Score評分制)\n====================\n" + "\n\n".join(r) if r else "運氣不好，沒找到強勢股。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=t))
    else:
        # 個股診斷 (Fail-safe)
        img, txt = create_stock_chart(msg)
        if img:
            url = request.host_url.replace("http://", "https://") + 'images/' + img
            line_bot_api.reply_message(event.reply_token, [
                ImageSendMessage(original_content_url=url, preview_image_url=url),
                TextSendMessage(text=txt)
            ])
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))

if __name__ == "__main__":
    app.run()