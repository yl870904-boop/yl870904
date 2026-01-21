import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import warnings

# 忽略 pandas 的一些運算警告
warnings.filterwarnings('ignore')

# ==========================================
# 1. 核心計算引擎 (與 v5.3 app.py 邏輯同步)
# ==========================================

def calculate_indicators(df):
    """計算所有技術指標"""
    df = df.copy()
    # 均線
    df['MA20'] = df['Close'].rolling(window=20).mean()
    df['MA60'] = df['Close'].rolling(window=60).mean()
    df['Slope'] = df['MA20'].diff(5)
    
    # ATR (14)
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    
    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # ADX (14) - 修正版標準算法
    up = df['High'].diff()
    down = -df['Low'].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_sum = tr.rolling(14).sum()
    
    # 避免除以零
    tr_sum = tr_sum.replace(0, 1)
    
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).sum() / tr_sum)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).sum() / tr_sum)
    
    sum_di = abs(plus_di + minus_di).replace(0, 1)
    dx = (abs(plus_di - minus_di) / sum_di) * 100
    df['ADX'] = dx.rolling(14).mean()
    
    # 量能結構
    df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    df['Vol_Ratio'] = df['Volume'] / df['Vol_MA20']
    
    return df

def detect_market_state(bench_df):
    """
    偵測市場狀態 (v5.3 核心)
    回傳: 'TREND' (趨勢), 'RANGE' (盤整), 'VOLATILE' (劇烈波動)
    """
    if bench_df.empty: return 'RANGE'
    
    last = bench_df.iloc[-1]
    ma20 = last['MA20']
    ma60 = last['MA60']
    adx = last['ADX']
    atr_pct = last['ATR'] / last['Close']
    
    if ma20 > ma60 and adx > 25:
        return 'TREND'
    elif atr_pct < 0.012: # 波動極低
        return 'RANGE'
    else:
        return 'VOLATILE' # 其他情況視為震盪/波動

def calculate_score_v5_2(row, weights):
    """
    v5.2 精準評分公式
    包含：鐘形量能獎勵、連續風險扣分
    """
    # 1. 趨勢分 Trend (線性)
    # RS Rank (0~1) * 100
    score_rs = row['rs_rank'] * 100
    # MA 結構 (0 or 100)
    score_ma = 100 if row['ma20'] > row['ma60'] else 0
    score_trend = (score_rs * 0.7) + (score_ma * 0.3)
    
    # 2. 動能分 Momentum (鐘形優化)
    # 斜率: 正斜率給分
    slope_pct = (row['slope'] / row['price']) if row['price'] > 0 else 0
    score_slope = min(max(slope_pct * 1000, 0), 100)
    
    # 量能: 使用鐘形曲線 (Bell Curve)，獎勵 1.5~2.5 倍，過熱(>4)扣分
    # 這裡用一個簡化的高斯函數模擬
    vol = row['vol_ratio']
    # 在 2.0 處達到峰值 100，超過 3.5 開始快速下降
    score_vol = np.exp(-((vol - 2.0) ** 2) / 2.0) * 100
    score_mom = (score_slope * 0.4) + (score_vol * 0.6)
    
    # 3. 風控分 Risk (連續性優化)
    # ATR% 越接近 3% 越好，太小(死魚)或太大(妖股)都扣分
    atr_pct = row['atr'] / row['price'] if row['price'] > 0 else 0.03
    # 理想值 0.03 (3%)，每偏離 1% 扣 20分
    dist = abs(atr_pct - 0.03)
    score_risk = max(100 - (dist * 100 * 20), 0)
    
    # 總分加權
    total = (
        score_trend * weights['trend'] +
        score_mom * weights['momentum'] +
        score_risk * weights['risk']
    )
    return total

# ==========================================
# 2. 視覺化模組
# ==========================================

