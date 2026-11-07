"""Critical extraction boundary: no legacy import or module-level execution."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tools.safe_ast import load_function

class SafeExtractionBoundaryTests(unittest.TestCase):
    def test_rejects_legacy_constructor_and_path_outside_allowlist(self):
        for path, name in [("controller.py", "Controller.__init__"), ("../features.py", "_norm")]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                load_function(path, name)

    def test_extracts_only_definition_and_rejects_changed_fingerprint(self):
        source = "def normalize(x):\n    return float(x)\n"
        fn = ast.parse(source).body[0]
        digest = hashlib.sha256(ast.dump(fn, include_attributes=False).encode()).hexdigest()
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "docs").mkdir()
            (root / "docs/safe-functions.json").write_text(json.dumps({"functions": [{"path": "fixture.py", "qualified_name": "normalize", "ast_sha256": digest}]}), encoding="utf-8")
            path = root / "fixture.py"
            path.write_text("raise AssertionError('module top level executed')\n" + source, encoding="utf-8")
            self.assertEqual(load_function("fixture.py", "normalize", root=root)(3), 3.0)
            path.write_text("def normalize(x):\n    return 99.0\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_function("fixture.py", "normalize", root=root)
