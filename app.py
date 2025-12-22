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

# --- 設定 matplotlib 後端 (無介面模式) ---
matplotlib.use('Agg')

app = Flask(__name__)

# --- 1. 設定密鑰 (Render 環境變數優先，找不到則使用預設值) ---
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

# --- 3. 定義產業板塊資料庫 (大幅擴充版) ---
# 包含圖片中的熱門分類
SECTOR_DICT = {
    # 電子與科技
    "半導體": ['2330.TW', '2454.TW', '2303.TW', '3711.TW', '3034.TW', '2379.TW', '3443.TW', '3035.TW', '3661.TW'],
    "電子": ['2317.TW', '2382.TW', '3231.TW', '2353.TW', '2357.TW', '2324.TW', '2301.TW', '2356.TW'],
    "光電": ['3008.TW', '3406.TW', '2409.TW', '3481.TW', '6706.TW', '2340.TW'],
    "網通": ['2345.TW', '5388.TWO', '2332.TW', '3704.TW', '3596.TWO', '6285.TW'],
    "電零組": ['2308.TW', '2313.TW', '3037.TW', '2383.TW', '2368.TW', '3044.TW'],
    "電腦週邊": ['2357.TW', '2324.TW', '3231.TW', '2382.TW', '2301.TW', '2376.TW'],
    "資訊服務": ['2471.TW', '3029.TW', '3130.TWO', '6214.TW'],
    
    # 傳產與民生
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
    
    # 金融與生技
    "金融": ['2881.TW', '2882.TW', '2886.TW', '2891.TW', '2892.TW', '2884.TW', '5880.TW', '2880.TW', '2885.TW'],
    "銀行": ['2881.TW', '2882.TW', '2886.TW', '2891.TW', '2892.TW', '2884.TW', '5880.TW'],
    "生技": ['6446.TW', '1795.TW', '4128.TWO', '1760.TW', '4114.TWO', '4743.TWO', '3176.TWO'],
    "化學": ['1722.TW', '1708.TW', '1710.TW', '1717.TW'],

    # 題材與其他
    "軍工": ['2634.TW', '8033.TWO', '5284.TWO', '3005.TW', '8222.TWO'],
    "AI": ['3231.TW', '2382.TW', '6669.TW', '2376.TW', '2356.TW', '3017.TW'],
    "ETN": ['020020.TW', '020019.TW'],
    "ETF": ['0050.TW', '0056.TW', '00878.TW', '00929.TW', '00919.TW', '006208.TW'],

    # 熱門集團股
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
}

# --- 股票代號名稱對照表 (手動維護以確保準確顯示) ---
CODE_NAME_MAP = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '3711': '日月光', '3034': '聯詠', '2379': '瑞昱', '3443': '創意', '3035': '智原', '3661': '世芯',
    '2317': '鴻海', '2382': '廣達', '3231': '緯創', '2353': '宏碁', '2357': '華碩', '2324': '仁寶', '2301': '光寶科', '2356': '英業達',
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
    '6446': '藥華藥', '1795': '美時', '4128': '中天', '1760': '寶齡富錦', '4114': '健喬', '4743': '合一', '3176': '基亞',
    '1722': '台肥', '1708': '東鹼', '1710': '東聯', '1717': '長興',
    '2634': '漢翔', '8033': '雷虎', '5284': 'jpp-KY', '3005': '神基', '8222': '寶一',
    '6669': '緯穎', '3017': '奇鋐',
    '0050': '元大台灣50', '0056': '元大高股息', '00878': '國泰永續', '00929': '復華科優息', '00919': '群益精選', '006208': '富邦台50',
    '5347': '世界', '3374': '精材', '3105': '穩懋',
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
    # 移除 .TW 或 .TWO 取得純代號
    code_only = stock_code.split('.')[0]
    return CODE_NAME_MAP.get(code_only, stock_code)

