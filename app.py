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
import logging
import traceback
import sys
import threading
import gc

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 設定應用程式版本 ---
APP_VERSION = "v6.1 結構修復版 (修正縮排錯誤)"

# --- 設定日誌顯示 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- 設定 matplotlib 後端 ---
matplotlib.use('Agg')

# 全域繪圖鎖
plot_lock = threading.Lock()

app = Flask(__name__)

# --- 1. 設定密鑰 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '0k2eulC1Ewzjg5O0FiLVPH3ShF3RdgzcThaCsTh4vil0FqvsOZ97kw8m6AHhaZ7YVk3nedStFUyQ9hv/6lGD9xc5o+2OC/BGE4Ua3z95PICP1lF6WWTdlXnfRe++hqhPrX6f4rMZ7wjVvMTZrJvXqwdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', 'a6de3f291be03ffe87b72790cad5496a')

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    logger.error("❌ 嚴重錯誤：找不到 LINE 密鑰，請檢查 Render 環境變數設定！")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 準備字型與圖片目錄 ---
static_dir = 'static_images'
if not os.path.exists(static_dir):
    try:
        os.makedirs(static_dir)
    except Exception as e:
        logger.error(f"❌ 無法建立圖片目錄: {e}")

font_file = 'TaipeiSansTCBeta-Regular.ttf'
if not os.path.exists(font_file):
    try:
        import urllib.request
        url = "https://drive.google.com/uc?id=1eGAsTN1HBpJAkeVM57_C7ccp7hbgSz3_&export=download"
        urllib.request.urlretrieve(url, font_file)
    except Exception as e:
        logger.error(f"❌ 字型下載失敗: {e}")

try:
    my_font = FontProperties(fname=font_file)
except:
    logger.warning("⚠️ 字型載入失敗，將使用預設字型")
    my_font = None

# --- 3. 全域快取 ---
EPS_CACHE = {}

def get_eps_cached(ticker_symbol):
    if ticker_symbol in EPS_CACHE: return EPS_CACHE[ticker_symbol]
    try:
        info = yf.Ticker(ticker_symbol).info
        eps = info.get('trailingEps') or info.get('forwardEps')
        if eps is None: eps = 'N/A'
        EPS_CACHE[ticker_symbol] = eps
        return eps
    except Exception as e:
        return 'N/A'

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
            logger.info(f"⏳ 正在抓取 {ticker.ticker} 資料 (嘗試 {i+1}/{retries})...")
            df = ticker.history(period=period)
            if not df.empty:
                logger.info(f"✅ {ticker.ticker} 資料抓取成功，共 {len(df)} 筆")
                return df
            time.sleep(0.5) 
        except Exception as e:
            logger.warning(f"⚠️ {ticker.ticker} 抓取失敗: {e}")
            time.sleep(delay * (i + 1))
    logger.error(f"❌ {ticker.ticker} 多次嘗試後仍失敗")
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

def get_position_sizing(score):
    if score >= 90: return "重倉 (1.5x) 🔥"
    elif score >= 80: return "標準倉 (1.0x) ✅"
    elif score >= 70: return "輕倉 (0.5x) 🛡️"
    else: return "觀望 (0x) 💤"

