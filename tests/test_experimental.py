"""Opt-in and recently graduated checkers.

Deterministic test suite for grounded (stdlib unittest, no dependencies).
"""
from __future__ import annotations

import json
import random
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src/ is on sys.path when running tests without prior editable install
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from grounded.config import Config
from grounded.parsers import parse_file
from grounded.repo_index import RepoIndex
from grounded.reporters import to_html, to_json, to_sarif
from grounded.scanner import collect_files, scan_root


class TestStaleContractRef(unittest.TestCase):
    def _tree(self, root, files):
        pkg = root / "pkg"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        for name, text in files.items():
            (pkg / name).write_text(text, encoding="utf-8")

    def _scan(self, td, *enable):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["scan", td, "--no-color", "--enable", ",".join(enable)])
        return rc, buf.getvalue()

    def test_deprecation_target_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": "def get_account(uid):\n    return uid\n",
                "notes.py": "# DEPRECATED: use fetch_user_v2(user) instead.\nX = 1\n",
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 1)
            self.assertIn("[stale-contract-ref]", out)
            self.assertIn("fetch_user_v2", out)

    def test_deprecation_target_present_and_prose_silent(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": "def get_account(uid):\n    return uid\n",
                "notes.py": ("# DEPRECATED: use get_account(uid) instead.\n"
                             "# Just some prose about use instead of things.\n"
                             "# See https://example.com/t/1 for history.\nX = 1\n"),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 0)
            self.assertNotIn("stale-contract-ref", out)

    def test_lock_holder(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": ("# Caller must hold _state_lock.\n"
                             "# Caller must hold a reference.\n"
                             "class Store:\n"
                             "    def __init__(self):\n"
                             "        self._lock = 1\n"),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 1)
            self.assertIn("_state_lock", out)
            self.assertNotIn("a reference", out)

    def test_lock_present_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": ("# Caller must hold _lock.\n"
                             "class Store:\n"
                             "    def __init__(self):\n"
                             "        self._lock = 1\n"),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 0)

    def test_env_default_drift_and_match(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": ('import os\n\n# Default port is 8080 (override with PORT).\n'
                             'port = int(os.getenv("PORT", 3000))\n'),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 0)  # drift sits below the default lie gate
            self.assertIn("[stale-contract-ref]", out)
            self.assertIn("PORT", out)
            self.assertIn("8080", out)
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": ('import os\n\n# Default port is 3000 (override with PORT).\n'
                             'port = int(os.getenv("PORT", 3000))\n'),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 0)

    def test_off_by_default(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "notes.py": "# DEPRECATED: use fetch_user_v2(user) instead.\nX = 1\n",
            })
            self.assertEqual(main(["scan", td, "--no-color"]), 0)

    def test_uppercase_in_weak_frame_silent(self):
        # Bare "use X instead" naming SQL/platform builtins (JSON_TYPE,
        # COALESCE) is not a deprecation claim. Explicit deprecation of
        # a missing CONSTANT still fires.
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": ("# Extract with JSON_EXTRACT(), use JSON_TYPE() instead.\n"
                            "X = 1\n"),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 0)
            self.assertNotIn("stale-contract-ref", out)
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), {
                "core.py": ("# DEPRECATED: use Config.MAX_ITEMS instead.\n"
                            "LIMIT = 10\n"),
            })
            rc, out = self._scan(td, "stale-contract-ref")
            self.assertEqual(rc, 1)
            self.assertIn("MAX_ITEMS", out)


