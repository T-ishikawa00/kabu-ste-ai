# -*- coding: utf-8 -*-
"""
train_from_ohlcv.py
- 1分足OHLCVの“銘柄の歴史”を読み込んで、銘柄別に OBI しきい値 θ を最適化
- OBIの代用: obi_proxy = (close - low) / max(1e-9, high - low)  ∈ [0,1]
- スプレッド近似: spread_proxy_ticks = (high - low) / tick
- エントリー条件は本番に寄せる（θ, spread, momentum, liquidity）
- スキャのTP/SL/最大保有バーで結果をロジック同等に判定
使い方:
  python train_from_ohlcv.py --data-dir .\\data\\1min --registry .\\model_registry.json
"""
import os, json, argparse, math
from pathlib import Path
import csv
from statistics import median

# --- 既存 tick_size を利用（なければ近似）
def _fallback_tick(price: float) -> float:
    # ざっくり近似（東証の細かい表は省略）
    if price < 3000: return 1.0
    if price < 5000: return 5.0
    if price < 10000: return 10.0
    if price < 30000: return 10.0
    if price < 50000: return 50.0
    return 100.0

try:
    from utils_common import tick_size as _live_tick
    def jp_tick(code: str, px: float) -> float:
        try:
            return float(_live_tick(code, px, {}))
        except Exception:
            return _fallback_tick(float(px))
except Exception:
    def jp_tick(code: str, px: float) -> float:
        return _fallback_tick(float(px))

def load_csv(path: Path):
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for d in r:
            try:
                rows.append({
                    "ts": d.get("ts") or d.get("datetime") or d.get("time"),
                    "open": float(d["open"]),
                    "high": float(d["high"]),
                    "low": float(d["low"]),
                    "close": float(d["close"]),
                    "volume": float(d.get("volume", 0) or 0),
                })
            except Exception:
                continue
    return rows

def simulate_symbol(code: str, bars, theta: float,
                    tp_ticks=1.0, sl_ticks=1.0, max_hold_bars=5,
                    vol_med_factor=0.4, momentum_delta_ticks=0.5, max_spread_ticks=6.0):
    """
    bars: list of dicts with open/high/low/close/volume
    return: dict(score stats)
    """
    if len(bars) < 200:
        return {"trades": 0, "avg_pnl": -1e9, "winrate": 0.0, "pf": 0.0}

    # rolling volume sums
    vol5 = [0.0]*len(bars)
    for i in range(len(bars)):
        s = 0.0
        for k in range(max(0, i-4), i+1):
            s += bars[k]["volume"]
        vol5[i] = s
    # rolling 60-bar median
    vol60_med = [0.0]*len(bars)
    for i in range(len(bars)):
        s = [bars[k]["volume"] for k in range(max(0, i-59), i+1)]
        vol60_med[i] = median(s) if s else 0.0

    pnl_list = []
    wins_sum = 0.0
    losses_sum = 0.0
    wins = 0
    losses = 0

    for i in range(60, len(bars)-max_hold_bars-1):
        c_prev = bars[i-1]["close"]
        b = bars[i]
        c = b["close"]; h = b["high"]; l = b["low"]; v5 = vol5[i]; vmed = vol60_med[i]
        if (h - l) <= 0:
            continue

        tick = jp_tick(code, c)
        # proxies
        obi = (c - l) / max(1e-9, (h - l))
        spread_ticks = (h - l) / max(1e-9, tick)

        # conditions ~ live
        obi_ok = (obi >= theta)
        spread_ok = (spread_ticks <= max_spread_ticks)
        delta_ticks = (c - c_prev) / max(0.5, tick)
        momentum_ok = (delta_ticks >= momentum_delta_ticks) or (c > c_prev)
        liq_thr = max(120.0, vmed * vol_med_factor)
        liquidity_ok = (v5 >= liq_thr)

        if not (obi_ok and spread_ok and momentum_ok and liquidity_ok):
            continue

        entry = c
        tp = entry + tp_ticks * tick
        sl = entry - sl_ticks * tick

        # walk forward up to max_hold_bars
        exit_px = None
        outcome = None
        for j in range(1, max_hold_bars+1):
            b2 = bars[i+j]
            hi, lo, cls = b2["high"], b2["low"], b2["close"]
            # 同バー両方ヒットは保守的にSL先行
            if lo <= sl:
                exit_px = sl
                outcome = "SL"
                break
            if hi >= tp:
                exit_px = tp
                outcome = "TP"
                break
        if exit_px is None:
            exit_px = bars[i+max_hold_bars]["close"]
            outcome = "TIME"

        pnl = exit_px - entry
        pnl_list.append(pnl)
        if pnl > 0:
            wins += 1
            wins_sum += pnl
        else:
            losses += 1
            losses_sum += abs(pnl)

    n = len(pnl_list)
    if n == 0:
        return {"trades": 0, "avg_pnl": -1e9, "winrate": 0.0, "pf": 0.0}
    avg_pnl = sum(pnl_list)/n
    winrate = wins/max(1, n)
    pf = (wins_sum/max(1e-9, losses_sum)) if losses_sum > 0 else (float("inf") if wins_sum>0 else 0.0)
    return {"trades": n, "avg_pnl": avg_pnl, "winrate": winrate, "pf": pf}