# --- 7. 繪圖引擎 (v6.1 結構修復版) ---
def create_stock_chart(stock_code):
    gc.collect() # 確保記憶體回收
    
    # 建立一個變數來存放可能的錯誤訊息或結果
    result_file = None
    result_text = ""
    
    # 鎖定以確保單執行緒繪圖
    with plot_lock:
        try:
            # 清理
            plt.close('all')
            plt.clf()
            
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

            if df.empty: 
                return None, "系統繁忙 (Yahoo 限流) 或 找不到該代號資料，請確認代號是否正確。"
            
            stock_name = get_stock_name(target)
            eps = get_eps_cached(target)

            # 抓大盤 RS
            try:
                bench_ticker = yf.Ticker("0050.TW")
                bench_df = fetch_data_with_retry(bench_ticker, period="1y")
            except:
                bench_df = pd.DataFrame()

            # --- 指標計算 ---
            if len(df) < 60:
                logger.warning(f"{target} 資料不足 60 筆，僅計算短期指標")
                df['MA20'] = df['Close'].rolling(window=20).mean()
                df['MA60'] = df['MA20']
            else:
                df['MA20'] = df['Close'].rolling(window=20).mean()
                df['MA60'] = df['Close'].rolling(window=60).mean()
                
            df['MA20_Slope'] = df['MA20'].diff(5)
            
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs_idx = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs_idx))

            df['Vol_MA20'] = df['Volume'].rolling(window=20).mean()
            df['Vol_Ratio'] = df['Volume'] / df['Vol_MA20']

            df['ADX'] = calculate_adx(df)
            df['ATR'] = calculate_atr(df)
            df['OBV'] = calculate_obv(df)

            # RS 計算
            if not bench_df.empty and len(bench_df) > 20:
                common_idx = df.index.intersection(bench_df.index)
                stock_close = df.loc[common_idx, 'Close']
                bench_close = bench_df.loc[common_idx, 'Close']
                stock_ret = stock_close.pct_change(20)
                bench_ret = bench_close.pct_change(20)
                df.loc[common_idx, 'RS'] = (1 + stock_ret) / (1 + bench_ret)
            else:
                df['RS'] = 1.0

            # --- 最新數據 ---
            current_price = df['Close'].iloc[-1]
            ma20 = df['MA20'].iloc[-1]
            ma60 = df['MA60'].iloc[-1]
            
            if pd.isna(ma20): ma20 = current_price
            if pd.isna(ma60): ma60 = current_price
            
            ma20_slope = df['MA20_Slope'].iloc[-1]
            rsi = df['RSI'].iloc[-1] if not pd.isna(df['RSI'].iloc[-1]) else 50
            vol_ratio = df['Vol_Ratio'].iloc[-1] if not pd.isna(df['Vol_Ratio'].iloc[-1]) else 1.0
            adx = df['ADX'].iloc[-1] if not pd.isna(df['ADX'].iloc[-1]) else 0
            atr = df['ATR'].iloc[-1]
            if pd.isna(atr) or atr <= 0: atr = current_price * 0.02
            rs_val = df['RS'].iloc[-1] if 'RS' in df.columns and not pd.isna(df['RS'].iloc[-1]) else 1.0
            
            # --- 策略分析 ---
            if adx < 20: trend_quality = "盤整 (無趨勢) 💤"
            elif adx > 40: trend_quality = "趨勢強勁 (留意回檔) 🔥"
            else: trend_quality = "趨勢確立 ✅"

            slope_val = ma20_slope if not pd.isna(ma20_slope) else 0
            if ma20 > ma60 and slope_val > 0: trend_dir = "多頭"
            elif ma20 < ma60 and slope_val < 0: trend_dir = "空頭"
            else: trend_dir = "震盪"

            if rs_val > 1.05: rs_str = "強於大盤 (資金青睞) 🦅"
            elif rs_val < 0.95: rs_str = "弱於大盤 (遭提款) 🐢"
            else: rs_str = "跟隨大盤"

            atr_stop_loss = current_price - (atr * 1.5)
            
            if trend_dir == "多頭":
                if ma20 < current_price: final_stop = max(atr_stop_loss, ma20)
                else: final_stop = atr_stop_loss
            else:
                final_stop = atr_stop_loss
            
            target_price = current_price + (atr * 3)

            obv_warning = ""
            try:
                if len(df) > 10:
                    price_trend = df['Close'].iloc[-1] > df['Close'].iloc[-10]
                    obv_trend = df['OBV'].iloc[-1] < df['OBV'].iloc[-10]
                    if price_trend and obv_trend: obv_warning = " (⚠️價漲量縮，留意背離)"
            except: pass

            advice = "觀望"
            if trend_dir == "多頭":
                if adx < 20: advice = "盤整股，不符本系統交易條件"
                elif rs_val < 1: advice = "個股趨勢雖好但跑輸大盤，補漲或假突破留意"
                elif vol_ratio > 3: advice = "短線爆量過熱" + obv_warning
                elif rsi < 40: advice = "多頭趨勢回檔中，耐心等量縮止跌"
                elif 60 <= rsi <= 75: advice = "量價健康，趨勢強勁，R值漂亮可佈局"
                elif rsi > 80: advice = "乖離過大，隨時回檔，勿追高"
                else: advice = "沿月線操作，跌破ATR停損出場" + obv_warning
            elif trend_dir == "空頭":
                advice = "趨勢向下，反彈皆是逃命波"
            else:
                advice = "均線糾結，方向未明，多看少做"

            analysis_report = (
                f"📊 {stock_name} ({target}) 實戰診斷\n"
                f"💰 現價: {current_price:.1f} | EPS: {eps}\n"
                f"📈 趨勢: {trend_dir} | {trend_quality}\n"
                f"🦅 RS值: {rs_val:.2f} ({rs_str})\n"
                f"🌊 動能: 量比 {vol_ratio:.1f}\n"
                f"⚡ RSI: {rsi:.1f}\n"
                f"------------------\n"
                f"🎯 目標價: {target_price:.1f} (ATR*3)\n"
                f"🛑 停損點: {final_stop:.1f} (移動停損)\n"
                f"💡 建議: {advice}\n"
                f"(看不懂名詞？輸入「說明」看教學)"
            )
            result_text = analysis_report

            # --- 繪圖 (Agg模式) ---
            logger.info(f"🎨 繪製圖表細節: {target}")
            
            try:
                fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 12), sharex=True, gridspec_kw={'height_ratios': [3, 1, 1]})

                # 使用預設字型以防萬一
                plot_font = my_font if my_font else None

                ax1.plot(df.index, df['Close'], color='black', alpha=0.6, linewidth=1, label='收盤價')
                if len(df) >= 20: ax1.plot(df.index, df['MA20'], color='#FF9900', linestyle='--', label='月線')
                if len(df) >= 60: ax1.plot(df.index, df['MA60'], color='#0066CC', linewidth=2, label='季線')
                
                if len(df) > 60:
                    ax1.plot(golden.index, golden['MA20'], '^', color='red', markersize=14, markeredgecolor='black', label='黃金交叉')
                    ax1.plot(death.index, death['MA20'], 'v', color='green', markersize=14, markeredgecolor='black', label='死亡交叉')
                
                try:
                    ax1.set_title(f"{stock_name} ({target}) 實戰分析圖", fontsize=22, fontproperties=plot_font, fontweight='bold')
                except:
                    ax1.set_title(f"{target} Analysis", fontsize=22)

                ax1.legend(loc='upper left', prop=plot_font)
                ax1.grid(True, linestyle=':', alpha=0.5)

                colors = ['red' if c >= o else 'green' for c, o in zip(df['Close'], df['Open'])]
                ax2.bar(df.index, df['Volume'], color=colors, alpha=0.8)
                ax2.plot(df.index, df['Vol_MA20'], color='blue', linewidth=1.5, label='20日均量')
                ax2.set_ylabel("成交量", fontproperties=plot_font)
                ax2.legend(loc='upper right', prop=plot_font)
                ax2.grid(True, linestyle=':', alpha=0.3)

                ax3.plot(df.index, df['RSI'], color='purple', linewidth=1.5, label='RSI')
                ax3.axhline(80, color='red', linestyle='--', alpha=0.5)
                ax3.axhline(60, color='orange', linestyle='--', alpha=0.5)
                ax3.axhline(30, color='green', linestyle='--', alpha=0.5)
                ax3.set_ylabel("RSI", fontproperties=plot_font)
                ax3.grid(True, linestyle=':', alpha=0.3)
                ax3.set_ylim(0, 100)

                fig.autofmt_xdate()
                
                filename = f"{target.replace('.', '_')}_{int(time.time())}.png"
                filepath = os.path.join(static_dir, filename)
                
                logger.info(f"💾 正在存檔: {filepath}")
                plt.savefig(filepath, bbox_inches='tight')
                logger.info("✅ 存檔完成")
                
                result_file = filename

            except Exception as plot_err:
                logger.error(f"❌ 畫圖子程序失敗: {plot_err}")
                result_file = None
                result_text = f"繪圖失敗 ({str(plot_err)})，但分析正常：\n\n{analysis_report}"
            finally:
                plt.close('all')
                plt.clf()

        except Exception as inner_e:
            logger.error(f"❌ 分析計算過程失敗: {inner_e}")
            return None, f"分析錯誤: {str(inner_e)}"

    # 鎖定結束後回傳結果
    return result_file, result_text

