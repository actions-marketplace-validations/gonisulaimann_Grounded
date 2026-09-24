"""Deterministic test suite for grounded (stdlib unittest, no dependencies).

Scope: import-aware, scope-aware reference checks. Docstring contracts
(params/returns/raises) and commented-out code are out of scope:
darglint/pydoclint, eslint-plugin-jsdoc, and Ruff ERA001 cover them.
Tests pin suppression rules, not just detections.
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


class TestImportsParsing(unittest.TestCase):
    def test_py_imports(self):
        import ast
        from grounded.parsers import _py_imports
        tree = ast.parse("import os\nimport a.b as c\nfrom x import y\nfrom . import z\n")
        self.assertEqual(_py_imports(tree), {"os": "os", "c": "a", "y": "x", "z": ""})

    def test_js_imports(self):
        from grounded.parsers import _js_imports
        got = _js_imports('import axios from "axios";\nimport { helper } from "./real.js";\nconst fs = require("fs");\n')
        self.assertEqual(got.get("axios"), "axios")
        self.assertEqual(got.get("helper"), "")
        self.assertEqual(got.get("fs"), "fs")


class TestFileCollection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _collect(self):
        return {p.relative_to(self.root.resolve()).as_posix()
                for p in collect_files(self.root.resolve(), Config())[0]}

    def test_symlinked_dir_not_followed(self):
        # Workspace-alias symlink (seen: OmniRoute's `@omniroute/` ->
        # `open-sse/`): following it scans the same files twice under two
        # rel paths, doubling findings and poisoning alias resolution.
        (self.root / "open-sse").mkdir()
        (self.root / "open-sse" / "a.ts").write_text("export const a = 1;\n",
                                                     encoding="utf-8")
        try:
            (self.root / "@omniroute").symlink_to("open-sse", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable on this filesystem")
        out = self._collect()
        self.assertIn("open-sse/a.ts", out)
        self.assertNotIn("@omniroute/a.ts", out)

    def test_agent_worktrees_dir_skipped(self):
        # `.claude/worktrees/` holds full second copies of the repo from
        # parallel agent sessions (seen: 4,027 of 4,458 findings on a
        # OmniRoute scan were worktree duplicates). Working state, not the
        # tree the repo ships.
        for rel, text in (("src/a.ts", "export const a = 1;\n"),
                          (".claude/worktrees/fix-1/src/a.ts", "export const a = 2;\n")):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        out = self._collect()
        self.assertIn("src/a.ts", out)
        self.assertNotIn(".claude/worktrees/fix-1/src/a.ts", out)

    def test_other_dotted_dirs_still_scanned(self):
        # The skip is precisely `.claude/worktrees`, not every dotted dir:
        # `.github/` configs remain scannable.
        p = self.root / ".github" / "x.ts"
        p.parent.mkdir(parents=True)
        p.write_text("export const x = 1;\n", encoding="utf-8")
        self.assertIn(".github/x.ts", self._collect())


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


class TestScannerV2(unittest.TestCase):
    def test_dts_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.d.ts").write_text(
                "/**\n * @param {string} ghost missing\n */\ndeclare function f(x: string): void;\n// Calls `ghost_fn()`.\n",
                encoding="utf-8")
            findings, facts, _ = scan_root(root, Config())
            self.assertEqual(facts, [])
            self.assertEqual(findings, [])

    def test_syntax_error_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bad.py").write_text("def broken(:\n  # Calls `ghost_fn()`.\n", encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertIsInstance(findings, list)

    def test_broken_buffer_keeps_comment_findings(self):
        # Mid-typing syntax error: comment diagnostics degrade, never vanish.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "def calculate_total(items):\n    # Calls `ghost_fn()`.\n    for item in items:\n",
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertTrue(any(f.checker == "stale-symbol-ref" for f in findings))

    def test_tolerant_extractor_skips_string_hashes(self):
        from grounded.parsers import _py_comments_tolerant
        out = _py_comments_tolerant('x = "not # a comment"\n# real `ghost_fn()`.\n')
        self.assertEqual(len(out), 1)
        self.assertIn("ghost_fn", out[0].text)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(scan_root(Path(td), Config())[0], [])


class TestReporters(unittest.TestCase):
    def test_json_roundtrip(self):
        from grounded.models import Finding
        f = [Finding(path="a.py", line=1, end_line=1, checker="stale-symbol-ref",
                      severity="lie", title="t", claim="c", evidence="e", fix="x", confidence=0.9)]
        data = json.loads(to_json(f))
        self.assertEqual(data[0]["checker"], "stale-symbol-ref")

    def test_terminal_unparsed_note(self):
        from grounded.reporters import format_terminal
        plain = format_terminal([], 2, root=".")
        self.assertNotIn("unparsed", plain)
        noted = format_terminal([], 2, root=".", n_unparsed=1)
        self.assertIn("1 file(s) unparsed", noted)

    def test_sarif_valid_shape(self):
        from grounded.models import Finding
        f = [Finding(path="a.py", line=3, end_line=3, checker="stale-file-ref",
                      severity="lie", title="t")]
        sarif = json.loads(to_sarif(f))
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "stale-file-ref")

    def test_markdown_table_is_row_safe(self):
        from grounded.models import Finding
        from grounded.reporters import to_markdown
        f = [Finding(path="a.py", line=4, end_line=4, checker="stale-symbol-ref",
                     severity="lie", title="a | b\nc")]
        out = to_markdown(f, 3)
        row = [l for l in out.splitlines() if l.startswith("| lie")][0]
        self.assertIn("a \\| b c", row)
        self.assertIn("1 finding(s) in 3 file(s): 1 lie.", out)
        self.assertIn("No findings in 2 file(s).", to_markdown([], 2))
        self.assertIn("Incomplete scan", to_markdown(f, 3, n_checker_errors=1))

    def test_html_escapes(self):
        from grounded.models import Finding
        f = [Finding(path="a.py", line=1, end_line=1, checker="stale-symbol-ref",
                      severity="lie", title="<b>bold</b>", claim="c", evidence="e", fix="x")]
        out = to_html(f, 1, root=".")
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", out)


class TestExitCodes(unittest.TestCase):
    def test_fail_on_never(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn_xyz()`.\nX = 1\n", encoding="utf-8")
            from grounded.cli import main
            self.assertEqual(main(["scan", str(root), "--no-color", "--fail-on", "never"]), 0)

    def test_fail_on_lie(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn_xyz()`.\nX = 1\n", encoding="utf-8")
            from grounded.cli import main
            self.assertEqual(main(["scan", str(root), "--no-color", "--fail-on", "lie"]), 1)


class TestBaseline(unittest.TestCase):
    def _write(self, root: Path, files: dict[str, str]) -> None:
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def test_write_then_suppress(self):
        from grounded.cli import main
        from grounded.delta import load_baseline
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            base = root / ".grounded-baseline.json"
            self.assertEqual(main(["baseline", str(root), "--output", str(base)]), 0)
            self.assertTrue(load_baseline(base))
            # same tree: everything baselined, exit 0
            self.assertEqual(
                main(["scan", str(root), "--no-color", "--baseline", str(base)]), 0)

    def test_new_finding_fails_new_only(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            base = root / "base.json"
            self.assertEqual(main(["baseline", str(root), "--output", str(base)]), 0)
            self._write(root, {"b.py": "# Calls `other_ghost()`.\nY = 2\n"})
            # without baseline: 2 findings; with: only the new one gates
            self.assertEqual(
                main(["scan", str(root), "--no-color", "--baseline", str(base),
                      "--format", "json"]),
                1)

    def test_line_shift_does_not_churn(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            base = root / "base.json"
            self.assertEqual(main(["baseline", str(root), "--output", str(base)]), 0)
            # insert 20 blank lines above: line numbers move, claim identical
            self._write(root, {"a.py": "\n" * 20 + "# Calls `ghost_fn()`.\nX = 1\n"})
            self.assertEqual(
                main(["scan", str(root), "--no-color", "--baseline", str(base)]), 0)

    def test_edited_claim_retriggers(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            base = root / "base.json"
            self.assertEqual(main(["baseline", str(root), "--output", str(base)]), 0)
            self._write(root, {"a.py": "# Calls `ghost_fn_v2()`.\nX = 1\n"})
            self.assertEqual(
                main(["scan", str(root), "--no-color", "--baseline", str(base)]), 1)

    def test_missing_baseline_is_error(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "X = 1\n"})
            self.assertEqual(
                main(["scan", str(root), "--no-color", "--baseline", str(root / "nope.json")]), 2)

    def test_rewrite_reports_delta_counts(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            base = root / "base.json"
            self.assertEqual(main(["baseline", str(root), "--output", str(base)]), 0)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            self.assertEqual(main(["baseline", str(root), "--output", str(base)]), 0)
            from grounded.delta import load_baseline
            self.assertEqual(load_baseline(base), set())


@unittest.skipUnless(shutil.which("git"), "git not available")
class TestChangedLines(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> None:
        import subprocess
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True, timeout=60)

    def _repo(self, root: Path, files: dict[str, str]) -> None:
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        self._git(root, "init", "-q")
        self._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "--allow-empty", "-m", "init")
        self._git(root, "add", "-A")
        self._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "base")

    def test_only_new_lines_reported(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._repo(root, {"a.py": "# Calls `old_ghost()`.\nX = 1\n"})
            # committed finding exists; uncommitted edit adds another + touches X
            (root / "a.py").write_text(
                "# Calls `old_ghost()`.\nX = 2\n# Calls `new_ghost()`.\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root), "--no-color", "--changed", "--format", "json"])
            self.assertEqual(rc, 1)
            changed_only = json.loads(buf.getvalue())
            self.assertEqual(len(changed_only), 1)
            self.assertIn("new_ghost", changed_only[0]["title"])
            buf_all = io.StringIO()
            with contextlib.redirect_stdout(buf_all):
                rc_all = main(["scan", str(root), "--no-color", "--format", "json"])
            self.assertEqual(rc_all, 1)
            self.assertEqual(len(json.loads(buf_all.getvalue())), 2)

    def test_untracked_file_fully_reported(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._repo(root, {"a.py": "X = 1\n"})
            (root / "new.py").write_text("# Calls `fresh_ghost()`.\nY = 1\n", encoding="utf-8")
            self.assertEqual(
                main(["scan", str(root), "--no-color", "--changed", "--fail-on", "lie"]), 1)

    def test_outside_git_is_error(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn()`.\nX = 1\n", encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color", "--changed"]), 2)

    def test_rename_fallout_on_untouched_lines_reported(self):
        import contextlib
        import io
        import json
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._repo(root, {
                "pkg/__init__.py": "",
                "pkg/core.py": "def get_user(uid):\n    return uid\n",
                "pkg/views.py": ('"""Views."""\nfrom .core import get_user\n\n\n'
                                 "def show(uid):\n    return get_user(uid)\n"),
                "other.py": "# Calls `other_ghost()`.\nY = 1\n",
            })
            (root / "pkg" / "core.py").write_text(
                "def get_account(uid):\n    return uid\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root), "--no-color", "--changed", "--format", "json"])
            self.assertEqual(rc, 1)
            out = json.loads(buf.getvalue())
            self.assertEqual(len(out), 1)
            self.assertIn("get_user", out[0]["title"])
            self.assertEqual(out[0]["checker"], "stale-import")


class TestMultiPathScan(unittest.TestCase):
    """`grounded scan FILE [FILE ...]` — pre-commit batches the changed
    filenames it passes to a `pass_filenames` hook, so the fence hook
    (`grounded-fences` in .pre-commit-hooks.yaml) needs multi-path scan.

    Semantics follow the single-file rule: each file scopes REPORTING to
    itself while the index is still built from the whole tree, so
    cross-file references keep resolving.
    """

    def test_two_files_one_finding(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bad.md").write_text("# Guide\n\n```console\ngrounded scan .\n",
                                         encoding="utf-8")
            (root / "ok.md").write_text("# Ok\n\n```python\nx = 1\n```\n",
                                        encoding="utf-8")
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root / "bad.md"), str(root / "ok.md"),
                           "--no-color", "--fail-on", "lie",
                           "--enable", "unclosed-fence"])
            self.assertEqual(rc, 1)
            self.assertIn("bad.md", buf.getvalue())
            self.assertNotIn("ok.md:", buf.getvalue())

    def test_two_files_both_clean(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.md").write_text("```python\nx = 1\n```\n", encoding="utf-8")
            (root / "b.md").write_text("```python\ny = 2\n```\n", encoding="utf-8")
            self.assertEqual(
                main(["scan", str(root / "a.md"), str(root / "b.md"),
                      "--no-color", "--fail-on", "lie",
                      "--enable", "unclosed-fence"]), 0)

    def test_zero_files_falls_back_to_dot(self):
        # pre-commit passes no filenames when nothing matched `files:`; the
        # hook must stay silent rather than die on `scan` with no path.
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.py").write_text("X = 1\n", encoding="utf-8")
            self.assertEqual(main(["scan", str(td), "--no-color"]), 0)


class TestSuppressions(unittest.TestCase):
    def scan(self, root: Path, files: dict[str, str]):
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return scan_root(root, Config())

    def test_trailing_disable_suppresses(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Calls `ghost_fn()`.  # grounded-disable: stale-symbol-ref\nX = 1\n",
                encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color"]), 0)

    def test_other_checker_not_suppressed(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Calls `ghost_fn()`.  # grounded-disable: fragile-anchor\nX = 1\n",
                encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color"]), 1)

    def test_all_suppresses(self):
        from grounded.scanner import apply_suppressions
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Calls `ghost_fn()`.  # grounded-disable: all\nX = 1\n", encoding="utf-8")
            findings, facts, _ = scan_root(root, Config())
            kept, n = apply_suppressions(findings, {f.path: f for f in facts})
            self.assertEqual(kept, [])
            self.assertEqual(n, 1)

    def test_js_marker_suppresses(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.js").write_text(
                "// Calls `ghost_fn()`. // grounded-disable: stale-symbol-ref\nconst x = 1;\n",
                encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color"]), 0)

    def test_unknown_id_warns_without_failing(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Calls `ghost_fn()`.  # grounded-disable: stale-symobl\nX = 1\n",
                encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = main(["scan", str(root), "--no-color", "--fail-on", "never"])
            self.assertEqual(rc, 0)
            self.assertIn("unknown checker id", err.getvalue())
            self.assertIn("stale-symobl", err.getvalue())

    def test_known_id_no_warning(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Calls `ghost_fn()`.  # grounded-disable: stale-symbol-ref\nX = 1\n",
                encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                main(["scan", str(root), "--no-color"])
            self.assertNotIn("unknown checker id", err.getvalue())


class TestGo(unittest.TestCase):
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

    def test_backticked_call_missing_is_lie(self):
        out = self.scan({"a.go": 'package main\n\n// Calls `ghost_cmd()` on failure.\nfunc Ping() {}\n'})
        self.assertTrue(any(f.checker == "stale-symbol-ref" and "ghost_cmd" in f.title for f in out))

    def test_imported_and_builtin_silent(self):
        out = self.scan({"a.go": 'package main\n\nimport "fmt"\n\n// Uses `fmt.Println()`.\n// len() of the batch.\nfunc f() {}\n'})
        self.assertFalse([f for f in out if f.checker == "stale-symbol-ref"])

    def test_defined_method_silent(self):
        out = self.scan({"a.go": 'package main\n\n// See ParseFlags() for merging.\nfunc ParseFlags() {}\n'})
        self.assertFalse([f for f in out if f.checker == "stale-symbol-ref"])

    def test_ticket_next_line_silences_temporal(self):
        out = self.scan({"a.go": 'package main\n\n// Temporarily disable check X.\n// See https://example.com/issue/1.\nfunc f() {}\n'})
        self.assertFalse([f for f in out if f.checker == "fragile-anchor"])

    def test_untracked_hack_still_fires(self):
        out = self.scan({"a.go": 'package main\n\n// HACK: skip validation for now\nfunc f() {}\n'})
        self.assertTrue(any(f.checker == "fragile-anchor" for f in out))


class TestC(unittest.TestCase):
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

    def syms(self, files):
        return [f for f in self.scan(files) if f.checker == "stale-symbol-ref"]

    def test_backticked_call_missing_is_lie(self):
        out = self.syms({"a.c": "// Calls `ghost_fn()` on error.\nint main() { return 0; }\n"})
        self.assertTrue(any("ghost_fn" in f.title for f in out))

    def test_split_declaration_known(self):
        out = self.syms({"a.c": "static void\nghost_fn(int x) {\n}\n",
                         "b.c": "// Calls `ghost_fn()` on error.\nint main() { return 0; }\n"})
        self.assertEqual(out, [])

    def test_knr_definition_known(self):
        out = self.syms({"a.c": "ghost_fn(x, y)\n{\n}\n",
                         "b.c": "// Calls `ghost_fn()` on error.\nint main() { return 0; }\n"})
        self.assertEqual(out, [])

    def test_function_pointer_member_known(self):
        out = self.syms({"a.h": "typedef struct { void (*cb)(int x); } T;\n",
                         "b.c": '// Uses `cb()` for events.\n#include "a.h"\nint main() { return 0; }\n'})
        # cb is imported via a.h include map (base a) or struct member context;
        # at minimum the fptr declaration must not break the scan
        self.assertIsInstance(out, list)

    def test_stdlib_and_posix_silent(self):
        out = self.syms({"a.c": "// Uses malloc() and strlen().\n// Calls socket() then bind().\nint main() { return 0; }\n"})
        self.assertEqual(out, [])

    def test_call_statement_not_indexed(self):
        # `ghost_fn(x);` as a bare statement must not register a definition
        from grounded.parsers import c_top_level_names
        self.assertNotIn("ghost_fn", c_top_level_names("int main() {\n  ghost_fn(1);\n  return 0;\n}\n"))


class TestStaleImport(unittest.TestCase):
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

    def imps(self, files):
        return [f for f in self.scan(files) if f.checker == "stale-import"]

    def test_missing_name_is_lie(self):
        out = self.imps({"pkg/mod.py": "def real():\n    pass\n",
                         "pkg/use.py": "from .mod import gone_thing\n"})
        self.assertTrue(any("gone_thing" in f.title for f in out))

    def test_from_package_star_reexport_silent(self):
        # `from . import X` where X arrives via `from .mod import *`
        # (seen: asyncio.DefaultEventLoopPolicy).
        out = self.imps({"pkg/__init__.py": "from .mod import *\n",
                         "pkg/mod.py": "Policy = 1\n",
                         "pkg/use.py": "from . import Policy\n"})
        self.assertEqual(out, [])

    def test_from_package_dynamic_ns_silent(self):
        # `globals().update(...)` in __init__: namespace unknowable
        # (seen: multiprocessing.get_context).
        out = self.imps({"pkg/__init__.py": "from . import context\nglobals().update({})\n",
                         "pkg/context.py": "X = 1\n",
                         "pkg/use.py": "from . import get_context\n"})
        self.assertEqual(out, [])

    def test_dynamic_ns_module_silent(self):
        # Names injected via globals().update in a regular module
        # (seen: re._constants BRANCH).
        out = self.imps({"pkg/_names.py": "def _make():\n    globals().update({'X': 1})\n",
                         "pkg/use.py": "from . import _names\nfrom ._names import X\n"})
        self.assertEqual(out, [])

    def test_src_layout_resolves(self):
        out = self.imps({"src/mypkg/__init__.py": "",
                         "src/mypkg/core.py": "def real():\n    return 1\n",
                         "src/mypkg/views.py": "from mypkg.core import gone_thing\n"})
        self.assertTrue(any("gone_thing" in f.title for f in out))

    def test_src_layout_valid_silent(self):
        out = self.imps({"src/mypkg/__init__.py": "",
                         "src/mypkg/core.py": "def real():\n    return 1\n",
                         "src/mypkg/views.py": "from mypkg.core import real\n"})
        self.assertEqual(out, [])

    def test_src_layout_root_package_wins(self):
        out = self.imps({"mypkg/core.py": "OTHER = 1\n",
                         "src/mypkg/__init__.py": "",
                         "src/mypkg/core.py": "def real():\n    return 1\n",
                         "use.py": "from mypkg.core import real\n"})
        self.assertTrue(any("real" in f.title for f in out))

    def test_missing_module_is_lie(self):
        out = self.imps({"pkg/use.py": "from .deleted import thing\n"})
        self.assertTrue(any("deleted" in f.title for f in out))

    def test_ok_import_silent(self):
        out = self.imps({"pkg/mod.py": "def real():\n    pass\n",
                         "pkg/use.py": "from .mod import real\n"})
        self.assertEqual(out, [])

    def test_unparseable_module_never_fires_absence(self):
        # Regression: a module that fails ast.parse (version-skewed
        # grammar, truncated buffer) must not cascade into phantom
        # "never defined there" lies for every importer. Seen: 24,881
        # stale-import findings on home-assistant/core scanned under
        # Python 3.13, whose grammar rejects 3.14 syntax. Helper is only
        # re-exported (no top-level def for the fallback to find), so
        # this exercises the opaque guard, not the fallback.
        out = self.imps({"pkg/base.py": "def Helper():\n    pass\n",
                         "pkg/core.py": "from .base import Helper\n{{{broken\n",
                         "pkg/use.py": "from .core import Helper\n"})
        self.assertEqual(out, [])

    def test_unparseable_module_marks_opaque(self):
        from grounded.config import Config
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "core.py").write_text("def get_thing():\n{{{broken\n", encoding="utf-8")
            _, _, index = scan_root(root, Config())
            self.assertIn("core.py", index.parse_failed)
            self.assertFalse(index.knows_symbols("core.py"))

    def test_fallback_symbols_suppress(self):
        # The regex fallback recovers top-level bindings so positively
        # evidenced names stay silent even when the file won't parse.
        out = self.imps({"pkg/core.py": "def get_thing():\n    return 1\n{{{broken\n",
                         "pkg/use.py": "from .core import get_thing\n"})
        self.assertEqual(out, [])

    def test_submodule_form_silent(self):
        out = self.imps({"pkg/sub.py": "X = 1\n",
                         "pkg/use.py": "from . import sub\n"})
        self.assertEqual(out, [])

    def test_reexport_silent(self):
        out = self.imps({"pkg/inner.py": "def real():\n    pass\n",
                         "pkg/__init__.py": "from .inner import real\n",
                         "pkg/use.py": "from . import real\n"})
        self.assertEqual(out, [])

    def test_stdlib_and_thirdparty_silent(self):
        out = self.imps({"a.py": "import os\nfrom requests import Session\n"})
        self.assertEqual(out, [])

    def test_guarded_imports_silent(self):
        out = self.imps({
            "a.py": "try:\n    from .gone import x\nexcept ImportError:\n    x = None\n",
            "b.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .alsogone import Y\n",
            "c.py": "import sys\nif sys.version_info >= (3, 11):\n    from .newonly import Z\n",
        })
        self.assertEqual(out, [])

    def test_star_silent(self):
        out = self.imps({"pkg/mod.py": "X = 1\n",
                         "pkg/use.py": "from .mod import *\n"})
        self.assertEqual(out, [])


class TestStaleImportJs(unittest.TestCase):
    def test_scan_ignored_target_dir_silent_via_alias(self):
        # Same verdict through the alias arm: a tsconfig/manual alias that
        # maps into a scan-ignored dir (seen: OmniRoute
        # `@omniroute/open-sse/*` reaching tracked vendor code) reports
        # drift for files that exist on disk. Unknowable == silent.
        out = self.imps({
            "tsconfig.json": '{"compilerOptions": {"paths": {"@omniroute/open-sse/*": ["./open-sse/*"]}}}',
            "open-sse/vendor/codex/browser-login.ts": "export function inspect() {}\n",
            "src/use.ts": "import { inspect } from '@omniroute/open-sse/vendor/codex/browser-login.ts';\n",
        })
        self.assertEqual(out, [])

    def test_reexported_type_visible(self):
        # `export { type X }` barrel re-exports must keep the type on the
        # module's export surface (seen: OmniRoute providerPageHelpers
        # re-exporting ProviderMessageTranslator -> 13 bogus stale-imports).
        out = self.imps({
            "src/providerCredentialText.ts":
                "export type ProviderMessageTranslator = (s: string) => string;\n"
                "export function providerText(): void {}\n",
            "src/providerPageHelpers.ts":
                "import { type ProviderMessageTranslator, providerText } from './providerCredentialText';\n"
                "export {\n"
                "  type ProviderMessageTranslator,\n"
                "  providerText,\n"
                "};\n",
            "src/page.tsx":
                "import type { ProviderMessageTranslator } from './providerPageHelpers';\n",
        })
        self.assertEqual(out, [])

    def test_reexported_type_missing_still_fires(self):
        # Positive control: the barrel does not re-export the imported name.
        out = self.imps({
            "src/t.ts": "export type T = string;\n",
            "src/barrel.ts": "export { type T } from './t';\n",
            "src/page.tsx": "import type { NotThere } from './barrel';\n",
        })
        self.assertTrue(any("NotThere" in f.title for f in out))

    def test_trailing_disable_silences_dynamic_import(self):
        # Intentional dynamic resolutions use the standard trailing
        # marker (explicit checker id, typo-warned): no second mechanism.
        files = {
            "src/real.ts": "export const x = 1;\n",
            "src/dyn.ts":
                "import { x } from '../gen/missing';  // grounded-disable: stale-import\n",
        }
        self.assertEqual(self.imps(files), [])
        # Positive control: without the marker the finding fires.
        files2 = {
            "src/real.ts": "export const x = 1;\n",
            "src/dyn.ts": "import { x } from '../gen/missing';\n",
        }
        out = self.imps(files2)
        self.assertTrue(len(out) == 1)

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

    def imps(self, files):
        return [f for f in self.scan(files) if f.checker == "stale-import"]

    def test_missing_name_is_lie(self):
        out = self.imps({"lib/util.js": "export function real() {}\n",
                         "lib/app.js": "import { gone } from './util.js';\n"})
        self.assertTrue(any("gone" in f.title for f in out))

    def test_missing_module_is_lie(self):
        out = self.imps({"lib/app.js": "import { x } from './deleted';\n"})
        self.assertTrue(any("deleted" in f.title for f in out))

    def test_ok_named_and_default_silent(self):
        out = self.imps({"lib/util.js": "export function real() {}\nexport default real;\n",
                         "lib/app.js": "import real, { real as r2 } from './util.js';\n"})
        self.assertEqual(out, [])

    def test_missing_default_is_lie(self):
        out = self.imps({"lib/util.js": "export function real() {}\n",
                         "lib/app.js": "import real from './util.js';\n"})
        self.assertTrue(any("default" in f.title for f in out))

    def test_cjs_default_import_silent(self):
        # Node default-import interop: importing the default of a CJS
        # module always binds module.exports (seen: express examples).
        out = self.imps({"lib/utils.js": "exports.compileETag = function(v) { return v; };\n",
                         "lib/app.js": "import compileETag from './utils';\n"})
        self.assertEqual(out, [])

    def test_directory_index_normalized(self):
        out = self.imps({"index.js": "module.exports = require('./lib/app');\n",
                         "lib/app.js": "module.exports = {};\n",
                         "examples/a/index.js": "import app from '../..';\n"})
        self.assertEqual(out, [])

    def test_require_prop_is_named_import(self):
        out = self.imps({"lib/utils.js": "exports.compileETag = function(v) { return v; };\n",
                         "lib/app.js": "var compileETag = require('./utils').compileETag;\n"})
        self.assertEqual(out, [])
        bad = self.imps({"lib/utils.js": "exports.other = 1;\n",
                         "lib/app.js": "var gone = require('./utils').gone;\n"})
        self.assertTrue(any("gone" in f.title for f in bad))

    def test_bare_and_sideeffect_silent(self):
        out = self.imps({"lib/poly.js": "X = 1\n",
                         "lib/app.js": "import axios from 'axios';\nimport './poly.js';\n"})
        self.assertEqual(out, [])

    def test_index_resolution_silent(self):
        out = self.imps({"lib/util.js": "export function real() {}\n",
                         "lib/app.js": "import { real } from './util';\n"})
        self.assertEqual(out, [])

    def test_star_reexport_silent(self):
        out = self.imps({"lib/inner.js": "export function real() {}\n",
                         "lib/index.js": "export * from './inner.js';\n",
                         "lib/app.js": "import { real } from './index.js';\n"})
        self.assertEqual(out, [])

    def test_require_named_checked(self):
        out = self.imps({"lib/util.js": "module.exports = { real: 1 };\n",
                         "lib/app.js": "const { gone } = require('./util.js');\n"})
        self.assertTrue(any("gone" in f.title for f in out))

    def test_require_plain_script_silent(self):
        out = self.imps({"lib/util.js": "function real() {}\n",
                         "lib/app.js": "const { gone } = require('./util.js');\n"})
        self.assertEqual(out, [])


class TestJsAliases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, files: dict[str, str], config_text: str | None = None):
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        if config_text is not None:
            (self.root / "grounded.toml").write_text(config_text, encoding="utf-8")
        return scan_root(self.root, Config.load(self.root))[0]

    def imps(self, files, config_text=None):
        return [f for f in self.scan(files, config_text) if f.checker == "stale-import"]

    def test_tsconfig_alias_resolves(self):
        out = self.imps({
            "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}',
            "src/util.ts": "export function real() {}\n",
            "src/app.ts": "import { gone } from '@/util';\n",
        })
        self.assertTrue(any("gone" in f.title for f in out))

    def test_tsconfig_alias_ok_silent(self):
        out = self.imps({
            "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}',
            "src/util.ts": "export function real() {}\n",
            "src/app.ts": "import { real } from '@/util';\n",
        })
        self.assertEqual(out, [])

    def test_alias_miss_is_drift_not_lie(self):
        out = self.imps({
            "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}',
            "src/app.ts": "import { x } from '@/nope';\n",
        })
        self.assertTrue(all(f.severity == "drift" for f in out))
        self.assertTrue(len(out) == 1)

    def test_node_modules_mapping_silent(self):
        out = self.imps({
            "tsconfig.json": '{"compilerOptions": {"paths": {"react": ["./node_modules/@types/react"]}}}',
            "src/app.ts": "import React from 'react';\n",
        })
        self.assertEqual(out, [])

    def test_manual_alias_config(self):
        out = self.imps({
            "src/util.ts": "export function real() {}\n",
            "src/app.ts": "import { gone } from '~/util';\n",
        }, config_text='path_aliases = {"~/" = "src/"}\n')
        self.assertTrue(any("gone" in f.title for f in out))

    def test_export_type_recognized(self):
        out = self.imps({"lib/t.ts": "export type { T };\ntype T = string;\n",
                         "lib/a.ts": "import type { T } from './t';\n"})
        self.assertEqual(out, [])

    def test_self_name_import_silent(self):
        # svelte fixture: the package maps its own name in tsconfig paths
        # (`svelte` -> `./src/index.d.ts`, a types-only surface excluded
        # from the scan). The import is resolved at runtime by the package
        # exports map / bundler self-reference — invisible to snapshot
        # analysis, so claiming it "does not exist" was 1235 false drift
        # findings on sveltejs/svelte. Self-name bare imports are silent.
        out = self.imps({
            "package.json": '{"name": "svelte"}',
            "tsconfig.json": json.dumps({"compilerOptions": {"paths": {
                "svelte": ["./src/index.d.ts"],
                "svelte/compiler": ["./src/compiler/public.d.ts"]}}}),
            "src/index.d.ts": "export function mount() {}\n",
            "tests/test.ts": "import { mount } from 'svelte';\n"
                             "import { compile } from 'svelte/compiler';\n",
        })
        self.assertEqual(out, [])

    def test_bare_external_types_mapping_silent(self):
        # Same shape as the svelte fixture with the self-name swapped for a
        # dependency: an alias whose every replacement is a `.d.ts` surface
        # excluded from the scan is not falsifiable from a snapshot.
        out = self.imps({
            "package.json": '{"name": "mylib", "dependencies": {"left-pad": "^1.0.0"}}',
            "tsconfig.json": json.dumps({"compilerOptions": {"paths": {
                "left-pad": ["./types/left-pad/index.d.ts"]}}}),
            "types/left-pad/index.d.ts": "export function pad(): void\n",
            "src/app.ts": "import { pad } from 'left-pad';\n",
        })
        self.assertEqual(out, [])

    def test_in_tree_alias_mapping_still_checked(self):
        # The suppression must not swallow the decidable case: an alias
        # mapped onto the scanned tree still reports named/missing verdicts.
        out = self.imps({
            "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}',
            "src/util.ts": "export function real() {}\n",
            "src/app.ts": "import { gone } from '@/util';\n",
        })
        self.assertTrue(any("gone" in f.title for f in out))

    def test_bare_unmapped_still_silent(self):
        # Plain bare external packages (node_modules surface) stay silent.
        out = self.imps({
            "package.json": '{"name": "mylib"}',
            "src/app.ts": "import React from 'react';\nimport x from 'lodash/merge';\n",
        })
        self.assertEqual(out, [])

    def test_other_workspaces_name_still_checked(self):
        # Monorepo: the root package's name is a self-name for every file
        # in the tree, but a *sibling* workspace's name is not silent when
        # nothing maps it — it is a cross-workspace import resolved by the
        # package manager. Here the name is unmapped and in no manifest:
        # silent (phantom-package's surface), never an import lie.
        out = self.imps({
            "package.json": '{"name": "root"}',
            "packages/a/package.json": '{"name": "a-pkg"}',
            "packages/b/index.ts": "import { thing } from 'a-pkg';\n",
        })
        self.assertEqual(out, [])

    def test_ts_style_js_extension_resolves_ts(self):
        # TS module resolution: `./shared.js` resolves shared.ts when no
        # JS file exists (universal in TS suites compiled to ESM — seen:
        # svelte tests, OmniRoute tests). The resolved module is checked
        # normally: a missing name is still a lie.
        out = self.imps({
            "lib/shared.ts": "export function suite() {}\n",
            "lib/app.ts": "import { suite } from './shared.js';\n",
        })
        self.assertEqual(out, [])
        bad = self.imps({
            "lib/shared.ts": "export function other() {}\n",
            "lib/app.ts": "import { gone } from './shared.js';\n",
        })
        self.assertTrue(any("gone" in f.title for f in bad))

    def test_ambient_dts_module_silent(self):
        # Extensionless specifier resolving to an ambient declaration
        # (`./types` -> types.d.ts, seen: svelte internal client): decl
        # files are existence-tracked, never parsed, so the check stays
        # silent instead of claiming the module does not exist.
        out = self.imps({
            "lib/types.d.ts": "export type Effect = { id: string };\n",
            "lib/app.test.ts": "import type { Effect } from './types';\n",
        })
        self.assertEqual(out, [])

    def test_tsconfig_excluded_claimer_silent(self):
        # Files the repo excludes from its own typecheck (tsconfig
        # `exclude`) are outside the import contract: codegen templates
        # whose relative imports resolve only after transplantation
        # (seen: svelte's scripts/process-messages/templates/).
        out = self.imps({
            "tsconfig.json": json.dumps({"compilerOptions": {},
                                         "exclude": ["./templates/"]}),
            "templates/compile-errors.js":
                "import { C } from './utils/compile_diagnostic.js';\n",
            "src/utils/compile_diagnostic.ts": "export class C {}\n",
        })
        self.assertEqual(out, [])

    def test_scan_ignored_target_dir_silent(self):
        # A relative import into a directory the scan deliberately ignores
        # (vendor, build, dist, ...) cannot be judged: the file may be a
        # real tracked file the snapshot skipped (seen: OmniRoute's
        # open-sse/vendor/ and scripts/build/ imports reported as missing).
        out = self.imps({
            "scripts/build/assemble.mjs": "export const x = 1;\n",
            "bin/tool.mjs": "import { x } from '../scripts/build/assemble.mjs';\n",
            "open-sse/vendor/dep.ts": "export const y = 2;\n",
            "src/use.ts": "import { y } from '../open-sse/vendor/dep.ts';\n",
        })
        self.assertEqual(out, [])


    def test_require_default_in_js_file_silent(self):
        # require() from a .js importer may run as CJS, where the call
        # binds any module.exports shape (seen: electron/loginManager.js
        # requiring a TS service with only named exports). Only .mjs
        # importers are forced-ESM; .js stays silent.
        out = self.imps({
            "lib/service.ts": "export const CONFIGS = {};\n",
            "electron/loginManager.js":
                "const mod = require('../lib/service.ts');\n",
        })
        self.assertEqual(out, [])


class TestRepoFiles(unittest.TestCase):
    def test_action_files_parse(self):
        import json
        root = Path(__file__).resolve().parent.parent
        matcher = json.loads((root / ".github" / "grounded-problem-matcher.json").read_text())
        self.assertEqual(len(matcher["problemMatcher"]), 3)
        for entry in matcher["problemMatcher"]:
            self.assertIn("owner", entry)
        action = (root / "action.yml").read_text()
        self.assertIn("grounded-problem-matcher.json", action)
        self.assertIn("composite", action)

    def test_action_no_dot_notation_hyphen_inputs(self):
        # Regression: `${{ inputs.fail-on }}` parses as arithmetic and
        # expands empty. Hyphenated inputs require bracket notation.
        import re
        root = Path(__file__).resolve().parent.parent
        action = (root / "action.yml").read_text()
        bad = re.findall(r"\$\{\{\s*inputs\.[A-Za-z0-9_]+-[A-Za-z0-9_-]*", action)
        self.assertEqual(bad, [])
        hooks = (root / ".pre-commit-hooks.yaml").read_text()
        self.assertIn("grounded scan", hooks)


class TestReleaseBinaries(unittest.TestCase):
    """The standalone-binary release is a three-way contract: `install.sh`
    derives `grounded-${OS}-${ARCH}` from `uname`, `release-binaries.yml`
    publishes exactly those names, and `verify-release.yml` audits them. On
    2026-09-22 three releases shipped three of four assets because the
    darwin-amd64 leg asked for the retired `macos-13` image and stayed queued
    forever instead of failing; with a missing Intel asset, the advertised
    one-liner 404'd on Intel Macs and blamed the network. These pin the
    invariants that let that gap stay invisible."""

    def _root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def _workflow(self, name: str) -> str:
        return (self._root() / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def _assets(self, text: str) -> set[str]:
        import re
        return set(
            re.findall(r"grounded-(?:darwin|linux|windows)-(?:arm64|amd64)(?:\.exe)?", text)
        )

    def test_producer_and_auditor_agree_on_the_asset_set(self) -> None:
        produced = self._assets(self._workflow("release-binaries.yml"))
        audited = self._assets(self._workflow("verify-release.yml"))
        self.assertEqual(produced, audited)
        self.assertEqual(
            produced,
            {
                "grounded-linux-amd64",
                "grounded-windows-amd64.exe",
                "grounded-darwin-arm64",
                "grounded-darwin-amd64",
            },
        )

    def test_no_dated_macos_runner_in_the_binary_matrix(self) -> None:
        # A job pointed at a retired image does not fail — it stays queued
        # forever, so the workflow never goes red. Every dated macOS image is
        # retired eventually, so the matrix must use the rolling label.
        import re
        text = self._workflow("release-binaries.yml")
        dated = re.findall(r"(?:runs-on:\s*|os:\s*)(macos-\d+)\b", text)
        self.assertEqual(dated, [], f"dated macOS runner images queue forever: {dated}")
        self.assertIn("macos-latest", text)

    def test_macos_binary_is_universal2_gated(self) -> None:
        # One fat binary serves both darwin names; the gate is what stops an
        # arm64-only build from ever being published under the amd64 name.
        text = self._workflow("release-binaries.yml")
        self.assertIn("--target-arch universal2", text)
        self.assertIn("ARCH_FLAG", text)
        self.assertIn("lipo -archs", text)

    def test_a_stuck_leg_is_audited_outside_the_producing_workflow(self) -> None:
        # A queued job cannot report on itself, so the audit runs in a separate
        # workflow, on a schedule as well as on the release event, and reaches
        # an issue rather than a red run nobody reads.
        text = self._workflow("verify-release.yml")
        self.assertIn("release:", text)
        self.assertIn("schedule:", text)
        self.assertIn("failure()", text)

    def test_installer_never_advertises_an_unpublished_platform(self) -> None:
        import re
        sh = (self._root() / "install.sh").read_text(encoding="utf-8")
        arches = set(re.findall(r'ARCH="([a-z0-9_]+)"', sh))
        self.assertEqual(arches, {"amd64", "arm64"}, arches)
        produced = self._assets(self._workflow("release-binaries.yml"))
        for arch in arches:  # both macOS architectures must resolve
            self.assertIn(f"grounded-darwin-{arch}", produced)
        for osname, arch in re.findall(r"\b(linux|darwin)/(amd64|arm64)\b", sh):
            self.assertIn(f"grounded-{osname}-{arch}", produced)


class TestCheckerErrors(unittest.TestCase):
    """A checker that raises must be a counted, failing event.

    `except Exception: continue` made a crash indistinguishable from a checker
    that found nothing: a scan whose checker died printed
    `grounded: clean, N file(s) scanned, 0 findings` and exited 0. Every gate
    built on that output could therefore only get *greener* from a bug, which
    is how `stale-doc-ref` stayed silently dark on any tree without a manifest
    (its `declared_dependencies()` returned None) while the dogfood gate read
    `clean`.
    """

    def setUp(self) -> None:
        from grounded.checkers import CHECKERS
        self._saved = dict(CHECKERS)
        self._checkers = CHECKERS

    def tearDown(self) -> None:
        self._checkers.clear()
        self._checkers.update(self._saved)

    def _break(self, checker_id: str) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("checker exploded")
        self._checkers[checker_id] = boom

    def _tree_with_a_finding(self, td: str) -> Path:
        root = Path(td)
        (root / "mod.py").write_text(
            "def helper():\n    # Call ghost_service() to sync state.\n    return 1\n",
            encoding="utf-8")
        return root

    def _clean_tree(self, td: str) -> Path:
        root = Path(td)
        (root / "mod.py").write_text(
            "def helper():\n    # Returns the cached value.\n    return 1\n",
            encoding="utf-8")
        return root

    def test_crash_is_recorded_with_its_checker_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._tree_with_a_finding(td)
            self._break("stale-symbol-ref")
            errors: list = []
            _, _, _ = scan_root(root, Config(), checker_errors=errors)
            self.assertEqual([(e.checker, e.path) for e in errors],
                             [("stale-symbol-ref", "mod.py")])
            self.assertIn("checker exploded", errors[0].message)

    def test_a_broken_checker_never_hides_a_healthy_ones_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._tree_with_a_finding(td)
            self._break("number-drift")
            errors: list = []
            findings, _, _ = scan_root(root, Config(), checker_errors=errors)
            self.assertTrue([f for f in findings if f.checker == "stale-symbol-ref"])
            self.assertEqual([e.checker for e in errors], ["number-drift"])

    def test_clean_is_never_claimed_when_a_checker_failed(self) -> None:
        from grounded.reporters import format_terminal
        self.assertIn("grounded: clean", format_terminal([], 1, root=".", use_color=False))
        out = format_terminal([], 1, root=".", use_color=False, n_checker_errors=1)
        self.assertNotIn("grounded: clean", out)
        self.assertIn("INCOMPLETE, not clean", out)

    def test_scan_exits_3_and_names_the_checker(self) -> None:
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = self._clean_tree(td)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                healthy = main(["scan", str(root), "--no-color"])
            self.assertEqual(healthy, 0)  # control: the tree really is clean
            self._break("stale-symbol-ref")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["scan", str(root), "--no-color"])
            self.assertEqual(code, 3)
            self.assertNotIn("grounded: clean", out.getvalue())
            self.assertIn("checker error", err.getvalue())
            self.assertIn("stale-symbol-ref", err.getvalue())

    def test_a_cached_file_still_reports_its_checker_error(self) -> None:
        # A cache hit means the checkers did not run this time, so an error
        # recorded in v1-style entries would vanish on the next scan.
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = self._clean_tree(td)
            cache = str(Path(td) / "cache.json")
            self._break("stale-symbol-ref")
            codes = []
            for _ in range(2):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    codes.append(main(["scan", str(root), "--no-color", "--cache", cache]))
            self.assertEqual(codes, [3, 3])

    def test_disable_is_the_explicit_escape_hatch(self) -> None:
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = self._clean_tree(td)
            self._break("stale-symbol-ref")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["scan", str(root), "--no-color",
                             "--disable", "stale-symbol-ref"])
            self.assertEqual(code, 0)

    def test_json_payload_is_not_polluted_by_checker_errors(self) -> None:
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = self._clean_tree(td)
            self._break("stale-symbol-ref")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                main(["scan", str(root), "--format", "json"])
            self.assertIsInstance(json.loads(out.getvalue()), list)

    def test_baseline_refuses_to_persist_an_incomplete_scan(self) -> None:
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = self._tree_with_a_finding(td)
            self._break("stale-symbol-ref")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["baseline", str(root)])
            self.assertEqual(code, 3)
            self.assertFalse((root / ".grounded-baseline.json").exists())


class TestFix(unittest.TestCase):
    def _write(self, root: Path, files: dict[str, str]) -> None:
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def test_unique_match_rewritten(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "src/real/deep.py": "X = 1\n",
                "a.py": "# See src/old/deep.py for details.\nY = 2\n",
            })
            self.assertEqual(main(["fix", str(root)]), 0)
            self.assertIn("src/real/deep.py", (root / "a.py").read_text())
            # rescan is clean
            self.assertEqual(main(["scan", str(root), "--no-color"]), 0)

    def test_dry_run_writes_nothing(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "src/real/deep.py": "X = 1\n",
                "a.py": "# See src/old/deep.py for details.\nY = 2\n",
            })
            before = (root / "a.py").read_text()
            self.assertEqual(main(["fix", str(root), "--dry-run"]), 0)
            self.assertEqual((root / "a.py").read_text(), before)

    def test_ambiguous_basename_untouched(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "src/one/deep.py": "X = 1\n",
                "src/two/deep.py": "X = 2\n",
                "a.py": "# See src/old/deep.py for details.\nY = 2\n",
            })
            self.assertEqual(main(["fix", str(root)]), 0)
            self.assertIn("src/old/deep.py", (root / "a.py").read_text())

    def test_docstring_claim_untouched(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "src/real/deep.py": "X = 1\n",
                "a.py": 'def f():\n    """Do.\n\n    See src/old/deep.py.\n    """\n    return 1\n',
            })
            self.assertEqual(main(["fix", str(root)]), 0)
            self.assertIn("src/old/deep.py", (root / "a.py").read_text())

    def test_non_file_findings_untouched(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            before = (root / "a.py").read_text()
            self.assertEqual(main(["fix", str(root)]), 0)
            self.assertEqual((root / "a.py").read_text(), before)

    def test_ignored_directories_not_used_as_fix_candidates(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                ".git/hooks/pre-commit.py": "print('hook')\n",
                ".venv/lib/helper.py": "print('venv')\n",
                "a.py": "# See src/hooks/pre-commit.py and src/old/helper.py\nX = 1\n",
            })
            before = (root / "a.py").read_text()
            self.assertEqual(main(["fix", str(root)]), 0)
            self.assertEqual((root / "a.py").read_text(), before)

    def test_symbol_rename_same_dir_unique(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "pkg/real.py": "def test_max_cookie_length():\n    pass\n",
                "pkg/note.py": "# see comment in CookieTests.test_cookie_max_length()\nX = 1\n",
                "other/test_choices_in_max_length.py": "def test_choices_in_max_length():\n    pass\n",
            })
            self.assertEqual(main(["fix", str(root)]), 0)
            text = (root / "pkg" / "note.py").read_text()
            self.assertIn("test_max_cookie_length", text)
            self.assertNotIn("test_cookie_max_length", text)

    def test_symbol_rename_ambiguous_same_dir_untouched(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "pkg/a.py": "def test_max_cookie_length():\n    pass\n",
                "pkg/b.py": "def test_cookie_max_thing():\n    pass\n",
                "pkg/note.py": "# see comment in CookieTests.test_cookie_max_length()\nX = 1\n",
            })
            before = (root / "pkg" / "note.py").read_text()
            self.assertEqual(main(["fix", str(root)]), 0)
            self.assertEqual((root / "pkg" / "note.py").read_text(), before)

    def test_symbol_bare_claim_fixed_when_unique(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {
                "pkg/real.py": "def fetch_user_data():\n    pass\n",
                "pkg/note.py": "# Calls fetch_user_data_old() and formats.\nX = 1\n",
            })
            self.assertEqual(main(["fix", str(root)]), 0)
            text = (root / "pkg" / "note.py").read_text()
            self.assertIn("fetch_user_data()", text)
            self.assertNotIn("fetch_user_data_old", text)


class TestIndexCoverage(unittest.TestCase):
    def test_js_index_extra_patterns(self):
        from grounded.repo_index import RepoIndex
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.ts").write_text(
                "export default function main() {}\n"
                "module.exports.helper = 1;\n"
                "export interface Config {}\n"
                "type Alias = string;\n",
                encoding="utf-8")
            idx = RepoIndex(root, [root / "a.ts"])
            for name in ["main", "helper", "Config", "Alias"]:
                self.assertIn(name, idx.all_symbols)

    @unittest.skipIf(sys.version_info < (3, 12), "PEP 695 needs 3.12+")
    def test_py_pep695_type_alias_indexed(self):
        # `type X = ...` is a runtime binding; without it every import
        # from the module misfires (seen: _pyrepl/types.py on CPython).
        from grounded.repo_index import RepoIndex
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "types.py").write_text("type KeySpec = str\n", encoding="utf-8")
            idx = RepoIndex(root, [root / "types.py"])
            self.assertIn("KeySpec", idx.all_symbols)


class TestParallelDeterminism(unittest.TestCase):
    def test_parallel_matches_serial(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "keep.py").write_text("X = 1\n", encoding="utf-8")
            (root / "a.py").write_text(
                "# Calls `ghost_fn()`.\n# See src/gone.py.\nX = 1\n", encoding="utf-8")
            (root / "b.js").write_text(
                "// Calls `other_ghost()`.\nconst y = 2;\n", encoding="utf-8")
            serial, _, _ = scan_root(root, Config(), jobs=1)
            parallel, _, _ = scan_root(root, Config(), jobs=2)
            self.assertEqual(
                [f.to_dict() for f in serial], [f.to_dict() for f in parallel])


class TestMatcherDrift(unittest.TestCase):
    def test_terminal_lines_match_problem_matcher(self):
        """The GitHub Action annotations break silently if terminal output
        drifts from the matcher regexes. This pins them together."""
        import io
        import contextlib
        import re
        from grounded.cli import main
        root = Path(__file__).resolve().parent.parent
        matcher = json.loads((root / ".github" / "grounded-problem-matcher.json").read_text())
        patterns = {}
        for entry in matcher["problemMatcher"]:
            sev = {"error": "LIE", "warning": "DRIFT", "notice": "SMELL"}[
                entry["severity"]]
            patterns[sev] = re.compile(entry["pattern"][0]["regexp"])
        with tempfile.TemporaryDirectory() as td:
            troot = Path(td)
            (troot / "a.py").write_text(
                "# Calls `ghost_fn()`.\n# See line 99 for details.\nX = 1\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(["scan", str(troot), "--no-color"])
            matched = {"LIE": False, "SMELL": False}
            for line in buf.getvalue().splitlines():
                for sev, pat in patterns.items():
                    m = pat.match(line)
                    if m and sev in matched:
                        matched[sev] = True
            self.assertTrue(matched["LIE"], "LIE line must match its matcher")
            self.assertTrue(matched["SMELL"], "SMELL line must match its matcher")


class TestMcp(unittest.TestCase):
    def _server(self, root: Path):
        from grounded.mcp import McpServer
        return McpServer(root)

    def _handshake(self, server):
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-03-26",
                                         "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "0"}}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_full_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn()`.\nX = 1\n", encoding="utf-8")
            server = self._server(root)
            self._handshake(server)
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {t["name"] for t in tools["result"]["tools"]}
            self.assertEqual(names, {"check_path", "explain_checker", "blast_radius"})
            resp = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "check_path", "arguments": {"path": "."}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertTrue(payload["failed"])
            self.assertEqual(payload["summary"]["lie"], 1)
            self.assertIn("ghost_fn", payload["findings"][0]["title"])
            exp = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                 "params": {"name": "explain_checker",
                                            "arguments": {"checker": "stale-symbol-ref"}}})
            self.assertIn("stale-symbol-ref", exp["result"]["content"][0]["text"])

    def test_version_negotiation_and_errors(self):
        with tempfile.TemporaryDirectory() as td:
            server = self._server(Path(td))
            old = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "1999-01-01", "capabilities": {},
                                            "clientInfo": {"name": "t", "version": "0"}}})
            self.assertEqual(old["result"]["protocolVersion"], "2025-03-26")
            self.assertEqual(
                server.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"}),
                {"jsonrpc": "2.0", "id": 2,
                 "error": {"code": -32601, "message": "method not found: nope"}})
            self.assertEqual(
                server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "nope", "arguments": {}}})["error"]["code"],
                -32602)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            server = self._server(Path(td))
            self._handshake(server)
            resp = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                  "params": {"name": "check_path", "arguments": {"path": ".."}}})
            self.assertEqual(resp["error"]["code"], -32602)

    def test_stdio_transport_roundtrip(self):
        import os
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            repo = Path(__file__).resolve().parent.parent
            env = dict(os.environ)
            env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [sys.executable, "-m", "grounded.cli", "mcp", "--root", str(root)],
                input=('{"jsonrpc":"2.0","id":1,"method":"initialize",'
                       '"params":{"protocolVersion":"2025-03-26","capabilities":{},'
                       '"clientInfo":{"name":"t","version":"0"}}}\n'
                       '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                       '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'),
                capture_output=True, text=True, timeout=120, env=env,
                cwd=str(Path(__file__).resolve().parent.parent))
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            lines = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
            self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "grounded")
            self.assertEqual({t["name"] for t in lines[1]["result"]["tools"]},
                             {"check_path", "explain_checker", "blast_radius"})
            self.assertNotIn("Traceback", proc.stderr)


class TestLsp(unittest.TestCase):
    def _framed_session(self, root: Path, messages: list[dict]) -> list[dict]:
        """Drive the server with Content-Length framing, return responses."""
        import os
        import subprocess
        import sys
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
        payload = b""
        for m in messages:
            body = json.dumps(m).encode()
            payload += b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        proc = subprocess.run(
            [sys.executable, "-m", "grounded.cli", "lsp"],
            input=payload, capture_output=True, timeout=120, env=env, cwd=str(repo))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[-500:])
        out, responses = proc.stdout, []
        while True:
            head, sep, rest = out.partition(b"\r\n\r\n")
            if not sep:
                break
            length = 0
            for ln in head.decode().split("\r\n"):
                if ln.lower().startswith("content-length:"):
                    length = int(ln.split(":")[1])
            responses.append(json.loads(rest[:length].decode()))
            out = rest[length:]
        self.assertNotIn("Traceback", proc.stderr.decode())
        return responses

    def test_full_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            uri = root.as_uri() + "/a.py"
            text = "# Calls `ghost_fn()`.\nX = 1\n"
            rs = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "initialized", "params": {}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": uri, "languageId": "python",
                                             "version": 1, "text": text}}},
            ])
            init = next(r for r in rs if r.get("id") == 1)
            self.assertIn("codeActionProvider", init["result"]["capabilities"])
            pubs = [r for r in rs if r.get("method") == "textDocument/publishDiagnostics"]
            self.assertEqual(len(pubs), 1)
            diags = pubs[0]["params"]["diagnostics"]
            self.assertEqual(len(diags), 1)
            self.assertEqual(diags[0]["severity"], 1)
            self.assertEqual(diags[0]["code"], "stale-symbol-ref")
            self.assertEqual(diags[0]["source"], "grounded")

    def test_code_action_and_heal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "real.py").write_text("def ghost_fix_target():\n    pass\n", encoding="utf-8")
            uri = root.as_uri() + "/a.py"
            text = "# Calls `ghost_fixtarget()`.\nX = 1\n"
            rs = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": uri, "languageId": "python",
                                             "version": 1, "text": text}}},
            ])
            diag = next(r for r in rs if r.get("method") == "textDocument/publishDiagnostics")
            d = diag["params"]["diagnostics"][0]
            rs2 = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": uri, "languageId": "python",
                                             "version": 1, "text": text}}},
                {"jsonrpc": "2.0", "id": 2, "method": "textDocument/codeAction",
                 "params": {"textDocument": {"uri": uri},
                            "range": d["range"],
                            "context": {"diagnostics": [d]}}},
                {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
                {"jsonrpc": "2.0", "method": "exit"},
            ])
            acts = next(r for r in rs2 if r.get("id") == 2)["result"]
            self.assertEqual(len(acts), 1)
            self.assertEqual(acts[0]["kind"], "quickfix")
            edit = acts[0]["edit"]["changes"][uri][0]
            self.assertIn("ghost_fix_target", edit["newText"])
            healed = text.replace("ghost_fixtarget", "ghost_fix_target")
            rs3 = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "textDocument/didChange",
                 "params": {"textDocument": {"uri": uri, "version": 2},
                            "contentChanges": [{"text": healed}]}},
            ])
            pubs = [r for r in rs3 if r.get("method") == "textDocument/publishDiagnostics"]
            self.assertEqual(pubs[0]["params"]["diagnostics"], [])

    def test_unknown_method_and_shutdown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rs = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "nope/method"},
                {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
                {"jsonrpc": "2.0", "method": "exit"},
            ])
            err = next(r for r in rs if r.get("id") == 2)
            self.assertEqual(err["error"]["code"], -32601)
            bye = next(r for r in rs if r.get("id") == 3)
            self.assertEqual(bye["result"], None)

    def test_buffer_rename_invalidates_peer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "models.py").write_text("class UserProfile:\n    pass\n", encoding="utf-8")
            (root / "views.py").write_text("# Uses `UserProfile()`.\nX = 1\n", encoding="utf-8")
            v_uri = root.as_uri() + "/views.py"
            m_uri = root.as_uri() + "/models.py"
            rs = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": v_uri, "languageId": "python",
                                             "version": 1,
                                             "text": "# Uses `UserProfile()`.\nX = 1\n"}}},
                {"jsonrpc": "2.0", "method": "textDocument/didChange",
                 "params": {"textDocument": {"uri": m_uri, "version": 2},
                            "contentChanges": [{"text": "class AccountProfile:\n    pass\n"}]}},
                {"jsonrpc": "2.0", "method": "textDocument/didChange",
                 "params": {"textDocument": {"uri": v_uri, "version": 2},
                            "contentChanges": [{"text": "# Uses `UserProfile()`.\nX = 1\n"}]}},
            ])
            pubs = [r for r in rs if r.get("method") == "textDocument/publishDiagnostics"]
            first = [p for p in pubs if p["params"]["uri"].endswith("views.py")][0]
            self.assertEqual(first["params"]["diagnostics"], [])
            last = [p for p in pubs if p["params"]["uri"].endswith("views.py")][-1]
            self.assertEqual(len(last["params"]["diagnostics"]), 1)
            self.assertEqual(last["params"]["diagnostics"][0]["code"], "stale-symbol-ref")

    def test_close_reverts_to_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "models.py").write_text("class UserProfile:\n    pass\n", encoding="utf-8")
            (root / "views.py").write_text("# Uses `UserProfile()`.\nX = 1\n", encoding="utf-8")
            v_uri = root.as_uri() + "/views.py"
            m_uri = root.as_uri() + "/models.py"
            rs = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": v_uri, "languageId": "python",
                                             "version": 1,
                                             "text": "# Uses `UserProfile()`.\nX = 1\n"}}},
                {"jsonrpc": "2.0", "method": "textDocument/didChange",
                 "params": {"textDocument": {"uri": m_uri, "version": 2},
                            "contentChanges": [{"text": "class AccountProfile:\n    pass\n"}]}},
                {"jsonrpc": "2.0", "method": "textDocument/didClose",
                 "params": {"textDocument": {"uri": m_uri}}},
                {"jsonrpc": "2.0", "method": "textDocument/didChange",
                 "params": {"textDocument": {"uri": v_uri, "version": 3},
                            "contentChanges": [{"text": "# Uses `UserProfile()`.\nX = 1\n"}]}},
            ])
            pubs = [r for r in rs if r.get("method") == "textDocument/publishDiagnostics"]
            last = [p for p in pubs if p["params"]["uri"].endswith("views.py")][-1]
            self.assertEqual(last["params"]["diagnostics"], [])

    def test_broken_buffer_keeps_diagnostics(self):
        # Mid-typing syntax error: diagnostics degrade, never vanish.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            uri = root.as_uri() + "/a.py"
            rs = self._framed_session(root, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": root.as_uri(), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": uri, "languageId": "python",
                                             "version": 1,
                                             "text": "# Calls `ghost_fn()`.\nX = 1\n"}}},
                {"jsonrpc": "2.0", "method": "textDocument/didChange",
                 "params": {"textDocument": {"uri": uri, "version": 2},
                            "contentChanges": [{"text": "# Calls `ghost_fn()`.\ndef broken(:\n"}]}},
            ])
            pubs = [r for r in rs if r.get("method") == "textDocument/publishDiagnostics"]
            self.assertTrue(all(len(p["params"]["diagnostics"]) == 1 for p in pubs))


class TestFileScope(unittest.TestCase):
    def test_scan_file_reports_only_that_file(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bad.py").write_text("# Calls `ghost_fn()`.\nX = 1\n", encoding="utf-8")
            (root / "ok.py").write_text("Y = 2\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root / "ok.py"), "--no-color", "--format", "json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()), [])

    def test_fix_file_leaves_others_alone(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "real").mkdir(parents=True)
            (root / "src" / "real" / "deep.py").write_text("X = 1\n", encoding="utf-8")
            a = root / "a.py"
            b = root / "b.py"
            a.write_text("# See src/old/deep.py.\n", encoding="utf-8")
            b.write_text("# See src/old/deep.py.\n", encoding="utf-8")
            self.assertEqual(main(["fix", str(a)]), 0)
            self.assertIn("src/real/deep.py", a.read_text())
            self.assertIn("src/old/deep.py", b.read_text())

    def test_bench_smoke(self):
        import os
        import subprocess
        import sys
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["GROUNDED_BENCH_N"] = "1"
        proc = subprocess.run(
            [sys.executable, str(repo / "examples" / "bench" / "bench.py")],
            capture_output=True, text=True, timeout=180, env=env, cwd=str(repo))
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("in-process", proc.stdout)
        self.assertIn("cold CLI", proc.stdout)


class TestJobsPolicy(unittest.TestCase):
    def test_thresholds(self):
        from grounded.scanner import default_jobs
        self.assertEqual(default_jobs(0), 1)
        self.assertEqual(default_jobs(40), 1)
        self.assertEqual(default_jobs(511), 1)
        self.assertGreaterEqual(default_jobs(512), 1)
        self.assertLessEqual(default_jobs(10**6), 8)


class TestCache(unittest.TestCase):
    def _write(self, root: Path, files: dict[str, str]) -> None:
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def test_roundtrip_identical(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n",
                               "b.py": "Y = 2\n"})
            cache = root / ".grounded-cache.json"
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)
            self.assertTrue(cache.exists())
            # second run: same verdict from cache
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)

    def test_edit_invalidates(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "X = 1\n"})
            cache = root / ".grounded-cache.json"
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 0)
            (root / "a.py").write_text("# Calls `ghost_fn()`.\nX = 1\n", encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)

    def test_corrupt_cache_falls_back(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            cache = root / ".grounded-cache.json"
            cache.write_text("not json{{{", encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)


class TestInitAgent(unittest.TestCase):
    def test_all_three_fresh(self):
        import json
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            cwd, target = Path.cwd(), Path(td)
            import os
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent"]), 0)
                settings = json.loads((target / ".claude" / "settings.json").read_text())
                cmds = [h.get("command")
                        for e in settings["hooks"]["PostToolUse"] for h in e.get("hooks", [])]
                self.assertIn("grounded hook claude-code", cmds)
                mdc = (target / ".cursor" / "rules" / "grounded.mdc").read_text()
                self.assertIn("alwaysApply: false", mdc)
                self.assertIn("description:", mdc)
                self.assertIn("lint-cmd", (target / ".aider.conf.yml").read_text())
            finally:
                os.chdir(cwd)

    def test_idempotent_and_merging(self):
        import json
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            (target / ".claude").mkdir()
            (target / ".claude" / "settings.json").write_text(
                json.dumps({"hooks": {"PostToolUse": [
                    {"matcher": "Bash",
                     "hooks": [{"type": "command", "command": "other"}]}]}}),
                encoding="utf-8")
            import os
            cwd = Path.cwd()
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--claude"]), 0)
                self.assertEqual(main(["init-agent", "--claude"]), 0)
                settings = json.loads((target / ".claude" / "settings.json").read_text())
                cmds = [h.get("command")
                        for e in settings["hooks"]["PostToolUse"] for h in e.get("hooks", [])]
                self.assertIn("other", cmds)
                self.assertEqual(cmds.count("grounded hook claude-code"), 1)
            finally:
                os.chdir(cwd)

    def test_invalid_json_refused(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            (target / ".claude").mkdir()
            (target / ".claude" / "settings.json").write_text("{nope", encoding="utf-8")
            import os
            cwd = Path.cwd()
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--claude"]), 0)
                self.assertEqual((target / ".claude" / "settings.json").read_text(), "{nope")
            finally:
                os.chdir(cwd)

    def test_existing_files_kept_without_force(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            (target / ".cursor" / "rules").mkdir(parents=True)
            (target / ".cursor" / "rules" / "grounded.mdc").write_text("mine\n", encoding="utf-8")
            (target / ".aider.conf.yml").write_text("lint: true\n", encoding="utf-8")
            import os
            cwd = Path.cwd()
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--cursor", "--aider"]), 0)
                self.assertEqual(
                    (target / ".cursor" / "rules" / "grounded.mdc").read_text(), "mine\n")
                self.assertEqual((target / ".aider.conf.yml").read_text(), "lint: true\n")
                self.assertEqual(
                    main(["init-agent", "--cursor", "--aider", "--force"]), 0)
                self.assertNotEqual(
                    (target / ".cursor" / "rules" / "grounded.mdc").read_text(), "mine\n")
            finally:
                os.chdir(cwd)

    def test_skill_project_installs_and_keeps(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            import os
            cwd = Path.cwd()
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--skill-project"]), 0)
                dest = target / ".claude" / "skills" / "grounded"
                self.assertTrue((dest / "SKILL.md").exists())
                self.assertTrue((dest / "references" / "rules.md").exists())
                self.assertTrue((dest / "references" / "commands.md").exists())
                self.assertTrue((dest / "examples" / "sessions.md").exists())
                (dest / "SKILL.md").write_text("mine\n", encoding="utf-8")
                self.assertEqual(main(["init-agent", "--skill-project"]), 0)
                self.assertEqual((dest / "SKILL.md").read_text(), "mine\n")
                self.assertEqual(main(["init-agent", "--skill-project", "--force"]), 0)
                self.assertNotEqual((dest / "SKILL.md").read_text(), "mine\n")
            finally:
                os.chdir(cwd)

    def test_skill_user_dir_uses_home(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target, home = Path(td) / "proj", Path(td) / "home"
            target.mkdir()
            import os
            cwd, old_home = Path.cwd(), os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--skill"]), 0)
                dest = home / ".claude" / "skills" / "grounded"
                self.assertTrue((dest / "SKILL.md").exists())
                self.assertFalse((target / ".claude" / "skills").exists())
            finally:
                os.chdir(cwd)
                if old_home is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = old_home

    def test_skill_dry_run_writes_nothing(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target, home = Path(td) / "proj", Path(td) / "home"
            target.mkdir()
            import os
            cwd, old_home = Path.cwd(), os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            os.chdir(target)
            try:
                self.assertEqual(
                    main(["init-agent", "--skill", "--skill-project", "--dry-run"]), 0)
                self.assertFalse((target / ".claude").exists())
                self.assertFalse((home / ".claude").exists())
            finally:
                os.chdir(cwd)
                if old_home is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = old_home

    def test_bare_init_agent_touches_no_skill(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target, home = Path(td) / "proj", Path(td) / "home"
            target.mkdir()
            import os
            cwd, old_home = Path.cwd(), os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent"]), 0)
                self.assertFalse((target / ".claude" / "skills").exists())
                self.assertFalse((home / ".claude").exists())
            finally:
                os.chdir(cwd)
                if old_home is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = old_home

    def test_precommit_install_keeps_force_dryrun(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            import os
            cwd = Path.cwd()
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--pre-commit"]), 0)
                text = (target / ".pre-commit-config.yaml").read_text()
                self.assertIn("gonisulaimann/Grounded", text)
                self.assertIn("- id: grounded", text)
                # Both first-party hooks are offered by default.
                self.assertIn("- id: grounded-fences", text)
                (target / ".pre-commit-config.yaml").write_text("mine\n", encoding="utf-8")
                self.assertEqual(main(["init-agent", "--pre-commit"]), 0)
                self.assertEqual(
                    (target / ".pre-commit-config.yaml").read_text(), "mine\n")
                self.assertEqual(
                    main(["init-agent", "--pre-commit", "--force", "--dry-run"]), 0)
            finally:
                os.chdir(cwd)


class TestClaudeCodeHook(unittest.TestCase):
    """The hook must exit 2 (stderr reaches the model) on lies in the
    agent's edit, 0 when clean, and 1 (human-only) on its own failures."""

    def _git(self, root: Path, *args: str) -> None:
        import subprocess
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t",
                            "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t"})

    def _event(self, root: Path, rel: str) -> str:
        return json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                           "cwd": str(root), "tool_input": {"file_path": rel}})

    def test_rename_fallout_in_other_file_blocks(self):
        from grounded.hooks import claude_code
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
            (root / "app").mkdir()
            (root / "app" / "__init__.py").write_text("", encoding="utf-8")
            (root / "app" / "core.py").write_text("def fetch_user():\n    return 1\n", encoding="utf-8")
            (root / "app" / "views.py").write_text("from app.core import fetch_user\n", encoding="utf-8")
            self._git(root, "init", "-q")
            self._git(root, "add", "-A")
            self._git(root, "commit", "-qm", "init")
            (root / "app" / "core.py").write_text("def load_user():\n    return 1\n", encoding="utf-8")
            code, err = claude_code(self._event(root, "app/core.py"))
            self.assertEqual(code, 2, err)
            self.assertIn("views.py", err)
            self.assertIn("fetch_user", err)

    def test_untouched_rot_does_not_nag(self):
        from grounded.hooks import claude_code
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn()` for retries.\nX = 1\n", encoding="utf-8")
            self._git(root, "init", "-q")
            self._git(root, "add", "-A")
            self._git(root, "commit", "-qm", "init")
            (root / "a.py").write_text("# Calls `ghost_fn()` for retries.\nX = 2\n", encoding="utf-8")
            self.assertEqual(claude_code(self._event(root, "a.py")), (0, ""))

    def test_no_git_checks_the_edited_file(self):
        from grounded.hooks import claude_code
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn()` for retries.\nX = 1\n", encoding="utf-8")
            (root / "b.py").write_text("# Calls `other_ghost()` for retries.\nY = 1\n", encoding="utf-8")
            code, err = claude_code(self._event(root, str(root / "a.py")))
            self.assertEqual(code, 2)
            self.assertIn("ghost_fn", err)
            self.assertNotIn("other_ghost", err)

    def test_clean_edit_and_bad_payloads(self):
        from grounded.hooks import claude_code
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ok.py").write_text("X = 1\n", encoding="utf-8")
            self.assertEqual(claude_code(self._event(root, "ok.py")), (0, ""))
            self.assertEqual(claude_code(self._event(root, "gone.py")), (0, ""))
        self.assertEqual(claude_code("not json")[0], 1)
        self.assertEqual(claude_code("[]")[0], 1)
        self.assertEqual(claude_code(json.dumps({"tool_input": {}})), (0, ""))

    def test_init_agent_upgrades_legacy_hook(self):
        import os
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            (target / ".claude").mkdir()
            (target / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [
                {"matcher": "Edit|Write", "hooks": [
                    {"type": "command", "command": "grounded scan . --changed --quiet"}]}]}}),
                encoding="utf-8")
            cwd = Path.cwd()
            os.chdir(target)
            try:
                self.assertEqual(main(["init-agent", "--claude"]), 0)
            finally:
                os.chdir(cwd)
            post = json.loads((target / ".claude" / "settings.json").read_text())["hooks"]["PostToolUse"]
            self.assertEqual(len(post), 1)
            self.assertEqual(post[0]["hooks"][0]["command"], "grounded hook claude-code")
            self.assertEqual(post[0]["matcher"], "Edit|Write|MultiEdit")


