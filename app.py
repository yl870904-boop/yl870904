import os
import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
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
import requests

# --- 設定應用程式版本 ---
APP_VERSION = "v26.0 雲端量化回測版 (內建 10 大策略回測引擎)"

# --- 設定日誌 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- 設定 matplotlib 後端 ---
matplotlib.use('Agg')

# 全域繪圖鎖
plot_lock = threading.Lock()

app = Flask(__name__)

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 1. 設定密鑰 (純雲端環境變數讀取) ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
FINMIND_TOKEN = os.environ.get('FINMIND_TOKEN', '')

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    logger.error("❌ 嚴重錯誤：找不到 LINE 密鑰！請確認 Render 環境變數是否已設定。")

# 只有在金鑰存在時才初始化
if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 準備字型與圖片目錄 ---
static_dir = 'static_images'
if not os.path.exists(static_dir):
    try: os.makedirs(static_dir)
    except Exception as e: logger.error(f"無法建立目錄: {e}")

font_file = 'TaipeiSansTCBeta-Regular.ttf'
if not os.path.exists(font_file):
    try:
        import urllib.request
        url = "https://drive.google.com/uc?id=1eGAsTN1HBpJAkeVM57_C7ccp7hbgSz3_&export=download"
        urllib.request.urlretrieve(url, font_file)
    except Exception as e: logger.error(f"字型下載失敗: {e}")

try: my_font = FontProperties(fname=font_file)
except: my_font = None

# --- 3. 全域快取與使用者狀態 ---
INFO_CACHE = {}
BENCHMARK_CACHE = {'data': None, 'time': 0}
USER_USAGE = {}
MAX_REQUESTS_PER_WINDOW = 15
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
        return True, f"⛔ **情緒熔斷啟動**\n操作過頻，強制冷靜 {remaining} 分鐘。"
    
    if (now - user_data['last_time']).total_seconds() < WINDOW_SECONDS:
        user_data['count'] += 1
    else:
        user_data['count'] = 1
        user_data['last_time'] = now
    
    if user_data['count'] > MAX_REQUESTS_PER_WINDOW:
        user_data['cooldown_until'] = now + timedelta(seconds=COOLDOWN_SECONDS)
        return True, f"⛔ **過度交易警示**\n頻率過高，系統鎖定 10 分鐘。"
    
    return False, ""