# --- 7. 選股功能 (略，同 v5.4，請保留完整版) ---
# ... (請將 v5.4 的 scan_potential_stocks 複製到這裡) ...
def scan_potential_stocks(max_price=None, sector_name=None):
    logger.info(f"🔎 開始掃描股票: {sector_name or '百元績優'}")
    
    if sector_name == "隨機":
        all_s = set()
        for s in SECTOR_DICT.values():
            for x in s:
                all_s.add(x)
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
        except Exception as e:
            logger.error(f"大盤資料抓取失敗: {e}")
            market_state = 'RANGE'
            weights = WEIGHT_BY_STATE['RANGE']
            stop_mult, target_mult, max_days, state_desc = get_trade_params('RANGE')
            bench_ret = 0

        logger.info(f"正在批量下載 {len(watch_list)} 檔股票資料...")
        data = yf.download(watch_list, period="3mo", progress=False)
        if data.empty: 
            logger.error("❌ 批量下載失敗 (可能是 Yahoo 限流)")
            return title_prefix, [f"系統繁忙 (Yahoo 限流)，請稍後再試。"]

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
        logger.error(f"掃描過程發生錯誤: {traceback.format_exc()}")
        return title_prefix, [f"掃描錯誤: {str(e)}"]

    return title_prefix, recommendations