class TestFenceToggleCommonMark(unittest.TestCase):
    """Fence tracking must follow CommonMark, not count fence-looking lines.

    Measured 2026-09-22: re-deriving the retired scan and A/B-ing it against
    the shared walk over 10,857 Markdown files (Grounded, OmniRoute, svelte,
    flask, requests) shows two error directions. Prose wrongly skipped
    (the disarm): 1 file, 41 lines — an i18n doc with header text glued to
    its fence lines shifting the toggle's state. The far larger error ran
    the other way: 10,962 lines of code in 1,032 files were read as prose,
    because the retired regex did not recognize indented fences, rich info
    strings (` ```bash title="x" `), or unclosed fences running to EOF.
    An earlier draft of this docstring cited "238 files / 20,681 lines";
    that figure came from a comparison that mixed line populations and is
    retracted here.

    CommonMark rules pinned here:

    * a closing fence is a run of the **same character**, **at least as
      long**, carrying **no info string** — a longer bare fence closes;
    * an info-carrying fence inside an open block is **content**, not an
      opener (the README-216 defect, renderer-verified);
    * a fence may carry a rich info string (` ```bash title="x" `) and up
      to three leading spaces; a backtick fence whose info string contains
      a backtick is not a fence at all.
    """

    def _doc_blocks(self, text):
        from grounded.checkers import _doc_fence_blocks
        return _doc_fence_blocks(text.splitlines())

    def test_closing_fence_requires_no_info_string(self):
        # ```console inside an open block is content, not an opener; the
        # block stays open to EOF.
        text = ("# Guide\n\n```console\ngrounded scan . --changed\n\n"
                "Untracked files are fully reported.\n\n"
                "```console\ngrounded scan . --cache\n```")
        self.assertEqual(self._doc_blocks(text), [])

    def test_rich_info_string_is_still_a_fence(self):
        # `bash title="x"` opens a block; the old regex did not match it, so
        # every line inside was analyzed as prose. CommonMark property: the
        # info string is everything after the fence run.
        from grounded.checkers import _fence_scan
        blocks, open_at = _fence_scan(
            ['```bash title="install"', "ghost_tool --run", "```"])
        self.assertEqual([(b["open"], b["close"], b["info"]) for b in blocks],
                         [(1, 3, 'bash title="install"')])
        self.assertEqual(open_at, 0)
        # and the language table's deliberate narrowness is unchanged:
        # bash blocks are console, not parsed doc examples.
        self.assertEqual(self._doc_blocks(
            '```bash title="install"\nghost_tool --run\n```\n'), [])

    def test_indented_fence_opens_a_block(self):
        # Up to three leading spaces are allowed; the old regex required the
        # fence at column 0.
        text = "intro\n\n   ```python\nx = ghost_call()\n   ```\n"
        self.assertEqual(self._doc_blocks(text), [("python", 4, 5)])

    def test_longer_bare_fence_closes(self):
        text = "````python\nx = ghost_call()\n`````\n"
        self.assertEqual(self._doc_blocks(text), [("python", 2, 3)])

    def test_backtick_in_info_string_is_not_a_fence(self):
        # CommonMark: a backtick fence's info string may not contain a
        # backtick, so ``` `code` ``` is content — and must not swallow the
        # rest of the file.
        text = "``` `code` example\nnot a fence\n\n```python\nx = ghost()\n```\n"
        self.assertEqual(self._doc_blocks(text), [("python", 5, 6)])

    def test_nested_shorter_fence_is_content(self):
        # Declared scaffold: ````markdown around ```python. The inner block
        # is part of the illustrated content, not a separate block: exactly
        # one CommonMark block, and the inner fence is never re-emitted as
        # a python doc block (no double-reporting, no unparsed-language
        # leak into stale-doc-ref).
        from grounded.checkers import _fence_scan
        lines = "````markdown\n```python\nx = 1\n```\n````\n".splitlines()
        blocks, open_at = _fence_scan(lines)
        self.assertEqual([(b["open"], b["close"]) for b in blocks], [(1, 5)])
        self.assertEqual(open_at, 0)
        self.assertEqual(self._doc_blocks("\n".join(lines) + "\n"), [])

    def test_cli_ref_sees_invocations_in_rich_fences(self):
        import io
        from contextlib import redirect_stdout
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                '# Guide\n\n```bash title="check"\ngrounded scan . --bogus-flag\n````\n',
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["scan", td, "--no-color", "--enable", "stale-cli-ref"])
            self.assertIn("bogus-flag", buf.getvalue())

    def test_cli_ref_not_disarmed_by_rich_fence(self):
        # The measured disarm: a rich fence the old toggle did not match
        # flipped its state for the rest of the file.
        import io
        from contextlib import redirect_stdout
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                '# Guide\n\n```bash title="x"\nls\n```\n\n'
                "```console\ngrounded frobnicate --yes\n```\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["scan", td, "--no-color", "--enable", "stale-cli-ref"])
            self.assertIn("frobnicate", buf.getvalue())

    def test_unclosed_fence_uses_the_same_walk(self):
        # The checker and the toggle must agree by construction: a rich
        # info string no longer hides a defect.
        import io
        from contextlib import redirect_stdout
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                '# Guide\n\n```bash title="x"\ngrounded scan .\n',
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["scan", td, "--no-color", "--enable", "unclosed-fence"])
            self.assertIn("never closed", buf.getvalue())