# --- ★ 核心：FinMind API 串接模組 ---
def call_finmind_api(dataset, data_id, start_date=None, days=365):
    """通用 FinMind API 呼叫函式 (Sponsor 權限)"""
    url = "https://api.finmindtrade.com/api/v4/data"
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    params = {
        "dataset": dataset, 
        "data_id": data_id, 
        "start_date": start_date, 
        "token": FINMIND_TOKEN
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            j = r.json()
            if j.get('msg') == 'success' and j.get('data'): 
                return pd.DataFrame(j['data'])
    except Exception as e:
        logger.error(f"FinMind API Error ({dataset} - {data_id}): {e}")
    return pd.DataFrame()

def fetch_data_finmind(stock_code, days=400, start_date=None):
    """專責抓取 K 線資料"""
    clean_code = stock_code.split('.')[0]
    df = call_finmind_api("TaiwanStockPrice", clean_code, days=days, start_date=start_date)
    
    if df.empty: 
        return pd.DataFrame()
    
    # 格式整理成標準 OHLCV
    df = df.rename(columns={'date':'Date','open':'Open','max':'High','min':'Low','close':'Close','Trading_Volume':'Volume'})
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    for c in ['Open','High','Low','Close','Volume']: 
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    
    return df.dropna(subset=['Close'])

def get_stock_info_finmind(stock_code):
    """專責抓取基本面 (PE)"""
    clean_code = stock_code.split('.')[0]
    if clean_code in INFO_CACHE:
        return INFO_CACHE[clean_code]
        
    df_per = call_finmind_api("TaiwanStockPER", clean_code, days=15)
    data = {'eps': 'N/A', 'pe': 'N/A'}
    if not df_per.empty:
        last = df_per.iloc[-1]
        p = last.get('PER', 0)
        data['pe'] = p if pd.notna(p) and p > 0 else 'N/A'
    
    INFO_CACHE[clean_code] = data
    return data

def get_eps_from_price_pe(price, pe):
    try:
        if pe != 'N/A' and float(pe) > 0: 
            return round(price / float(pe), 2)
    except: pass
    return 'N/A'

def get_benchmark_data():
    """專責抓取加權指數做大盤指標"""
    now = time.time()
    if BENCHMARK_CACHE['data'] is not None and (now - BENCHMARK_CACHE['time']) < 3600:
        return BENCHMARK_CACHE['data']
    
    bench = fetch_data_finmind("TAIEX", days=400)
    if not bench.empty and len(bench) > 20:
        BENCHMARK_CACHE['data'] = bench
        BENCHMARK_CACHE['time'] = now
        return bench
    return pd.DataFrame()

# --- 4. 資料庫定義 (完整版) ---
SECTOR_DICT = {
    "百元績優": ['2303', '2317', '2454', '2603', '2881', '1605', '2382', '3231', '2409', '2609', '2615', '2002', '2882', '0050', '0056', '2324', '2356', '2353', '2352', '3481', '2408', '2344', '2337', '3702', '2312', '6282', '3260', '8150', '6147', '5347', '2363', '2449', '3036', '2884', '2880', '2886', '2891', '2892', '5880', '2885', '2890', '2883', '2887', '2834', '2801', '1101', '1102', '2027', '1402', '1907', '2105', '2618', '2610', '9945', '2542', '00878', '00929', '00919'],
    "半導體": ['2330', '2454', '2303', '3711', '3034', '2379', '3443', '3035', '3661'],
    "電子": ['2317', '2382', '3231', '2353', '2357', '2324', '2301', '2356'],
    "航運": ['2603', '2609', '2615', '2618', '2610', '2637', '2606'],
    "金融": ['2881', '2882', '2886', '2891', '2892', '2884', '5880', '2880', '2885'],
    "AI": ['3231', '2382', '6669', '2376', '2356', '3017'],
}

CODE_NAME_MAP = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '2317': '鴻海', '2409': '友達', '2603': '長榮', '1605': '華新', '2609': '陽明', '3481': '群創', '2615': '萬海', '2618': '長榮航', '2610': '華航', '2637': '慧洋', '2606': '裕民', '2002': '中鋼', '2014': '中鴻', '2027': '大成鋼', '1301': '台塑', '1402': '遠東新', '1101': '台泥', '2881': '富邦金', '2882': '國泰金', '0050': '元大台灣50', '0056': '元大高股息', '3231': '緯創', '2382': '廣達', '2376': '技嘉', '2356': '英業達', '3037': '欣興', '2324': '仁寶', '2357': '華碩', '5880': '合庫金', '2891': '中信金', '2892': '第一金', '2886': '兆豐金', '2884': '玉山金', '2885': '元大金', '2890': '永豐金', '2883': '開發金', '2887': '台新金', '2880': '華南金', '2834': '臺企銀', '2801': '彰銀', '1102': '亞泥', '1907': '永豐餘', '2105': '正新', '9945': '潤泰新', '2542': '興富發', '00878': '國泰永續', '00929': '復華科優息', '00919': '群益精選', '2353': '宏碁', '2352': '佳世達', '2408': '南亞科', '2344': '華邦電', '2337': '旺宏', '3702': '大聯大', '2312': '金寶', '6282': '康舒', '3260': '威剛', '8150': '南茂', '6147': '頎邦', '5347': '世界', '2363': '矽統', '2449': '京元電', '3036': '文曄'
}

def get_stock_name(stock_code):
    clean = stock_code.split('.')[0]
    return CODE_NAME_MAP.get(clean, clean)