# --- 4. 核心功能 A: 繪圖引擎 (含 EPS 與長短線建議，支援上市上櫃自動判斷) ---
def create_stock_chart(stock_code):
    try:
        raw_code = stock_code.upper().strip()
        
        # 判斷邏輯：支援自動加上櫃後綴
        # 1. 如果使用者已經輸入 .TW 或 .TWO，直接使用
        if raw_code.endswith('.TW') or raw_code.endswith('.TWO'):
            target = raw_code
            ticker = yf.Ticker(target)
            df = ticker.history(period="1y")
        else:
            # 2. 如果沒輸入，先嘗試上市 .TW
            target = raw_code + ".TW"
            ticker = yf.Ticker(target)
            df = ticker.history(period="1y")
            
            # 3. 如果上市抓不到，改試上櫃 .TWO
            if df.empty:
                target = raw_code + ".TWO"
                ticker = yf.Ticker(target)
                df = ticker.history(period="1y")
        
        if df.empty: return None, "找不到資料或代號錯誤 (請確認該股是否存在)"
        
        # 取得個股中文名稱
        stock_name = get_stock_name(target)

        # 嘗試取得 EPS
        try:
            stock_info = ticker.info
            eps = stock_info.get('trailingEps', None)
            if eps is None:
                eps = stock_info.get('forwardEps', 'N/A')
        except:
            eps = 'N/A'

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

        # --- 生成個股分析報告 ---
        current_price = df['Close'].iloc[-1]
        ma20 = df['MA20'].iloc[-1]
        ma60 = df['MA60'].iloc[-1]
        upper_band = df['Upper'].iloc[-1]
        rsi = df['RSI'].iloc[-1]
        
        # 1. 判斷趨勢
        if ma20 > ma60:
            trend_str = "多頭排列 (強勢) 🔥"
            trend_score = 1
        else:
            trend_str = "空頭排列 (弱勢) ❄️"
            trend_score = -1
            
        # 2. 判斷 RSI 狀態
        if rsi > 75:
            rsi_str = f"{rsi:.1f} (過熱 ⚠️)"
        elif rsi < 25:
            rsi_str = f"{rsi:.1f} (超賣 🟢)"
        else:
            rsi_str = f"{rsi:.1f} (中性)"

        # 3. 計算目標價與停損點
        if trend_score == 1:
            target_price = max(upper_band, current_price * 1.05)
            stop_loss = ma20 if current_price > ma20 else current_price * 0.95
        else:
            target_price = ma60 # 空頭時季線是壓力
            stop_loss = current_price * 0.95

        # 4. 給予長短線建議
        short_term_advice = "觀望"
        if trend_score == 1:
            if rsi < 70: short_term_advice = "沿月線操作，拉回可接"
            else: short_term_advice = "乖離過大，勿追高，防回檔"
        else:
            if rsi < 30: short_term_advice = "有反彈契機，搶短手腳要快"
            else: short_term_advice = "趨勢向下，反彈至月線減碼"

        long_term_advice = "中立"
        eps_val = float(eps) if eps != 'N/A' else 0
        
        if current_price > ma60:
            if eps_val > 0: long_term_advice = "基本面有撐，站穩季線續抱"
            else: long_term_advice = "無獲利支撐，僅視為題材炒作"
        else:
            if eps_val > 0: long_term_advice = "股價委屈，待站回季線轉強"
            else: long_term_advice = "獲利衰退且破季線，避開為妙"

        analysis_report = (
            f"📊 {stock_name} ({target}) 診斷報告\n"
            f"💰 現價: {current_price:.1f} | EPS: {eps}\n"
            f"📈 趨勢: {trend_str}\n"
            f"⚡ RSI: {rsi_str}\n"
            f"------------------\n"
            f"🎯 目標價: {target_price:.1f}\n"
            f"🛑 停損點: {stop_loss:.1f}\n"
            f"💡 短線: {short_term_advice}\n"
            f"🔭 長線: {long_term_advice}"
        )

        # --- 開始繪圖 ---
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 12), sharex=True, gridspec_kw={'height_ratios': [3, 1, 1]})

        # 主圖
        ax1.plot(df.index, df['Close'], color='black', alpha=0.6, linewidth=1, label='收盤價')
        ax1.plot(df.index, df['MA20'], color='#FF9900', linestyle='--', label='月線')
        ax1.plot(df.index, df['MA60'], color='#0066CC', linewidth=2, label='季線')
        ax1.fill_between(df.index, df['Upper'], df['Lower'], color='skyblue', alpha=0.2)
        ax1.plot(golden.index, golden['MA20'], '^', color='red', markersize=14, markeredgecolor='black', label='黃金交叉')
        ax1.plot(death.index, death['MA20'], 'v', color='green', markersize=14, markeredgecolor='black', label='死亡交叉')
        
        # 標題加入中文名稱
        ax1.set_title(f"{stock_name} ({target}) 專業分析圖", fontsize=22, fontproperties=my_font, fontweight='bold')
        
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
        
        filename = f"{target.replace('.', '_')}_{int(time.time())}.png"
        filepath = os.path.join(static_dir, filename)
        plt.savefig(filepath, bbox_inches='tight')
        plt.close()
        
        return filename, analysis_report

    except Exception as e:
        print(f"繪圖錯誤: {e}")
        return None, str(e)