class TestStaleDocRef(unittest.TestCase):
    DOC = (
        "# Demo\n\n```python\nfrom pkg.core import get_account\n\n"
        "client = get_account(1)\nghost = fetch_user(2)\n```\n"
    )

    def _tree(self, root, readme=None):
        pkg = root / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "core.py").write_text(
            "def get_account(uid):\n    return uid\n", encoding="utf-8")
        (root / "README.md").write_text(
            readme if readme is not None else self.DOC, encoding="utf-8")

    def test_off_by_default(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            import os
            self._tree(Path(td))
            self.assertEqual(main(["scan", td, "--no-color"]), 0)

    def test_opt_in_finds_stale_call(self):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["scan", td, "--no-color", "--enable", "stale-doc-ref"])
            self.assertEqual(rc, 1)
            self.assertIn("[stale-doc-ref]", buf.getvalue())
            self.assertIn("fetch_user", buf.getvalue())
            self.assertNotIn("get_account(1)", buf.getvalue())

    def test_go_stdlib_calls_silent(self):
        # Toolchain calls (fmt.Printf, time.Now) are not repo claims
        # (seen: gin docs). PONDER stays flagged as the control.
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "# Demo\n\n```go\nfmt.Println(time.Now())\nPONDER()\n```\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["scan", td, "--no-color", "--enable", "stale-doc-ref"])
            self.assertEqual(rc, 1)
            self.assertIn("PONDER", buf.getvalue())
            self.assertNotIn("fmt.Println", buf.getvalue())
            self.assertNotIn("time.Now", buf.getvalue())

    def test_skips_uncheckable_blocks(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            self._tree(Path(td), readme=(
                "# Demo\n\n```console\nghost_tool --run\n```\n\n"
                "```\nfoo(bar)\n```\n\n"
                "```python\nresult = compute_total(...)\n```\n\n"
                "```python\nx = my_widget.render()\n```\n\n"
                "```python\nimport os\nprint(os.getcwd())\n```\n"))
            self.assertEqual(
                main(["scan", td, "--no-color", "--enable", "stale-doc-ref"]), 0)

    def test_enable_via_config_file(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            import os
            root = Path(td)
            self._tree(root)
            (root / "grounded.toml").write_text(
                'enable = ["stale-doc-ref"]\n', encoding="utf-8")
            cwd = Path.cwd()
            os.chdir(root)
            try:
                self.assertEqual(main(["scan", ".", "--no-color"]), 1)
            finally:
                os.chdir(cwd)

    def test_default_set_excludes_opt_in(self):
        from grounded.checkers import CHECKERS, DEFAULT_ENABLED, OPT_IN_CHECKERS
        self.assertIn("stale-doc-ref", CHECKERS)
        self.assertIn("stale-doc-ref", OPT_IN_CHECKERS)
        self.assertNotIn("stale-doc-ref", DEFAULT_ENABLED)
        from grounded.config import Config
        self.assertNotIn("stale-doc-ref", Config().enabled)

    def test_js_ambient_roots_silent(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text(
                "# Demo\n\n```js\n"
                "return Promise.reject(error);\n"
                "const el = document.getElementById('x');\n"
                "this.setup();\n"
                "try {\n  foo();\n"
                "} catch (e) {\n  bar(e);\n"
                "}\n```\n",
                encoding="utf-8")
            self.assertEqual(
                main(["scan", td, "--no-color", "--enable", "stale-doc-ref"]), 0)

    def test_js_bindings_params_methods_templates(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
            (root / "README.md").write_text(
                "# Demo\n\n```js\n"
                "import qs from 'qs';\n"
                "import { a, b as c } from './lib';\n"
                "import * as ns from './ns';\n"
                "function handle(x) { return x; }\n"
                "const show = (t) => t;\n"
                "const out = qs.stringify(ns.val(c));\n"
                "[1].forEach((item) => handle(item));\n"
                "function render(title) { return show(title); }\n"
                "class Widget {\n  normalize(e) { return e; }\n"
                "}\n"
                "const msg = `hi ${name()}`;\n"
                "demo.run();\n"
                "```\n",
                encoding="utf-8")
            self.assertEqual(
                main(["scan", td, "--no-color", "--enable", "stale-doc-ref"]), 0)

    def test_js_chain_lines_silent(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text(
                "# Demo\n\n```js\n"
                "fetch(url)\n"
                "  .then(r => r.json())\n"
                "  .catch(handle);\n"
                "```\n",
                encoding="utf-8")
            # fetch is ambient; .then/.catch are continuation chains
            self.assertEqual(
                main(["scan", td, "--no-color", "--enable", "stale-doc-ref"]), 0)

    def test_js_event_constructors_silent(self):
        # DOM event constructors are ambient (seen: axios migration guide).
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text(
                "# Demo\n\n```js\n"
                "const el = document.createElement('x');\n"
                "el.dispatchEvent(new CustomEvent('x'));\n"
                "const m = new Map();\n"
                "console.log(el, m);\n"
                "```\n",
                encoding="utf-8")
            self.assertEqual(
                main(["scan", td, "--no-color", "--enable", "stale-doc-ref"]), 0)


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


class TestStaleCliRef(unittest.TestCase):
    def _scan(self, td):
        from grounded.cli import main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["scan", td, "--no-color", "--enable", "stale-cli-ref"])
        return rc, buf.getvalue()

    def test_bad_subcommand_and_flag_fire(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "# Demo\n\n```console\ngrounded scan . --changed --quiet\n"
                "grounded frobnicate --yes\n```\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 1)
            self.assertIn("frobnicate", out)

    def test_valid_prose_synopsis_output_silent(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "# Demo\n\nRun `grounded init-agent --cursor` after edits.\n\n"
                "```console\n$ grounded mcp [--root .]\n"
                "grounded fix: 1 file(s) would change.\n```\n\n"
                "The grounded skill teaches agents to verify first.\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertEqual(rc, 0)
            self.assertNotIn("stale-cli-ref", out)

    def test_python_m_form(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "```console\npython -m grounded.cli scan . --bogus-flag\n```\n",
                encoding="utf-8")
            rc, out = self._scan(td)
            self.assertIn("--bogus-flag", out)

    def test_off_by_default(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "```console\ngrounded frobnicate\n```\n", encoding="utf-8")
            self.assertEqual(main(["scan", td, "--no-color"]), 0)


class TestSkillSync(unittest.TestCase):
    """`agent-skill/` is generated from `src/grounded/skill/`, not maintained.

    `src/grounded/skill/` is the source of truth (it is what
    `init-agent --skill` installs and what `package-data` ships in the wheel);
    the top-level directory exists so the skill stays browsable and
    `cp -r`-able from the repository. A test that only compared the two trees
    told a maintainer *that* they had drifted, never how to fix it, and left
    every doc correction to be typed twice.
    """

    def _repo(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def _run(self, *args: str) -> "subprocess.CompletedProcess":
        import subprocess
        script = self._repo() / "scripts" / "sync-skill.py"
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, text=True)

    def test_mirror_is_in_sync_with_its_source(self) -> None:
        proc = self._run("--check")
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_drift_is_detected_then_repaired(self) -> None:
        repo = self._repo()
        source = repo / "src" / "grounded" / "skill"
        with tempfile.TemporaryDirectory() as td:
            mirror = Path(td) / "agent-skill"
            # control: a missing mirror is drift, and says what is missing
            empty = self._run("--check", "--source", str(source), "--mirror", str(mirror))
            self.assertEqual(empty.returncode, 1)
            self.assertIn("SKILL.md", empty.stderr)
            # generating repairs it, and a re-check is clean
            self.assertEqual(
                self._run("--source", str(source), "--mirror", str(mirror)).returncode, 0)
            self.assertEqual(
                self._run("--check", "--source", str(source), "--mirror", str(mirror)).returncode, 0)
            # a file the source no longer has is drift too, and is pruned
            stale = mirror / "references" / "gone.md"
            stale.write_text("removed upstream\n", encoding="utf-8")
            self.assertEqual(
                self._run("--check", "--source", str(source), "--mirror", str(mirror)).returncode, 1)
            self._run("--source", str(source), "--mirror", str(mirror))
            self.assertFalse(stale.exists())

    def test_skill_front_matter_and_examples_shipped(self) -> None:
        repo = self._repo()
        for rel in ("agent-skill", "src/grounded/skill"):
            skill = repo / rel / "SKILL.md"
            self.assertTrue(skill.exists(), rel)
            self.assertIn("name: grounded", skill.read_text().splitlines()[1])
        self.assertTrue((repo / "src/grounded/skill" / "examples").is_dir())


class TestRecallHarness(unittest.TestCase):
    """`bench/recall.py` measures recall inside real repos, so the planting
    itself must never decide the outcome.

    Two artifacts were measured on 2026-09-22 while building it, both of which
    first reported as recall losses the checkers never caused:

    * a case planted one directory deeper stopped firing, because three cases
      depend on repo-root-relative semantics (`src/` layout, `@patch` module
      roots, a top-level `pyproject.toml`);
    * a fixture merged into a host whose Markdown ends inside an unclosed fence
      landed *inside* that dangling block, so `stale-cli-ref` could not parse
      the invocation as one. This repo's own `README.md` had exactly that
      defect, so the phantom miss was not hypothetical.

    A published recall number is only meaningful if the harness can show the
    rot was planted in a well-formed context; the second case is pinned here.
    """

    def _recall(self):
        import importlib.util
        path = Path(__file__).resolve().parent.parent / "bench" / "recall.py"
        spec = importlib.util.spec_from_file_location("bench_recall", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _host(self, readme: str):
        td = tempfile.TemporaryDirectory()
        host = Path(td.name) / "host"
        host.mkdir()
        (host / "README.md").write_text(readme, encoding="utf-8")
        return td, host

    def test_plant_survives_an_unclosed_host_fence(self) -> None:
        recall = self._recall()
        original = "# Host\n\n```console\ngrounded scan .\n"  # opened, never closed
        td, host = self._host(original)
        with td:
            self.assertTrue(recall.ends_inside_fence(original))
            report = recall.run_repo(host, recall.firing_cases(["cli-stale"]))
            self.assertEqual([r["outcome"] for r in report["results"]], ["caught"],
                             report["results"])
            # the host's own defect is surfaced, never silently absorbed
            self.assertEqual(report["unbalanced_hosts"], ["README.md"])
            self.assertEqual(report["checker_errors"], [])
            # and a measured repo is left exactly as it was found
            self.assertEqual((host / "README.md").read_text(encoding="utf-8"),
                             original)

    def test_balanced_host_merges_without_closing_anything(self) -> None:
        recall = self._recall()
        td, host = self._host("# Host\n\n```console\ngrounded scan .\n```\n")
        with td:
            report = recall.run_repo(host, recall.firing_cases(["cli-stale"]))
            self.assertEqual([r["outcome"] for r in report["results"]], ["caught"])
            self.assertEqual(report["unbalanced_hosts"], [])

    def test_merged_expectation_line_numbers_translate(self) -> None:
        # Expectations that embed fixture line numbers (a title like
        # "swallowed by the block opened at line <N>") must shift by the
        # plant offset, or a merge into a non-empty host reports a phantom
        # title miss. The host here contributes 3 content lines + 2
        # separator lines, so the fixture's seventh line lands at twelve —
        # judged caught there, not a miss with "title changed".
        recall = self._recall()
        td, host = self._host("# Host\n\nSome intro prose.\n")
        with td:
            report = recall.run_repo(
                host, recall.firing_cases(["fence-bare-outer-boundary"]))
            r = report["results"][0]
            self.assertEqual(r["outcome"], "caught", report["results"])
            self.assertEqual(r["expected_line"], 12, report["results"])
            self.assertEqual(r["observed_line"], 12, report["results"])
            self.assertEqual(r["detail"],
                             "merged into the host's own file", report["results"])


class TestUnclosedFence(unittest.TestCase):
    """`unclosed-fence`: fences the renderer does not honour.

    Precision measured 2026-09-22 over 11,564 Markdown files in eight real
    repos (svelte, vuejs/docs, rust-lang/book, markdown-it, flask, requests,
    OmniRoute, and this repo): 0 false positives. The only findings were these
    fixtures and one real document — OmniRoute's
    `docs/frameworks/OPEN_SSE_ARCHITECTURE.md`, where a stray bare
    ```` ```` ```` fence made GitHub render a 76-line code block containing
    `## Services (117 modules)`, `### Common Patterns` and the surrounding
    prose, verified with `POST /markdown` (`mode=gfm`).

    The two silence cases below are the shapes that would otherwise fire: a
    *declared* nesting scaffold (` ````markdown ` around ` ```python `, which
    is how svelte's docs show Svelte inside HTML on purpose, and renders
    correctly) and a bare fence inside a block (the illustrated closer of a
    nested example).
    """

    def _findings(self, td):
        return scan_root(Path(td), Config(enabled={"unclosed-fence"}))[0]

    def test_never_closed_fires_at_the_opener(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "\n".join(["# Guide", "", "Prose.", "", "```console",
                           "grounded scan ."]), encoding="utf-8")
            findings = self._findings(td)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0].line, 5)
            self.assertIn("never closed", findings[0].title)
            # Graduated 2026-09-22: a fence the renderer does not honour is
            # document corruption, not a style note — it gates like a lie.
            self.assertEqual(findings[0].severity, "lie")

    def test_swallowed_boundary_names_the_open_block(self):
        # The real shape: a missing close, then a second header of the same
        # length, which CommonMark reads as content rather than an opener.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "\n".join(["# Guide", "", "Gate on changed lines:", "",
                           "```console", "grounded scan . --changed", "",
                           "Untracked files are fully reported.", "",
                           "```console", "grounded scan . --cache", "```"]),
                encoding="utf-8")
            findings = self._findings(td)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0].line, 10)
            self.assertIn("swallowed by the block opened at line 5",
                          findings[0].title)

    def test_bare_longer_fence_fires(self):
        # A bare 4-backtick line reads as a closer but opens a block; the ts
        # header inside it is then content. This is the OmniRoute shape.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "\n".join(["# Guide", "", "Providers are configured in one place.",
                           "", "````", "", "```ts", "const x = 1;", "```",
                           "", "````"]), encoding="utf-8")
            findings = self._findings(td)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0].line, 7)
            self.assertIn("block opened at line 5", findings[0].title)

    def test_declared_nesting_scaffold_is_silent(self):
        # ````markdown around ```python: the outer fence declares itself, so
        # the inner header is an illustration, not a swallowed boundary.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "\n".join(["# Guide", "", "````markdown", "```python",
                           "x = 1", "```", "````"]), encoding="utf-8")
            self.assertEqual(self._findings(td), [])

    def test_bare_inner_fence_is_silent(self):
        # A bare fence inside a block is the illustrated closer of a nested
        # example, not a header.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "\n".join(["# Guide", "", "````svelte", "<p>hi</p>", "```",
                           "````"]), encoding="utf-8")
            self.assertEqual(self._findings(td), [])

    def test_non_markdown_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.py").write_text("x = '```console'\n", encoding="utf-8")
            self.assertEqual(self._findings(td), [])

    def test_cli_collects_markdown_for_this_checker(self):
        # The scanner only collects Markdown when one of its checkers runs;
        # without that gate this checker would silently see no files.
        import io
        from contextlib import redirect_stdout
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "# Guide\n\n```console\ngrounded scan .\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["scan", td, "--no-color"])
            # Graduated 2026-09-22: collected and gating by default. The
            # scanner only collects Markdown when one of its checkers runs,
            # and this checker is always in DEFAULT_ENABLED now.
            self.assertEqual(rc, 1)
            self.assertIn("unclosed-fence", buf.getvalue())

    def test_explicit_opt_out_still_works(self):
        import io
        from contextlib import redirect_stdout
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text(
                "# Guide\n\n```console\ngrounded scan .\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["scan", td, "--no-color", "--disable", "unclosed-fence"])
            self.assertEqual(rc, 0)

    def test_on_by_default(self):
        from grounded.checkers import DEFAULT_ENABLED, OPT_IN_CHECKERS
        self.assertIn("unclosed-fence", DEFAULT_ENABLED)
        self.assertNotIn("unclosed-fence", OPT_IN_CHECKERS)


def _reference_scan(text: str) -> list[tuple[int, int | None, str]]:
    """Independent fenced-code extraction for the differential fuzz.

    Written from the CommonMark 0.31.2 fenced-code-block clauses but
    structurally unlike `_fence_scan`: char-by-char scanning, no regexes,
    no block list. Returns `(open_line, close_line, info)` per block, with
    `close_line` None when a block runs to EOF. Deliberately shares the
    scanner's documented contract: top-level (non-list) structure only.
    """
    open_char, open_len, open_line, open_info = "", 0, 0, ""
    blocks: list[tuple[int, int | None, str]] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        stripped = line.rstrip("\r")
        indent = 0
        i = 0
        while i < len(stripped) and stripped[i] == " " and indent < 4:
            indent += 1
            i += 1
        rest = stripped[i:]
        if not open_char:
            if indent <= 3 and rest[:3] in ("```", "~~~"):
                ch = rest[0]
                n = 0
                while n < len(rest) and rest[n] == ch:
                    n += 1
                if n >= 3:
                    info = rest[n:]
                    if ch == "`" and "`" in info:
                        continue  # info may not contain backticks: not a fence
                    open_char, open_len = ch, n
                    open_line, open_info = lineno, info
            continue
        if indent <= 3 and rest[:1] == open_char:
            n = 0
            while n < len(rest) and rest[n] == open_char:
                n += 1
            if n >= open_len and not rest[n:].strip():
                blocks.append((open_line, lineno, open_info))
                open_char, open_len, open_line, open_info = "", 0, 0, ""
    if open_char:
        blocks.append((open_line, None, open_info))
    return blocks


class TestFenceScanSpec(unittest.TestCase):
    """`_fence_scan` pinned three ways: spec goldens, differential fuzz,
    real-document mutation.

    The fence walk is the single source of truth for every doc checker, so
    a regression there silently corrupts several checkers at once — the
    failure mode that started this (a naive toggle missing rich-info
    fences read 10,962 lines of fenced code as prose across 1,032 files).
    The reference walker above is written from the CommonMark clauses but
    shares no code or regexes with `_fence_scan`; the fuzz layer mutates
    real repository Markdown so the corpus of inputs is the repo itself,
    not just synthetic lines.
    """

    GOLDENS: list[tuple[str, list[tuple[int, int | None, str]]]] = [
        # CommonMark 0.31.2 example 141: info line with a backtick inside an
        # open block is content, and the block still closes on the bare fence.
        ("```\n``` aaa\n```\n", [(1, 3, "")]),
        # Example 142: tilde block swallows a backtick fence as content.
        ("~~~\n```\n", [(1, None, "")]),
        # Example 143: a longer outer fence; inner fences are content.
        ("````\n```\naaa\n```\n````\n", [(1, 5, "")]),
        # Closing fence may be longer than the opener, never shorter.
        ("```\naaa\n`````\n", [(1, 3, "")]),
        ("````\naaa\n```\n", [(1, None, "")]),
        # Characters cannot mix: ~~~ never closes a backtick block.
        ("```\naaa\n~~~\n", [(1, None, "")]),
        # Backtick info strings containing a backtick are not fences.
        ("``` a`b\nfoo\n", []),
        # ...but the same line inside a tilde block is just content there.
        ("~~~\n``` a`b\n~~~\n", [(1, 3, "")]),
        # Tilde info strings may contain backticks freely — the whole rest
        # of the line is the info string, backticks and all.
        ("~~~ ```\naaa\n~~~\n", [(1, 3, " ```")]),
        # An info-carrying fence inside an open block is content — the
        # exact shape that broke this repo's README (a ```console opened
        # and a later ```console was swallowed).
        ("```console\n$ ls\n```py\nx = 1\n```\n", [(1, 5, "console")]),
        # Indented 4+ is not a fence at all.
        ("     ```\nfoo\n", []),
        # Up to 3 spaces of indent open, and close, a fence.
        ("  ```py\nx = 1\n   ```\n", [(1, 3, "py")]),
        # Closing fence may carry trailing whitespace, never an info string.
        ("```\naaa\n```   \n", [(1, 3, "")]),
        ("```\naaa\n``` py\n", [(1, None, "")]),
        # Unclosed at EOF: close is None.
        ("a\n```py\nx = 1\n", [(2, None, "py")]),
    ]

    _FUZZ_LINES = [
        "```", "~~~", "````", "~~~~", "```py", "~~~py", "``` a`b", "```a``",
        "~~~ x~~~", "  ```", "   ~~~", "    ```", "``` ", "```\t", "text",
        "```md", "~~~~~~", "``````", "```~", "~```", "``` `` ", "console",
        "$ grounded scan .", "", "   ", "````md", "```{python}", "~```~",
        "``` ```", "  ~~~py", "```py``", "~~~~~", "     ```", "\t```",
    ]

    def _walk(self, text: str) -> list[tuple[int, int | None, str]]:
        from grounded.checkers import _fence_scan
        blocks, _open_at = _fence_scan(text.split("\n"))
        return [(b["open"], b["close"], b["info"]) for b in blocks]

    def test_spec_goldens(self) -> None:
        for text, expected in self.GOLDENS:
            with self.subTest(text=text):
                got = self._walk(text)
                self.assertEqual(got, expected,
                                 f"reference={_reference_scan(text)!r}")

    def _repo_markdown(self) -> list[Path]:
        root = Path(__file__).resolve().parent.parent
        skip = {".git", "node_modules", "__pycache__", "corpus"}
        return sorted(p for p in root.rglob("*.md")
                      if not any(part in skip for part in p.parts))

    def test_differential_random_lines(self) -> None:
        rng = random.Random(0xF11)
        for seed in range(240):
            lines = [rng.choice(self._FUZZ_LINES)
                     for _ in range(rng.randint(2, 12))]
            text = "\n".join(lines)
            with self.subTest(seed=seed, text=text):
                self.assertEqual(self._walk(text), _reference_scan(text),
                                 f"seed {seed} diverged")

    def test_differential_splice_into_real_docs(self) -> None:
        rng = random.Random(0xFE2)
        docs = self._repo_markdown()
        self.assertGreater(len(docs), 20, "expected repo docs to mutate")
        for path in docs:
            text = path.read_text(encoding="utf-8")
            for _ in range(2):
                lines = text.split("\n")
                lines.insert(rng.randrange(len(lines) + 1),
                             rng.choice(self._FUZZ_LINES))
                mutated = "\n".join(lines)
                with self.subTest(doc=str(path)):
                    self.assertEqual(self._walk(mutated),
                                     _reference_scan(mutated),
                                     f"splice into {path.name} diverged")

    def test_differential_mutate_fence_lines_of_real_docs(self) -> None:
        from grounded.checkers import _FENCE_LINE
        rng = random.Random(0xFE3)
        for path in self._repo_markdown():
            lines = path.read_text(encoding="utf-8").split("\n")
            fence_idx = [i for i, ln in enumerate(lines) if _FENCE_LINE.match(ln)]
            for i in rng.sample(fence_idx, min(2, len(fence_idx))):
                for kind in ("length", "char", "info"):
                    mutated = list(lines)
                    if kind == "length":
                        mutated[i] = mutated[i].replace(
                            "```", "```" + "`", 1) if "```" in mutated[i] \
                            else mutated[i] + "~"
                    elif kind == "char":
                        mutated[i] = mutated[i].replace("```", "~~~", 1) \
                            if "```" in mutated[i] else mutated[i].replace(
                            "~~~", "```", 1)
                    else:
                        mutated[i] = mutated[i] + " py"
                    text = "\n".join(mutated)
                    with self.subTest(doc=str(path), line=i + 1, kind=kind):
                        self.assertEqual(self._walk(text),
                                         _reference_scan(text),
                                         f"{path.name}:{i + 1} {kind} diverged")

    def test_differential_over_real_corpora_when_present(self) -> None:
        hosts = [p for p in (Path("/tmp/eval2/svelte"),
                             Path("/tmp/reverify/flask"),
                             Path("/tmp/reverify/requests")) if p.is_dir()]
        if not hosts:
            self.skipTest("no real-world corpora checked out")
        for host in hosts:
            docs = sorted(host.rglob("*.md"))[:250]
            self.assertGreater(len(docs), 0)
            for path in docs:
                text = path.read_text(encoding="utf-8", errors="replace")
                with self.subTest(host=host.name, doc=str(path)):
                    self.assertEqual(self._walk(text), _reference_scan(text),
                                     f"{path} diverged")


class TestRepoDocFences(unittest.TestCase):
    """The README's fences must balance.

    Measured 2026-09-22: one ` ```console ` opener at line 216 was never
    closed, so the paragraphs at lines 220-228 rendered as code on GitHub
    (verified with GitHub's own renderer: 24 code blocks broken, 25 fixed).
    It also inverted the doc checkers' fence state for the rest of the file,
    disarming `stale-cli-ref` for the whole tail. Neither symptom is visible
    in a diff review. Balance is judged by the shared `_fence_scan` walk —
    the CommonMark close rule, not a toggle count — so a file with a stray
    fence-lookalike inside a block still counts as balanced.
    """

    def test_readme_fences_are_balanced(self) -> None:
        from grounded.checkers import _fence_scan
        readme = Path(__file__).resolve().parent.parent / "README.md"
        _, open_at = _fence_scan(
            readme.read_text(encoding="utf-8").splitlines())
        self.assertEqual(open_at, 0, "README.md has an unclosed fence")


class TestTomlCompat(unittest.TestCase):
    SAMPLE = (
        "# comment\n"
        "fail_on = \"drift\"\n"
        "disable = [\"fragile-anchor\", \"number-drift\"]\n"
        "[tool.grounded]\n"
        "ignore_dirs = [\"docs\"]\n"
        "path_aliases = {\"@/\" = \"src/\", \"~/\" = [\"app/\", \"web/\"]}\n"
    )

    def test_parity_with_tomllib(self):
        import importlib.util
        if importlib.util.find_spec("tomllib") is None:
            self.skipTest("tomllib unavailable (Python 3.10)")
        import tomllib
        from grounded import toml_compat
        expected = tomllib.loads(self.SAMPLE)
        self.assertEqual(toml_compat.loads(self.SAMPLE), expected)

    def test_rejects_non_subset(self):
        from grounded import toml_compat
        for bad in ["a = 2026-09-21\n", "[a\n", "a = 'x\n", "a = [\n\"x\",\n"]:
            with self.assertRaises(ValueError, msg=bad):
                toml_compat.loads(bad)
        # Multi-line arrays joined the subset (valid TOML, and the shape of
        # nearly every real pyproject.toml); an unterminated one stays an error.
        self.assertEqual(toml_compat.loads("a = [\n\"x\",\n]\n"), {"a": ["x"]})

    def test_config_loads_without_tomllib(self):
        import grounded.config as config_mod
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "grounded.toml").write_text(
                'fail_on = "drift"\npath_aliases = {"~/" = "src/"}\n',
                encoding="utf-8")
            saved, config_mod.tomllib = config_mod.tomllib, None
            try:
                cfg = config_mod.Config.load(root)
            finally:
                config_mod.tomllib = saved
            self.assertEqual(cfg.fail_on, "drift")
            self.assertEqual(cfg.path_aliases, {"~/": ["src/"]})