# --- 8. Line Bot 路由與處理 (v5.6 防彈版) ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    logger.info(f"收到 Webhook 請求: {body[:100]}...") 

    if signature is None:
        logger.error("❌ 錯誤：請求缺少 X-Line-Signature Header")
        abort(400)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("❌ 錯誤：簽章驗證失敗")
        abort(400)
    except Exception as e:
        logger.error(f"❌ Callback 發生未預期錯誤: {traceback.format_exc()}")
        abort(500)
        
    return 'OK'

@app.route("/")
def home(): return f"Stock Bot Running: {APP_VERSION}"

@app.route('/images/<filename>')
def serve_image(filename): return send_from_directory(static_dir, filename)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip() if event.message.text else ""
    logger.info(f"處理使用者訊息: {user_msg}")
    
    try:
        if not user_msg: return

        if user_msg in ["說明", "教學", "名詞解釋", "新手", "看不懂"]:
            tutorial_plus = (
                "🎓 **股市小白 專有名詞懶人包**\n"
                "======================\n\n"
                "⚖️ **倉位建議 (Position Sizing)**\n"
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
        elif user_msg == "百元推薦":
            prefix, res = scan_potential_stocks(max_price=100)
            text = f"📊 {prefix}潛力股\n(Score評分制)\n====================\n" + "\n\n".join(res) if res else "無符合條件個股"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))
        elif user_msg in ["隨機推薦", "隨機", "手氣不錯", "熱門隨機推薦"]:
            prefix, res = scan_potential_stocks(sector_name="隨機")
            text = f"🎲 {prefix}潛力股\n(Score評分制)\n====================\n" + "\n\n".join(res) if res else "運氣不好，沒找到強勢股。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))
        else:
            # 個股診斷 (重點修復區)
            img, txt = create_stock_chart(user_msg)
            if img:
                url = request.host_url.replace("http://", "https://") + 'images/' + img
                line_bot_api.reply_message(event.reply_token, [
                    ImageSendMessage(original_content_url=url, preview_image_url=url),
                    TextSendMessage(text=txt)
                ])
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))
                
    except Exception as e:
        logger.error(f"處理訊息時發生嚴重錯誤: {traceback.format_exc()}")
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="系統發生內部錯誤，請稍後再試。"))
        except:
            logger.error("無法回傳錯誤訊息給使用者")

if __name__ == "__main__":
    app.run()