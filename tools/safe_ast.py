"""Compile only fingerprinted pure function nodes; never import legacy modules."""
import ast
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE_CALL_NAMES = {"max", "min", "float", "int", "bool", "abs", "len", "round"}
SAFE_CALL_ATTRIBUTES = {"get", "_scale01"}

def load_function(relative_path, qualified_name, root=None):
    root = Path(root or ROOT).resolve()
    manifest = json.loads((root / "docs/safe-functions.json").read_text(encoding="utf-8"))
    record = next((x for x in manifest["functions"] if x["path"] == relative_path and x["qualified_name"] == qualified_name), None)
    if record is None:
        raise ValueError("Function is outside the reviewed allowlist")
    path = (root / relative_path).resolve()
    if path.parent != root or path.is_symlink():
        raise ValueError("Unexpected source path")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    body = tree.body
    for name in qualified_name.split("."):
        node = next(n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name)
        body = node.body
    if not isinstance(node, ast.FunctionDef) or node.decorator_list:
        raise ValueError("Only undecorated reviewed functions are allowed")
    digest = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
    if digest != record["ast_sha256"]:
        raise ValueError("Reviewed function fingerprint changed")
    function = copy.deepcopy(node)
    function.returns = None
    for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]:
        arg.annotation = None
    if function.args.vararg:
        function.args.vararg.annotation = None
    if function.args.kwarg:
        function.args.kwarg.annotation = None
    for child in ast.walk(function):
        if isinstance(child, (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal, ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom)):
            raise ValueError("Unsafe AST construct")
        if isinstance(child, ast.Attribute) and child.attr.startswith("__"):
            raise ValueError("Dunder attribute is forbidden")
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in SAFE_CALL_NAMES:
                continue
            if isinstance(child.func, ast.Attribute) and child.func.attr in SAFE_CALL_ATTRIBUTES:
                continue
            raise ValueError("Call outside the pure function allowlist")
    safe_builtins = {name: {"max": max, "min": min, "float": float, "int": int, "bool": bool, "abs": abs, "len": len, "round": round}[name] for name in SAFE_CALL_NAMES}
    namespace = {"__builtins__": safe_builtins}
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, "<reviewed-pure:" + relative_path + ":" + qualified_name + ">", "exec"), namespace)
    return namespace[function.name]