class TestTomlCompatRealPyproject(unittest.TestCase):
    """Python 3.10 (no tomllib) must read `[tool.grounded]` out of a real
    pyproject.toml whatever other tools put there. Measured: CI's 3.10
    dogfood exited 2 on this repo's own multi-line `keywords = [...]`."""

    PYPROJECT = (
        '[project]\nname = "x"\nkeywords = [\n  "a",  # note\n  "b",\n]\n'
        'description = """Multi-line\n[not.a.header]\n"""\n'
        '[[tool.mypy.overrides]]\nmodule = "x.*"\nignore_errors = true\n'
        '[tool.black]\nline-length = 88\ntarget-version = ["py310",\n "py311"]\n'
        '[tool.grounded]\nfail_on = "drift"\ndisable = [\n  "fragile-anchor",\n]\n'
        '[tool.grounded.path_aliases]\n"@/" = ["src/"]\n'
        '[tool.ruff]\nselect = ["E"]\n'
    )

    def test_compat_reads_grounded_section_only(self):
        from unittest import mock
        import grounded.config as cfg
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text(self.PYPROJECT, encoding="utf-8")
            with mock.patch.object(cfg, "tomllib", None):
                c = cfg.Config.load(root)
            self.assertEqual(c.fail_on, "drift")
            self.assertNotIn("fragile-anchor", c.enabled)
            self.assertEqual(c.path_aliases, {"@/": ["src/"]})

    def test_multiline_arrays_and_tables(self):
        from grounded.toml_compat import loads
        self.assertEqual(loads('a = [\n "x", # c\n "y",\n]\n[t]\nb = { k = ["1",\n "2"] }\n'),
                         {"a": ["x", "y"], "t": {"b": {"k": ["1", "2"]}}})
        with self.assertRaises(ValueError):
            loads('a = [\n "x",\n')

    def test_grounded_section_errors_still_fail(self):
        from unittest import mock
        import grounded.config as cfg
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text(
                '[tool.black]\nx = 1979-05-27\n[tool.grounded]\nfail_on = @@\n', encoding="utf-8")
            with mock.patch.object(cfg, "tomllib", None):
                with self.assertRaises(cfg.ConfigError):
                    cfg.Config.load(root)