class TestGhostExport(unittest.TestCase):
    def _scan(self, td):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["scan", td, "--no-color", "--enable", "ghost-export"])
        return rc, buf.getvalue()

    def test_unused_public_function_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "def validate_legacy_token(tok):\n    return tok\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 0)  # smell never fails the default gate
            self.assertIn("[ghost-export]", out)
            self.assertIn("validate_legacy_token", out)

    def test_used_imported_and_private_silent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "def used():\n    return 1\n\ndef _private():\n    return 2\n\n"
                "class K:\n    def method(self):\n        return used()\n"
                "def aliased():\n    return 3\n",
                encoding="utf-8")
            (root / "b.py").write_text("from a import used, aliased as other\nprint(used(), other())\n",
                                       encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("ghost-export", out)

    def test_dunder_all_and_init_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(
                'def helper():\n    return 1\n__all__ = ["listed"]\n', encoding="utf-8")
            (pkg / "core.py").write_text(
                'def listed():\n    return 1\n__all__ = ["listed"]\n', encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("ghost-export", out)

    def test_js_export_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.js").write_text(
                "export function shipped() { return 1; }\n"
                "function internal() { return 2; }\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("internal", out)
            self.assertNotIn("shipped", out)

    def test_go_exported_and_methods_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.go").write_text(
                "package p\n\nfunc Exported() int { return 1 }\n\n"
                "func helper() int { return 2 }\n\n"
                "type S struct{}\nfunc (s S) Method() int { return 3 }\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("helper", out)
            self.assertNotIn("Exported", out)
            self.assertNotIn("Method", out)

    def test_go_sibling_use_silent(self):
        # Same-package cross-file calls need no import (seen: gin's
        # debugPrintRoute called from gin.go, defined in debug.go).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "debug.go").write_text(
                "package p\n\nfunc debugPrintRoute() {}\n", encoding="utf-8")
            (root / "gin.go").write_text(
                "package p\n\nfunc run() {\n\tdebugPrintRoute()\n}\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("debugPrintRoute", out)

    def test_go_build_tag_variants_silent(self):
        # Mutually exclusive //go:build variants (seen: grpc-go
        # binding.go vs binding_nomsgpack.go): flagging either deletes
        # a live build configuration.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.go").write_text(
                "package p\n\n//go:build !tagx\n\nfunc validate() int { return 1 }\n",
                encoding="utf-8")
            (root / "b.go").write_text(
                "package p\n\n//go:build tagx\n\nfunc validate() int { return 2 }\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("[ghost-export]", out)

    def test_module_attribute_use_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "helper.py").write_text(
                "def serve():\n    return 1\n", encoding="utf-8")
            (root / "main.py").write_text(
                "from pkg import helper\nhelper.serve()\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("ghost-export", out)

    def test_module_attribute_use_without_import_still_flags(self):
        # Same-named local, no module import: not evidence, still a ghost.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "helper.py").write_text(
                "def serve():\n    return 1\n", encoding="utf-8")
            (root / "main.py").write_text(
                "helper = object()\nhelper.serve()\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("[ghost-export]", out)

    def test_framework_test_entry_silent_helper_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "test_thing.py").write_text(
                "def test_old():\n    assert True\n\ndef make_ghost():\n    return 1\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("test_old", out)
            self.assertIn("make_ghost", out)


class TestStaleEntrypoint(unittest.TestCase):
    def _scan(self, td):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["scan", td, "--no-color", "--enable", "stale-entrypoint"])
        return rc, buf.getvalue()

    def test_pyproject_scripts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "cli.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\n[project.scripts]\n'
                'demo = "pkg.cli:main"\nold = "pkg.gone:run"\n'
                'renamed = "pkg.cli:execute"\n',
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 1)
            self.assertIn("pkg.gone", out)
            self.assertIn("execute", out)
            self.assertNotIn("[stale-entrypoint] Entry point `demo`", out)

    def test_package_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            js = root / "js"
            js.mkdir()
            (js / "cli.js").write_text("module.exports = {};\n", encoding="utf-8")
            (js / "package.json").write_text(
                '{"name": "demo", "bin": {"demo": "./cli.js", "old": "./missing.js"},'
                ' "main": "./dist/index.js"}\n',
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("missing.js", out)
            self.assertNotIn("dist/index.js", out)

    def test_on_by_default(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\n[project.scripts]\nold = "pkg.gone:run"\n',
                encoding="utf-8")
            self.assertEqual(main(["scan", td, "--no-color"]), 1)


class TestStaleMockRef(unittest.TestCase):
    def _scan(self, td):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["scan", td, "--no-color", "--enable", "stale-mock-ref"])
        return rc, buf.getvalue()

    def _tree(self, root):
        app = root / "app"
        (app / "services").mkdir(parents=True)
        (app / "__init__.py").write_text("", encoding="utf-8")
        (app / "services" / "__init__.py").write_text("", encoding="utf-8")
        (app / "services" / "billing.py").write_text(
            "def process_payment(amount):\n    return amount\n", encoding="utf-8")
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        return tests

    def test_stale_patch_fires(self):
        with tempfile.TemporaryDirectory() as td:
            tests = self._tree(Path(td))
            (tests / "test_billing.py").write_text(
                'from unittest.mock import patch\n'
                '@patch("app.services.billing.charge_card")\n'
                "def test_old(mock_c):\n    pass\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 1)
            self.assertIn("[stale-mock-ref]", out)
            self.assertIn("charge_card", out)

    def test_valid_external_create_silent(self):
        with tempfile.TemporaryDirectory() as td:
            tests = self._tree(Path(td))
            (tests / "test_billing.py").write_text(
                'from unittest.mock import patch\n'
                '@patch("app.services.billing.process_payment")\n'
                "def test_new(mock_p):\n    pass\n"
                '@patch("requests.get")\n'
                "def test_ext(mock_g):\n    pass\n"
                "def test_create():\n"
                '    with patch("app.services.billing.nope", create=True):\n'
                "        pass\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 0)
            self.assertNotIn("stale-mock-ref", out)

    def test_patch_object(self):
        # Bare patch.object verifies the OBJECT path only; the attr is a
        # method/meta gap the snapshot cannot falsify (Django _meta,
        # proxies). A renamed class still fires (see below).
        with tempfile.TemporaryDirectory() as td:
            tests = self._tree(Path(td))
            (tests / "test_billing.py").write_text(
                'from unittest.mock import patch\n'
                'from app.services.billing import process_payment\n'
                'patch.object(process_payment, "nope")\n',
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 0)
            self.assertNotIn("stale-mock-ref", out)

    def test_patch_object_broken_class_fires(self):
        # The object path itself is judged: a renamed class breaks every
        # patch.object on it, and no other checker sees mock targets.
        with tempfile.TemporaryDirectory() as td:
            tests = self._tree(Path(td))
            (tests / "test_billing.py").write_text(
                'from unittest.mock import patch\n'
                'from app.services.billing import GoneClass\n'
                'patch.object(GoneClass, "meth")\n',
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 1)
            self.assertIn("GoneClass", out)

    def test_on_by_default(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            tests = self._tree(Path(td))
            (tests / "test_billing.py").write_text(
                '@patch("app.services.billing.charge_card")\nX = 1\n', encoding="utf-8")
            self.assertEqual(main(["scan", td, "--no-color"]), 1)


class TestPhantomPackage(unittest.TestCase):
    def _scan(self, td):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["scan", td, "--no-color", "--enable", "phantom-package"])
        return rc, buf.getvalue()

    def test_undeclared_is_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\ndependencies = ["requests"]\n', encoding="utf-8")
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "a.py").write_text(
                "import requests\nimport yaml\nimport os\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 0)  # drift sits below the lie gate
            self.assertIn("[phantom-package]", out)
            self.assertIn("yaml", out)
            self.assertNotIn("`requests` is imported", out)
            self.assertNotIn("`os` is imported", out)

    def test_requirements_extras_markers_includes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements-base.txt").write_text("pyyaml>=6\n", encoding="utf-8")
            (root / "requirements.txt").write_text(
                "# comment\n-r requirements-base.txt\npydantic[email]>=2; python_version > '3.9'\n",
                encoding="utf-8")
            (root / "a.py").write_text(
                "import yaml\nfrom pydantic import BaseModel\nprint(BaseModel)\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("phantom-package", out)

    def test_js_deps_and_builtins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                '{"dependencies": {"express": "^5"}}', encoding="utf-8")
            (root / "a.js").write_text(
                'import express from "express";\n'
                'import fs from "fs";\n'
                'import _ from "lodash";\n'
                'console.log(express, fs, _);\n',
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("lodash", out)
            self.assertNotIn("`express` is imported", out)
            self.assertNotIn("`fs` is imported", out)

    def test_self_dir_require_silent(self):
        # require('..') resolves to the parent package itself, not a
        # distribution (seen: express test/app.js).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text('{"name": "mylib"}', encoding="utf-8")
            testdir = root / "test"
            testdir.mkdir()
            (testdir / "app.js").write_text(
                "var mylib = require('..');\nconsole.log(mylib);\n", encoding="utf-8")
            rc, out = self._scan(td)
            self.assertNotIn("phantom-package", out)

    def test_off_by_default(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("import yaml\nprint(yaml)\n", encoding="utf-8")
            self.assertEqual(main(["scan", td, "--no-color"]), 0)

    def test_guarded_imports_silent(self):
        # try/except, TYPE_CHECKING, and version/platform conditionals
        # are compat imports that may legitimately fail (seen: 55 such
        # drifts on django backend drivers). Only the unguarded one fires.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
            (root / "a.py").write_text(
                "import sys\n"
                "try:\n"
                "    import yaml\n"
                "except ImportError:\n"
                "    yaml = None\n"
                "if sys.version_info >= (3, 99):\n"
                "    import tomli\n"
                "import requests\n"
                "print(sys, yaml, tomli, requests)\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("`requests` is imported", out)
            self.assertNotIn("yaml", out)
            self.assertNotIn("tomli", out)


class TestGhostExportSuppression(unittest.TestCase):
    def _scan(self, root):
        return scan_root(root, Config(enabled={"ghost-export"}))[0]

    def test_use_after_inline_block_comment_is_seen(self):
        # svelte html.js: `reg_exp_entity` is called on a line that opens
        # with `/** @param {any} entity_name */`. Blanking that whole line
        # as a comment hid the call, so a live function read as dead.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "html.js").write_text(
                "function reg_exp_entity(entity_name, is_attribute_value) {\n"
                "\treturn entity_name;\n"
                "}\n"
                "\n"
                "function get_entity_pattern(is_attribute_value) {\n"
                "\treturn Object.keys(entities).map(\n"
                "\t\t/** @param {any} entity_name */ (entity_name) => "
                "reg_exp_entity(entity_name, is_attribute_value)\n"
                "\t);\n"
                "}\n",
                encoding="utf-8")
            out = self._scan(root)
            self.assertEqual([f for f in out if f.claim == "`reg_exp_entity`"], [])

    def test_truly_unused_function_still_reports(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "dead.js").write_text(
                "function deadFunction(a) {\n\treturn a;\n}\n", encoding="utf-8")
            out = self._scan(root)
            self.assertTrue([f for f in out if f.claim == "`deadFunction`"])

    def test_generated_expected_output_is_silent(self):
        # svelte tests/snapshot/samples/*/_expected/: compiler output, not
        # authored surface. Reachability of generated code is unknowable.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "tests" / "snapshot" / "samples" / "x" / "_expected" / "client"
            d.mkdir(parents=True)
            (d / "index.svelte.js").write_text(
                "function $$render() {\n\treturn 1;\n}\n", encoding="utf-8")
            out = self._scan(root)
            self.assertEqual(out, [])

    def test_jest_snapshot_dir_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "__snapshots__"
            d.mkdir()
            (d / "a.test.js.snap.js").write_text(
                "function snapHelper() {\n\treturn 1;\n}\n", encoding="utf-8")
            out = self._scan(root)
            self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
