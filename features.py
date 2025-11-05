from __future__ import annotations
import numpy as np

def _norm(x, lo, hi):
    if hi == lo: return 0.0
    v = (x - lo)/float(hi-lo)
    return float(max(0.0, min(1.0, v)))

def entry_feature_vector(ctx):
    return np.array([
        getattr(ctx, "obi_median_0_5s", 0.0),
        getattr(ctx, "spread_ticks", 1.0),
        getattr(ctx, "vol5", 0.0) / 12000.0,
        getattr(ctx, "upticks", 0.0) / 8.0,
        getattr(ctx, "delta_ticks_2s", 0.0) / 3.0,
        _norm(getattr(ctx, "board_rtt_ms", 100.0), 0, 80),
        _norm(getattr(ctx, "cur_px", 0.0) - getattr(ctx, "recent_low_2s", 0.0), 0, getattr(ctx, "tick_size",1.0)*3),
        _norm(getattr(ctx, "recent_high_2s", 0.0) - getattr(ctx, "cur_px", 0.0), 0, getattr(ctx, "tick_size",1.0)*3),
        1.0,
    ], dtype=float).reshape((-1,1))

def exit_feature_vector(ctx, pos):
    tick = getattr(ctx, "tick_size", 1.0)
    ep = pos.get("entry", getattr(ctx, "cur_px", 0.0))
    cur = getattr(ctx, "cur_px", ep)
    u_pnl = (cur - ep) / tick
    return np.array([
        getattr(ctx, "obi_median_0_5s", 0.0),
        getattr(ctx, "spread_ticks", 1.0),
        getattr(ctx, "vol5", 0.0) / 12000.0,
        getattr(ctx, "upticks", 0.0) / 8.0,
        getattr(ctx, "delta_ticks_2s", 0.0) / 3.0,
        _norm(getattr(ctx, "board_rtt_ms", 100.0), 0, 80),
        float(pos.get("be_armed", False)),
        _norm(u_pnl, -2.0, 3.0),
        _norm((getattr(ctx, "now_ms", 0)-pos.get("ts_entry_ms",0))/1000.0, 0, 20),
        1.0,
    ], dtype=float).reshape((-1,1))
