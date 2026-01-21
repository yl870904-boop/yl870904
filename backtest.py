import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import itertools
import warnings

# 忽略 pandas 的一些運算警告
warnings.filterwarnings('ignore')

# ==========================================
# 1. 核心計算引擎
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
    
    # ADX (14)
    up = df['High'].diff()
    down = -df['Low'].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_sum = tr.rolling(14).sum().replace(0, 1)
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
    if bench_df.empty: return 'RANGE'
    last = bench_df.iloc[-1]
    if last['MA20'] > last['MA60'] and last['ADX'] > 25: return 'TREND'
    elif (last['ATR'] / last['Close']) < 0.012: return 'RANGE'
    else: return 'VOLATILE'

def calculate_score_v5_2(row, weights):
    # Trend
    score_rs = row['rs_rank'] * 100
    score_ma = 100 if row['ma20'] > row['ma60'] else 0
    score_trend = (score_rs * 0.7) + (score_ma * 0.3)
    
    # Momentum
    slope_pct = (row['slope'] / row['price']) if row['price'] > 0 else 0
    score_slope = min(max(slope_pct * 1000, 0), 100)
    vol = row['vol_ratio']
    score_vol = np.exp(-((vol - 2.0) ** 2) / 2.0) * 100
    score_mom = (score_slope * 0.4) + (score_vol * 0.6)
    
    # Risk
    atr_pct = row['atr'] / row['price'] if row['price'] > 0 else 0.03
    dist = abs(atr_pct - 0.03)
    score_risk = max(100 - (dist * 100 * 20), 0)
    
    total = (
        score_trend * weights['trend'] +
        score_mom * weights['momentum'] +
        score_risk * weights['risk']
    )
    return total

def calculate_position_size(score):
    """
    v5.4 資金管理核心：動態部位規模 (Position Sizing)
    Score 越高，下注越大
    """
    if score < 60: return 0.0
    # 60分=0.5倍(試單), 80分=1.0倍(標準), 100分=1.5倍(重倉)
    size = 0.5 + (score - 60) * (1.0 / 40.0) 
    return round(size, 2)

# ==========================================
# 2. 視覺化模組 (含 Equity Curve)
# ==========================================

def plot_full_analysis(df_res, equity_curve):
    """繪製全套分析圖表"""
    if df_res.empty: return

    plt.figure(figsize=(16, 12))

    # 1. Equity Curve (資金曲線)
    plt.subplot(3, 1, 1)
    plt.plot(equity_curve.index, equity_curve['Equity'], label='Strategy Equity', color='blue', linewidth=2)
    plt.title(f"Equity Curve (Final: {equity_curve['Equity'].iloc[-1]:.2f})")
    plt.grid(True, alpha=0.3)
    plt.legend()

    # 2. Drawdown (回撤圖)
    plt.subplot(3, 1, 2)
    plt.fill_between(equity_curve.index, equity_curve['Drawdown'], 0, color='red', alpha=0.3, label='Drawdown')
    plt.title(f"Max Drawdown: {equity_curve['Drawdown'].min()*100:.2f}%")
    plt.grid(True, alpha=0.3)
    plt.legend()

    # 3. Score vs ROI 散佈圖
    plt.subplot(3, 2, 5)
    plt.scatter(df_res['Score'], df_res['ROI'] * 100, alpha=0.6, c=df_res['Size'], cmap='viridis')
    plt.colorbar(label='Position Size')
    plt.axhline(0, color='red', linestyle='--')
    plt.xlabel('Score')
    plt.ylabel('Return (%)')
    plt.title('Score vs ROI (Color=Size)')
    plt.grid(True, alpha=0.3)

    # 4. Score 分桶績效
    plt.subplot(3, 2, 6)
    bins = [0, 60, 70, 80, 90, 100]
    labels = ['<60', '60-70', '70-80', '80-90', '90+']
    df_res['score_bin'] = pd.cut(df_res['Score'], bins=bins, labels=labels)
    grp = df_res.groupby('score_bin')['ROI'].mean() * 100
    grp.plot(kind='bar', color=['gray' if x < 0 else 'red' for x in grp.values], alpha=0.7)
    plt.title('Avg Return by Score Bucket')
    plt.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    plt.show()

# ==========================================
# 3. 回測引擎
# ==========================================

WATCH_LIST = [
    '2330.TW', '2317.TW', '2454.TW', '2303.TW', '2603.TW', '2881.TW', '1605.TW', '2382.TW', '3231.TW', '2376.TW',
    '3037.TW', '2356.TW', '2324.TW', '3481.TW', '2609.TW', '2002.TW', '2882.TW', '2891.TW', '5880.TW', '2357.TW',
    '0050.TW'
]

def simulate_trade_v5_3(entry_price, df_future, atr, state):
    if state == 'TREND': stop_mult, target_mult, max_days = 1.5, 3.5, 30
    elif state == 'RANGE': stop_mult, target_mult, max_days = 1.0, 1.5, 10
    else: stop_mult, target_mult, max_days = 2.0, 2.0, 5

    stop_loss = entry_price - (atr * stop_mult)
    target = entry_price + (atr * target_mult)
    df_future = df_future.iloc[:max_days]

    for date, row in df_future.iterrows():
        if row['Low'] <= stop_loss:
            return (stop_loss - entry_price) / entry_price, 'STOP', date
        if row['High'] >= target:
            return (target - entry_price) / entry_price, 'TARGET', date
            
    final_price = df_future.iloc[-1]['Close']
    return (final_price - entry_price) / entry_price, 'TIME', df_future.index[-1]

