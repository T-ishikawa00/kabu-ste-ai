# -*- coding: utf-8 -*-
"""
step2_screening.py v6.1 (Ghost Buster + Point System)
=====================================================
v5.1の高度なポイント分析ロジックに、
v6.0の「上場廃止銘柄除外（鮮度チェック）」を追加した完全版。

【機能】
1. データの最終更新日が古い（5日以上前）銘柄を自動除外
2. RSI, 乖離率, ADX, 出来高倍率などを総合評価
"""

import os
import glob
import pandas as pd
import numpy as np
import warnings
from concurrent.futures import ProcessPoolExecutor

warnings.filterwarnings('ignore')

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data_archive_daily"
OUTPUT_FILE = "target_list.csv"
TARGET_COUNT = 10

# ★追加: データの鮮度チェック（何日以上前なら除外するか）
MAX_DAYS_OLD = 5  

ABSOLUTE_NG = {
    "max_rsi": 80,
    "max_kairi": 20,
    "max_change": 10,
}

BASE_PARAMS = {
    "min_price": 150,
    "max_price": 5000,
    "min_trading_val": 300_000_000,
    "min_days": 30
}


def convert_code(code_str):
    """5桁コードを4桁に変換"""
    if len(code_str) == 5 and code_str.endswith('0'):
        return code_str[:-1]
    return code_str


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calc_adx(high, low, close, period=14):
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
    
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(period).mean()
    
    return adx


