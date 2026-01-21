import os
import time
import numpy as np
import pandas as pd
import yfinance as yf
# ★ 改用物件導向繪圖
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

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# --- 設定應用程式版本 ---
APP_VERSION = "v7.0 K線價值診斷版 (新增型態辨識+估值模型)"

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

# --- 3. 全域快取 (Info Cache) ---
INFO_CACHE = {} # 儲存 PE, EPS 等基本面資料

def get_stock_info_cached(ticker_symbol):
    """取得股票基本面資訊 (PE, EPS)"""
    if ticker_symbol in INFO_CACHE: return INFO_CACHE[ticker_symbol]
    try:
        info = yf.Ticker(ticker_symbol).info
        # 提取關鍵數據
        data = {
            'eps': info.get('trailingEps') or info.get('forwardEps') or 'N/A',
            'pe': info.get('trailingPE') or info.get('forwardPE') or 'N/A',
            'pb': info.get('priceToBook') or 'N/A'
        }
        INFO_CACHE[ticker_symbol] = data
        return data
    except:
        return {'eps': 'N/A', 'pe': 'N/A', 'pb': 'N/A'}

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
    # ... (其餘板塊資料保留) ...
    "台積電集團": ['2330.TW', '5347.TWO', '3443.TW', '3374.TW', '3661.TW', '3105.TWO'],
    "鴻海集團": ['2317.TW', '2328.TW', '2354.TW', '6414.TW', '5243.TW', '3413.TW', '6451.TW'],
    "半導體": ['2330.TW', '2454.TW', '2303.TW', '3711.TW', '3034.TW', '2379.TW', '3443.TW', '3035.TW', '3661.TW'],
    "航運": ['2603.TW', '2609.TW', '2615.TW', '2618.TW', '2610.TW', '2637.TW', '2606.TW'],
    "AI": ['3231.TW', '2382.TW', '6669.TW', '2376.TW', '2356.TW', '3017.TW'],
    "ETF": ['0050.TW', '0056.TW', '00878.TW', '00929.TW', '00919.TW', '006208.TW'],
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

def fetch_data_with_retry(ticker, period="1y", retries=3, delay=1):
    for i in range(retries):
        try:
            df = ticker.history(period=period)
            if not df.empty: return df
            time.sleep(0.5)
        except Exception: time.sleep(delay * (i + 1))
    return pd.DataFrame()

# --- ★ 新增：K線型態辨識引擎 (v7.0) ---
def detect_kline_pattern(df):
    """
    辨識 K 線型態
    回傳: (Pattern Name, Sentiment Score)
    Score: 1(多), -1(空), 0(無/盤)
    """
    if len(df) < 3: return "資料不足", 0
    
    # 取得最後 3 根 K 棒
    t0 = df.iloc[-1]   # 今天
    t1 = df.iloc[-2]   # 昨天
    t2 = df.iloc[-3]   # 前天

    # 計算實體與影線
    def get_body(row): return abs(row['Close'] - row['Open'])
    def get_upper(row): return row['High'] - max(row['Close'], row['Open'])
    def get_lower(row): return min(row['Close'], row['Open']) - row['Low']
    def is_bull(row): return row['Close'] > row['Open']
    def is_bear(row): return row['Close'] < row['Open']

    body0 = get_body(t0); body1 = get_body(t1)
    avg_body = np.mean([get_body(df.iloc[-i]) for i in range(1, 6)])

    # 1. 吞噬型態 (Engulfing) - 強烈反轉訊號
    if is_bull(t0) and is_bear(t1) and t0['Close'] > t1['Open'] and t0['Open'] < t1['Close']:
        return "多頭吞噬 (強烈買進) 🔥", 1
    if is_bear(t0) and is_bull(t1) and t0['Close'] < t1['Open'] and t0['Open'] > t1['Close']:
        return "空頭吞噬 (強烈賣出) 🌧️", -1

    # 2. 錘頭與流星 (Hammer / Shooting Star)
    if get_lower(t0) > 2 * body0 and get_upper(t0) < body0 * 0.5:
        # 下影線長，實體小
        return "錘頭 (底部反轉/支撐) 🔨", 0.5 
    if get_upper(t0) > 2 * body0 and get_lower(t0) < body0 * 0.5:
        # 上影線長，實體小
        return "流星 (頂部反轉/壓力) ☄️", -0.5

    # 3. 紅三兵 / 黑三兵
    if is_bull(t0) and is_bull(t1) and is_bull(t2):
        if t0['Close']>t1['Close']>t2['Close']: return "紅三兵 (多頭續攻) 💂‍♂️", 0.8
    if is_bear(t0) and is_bear(t1) and is_bear(t2):
        if t0['Close']<t1['Close']<t2['Close']: return "黑三兵 (空頭持續) 🐻", -0.8

    # 4. 貫穿線 / 烏雲蓋頂
    if is_bear(t1) and is_bull(t0) and t0['Open'] < t1['Low'] and t0['Close'] > (t1['Open'] + t1['Close'])/2:
        return "貫穿線 (反彈訊號) 📈", 0.7
    if is_bull(t1) and is_bear(t0) and t0['Open'] > t1['High'] and t0['Close'] < (t1['Open'] + t1['Close'])/2:
        return "烏雲蓋頂 (回檔訊號) 📉", -0.7

    # 5. 十字星
    if body0 < avg_body * 0.1:
        return "十字星 (變盤觀望) ➕", 0

    # 6. 大紅K / 大黑K
    if is_bull(t0) and body0 > avg_body * 2: return "長紅K (強勢) 🟥", 0.6
    if is_bear(t0) and body0 > avg_body * 2: return "長黑K (弱勢) ⬛", -0.6

    return "一般整理", 0

