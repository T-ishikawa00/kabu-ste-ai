# -*- coding: utf-8 -*-
# Lv7.3: 取引終了ごとに戦略別thetaを±0.01で微調整（0.55〜0.75, 非悪化ガード）
import os, json, time
from datetime import datetime, timezone, timedelta

MODELS_DIR="models"
KPI_PATH="telemetry/rolling_kpi.json"
JST=timezone(timedelta(hours=9))

def _now_iso(): return datetime.now(JST).isoformat()
def _mp(code): return os.path.join(MODELS_DIR,f"{code}.json")

def _load(code):
    if not os.path.exists(_mp(code)):
        return {"theta_by_strategy":{"SCALP":0.64,"MINI_SWING":0.62},"w_buy":1.0,"w_sell":1.0,"reward_sum":0.0,"updated_at":_now_iso()}
    with open(_mp(code),"r",encoding="utf-8") as f: return json.load(f)

def _atomic_write(path,data):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    tmp=path+f".tmp.{int(time.time()*1000)}"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(tmp,path)

def _save(code,m):
    m["updated_at"]=_now_iso()
    _atomic_write(_mp(code),m)

def _load_kpi():
    if not os.path.exists(KPI_PATH): return None
    try: return json.load(open(KPI_PATH,"r",encoding="utf-8"))
    except Exception: return None

def _guard(code,strat,new_theta):
    k=_load_kpi()
    if not k: return True
    ks=k.get(code,{}).get(strat,{})
    pf=ks.get("pf_7d"); ev=ks.get("ev_ticks_7d")
    if pf is None or ev is None: return True
    # 弱い時は過剰な締め(θ↑)を抑制
    if pf<1.05 or ev<0.10:
        return new_theta<=0.66
    return True

def update_after_trade(trade: dict, log=print):
    """
    trade 例:
      {"code":"7011","strategy":"SCALP","pnl_ticks":+2,"pnl":+200.0}
    ※後方互換: "pnl"のみでも可（tick不明時は±で±0.01調整）
    """
    code=trade.get("code"); strat=trade.get("strategy","SCALP")
    if not code: return
    pnl_ticks=trade.get("pnl_ticks")
    pnl=trade.get("pnl",0.0)

    m=_load(code)
    tb=dict(m.get("theta_by_strategy",{"SCALP":0.64,"MINI_SWING":0.62}))
    old=float(tb.get(strat,0.62))

    if isinstance(pnl_ticks,(int,float)):
        step=0.01 if pnl_ticks>0 else (-0.01 if pnl_ticks<0 else 0.0)
    else:
        step=0.01 if pnl>0 else (-0.01 if pnl<0 else 0.0)

    new=max(0.55,min(0.75,old+step))
    m["reward_sum"]=float(m.get("reward_sum",0.0)+(pnl_ticks if isinstance(pnl_ticks,(int,float)) else 0.0))

    if not _guard(code,strat,new):
        log(f"[OnlineLearn] {code}/{strat} theta unchanged (guard) old={old:.2f}")
        _save(code,m)
        return {"code":code,"strategy":strat,"theta_old":old,"theta_new":old,"applied":False}

    tb[strat]=new; m["theta_by_strategy"]=tb
    _save(code,m)
    log(f"[OnlineLearn] {code}/{strat} theta {old:.2f}->{new:.2f}")
    return {"code":code,"strategy":strat,"theta_old":old,"theta_new":new,"applied":True}


