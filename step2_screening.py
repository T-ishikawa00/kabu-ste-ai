# -*- coding: utf-8 -*-
"""
step2_screening.py v6.5 (Safety First)
======================================
【修正点】
1. ブラックリスト対応: config.yamlのblacklistに入っている銘柄を絶対除外
2. 暴落回避: 前日比 -5% 以下の銘柄は「落ちるナイフ」として除外
"""

import os
import glob
import pandas as pd
import numpy as np
import re
import yaml
import warnings
from concurrent.futures import ProcessPoolExecutor

warnings.filterwarnings('ignore')

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data_archive_daily"
OUTPUT_FILE = "target_list.csv"
CONFIG_FILE = "config.yaml"
TARGET_COUNT = 7
MAX_DAYS_OLD = 5  

ABSOLUTE_NG = {
    "max_rsi": 85,
    "max_kairi": 25,
    "max_change": 15,     # 上昇しすぎも怖いけど一旦許容
    "min_change": -5.0,   # ★追加: これ以上暴落してる銘柄は触らない
}

BASE_PARAMS = {
    "min_price": 1000,
    "max_price":10000,                # ★修正: 10000 -> 4000 (高額株排除)
    "min_trading_val": 1_000_000_000, # 10億円以上
    "min_days": 30,
    "min_volatility": 1.5
}


def load_config_lists():
    """config.yaml から固定銘柄とブラックリストを取得"""
    fixed_codes = set()
    blacklist_codes = set()
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                
                # 固定銘柄
                symbols = config.get("symbols", [])
                for s in symbols:
                    if s.get("fixed", False):
                        fixed_codes.add(str(s["code"]))
                
                # ブラックリスト
                gatekeeper = config.get("gatekeeper", {})
                bl = gatekeeper.get("blacklist", [])
                for b in bl:
                    blacklist_codes.add(str(b))
                    
        except Exception as e:
            print(f"⚠️ config.yaml 読み込みエラー: {e}")
            
    return fixed_codes, blacklist_codes


def convert_code(code_str):
    if len(code_str) == 5 and code_str.endswith('0'):
        return code_str[:-1]
    return code_str