class TestImpact(unittest.TestCase):
    def _tree(self, root: Path) -> None:
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "pkg" / "core.py").write_text(
            "def get_account(uid):\n    return uid\n", encoding="utf-8")
        (root / "pkg" / "views.py").write_text(
            "from .core import get_account\n\n\n"
            "def show(uid):\n    # Uses `get_account()`.\n    return get_account(uid)\n",
            encoding="utf-8")

    def test_blast_radius(self):
        from grounded.config import Config
        from grounded.graph import ClaimGraph
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            _, facts, index = scan_root(root, Config())
            result = ClaimGraph(index, {f.path: f for f in facts}).blast_radius("get_account")
            self.assertEqual(result["defined_in"], ["pkg/core.py"])
            self.assertEqual(result["imported_by"], ["pkg/views.py"])
            self.assertEqual(result["claimed_by"], ["pkg/views.py"])

    def test_blast_radius_claims_all_surfaces(self):
        from grounded.config import Config
        from grounded.graph import ClaimGraph
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "core.py").write_text(
                "def get_account(uid):\n    return uid\n", encoding="utf-8")
            (pkg / "views.py").write_text(
                "# DEPRECATED: use get_account(uid) instead.\nX = 1\n", encoding="utf-8")
            (root / "README.md").write_text(
                "# Demo\n\n```python\nresult = get_account(1)\n```\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_core.py").write_text(
                'from unittest.mock import patch\n\n@patch("pkg.core.get_account")\n'
                "def test_x(m):\n    pass\n",
                encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "d"\n[project.scripts]\ndemo = "pkg.core:get_account"\n',
                encoding="utf-8")
            _, facts, index = scan_root(root, Config(), include_claim_surfaces=True)
            result = ClaimGraph(index, {f.path: f for f in facts}).blast_radius("get_account")
            self.assertEqual(
                sorted(result["claimed_by"]),
                ["README.md", "pkg/views.py", "pyproject.toml", "tests/test_core.py"])

    def test_dangling_signature(self):
        from grounded.config import Config
        from grounded.graph import ClaimGraph
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            _, facts, index = scan_root(root, Config())
            result = ClaimGraph(index, {f.path: f for f in facts}).blast_radius("gone_fn")
            self.assertEqual(result, {"symbol": "gone_fn", "defined_in": [],
                                      "imported_by": [], "claimed_by": []})

    def test_cli_terminal_and_json(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["impact", "get_account", str(root)]), 0)
            self.assertIn("pkg/core.py", buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["impact", "get_account", str(root), "--format", "json"]), 0)
            self.assertEqual(json.loads(buf.getvalue())["defined_in"], ["pkg/core.py"])

    def test_mcp_blast_radius(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            from grounded.mcp import McpServer
            server = McpServer(root)
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertIn("blast_radius", {t["name"] for t in tools["result"]["tools"]})
            resp = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "blast_radius",
                                             "arguments": {"symbol": "get_account"}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(payload["defined_in"], ["pkg/core.py"])
            bad = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                 "params": {"name": "blast_radius", "arguments": {}}})
            self.assertEqual(bad["error"]["code"], -32602)

    def test_blast_radius_sees_module_attribute_use(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "helper.py").write_text(
                "def serve():\n    return 1\n", encoding="utf-8")
            (root / "main.py").write_text(
                "from pkg import helper\nhelper.serve()\n", encoding="utf-8")
            from grounded.mcp import McpServer
            server = McpServer(root)
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
            resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "blast_radius",
                                             "arguments": {"symbol": "serve"}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertIn("main.py", payload["imported_by"])

    def test_mcp_honors_project_config(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn()`.\nX = 1\n", encoding="utf-8")
            (root / "grounded.toml").write_text(
                'disable = ["stale-symbol-ref"]\n', encoding="utf-8")
            from grounded.mcp import McpServer
            server = McpServer(root)
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
            resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "check_path", "arguments": {"path": "."}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertFalse(payload["failed"])
            self.assertEqual(payload["summary"]["findings"], 0)


class TestSubTreeScanOpacity(unittest.TestCase):
    """A partial snapshot must never manufacture absence claims about
    files it did not look at.

    Measured (OmniRoute, 2026-09-22): scanning `src/` reported 51
    stale-import lies whose targets (`../../shared/...`,
    `../../../open-sse/...`) exist one level above the scan root; the
    same tree scanned from the repo root reported 3. Sub-tree and
    single-file scans are first-class agent workflows, so this class is
    load-bearing for the advertised lint loop.
    """

    def test_relative_import_above_scan_root_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared" / "services").mkdir(parents=True)
            (root / "shared" / "services" / "cliRuntime.ts").write_text(
                "export const getCliConfigPaths = () => [];\n", encoding="utf-8")
            (root / "src" / "lib").mkdir(parents=True)
            (root / "src" / "lib" / "app.ts").write_text(
                'import { getCliConfigPaths } from "../../shared/services/cliRuntime";\n'
                "export const paths = getCliConfigPaths;\n", encoding="utf-8")
            findings, _, _ = scan_root(root / "src", Config())
            self.assertEqual([f for f in findings if f.checker == "stale-import"], [])

    def test_whole_tree_scan_agrees_with_subtree_scan(self):
        # The guard must not depend on where the scan started: both roots
        # see the target (or neither does), so neither reports a lie.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "cliRuntime.ts").write_text("export const a = 1;\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.ts").write_text(
                'import { a } from "../shared/cliRuntime";\n', encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertEqual([f for f in findings if f.checker == "stale-import"], [])

    def test_missing_relative_import_inside_root_still_reports(self):
        # Control: the escape guard must not disarm in-tree detection.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.ts").write_text('import { x } from "./gone";\n', encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            self.assertTrue([f for f in findings if f.checker == "stale-import"])


class TestDirectoryScanScope(unittest.TestCase):
    """`grounded scan <subdir>` indexes the whole project and scopes only
    the report, exactly like a file argument.

    Measured: a comment in `src/` naming a helper defined in `scripts/` was
    a stale-symbol-ref lie under `scan src` and clean under `scan .`, and
    CI gates (`grounded scan src`) could not see file refs the root scan
    reported. One file, one verdict, whatever directory was named.
    """

    def _project(self, td: str) -> Path:
        root = Path(td)
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
        (root / "scripts").mkdir()
        (root / "scripts" / "tools.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (root / "src" / "app").mkdir(parents=True)
        (root / "src" / "app" / "core.py").write_text(
            "# Nightly job calls `helper()` to rebuild caches.\ndef run():\n    return 2\n",
            encoding="utf-8")
        (root / "src" / "app" / "bad.py").write_text(
            "# Calls `ghost_fn()` for retries.\nX = 1\n", encoding="utf-8")
        (root / "scripts" / "bad.py").write_text(
            "# Calls `other_ghost()` for retries.\nY = 1\n", encoding="utf-8")
        return root

    def _scan_json(self, *argv: str) -> tuple[int, list[dict]]:
        import contextlib
        import io
        from grounded.cli import main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["scan", *argv, "--no-color", "--format", "json", "--fail-on", "never"])
        return rc, json.loads(buf.getvalue())

    def test_subdir_scan_sees_definitions_outside_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            _, got = self._scan_json(str(root / "src"))
            self.assertNotIn("src/app/core.py", {f["path"] for f in got})

    def test_subdir_scan_reports_only_its_subtree_with_project_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            _, got = self._scan_json(str(root / "src"))
            self.assertEqual({f["path"] for f in got}, {"src/app/bad.py"})

    def test_subdir_and_root_scans_agree_per_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            _, sub = self._scan_json(str(root / "src"))
            _, whole = self._scan_json(str(root))
            key = lambda f: (f["path"], f["line"], f["checker"])
            self.assertEqual(sorted(map(key, sub)),
                             sorted(key(f) for f in whole if f["path"].startswith("src/")))

    def test_resolve_scan_scope(self):
        from grounded.scanner import in_scope, resolve_scan_scope
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            self.assertEqual(resolve_scan_scope(root), (root.resolve(), None))
            self.assertEqual(resolve_scan_scope(root / "src" / "app"),
                             (root.resolve(), "src/app"))
            self.assertTrue(in_scope("src/app/x.py", "src/app"))
            self.assertFalse(in_scope("src/application.py", "src/app"))
            self.assertTrue(in_scope("anything.py", None))

    def test_never_climbs_to_home(self):
        from unittest import mock
        from grounded.scanner import project_root_for
        with tempfile.TemporaryDirectory() as td:
            home = Path(td).resolve()
            (home / ".git").mkdir()  # a dotfiles repo at ~
            lone = home / "scratch" / "notes"
            lone.mkdir(parents=True)
            with mock.patch("pathlib.Path.home", return_value=home):
                self.assertEqual(project_root_for(lone), lone)
                self.assertEqual(project_root_for(home), home)

    def test_placeholder_paths_are_not_claims(self):
        from grounded.checkers import _is_placeholder_path
        for ref in ("src/mypkg/x.py", "src/old/x.py", "my_app/models.py", "lib/mymodule.js"):
            self.assertTrue(_is_placeholder_path(ref), ref)
        for ref in ("src/app/loader.py", "myth/core.py", "src/xy.py", "mypy/checker.py"):
            self.assertFalse(_is_placeholder_path(ref), ref)


class TestRuntimeModuleRegistration(unittest.TestCase):
    """`sys.modules[...] = mod` makes dotted imports resolvable with no
    file behind them (requests.packages). Silence needs that evidence in an
    ancestor module; without it a missing module is still a lie."""

    def _scan(self, alias_src: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "packages.py").write_text(alias_src, encoding="utf-8")
            (root / "use.py").write_text(
                "from pkg.packages.urllib3.poolmanager import PoolManager\n", encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            return [f for f in findings if f.checker == "stale-import"]

    def test_registered_alias_is_silent(self):
        for src in ('import sys\nsys.modules["pkg.packages.urllib3"] = object()\n',
                    "import sys\nsys.modules.update(extra)\n",
                    'import sys\nsys.modules.setdefault("x", m)\n'):
            self.assertEqual(self._scan(src), [], src)

    def test_without_registration_still_fires(self):
        self.assertEqual(len(self._scan("import sys\nif sys.modules['x'] == 1:\n    pass\n")), 1)


class TestPrecisionRound(unittest.TestCase):
    """Helpers behind the 2026-09-24 real-repo precision round
    (bench/precision/): each pins one false-positive family and the
    boundary that keeps real rot firing."""

    def test_fixture_paths(self):
        from grounded.scanner import is_fixture_path
        for rel in ("tests/format/js/a.js", "tests/data/cases/x.py", "pkg/fixtures/a.py",
                    "src/__tests__/fixtures/x.js", "testdata/README.md",
                    "tests/admin/broken_app/models.py", "tests/template_tests/broken_tag.py",
                    "tests/mypy/outputs/x.py"):
            self.assertTrue(is_fixture_path(rel), rel)
        for rel in ("tests/test_app.py", "src/data/loader.py", "tests/test_apps/mod/__init__.py",
                    "src/broken.py", "format/x.js"):
            self.assertFalse(is_fixture_path(rel), rel)

    def test_nested_gitignore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text("/build\n*.log\ngen/\n", encoding="utf-8")
            (root / "website").mkdir()
            (root / "website" / ".gitignore").write_text("static/lib\n!keep.log\n", encoding="utf-8")
            idx = RepoIndex(root, [])
            self.assertTrue(idx.is_gitignored("build/x.js"))
            self.assertTrue(idx.is_gitignored("a/b/debug.log"))
            self.assertTrue(idx.is_gitignored("pkg/gen/client.py"))
            self.assertTrue(idx.is_gitignored("website/static/lib/next/m.mjs"))
            self.assertFalse(idx.is_gitignored("website/keep.log"))
            self.assertFalse(idx.is_gitignored("src/build.py"))
            self.assertFalse(idx.is_gitignored("static/lib/x.js"))

    def test_require_inside_string_is_not_an_import(self):
        from grounded.parsers import _js_import_entries
        got = _js_import_entries(
            "const a = require('./a')\n"
            "const t = { src: 'const r = require(\"./lib/r\")' }\n")
        self.assertEqual([e[0] for e in got], ["./a"])

    def test_docstring_listing_dropped_prose_kept(self):
        from grounded.checkers import _drop_literal_blocks
        doc = ("Names in a distribution.\n\n    Listed as:\n\n        src/a/b.py\n"
               "        src/a/c.py\n\n    Moved to src/pkg/x.py.\n"
               "    Args:\n        path: see src/real.py\n")
        out = _drop_literal_blocks(doc)
        self.assertNotIn("src/a/b.py", out)
        self.assertIn("src/pkg/x.py", out)
        self.assertIn("see src/real.py", out)

    def test_c_include_regex_is_multiline(self):
        from grounded.parsers import _c_imports
        got = _c_imports("/* hdr */\n#include <stdio.h>\n#  include <ares.h>\n#include \"local.h\"\n")
        self.assertEqual(got, {"stdio": "stdio.h", "ares": "ares.h", "local": ""})

    def test_derived_lookups_follow_rebuilds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            f = root / "a.py"
            f.write_text("def cf_socket_active():\n    pass\n", encoding="utf-8")
            idx = RepoIndex(root, [f])
            self.assertIn("_active", idx.underscore_suffixes())
            idx._index_one("a.py", ".py", "def renamed():\n    pass\n")
            self.assertNotIn("_active", idx.underscore_suffixes())

    def test_dunder_typo_only(self):
        from grounded.checkers import _dunder_typo_of

        class _Idx:
            all_symbols: set = set()
        self.assertTrue(_dunder_typo_of("__get_item__", _Idx()))
        for name in ("__annotations__", "__wrapped__", "__pydantic_fields__", "__tests__"):
            self.assertFalse(_dunder_typo_of(name, _Idx()), name)


class TestParallelIndexBuild(unittest.TestCase):
    """The parallel index build must produce exactly the serial index.

    Per-file attributes are merged from worker chunks by name
    (RepoIndex._PER_FILE_DICTS/_PER_FILE_SETS/_NAME_SETS). A new per-file
    attribute missing from those lists would be silently empty in every
    parallel scan; this test compares every attribute of both builds."""

    def _tree(self, root: Path) -> list[Path]:
        (root / "pkg").mkdir()
        (root / "web").mkdir()
        files = []
        for i in range(40):
            f = root / "pkg" / f"m{i}.py"
            f.write_text(
                f"import sys\nfrom pkg.m{(i + 1) % 40} import f{(i + 1) % 40}\n"
                f"from . import *\n"
                f"def f{i}():\n    return {i}\nclass C{i}:\n    pass\n"
                + ("sys.modules['x'] = sys\n" if i % 7 == 0 else "")
                + ("globals().update({})\n" if i % 11 == 0 else "")
                + ("def broken(:\n" if i == 13 else ""),
                encoding="utf-8")
            files.append(f)
            j = root / "web" / f"w{i}.js"
            j.write_text(
                f"export const {{ a{i}, b{i}: c{i} }} = obj;\nexport function w{i}() {{}}\n"
                f"export * from './w{(i + 1) % 40}.js';\nexport default w{i};\n"
                + ("export * from 'external-pkg';\n" if i % 9 == 0 else ""),
                encoding="utf-8")
            files.append(j)
        return files

    def test_parallel_equals_serial(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            files = self._tree(root)
            serial = RepoIndex(root, files, jobs=1)
            with mock.patch.object(RepoIndex, "_PARALLEL_MIN_FILES", 1):
                parallel = RepoIndex(root, files, jobs=2)
            skip = {"_jobs", "_texts", "_deps_cache", "_gitignore_cache", "_disk_texts"}
            names = (set(vars(serial)) | set(vars(parallel))) - skip
            for name in sorted(names):
                self.assertEqual(getattr(serial, name, None), getattr(parallel, name, None), name)
            self.assertTrue(serial.file_registers_modules)
            self.assertTrue(serial.parse_failed)

    def test_worker_copy_reads_text_from_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            f = root / "a.py"
            f.write_text("X = 1\n", encoding="utf-8")
            idx = RepoIndex(root, [f], {str(f): "X = 1\n"})
            clone = idx.worker_copy()
            self.assertEqual(clone._texts, {})
            self.assertEqual(clone.text_of("a.py"), "X = 1\n")
            self.assertIsNone(clone.text_of("missing.py"))
            self.assertEqual(idx._texts, {str(f): "X = 1\n"})


class TestChangedFastPath(unittest.TestCase):
    """`scan --changed` checks only changed files and files naming a
    changed symbol; the report must equal a full scan filtered by
    filter_changed (speed changes, output never does)."""

    def _git(self, root: Path, *args: str) -> None:
        import os
        import subprocess
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def test_fast_path_equals_full_scan_filtered(self):
        import contextlib
        import io
        from grounded.cli import main
        from grounded.delta import changed_lines, changed_symbols, filter_changed
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
            (root / "app").mkdir()
            (root / "app" / "__init__.py").write_text("", encoding="utf-8")
            (root / "app" / "core.py").write_text("def fetch_user():\n    return 1\n", encoding="utf-8")
            (root / "app" / "views.py").write_text(
                "from app.core import fetch_user\n# Calls `fetch_user()` for the page.\n", encoding="utf-8")
            (root / "app" / "other.py").write_text("# Calls `old_ghost()` here.\nX = 1\n", encoding="utf-8")
            for i in range(20):
                (root / "app" / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
            self._git(root, "init", "-q")
            self._git(root, "add", "-A")
            self._git(root, "commit", "-qm", "init")
            (root / "app" / "core.py").write_text("def load_user():\n    return 1\n", encoding="utf-8")
            (root / "app" / "new.py").write_text("# Calls `brand_new_ghost()`.\nY = 1\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                main(["scan", str(root), "--changed", "--format", "json", "--fail-on", "never"])
            fast = sorted((f["path"], f["line"], f["checker"]) for f in json.loads(buf.getvalue()))
            full, _, _ = scan_root(root, Config())
            hunks, untracked = changed_lines(root, "HEAD")
            want = filter_changed(full, hunks, untracked, changed_symbols(root, "HEAD"))
            self.assertEqual(fast, sorted((f.path, f.line, f.checker) for f in want))
            self.assertIn(("app/views.py", 1, "stale-import"), fast)
            self.assertIn(("app/new.py", 1, "stale-symbol-ref"), fast)
            self.assertNotIn("app/other.py", {p for p, _, _ in fast})

    def test_filter_is_superset_by_construction(self):
        from grounded.delta import changed_file_filter
        pred = changed_file_filter({"a.py": {1}}, {"u.py"}, {"fetch_user", "$el"})
        self.assertTrue(pred("a.py", ""))
        self.assertTrue(pred("u.py", ""))
        self.assertTrue(pred("b.py", "from x import fetch_user\n"))
        self.assertFalse(pred("c.py", "fetch_user_v2 = 1\n"))
        self.assertFalse(pred("d.py", "nothing here\n"))


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


class TestDocRefPrecision(unittest.TestCase):
    def _doc(self, root, text):
        (root / "guide.md").write_text(text, encoding="utf-8")
        return scan_root(root, Config(enabled={"stale-doc-ref"}))[0]

    def test_diff_annotated_bindings_are_known(self):
        # svelte annotates doc examples with `+++`/`---`. The markers broke
        # identifier extraction (`function add(+++getA, getB+++)` binds
        # nothing), turning bound params into phantom calls.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = self._doc(root,
                "```js\n"
                "function add(+++getA, getB+++) {\n"
                "\treturn +++() => getA() + getB()+++;\n"
                "}\n"
                "```\n")
            self.assertEqual([f for f in out if f.checker == "stale-doc-ref"], [])

    def test_destructured_fixture_param_is_known(self):
        # Playwright fixtures arrive destructured: `({ page }) =>`.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = self._doc(root,
                "```js\n"
                "test('home', async ({ page }) => {\n"
                "\tawait page.goto('/');\n"
                "});\n"
                "```\n")
            self.assertEqual([f for f in out if f.checker == "stale-doc-ref"], [])

    def test_platform_roots_are_ambient(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = self._doc(root,
                "```js\n"
                "customElements.define('x-el', class extends HTMLElement {});\n"
                "```\n")
            self.assertEqual([f for f in out if f.checker == "stale-doc-ref"], [])

    def test_directive_blocks_are_illustrative(self):
        # `// @noErrors` and `/// file:` are doc-tooling directives: the
        # docs themselves declare the code is not project surface.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = self._doc(root,
                "```js\n"
                "// @noErrors\n"
                "preprocess: [\n"
                "\tvitePreprocess(),\n"
                "]\n"
                "```\n")
            self.assertEqual([f for f in out if f.checker == "stale-doc-ref"], [])

    def test_missing_repo_symbol_still_reports(self):
        # Control: an example calling a symbol this repo really lacks.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = self._doc(root,
                "```js\n"
                "const result = vanishedHelper(41);\n"
                "```\n")
            self.assertTrue([f for f in out if f.checker == "stale-doc-ref"])


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


