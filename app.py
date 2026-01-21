import os
import time
import numpy as np
import pandas as pd
import yfinance as yf
# ★ 改用物件導向繪圖，解決雲端崩潰問題
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

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 設定應用程式版本 ---
APP_VERSION = "v6.2 終極繪圖修正版 (OO模式+畫布優化)"

# --- 設定日誌 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- 設定 matplotlib 後端 (必要) ---
matplotlib.use('Agg')

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
    my_font = None # Fallback

# --- 3. 全域快取 ---
EPS_CACHE = {}
def get_eps_cached(ticker_symbol):
    if ticker_symbol in EPS_CACHE: return EPS_CACHE[ticker_symbol]
    try:
        info = yf.Ticker(ticker_symbol).info
        eps = info.get('trailingEps') or info.get('forwardEps') or 'N/A'
        EPS_CACHE[ticker_symbol] = eps
        return eps
    except: return 'N/A'

# --- 4. 資料庫定義 (完整版) ---
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
    "台積電集團": ['2330.TW', '5347.TWO', '3443.TW', '3374.TW', '3661.TW', '3105.TWO'],
    "鴻海集團": ['2317.TW', '2328.TW', '2354.TW', '6414.TW', '5243.TW', '3413.TW', '6451.TW'],
    "台塑集團": ['1301.TW', '1303.TW', '1326.TW', '6505.TW', '2408.TW', '8039.TW'],
    "聯電集團": ['2303.TW', '3037.TW', '3035.TW', '3034.TW', '3529.TWO', '6166.TWO'],
    "長榮集團": ['2603.TW', '2618.TW', '2609.TW', '2637.TW', '2607.TW'],
    "華新集團": ['1605.TW', '2492.TW', '5469.TWO', '6173.TWO', '8163.TWO', '2344.TW'],
    "國巨集團": ['2327.TW', '2456.TW', '6271.TW', '5328.TWO', '3026.TW'],
    "永豐餘集團": ['1907.TW', '8069.TWO', '6404.TW'],
    "統一集團": ['1216.TW', '1232.TW', '2912.TW', '1210.TW'],
    "遠東集團": ['1402.TW', '1102.TW', '2903.TW', '2845.TW', '1710.TW'],
    "潤泰集團": ['2915.TW', '9945.TW', '8463.TW', '4174.TWO'],
    "金仁寶集團": ['2312.TW', '2324.TW', '6282.TW', '3715.TW'],
    "裕隆集團": ['2201.TW', '2204.TW', '2412.TW', '3122.TWO'],
    "大同集團": ['2371.TW', '2313.TW', '3519.TW', '8081.TW'],
    "聯華神通集團": ['1229.TW', '2347.TW', '3702.TW', '3005.TW'],
    "友達集團": ['2409.TW', '4960.TW', '6120.TWO'],
    "半導體": ['2330.TW', '2454.TW', '2303.TW', '3711.TW', '3034.TW', '2379.TW', '3443.TW', '3035.TW', '3661.TW'],
    "電子": ['2317.TW', '2382.TW', '3231.TW', '2353.TW', '2357.TW', '2324.TW', '2301.TW', '2356.TW'],
    "光電": ['3008.TW', '3406.TW', '2409.TW', '3481.TW', '6706.TW', '2340.TW'],
    "網通": ['2345.TW', '5388.TWO', '2332.TW', '3704.TW', '3596.TWO', '6285.TW'],
    "電零組": ['2308.TW', '2313.TW', '3037.TW', '2383.TW', '2368.TW', '3044.TW'],
    "電腦週邊": ['2357.TW', '2324.TW', '3231.TW', '2382.TW', '2301.TW', '2376.TW'],
    "資訊服務": ['2471.TW', '3029.TW', '3130.TWO', '6214.TW'],
    "航運": ['2603.TW', '2609.TW', '2615.TW', '2618.TW', '2610.TW', '2637.TW', '2606.TW'],
    "鋼鐵": ['2002.TW', '2014.TW', '2027.TW', '2006.TW', '2031.TW', '2009.TW'],
    "塑膠": ['1301.TW', '1303.TW', '1326.TW', '1304.TW', '1308.TW'],
    "紡織": ['1402.TW', '1476.TW', '1477.TW', '1409.TW', '1440.TW'],
    "電機": ['1503.TW', '1504.TW', '1513.TW', '1519.TW', '1514.TW'],
    "電纜": ['1605.TW', '1609.TW', '1608.TW', '1618.TW'],
    "水泥": ['1101.TW', '1102.TW', '1108.TW', '1110.TW'],
    "玻璃": ['1802.TW', '1809.TW', '1806.TW'],
    "造紙": ['1904.TW', '1907.TW', '1909.TW', '1906.TW'],
    "橡膠": ['2105.TW', '2103.TW', '2106.TW', '2104.TW'],
    "汽車": ['2207.TW', '2201.TW', '2204.TW', '1319.TW', '2227.TW'],
    "食品": ['1216.TW', '1210.TW', '1227.TW', '1201.TW', '1215.TW'],
    "營建": ['2501.TW', '2542.TW', '5522.TW', '2548.TW', '2520.TW', '2538.TW'],
    "觀光": ['2707.TW', '2727.TW', '2723.TW', '5706.TWO', '2704.TW'],
    "金融": ['2881.TW', '2882.TW', '2886.TW', '2891.TW', '2892.TW', '2884.TW', '5880.TW', '2880.TW', '2885.TW'],
    "生技": ['6446.TW', '1795.TW', '4128.TWO', '1760.TW', '4114.TWO', '4743.TWO', '3176.TWO'],
    "化學": ['1722.TW', '1708.TW', '1710.TW', '1717.TW'],
    "軍工": ['2634.TW', '8033.TWO', '5284.TWO', '3005.TW', '8222.TWO'],
    "AI": ['3231.TW', '2382.TW', '6669.TW', '2376.TW', '2356.TW', '3017.TW'],
    "ETF": ['0050.TW', '0056.TW', '00878.TW', '00929.TW', '00919.TW', '006208.TW'],
}