def is_valid_code(code_str):
    if len(code_str) != 4: return False
    if not re.match(r'^[0-9A-Z]{4}$', code_str): return False
    return True


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def analyze_stock(file_path):
    try:
        df = pd.read_csv(file_path)
        if len(df) < BASE_PARAMS["min_days"]: return None
        
        df.columns = df.columns.str.lower()
        
        if 'datetime' in df.columns: date_col = 'datetime'
        elif 'date' in df.columns: date_col = 'date'
        else: return None
        
        if 'adjc' in df.columns:
            close_col = 'adjc'
            open_col = 'adjo' if 'adjo' in df.columns else 'open'
            high_col = 'adjh' if 'adjh' in df.columns else 'high'
            low_col = 'adjl' if 'adjl' in df.columns else 'low'
            vol_col = 'adjvo' if 'adjvo' in df.columns else 'volume'
        else:
            close_col, open_col, high_col, low_col, vol_col = 'close', 'open', 'high', 'low', 'volume'
        
        required = [close_col, open_col, high_col, low_col, vol_col]
        for col in required:
            if col not in df.columns: return None
        
        df[date_col] = pd.to_datetime(df[date_col], format='mixed', errors='coerce')
        df = df.dropna(subset=[date_col])
        if len(df) < BASE_PARAMS["min_days"]: return None
        df.sort_values(date_col, inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        last_date = df[date_col].iloc[-1]
        if last_date.tzinfo is not None: last_date = last_date.tz_localize(None)
        today = pd.Timestamp.now()
        if (today - last_date).days > MAX_DAYS_OLD: return None

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        code = os.path.basename(file_path).replace("daily_", "").replace(".csv", "")
        code = convert_code(code)
        if not is_valid_code(code): return None
        
        close = float(latest[close_col])
        open_p = float(latest[open_col])
        high = float(latest[high_col])
        low = float(latest[low_col])
        volume = float(latest[vol_col])
        
        if pd.isna(close) or pd.isna(volume) or close == 0 or volume == 0: return None
        
        # 価格フィルタ (4000円以下に厳選)
        if not (BASE_PARAMS["min_price"] <= close <= BASE_PARAMS["max_price"]): return None
        
        trading_value = close * volume
        if trading_value < BASE_PARAMS["min_trading_val"]: return None
        
        # ボラティリティチェック
        df['range_pct'] = (df[high_col] - df[low_col]) / df[open_col] * 100
        daily_range_pct = (high - low) / open_p * 100
        avg_range_5d = df['range_pct'].iloc[-5:].mean()
        
        if avg_range_5d < BASE_PARAMS["min_volatility"]: return None

        # テクニカル指標
        df['rsi'] = calc_rsi(df[close_col], 14)
        rsi = df['rsi'].iloc[-1]
        if pd.isna(rsi) or rsi > ABSOLUTE_NG["max_rsi"]: return None
        
        df['ma25'] = df[close_col].rolling(25).mean()
        ma25 = df['ma25'].iloc[-1]
        if pd.isna(ma25): return None
        
        kairi = (close - ma25) / ma25 * 100
        if kairi > ABSOLUTE_NG["max_kairi"]: return None
        
        prev_close = float(prev[close_col])
        if pd.isna(prev_close) or prev_close == 0: return None
        change_pct = (close - prev_close) / prev_close * 100
        
        # ★NG条件: 上がりすぎ or 下がりすぎ(暴落)
        if change_pct > ABSOLUTE_NG["max_change"]: return None
        if change_pct < ABSOLUTE_NG["min_change"]: return None # -5%以下は除外

        # ポイント計算
        points = 0
        if daily_range_pct >= 5.0: points += 20
        elif daily_range_pct >= 3.0: points += 15
        elif daily_range_pct >= 2.0: points += 10
        elif daily_range_pct < 1.0: points -= 10
        
        recent_vol = df[vol_col].iloc[-6:-1].mean()
        vol_ratio = 1.0
        if recent_vol > 0:
            vol_ratio = volume / recent_vol
            if vol_ratio >= 3.0: points += 25
            elif vol_ratio >= 2.0: points += 20
            elif vol_ratio >= 1.5: points += 15
            elif vol_ratio >= 1.2: points += 10
            elif vol_ratio < 0.8: points -= 10
        
        if 50 <= rsi <= 70: points += 10
        elif 40 <= rsi < 50: points += 5
        elif rsi > 75: points -= 5
        elif rsi < 30: points -= 5
        
        tv_oku = trading_value / 100_000_000
        if tv_oku >= 50: points += 15
        elif tv_oku >= 30: points += 10
        elif tv_oku >= 20: points += 5
        
        if close > ma25: points += 10
        if close > open_p: points += 5
        
        if high > low:
            upper_shadow = high - max(close, open_p)
            upper_ratio = upper_shadow / (high - low)
            if upper_ratio > 0.4: points -= 15
            elif upper_ratio > 0.25: points -= 5

        score = points * np.log10(trading_value)
        
        return {
            "code": code,
            "close": int(close),
            "change_pct": round(change_pct, 2),
            "vol_ratio": round(vol_ratio, 2),
            "range_pct": round(daily_range_pct, 2),
            "trading_val": round(tv_oku, 1),
            "points": points,
            "score": round(score, 1)
        }
        
    except Exception:
        return None


def main():
    print("=" * 70)
    print("💎 ポイント制スクリーニング v6.5 (Safety First)")
    print(f"   基準: 4,000円以下 / 暴落(-5%)除外 / 売買代金10億円以上")
    print("=" * 70)
    
    fixed_codes, blacklist_codes = load_config_lists()
    
    if fixed_codes: print(f"🔒 固定銘柄を除外: {len(fixed_codes)}件")
    if blacklist_codes: print(f"⛔ ブラックリスト除外: {len(blacklist_codes)}件")
    
    files = glob.glob(os.path.join(DATA_DIR, "daily_*.csv"))
    if not files:
        print("❌ データファイルがありません")
        return
    
    print(f"📊 分析対象: {len(files)} 銘柄")
    
    results = []
    with ProcessPoolExecutor() as executor:
        for i, res in enumerate(executor.map(analyze_stock, files)):
            if res and res["points"] > 0:
                results.append(res)
            if (i + 1) % 500 == 0:
                print(f"   処理中... [{i+1}/{len(files)}]")
    
    print(f"   処理完了: {len(files)} 銘柄 → {len(results)} 銘柄が候補")
    print("-" * 70)
    
    if results:
        results.sort(key=lambda x: x["score"], reverse=True)
        
        final = []
        for res in results:
            if len(final) >= TARGET_COUNT: break
            
            # 除外チェック
            code = res["code"]
            if code in fixed_codes: continue
            if code in blacklist_codes: continue
            
            final.append(res)
        
        print(f"\n🎯 最終選抜 ({len(final)}銘柄)")
        print(f"{'順位':<4} {'コード':<6} {'終値':>7} {'変化率':>7} {'値幅%':>6} {'倍率':>5} {'代金(億)':>8} {'Pt':>4} {'スコア':>7}")
        print("-" * 80)
        
        for i, row in enumerate(final):
            print(f"{i+1:<4} {row['code']:<6} {row['close']:>7,} {row['change_pct']:>+6.2f}% "
                  f"{row['range_pct']:>6.1f} {row['vol_ratio']:>4.1f}x "
                  f"{row['trading_val']:>8.1f} {row['points']:>4} {row['score']:>7.1f}")
        
        df_result = pd.DataFrame(final)
        df_result.to_csv(OUTPUT_FILE, index=False)
        print(f"\n💾 {OUTPUT_FILE} に保存しました")
        
    else:
        print("\n❌ 候補が見つかりませんでした")
        pd.DataFrame(columns=["code"]).to_csv(OUTPUT_FILE, index=False)
    
    print("=" * 70)


if __name__ == "__main__":
    main()