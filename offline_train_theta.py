# -*- coding: utf-8 -*-
import json, argparse
from pathlib import Path
from collections import defaultdict

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="experiences.jsonl")
    ap.add_argument("--registry", required=True, help="model_registry.json")
    ap.add_argument("--min-trades", type=int, default=10, help="銘柄ごとの最小サンプル数")
    ap.add_argument("--metric", choices=["avg_pnl", "winrate", "pf"], default="avg_pnl")
    return ap.parse_args()

def load_dataset(path):
    data = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
                code = str(r.get("code"))
                if code:
                    data[code].append(r)
            except Exception:
                continue
    return data

def score_subset(rows, metric):
    if not rows:
        return -1e9
    pnl = [r["pnl"] for r in rows]
    wins = sum(1 for x in pnl if x > 0)
    losses = sum(1 for x in pnl if x <= 0)
    avg_pnl = sum(pnl) / max(1, len(pnl))
    winrate = wins / max(1, len(pnl))
    if losses == 0:
        pf = float("inf") if wins > 0 else 0.0
    else:
        pf = (sum(x for x in pnl if x > 0) / max(1e-9, abs(sum(x for x in pnl if x <= 0))))
    return {"avg_pnl": avg_pnl, "winrate": winrate, "pf": pf}[metric]

def choose_theta(rows, metric):
    best_theta = 0.55
    best_score = -1e9
    for i in range(50, 71):  # 0.50〜0.70
        th = i / 100
        subset = [r for r in rows if float(r.get("obi", 0.5)) >= th]
        if len(subset) < max(5, int(len(rows)*0.2)):
            continue
        sc = score_subset(subset, metric)
        if sc > best_score:
            best_score, best_theta = sc, th
    return best_theta, best_score

def main():
    args = parse_args()
    data = load_dataset(args.inp)

    reg_p = Path(args.registry)
    if reg_p.exists():
        try:
            model = json.loads(reg_p.read_text(encoding="utf-8"))
        except Exception:
            model = {}
    else:
        model = {}

    updated = 0
    for code, rows in data.items():
        if len(rows) < args.min_trades:
            continue
        th, _ = choose_theta(rows, args.metric)
        model.setdefault(code, {})["theta"] = round(th, 2)
        updated += 1

    reg_p.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"更新完了: {updated}銘柄 → {reg_p}")

if __name__ == "__main__":
    main()
