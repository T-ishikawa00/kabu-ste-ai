# kpi_rollup.py
import json, glob, statistics, os
OUT="telemetry/rolling_kpi.json"

def _iter_trades(pattern="logs/trades_*.jsonl"):
    for p in sorted(glob.glob(pattern))[-7:]:
        with open(p,encoding="utf-8") as f:
            for line in f:
                try: yield json.loads(line)
                except: pass

def rollup():
    agg={}
    for tr in _iter_trades():
        key=(tr.get("code"), tr.get("strategy","SCALP"))
        a=agg.setdefault(key, dict(pnl=[], slip=[]))
        a["pnl"].append(tr.get("pnl_ticks", tr.get("pnl",0.0)))
        if "slippage_ticks" in tr: a["slip"].append(tr["slippage_ticks"])
    out={}
    for (code,strategy),a in agg.items():
        wins=[x for x in a["pnl"] if x>0]; losses=[-x for x in a["pnl"] if x<0]
        pf=(sum(wins)/max(1,sum(losses))) if losses else (float("inf") if wins else 0.0)
        ev=sum(a["pnl"])/max(1,len(a["pnl"]))
        slip_med=statistics.median(a["slip"]) if a["slip"] else None
        out.setdefault(code,{})[strategy]=dict(pf_7d=pf, ev_ticks_7d=ev, slip_med=slip_med)
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)

if __name__=="__main__":
    rollup()
