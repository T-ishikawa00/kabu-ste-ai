
# -*- coding: utf-8 -*-
"""
train_policy.py — ログから銘柄別PnLを集計して ModelRegistry を更新
- 対象期間: 環境変数 LOOKBACK_DAYS（日数, 既定=7）
- 入力: logs/run_YYYYMMDD.log （PAPER SELL 行の pnl= を集計）
- 出力: models/{code}.json の theta 等を微調整
"""

import os, re, json, glob, logging
from datetime import datetime, timedelta

MODELS_DIR = "models"           # model_registry.py が使う保存先と合わせる
LOG_GLOB   = "logs/run_*.log"   # main.py の出力先と合わせる

# ===== ログ設定 =====
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

# ===== ログからPnL集計 =====
SELL_RE = re.compile(
    r"(PAPER SELL|LIVE\s+SELL)\s+(\d+)"
    r".*?\s+pnl=(-?\d+(?:\.\d+)?)", re.IGNORECASE
)
# 例: "PAPER SELL 3778@1 x100 ... pnl=-500"

def collect_pnl(lookback_days: int):
    cutoff = datetime.now() - timedelta(days=lookback_days)
    per_symbol = {}  # code -> {"count": n, "pnl": sum}
    files = sorted(glob.glob(LOG_GLOB))
    used_files = 0

    for path in files:
        # run_YYYYMMDD.log から日付判定
        base = os.path.basename(path)
        m = re.match(r"run_(\d{8})\.log", base)
        if not m:
            continue
        day = datetime.strptime(m.group(1), "%Y%m%d")
        if day < cutoff:
            continue

        used_files += 1
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    mo = SELL_RE.search(line)
                    if not mo:
                        continue
                    _kind, code, pnl_s = mo.groups()
                    pnl = float(pnl_s)
                    d = per_symbol.setdefault(code, {"count": 0, "pnl": 0.0})
                    d["count"] += 1
                    d["pnl"] += pnl
        except Exception as e:
            logging.warning("log read error %s: %s", path, e)

    return per_symbol, used_files

# ===== モデルの読み書き =====
def load_model(code: str):
    path = os.path.join(MODELS_DIR, f"{code}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_model(code: str, model: dict):
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, f"{code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

# ===== 学習ロジック（超シンプルなヒューリスティック） =====
def adjust_model_from_pnl(code: str, stats: dict):
    """
    負けている銘柄はエントリーを厳しめに（theta を上げる）
    勝っている銘柄はやや緩めに（theta を下げる）
    増減幅は微小。範囲は [0.55, 0.75] にクリップ
    """
    pnl = stats["pnl"]
    cnt = stats["count"]

    model = load_model(code)
    theta = float(model.get("theta", 0.62))

    if cnt == 0:
        return None, None  # 変更なし

    if pnl < 0:
        theta += 0.01  # 厳しく
    elif pnl > 0:
        theta -= 0.005 # やや緩く

    theta = max(0.55, min(0.75, theta))
    model["theta"] = round(theta, 3)

    # 参考で重みも用意（将来の拡張用）
    model["w_buy"] = float(model.get("w_buy", 1.0)) * (0.999 if pnl < 0 else 1.001)
    model["w_sell"] = float(model.get("w_sell", 1.0)) * 1.0

    save_model(code, model)
    return model, pnl

def main():
    setup_logging()
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "7"))
    logging.info("=== train start (lookback_days=%d) ===", lookback_days)

    stats, used_files = collect_pnl(lookback_days)
    if used_files == 0:
        logging.warning("対象期間内のログが見つかりませんでした")
    else:
        logging.info("対象ログファイル数: %d", used_files)

    if not stats:
        logging.warning("売買の集計結果が空です（SELLログが無い可能性）")
        logging.info("終了")
        return

    logging.info("集計結果:")
    for code, d in sorted(stats.items()):
        logging.info("  code=%s trades=%d pnl=%.0f", code, d["count"], d["pnl"])

    logging.info("モデル更新:")
    for code, d in sorted(stats.items()):
        model, pnl = adjust_model_from_pnl(code, d)
        if model is None:
            continue
        logging.info("  %s -> theta=%.3f (pnl=%.0f, trades=%d)", code, model["theta"], d["pnl"], d["count"])

    logging.info("=== train done ===")

if __name__ == "__main__":
    main()
