"""stale-symbol-ref, stale-file-ref, number-drift, fragile-anchor.

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


class TestSymbolV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, files: dict[str, str], **cfg_kwargs):
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        config = Config(**cfg_kwargs) if cfg_kwargs else Config()
        findings, facts, index = scan_root(self.root, config)
        return findings

    def syms(self, files):
        return [f for f in self.scan(files) if f.checker == "stale-symbol-ref"]

    def test_backticked_call_missing_is_lie(self):
        out = self.syms({"a.py": "def real():\n    return 1\n",
                         "b.py": "# Calls `ghost_fn()` for help.\nX = 1\n"})
        self.assertTrue(any("ghost_fn" in f.title for f in out))

    def test_placeholder_xxx_is_silent(self):
        # Xxx/XXX is the protobuf placeholder convention (seen: grpc-go
        # testutils), never a real reference.
        out = self.syms({"a.py": "# Uses SendXxx() for delivery.\nX = 1\n"})
        self.assertEqual(out, [])

    def test_negated_backticked_call_silent(self):
        # A negated claim asserts absence ("no call to X"): flagging it
        # contradicts a true statement (seen: gh httpmock test comment).
        out = self.syms({"a.py": "# Notice there is no call to `ghost_fn()`.\nX = 1\n"})
        self.assertEqual(out, [])

    def test_imported_root_is_silent(self):
        out = self.syms({"a.py": "from http.cookiejar import CookieJar\n# Wraps CookieJar.clear().\nX = 1\n"})
        self.assertEqual(out, [])

    def test_stdlib_root_is_silent(self):
        out = self.syms({"a.py": "# See Tarfile.extractfile docs.\nX = 1\n",
                         "t.py": "import tarfile\nY = tarfile.open\n"})
        # Tarfile imported in t.py but NOT in a.py; stdlib rule still silences
        self.assertFalse([f for f in out if "Tarfile" in f.title or "extractfile" in f.title])

    def test_same_file_param_local_attr_silent(self):
        out = self.syms({"a.py": "def f(dict_class):\n    # merged using `dict_class`\n    rewindable = True\n    # `rewindable` guards retry\n    self._x = 1\n    # `_x` set above\n    return dict_class\n"})
        self.assertEqual(out, [])

    def test_sphinx_field_lines_scrubbed(self):
        out = self.syms({"a.py": 'def f(headers):\n    """Do.\n\n    :param headers: a mapping.\n    """\n    return headers\n'})
        self.assertEqual(out, [])

    def test_backticked_field_name_silent(self):
        out = self.syms({"a.py": "# `status_code` below 400 is success.\nstatus_code = 200\n"})
        self.assertEqual(out, [])

    def test_dunder_typo_is_lie_with_suggestion(self):
        out = self.syms({"a.py": "def __getitem__(self, k):\n    return k\n",
                         "b.py": "# Both ``__get_item__`` and get use this.\nX = 1\n"})
        self.assertTrue(any("__get_item__" in f.title for f in out))
        hit = [f for f in out if "__get_item__" in f.title][0]
        self.assertIn("__getitem__", hit.fix)

    def test_common_dunder_silent(self):
        out = self.syms({'a.py': '# Run with `__main__` guard.\nif __name__ == "__main__":\n    pass\n'})
        self.assertEqual(out, [])

    def test_bare_call_with_verb_is_lie(self):
        out = self.syms({"a.py": "def real():\n    return 1\n",
                         "b.py": "# Calls fetch_user_data() and formats.\nX = 1\n"})
        self.assertTrue(any("fetch_user_data" in f.title for f in out))

    def test_bare_call_without_verb_silent(self):
        out = self.syms({"a.py": "# _fix_ie_filename() edge cases noted.\nX = 1\n"})
        self.assertEqual(out, [])

    def test_builtin_and_keyword_silent(self):
        out = self.syms({"a.py": "# Uses print() for output.\nX = 1\n",
                         "b.js": "// method without `function` keyword\nconst x = 1;\n"})
        self.assertEqual(out, [])

    def test_defined_symbol_silent(self):
        out = self.syms({"a.py": "def real_thing():\n    return 1\n",
                         "b.py": "# Calls `real_thing()` for help.\nX = 1\n"})
        self.assertEqual(out, [])

    def test_js_relative_import_silent(self):
        out = self.syms({"a.js": 'import { helper } from "./real.js";\n// Uses `helper()`.\nconst x = 1;\n',
                         "real.js": "export function helper(x) { return x; }\n"})
        self.assertEqual(out, [])

    def test_js_same_file_ident_silent(self):
        out = self.syms({"a.js": "// `socketPath` option noted.\nconst opts = { socketPath: '/x' };\n"})
        self.assertEqual(out, [])

    def test_module_level_assignment_known(self):
        out = self.syms({"a.py": "from lazy import lazy\n",
                         "b.py": "gettext_lazy = 1\n",
                         "c.py": "# The input is the result of a gettext_lazy() call.\nX = 1\n"})
        # gettext_lazy assigned at module level in b.py -> repo-known
        self.assertEqual(out, [])

    def test_stdlib_class_form_silent(self):
        out = self.syms({"a.py": "# See `Tarfile.extractfile()` docs.\nX = 1\n"})
        self.assertEqual(out, [])

    def test_screaming_bare_call_silent(self):
        out = self.syms({"a.py": "# Don't use the built-in RANDOM() function here.\nX = 1\n",
                         "b.py": "# The use of VALUES() is not supported.\nY = 2\n"})
        self.assertEqual(out, [])

    def test_negated_bare_call_silent(self):
        out = self.syms({"a.py": "# arrays are cast and byref() calls are not needed.\nX = 1\n"})
        self.assertEqual(out, [])

    def test_rename_candidate_still_fires(self):
        out = self.syms({"a.py": "def test_max_cookie_length():\n    pass\n",
                         "b.py": "# see comment in CookieTests.test_cookie_max_length()\nX = 1\n"})
        self.assertTrue(any("test_cookie_max_length" in f.title for f in out))


class TestFileV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, files: dict[str, str]):
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return scan_root(self.root, Config())[0]

    def files(self, files):
        return [f for f in self.scan(files) if f.checker == "stale-file-ref"]

    def test_in_scope_missing_is_lie(self):
        out = self.files({"src/keep.py": "X = 1\n",
                          "a.py": "# See src/gone.py for details.\nY = 2\n"})
        self.assertTrue(any("src/gone.py" in f.title for f in out))

    def test_in_scope_existing_silent(self):
        out = self.files({"src/keep.py": "X = 1\n",
                          "a.py": "# See src/keep.py for details.\nY = 2\n"})
        self.assertEqual(out, [])

    def test_illustrative_eg_silent(self):
        # e.g./i.e. markers introduce examples, not claims (seen: gh
        # skill-convention doc comment). Without placeholder segments,
        # so this isolates the illustrative guard itself.
        out = self.files({"pkg/exists.py": "X = 1\n",
                          "a.py": "# e.g. pkg/missing/file.py\nY = 2\n"})
        self.assertEqual(out, [])

    def test_out_of_scope_silent(self):
        # dependency modules, template namespaces, external sources:
        # the snapshot cannot decide them, so they must stay quiet.
        out = self.files({"src/a.py": "X = 1\n",
                          "a.py": ("# See MySQLdb/cursors.py.\n"
                                   "# See flatpages/default.html.\n"
                                   "# See Modules/_sqlite/connection.c.\n"
                                   "Y = 2\n")})
        self.assertEqual(out, [])

    def test_placeholder_silent(self):
        out = self.files({"src/a.py": "X = 1\n",
                          "a.py": "# e.g. myapp/css/base.css\nY = 2\n"})
        self.assertEqual(out, [])

    def test_protocol_version_silent(self):
        out = self.files({"src/a.py": "X = 1\n",
                          "a.py": "# HTTP/1.1 requires persistence.\nY = 2\n"})
        self.assertEqual(out, [])

    def test_illustrative_example_silent(self):
        out = self.files({"src/a.py": "X = 1\n",
                          "a.py": "# For example, see src/news/photos.py for layout.\nY = 2\n"})
        self.assertEqual(out, [])

    def test_moved_path_fires_with_candidates(self):
        out = self.files({"src/real/deep.py": "X = 1\n",
                          "a.py": "# See src/old/deep.py for details.\nY = 2\n"})
        self.assertTrue(any("src/old/deep.py" in f.title for f in out))
        hit = [f for f in out if "src/old/deep.py" in f.title][0]
        self.assertIn("src/real/deep.py", hit.evidence)

    def test_ticketed_history_note_silent(self):
        out = self.files({"src/a.py": "X = 1\n",
                          "a.py": ("# These symbols used to be in src/old/stuff.py\n"
                                   "# before the reorganization (See #10355)\nY = 2\n")})
        self.assertEqual(out, [])


class TestNumberAndAnchor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, files: dict[str, str]):
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return scan_root(self.root, Config())[0]

    def test_number_drift(self):
        out = self.scan({"a.py": "# timeout is 30s\ntimeout = 60\n"})
        self.assertTrue(any(f.checker == "number-drift" for f in out))

    def test_number_agree_silent(self):
        out = self.scan({"a.py": "# timeout is 60s\ntimeout = 60\n"})
        self.assertFalse([f for f in out if f.checker == "number-drift"])

    def test_line_anchor_smell(self):
        out = self.scan({"a.py": "# See line 42 for logic.\nX = 1\n"})
        self.assertTrue(any(f.checker == "fragile-anchor" for f in out))

    def test_ticketed_workaround_silent(self):
        out = self.scan({"a.py": "# HACK(#123): skip until v2\nX = 1\n"})
        self.assertFalse([f for f in out if f.checker == "fragile-anchor"])

    def test_prose_not_commented_code_cascade(self):
        # 3 prose lines must not suppress their own symbol findings
        out = self.scan({"a.py": "def real():\n    return 1\n",
                         "b.py": ("# Calls `ghost_xyz()` when ready.\n"
                                  "# timeout is 30s for dialing.\n"
                                  "# See line 9 for policy.\ntimeout = 60\n")})
        kinds = {(f.checker) for f in out}
        self.assertIn("stale-symbol-ref", kinds)
        self.assertIn("number-drift", kinds)


class TestRemovedCheckers(unittest.TestCase):
    def test_no_removed_ids_emitted(self):
        removed = {"param-mismatch", "raises-mismatch", "return-mismatch", "commented-code"}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                'def f(timeout):\n    """Do.\n\n    Args:\n        host: missing.\n    """\n'
                '    # result = compute(1)\n    # total = result + 2\n    # print(total)\n    return 1\n',
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertFalse([f for f in findings if f.checker in removed])

    def test_explain_points_to_incumbents(self):
        from grounded.cli import main
        for cid in ["param-mismatch", "raises-mismatch", "return-mismatch", "commented-code"]:
            self.assertEqual(main(["explain", cid]), 0)


class TestFileRefIgnoresBuildOutputs(unittest.TestCase):
    def test_build_output_paths_are_silent(self):
        """`dist/`, `build/`, `coverage/` are never indexed, so a comment
        naming a path there cannot be judged missing (measured: the
        dominant `stale-file-ref` shape on a real monorepo)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "cli.js").write_text(
                "// Writes the bundle to `dist/index.cjs` for the loader.\n",
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertEqual([f for f in findings if f.checker == "stale-file-ref"], [])

    def test_elided_paths_are_placeholders(self):
        """`src/.../File.tsx` is shorthand, not a claim: the `...` is the
        placeholder. The segment split turned it into empty strings, so the
        placeholder list never matched it."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "a.ts").write_text(
                "// See `src/.../EndpointPageClient.tsx` for the flow.\n",
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertEqual([f for f in findings if f.checker == "stale-file-ref"], [])

    def test_missing_authored_path_still_reports(self):
        # Control: the guards must not disarm ordinary path claims.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text(
                'def f():\n    """See `src/gone.py` for the old flow."""\n',
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertTrue([f for f in findings if f.checker == "stale-file-ref"])


if __name__ == "__main__":
    unittest.main()