def grid_search_theta(code: str, bars, metric="avg_pnl",
                      th_min=0.50, th_max=0.70, step=0.01,
                      min_trades=50):
    best = {"theta": 0.55, "score": -1e9, "stats": {}}
    th = th_min
    while th <= th_max + 1e-9:
        stats = simulate_symbol(code, bars, theta=th)
        # サンプル不足はスキップ
        if stats["trades"] >= max(min_trades, int(len(bars)*0.01)):
            score = stats[metric]
            if score > best["score"]:
                best = {"theta": round(th, 2), "score": score, "stats": stats}
        th = round(th + step, 2)
    return best

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="1分足CSVのフォルダ（ts,open,high,low,close,volume）")
    ap.add_argument("--registry", default="model_registry.json", help="出力/上書き先の model_registry.json")
    ap.add_argument("--metric", choices=["avg_pnl","winrate","pf"], default="avg_pnl")
    ap.add_argument("--theta-min", type=float, default=0.50)
    ap.add_argument("--theta-max", type=float, default=0.70)
    ap.add_argument("--theta-step", type=float, default=0.01)
    ap.add_argument("--min-trades", type=int, default=50)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    files = sorted([p for p in data_dir.glob("*.csv")])
    if not files:
        print("CSVが見つかりません:", data_dir)
        return

    # 既存レジストリをロード
    reg_p = Path(args.registry)
    if reg_p.exists():
        try:
            registry = json.loads(reg_p.read_text(encoding="utf-8"))
        except Exception:
            registry = {}
    else:
        registry = {}

    updated = 0
    for f in files:
        code = f.stem  # 例: 7013.csv -> "7013"
        bars = load_csv(f)
        if len(bars) < 300:
            print(f"{code}: データ不足 {len(bars)} 行")
            continue
        res = grid_search_theta(code, bars, metric=args.metric,
                                th_min=args.theta_min, th_max=args.theta_max,
                                step=args.theta_step, min_trades=args.min_trades)
        if res["stats"].get("trades",0) <= 0:
            print(f"{code}: 最適化不可（サンプル不足）")
            continue
        if code not in registry or not isinstance(registry.get(code), dict):
            registry[code] = {}
        registry[code]["theta"] = float(res["theta"])
        updated += 1
        s = res["stats"]
        print(f"{code}: theta={res['theta']} trades={s['trades']} avg_pnl={s['avg_pnl']:.4f} winrate={s['winrate']:.2%} pf={s['pf']:.2f}")

    reg_p.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"更新完了: {updated}銘柄 → {reg_p}")

if __name__ == "__main__":
    main()