# --- 5. 核心功能 B: 智能選股 (支援多板塊與隨機) ---
def scan_potential_stocks(max_price=None, sector_name=None):
    # 決定要掃描的清單
    if sector_name == "隨機":
        # 收集所有股票
        all_stocks = set()
        for s_list in SECTOR_DICT.values():
            for s in s_list:
                all_stocks.add(s)
        # 隨機抽取 30 檔進行掃描，避免掃描太久
        watch_list = random.sample(list(all_stocks), min(30, len(all_stocks)))
        title_prefix = "【熱門隨機】"
    elif sector_name and sector_name in SECTOR_DICT:
        watch_list = SECTOR_DICT[sector_name]
        title_prefix = f"【{sector_name}股】"
    else:
        # 預設：全市場熱門股
        watch_list = [
            '2330.TW', '2454.TW', '2317.TW', '3008.TW', '6669.TW', 
            '2303.TW', '2353.TW', '2324.TW', '2356.TW', '2409.TW', '3481.TW', 
            '2603.TW', '2609.TW', '2615.TW', '2618.TW', '2610.TW', '2606.TW',
            '2884.TW', '2885.TW', '2886.TW', '2890.TW', '2891.TW', '2892.TW', 
            '2002.TW', '2014.TW', '1605.TW', '1904.TW', '1314.TW',
            '3231.TW', '2382.TW', '2376.TW', '2312.TW', '1101.TW'
        ]
        title_prefix = "【全市場】"

    recommendations = []
    try:
        # 批次下載
        data = yf.download(watch_list, period="3mo")
        for stock in watch_list:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    try: closes = data['Close'][stock]
                    except KeyError: continue
                else:
                    closes = data['Close']
                
                if isinstance(closes, pd.DataFrame):
                    if not closes.empty: closes = closes.iloc[:, 0]
                    else: continue
                
                closes = closes.dropna()
                if len(closes) < 60: continue
                current_price = closes.iloc[-1]
                
                # 價格過濾
                if max_price is not None and current_price > max_price:
                    continue
                
                ma20 = closes.rolling(20).mean().iloc[-1]
                ma60 = closes.rolling(60).mean().iloc[-1]
                std = closes.rolling(20).std().iloc[-1]
                
                # 篩選邏輯：站上月線 且 月線>季線 (多頭)
                if ma20 > ma60 and current_price > ma20:
                    bias = (current_price - ma20) / ma20 * 100
                    if bias < 15: # 放寬一點乖離率
                        stop_loss = ma20 * 0.99
                        upper_band = ma20 + (2 * std)
                        target_price = max(upper_band, current_price * 1.05)
                        
                        # 顯示中文名稱
                        stock_name = get_stock_name(stock)
                        
                        info = (
                            f"📌 {stock_name} ({stock.replace('.TW','').replace('.TWO','')})\n"
                            f"💰 現價: {current_price:.1f}\n"
                            f"🎯 目標: {target_price:.1f}\n"
                            f"🛑 停損: {stop_loss:.1f}"
                        )
                        recommendations.append(info)
            except Exception: continue
    except Exception as e: return [f"掃描錯誤: {str(e)}"]
    
    # 如果是隨機推薦，將結果打亂後回傳
    if sector_name == "隨機":
        random.shuffle(recommendations)

    return title_prefix, recommendations[:6]

