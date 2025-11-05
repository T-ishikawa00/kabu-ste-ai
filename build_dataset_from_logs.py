# -*- coding: utf-8 -*-
import re, os, json, argparse
from pathlib import Path

RE_DEBUG = re.compile(
    r"\[ENTRY_DECIDER\]\s+code=(?P<code>\d+)\s+act=(?P<act>\w+)\s+OBI=(?P<obi>[\d\.]+)\s+spread=(?P<spr>[\d\.]+)\s+vol5=(?P<vol5>[\d\.]+)\s+upticks=(?P<upticks>-?\d+)",
    re.I,
)
RE_BUY = re.compile(r"(PAPER|LIVE)\s+BUY\s+(?P<code>\d+)@(?P<ex>\d+)\s+x(?P<qty>\d+)\s+cur=(?P<cur>[\d\.]+)", re.I)
RE_SELL = re.compile(
    r"(PAPER|LIVE)\s+SELL\s+(?P<code>\d+)@(?P<ex>\d+)\s+x(?P<qty>\d+)\s+cur=(?P<cur>[\d\.]+).*?→\s+(?P<reason>TP|SL|TIME|FORCE)\s+pnl=(?P<pnl>[-\d\.]+)",
    re.I,
)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True, help="ログフォルダ（例: .\\logs）")
    ap.add_argument("--out", required=True, help="出力 jsonl（例: .\\dataset\\experiences.jsonl）")
    return ap.parse_args()

def main():
    args = parse_args()
    log_dir = Path(args.logs)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in log_dir.glob("*.log") if p.is_file()])
    if not files:
        print("ログが見つかりません:", log_dir)
        return

    last_feat = {}  # code -> dict
    open_pos = {}   # code -> dict
    n_written = 0

    with out_p.open("w", encoding="utf-8") as fw:
        for f in files:
            with f.open("r", encoding="utf-8", errors="ignore") as fr:
                for line in fr:
                    m = RE_DEBUG.search(line)
                    if m:
                        d = m.groupdict()
                        code = d["code"]
                        try:
                            last_feat[code] = {
                                "code": code,
                                "obi": float(d.get("obi", 0.5)),
                                "spread": float(d.get("spr", 0.0)),
                                "vol5": float(d.get("vol5", 0.0)),
                                "upticks": int(d.get("upticks", 0)),
                            }
                        except Exception:
                            pass
                        continue

                    m = RE_BUY.search(line)
                    if m:
                        d = m.groupdict()
                        code = d["code"]
                        try:
                            entry = {
                                "code": code,
                                "exchange": int(d["ex"]),
                                "qty": int(d["qty"]),
                                "entry_px": float(d["cur"]),
                            }
                            feat = last_feat.get(code, {"obi": 0.5, "spread": 0.0, "vol5": 0.0, "upticks": 0})
                            entry.update(feat)
                            open_pos[code] = entry
                        except Exception:
                            pass
                        continue

                    m = RE_SELL.search(line)
                    if m:
                        d = m.groupdict()
                        code = d["code"]
                        if code not in open_pos:
                            continue
                        try:
                            ex = open_pos.pop(code)
                            pnl = float(d["pnl"])
                            rec = {
                                "code": code,
                                "qty": ex.get("qty", 0),
                                "entry_px": ex.get("entry_px", None),
                                "obi": ex.get("obi", 0.5),
                                "spread": ex.get("spread", 0.0),
                                "vol5": ex.get("vol5", 0.0),
                                "upticks": ex.get("upticks", 0),
                                "pnl": pnl,
                                "label": 1 if pnl > 0 else 0,
                                "reason": d.get("reason"),
                            }
                            fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                            n_written += 1
                        except Exception:
                            pass

    print(f"書き出し完了: {n_written} 行 → {out_p}")

if __name__ == "__main__":
    main()