def plot_analysis(df_res):
    """繪製三大關鍵驗證圖表"""
    if df_res.empty: return

    # 設定畫布
    plt.figure(figsize=(18, 5))

    # 1. Score vs ROI 散佈圖
    plt.subplot(1, 3, 1)
    plt.scatter(df_res['Score'], df_res['ROI'] * 100, alpha=0.6, c='blue')
    plt.axhline(0, color='red', linestyle='--')
    plt.xlabel('Score')
    plt.ylabel('Return (%)')
    plt.title('Score vs ROI (有效性驗證)')
    plt.grid(True, alpha=0.3)

    # 2. Score 分桶績效 (Bar Chart)
    plt.subplot(1, 3, 2)
    # 分桶
    bins = [0, 60, 70, 80, 90, 100]
    labels = ['<60', '60-70', '70-80', '80-90', '90+']
    df_res['score_bin'] = pd.cut(df_res['Score'], bins=bins, labels=labels)
    
    # 計算各組平均報酬
    grp = df_res.groupby('score_bin')['ROI'].mean() * 100
    colors = ['gray' if x < 0 else 'red' for x in grp.values]
    grp.plot(kind='bar', color=colors, alpha=0.7)
    plt.axhline(0, color='black', linewidth=0.8)
    plt.title('Avg Return by Score Bucket')
    plt.ylabel('Avg Return (%)')
    plt.grid(axis='y', alpha=0.3)

    # 3. 分市場狀態表現
    plt.subplot(1, 3, 3)
    states = df_res['State'].unique()
    for state in states:
        subset = df_res[df_res['State'] == state]
        plt.scatter(subset['Score'], subset['ROI'] * 100, label=state, alpha=0.6)
    
    plt.axhline(0, color='red', linestyle='--')
    plt.xlabel('Score')
    plt.ylabel('Return (%)')
    plt.title('Score vs Return by Market State')
    plt.legend()
    plt.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

# ==========================================
# 3. 回測執行模組 (v5.3 策略切換)
# ==========================================

# 權重設定 (依據市場狀態)
WEIGHT_BY_STATE = {
    'TREND':     {'trend': 0.6, 'momentum': 0.3, 'risk': 0.1}, # 趨勢盤：重順勢
    'RANGE':     {'trend': 0.4, 'momentum': 0.2, 'risk': 0.4}, # 盤整盤：重風控
    'VOLATILE':  {'trend': 0.3, 'momentum': 0.4, 'risk': 0.3}  # 波動盤：重短線動能
}

# 測試名單 (50檔績優股 + 指數 ETF)
WATCH_LIST = [
    '2330.TW', '2317.TW', '2454.TW', '2303.TW', '2603.TW', '2881.TW', '1605.TW', '2382.TW', '3231.TW', '2376.TW',
    '3037.TW', '2356.TW', '2324.TW', '3481.TW', '2609.TW', '2002.TW', '2882.TW', '2891.TW', '5880.TW', '2357.TW',
    '2308.TW', '3008.TW', '1101.TW', '2886.TW', '2892.TW', '2884.TW', '2885.TW', '1301.TW', '1303.TW', '2002.TW',
    '0050.TW', '0056.TW', '00878.TW'
]

def simulate_trade_v5_3(entry_price, entry_date, df_future, atr, state):
    """
    v5.3 策略切換核心
    根據市場狀態決定 Stop / Target / Holding Days
    """
    # --- 策略參數表 ---
    if state == 'TREND':
        stop_mult, target_mult, max_days = 1.5, 3.5, 30
    elif state == 'RANGE':
        stop_mult, target_mult, max_days = 1.0, 1.5, 10
    else:  # VOLATILE
        stop_mult, target_mult, max_days = 2.0, 2.0, 5

    stop_loss = entry_price - (atr * stop_mult)
    target = entry_price + (atr * target_mult)

    # 截取最大持有天數
    df_future = df_future.iloc[:max_days]

    for date, row in df_future.iterrows():
        # 停損
        if row['Low'] <= stop_loss:
            return (stop_loss - entry_price) / entry_price, 'STOP', date, (date - df_future.index[0]).days
        # 停利
        if row['High'] >= target:
            return (target - entry_price) / entry_price, 'TARGET', date, (date - df_future.index[0]).days
            
    # 時間到期，強制平倉
    final_price = df_future.iloc[-1]['Close']
    return (final_price - entry_price) / entry_price, 'TIME', df_future.index[-1], max_days

