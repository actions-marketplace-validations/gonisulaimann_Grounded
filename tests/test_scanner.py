"""File collection, indexing, scan scoping, caching and parallelism.

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

    def test_edit_to_another_file_invalidates(self):
        # a.py's own stat never changes, but its finding depends on b.py:
        # replaying a.py's cached `clean` after b.py renamed the imported
        # function was a false green.
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"pyproject.toml": '[project]\nname = "p"\n',
                               "app/__init__.py": "",
                               "app/b.py": "def helper():\n    return 1\n",
                               "app/a.py": "from app.b import helper\n"})
            cache = root / ".grounded-cache.json"
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 0)
            (root / "app" / "b.py").write_text("def renamed():\n    return 1\n", encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)

    def test_corrupt_cache_falls_back(self):
        from grounded.cli import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write(root, {"a.py": "# Calls `ghost_fn()`.\nX = 1\n"})
            cache = root / ".grounded-cache.json"
            cache.write_text("not json{{{", encoding="utf-8")
            self.assertEqual(main(["scan", str(root), "--no-color", "--cache", str(cache)]), 1)


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

    def test_entries_capture_everything_index_one_writes(self):
        # Every build (serial, parallel, cached) now merges per-file entries,
        # so comparing builds with each other cannot catch an attribute that
        # index_entry fails to capture or _apply_entry fails to replay. The
        # reference here writes straight into one index via _index_one.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            files = self._tree(root)
            built = RepoIndex(root, files)
            direct = RepoIndex(root, [])
            for f in files:
                direct._index_one(f.relative_to(root).as_posix(), f.suffix.lower(),
                                  f.read_text(encoding="utf-8"), rebuild=False)
            direct._rebuild_unions()
            names = (RepoIndex._PER_FILE_DICTS + RepoIndex._PER_FILE_SETS
                     + RepoIndex._NAME_SETS + ("symbol_files", "all_symbols", "lower_map"))
            for name in names:
                self.assertEqual(getattr(built, name), getattr(direct, name), name)
            written = {n for n, v in vars(direct).items() if v} - {"root", "files", "parent_tops",
                                                                   "root_package_names", "top_names",
                                                                   "_jobs", "_index_cache", "_stats"}
            self.assertLessEqual(written, set(names), "attribute written by _index_one "
                                 "but not carried by index entries")

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


class TestIndexCache(unittest.TestCase):
    """The persisted per-file index contributions (index_cache.py) must
    rebuild exactly the index a fresh build gives, and must never be
    trusted when the file may have changed under the same stat."""

    _SKIP = {"_jobs", "_texts", "_deps_cache", "_gitignore_cache", "_disk_texts",
             "_index_cache", "_stats"}

    def _stats(self, files):
        return {str(f): (f.stat().st_mtime_ns, f.stat().st_size) for f in files}

    def _age(self, files, seconds=60):
        import os
        import time
        past = time.time() - seconds
        for f in files:
            os.utime(f, (past, past))

    def _assert_same(self, a, b):
        names = (set(vars(a)) | set(vars(b))) - self._SKIP
        for name in sorted(names):
            self.assertEqual(getattr(a, name, None), getattr(b, name, None), name)

    def test_cached_rebuild_equals_fresh_build(self):
        from unittest import mock
        from grounded.index_cache import IndexCache, default_cache_path
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(IndexCache, "SAVE_MIN_MISSES", 0):
            root = Path(td).resolve()
            (root / ".git").mkdir()
            files = TestParallelIndexBuild()._tree(root)
            self._age(files)
            path = default_cache_path(root)
            RepoIndex(root, files, stats=self._stats(files), index_cache=IndexCache(path))
            self.assertTrue(path.exists())
            # Edit, delete, add, and move a name between languages: `w3`
            # stops being defined in JS and starts being defined in Python.
            (root / "pkg" / "m1.py").write_text("def renamed():\n    pass\n", encoding="utf-8")
            (root / "pkg" / "m2.py").unlink()
            (root / "web" / "w3.js").write_text("export const other = 1;\n", encoding="utf-8")
            (root / "pkg" / "m3.py").write_text("def w3():\n    pass\n", encoding="utf-8")
            new = root / "pkg" / "fresh.py"
            new.write_text("from pkg.m1 import renamed\n", encoding="utf-8")
            files = [f for f in files if f.exists()] + [new]
            self._age([root / "pkg" / "m1.py", root / "web" / "w3.js",
                       root / "pkg" / "m3.py", new], seconds=30)
            cache = IndexCache(path)
            cached = RepoIndex(root, files, stats=self._stats(files), index_cache=cache)
            fresh = RepoIndex(root, files)
            self._assert_same(cached, fresh)
            self.assertEqual(cache.misses, 4)  # m1, w3, m3, fresh.py
            self.assertEqual(cache.hits, len(files) - 4)
            self.assertIn("w3", cached.py_symbols)
            self.assertNotIn("w3", cached.js_symbols)
            self.assertNotIn("pkg/m2.py", cached.file_symbols)
            # Third build, nothing changed: all hits, cache not rewritten.
            before = path.stat().st_mtime_ns
            cache = IndexCache(path)
            again = RepoIndex(root, files, stats=self._stats(files), index_cache=cache)
            self.assertEqual((cache.hits, cache.misses), (len(files), 0))
            self.assertEqual(path.stat().st_mtime_ns, before)
            self._assert_same(again, fresh)

    def test_few_misses_do_not_rewrite_the_cache(self):
        # The edited files re-index in milliseconds; rewriting the whole
        # cache for them costs more than it saves. They stay misses (still
        # correct), and a larger change persists them.
        from grounded.index_cache import IndexCache, default_cache_path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / ".git").mkdir()
            files = TestParallelIndexBuild()._tree(root)
            self._age(files)
            path = default_cache_path(root)
            RepoIndex(root, files, stats=self._stats(files), index_cache=IndexCache(path))
            written = path.stat().st_mtime_ns
            edited = root / "pkg" / "m1.py"
            edited.write_text("def renamed():\n    pass\n", encoding="utf-8")
            self._age([edited], seconds=30)
            for _ in range(2):
                cache = IndexCache(path)
                idx = RepoIndex(root, files, stats=self._stats(files), index_cache=cache)
                self.assertEqual(cache.misses, 1)
                self.assertIn("renamed", idx.all_symbols)
            self.assertEqual(path.stat().st_mtime_ns, written)

    def test_racily_clean_entry_is_reindexed(self):
        # Same stat, different content: possible on coarse-mtime filesystems
        # when the edit lands in the same tick the cache was written.
        from grounded.index_cache import IndexCache, default_cache_path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / ".git").mkdir()
            f = root / "a.py"
            f.write_text("def old():\n    pass\n", encoding="utf-8")
            stat = {str(f): (f.stat().st_mtime_ns, f.stat().st_size)}
            path = default_cache_path(root)
            RepoIndex(root, [f], stats=stat, index_cache=IndexCache(path))
            f.write_text("def new():\n    pass\n", encoding="utf-8")  # same size
            cache = IndexCache(path)
            idx = RepoIndex(root, [f], stats=stat, index_cache=cache)  # stale stat on purpose
            self.assertEqual(cache.hits, 0)
            self.assertIn("new", idx.all_symbols)
            self.assertNotIn("old", idx.all_symbols)

    def test_unusable_cache_is_ignored(self):
        import marshal
        from grounded.index_cache import IndexCache, default_cache_path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / ".git").mkdir()
            f = root / "a.py"
            f.write_text("def a():\n    pass\n", encoding="utf-8")
            self._age([f])
            path = default_cache_path(root)
            path.parent.mkdir(parents=True)
            stat = self._stats([f])
            entry = {"file_symbols": {"forged"}, "py_symbols": {"forged"}}
            for blob in (b"", b"\x00garbage", marshal.dumps([1, 2]),
                         marshal.dumps({"key": ("other",), "written_ns": 1 << 62,
                                        "files": {"a.py": (*stat[str(f)], entry)}})):
                path.write_bytes(blob)
                cache = IndexCache(path)
                idx = RepoIndex(root, [f], stats=stat, index_cache=cache)
                self.assertEqual(cache.hits, 0)
                self.assertIn("a", idx.all_symbols)
                self.assertNotIn("forged", idx.all_symbols)

    def test_cache_location(self):
        from grounded.index_cache import default_cache_path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            self.assertIsNone(default_cache_path(root))
            (root / "real-gitdir").mkdir()
            (root / "wt").mkdir()
            (root / "wt" / ".git").write_text("gitdir: ../real-gitdir\n", encoding="utf-8")
            path = default_cache_path(root / "wt" / "sub")
            self.assertEqual(path.parent, root / "real-gitdir" / "grounded")
            self.assertNotEqual(default_cache_path(root / "wt"), default_cache_path(root / "wt", "claims"))

    def test_scan_output_identical_with_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / ".git").mkdir()
            (root / "lib.py").write_text("def helper():\n    pass\n", encoding="utf-8")
            (root / "use.py").write_text("from lib import helper, gone\n\n"
                                          "# Calls old_helper() first.\nhelper()\n", encoding="utf-8")
            self._age(list(root.glob("*.py")))
            plain = [f.to_dict() for f in scan_root(root, Config())[0]]
            self.assertTrue(plain)
            first = [f.to_dict() for f in scan_root(root, Config(), index_cache=True)[0]]
            second = [f.to_dict() for f in scan_root(root, Config(), index_cache=True)[0]]
            self.assertEqual(plain, first)
            self.assertEqual(plain, second)
            self.assertTrue(list((root / ".git" / "grounded").glob("index-*.marshal")))


class TestBaseVariant(unittest.TestCase):
    """RepoIndex.base_variant patches a copy of the current index back to
    the diff base. It must equal a fresh build of the base tree in every
    attribute, and must leave the current index untouched."""

    _SKIP = {"_jobs", "_texts", "_deps_cache", "_gitignore_cache", "_disk_texts",
             "_index_cache", "_stats", "_given_entries", "files"}

    def test_patched_base_equals_fresh_base_build(self):
        import copy
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            files = TestParallelIndexBuild()._tree(root)
            # At the base, m1 also defines f7, which unchanged m7 defines too:
            # restoring it must not write into the current index's shared set.
            m1 = root / "pkg" / "m1.py"
            m1.write_text(m1.read_text(encoding="utf-8") + "def f7():\n    pass\n", encoding="utf-8")
            base_texts = {f.relative_to(root).as_posix(): f.read_text(encoding="utf-8")
                          for f in files}
            base_fresh = RepoIndex(root, list(files))
            # The change: edit, delete, add, and move `w3` from JS to Python
            # while keeping `f5` defined in a second file.
            (root / "pkg" / "m1.py").write_text("def renamed():\n    pass\n", encoding="utf-8")
            (root / "pkg" / "m2.py").unlink()
            (root / "web" / "w3.js").write_text("export const other = 1;\n", encoding="utf-8")
            (root / "pkg" / "m3.py").write_text("def w3():\n    pass\ndef f5():\n    pass\n",
                                                encoding="utf-8")
            (root / "pkg" / "sub").mkdir()
            new = root / "pkg" / "sub" / "fresh.py"
            new.write_text("def F1():\n    pass\n", encoding="utf-8")
            now_files = sorted([f for f in files if f.exists()] + [new])
            current = RepoIndex(root, now_files)
            snapshot = copy.deepcopy({k: v for k, v in vars(current).items() if k != "_texts"})
            status = {"pkg/m1.py": "M", "pkg/m2.py": "D", "web/w3.js": "M",
                      "pkg/m3.py": "M", "pkg/sub/fresh.py": "A"}
            variant = current.base_variant(status, {r: base_texts[r] for r in status if r in base_texts})
            names = (set(vars(variant)) | set(vars(base_fresh))) - self._SKIP
            for name in sorted(names):
                self.assertEqual(getattr(variant, name, None), getattr(base_fresh, name, None), name)
            self.assertEqual([str(f) for f in variant.files], [str(f) for f in sorted(files)])
            for name, value in snapshot.items():
                self.assertEqual(vars(current)[name], value, f"current index changed: {name}")
            self.assertIn("w3", variant.js_symbols)
            self.assertNotIn("w3", variant.py_symbols)
            self.assertIn("f1", variant.lower_map)
            self.assertEqual(variant.lower_map["f1"], {"f1"})


class TestBrokenPoolFallback(unittest.TestCase):
    """A worker killed mid-scan (OOM, sandbox) must not lose the scan:
    both process pools fall back to the serial path, same result."""

    def test_checker_pool_breaks(self):
        from concurrent.futures.process import BrokenProcessPool
        from unittest import mock
        import grounded.scanner as sc

        class Boom:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                raise BrokenProcessPool("worker killed")

            def __exit__(self, *a):
                return False
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(3):
                (root / f"m{i}.py").write_text("# Calls `ghost_fn()` here.\nX = 1\n", encoding="utf-8")
            serial, _, _ = scan_root(root, Config(), jobs=1)
            with mock.patch.object(sc, "ProcessPoolExecutor", Boom):
                broken, _, _ = sc.scan_root(root, Config(), jobs=2)
            self.assertEqual([(f.path, f.line) for f in broken], [(f.path, f.line) for f in serial])
            self.assertEqual(len(broken), 3)


class TestChangedFastPath(unittest.TestCase):
    """The broad fallback of `scan --changed` (see changed.py) checks only
    changed files and files naming a changed symbol; its report must equal
    a full scan filtered by filter_changed."""

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
            (root / "setup.cfg").write_text("[metadata]\nname = p\n", encoding="utf-8")
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
            # Editing a file checkers read from disk forces the broad mode.
            (root / "setup.cfg").write_text("[metadata]\nname = p2\n", encoding="utf-8")
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



class TestChangedIntroduced(unittest.TestCase):
    """`scan --changed` reports what the change introduced: findings on
    changed lines or in new files, plus findings anywhere that a full scan
    of the worktree has and a full scan of the base does not. Each case
    computes that truth with two full scans and compares."""

    def _git(self, root: Path, *args: str) -> None:
        import os
        import subprocess
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def _write(self, root: Path, files: dict) -> None:
        for rel, text in files.items():
            p = root / rel
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def _run(self, base: dict, edits: dict, scan_sub: str = ""):
        import contextlib
        import io
        from collections import Counter
        from grounded.cli import main
        from grounded.delta import changed_lines, fingerprint
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as snap:
            root = Path(td).resolve()
            self._write(root, base)
            self._git(root, "init", "-q")
            self._git(root, "add", "-A")
            self._git(root, "commit", "-qm", "base")
            shutil.copytree(root, Path(snap) / "t", ignore=shutil.ignore_patterns(".git"))
            scan_at = root / scan_sub if scan_sub else root
            before = scan_root(Path(snap) / "t" / scan_sub if scan_sub else Path(snap) / "t", Config())[0]
            self._write(root, edits)
            after = scan_root(scan_at, Config())[0]
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                main(["scan", str(scan_at), "--changed", "--format", "json", "--fail-on", "never",
                      "--no-index-cache"])
            got = sorted((f["path"], f["line"], f["checker"]) for f in json.loads(out.getvalue()))
            hunks, untracked = changed_lines(scan_at, "HEAD")
            budget = Counter(fingerprint(f) for f in before)
            want = set()
            for f in sorted(after, key=lambda x: (x.path, x.line, x.checker)):
                fp = fingerprint(f)
                online = f.path in untracked or any(
                    ln in hunks.get(f.path, ()) for ln in range(f.line, f.end_line + 1))
                if budget[fp] > 0 and not online:
                    budget[fp] -= 1
                    continue
                want.add((f.path, f.line, f.checker))
            return got, sorted(want), err.getvalue()

    _PKG = {
        "pyproject.toml": '[project]\nname = "p"\n',
        "app/__init__.py": "",
        "app/core.py": "def fetch_user():\n    return 1\n\n\ndef helper():\n    return 2\n",
        "app/views.py": ("from app.core import fetch_user\n\n\ndef show():\n"
                         "    # Renders through `legacy_ghost()`.\n    return fetch_user()\n"),
        "app/other.py": "# Calls `old_ghost()` for self and None.\nX = None\n",
        "tests/__init__.py": "",
        "tests/test_core.py": ("from unittest import mock\n\n\ndef test_it():\n"
                               "    with mock.patch('app.core.helper'):\n        pass\n"),
    }

    def test_rename_reports_fallout_not_old_noise(self):
        # `other.py` holds an old finding whose claim shares words with the
        # diff (`self`, `None`); the old rule reported it on every edit.
        got, want, _ = self._run(self._PKG, {
            "app/core.py": ("def load_user():\n    return None\n\n\n"
                            "def helper(self=None):\n    return 2\n"),
        })
        self.assertEqual(got, want)
        self.assertIn(("app/views.py", 1, "stale-import"), got)
        # views.py is checked (it imports the renamed name), but its older
        # finding was there before the change.
        self.assertNotIn(("app/views.py", 5, "stale-symbol-ref"), got)
        self.assertNotIn("app/other.py", {p for p, _, _ in got})

    def test_mock_target_fallout(self):
        got, want, _ = self._run(self._PKG, {
            "app/core.py": "def fetch_user():\n    return 1\n\n\ndef helper_v2():\n    return 2\n",
        })
        self.assertEqual(got, want)
        self.assertIn(("tests/test_core.py", 5, "stale-mock-ref"), got)

    def test_in_file_fallout_off_the_changed_lines(self):
        base = dict(self._PKG)
        base["app/core.py"] = ("# Delegates to `helper()` below.\n"
                               "def fetch_user():\n    return helper()\n\n\n"
                               "def helper():\n    return 2\n")
        got, want, _ = self._run(base, {
            "app/core.py": ("# Delegates to `helper()` below.\n"
                            "def fetch_user():\n    return 2\n"),
        })
        self.assertEqual(got, want)
        self.assertIn(("app/core.py", 1, "stale-symbol-ref"), got)

    def test_deleted_module(self):
        got, want, err = self._run(self._PKG, {"app/core.py": None})
        self.assertEqual(got, want)
        self.assertIn(("app/views.py", 1, "stale-import"), got)
        self.assertNotIn("broad mode", err)

    def test_new_file_and_changed_line(self):
        got, want, _ = self._run(self._PKG, {
            "app/new.py": "# Calls `brand_new_ghost()`.\nY = 1\n",
            "app/views.py": ("from app.core import fetch_user\n\n\ndef show():\n"
                             "    # Renders through `legacy_ghost()`.\n"
                             "    # Uses `render_ghost()`.\n    return fetch_user()\n"),
        })
        self.assertEqual(got, want)
        self.assertEqual({p for p, _, _ in got}, {"app/new.py", "app/views.py"})

    def test_scan_root_below_the_git_top_level(self):
        # A monorepo package: git prints top-level paths, findings are
        # package-relative; `--relative` must line them up.
        base = {f"pkg/{k}": v for k, v in self._PKG.items()}
        got, want, _ = self._run(base, {
            "pkg/app/core.py": "def load_user():\n    return 1\n\n\ndef helper():\n    return 2\n",
        }, scan_sub="pkg")
        self.assertEqual(got, want)
        self.assertIn(("app/views.py", 1, "stale-import"), got)

    def test_many_changed_names_use_git_grep(self):
        # More than 8 names whose repo-wide presence changes: candidates
        # come from one `git grep` (delta.grep_files), not a Python regex.
        from unittest import mock
        import grounded.delta as delta
        base = dict(self._PKG)
        base["app/many.py"] = "".join(f"def fn_{i}():\n    return {i}\n\n\n" for i in range(12))
        base["app/uses.py"] = "from app.many import fn_7\n"
        calls = []
        real = delta.grep_files

        def spy(root, tokens):
            calls.append(len(tokens))
            return real(root, tokens)
        with mock.patch.object(delta, "grep_files", spy):
            got, want, _ = self._run(base, {
                "app/many.py": "".join(f"def fn2_{i}():\n    return {i}\n\n\n" for i in range(12)),
            })
        self.assertEqual(got, want)
        self.assertIn(("app/uses.py", 1, "stale-import"), got)
        self.assertTrue(calls and calls[0] > 8)

    def test_manifest_edit_falls_back_to_broad_mode(self):
        got, _, err = self._run(self._PKG, {
            "pyproject.toml": '[project]\nname = "p"\nversion = "2"\n',
            "app/core.py": "def load_user():\n    return 1\n\n\ndef helper():\n    return 2\n",
        })
        self.assertIn("broad mode because pyproject.toml", err)
        self.assertIn(("app/views.py", 1, "stale-import"), got)

if __name__ == "__main__":
    unittest.main()