CODE_NAME_MAP = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '3711': '日月光', '3034': '聯詠', '2379': '瑞昱', '3443': '創意', '3035': '智原', '3661': '世芯',
    '2317': '鴻海', '2382': '廣達', '3231': '緯創', '2353': '宏碁', '2357': '華碩', '2324': '仁寶', '2301': '光寶科', '2356': '英業達',
    '2352': '佳世達', '2337': '旺宏', '2344': '華邦電', '2449': '京元電', '2363': '矽統', '3036': '文曄',
    '3008': '大立光', '3406': '玉晶光', '2409': '友達', '3481': '群創', '6706': '惠特', '2340': '台亞',
    '2345': '智邦', '5388': '中磊', '2332': '友訊', '3704': '合勤控', '3596': '智易', '6285': '啟碁',
    '2308': '台達電', '2313': '華通', '3037': '欣興', '2383': '台光電', '2368': '金像電', '3044': '健鼎',
    '2376': '技嘉', '2471': '資通', '3029': '零壹', '3130': '一零四', '6214': '精誠',
    '2603': '長榮', '2609': '陽明', '2615': '萬海', '2618': '長榮航', '2610': '華航', '2637': '慧洋', '2606': '裕民',
    '2002': '中鋼', '2014': '中鴻', '2027': '大成鋼', '2006': '東和鋼鐵', '2031': '新光鋼', '2009': '第一銅',
    '1301': '台塑', '1303': '南亞', '1326': '台化', '1304': '台聚', '1308': '亞聚',
    '1402': '遠東新', '1476': '儒鴻', '1477': '聚陽', '1409': '新纖', '1440': '南紡',
    '1503': '士電', '1504': '東元', '1513': '中興電', '1519': '華城', '1514': '亞力',
    '1605': '華新', '1609': '大亞', '1608': '華榮', '1618': '合機',
    '1101': '台泥', '1102': '亞泥', '1108': '幸福', '1110': '東泥',
    '1802': '台玻', '1809': '中釉', '1806': '冠軍',
    '1904': '正隆', '1907': '永豐餘', '1909': '榮成', '1906': '寶隆',
    '2105': '正新', '2103': '台橡', '2106': '建大', '2104': '中橡',
    '2207': '和泰車', '2201': '裕隆', '2204': '中華', '1319': '東陽', '2227': '裕日車',
    '1216': '統一', '1210': '大成', '1227': '佳格', '1201': '味全', '1215': '卜蜂',
    '2501': '國建', '2542': '興富發', '5522': '遠雄', '2548': '華固', '2520': '冠德', '2538': '基泰',
    '2707': '晶華', '2727': '王品', '2723': '美食', '5706': '鳳凰', '2704': '六福',
    '2881': '富邦金', '2882': '國泰金', '2886': '兆豐金', '2891': '中信金', '2892': '第一金', '2884': '玉山金', '5880': '合庫金', '2880': '華南金', '2885': '元大金',
    '2883': '開發金', '2887': '台新金', '2890': '永豐金', '2834': '臺企銀', '2801': '彰銀',
    '6446': '藥華藥', '1795': '美時', '4128': '中天', '1760': '寶齡富錦', '4114': '健喬', '4743': '合一', '3176': '基亞',
    '1722': '台肥', '1708': '東鹼', '1710': '東聯', '1717': '長興',
    '2634': '漢翔', '8033': '雷虎', '5284': 'jpp-KY', '3005': '神基', '8222': '寶一',
    '6669': '緯穎', '3017': '奇鋐',
    '0050': '元大台灣50', '0056': '元大高股息', '00878': '國泰永續', '00929': '復華科優息', '00919': '群益精選', '006208': '富邦台50',
    '5347': '世界', '3374': '精材', '3105': '穩懋', '3260': '威剛', '8150': '南茂', '6147': '頎邦',
    '2328': '廣宇', '2354': '鴻準', '6414': '樺漢', '5243': '乙盛', '3413': '京鼎', '6451': '訊芯',
    '6505': '台塑化', '2408': '南亞科', '8039': '台虹',
    '3529': '力旺', '6166': '凌華',
    '2607': '榮運',
    '2492': '華新科', '5469': '瀚宇博', '6173': '信昌電', '8163': '達方', '2344': '華邦電',
    '2327': '國巨', '2456': '奇力新', '6271': '同欣電', '5328': '華容', '3026': '禾伸堂',
    '8069': '元太', '6404': '鳳凰',
    '1232': '大統益', '2912': '統一超',
    '2903': '遠百', '2845': '遠東銀',
    '2915': '潤泰全', '9945': '潤泰新', '8463': '潤泰材', '4174': '浩鼎',
    '2312': '金寶', '6282': '康舒', '3715': '定穎',
    '2412': '中華電', '3122': '笙泉',
    '2371': '大同', '3519': '綠能', '8081': '致新',
    '1229': '聯華', '2347': '聯強', '3702': '大聯大',
    '4960': '誠美材', '6120': '達運'
}