def run_backtest():
    print("🚀 啟動 v5.3 策略回測實驗 (含視覺化)...")
    print("📥 下載歷史資料 (12個月)...")
    
    # 抓長一點 (12個月) 以驗證不同市場週期
    data = yf.download(WATCH_LIST, period="1y", progress=False)
    bench = yf.Ticker("0050.TW").history(period="1y")
    bench = calculate_indicators(bench)
    
    trades = []
    
    # 開始回測 (從第 60 天開始)
    valid_dates = data.index[60:-35] # 留 35 天給未來模擬
    print(f"📅 回測區間: {valid_dates[0].date()} ~ {valid_dates[-1].date()}")
    print("🔄 逐日模擬交易中 (請稍候)...")

    for date in valid_dates:
        # 1. 判斷當日市場狀態
        current_bench = bench.loc[:date]
        market_state = detect_market_state(current_bench)
        weights = WEIGHT_BY_STATE[market_state]
        
        # 2. 掃描當日個股
        candidates = []
        bench_ret = current_bench['Close'].pct_change(20).iloc[-1]
        
        for stock in WATCH_LIST:
            try:
                # 取得該股歷史數據 (截至當日)
                stock_hist = data.xs(stock, axis=1, level=1).loc[:date]
                if len(stock_hist) < 60: continue
                
                # 計算當下指標
                stock_hist = calculate_indicators(stock_hist)
                last = stock_hist.iloc[-1]
                
                stock_ret = stock_hist['Close'].pct_change(20).iloc[-1]
                rs_raw = (1 + stock_ret) / (1 + bench_ret)
                
                # 初步篩選 (均線多頭 + 有量)
                if last['MA20'] > last['MA60'] and last['Slope'] > 0 and last['Vol_Ratio'] > 0.8:
                    candidates.append({
                        'stock': stock,
                        'price': last['Close'],
                        'atr': last['ATR'],
                        'ma20': last['MA20'],
                        'ma60': last['MA60'],
                        'slope': last['Slope'],
                        'vol_ratio': last['Vol_Ratio'],
                        'rs_raw': rs_raw
                    })
            except: continue
            
        # 3. 計算分數與排名
        if candidates:
            df_cand = pd.DataFrame(candidates)
            df_cand['rs_rank'] = df_cand['rs_raw'].rank(pct=True)
            
            # 套用 v5.2 評分邏輯
            df_cand['score'] = df_cand.apply(lambda row: calculate_score_v5_2(row, weights), axis=1)
            
            # 4. 模擬進場 (只買當天第一名，且分數 > 70)
            top_pick = df_cand.sort_values('score', ascending=False).iloc[0]
            
            if top_pick['score'] >= 70:
                # v5.3 策略切換模擬
                future_data = data.xs(top_pick['stock'], axis=1, level=1).loc[date:].iloc[1:32] # 抓夠長以符合 TREND 策略
                if not future_data.empty:
                    roi, reason, exit_date, days = simulate_trade_v5_3(
                        top_pick['price'], 
                        date,
                        future_data, 
                        top_pick['atr'], 
                        market_state
                    )
                    trades.append({
                        'Date': date,
                        'Stock': top_pick['stock'],
                        'State': market_state,
                        'Score': int(top_pick['score']),
                        'Result': reason,
                        'ROI': roi,
                        'Days': days
                    })

    # 輸出結果與圖表
    if trades:
        df_res = pd.DataFrame(trades)
        print("\n🏆 === 回測績效報告 ===")
        print(f"總交易次數: {len(df_res)}")
        print(f"勝率: {(df_res['ROI'] > 0).mean() * 100:.1f}%")
        print(f"平均報酬: {df_res['ROI'].mean() * 100:.2f}%")
        print(f"總報酬 (單利): {df_res['ROI'].sum() * 100:.2f}%")
        print("\n📊 各市場狀態表現 (平均報酬):")
        print(df_res.groupby('State')['ROI'].mean() * 100)
        
        # 呼叫繪圖
        print("\n📉 正在繪製分析圖表...")
        plot_analysis(df_res)
    else:
        print("無符合條件的交易")

if __name__ == "__main__":
    run_backtest()