# --- 6. Flask 路由設定 ---
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
    
    # ★ 新增功能：功能選單
    if user_msg in ["功能", "指令", "Help", "help", "menu"]:
        menu_text = (
            "🤖 **股市全能助理 功能清單**\n"
            "======================\n\n"
            "🔍 **個股診斷**\n"
            "輸入：`2330` 或 `台積電` (代號)\n"
            "👉 提供線圖、EPS、長短線建議\n\n"
            "📊 **智能選股**\n"
            "輸入：`推薦` 或 `選股`\n"
            "👉 掃描全市場強勢股\n\n"
            "🎲 **隨機靈感**\n"
            "輸入：`隨機推薦` 或 `手氣不錯`\n"
            "👉 隨機挖掘熱門強勢股\n\n"
            "💰 **小資選股**\n"
            "輸入：`百元推薦`\n"
            "👉 掃描 100 元以內的強勢股\n\n"
            "🏭 **產業板塊與集團選股**\n"
            "輸入：`[名稱]推薦`，例如：\n"
            "• `台積電集團推薦`、`鴻海集團推薦`\n"
            "• `長榮集團推薦`、`台塑集團推薦`\n"
            "• `華新集團推薦`、`裕隆集團推薦`\n"
            "• `半導體推薦`、`航運推薦`\n"
            "======================\n"
            "💡 試試看輸入：`隨機推薦`"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=menu_text))
        return

    # 判斷是否為板塊/集團推薦指令
    sector_hit = None
    for sector in SECTOR_DICT.keys():
        # 如果使用者輸入 "電子股推薦" 或 "電子推薦"
        if sector in user_msg and ("推薦" in user_msg or "選股" in user_msg):
            sector_hit = sector
            break
    
    if sector_hit:
        # 執行特定板塊掃描
        title_prefix, results = scan_potential_stocks(max_price=None, sector_name=sector_hit)
        title = f"📊 {title_prefix}潛力股交易計畫"
        
        if results:
            reply_text = f"{title}\n(EPS可能影響長線判斷，請參考個股分析)\n====================\n"
            reply_text += "\n\n".join(results)
            reply_text += "\n====================\n💡 建議：點擊代號可查看EPS與長短線建議。"
        else:
            reply_text = f"目前{sector_hit}板塊無符合強勢條件的個股，建議觀望。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_msg == "百元推薦":
        title_prefix, results = scan_potential_stocks(max_price=100)
        title = "📊 【百元內潛力股交易計畫】"
        if results:
            reply_text = f"{title}\n(小資族首選)\n====================\n"
            reply_text += "\n\n".join(results)
            reply_text += "\n====================\n💡 建議：輸入代號看詳細診斷。"
        else:
            reply_text = "目前無符合條件的百元內潛力股。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_msg in ["隨機推薦", "隨機", "手氣不錯", "熱門隨機推薦"]:
        title_prefix, results = scan_potential_stocks(max_price=None, sector_name="隨機")
        title = "🎲 【熱門隨機潛力股】"
        if results:
            reply_text = f"{title}\n(隨機挖掘強勢股)\n====================\n"
            reply_text += "\n\n".join(results)
            reply_text += "\n====================\n💡 建議：輸入代號看詳細診斷。"
        else:
            reply_text = "運氣不好，這次隨機抽樣沒找到強勢股，請再試一次！"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_msg == "推薦" or user_msg == "選股":
        title_prefix, results = scan_potential_stocks(max_price=None)
        title = "📊 【全市場潛力股交易計畫】"
        if results:
            reply_text = f"{title}\n(包含權值股)\n====================\n"
            reply_text += "\n\n".join(results)
            reply_text += "\n====================\n💡 建議：輸入代號看詳細診斷。"
        else:
            reply_text = "目前市場震盪，無符合條件個股。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    else:
        # 繪圖並取得分析報告
        img_filename, result_content = create_stock_chart(user_msg)
        
        if img_filename:
            root_url = request.host_url.replace("http://", "https://")
            img_url = root_url + 'images/' + img_filename
            
            line_bot_api.reply_message(
                event.reply_token,
                [
                    ImageSendMessage(original_content_url=img_url, preview_image_url=img_url),
                    TextSendMessage(text=result_content)
                ]
            )
        else:
            # 提示使用者可以輸入哪些指令
            help_text = (
                f"找不到代號或指令不明。\n(錯誤: {result_content})\n\n"
                "👉 您可以試試輸入 **「功能」** 查看所有指令！\n\n"
                "或嘗試：\n"
                "1. `2330` (查個股)\n"
                "2. `推薦` (全市場掃描)\n"
                "3. `隨機推薦` (隨機靈感)\n"
                "4. `台積電集團推薦` (集團掃描)"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=help_text)
            )

if __name__ == "__main__":
    app.run()