"""Config loading, the Python 3.10 TOML subset, and repair-round regressions.

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
