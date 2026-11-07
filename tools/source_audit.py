"""Read source as text/AST only; do not import application modules."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "known_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{30,})\b"),
    "notification_url": re.compile(rb"https://(?:discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services)/[^\s\"'<>]+"),
    "sensitive_literal": re.compile(rb"(?i)(?:password|passwd|passcode|api_key|apikey|client_secret|access_token|refresh_token|webhook_url)[\"']?\s*[:=]\s*[\"'][^\"'\r\n]{4,}[\"']"),
}

class MaskStrings(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            node.value = "<STRING>"
        return node

def audit(root=ROOT):
    root = Path(root)
    source_map = json.loads((root / "docs/source-map.json").read_text(encoding="utf-8"))
    files = source_map["retained_files"]
    errors = []
    imports = set()
    parsed = 0
    for entry in files:
        p = root / entry["target_path"]
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["latest_sha256"]:
            errors.append({"path": entry["target_path"], "category": "source_hash_changed"})
        for category, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                errors.append({"path": entry["target_path"], "category": category})
        if p.suffix == ".py":
            t = ast.parse(data.decode("utf-8-sig"), filename=entry["target_path"])
            parsed += 1
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    imports.update(a.name for a in n.names)
                if isinstance(n, ast.ImportFrom) and n.module:
                    imports.add(n.module)
            shape = hashlib.sha256(ast.dump(MaskStrings().visit(t), include_attributes=False).encode()).hexdigest()
            if shape != entry["logic_shape_sha256"]:
                errors.append({"path": entry["target_path"], "category": "logic_shape_changed"})
    broken = []
    for p in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", p.read_text(encoding="utf-8")):
            if re.match(r"[A-Za-z]+://", target) or target.startswith("#"):
                continue
            if not (p.parent / target.split("#")[0]).exists():
                broken.append({"path": p.relative_to(root).as_posix(), "target": target})
    errors.extend({**x, "category": "broken_link"} for x in broken)
    return {"scope": "legacy source syntax and byte/AST preservation, no imports", "source_files": len(files), "python_ast_parsed": parsed, "byte_identical_to_original": sum(x["bytes_identical"] for x in files), "redacted_source_files": sum(not x["bytes_identical"] for x in files), "declared_imports": sorted(imports), "third_party_import_roots": sorted({x.split(".")[0] for x in imports if x.split(".")[0] not in sys.stdlib_module_names and not (root / (x.split(".")[0] + ".py")).exists()}), "errors_masked": errors}

if __name__ == "__main__":
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["errors_masked"]))