def get_stock_name(stock_code):
    code_only = stock_code.split('.')[0]
    return CODE_NAME_MAP.get(code_only, stock_code)

# --- 5. 計算指標函數 ---
def calculate_adx(df, window=14):
    try:
        high, low, close = df['High'], df['Low'], df['Close']
        up, down = high.diff(), -low.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window).mean()
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(window).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(window).mean() / atr)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)) * 100
        return dx.rolling(window).mean()
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_atr(df, window=14):
    try:
        high, low, close = df['High'], df['Low'], df['Close']
        tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
        return tr.rolling(window).mean()
    except: return pd.Series([0]*len(df), index=df.index)

def calculate_obv(df):
    try: return (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    except: return pd.Series([0]*len(df), index=df.index)

def fetch_data_with_retry(ticker, period="1y", retries=3, delay=1):
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
    atr_pct = (calculate_atr(index_df).iloc[-1] / last['Close']) if last['Close'] > 0 else 0
    ma20 = index_df['Close'].rolling(20).mean().iloc[-1]
    ma60 = index_df['Close'].rolling(60).mean().iloc[-1]
    
    if ma20 > ma60 and adx > 25: return 'TREND'
    elif atr_pct < 0.012: return 'RANGE'
    else: return 'VOLATILE'

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
    score_mom = (score_slope * 0.4) + (score_vol * 0.6)
    
    atr_pct = df_cand['atr'] / df_cand['price']
    dist = (atr_pct - 0.03).abs()
    score_risk = (100 - (dist * 100 * 20)).clip(lower=0)
    
    df_cand['total_score'] = (
        score_trend * weights['trend'] +
        score_mom * weights['momentum'] +
        score_risk * weights['risk']
    )
    return df_cand

def get_trade_params(state):
    if state == 'TREND': return 1.5, 3.5, 30, "趨勢盤 (順勢操作)"
    elif state == 'RANGE': return 1.0, 1.5, 10, "盤整盤 (快進快出)"
    else: return 2.0, 2.0, 5, "波動盤 (防洗盤)"

def get_position_sizing(score):
    if score >= 90: return "重倉 (1.5x) 🔥"
    elif score >= 80: return "標準倉 (1.0x) ✅"
    elif score >= 70: return "輕倉 (0.5x) 🛡️"
    else: return "觀望 (0x) 💤"

# --- 7. 繪圖引擎 (OO模式) ---
def create_stock_chart(stock_code):
    gc.collect()
    result_file, result_text = None, ""
    
    try:
        raw_code = stock_code.upper().strip()
        # 1. 取得資料
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
            df = fetch_data_with_retry(ticker_two, period="1y")
            if not df.empty:
                target = target_two
                ticker = ticker_two
        
        if df.empty: return None, "系統繁忙或找不到代號。"
        
        stock_name = get_stock_name(target)
        eps = get_eps_cached(target)

        # 抓大盤 RS (簡化版，失敗不卡)
        try:
            bench = yf.Ticker("0050.TW").history(period="1y")
            common = df.index.intersection(bench.index)
            if len(common) > 20:
                s_ret = df.loc[common, 'Close'].pct_change(20)
                b_ret = bench.loc[common, 'Close'].pct_change(20)
                df.loc[common, 'RS'] = (1+s_ret)/(1+b_ret)
            else: df['RS'] = 1.0
        except: df['RS'] = 1.0

        # 指標
        if len(df) < 60:
            df['MA20'] = df['Close'].rolling(20).mean()
            df['MA60'] = df['MA20']
        else:
            df['MA20'] = df['Close'].rolling(20).mean()
            df['MA60'] = df['Close'].rolling(60).mean()
        
        df['Slope'] = df['MA20'].diff(5)
        
        delta = df['Close'].diff()
        gain = (delta.where(delta>0, 0)).rolling(14).mean()
        loss = (-delta.where(delta<0, 0)).rolling(14).mean()
        rs_idx = gain / loss
        df['RSI'] = 100 - (100/(1+rs_idx))
        
        df['Vol_MA20'] = df['Volume'].rolling(20).mean()
        df['Vol_Ratio'] = df['Volume'] / df['Vol_MA20']
        
        df['ADX'] = calculate_adx(df)
        df['ATR'] = calculate_atr(df)
        df['OBV'] = calculate_obv(df)

        # 策略判斷
        last = df.iloc[-1]
        price = last['Close']
        ma20, ma60 = last['MA20'], last['MA60']
        slope = last['Slope'] if not pd.isna(last['Slope']) else 0
        rsi = last['RSI'] if not pd.isna(last['RSI']) else 50
        adx = last['ADX'] if not pd.isna(last['ADX']) else 0
        atr = last['ATR'] if not pd.isna(last['ATR']) and last['ATR'] > 0 else price*0.02
        rs_val = last['RS'] if 'RS' in df.columns and not pd.isna(last['RS']) else 1.0
        vol_ratio = last['Vol_Ratio'] if not pd.isna(last['Vol_Ratio']) else 1.0

        if adx < 20: trend_quality = "盤整 💤"
        elif adx > 40: trend_quality = "趨勢強勁 🔥"
        else: trend_quality = "趨勢確立 ✅"

        if ma20 > ma60 and slope > 0: trend_dir = "多頭"
        elif ma20 < ma60 and slope < 0: trend_dir = "空頭"
        else: trend_dir = "震盪"

        if rs_val > 1.05: rs_str = "強於大盤 🦅"
        elif rs_val < 0.95: rs_str = "弱於大盤 🐢"
        else: rs_str = "跟隨大盤"

        stop_loss = price - atr * 1.5
        final_stop = max(stop_loss, ma20) if trend_dir == "多頭" and ma20 < price else stop_loss
        target = price + atr * 3

        advice = "觀望"
        if trend_dir == "多頭":
            if adx < 20: advice = "盤整股，觀望"
            elif rs_val < 1: advice = "趨勢好但跑輸大盤"
            elif vol_ratio > 3: advice = "短線爆量過熱"
            elif rsi < 40: advice = "回檔中，等止跌"
            elif 60 <= rsi <= 75: advice = "量價健康，可佈局"
            else: advice = "沿月線操作"
        elif trend_dir == "空頭": advice = "趨勢向下，勿接刀"
        
        result_text = (
            f"📊 {stock_name} ({target}) 實戰診斷\n"
            f"💰 現價: {price:.1f} | EPS: {eps}\n"
            f"📈 趨勢: {trend_dir} | {trend_quality}\n"
            f"🦅 RS值: {rs_val:.2f} ({rs_str})\n"
            f"🌊 動能: {vol_ratio:.1f}\n"
            f"------------------\n"
            f"🎯 目標: {target:.1f} | 🛑 停損: {final_stop:.1f}\n"
            f"💡 建議: {advice}\n"
            f"(輸入「說明」看名詞解釋)"
        )

        # ★ 純 OO 繪圖 (無 plt)
        fig = Figure(figsize=(10, 10))
        canvas = FigureCanvas(fig)
        
        ax1 = fig.add_subplot(3, 1, 1)
        ax1.plot(df.index, df['Close'], color='black', alpha=0.6, label='Price')
        if len(df) >= 20: ax1.plot(df.index, df['MA20'], color='#FF9900', linestyle='--', label='MA20')
        if len(df) >= 60: ax1.plot(df.index, df['MA60'], color='#0066CC', linewidth=2, label='MA60')
        
        # 標題字型處理
        title_prop = my_font if my_font else None
        try:
            ax1.set_title(f"{stock_name} ({target}) 實戰分析", fontproperties=title_prop, fontsize=18)
        except:
            ax1.set_title(f"{target} Analysis", fontsize=18)
            
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

    except Exception as e:
        return None, f"繪圖失敗: {str(e)}\n\n{result_text}"

    return result_file, result_text

# --- 8. 選股功能 (略，同前) ---
def scan_potential_stocks(max_price=None, sector_name=None):
    if sector_name == "隨機":
        all_s = set()
        for s in SECTOR_DICT.values():
            for x in s: all_s.add(x)
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
            bench = yf.Ticker("0050.TW").history(period="6mo")
            mkt = detect_market_state(bench)
            w = WEIGHT_BY_STATE[mkt]
            b_ret = bench['Close'].pct_change(20).iloc[-1] if not bench.empty else 0
            state_desc = get_trade_params(mkt)[3]
        except:
            mkt, w, b_ret, state_desc = 'RANGE', WEIGHT_BY_STATE['RANGE'], 0, "盤整"

        data = yf.download(watch_list, period="3mo", progress=False)
        if data.empty: return title_prefix, ["Yahoo 限流中，請稍候"]

        for stock in watch_list:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    try:
                        c = data['Close'][stock]; v = data['Volume'][stock]
                        h = data['High'][stock]; l = data['Low'][stock]
                    except: continue
                else:
                    c = data['Close']; v = data['Volume']
                    h = data['High']; l = data['Low']
                
                if isinstance(c, pd.DataFrame): 
                    if c.empty: continue
                    c=c.iloc[:,0]; v=v.iloc[:,0]; h=h.iloc[:,0]; l=l.iloc[:,0]

                c = c.dropna()
                if len(c) < 60: continue
                price = c.iloc[-1]
                if max_price and price > max_price: continue

                ma20 = c.rolling(20).mean()
                ma60 = c.rolling(60).mean()
                v_ma = v.rolling(20).mean()
                slope = ma20.diff(5).iloc[-1]
                vol_r = v.iloc[-1]/v_ma.iloc[-1] if v_ma.iloc[-1]>0 else 0
                s_ret = c.pct_change(20).iloc[-1]
                rs = (1+s_ret)/(1+b_ret)
                tr = (h-l).rolling(14).mean().iloc[-1]
                atr = tr if tr > 0 else price*0.02

                if ma20.iloc[-1] > ma60.iloc[-1] and slope > 0:
                    candidates.append({
                        'stock': stock, 'price': price, 'ma20': ma20.iloc[-1], 'ma60': ma60.iloc[-1],
                        'slope': slope, 'vol_ratio': vol_r, 'atr': atr, 'rs_raw': rs, 'rs_rank': 0
                    })
            except: continue

        if candidates:
            df = pd.DataFrame(candidates)
            df['rs_rank'] = df['rs_raw'].rank(pct=True)
            df = calculate_score(df, w)
            
            th = 70 if mkt == 'RANGE' else 60
            picks = df[df['total_score']>=th].sort_values('total_score', ascending=False).head(6)
            
            icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
            for i, r in enumerate(picks.itertuples()):
                name = get_stock_name(r.stock)
                stop, target, _, _ = get_trade_params(mkt)
                stop_price = r.price - r.atr*stop
                target_price = r.price + r.atr*target
                icon = icons[i] if i < 6 else "🔹"
                pos = get_position_sizing(r.total_score)
                
                info = (
                    f"{icon} {name} ({r.stock.split('.')[0]})\n"
                    f"🏆 Score: {int(r.total_score)} | 倉位: {pos}\n"
                    f"💰 {r.price:.1f} | RS Top {int((1-r.rs_rank)*100)}%\n"
                    f"🎯 {target_price:.1f} | 🛑 {stop_price:.1f}"
                )
                recommendations.append(info)
            
            title_prefix += f"\n({state_desc})"

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
    
    # 新手教學
    if msg in ["說明", "教學", "名詞解釋", "新手", "看不懂"]:
        txt = (
            "🎓 **股市小白 專有名詞懶人包**\n"
            "======================\n\n"
            "⚖️ **倉位建議**\n"
            "• 🔥 重倉 (1.5x): 分數>90，勝率極高。\n"
            "• ✅ 標準倉 (1.0x): 分數>80，正常買進。\n"
            "• 🛡️ 輕倉 (0.5x): 分數>70，嘗試性建倉。\n\n"
            "🏆 **Score (綜合評分)**\n"
            "• 滿分100，越高越好，代表趨勢+資金+動能都到位。\n\n"
            "🦅 **RS Rank (相對強弱)**\n"
            "• Top 10%: 代表打敗市場90%的股票。\n\n"
            "🛡️ **ATR (真實波幅)**\n"
            "• 用來設停損，波動越大停損設越遠。"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))
        return

    # 選單
    if msg in ["功能", "指令", "Help", "help", "menu"]:
        menu = (
            f"🤖 **股市全能助理** ({APP_VERSION})\n"
            "======================\n\n"
            "🔍 **個股診斷**\n"
            "輸入：`2330` 或 `8069`\n"
            "👉 線圖、EPS、RS、建議\n\n"
            "📊 **智能選股 (自適應)**\n"
            "輸入：`推薦` 或 `選股`\n"
            "👉 自動偵測大盤狀態，調整權重\n\n"
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

    # 選股
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
        # 個股診斷
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