# --- 5. 核心計算函數 ---
def calculate_adx(df, window=14):
    try:
        high, low, close = df['High'], df['Low'], df['Close']
        up, down = high.diff(), -low.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(window).mean()
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(window).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(window).mean() / atr)
        dx = (abs(plus_di - minus_di) / (abs(plus_di + minus_di) + 1e-9)) * 100
        return dx.rolling(window).mean()
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_atr(df, window=14):
    try:
        tr = pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift(1)), abs(df['Low']-df['Close'].shift(1))], axis=1).max(axis=1)
        return tr.rolling(window).mean()
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_obv(df):
    try: return (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    except: return pd.Series([0]*len(df), index=df.index)

# --- ★ v21.1 K線戰法全攻略引擎 ---
def detect_kline_pattern(df):
    if len(df) < 5: return "資料不足", 0
    t0 = df.iloc[-1]; t1 = df.iloc[-2]; t2 = df.iloc[-3]; t3 = df.iloc[-4]; t4 = df.iloc[-5]
    O0,H0,L0,C0 = t0['Open'], t0['High'], t0['Low'], t0['Close']
    O1,H1,L1,C1 = t1['Open'], t1['High'], t1['Low'], t1['Close']
    O2,H2,L2,C2 = t2['Open'], t2['High'], t2['Low'], t2['Close']
    
    def body(r): return abs(r['Close']-r['Open'])
    def upper(r): return r['High'] - max(r['Close'], r['Open'])
    def lower(r): return min(r['Close'], r['Open']) - r['Low']
    def is_red(r): return r['Close'] > r['Open']
    def is_green(r): return r['Close'] < r['Open']
    def is_doji(r): return body(r) < (r['High']-r['Low']) * 0.1
    
    avg_body = np.mean([body(df.iloc[-i]) for i in range(1, 6)])
    if avg_body == 0: avg_body = 0.1
    
    ma20 = df['Close'].rolling(20).mean().iloc[-1]
    trend_up = C0 > ma20
    trend_down = C0 < ma20
    
    if is_green(t2) and body(t2) > avg_body and body(t1) < avg_body*0.5 and t1['Close'] < C2 and is_red(t0) and C0 > (t2['Open']+t2['Close'])/2:
       return "晨星 (黎明將至) [空轉多] 🌅", 0.95
    if is_red(t2) and body(t2) > avg_body and body(t1) < avg_body*0.5 and t1['Close'] > C2 and is_green(t0) and C0 < (t2['Open']+t2['Close'])/2:
       return "夜星 (黑夜降臨) [多轉空] 🌃", -0.95
    if is_green(t1) and is_red(t0) and C0 > t1['Open'] and O0 < t1['Close']:
        return "多頭吞噬 (一舉扭轉) [空轉多] 🔥", 0.9
    if is_red(t1) and is_green(t0) and C0 < t1['Open'] and O0 > t1['Close']:
        return "空頭吞噬 (空方反撲) [多轉空] 🌧️", -0.9
    if is_green(t1) and is_red(t0) and O0 < t1['Low'] and C0 > (t1['Open']+t1['Close'])/2:
        return "貫穿線 (多方反擊) [空轉多] 🗡️", 0.8
    if is_red(t1) and is_green(t0) and O0 > t1['High'] and C0 < (t1['Open']+t1['Close'])/2:
        return "烏雲蓋頂 (空方壓頂) [多轉空] 🌥️", -0.8
    if lower(t0) > 2 * body(t0) and upper(t0) < body(t0) * 0.2:
        if trend_down: return "錘頭 (底部支撐) [空轉多] 🔨", 0.7
        if trend_up: return "上吊線 (高檔出貨?) [多轉空] 🎗️", -0.6
    if upper(t0) > 2 * body(t0) and lower(t0) < body(t0) * 0.2:
        if trend_up: return "流星 (高檔避雷針) [多轉空] ☄️", -0.7
        if trend_down: return "倒狀錘頭 (試盤反彈) [空轉多] ☝️", 0.4
    if is_red(t0) and is_red(t1) and is_red(t2) and C0>C1>C2:
        return "紅三兵 (多頭氣盛) [多頭持續] 💂‍♂️", 0.8
    if is_green(t0) and is_green(t1) and is_green(t2) and C0<C1<C2:
        return "黑三兵 (烏鴉滿天) [空頭持續] 🐻", -0.8
    
    # 趨勢動態解讀
    ma5 = df['Close'].rolling(5).mean().iloc[-1]
    prev_ma5 = df['Close'].rolling(5).mean().iloc[-2]
    prev_ma20 = df['Close'].rolling(20).mean().iloc[-2]
    
    if prev_ma5 <= prev_ma20 and ma5 > ma20 and ma5 > prev_ma5 and ma20 > prev_ma20:
        return "鳥嘴攻擊型態 [趨勢啟動] 🐦", 0.9
    if is_red(t0) and is_green(t1) and is_red(t2) and L0 > L2 and trend_down:
         return "W底雛形 (屁股型態) [見底訊號] 🍑", 0.7
    
    if C0 > ma5 and ma5 > ma20: return "多頭排列 (沿5日線強勢) 📈", 0.3
    if C0 < ma5 and ma5 < ma20: return "空頭排列 (沿5日線下跌) 📉", -0.3
    if C0 > ma20 and C1 <= prev_ma20: return "站上月線 (短線轉強) 🌤️", 0.4
    if C0 < ma20 and C1 >= prev_ma20: return "跌破月線 (短線轉弱) 🌧️", -0.4

    return "區間整理 (無明確型態) 💤", 0

# --- 價值與狀態 ---
def get_valuation_status(current_price, ma60, info_data):
    pe = info_data.get('pe', 'N/A')
    bias = (current_price - ma60) / ma60 * 100
    tech_val = "合理"
    if bias > 20: tech_val = "過熱(昂貴)"
    elif bias < -15: tech_val = "超跌(便宜)"
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
    return f"{tech_val}{fund_val}", bias

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
    quotes = ["💡 心法：Score 高不代表必勝，只代表勝率較高。", "💡 心法：新手死於追高，老手死於抄底。", "💡 心法：連續虧損時，縮小部位或停止交易。", "💡 心法：不持有部位，也是一種部位。", "💡 心法：交易的目標不是全對，而是活得久。"]
    return random.choice(quotes)

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
    is_aplus = ((df_cand['rs_rank'] >= 0.85) & (df_cand['ma20'] > df_cand['ma60']) & (df_cand['slope'] > 0) & (df_cand['vol_ratio'].between(1.5, 2.5)) & (df_cand['score_risk'] > 60))
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

def check_entry_gate(bias, rsi):
    if bias > 12: return "WAIT", "乖離過大"
    if rsi > 85: return "BAN", "指標過熱"
    return "PASS", "符合"

# --- 7. 繪圖引擎 ---
def create_stock_chart(stock_code):
    gc.collect()
    result_file = None
    result_text = ""
    with plot_lock:
        try:
            target = stock_code.upper().strip()
            df = fetch_data_finmind(target)

            if df.empty: return None, f"FinMind 查無代號 {target} 資料。"
            
            stock_name = get_stock_name(target)
            info_data = get_stock_info_finmind(target)
            last = df.iloc[-1]
            price = last['Close']
            eps = get_eps_from_price_pe(price, info_data.get('pe'))

            try:
                bench = get_benchmark_data()
                if not bench.empty:
                    common = df.index.intersection(bench.index)
                    if len(common) > 20:
                        s_ret = df.loc[common, 'Close'].pct_change(20)
                        b_ret = bench.loc[common, 'Close'].pct_change(20)
                        df.loc[common, 'RS'] = (1+s_ret)/(1+b_ret)
                    else: df['RS'] = 1.0
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
            rs_str = "無數據" if rs_val == 1.0 else ("強於大盤 🦅" if rs_val > 1.05 else ("弱於大盤 🐢" if rs_val < 0.95 else "跟隨大盤"))
            vol_ratio = last['Vol_Ratio'] if not pd.isna(last['Vol_Ratio']) else 1.0

            kline_pattern, kline_score = detect_kline_pattern(df)
            valuation_status_str, bias_val = get_valuation_status(price, ma60, info_data)

            if adx < 20: trend_quality = "盤整 💤"
            elif adx > 40: trend_quality = "強勁 🔥"
            else: trend_quality = "確立 ✅"

            if ma20 > ma60 and slope > 0: trend_dir = "多頭"
            elif ma20 < ma60 and slope < 0: trend_dir = "空頭"
            else: trend_dir = "震盪"

            stop = price - atr * 1.5
            final_stop = max(stop, ma20) if trend_dir == "多頭" and ma20 < price else stop
            target_price_val = price + atr * 3 

            entry_status, entry_msg = check_entry_gate(bias_val, rsi)
            entry_warning = f"\n{entry_msg}" if entry_status != "PASS" else ""

            advice = "觀望"
            if trend_dir == "多頭":
                if kline_score <= -0.5: advice = f"⚠️ 警戒：趨勢雖多，但{kline_pattern.split(' ')[0]}，留意回檔"
                elif "過熱" in valuation_status_str: advice = "⛔ 價值過熱，禁止追價"
                elif entry_status == "BAN": advice = "⛔ 指標過熱，禁止進場"
                elif entry_status == "WAIT": advice = "⏳ 短線乖離大，暫緩"
                elif kline_score > 0: advice = f"✅ 買點浮現 ({kline_pattern.split(' ')[0]})"
                elif adx < 20: advice = "盤整中，多看少做"
                elif rs_val < 0.95: advice = "弱於大盤，恐補跌"
                elif 60 <= rsi <= 75: advice = "量價健康，可尋買點"
                else: advice = "沿月線操作"
            elif trend_dir == "空頭":
                if kline_score > 0.5: advice = f"空頭反彈 ({kline_pattern.split(' ')[0]})，老手搶短"
                else: advice = "趨勢向下，勿接刀"
            else:
                if kline_score > 0.5: advice = f"震盪轉強 ({kline_pattern.split(' ')[0]})，老手試單"
                else: advice = "方向不明，建議觀望"

            exit_rule = f"🛑 **停損鐵律**：跌破 {final_stop:.1f} 市價出場。"
            analysis_report = (
                f"📊 {stock_name} ({target.split('.')[0]}) 診斷 [FinMind]\n"
                f"💰 現價: {price:.1f} | EPS: {eps}\n"
                f"📈 趨勢: {trend_dir} | {trend_quality}\n"
                f"🕯️ {kline_pattern}\n"
                f"💎 價值: {valuation_status_str}\n"
                f"🦅 RS值: {rs_val:.2f} ({rs_str})\n"
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
            try: ax1.set_title(f"{stock_name} ({target.split('.')[0]})", fontproperties=my_font, fontsize=18)
            except: ax1.set_title(f"{target.split('.')[0]}", fontsize=18)
            ax1.legend(loc='upper left', prop=my_font); ax1.grid(True, linestyle=':', alpha=0.5)
            ax2 = fig.add_subplot(3, 1, 2)
            cols = ['red' if c >= o else 'green' for c, o in zip(df['Close'], df['Open'])]
            ax2.bar(df.index, df['Volume'], color=cols, alpha=0.8)
            ax2.set_ylabel("Volume", fontproperties=my_font); ax2.grid(True, linestyle=':', alpha=0.3)
            ax3 = fig.add_subplot(3, 1, 3)
            ax3.plot(df.index, df['RSI'], color='purple')
            ax3.axhline(80, color='red', linestyle='--'); ax3.axhline(30, color='green', linestyle='--')
            ax3.set_ylabel("RSI", fontproperties=my_font); ax3.grid(True, linestyle=':', alpha=0.3)
            fig.autofmt_xdate()
            filename = f"{target.split('.')[0]}_{int(time.time())}.png"
            filepath = os.path.join(static_dir, filename)
            fig.savefig(filepath, bbox_inches='tight')
            result_file = filename
            del fig; del canvas
        except Exception as e:
            return None, f"繪圖失敗: {str(e)}\n\n{result_text}"
        finally: gc.collect()
    return result_file, result_text

# --- 8. 選股功能 ---
def scan_potential_stocks(max_price=None, sector_name=None):
    if sector_name and sector_name in SECTOR_DICT:
        watch_list = SECTOR_DICT[sector_name]
        title_prefix = f"【{sector_name}股】"
    else:
        watch_list = SECTOR_DICT.get("百元績優", [])
        title_prefix = "【百元績優】"

    recommendations = []
    candidates = []

    try:
        bench = get_benchmark_data()
        if not bench.empty:
            mkt = detect_market_state(bench)
            w = {'TREND': {'trend': 0.6, 'momentum': 0.3, 'risk': 0.1}, 'RANGE': {'trend': 0.4, 'momentum': 0.2, 'risk': 0.4}, 'VOLATILE': {'trend': 0.3, 'momentum': 0.4, 'risk': 0.3}}[mkt]
            b_ret = bench['Close'].pct_change(20).iloc[-1]
            market_commentary = get_market_commentary(mkt)
            stop_mult, target_mult, max_days, trade_type, risk_desc, max_trades = get_trade_params(mkt)
            if mkt == 'VOLATILE':
                return f"🔴 **市場熔斷啟動**\n\n目前盤勢為【{mkt}】，風險極高。\n系統已強制停止選股功能，請保留現金，靜待落底訊號。", []
        else:
            mkt, w, b_ret, trade_type, risk_desc = 'RANGE', {'trend': 0.4, 'momentum': 0.2, 'risk': 0.4}, 0, "區間突破單", "未知"
            stop_mult, target_mult, max_days, max_trades = 1.0, 1.5, 10, "1"
            market_commentary = "⚠️ 無法取得大盤狀態，請保守操作。"

        def process_stock_for_scan(stock):
            try:
                df = fetch_data_finmind(stock)
                if df.empty or len(df) < 60: return None
                price = df['Close'].iloc[-1]
                if max_price and price > max_price: return None

                ma20 = df['Close'].rolling(20).mean(); ma60 = df['Close'].rolling(60).mean()
                v_ma = df['Volume'].rolling(20).mean()
                slope = ma20.diff(5).iloc[-1]
                vol_r = df['Volume'].iloc[-1]/v_ma.iloc[-1] if v_ma.iloc[-1]>0 else 0
                s_ret = df['Close'].pct_change(20).iloc[-1]
                rs = (1+s_ret)/(1+b_ret)
                tr = (df['High']-df['Low']).rolling(14).mean().iloc[-1]
                atr = tr if tr > 0 else price*0.02
                
                delta = df['Close'].diff()
                gain = (delta.where(delta>0, 0)).rolling(14).mean()
                loss = (-delta.where(delta<0, 0)).rolling(14).mean()
                rs_idx = gain/loss
                rsi = 100-(100/(1+rs_idx))
                curr_rsi = rsi.iloc[-1]
                curr_ma20 = ma20.iloc[-1]; curr_ma60 = ma60.iloc[-1]

                if curr_ma20 > curr_ma60 and slope > 0:
                    return {
                        'stock': stock, 'price': price, 'ma20': curr_ma20, 'ma60': curr_ma60,
                        'slope': slope, 'vol_ratio': vol_r, 'atr': atr, 'rs_raw': rs, 'rs_rank': 0,
                        'rsi': curr_rsi 
                    }
            except: pass
            return None

        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_stock = {executor.submit(process_stock_for_scan, stock): stock for stock in watch_list}
            for future in as_completed(future_to_stock):
                res = future.result()
                if res: candidates.append(res)

        if not candidates:
             return title_prefix, ["今日掃描無符合強勢條件之個股，或因 API 限制查無資料。"]

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
                
                bias_val = (r.price - r.ma20) / r.ma20 * 100
                entry_status, _ = check_entry_gate(bias_val, r.rsi)
                
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

    except Exception as e:
        return title_prefix, [f"掃描發生未預期錯誤: {str(e)}"]

    return title_prefix, recommendations

# --- ★ v26.0 雲端量化回測引擎 ---
def run_multi_strategy_backtest(stock_code):
    clean_code = stock_code.upper().replace('.TW', '').replace('.TWO', '').strip()
    stock_name = get_stock_name(clean_code)
    
    # 定義回測區間 (近一年)
    end_date_dt = datetime.now()
    start_date_dt = end_date_dt - timedelta(days=365)
    start_date = start_date_dt.strftime('%Y-%m-%d')
    end_date = end_date_dt.strftime('%Y-%m-%d')
    
    try:
        # 多線程抓取四大資料庫
        with ThreadPoolExecutor(max_workers=4) as executor:
            f_price = executor.submit(fetch_data_finmind, clean_code, 400, start_date)
            f_bench = executor.submit(fetch_data_finmind, "0050", 400, start_date)
            f_inst = executor.submit(call_finmind_api, "TaiwanStockInstitutionalInvestorsBuySell", clean_code, start_date)
            f_margin = executor.submit(call_finmind_api, "TaiwanStockMarginPurchaseShortSale", clean_code, start_date)
        
        df = f_price.result()
        bench_df = f_bench.result()
        df_inst = f_inst.result()
        df_margin = f_margin.result()

        if df.empty or bench_df.empty:
            return f"❌ {clean_code} 回測資料不足，請確認代號或 API 狀態。"
        
        # 對齊日期
        df = df.loc[start_date:]
        bench_df = bench_df.loc[start_date:]
        common_idx = df.index.intersection(bench_df.index)
        df = df.loc[common_idx]
        bench_df = bench_df.loc[common_idx]

        if len(df) < 50: return f"❌ {clean_code} 有效交易日不足，無法回測。"

        # --- 計算大盤基準 ---
        bench_daily_ret = bench_df['Close'].pct_change().fillna(0)
        bench_cum = (1 + bench_daily_ret).cumprod()
        bench_ret = bench_cum.iloc[-1] - 1
        bench_mdd = (bench_cum / bench_cum.cummax() - 1).min()

        # --- 預先計算個股指標 ---
        close = df['Close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macdsig = macd.ewm(span=9, adjust=False).mean()
        
        min9 = df['Low'].rolling(9).min()
        max9 = df['High'].rolling(9).max()
        rsv = (close - min9) / (max9 - min9 + 1e-9) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        
        low20 = close.rolling(20).min()
        high20 = close.rolling(20).max()

        results = []
        
        # --- 核心績效計算器 (支援手續費與稅金) ---
        def calc_perf(sig):
            pos = sig.shift(1).fillna(0) # T日訊號，T+1日持有
            trade = pos.diff().fillna(0)
            daily_ret = close.pct_change().fillna(0)
            
            # 手續費 0.1425%, 交易稅 0.3%
            buy_cost = (trade == 1) * 0.001425
            sell_cost = (trade == -1) * 0.004425
            
            net_ret = pos * daily_ret - buy_cost - sell_cost
            cum = (1 + net_ret).cumprod()
            if len(cum) > 0:
                final = cum.iloc[-1] - 1
                mdd = (cum / cum.cummax() - 1).min()
                return final, mdd
            return 0, 0

        # 1. ContinueHolding (買入持有)
        sig_hold = pd.Series(1, index=df.index)
        results.append(("Holding", *calc_perf(sig_hold)))

        # 2. MaCrossOver (5日線上穿20日線)
        sig_ma = (ma5 > ma20).astype(int)
        results.append(("MaCrossOver", *calc_perf(sig_ma)))

        # 3. MacdCrossOver
        sig_macd = (macd > macdsig).astype(int)
        results.append(("MacdCross", *calc_perf(sig_macd)))

        # 4. KdCrossOver
        sig_kdx = (k > d).astype(int)
        results.append(("KdCrossOver", *calc_perf(sig_kdx)))

        # 5. Kd (低買高賣)
        sig_kd = pd.Series(np.nan, index=df.index)
        sig_kd[k < 20] = 1
        sig_kd[k > 80] = 0
        sig_kd = sig_kd.ffill().fillna(0)
        results.append(("Kd", *calc_perf(sig_kd)))

        # 6. NaiveKd (>50 買入)
        sig_nkd = (k > 50).astype(int)
        results.append(("NaiveKd", *calc_perf(sig_nkd)))

        # 7. Bias (乖離率過低買入)
        bias = (close - ma20) / ma20
        sig_bias = pd.Series(np.nan, index=df.index)
        sig_bias[bias < -0.05] = 1
        sig_bias[bias > 0.05] = 0
        sig_bias = sig_bias.ffill().fillna(0)
        results.append(("Bias", *calc_perf(sig_bias)))

        # 8. MaxMinPeriodBias (創新低買，創新高賣)
        sig_mm = pd.Series(np.nan, index=df.index)
        sig_mm[close <= low20] = 1
        sig_mm[close >= high20] = 0
        sig_mm = sig_mm.ffill().fillna(0)
        results.append(("MaxMinBias", *calc_perf(sig_mm)))

        # 9. InstitutionalInvestorsFollower (法人跟隨)
        if not df_inst.empty and 'date' in df_inst.columns:
            df_inst['date'] = pd.to_datetime(df_inst['date'])
            df_inst['net'] = df_inst['buy'] - df_inst['sell']
            daily_inst = df_inst.groupby('date')['net'].sum()
            
            sig_inst_temp = pd.Series(np.nan, index=df.index)
            # 確保有資料的日子才去標記
            common_d = daily_inst.index.intersection(df.index)
            sig_inst_temp.loc[daily_inst[daily_inst > 0].index.intersection(common_d)] = 1
            sig_inst_temp.loc[daily_inst[daily_inst < 0].index.intersection(common_d)] = 0
            sig_inst = sig_inst_temp.ffill().fillna(0)
            results.append(("InstFollow", *calc_perf(sig_inst)))
        else:
            results.append(("InstFollow", 0, 0))

        # 10. ShortSaleMarginPurchaseRatio (融資跟隨)
        if not df_margin.empty and 'date' in df_margin.columns:
            df_margin['date'] = pd.to_datetime(df_margin['date'])
            df_margin = df_margin.set_index('date')
            common_m = df.index.intersection(df_margin.index)
            if 'MarginPurchaseTodayBalance' in df_margin.columns:
                margin_bal = df_margin.loc[common_m, 'MarginPurchaseTodayBalance']
                margin_diff = margin_bal.diff()
                sig_margin = (margin_diff > 0).astype(int).reindex(df.index).fillna(0)
                results.append(("MarginRatio", *calc_perf(sig_margin)))
            else:
                results.append(("MarginRatio", 0, 0))
        else:
            results.append(("MarginRatio", 0, 0))

        # --- 生成報告 ---
        report = f"📊 {stock_name} ({clean_code}) 10大策略回測\n"
        report += f"起始: {start_date} | 結束: {end_date}\n"
        report += f"基準(0050) 獲利: {bench_ret*100:.1f}% | 損失: {bench_mdd*100:.1f}%\n"
        report += f"資金: 100萬 | 手續費: 0.1425% | 稅金: 0.3%\n"
        report += "=====================\n"
        report += "策略 | 損失/大盤 | 獲利/大盤\n"
        report += "---------------------\n"
        
        for name, ret, mdd in results:
            short_name = name[:11].ljust(11) 
            report += f"{short_name} | {mdd*100:.1f}%/{bench_mdd*100:.1f}% | {ret*100:.1f}%/{bench_ret*100:.1f}%\n"
            
        report += "=====================\n"
        
        # 尋找最佳策略 (排除完全沒交易的 0% 策略)
        valid_results = [r for r in results if r[1] != 0]
        if valid_results:
            best_ret_strat = max(valid_results, key=lambda x: x[1])
            best_mdd_strat = max(valid_results, key=lambda x: x[2]) # MDD為負數，max代表最接近0
            
            report += f"🏆 最佳獲利: {best_ret_strat[0]} ({best_ret_strat[1]*100:.1f}%)\n"
            report += f"🛡️ 最抗跌: {best_mdd_strat[0]} ({best_mdd_strat[2]*100:.1f}%)\n\n"
            report += "💡 【組合分析建議】:\n"
            report += f"單一策略盲點多，建議結合高報酬的【{best_ret_strat[0]}】提供攻擊動能，並輔以【{best_mdd_strat[0]}】控制下檔風險，打造穩健的雙因子模型。"
        
        return report
        
    except Exception as e:
        logger.error(f"回測引擎錯誤: {traceback.format_exc()}")
        return f"❌ 回測發生系統錯誤: {str(e)}"

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
            "🕯️ **K線教學 (多轉空/空轉多)**\n"
            "• 🌅 **晨星**: [空轉多] 跌勢末端出現一根紅K吃掉黑K，黎明將至。\n"
            "• 🌃 **夜星**: [多轉空] 漲勢末端出現黑K吞噬紅K，黑夜降臨。\n"
            "• 🔥 **吞噬**: [強力反轉] 今日K線完全包覆昨日，力道極強。\n"
            "• 🔨 **錘頭**: [底部支撐] 長下影線，代表低檔有人接手。\n"
            "• ☄️ **流星**: [頭部壓力] 長上影線，代表高檔有人出貨。\n"
            "• 📈 **貫穿線**: [空轉多] 紅K收盤穿越昨黑K實體一半以上。\n"
            "• 🌥️ **烏雲蓋頂**: [多轉空] 黑K收盤跌破昨紅K實體一半以上。\n"
            "• 🐦 **鳥嘴**: [趨勢啟動] 5日線上穿20日線，開口擴大。\n"
            "• 🍑 **屁股**: [見底訊號] W底雛形，跌勢末端連續紅黑K墊高。"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))
        return

    # 指令模糊辨識
    if any(x in msg for x in ["小資", "便宜"]):
        msg = "百元推薦"
    elif any(x in msg for x in ["績優"]):
        msg = "百元績優推薦" 
    elif any(x in msg for x in ["智能", "選股", "幫我選"]):
        msg = "推薦"

    # ★ v26.0 攔截回測指令
    if msg.startswith("回測") or msg.startswith("分析"):
        stock_code = msg.replace("回測", "").replace("分析", "").strip()
        if not stock_code:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入要回測的代號，例如：回測 2330"))
            return
        
        report_txt = run_multi_strategy_backtest(stock_code)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=report_txt))
        return

    if msg in ["功能", "指令", "Help", "help", "menu"]:
        menu = (
            f"🤖 **股市全能助理** ({APP_VERSION})\n"
            "======================\n\n"
            "🔍 **個股診斷**\n"
            "輸入：`2330`\n"
            "👉 線圖、K線型態、價值評估、教練建議\n\n"
            "🔬 **10大策略回測 (Premium)**\n"
            "輸入：`回測 2330`\n"
            "👉 執行一年期策略回測與優缺點分析\n\n"
            "📊 **智能選股 (極速版)**\n"
            "輸入：`推薦` 或 `選股`\n"
            "👉 自動偵測盤勢，A+訊號優先展示\n\n"
            "💰 **小資選股**\n"
            "輸入：`小資` 或 `百元推薦`\n"
            "👉 掃描 100 元以內的強勢股\n\n"
            "🏅 **績優選股**\n"
            "輸入：`績優股`\n"
            "👉 掃描精選績優股\n\n"
            "📖 **K線教學**\n"
            "輸入：`說明`"
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
    else:
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