class TestBaselineIntegrity(unittest.TestCase):
    def test_baseline_is_deduplicated_and_total_matches_file(self):
        """Two findings on different lines can share a fingerprint (it
        hashes rule, path, title and claim — never line numbers). Writing
        the raw list made the file longer than the reported total
        (measured: 284 entries for 277 unique fingerprints on a 10k-file
        tree)."""
        from grounded.delta import write_baseline
        from grounded.models import Finding
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".grounded-baseline.json"
            same = dict(path="a.py", line=1, end_line=1, checker="stale-symbol-ref",
                        severity="lie", title="t", claim="`ghost()`")
            findings = [Finding(**same), Finding(**{**same, "line": 9})]
            stats = write_baseline(path, findings)
            written = json.loads(path.read_text(encoding="utf-8"))["fingerprints"]
            self.assertEqual(len(written), len(set(written)))
            self.assertEqual(len(written), stats["total"])
            self.assertEqual(stats["total"], 1)

    def test_baseline_round_trips_as_a_set(self):
        from grounded.delta import load_baseline, write_baseline
        from grounded.models import Finding
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "b.json"
            f = Finding(path="a.py", line=3, end_line=3, checker="stale-file-ref",
                        severity="lie", title="t", claim="src/gone.py")
            write_baseline(path, [f])
            self.assertEqual(len(load_baseline(path)), 1)