# --- ★ 新增：市場價值評估模型 (v7.0) ---
def get_valuation_status(current_price, ma60, info_data):
    """
    評估市場價值 (結合 PE 與 技術乖離)
    回傳: (Valuation Status string, Score)
    """
    pe = info_data.get('pe', 'N/A')
    
    # 1. 乖離率評估 (Bias)
    bias = (current_price - ma60) / ma60 * 100
    
    tech_val = "合理"
    if bias > 20: tech_val = "過熱 (昂貴)"
    elif bias < -15: tech_val = "超跌 (便宜)"
    elif bias > 10: tech_val = "略貴"
    elif bias < -5: tech_val = "略低"

    # 2. 本益比評估 (PE)
    fund_val = ""
    if pe != 'N/A':
        try:
            pe_val = float(pe)
            if pe_val < 10: fund_val = " | PE低估 (價值股)"
            elif pe_val > 40: fund_val = " | PE高估 (成長股)"
            elif pe_val < 15: fund_val = " | PE合理偏低"
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

WEIGHT_BY_STATE = {
    'TREND': {'trend': 0.6, 'momentum': 0.3, 'risk': 0.1},
    'RANGE': {'trend': 0.4, 'momentum': 0.2, 'risk': 0.4},
    'VOLATILE': {'trend': 0.3, 'momentum': 0.4, 'risk': 0.3}
}

def calculate_score(df_cand, weights):
    # Trend
    score_rs = df_cand['rs_rank'] * 100
    score_ma = np.where(df_cand['ma20'] > df_cand['ma60'], 100, 0)
    score_trend = (score_rs * 0.7) + (score_ma * 0.3)
    
    # Momentum
    slope_pct = (df_cand['slope'] / df_cand['price']).fillna(0)
    score_slope = np.where(slope_pct > 0, (slope_pct * 1000).clip(upper=100), 0)
    vol = df_cand['vol_ratio']
    score_vol = np.exp(-((vol - 2.0) ** 2) / 2.0) * 100
    score_mom = (score_slope * 0.4) + (score_vol * 0.6)
    
    # Risk
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