def run_strategy(data, bench, weights_config):
    """執行一次完整策略回測"""
    trades = []
    # 起始日 (避開指標計算期)
    valid_dates = data.index[60:-35]
    
    for date in valid_dates:
        # 1. 狀態判斷
        current_bench = bench.loc[:date]
        market_state = detect_market_state(current_bench)
        # 根據狀態取權重，如果 config 沒有該狀態則用預設
        weights = weights_config.get(market_state, {'trend':0.6, 'momentum':0.3, 'risk':0.1})
        
        candidates = []
        bench_ret = current_bench['Close'].pct_change(20).iloc[-1]
        
        # 2. 掃描
        for stock in WATCH_LIST:
            try:
                stock_hist = data.xs(stock, axis=1, level=1).loc[:date]
                if len(stock_hist) < 60: continue
                
                stock_hist = calculate_indicators(stock_hist)
                last = stock_hist.iloc[-1]
                
                # 初篩
                if last['MA20'] > last['MA60'] and last['Slope'] > 0:
                    stock_ret = stock_hist['Close'].pct_change(20).iloc[-1]
                    rs_raw = (1 + stock_ret) / (1 + bench_ret)
                    
                    candidates.append({
                        'stock': stock, 'price': last['Close'], 'atr': last['ATR'],
                        'ma20': last['MA20'], 'ma60': last['MA60'], 'slope': last['Slope'],
                        'vol_ratio': last['Vol_Ratio'], 'rs_raw': rs_raw
                    })
            except: continue
            
        # 3. 評分與下單
        if candidates:
            df_cand = pd.DataFrame(candidates)
            df_cand['rs_rank'] = df_cand['rs_raw'].rank(pct=True)
            df_cand['score'] = df_cand.apply(lambda row: calculate_score_v5_2(row, weights), axis=1)
            
            # 取第一名且分數夠高
            top_pick = df_cand.sort_values('score', ascending=False).iloc[0]
            
            # v5.4 動態部位規模
            pos_size = calculate_position_size(top_pick['score'])
            
            if pos_size > 0: # 有下注才交易
                future_data = data.xs(top_pick['stock'], axis=1, level=1).loc[date:].iloc[1:32]
                if not future_data.empty:
                    roi, reason, exit_date = simulate_trade_v5_3(
                        top_pick['price'], future_data, top_pick['atr'], market_state
                    )
                    # 紀錄交易結果 (ROI 乘上部位規模 = 實際對帳戶影響)
                    # 假設每次只持有一檔，全倉的 pos_size 倍
                    actual_return = roi * pos_size 
                    
                    trades.append({
                        'Exit Date': exit_date,
                        'Return': actual_return, # 實際損益%
                        'Raw ROI': roi,
                        'Score': int(top_pick['score']),
                        'Size': pos_size,
                        'State': market_state
                    })
    
    return pd.DataFrame(trades)

# ==========================================
# 4. Grid Search (網格搜索)
# ==========================================

def run_grid_search():
    print("🚀 啟動 v5.4 Grid Search 自動參數優化...")
    
    # 下載數據 (一次性)
    print("📥 下載歷史資料 (6個月)...")
    data = yf.download(WATCH_LIST, period="6mo", progress=False)
    bench = yf.Ticker("0050.TW").history(period="6mo")
    bench = calculate_indicators(bench)
    
    # 定義要測試的權重組合 (Trend, Momentum, Risk)
    # 限制總和為 1.0
    weight_combinations = []
    for t in [0.4, 0.5, 0.6, 0.7]:
        for m in [0.2, 0.3, 0.4]:
            r = round(1.0 - t - m, 1)
            if r >= 0.1:
                weight_combinations.append({'trend': t, 'momentum': m, 'risk': r})
    
    print(f"🧪 總共測試 {len(weight_combinations)} 組權重組合...")
    
    best_perf = -999
    best_weights = None
    best_trades = None
    
    results_log = []

    for w in weight_combinations:
        # 這裡簡化：假設所有市場狀態都用同一組權重來測試基準體質
        # 實務上可以針對 TREND/RANGE 分別優化
        config = {'TREND': w, 'RANGE': w, 'VOLATILE': w}
        
        trades_df = run_strategy(data, bench, config)
        
        if not trades_df.empty:
            avg_ret = trades_df['Return'].mean()
            win_rate = (trades_df['Return'] > 0).mean()
            # 績效指標：簡單用 平均報酬 * 勝率
            score = avg_ret * win_rate * 100
            
            results_log.append({
                'Weights': str(w),
                'Win Rate': win_rate,
                'Avg Return': avg_ret,
                'Perf Score': score
            })
            
            if score > best_perf:
                best_perf = score
                best_weights = w
                best_trades = trades_df
    
    # 輸出最佳結果
    res_df = pd.DataFrame(results_log).sort_values('Perf Score', ascending=False)
    print("\n🏆 === Grid Search 結果 (Top 3) ===")
    print(res_df.head(3))
    print(f"\n✅ 最佳權重: {best_weights}")
    
    # 繪製最佳策略的 Equity Curve
    if best_trades is not None:
        print("\n📈 繪製最佳策略資金曲線...")
        # 整理資金曲線
        best_trades = best_trades.sort_values('Exit Date')
        # 簡單模擬：假設本金 1.0，每次交易複利 (這裡用單利累加示範 Equity Curve 形狀)
        best_trades['Equity'] = 1 + best_trades['Return'].cumsum()
        best_trades['RollingMax'] = best_trades['Equity'].cummax()
        best_trades['Drawdown'] = (best_trades['Equity'] - best_trades['RollingMax']) / best_trades['RollingMax']
        
        # 將索引設為日期以便繪圖
        equity_curve = best_trades.set_index('Exit Date')[['Equity', 'Drawdown']]
        
        plot_full_analysis(best_trades, equity_curve)

if __name__ == "__main__":
    run_grid_search()