# -*- coding: utf-8 -*-
"""
migrate_models_71_to_73_safe.py
- 対象: models\[0-9]{4}.json （コード4桁の純粋モデルのみ）
- 既に theta_by_strategy があればスキップ
- JSONL/複合JSON/コメント混入は 1行目のみ試してダメならスキップ
- 失敗しても他ファイルは続行
"""
import os, re, json, glob, time

MODELS_DIR = "models"
PTRN = re.compile(r".*[\\/](\d{4})\.json$", re.I)

def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{int(time.time()*1000)}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _try_load(path):
    # まずフルで読む
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    # 純JSONを試す
    try:
        return json.loads(txt)
    except Exception:
        pass
    # 1行目だけ（JSONL/複合）の救済
    try:
        first = txt.splitlines()[0].strip()
        return json.loads(first)
    except Exception:
        return None

def main():
    files = [p for p in glob.glob(os.path.join(MODELS_DIR, "*.json")) if PTRN.match(p)]
    migrated = 0; skipped = 0; failed = 0
    for p in sorted(files):
        m = _try_load(p)
        if not isinstance(m, dict):
            print(f"SKIP (not dict or parse fail): {p}"); failed += 1; continue
        if "theta_by_strategy" in m:
            print(f"SKIP (already migrated): {p}"); skipped += 1; continue
        th = m.get("theta")
        if th is None:
            print(f"SKIP (no 'theta' root key): {p}"); skipped += 1; continue
        try:
            th = float(th)
        except Exception:
            print(f"SKIP (theta not float): {p}"); skipped += 1; continue

        m["theta_by_strategy"] = {
            "SCALP": max(0.55, min(0.75, th + 0.02)),
            "MINI_SWING": th
        }
        # 旧キーは残さない
        if "theta" in m: m.pop("theta")
        _atomic_write(p, m)
        print(f"MIGRATED: {p}")
        migrated += 1

    print(f"Done. migrated={migrated} skipped={skipped} failed={failed}")

if __name__ == "__main__":
    main()