def analyze_stock(file_path):
    """ポイント制で銘柄を評価"""
    try:
        # カラム名を柔軟に処理
        df = pd.read_csv(file_path)
        
        if len(df) < BASE_PARAMS["min_days"]:
            return None
        
        # カラム名の正規化（大文字小文字対応）
        df.columns = df.columns.str.lower()
        
        # 必要なカラムを探す
        if 'datetime' in df.columns:
            date_col = 'datetime'
        elif 'date' in df.columns:
            date_col = 'date'
        else:
            return None
        
        # 調整後価格があればそちらを使う
        if 'adjc' in df.columns:
            close_col = 'adjc'
            open_col = 'adjo' if 'adjo' in df.columns else 'open'
            high_col = 'adjh' if 'adjh' in df.columns else 'high'
            low_col = 'adjl' if 'adjl' in df.columns else 'low'
            vol_col = 'adjvo' if 'adjvo' in df.columns else 'volume'
        else:
            close_col = 'close'
            open_col = 'open'
            high_col = 'high'
            low_col = 'low'
            vol_col = 'volume'
        
        required = [close_col, open_col, high_col, low_col, vol_col]
        for col in required:
            if col not in df.columns:
                return None
        
        df[date_col] = pd.to_datetime(df[date_col])
        df.sort_values(date_col, inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        # ==========================================
        # ★追加: データの鮮度チェック（ゴーストバスター）
        # ==========================================
        last_date = df[date_col].iloc[-1]
        today = pd.Timestamp.now()
        delta_days = (today - last_date).days
        
        # 「最後のデータが指定日数以上前」なら上場廃止かデータ切れなので除外
        if delta_days > MAX_DAYS_OLD:
            # print(f"👻 除外(古い): 最終更新 {last_date.date()}")
            return None

        # ここからいつもの分析
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # コード取得（5桁→4桁変換）
        code = os.path.basename(file_path).replace("daily_", "").replace(".csv", "")
        code = convert_code(code)
        
        if len(code) != 4 or not code.isdigit():
            return None
        
        close = float(latest[close_col])
        open_price = float(latest[open_col])
        high = float(latest[high_col])
        low = float(latest[low_col])
        volume = float(latest[vol_col])
        
        if pd.isna(close) or pd.isna(volume) or close == 0 or volume == 0:
            return None
        
        # 基本フィルタ
        if not (BASE_PARAMS["min_price"] <= close <= BASE_PARAMS["max_price"]):
            return None
        
        trading_value = close * volume
        if trading_value < BASE_PARAMS["min_trading_val"]:
            return None
        
        # 絶対NG条件
        df['rsi'] = calc_rsi(df[close_col], 14)
        rsi = df['rsi'].iloc[-1]
        if pd.isna(rsi) or rsi > ABSOLUTE_NG["max_rsi"]:
            return None
        
        df['ma25'] = df[close_col].rolling(25).mean()
        ma25 = df['ma25'].iloc[-1]
        if pd.isna(ma25):
            return None
        
        kairi = (close - ma25) / ma25 * 100
        if kairi > ABSOLUTE_NG["max_kairi"]:
            return None
        
        prev_close = float(prev[close_col])
        if pd.isna(prev_close) or prev_close == 0:
            return None
        change_pct = (close - prev_close) / prev_close * 100
        if change_pct > ABSOLUTE_NG["max_change"]:
            return None
        
        # ==========================================
        # ポイント制評価
        # ==========================================
        points = 0
        
        # RSI
        if 50 <= rsi <= 60:
            points += 20
        elif 40 <= rsi <= 70:
            points += 10
        elif rsi < 40:
            points += 5
        elif rsi > 70:
            points -= 10
        
        # 乖離率
        if 0 <= kairi <= 5:
            points += 15
        elif 5 < kairi <= 10:
            points += 5
        elif kairi > 10:
            points -= 10
        elif kairi < 0:
            points -= 5
        
        # トレンド
        if close > ma25:
            points += 15
        else:
            points -= 10
        
        # 変化率
        if 0.5 <= change_pct <= 3:
            points += 15
        elif 0 < change_pct < 0.5:
            points += 5
        elif 3 < change_pct <= 5:
            points += 0
        elif change_pct > 5:
            points -= 10
        elif change_pct < 0:
            points -= 5
        
        # 出来高
        recent_vol = df[vol_col].iloc[-6:-1].mean()
        if recent_vol > 0:
            vol_ratio = volume / recent_vol
            if vol_ratio >= 1.5:
                points += 15
            elif vol_ratio >= 1.2:
                points += 10
            elif vol_ratio >= 1.0:
                points += 5
            else:
                points -= 5
        else:
            vol_ratio = 1.0
        
        # ADX (本格計算)
        df['adx'] = calc_adx(df[high_col], df[low_col], df[close_col], 14)
        adx = df['adx'].iloc[-1]
        if pd.isna(adx):
            adx = 0
        
        if adx >= 30:
            points += 15
        elif adx >= 20:
            points += 10
        elif adx >= 15:
            points += 5
        
        # 陽線/陰線
        if close > open_price:
            points += 10
        else:
            points -= 5
        
        # 上ヒゲ
        if high > low:
            upper_shadow = high - max(close, open_price)
            total_range = high - low
            upper_ratio = upper_shadow / total_range
            if upper_ratio > 0.3:
                points -= 10
            elif upper_ratio > 0.2:
                points -= 5
        
        # 売買代金ボーナス
        tv_oku = trading_value / 100_000_000
        if tv_oku >= 10:
            points += 10
        elif tv_oku >= 5:
            points += 5
        
        # 最終スコア
        score = points * np.log10(trading_value)
        
        return {
            "code": code,
            "close": int(close),
            "change_pct": round(change_pct, 2),
            "vol_ratio": round(vol_ratio, 2),
            "rsi": round(rsi, 1),
            "adx": round(adx, 1),
            "kairi": round(kairi, 2),
            "trading_val": round(tv_oku, 1),
            "points": points,
            "score": round(score, 1)
        }
        
    except Exception as e:
        return None


def main():
    print("=" * 70)
    print("💎 ポイント制スクリーニング v6.1 (Ghost Buster + Point System)")
    print(f"   データ期限: {MAX_DAYS_OLD}日以内 (古いデータは除外)")
    print("=" * 70)
    print("【絶対NG条件】")
    print(f"   RSI > {ABSOLUTE_NG['max_rsi']}")
    print(f"   乖離率 > {ABSOLUTE_NG['max_kairi']}%")
    print(f"   上昇率 > {ABSOLUTE_NG['max_change']}%")
    print("-" * 70)
    
    files = glob.glob(os.path.join(DATA_DIR, "daily_*.csv"))
    
    if not files:
        print("❌ データファイルがありません")
        return
    
    print(f"📊 分析対象: {len(files)} 銘柄")
    
    # 並列処理
    results = []
    with ProcessPoolExecutor() as executor:
        for i, res in enumerate(executor.map(analyze_stock, files)):
            if res and res["points"] > 0:
                results.append(res)
            if (i + 1) % 500 == 0:
                print(f"   処理中... [{i+1}/{len(files)}]")
    
    print(f"   処理完了: {len(files)} 銘柄 → {len(results)} 銘柄がポイント>0")
    print("-" * 70)
    
    if results:
        results.sort(key=lambda x: x["score"], reverse=True)
        final = results[:TARGET_COUNT]
        
        print(f"\n🎯 最終選抜 ({len(final)}銘柄)")
        print(f"{'順位':<4} {'コード':<6} {'終値':>7} {'変化率':>7} {'RSI':>6} {'ADX':>6} {'乖離':>6} {'倍率':>5} {'Pt':>4} {'スコア':>7}")
        print("-" * 75)
        
        for i, row in enumerate(final):
            print(f"{i+1:<4} {row['code']:<6} {row['close']:>7,} {row['change_pct']:>+6.2f}% "
                  f"{row['rsi']:>6.1f} {row['adx']:>6.1f} {row['kairi']:>+5.1f}% "
                  f"{row['vol_ratio']:>4.1f}x {row['points']:>4} {row['score']:>7.1f}")
        
        df_result = pd.DataFrame(final)
        df_result.to_csv(OUTPUT_FILE, index=False)
        
        print(f"\n💾 {OUTPUT_FILE} に保存しました")
        
    else:
        print("\n❌ ポイント>0の銘柄がありませんでした")
        pd.DataFrame(columns=["code"]).to_csv(OUTPUT_FILE, index=False)
    
    print("=" * 70)


if __name__ == "__main__":
    main()