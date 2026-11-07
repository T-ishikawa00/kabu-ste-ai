"""Approved stdlib-only entry point; application modules are never imported."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
tempfile.gettempdir()
sys.path.insert(0, str(ROOT))
LEGACY = {p.stem for p in ROOT.glob("*.py")}
BLOCKED = LEGACY | {"numpy", "pandas", "torch", "stable_baselines3", "requests", "websocket", "google", "pickle", "shelve", "ctypes", "pyautogui", "tkinter", "smtplib", "webbrowser"}
attempts = []

def deny(category):
    attempts.append(category)
    raise RuntimeError("Safe review guard blocked: " + category)

def guard(event, args):
    if event.startswith("socket."):
        deny("network:" + event)
    if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork"}:
        deny("process:" + event)
    if event.startswith("ctypes."):
        deny("native:" + event)
    if event == "import":
        name = str(args[0]).split(".")[0]
        if name in BLOCKED or (name not in sys.stdlib_module_names and name != "tools" and not name.startswith("test_safe_")):
            deny("forbidden_import:" + name)
    if event == "open" and isinstance(args[0], (str, bytes)):
        name = os.fsdecode(args[0]).replace("\\", "/").lower()
        parts = name.split("/")
        if any(p.startswith(".env") and p != ".env.example" or p in {"credentials.json", "token.pickle", "config.yaml"} for p in parts):
            deny("real_configuration_or_credentials")
        if name.endswith((".pkl", ".pickle", ".pt", ".pth", ".zip")) or any(p in {"model", "models", "runtime", "preservation.git", "data", "data_raw", "logs"} for p in parts):
            deny("private_asset_read")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["audit", "tests", "simulate", "selfcheck"])
    mode = parser.parse_args().mode
    sys.addaudithook(guard)
    if mode == "selfcheck":
        events = [("socket.connect", (None, ("127.0.0.1", 9))), ("socket.connect", (None, ("192.0.2.1", 9))), ("subprocess.Popen", ("synthetic", [], None, None)), ("open", ("/tmp/credentials.json", "r", 0)), ("open", ("/tmp/model/sample.pkl", "r", 0)), ("import", ("controller", None, None, None, None))]
        for event, args in events:
            try:
                sys.audit(event, *args)
            except RuntimeError:
                continue
            raise AssertionError("Guard failed to reject a deliberate control")
        print(json.dumps({"intentional_controls_blocked": len(attempts), "categories": attempts}))
        return 0
    if mode == "audit":
        from tools.source_audit import audit
        result = audit()
        result["forbidden_attempts"] = len(attempts)
        result["guard_categories"] = attempts
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(bool(result["errors_masked"]) or bool(attempts))
    elif mode == "tests":
        tests = unittest.TestLoader().discover(str(ROOT / "tests/safe"), pattern="test_safe_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        code = not result.wasSuccessful()
    else:
        from tools.offline_pipeline import main as simulate
        code = simulate()
    print(json.dumps({"mode": mode, "forbidden_attempts": len(attempts), "categories": attempts}))
    return 1 if attempts else int(code or 0)

if __name__ == "__main__":
    raise SystemExit(main())