# --- 7. 繪圖引擎 (v7.0 整合版) ---
def create_stock_chart(stock_code):
    gc.collect()
    result_file, result_text = None, ""
    
    with plot_lock:
        try:
            plt.close('all'); plt.clf()
            
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
            info_data = get_stock_info_cached(target) # 取得基本面快取
            eps = info_data['eps']

            # 抓大盤 RS
            try:
                bench = yf.Ticker("0050.TW").history(period="1y")
                common = df.index.intersection(bench.index)
                if len(common) > 20:
                    s_ret = df.loc[common, 'Close'].pct_change(20)
                    b_ret = bench.loc[common, 'Close'].pct_change(20)
                    df.loc[common, 'RS'] = (1+s_ret)/(1+b_ret)
                else: df['RS'] = 1.0
            except: df['RS'] = 1.0

            # 指標計算
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

            # 最新數據
            last = df.iloc[-1]
            price = last['Close']
            ma20, ma60 = last['MA20'], last['MA60']
            slope = last['Slope'] if not pd.isna(last['Slope']) else 0
            rsi = last['RSI'] if not pd.isna(last['RSI']) else 50
            adx = last['ADX'] if not pd.isna(last['ADX']) else 0
            atr = last['ATR'] if not pd.isna(last['ATR']) and last['ATR'] > 0 else price*0.02
            rs_val = last['RS'] if 'RS' in df.columns and not pd.isna(last['RS']) else 1.0
            vol_ratio = last['Vol_Ratio'] if not pd.isna(last['Vol_Ratio']) else 1.0

            # --- v7.0 新增判斷 ---
            kline_pattern, kline_score = detect_kline_pattern(df)
            valuation_status = get_valuation_status(price, ma60, info_data)

            # 趨勢與策略
            if adx < 20: trend_quality = "盤整 💤"
            elif adx > 40: trend_quality = "趨勢強勁 🔥"
            else: trend_quality = "趨勢確立 ✅"

            if ma20 > ma60 and slope > 0: trend_dir = "多頭"
            elif ma20 < ma60 and slope < 0: trend_dir = "空頭"
            else: trend_dir = "震盪"

            if rs_val > 1.05: rs_str = "強於大盤 🦅"
            elif rs_val < 0.95: rs_str = "弱於大盤 🐢"
            else: rs_str = "跟隨大盤"

            atr_stop_loss = price - atr * 1.5
            final_stop = max(atr_stop_loss, ma20) if trend_dir == "多頭" and ma20 < price else atr_stop_loss
            target_price = price + atr * 3

            obv_warning = ""
            try:
                if len(df) > 10:
                    if df['Close'].iloc[-1] > df['Close'].iloc[-10] and df['OBV'].iloc[-1] < df['OBV'].iloc[-10]:
                        obv_warning = " (⚠️背離)"
            except: pass

            # 綜合建議 (結合型態)
            advice = "觀望"
            if trend_dir == "多頭":
                if kline_score > 0: advice = f"多頭型態出現({kline_pattern})，買進訊號"
                elif adx < 20: advice = "盤整中，等待突破"
                elif rs_val < 1: advice = "趨勢雖好但弱於大盤"
                elif vol_ratio > 3: advice = "短線爆量，小心出貨" + obv_warning
                elif 60 <= rsi <= 75: advice = "量價健康，趨勢強"
                else: advice = "沿月線操作"
            elif trend_dir == "空頭":
                if kline_score > 0.5: advice = "空頭反彈(有底部型態)，搶短手腳要快"
                else: advice = "趨勢向下，勿隨意接刀"
            else:
                if kline_score > 0.5: advice = "震盪中出現轉強訊號，試單"
                else: advice = "多空不明，觀望"

            analysis_report = (
                f"📊 {stock_name} ({target}) 診斷\n"
                f"💰 現價: {price:.1f} | EPS: {eps}\n"
                f"📈 趨勢: {trend_dir} | {trend_quality}\n"
                f"🕯️ K線: {kline_pattern}\n"
                f"💎 價值: {valuation_status}\n"
                f"🦅 RS值: {rs_val:.2f} ({rs_str})\n"
                f"🌊 動能: {vol_ratio:.1f}\n"
                f"------------------\n"
                f"🎯 目標: {target_price:.1f} | 🛑 停損: {final_stop:.1f}\n"
                f"💡 建議: {advice}\n"
                f"(輸入「說明」看名詞解釋)"
            )

            # OO 繪圖
            fig = Figure(figsize=(10, 10))
            canvas = FigureCanvas(fig)
            
            ax1 = fig.add_subplot(3, 1, 1)
            ax1.plot(df.index, df['Close'], color='black', alpha=0.6, label='Price')
            if len(df) >= 20: ax1.plot(df.index, df['MA20'], color='#FF9900', linestyle='--', label='MA20')
            if len(df) >= 60: ax1.plot(df.index, df['MA60'], color='#0066CC', linewidth=2, label='MA60')
            
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
            
            del fig; del canvas

        except Exception as e:
            return None, f"繪圖失敗: {str(e)}\n\n{result_text}"
        finally:
            gc.collect()

    return result_file, result_text

# --- 8. 選股功能 (同 v5.4，略) ---
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
                pos = get_position_sizing(r.total_score)
                icon = icons[i] if i < 6 else "🔹"
                
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
    
    if msg in ["說明", "教學", "名詞解釋", "新手", "看不懂"]:
        txt = (
            "🎓 **股市小白 專有名詞懶人包**\n"
            "======================\n\n"
            "🕯️ **K線型態**\n"
            "• 🔨 錘頭: 底部反轉訊號，有長下影線。\n"
            "• 🔥 吞噬: 強力反轉，今日K線吃掉昨日。\n\n"
            "💎 **市場價值**\n"
            "• 根據乖離率與本益比，判斷現在是便宜還是貴。\n\n"
            "⚖️ **倉位建議**\n"
            "• 🔥 重倉 (1.5x): 分數>90，勝率極高。\n"
            "• ✅ 標準倉 (1.0x): 分數>80，正常買進。\n"
            "• 🛡️ 輕倉 (0.5x): 分數>70，嘗試性建倉。\n\n"
            "🏆 **Score (綜合評分)**\n"
            "• 滿分100，越高越好。\n\n"
            "🦅 **RS Rank (相對強弱)**\n"
            "• Top 10%: 代表打敗市場90%的股票。"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=txt))
        return

    if msg in ["功能", "指令", "Help", "help", "menu"]:
        menu = (
            f"🤖 **股市全能助理** ({APP_VERSION})\n"
            "======================\n\n"
            "🔍 **個股診斷**\n"
            "輸入：`2330` 或 `8069`\n"
            "👉 K線型態、市場價值、EPS、建議\n\n"
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