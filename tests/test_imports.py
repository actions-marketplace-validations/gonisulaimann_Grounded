"""stale-import for Python and JavaScript/TypeScript.

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


if __name__ == "__main__":
    unittest.main()
