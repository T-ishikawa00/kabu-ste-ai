# -*- coding: utf-8 -*-
"""
backtest_rules.py - ルールベース戦略バックテスト
================================================
【目的】
RLを使う前に、そもそも「この特徴量で勝てるルールが存在するか」を確認する。
勝率50%を超えるルールが見つからなければ、RLに何を学習させても無駄。

【使い方】
py -3.12 backtest_rules.py
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
from dataclasses import dataclass
from typing import List, Tuple

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"

# 複数銘柄でテスト
TARGET_STOCKS = [
    "6525",  # KOKUSAI（ボラ適切）
]

# トレードパラメータ
SLIPPAGE_PCT = 0.05      # 0.05%
SPREAD_PCT = 0.03        # 0.03%
TOTAL_COST_PCT = SLIPPAGE_PCT + SPREAD_PCT

TP_PCT = 0.5             # 利確 +0.5%
SL_PCT = 0.5             # 損切 -0.5%（対称）
MAX_HOLD_BARS = 30       # 最大保有30バー（≒30分）


@dataclass
class Trade:
    entry_bar: int
    entry_price: float
    side: str  # "BUY" or "SHORT"
    exit_bar: int = 0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


def calc_rci(series: pd.Series, period: int) -> pd.Series:
    """RCI計算"""
    result = pd.Series(index=series.index, dtype=float)
    result[:] = np.nan
    
    for i in range(period - 1, len(series)):
        window = series.iloc[i - period + 1:i + 1].values
        if len(window) < period:
            continue
        price_rank = pd.Series(window).rank(ascending=False).values
        time_rank = np.arange(1, period + 1)
        d_squared = np.sum((price_rank - time_rank) ** 2)
        result.iloc[i] = (1 - 6 * d_squared / (period * (period**2 - 1))) * 100
    
    return result


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    """テクニカル指標を計算"""
    df = df.copy()
    
    # カラム名正規化
    df.columns = [str(c).lower().strip() for c in df.columns]
    
    # closeカラムを探す
    if 'close' not in df.columns:
        for alias in ['current', 'price', 'currentprice']:
            if alias in df.columns:
                df['close'] = df[alias]
                break
    
    if 'close' not in df.columns:
        raise ValueError("closeカラムが見つかりません")
    
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna(subset=['close'])
    df = df[df['close'] > 0].reset_index(drop=True)
    
    # OHLC補完
    if 'high' not in df.columns:
        df['high'] = df['close']
    if 'low' not in df.columns:
        df['low'] = df['close']
    if 'open' not in df.columns:
        df['open'] = df['close']
    
    # 移動平均
    df['ma15'] = df['close'].rolling(15, min_periods=1).mean()
    df['ma25'] = df['close'].rolling(25, min_periods=1).mean()
    df['ma75'] = df['close'].rolling(75, min_periods=1).mean()
    
    # MA乖離率
    df['dist_ma15'] = (df['close'] - df['ma15']) / df['ma15'] * 100
    df['dist_ma25'] = (df['close'] - df['ma25']) / df['ma25'] * 100
    df['dist_ma75'] = (df['close'] - df['ma75']) / df['ma75'] * 100
    
    # トレンド
    df['trend'] = np.where(df['close'] > df['ma75'], 1, -1)
    df['ma75_slope'] = df['ma75'].diff()
    
    # RSI
    rsi = df.ta.rsi(close='close', length=14)
    df['rsi'] = rsi if rsi is not None else 50.0
    
    # MACD
    macd = df.ta.macd(close='close', fast=12, slow=26, signal=9)
    if macd is not None and 'MACDh_12_26_9' in macd.columns:
        df['macd_hist'] = macd['MACDh_12_26_9']
    else:
        df['macd_hist'] = 0.0
    
    # RCI
    df['rci9'] = calc_rci(df['close'], 9)
    df['rci26'] = calc_rci(df['close'], 26)
    
    # Bollinger Bands
    bbands = df.ta.bbands(close='close', length=20, std=2)
    if bbands is not None:
        bbl_col = [c for c in bbands.columns if 'BBL' in c][0]
        bbu_col = [c for c in bbands.columns if 'BBU' in c][0]
        df['bb_lower'] = bbands[bbl_col]
        df['bb_upper'] = bbands[bbu_col]
        df['bb_pct'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-9)
    else:
        df['bb_pct'] = 0.5
    
    # 欠損値処理
    df = df.ffill().bfill().fillna(0)
    
    # 最初の100バーは指標が安定しないのでスキップ
    if len(df) > 100:
        df = df.iloc[100:].reset_index(drop=True)
    
    return df


def simulate_trade(df: pd.DataFrame, entry_bar: int, side: str) -> Trade:
    """1トレードをシミュレート"""
    entry_price = df.iloc[entry_bar]['close']
    
    # スリッページ込みのエントリー価格
    if side == "BUY":
        entry_price *= (1 + TOTAL_COST_PCT / 100)
    else:
        entry_price *= (1 - TOTAL_COST_PCT / 100)
    
    trade = Trade(
        entry_bar=entry_bar,
        entry_price=entry_price,
        side=side
    )
    
    for i in range(entry_bar + 1, min(entry_bar + MAX_HOLD_BARS + 1, len(df))):
        current_price = df.iloc[i]['close']
        
        # PnL計算
        if side == "BUY":
            exit_price = current_price * (1 - TOTAL_COST_PCT / 100)
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            exit_price = current_price * (1 + TOTAL_COST_PCT / 100)
            pnl_pct = (entry_price - exit_price) / entry_price * 100
        
        # TP/SLチェック
        if pnl_pct >= TP_PCT:
            trade.exit_bar = i
            trade.exit_price = current_price
            trade.pnl_pct = pnl_pct
            trade.exit_reason = "TP"
            return trade
        
        if pnl_pct <= -SL_PCT:
            trade.exit_bar = i
            trade.exit_price = current_price
            trade.pnl_pct = pnl_pct
            trade.exit_reason = "SL"
            return trade
    
    # 時間切れ
    final_bar = min(entry_bar + MAX_HOLD_BARS, len(df) - 1)
    final_price = df.iloc[final_bar]['close']
    
    if side == "BUY":
        exit_price = final_price * (1 - TOTAL_COST_PCT / 100)
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        exit_price = final_price * (1 + TOTAL_COST_PCT / 100)
        pnl_pct = (entry_price - exit_price) / entry_price * 100
    
    trade.exit_bar = final_bar
    trade.exit_price = final_price
    trade.pnl_pct = pnl_pct
    trade.exit_reason = "TIME"
    
    return trade


# ==========================================
# 戦略定義
# ==========================================

def strategy_momentum(df: pd.DataFrame, bar: int) -> str:
    """
    戦略1: モメンタム
    - RSI < 30 かつ 上昇トレンド → BUY
    - RSI > 70 かつ 下降トレンド → SHORT
    """
    row = df.iloc[bar]
    
    if row['rsi'] < 30 and row['trend'] > 0:
        return "BUY"
    if row['rsi'] > 70 and row['trend'] < 0:
        return "SHORT"
    return "STAY"


def strategy_rci_cross(df: pd.DataFrame, bar: int) -> str:
    """
    戦略2: RCIクロス
    - RCI9が-80から上昇 かつ RCI26 < 0 → BUY
    - RCI9が+80から下落 かつ RCI26 > 0 → SHORT
    """
    if bar < 1:
        return "STAY"
    
    row = df.iloc[bar]
    prev = df.iloc[bar - 1]
    
    # RCI9が-80以下から上昇
    if prev['rci9'] < -80 and row['rci9'] > prev['rci9'] and row['rci26'] < 0:
        return "BUY"
    
    # RCI9が+80以上から下落
    if prev['rci9'] > 80 and row['rci9'] < prev['rci9'] and row['rci26'] > 0:
        return "SHORT"
    
    return "STAY"


def strategy_bb_bounce(df: pd.DataFrame, bar: int) -> str:
    """
    戦略3: ボリンジャーバンド反発
    - 下限バンド付近(BB% < 0.1) かつ 上昇トレンド → BUY
    - 上限バンド付近(BB% > 0.9) かつ 下降トレンド → SHORT
    """
    row = df.iloc[bar]
    
    if row['bb_pct'] < 0.1 and row['trend'] > 0:
        return "BUY"
    if row['bb_pct'] > 0.9 and row['trend'] < 0:
        return "SHORT"
    return "STAY"


def strategy_macd_trend(df: pd.DataFrame, bar: int) -> str:
    """
    戦略4: MACDトレンドフォロー
    - MACDヒストグラム > 0 かつ MA75上昇 かつ 価格 > MA75 → BUY
    - MACDヒストグラム < 0 かつ MA75下降 かつ 価格 < MA75 → SHORT
    """
    row = df.iloc[bar]
    
    if row['macd_hist'] > 0 and row['ma75_slope'] > 0 and row['close'] > row['ma75']:
        return "BUY"
    if row['macd_hist'] < 0 and row['ma75_slope'] < 0 and row['close'] < row['ma75']:
        return "SHORT"
    return "STAY"


def strategy_ma_deviation(df: pd.DataFrame, bar: int) -> str:
    """
    戦略5: MA乖離率回帰
    - MA25から-1%以上乖離 かつ 上昇トレンド → BUY（押し目買い）
    - MA25から+1%以上乖離 かつ 下降トレンド → SHORT（戻り売り）
    """
    row = df.iloc[bar]
    
    if row['dist_ma25'] < -1.0 and row['trend'] > 0:
        return "BUY"
    if row['dist_ma25'] > 1.0 and row['trend'] < 0:
        return "SHORT"
    return "STAY"


def strategy_combined(df: pd.DataFrame, bar: int) -> str:
    """
    戦略6: 複合条件（厳選エントリー）
    - RSI < 40 かつ RCI9 < -50 かつ トレンド上昇 かつ MACD > 0 → BUY
    - RSI > 60 かつ RCI9 > 50 かつ トレンド下降 かつ MACD < 0 → SHORT
    """
    row = df.iloc[bar]
    
    buy_cond = (
        row['rsi'] < 40 and 
        row['rci9'] < -50 and 
        row['trend'] > 0 and 
        row['macd_hist'] > 0
    )
    
    short_cond = (
        row['rsi'] > 60 and 
        row['rci9'] > 50 and 
        row['trend'] < 0 and 
        row['macd_hist'] < 0
    )
    
    if buy_cond:
        return "BUY"
    if short_cond:
        return "SHORT"
    return "STAY"


def strategy_random(df: pd.DataFrame, bar: int) -> str:
    """
    戦略7: ランダム（ベースライン）
    - 10%の確率でBUY、10%の確率でSHORT
    """
    import random
    r = random.random()
    if r < 0.02:  # 2%でBUY
        return "BUY"
    if r < 0.04:  # 2%でSHORT
        return "SHORT"
    return "STAY"


def strategy_inverse_momentum(df: pd.DataFrame, bar: int) -> str:
    """
    戦略8: 逆モメンタム（シグナルの逆を張る）
    - RSI > 70 かつ 上昇トレンド → BUY（過熱してるけど順張り）
    - RSI < 30 かつ 下降トレンド → SHORT（売られ過ぎだけど順張り）
    """
    row = df.iloc[bar]
    
    if row['rsi'] > 70 and row['trend'] > 0:
        return "BUY"
    if row['rsi'] < 30 and row['trend'] < 0:
        return "SHORT"
    return "STAY"


def strategy_trend_only(df: pd.DataFrame, bar: int) -> str:
    """
    戦略9: トレンドのみ（最もシンプル）
    - 価格 > MA75 → BUY
    - 価格 < MA75 → SHORT
    """
    row = df.iloc[bar]
    
    # 5%の確率でエントリー（頻度調整）
    import random
    if random.random() > 0.05:
        return "STAY"
    
    if row['close'] > row['ma75']:
        return "BUY"
    else:
        return "SHORT"


def strategy_inverse_macd(df: pd.DataFrame, bar: int) -> str:
    """
    戦略10: 逆MACD（シグナルの逆を張る）
    - MACDヒストグラム > 0 → SHORT（過熱売り）
    - MACDヒストグラム < 0 → BUY（売られ過ぎ買い）
    """
    row = df.iloc[bar]
    
    import random
    if random.random() > 0.05:
        return "STAY"
    
    if row['macd_hist'] > 0:
        return "SHORT"  # 逆！
    elif row['macd_hist'] < 0:
        return "BUY"    # 逆！
    return "STAY"


def strategy_inverse_rsi(df: pd.DataFrame, bar: int) -> str:
    """
    戦略11: 逆RSI
    - RSI > 50 → SHORT
    - RSI < 50 → BUY
    """
    row = df.iloc[bar]
    
    import random
    if random.random() > 0.05:
        return "STAY"
    
    if row['rsi'] > 50:
        return "SHORT"
    else:
        return "BUY"


def strategy_mean_reversion(df: pd.DataFrame, bar: int) -> str:
    """
    戦略12: 平均回帰（逆張り）
    - 価格がMA25より上 → SHORT（下がるはず）
    - 価格がMA25より下 → BUY（上がるはず）
    """
    row = df.iloc[bar]
    
    import random
    if random.random() > 0.05:
        return "STAY"
    
    if row['close'] > row['ma25']:
        return "SHORT"
    else:
        return "BUY"


# 戦略一覧
STRATEGIES = {
    "1_Momentum": strategy_momentum,
    "2_RCI_Cross": strategy_rci_cross,
    "3_BB_Bounce": strategy_bb_bounce,
    "4_MACD_Trend": strategy_macd_trend,
    "5_MA_Deviation": strategy_ma_deviation,
    "6_Combined": strategy_combined,
    "7_RANDOM": strategy_random,
    "8_Inverse_Mom": strategy_inverse_momentum,
    "9_Trend_Only": strategy_trend_only,
}


def backtest_strategy(df: pd.DataFrame, strategy_func) -> Tuple[List[Trade], dict]:
    """戦略をバックテスト"""
    trades = []
    in_position = False
    position_end_bar = 0
    
    for bar in range(len(df) - MAX_HOLD_BARS - 1):
        # ポジション中はスキップ
        if in_position and bar < position_end_bar:
            continue
        
        in_position = False
        
        # シグナル取得
        signal = strategy_func(df, bar)
        
        if signal in ["BUY", "SHORT"]:
            trade = simulate_trade(df, bar, signal)
            trades.append(trade)
            in_position = True
            position_end_bar = trade.exit_bar + 1
    
    # 統計計算
    if not trades:
        return trades, {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_pnl": 0,
            "total_pnl": 0,
            "profit_factor": 0,
        }
    
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    losses = sum(1 for t in trades if t.pnl_pct <= 0)
    total_pnl = sum(t.pnl_pct for t in trades)
    
    gross_profit = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    
    # 出口理由の集計
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    
    return trades, {
        "total": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "avg_pnl": total_pnl / len(trades) if trades else 0,
        "total_pnl": total_pnl,
        "profit_factor": profit_factor,
        "exit_reasons": exit_reasons,
    }


def main():
    print("=" * 60)
    print(f"📊 ルールベース戦略バックテスト（複数銘柄）")
    print(f"   TP: +{TP_PCT}% / SL: -{SL_PCT}% / MaxHold: {MAX_HOLD_BARS}バー")
    print("=" * 60)
    
    for TARGET_STOCK in TARGET_STOCKS:
        print(f"\n{'─'*60}")
        print(f"📈 銘柄: {TARGET_STOCK}")
        print(f"{'─'*60}")
        
        # データ読み込み
        file_path = os.path.join(DATA_DIR, f"merged_{TARGET_STOCK}.csv")
        if not os.path.exists(file_path):
            print(f"❌ ファイルが見つかりません: {file_path}")
            continue
        
        df = pd.read_csv(file_path)
        
        try:
            df = prepare_data(df)
        except Exception as e:
            print(f"❌ データ準備エラー: {e}")
            continue
        
        # ボラティリティ計算
        df['returns'] = df['close'].pct_change() * 100
        volatility = df['returns'].std()
        print(f"📊 データ: {len(df)}行 | ボラ: {volatility:.3f}%/バー")
        
        # 主要戦略だけテスト
        test_strategies = {
            "RANDOM": strategy_random,
            "Trend_Follow": strategy_trend_only,
            "MACD_Trend": strategy_macd_trend,
            "Inverse_MACD": strategy_inverse_macd,
            "Inverse_RSI": strategy_inverse_rsi,
            "Mean_Revert": strategy_mean_reversion,
        }
        
        for name, func in test_strategies.items():
            trades, stats = backtest_strategy(df, func)
            
            if stats['total'] < 5:
                print(f"   【{name}】トレード数不足: {stats['total']}")
                continue
            
            tp_count = stats.get('exit_reasons', {}).get('TP', 0)
            sl_count = stats.get('exit_reasons', {}).get('SL', 0)
            time_count = stats.get('exit_reasons', {}).get('TIME', 0)
            
            print(f"   【{name}】勝率:{stats['win_rate']:.0f}% PF:{stats['profit_factor']:.2f} | TP:{tp_count} SL:{sl_count} TIME:{time_count}")


if __name__ == "__main__":
    main()