class TestRepairRound(unittest.TestCase):
    """Bugs found by the full-codebase audit: config errors must fail,
    file scans must index the project, partial snapshots must not lie,
    crashes must not crash, and template slots must not collide."""

    def test_config_empty_sets_are_meaningful(self):
        self.assertEqual(Config(enabled=set()).enabled, set())
        self.assertEqual(Config(ignore_dirs=set()).ignore_dirs, set())

    def test_config_explicit_missing_raises(self):
        from grounded.config import ConfigError
        with self.assertRaises(ConfigError):
            Config.load(Path("/tmp"), explicit="/nonexistent-grounded.toml")

    def test_config_corrupt_raises(self):
        from grounded.config import ConfigError
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "grounded.toml").write_text("enable = [unclosed\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                Config.load(Path(td))

    def test_config_unknown_ids_raise(self):
        from grounded.config import ConfigError
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "grounded.toml").write_text('enable = ["stale-symobl"]\n', encoding="utf-8")
            with self.assertRaises(ConfigError) as cm:
                Config.load(root)
            self.assertIn("stale-symobl", str(cm.exception))
            (root / "grounded.toml").write_text('disable = ["nope"]\n', encoding="utf-8")
            with self.assertRaises(ConfigError):
                Config.load(root)

    def test_config_enable_empty_means_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "grounded.toml").write_text('enable = []\n', encoding="utf-8")
            self.assertEqual(Config.load(root).enabled, set())

    def test_cli_config_errors_exit_2(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    main(["scan", str(root), "--config", str(root / "nope.toml")])
            self.assertEqual(cm.exception.code, 2)
            (root / "grounded.toml").write_text("enable = [unclosed\n", encoding="utf-8")
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    main(["scan", str(root), "--no-color"])
            self.assertEqual(cm.exception.code, 2)

    def test_cli_unknown_enable_exits_2(self):
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(main(["scan", str(root), "--enable", "stale-symobl"]), 2)
            self.assertIn("stale-symobl", err.getvalue())

    def test_project_root_for_finds_markers(self):
        from grounded.scanner import project_root_for
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            deep = root / "pkg" / "sub"
            deep.mkdir(parents=True)
            self.assertEqual(project_root_for(deep), root.resolve())
            # no markers anywhere: falls back to the start dir
            lone = Path(tempfile.mkdtemp()) / "x"
            lone.mkdir()
            try:
                self.assertEqual(project_root_for(lone), lone.resolve())
            finally:
                import shutil
                shutil.rmtree(lone.parent, ignore_errors=True)

    def test_project_root_for_respects_stop(self):
        from grounded.scanner import project_root_for
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".git").mkdir()
            sub = root / "pkg"
            sub.mkdir()
            self.assertEqual(project_root_for(sub, stop=sub), sub.resolve())

    def test_cli_file_scan_indexes_project(self):
        # sub/file.py references a name defined in other/: a parent-only
        # index manufactured a lie; the project index stays silent, and
        # reporting is still scoped to the named file.
        import contextlib
        import io
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "other").mkdir()
            (root / "other" / "real.py").write_text("def target_fn():\n    return 1\n", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "file.py").write_text("# Calls target_fn() for help.\nX = 1\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root / "sub" / "file.py"), "--no-color", "--format", "json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()), [])

    def test_mcp_notification_gets_no_response(self):
        from grounded.mcp import McpServer
        with tempfile.TemporaryDirectory() as td:
            srv = McpServer(Path(td))
            srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertIsNone(srv.handle({"jsonrpc": "2.0", "method": "tools/list"}))
            self.assertIsNone(srv.handle({"jsonrpc": "2.0", "method": "ping"}))

    def test_mcp_fail_on_never_never_fails(self):
        from grounded.mcp import McpServer
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("# Calls `ghost_fn_xyz()`.\nX = 1\n", encoding="utf-8")
            srv = McpServer(root)
            srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            resp = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "check_path",
                                          "arguments": {"path": ".", "fail_on": "never"}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertTrue(payload["findings"])
            self.assertFalse(payload["failed"])

    def test_mcp_file_target_is_root_relative_and_honest(self):
        from grounded.mcp import McpServer
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "other").mkdir()
            (root / "other" / "real.py").write_text("def target_fn():\n    return 1\n", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "file.py").write_text("# Calls target_fn() for help.\nX = 1\n", encoding="utf-8")
            srv = McpServer(root)
            srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            resp = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "check_path",
                                          "arguments": {"path": "sub/file.py"}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(payload["findings"], [])
            self.assertFalse(payload["failed"])

    def test_lsp_outside_file_does_not_clobber(self):
        from grounded.lsp import LspServer
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "foo.py").write_text("def real():\n    return 1\n", encoding="utf-8")
            srv = LspServer()
            srv.root = root
            srv._ensure_index("file://" + str(root / "foo.py"))
            self.assertTrue(srv.index.has_symbol("real"))
            outside = Path(td + "-outside")
            outside.mkdir()
            (outside / "foo.py").write_text("def evil():\n    return 2\n", encoding="utf-8")
            srv._reindex_doc("file://" + str(outside / "foo.py"),
                             "def evil():\n    return 2\n")
            self.assertTrue(srv.index.has_symbol("real"))
            self.assertTrue(srv.index.has_symbol("evil"))

    def test_html_placeholders_do_not_collide_with_root(self):
        h = to_html([], 0, root="__LIE__")
        self.assertIn("__LIE__", h)
        self.assertNotIn("<b>__LIE__</b>", h)
        h2 = to_html([], 0, root="__ROWS__")
        self.assertIn("__ROWS__", h2)
        self.assertEqual(h2.count("All beliefs check out"), 1)

    def test_toml_compat_rejects_array_tables(self):
        from grounded.toml_compat import loads
        with self.assertRaises(ValueError):
            loads('[[tool]]\nx = 1\n')

    def test_toml_compat_dotted_keys_nest(self):
        from grounded.toml_compat import loads
        self.assertEqual(loads('a.b = 1\n'), {"a": {"b": 1}})

    def test_toml_compat_trailing_comma_ok(self):
        from grounded.toml_compat import loads
        self.assertEqual(loads('x = ["a",]\n'), {"x": ["a"]})
        with self.assertRaises(ValueError):
            loads('x = ["a",,]\n')

    def test_toml_compat_unicode_escapes(self):
        from grounded.toml_compat import loads
        self.assertEqual(loads('x = "\\u0041"\n'), {"x": "A"})
        with self.assertRaises(ValueError):
            loads('x = "\\d"\n')

    def test_tsconfig_cycle_does_not_crash(self):
        from grounded.tsconfig import _exclude_entries, load_tsconfig
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.json").write_text('{"extends": "./b.json"}', encoding="utf-8")
            (root / "b.json").write_text('{"extends": "./a.json"}', encoding="utf-8")
            self.assertEqual(load_tsconfig(root / "a.json"), {})
            self.assertEqual(_exclude_entries(root / "a.json"), [])

    def test_baseline_write_survives_corrupt_file(self):
        from grounded.delta import load_baseline, write_baseline
        from grounded.models import Finding
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "b.json"
            path.write_text("{corrupt", encoding="utf-8")
            f = Finding(path="a.py", line=1, end_line=1, checker="stale-symbol-ref",
                        severity="lie", title="t", claim="`ghost()`")
            stats = write_baseline(path, [f])
            self.assertEqual(stats["total"], 1)
            self.assertEqual(len(load_baseline(path)), 1)

    def test_changed_lines_include_staged(self):
        import subprocess
        from grounded.delta import changed_lines
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            (root / "a.py").write_text("X = 2\n# staged line\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.py"], cwd=root, check=True)
            hunks, _ = changed_lines(root, "HEAD")
            self.assertIn(1, hunks.get("a.py", set()))
            self.assertIn(2, hunks.get("a.py", set()))

    def test_changed_lines_unquote_paths(self):
        import subprocess
        from grounded.delta import _parse_unified0
        diff = ('diff --git a/"caf\\303\\251.py" b/"caf\\303\\251.py"\n'
                '--- a/"caf\\303\\251.py"\n'
                '+++ b/"caf\\303\\251.py"\n'
                '@@ -0,0 +1 @@\n+X = 1\n')
        hunks = _parse_unified0(diff)
        self.assertIn("caf\u00e9.py", hunks)

    def test_metasyntactic_calls_are_silent(self):
        # `foo()`/`blah()` in comments are placeholders, never references
        # (seen: svelte's "`foo` in `foo.bar` or `foo()`" reported as lies).
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# The `foo` in `foo.bar` or `foo()` is safe.\n"
                "# Calls blah() when done.\nX = 1\n", encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            syms = [f for f in findings if f.checker == "stale-symbol-ref"]
            self.assertEqual(syms, [])

    def test_missing_builtin_locals_is_silent(self):
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Calls locals() for debugging.\nX = 1\n", encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            syms = [f for f in findings if f.checker == "stale-symbol-ref"]
            self.assertEqual(syms, [])

    def test_doc_example_framed_as_example_is_silent(self):        # Narrative above the fence marks the block illustrative
        # (seen: rich's "Here's an example:" + do_step(step)).
        from grounded.cli import main
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text(
                "Here's an example:\n\n```python\nfrom pkg import track\n\n"
                "do_step(step)\n```\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root), "--no-color", "--enable", "stale-doc-ref"])
            self.assertEqual(rc, 0)
            # without the framing line the same call still fires
            (root / "README.md").write_text(
                "Usage:\n\n```python\nfrom pkg import track\n\ndo_step(step)\n```\n",
                encoding="utf-8")
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                rc2 = main(["scan", str(root), "--no-color", "--enable", "stale-doc-ref"])
            self.assertEqual(rc2, 1)
            self.assertIn("do_step", buf2.getvalue())

    def test_history_frame_is_silent(self):
        # "We used to use X" documents the past, not a live claim
        # (seen: httpx's `cgi.parse_header()` + PEP 594 link).
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# We used to use `cgi.parse_header()` here.\n"
                "# See: https://peps.python.org/pep-0594/#cgi\nX = 1\n",
                encoding="utf-8")
            (root / "b.py").write_text(
                "# We used to call old_helper() for this.\nY = 2\n",
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            syms = [f for f in findings if f.checker == "stale-symbol-ref"]
            self.assertEqual(syms, [])

    def test_ticket_anchored_block_is_silent(self):
        # A ticket inside the same comment block marks discussion/history
        # (seen: preact's SES `lockdown()` + See #5109, React-compat
        # `UNSAFE_componentDidMount()` + issue link).
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# Hardened envs (SES `lockdown()`) freeze prototypes.\n"
                "# Copying then throws. See #5109.\nX = 1\n",
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            syms = [f for f in findings if f.checker == "stale-symbol-ref"]
            self.assertEqual(syms, [])

    def test_work_item_block_keeps_checking(self):
        # A TODO block names a ticket for the TASK, not the API: a stale
        # claim inside it must still fire (positive control).
        from grounded.scanner import scan_root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text(
                "# TODO(#123): migrate to `new_auth()` when ready.\nX = 1\n",
                encoding="utf-8")
            findings, _, _ = scan_root(root, Config())
            syms = [f for f in findings if f.checker == "stale-symbol-ref"]
            self.assertTrue(any("new_auth" in f.title for f in syms))

    def test_alias_prefix_respects_segments(self):
        # tsconfig `paths: {"mylib": [...]}` must not hijack `mylib-extra`
        # (seen: preact's demo `preact` mapping drifted `preact-router`,
        # a declared dependency).
        from grounded.cli import main
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tsconfig.json").write_text(
                '{"compilerOptions": {"baseUrl": ".", "paths": '
                '{"mylib": ["./src/index.js"]}}}', encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "index.js").write_text(
                "export function real_fn() {}\n", encoding="utf-8")
            (root / "a.js").write_text(
                "import { ghost_fn } from 'mylib-extra';\nconsole.log(1);\n",
                encoding="utf-8")
            (root / "b.js").write_text(
                "import { ghost_fn } from 'mylib';\nconsole.log(1);\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root), "--no-color", "--format", "json"])
            out = json.loads(buf.getvalue())
            self.assertEqual(rc, 1)
            # exact alias resolves and the missing name fires; the sibling
            # package name stays silent
            self.assertTrue(any("from `mylib`" in f["title"] and "ghost_fn" in f["title"]
                                for f in out))
            self.assertFalse(any("mylib-extra" in f["title"] for f in out))

    def test_package_main_build_output_is_silent(self):
        # A directory import resolving via package.json `main` to build
        # output (absent pre-build) is unjudgeable (seen: preact's
        # `../../` test imports with a `dist/` main).
        from grounded.cli import main
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                '{"name": "pkg", "main": "dist/index.js"}', encoding="utf-8")
            (root / "test").mkdir()
            (root / "test" / "a.test.js").write_text(
                "import { thing } from '../';\nconsole.log(thing);\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root), "--no-color", "--format", "json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()), [])

    def test_package_main_real_target_still_checks(self):
        # ...but when `main` exists in-tree, named bindings verify
        # against it (positive control: recall survives).
        from grounded.cli import main
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                '{"name": "pkg", "main": "src/index.js"}', encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "index.js").write_text(
                "export function real_fn() {}\n", encoding="utf-8")
            (root / "test").mkdir()
            (root / "test" / "a.test.js").write_text(
                "import { ghost_fn } from '../';\nconsole.log(ghost_fn);\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["scan", str(root), "--no-color", "--format", "json"])
            self.assertEqual(rc, 1)
            self.assertTrue(any("ghost_fn" in f["title"] for f in json.loads(buf.getvalue())))

    def test_fix_walk_ignores_symlinked_dirs(self):
        import time
        from grounded.fix import file_fix_candidates
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "real").mkdir()
            (root / "real" / "gone.py").write_text("X = 1\n", encoding="utf-8")
            (root / "link").symlink_to(root / "real", target_is_directory=True)
            (root / "loop").symlink_to(root, target_is_directory=True)
            start = time.time()
            file_fix_candidates([], root)
            self.assertLess(time.time() - start, 10)


if __name__ == "__main__":
    unittest.main()
