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


if __name__ == "__main__":
    unittest.main()
