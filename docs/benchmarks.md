# Benchmarks

All numbers below are reproducible and measured on physical hardware.
Nothing here is projected, estimated, or compared against unmeasured
runners.

## Reproducible harness

`examples/bench/bench.py` measures the pre-test-filter path (stdlib
only, pytest leg skipped when pytest is absent):

```console
python3 examples/bench/bench.py
```

```text
pre-test-filter benchmark (best of 7, wall clock)
  A. grounded in-process, single file :      0.6 ms
  B. grounded cold CLI, single file   :     58.0 ms
```

* **A** is the agent-loop path: parse plus check inside a running
  process (MCP server, LSP server, editor hook).
* **B** is dominated by Python interpreter startup, not by checking.
* No test-runner comparison is published: tests catch everything, and
  any head-to-head number without a fixed fixture would be marketing.

## Precision gate: every finding on 25 pinned repos, classified (measured 2026-09-24)

`bench/precision.py` scans 25 real repositories pinned to exact commits
(`bench/precision/repos.json`: 14 Python, 5 JS/TS, 3 Go, 3 C) with the
default checkers and requires every lie and drift to be labeled in
`bench/precision/ledger.json`, with a one-line reason. CI
(`.github/workflows/precision.yml`) fails on an unlabeled finding, a new
false positive, or a true positive that stopped firing.

```console
python3 bench/precision.py [--repos-dir DIR] [--only flask,redis]
```

| | lie + drift findings | true | false | precision |
|---|---:|---:|---:|---:|
| Before this round (v0.16.0 + scope fix) | 175 | 30 | 145 | 0.17 |
| After this round | 39 | 30 | 9 | **0.77** |

Every true positive survived the round (recall on planted rot: 115/115
corpus plants caught in flask, requests and express via `bench/recall.py`).
The false-positive families removed, each with a corpus case and a
positive control that still fires:

* test-fixture data (`tests/format/`, `tests/data/cases/`, `fixtures/`,
  `testdata/`, `broken_*` fixtures): about 90 findings in prettier, black,
  vite, pydantic, django;
* bundler resource queries (`./worker?worker`), Node CJS extension
  appending (`require('./x.tsx')` -> `x.tsx.js`), `package.json` `main`
  resolution and `browser` maps, symlinked sources;
* destructured `export const { a, b } = obj` and comments inside
  `export { ... }` lists (prettier: 9 findings);
* build artifacts named by any `.gitignore` on the path, `.pyi`-only
  compiled extensions, lazy string-keyed exports (celery, transformers
  style);
* non-typo dunders, `Type#method()` notation, pluralized C calls, Win32
  and included-library C APIs, `_suffix()` family fragments, and a C
  include parser that only ever saw the first line of a file;
* number-drift comparing against any number near the keyword instead of
  the keyword's own value.

The 9 remaining false positives are listed with reasons in the ledger's
`known_fp` (test-time `tsc` output, intentionally broken fixtures,
external API methods named in Python comments, tzcode internals).

True positives found (all verified by hand): flask `Module`, typer
`_extract_default()`, sqlmodel `_partial_init()`, pytest
`_get_stderr_fileno()`, django `CookieTests.test_cookie_max_length()`,
celery `get_default_timezone()`, `from celery import task` (x3),
`test_deamonization.py`, `patch('celery.result.copy')`, cobra
`ParsedFlags()`, curl `Curl_req_flush()`, `tests/ech_test.sh`, fastapi
`fastapi.temp_pydantic_v1_params`, grpc-go `NewContextWithHandshakeInfo()`,
`callStarted()`, `toLoadReport()`, prettier `parse/json.js`,
`builtin-plugins-proxy.js`, pydantic `docs/javascripts/search-worker.js`
and two stale benchmark paths, redis `rioClearError()`,
`RedisModule_DigestAddElement()`, `RedisModule_AuthClientWithUser()`,
`zmalloc_register_reserved_thread()`, `render_sequence()`,
`hnsw_update()`, jemalloc `extent_head_no_merge()`, scrapy
`_shutdown_graceful_reactorless()`, vite `plugin/assets.ts`.

## Full-tree scans (measured 2026-09-21)

Machine: MacBook Air (Apple silicon, 10 cores), Python 3.14.6.
Harness: `python3 bench/run.py <repo> --reps 3` (one invocation per
repo so RSS is attributable; each rep is a fresh cold-CLI process,
median reported). Repos are shallow clones at 2026-09-21 main.

| Tree | Files | Median | Throughput | Peak RSS | Findings |
|---|---|---|---|---|---|
| django | 2,977 | 16.7 s | 178 files/s | 468 MB | 33 smells, 5 lies |
| cpython (3.13) | 3,049 | 47.2 s | 65 files/s | 1,251 MB | 1,216 smells, 60 lies, 7 drifts |
| grpc-go | 1,068 | 4.4 s | 242 files/s | 250 MB | 5 smells, 5 lies |
| axios | 246 | 1.0 s | 255 files/s | 39 MB | 1 lie |
| express | 141 | 0.6 s | 229 files/s | 33 MB | 0 |
| flask | 83 | 1.1 s | 75 files/s | 43 MB | 0 |

Small-tree times are dominated by interpreter startup (~58 ms, see
above), not by checking. Memory scales with total source held for the
single-pass index build. Parallelism engages automatically at 512+
files; repeat scans reuse per-file results with `scan --cache`.

## Corpora results (2026-09-21 run, all classified)

| Repository | Findings | Standing |
|---|---|---|
| django | 33 smells, 5 lies | 4 lies in deliberately-broken test fixtures (`broken_app`, `import_error`, `broken_tag`, staticfiles test data); **1 true lie**: `test_fallback.py` references `CookieTests.test_cookie_max_length()`, which exists nowhere |
| cpython | 1,216 smells, 60 lies, 7 drifts | smells are stdlib `XXX`/`TODO` markers; sampled lies are moved/removed files (**true**: `Include/code.h` → `Include/cpython/code.h`, `Tools/scripts/*` reorg, `Lib/distutils/msvccompiler.py` removal) and deliberately-broken `test_import` fixtures; residual lies are platform APIs (documented limitation) plus one cross-branch number comparison (known `number-drift` weakness) |
| grpc-go | 5 smells, 5 lies | **true**: `NewContextWithHandshakeInfo()` documented but defined nowhere; suspected-true `toLoadReport()`; FPs: inlined-copy provenance mention, one cross-file local |
| axios | 1 lie | known FP: import from a `tsc`-generated `declarations/` dir that exists only during tests |
| express | 0 | was 101 stale-import lies before the CJS-interop and `./`-normalization fixes (same run) |
| flask | 0 | — |

Earlier runs (different hardware): cobra (Go) 6 smells + 2 confirmed
rename-rot lies; redis (C) 63 smells + 13 lies (7 confirmed true).
Remaining false positives fall in documented classes (vendored code,
kernel idioms, platform APIs, prose verbs); see Limitations.

## External validation: merged upstream fixes

On 2026-09-22 the maintainer of OmniRoute (`diegosouzapw/OmniRoute`,
69k stars) merged [PR #14412](https://github.com/diegosouzapw/OmniRoute/pull/14412),
three fixes for rot a Grounded audit surfaced, each confirmed broken
by the project's own toolchain before repair:

* `src/lib/db/discovery.ts` — relative import unresolvable from its
  directory (TS2307), masked in CI by a narrow typecheck config.
* `scripts/ad-hoc/regen-opencode-config.ts` — import and usage docs
  left pointing at a pre-move directory (TS2307).
* `tests/unit/combo/recovery-hint.test.ts` — renamed ghost type plus
  a fixture built on a superseded interface shape (TS2305).

Validation recorded on the PR: 11/11 tests pass, scoped `tsc` probe
clean, eslint clean, 16 checks green. One data point, not a trend —
but it is the first maintainer-merged proof that the contradiction
class Grounded checks is real rot other tooling misses.

## What the benchmark round fixed

Measuring on real repos paid for itself four times over, all in
`stale-import` and `stale-symbol-ref`:

* PEP 695 `type X = ...` aliases are runtime bindings (was: 32 false
  lies on CPython's `_pyrepl`).
* `./`-prefixed index paths are normalized before export lookup (was:
  101 false lies on express examples).
* `require('./x').prop` is a named import, not a default import.
* Default imports from CJS-shaped targets are valid via Node interop
  and stay silent; the check applies to ESM targets only.
* `from . import X` follows star re-exports and yields to
  `globals().update()` namespaces.
* `Xxx`/`XXX` placeholder names stay silent in `stale-symbol-ref`.

## Precision round: svelte and a production TypeScript monorepo (measured 2026-09-22)

Machine: MacBook Air (Apple silicon, 10 cores), Python 3.13. Trees are
shallow clones fetched 2026-09-22. Harness: `grounded scan <tree>
--format json`, opt-in checkers named per table.

### svelte (`--enable stale-doc-ref,ghost-export`)

| | Findings |
| --- | ---: |
| Before | 23 (12 `stale-doc-ref` lies, 11 `ghost-export` smells) |
| After | **1** (`ghost-export`: `strip_link` in `tests/css/test.ts`) |

Every one of the 12 `stale-doc-ref` lies was a false positive against
svelte's own docs, in three families: doc examples annotated with
`+++`/`---` highlight markers (the markers broke identifier extraction,
turning bound parameters into phantom calls), Playwright fixture
parameters arriving destructured (`async ({ page }) =>`), and doc-tooling
directives (`// @noErrors`) marking an example as not-executed config.

Of the 11 `ghost-export` smells, 10 were generated compiler output under
`tests/snapshot/samples/*/_expected/`, and 1 was a live function called
on a line opening with an inline `/** @param ... */`. The single
survivor is a true positive: `strip_link` is defined in
`tests/css/test.ts` and referenced nowhere in the tree.

### Follow-up round (measured 2026-09-23)

Re-scanned with the repair round plus three new silences (metasyntactic
calls, completed Python builtins, narrative-framed doc blocks):

| Tree | Before | After | Residue |
| --- | ---: | ---: | --- |
| svelte, default set | 8 (6 smells, 2 lies) | **6** (smells only) | `fragile-anchor` smells, legitimate by contract |
| rich, `stale-doc-ref` | 36 | **17** | all `do_step()` in 17 translated READMEs (English framing only) |

The 2 svelte lies were `` `foo` ``/`` `blah` `` metasyntactic names.
The 19 removed rich findings were `locals()` (missing builtin, now in
the set) and `do_step()` under "Here's an example:" (narrative frame).
The remaining 17 are the translation boundary: the marker set is
English, translated docs keep their residue. Documented in
`docs/rules.md`, not chased.

### New-repo round: httpx, preact (measured 2026-09-23)

Two trees the tool had never been tuned against, default set plus
classification of everything found:

| Tree | Findings | Verdict |
| --- | --- | --- |
| httpx (90 files) | 1 → **0** | `cgi.parse_header()` history note + PEP 594 link: past-tense frame now silent |
| preact (3,621 files) | 11 → **4** | 3 symbol lies (SES `lockdown` + ticket, React-compat `UNSAFE_*` + issue, both ticket-anchored discussion), 1 alias drift (`preact` mapping hijacked declared `preact-router`), 3 `../../` lies (package `main` points at unbuilt `dist/`) — all fixed; 4 `fragile-anchor` smells remain, legitimate by contract |

No true positives surfaced in either tree (both are clean, well-kept
codebases — a silent verdict on a clean tree is itself evidence), but
every false positive became a rule with a corpus case: history frames,
block tickets with a work-item carve-out, TS segment-boundary aliases,
and package-main directory resolution (in-tree mains still verify
bindings: recall preserved, proven by the positive-control corpus
cases).

### Wild recall: `unclosed-fence` on cli/cli (measured 2026-09-23)

First contact with a fresh tree (943 Go files, never tuned against):
`unclosed-fence` fired once — `.github/workflows/shared/spam-criteria.md:148`,
a ` ```shell ` line inside a bare ` ```` ` block opened at line 119.
Verified against GitHub's own renderer (`POST /markdown`, `mode=gfm`):
the line renders literally inside a `<pre>` block, never as a fence —
the checker's claim is mechanically true.

Classified **deliberate, not a defect**: all six ` ```` ` wrappers in
the file are balanced; the authors display issue-template source, so
the inner ` ```shell ` is content being shown, not a fence being
opened. The checker's scaffold exception requires a longer outer fence
*with an info string*; a bare longer outer fence fires by design
(the OmniRoute twin of this shape was a genuine 76-line rendering
defect). No upstream report filed: a mechanically-true,
intent-false finding reported as a bug would be precisely the noise
this tool exists to prevent. The boundary is now documented here
instead.

### OmniRoute, `src/lib` (default checkers)

| Checker | Before | After |
| --- | ---: | ---: |
| `stale-import` | 58 | **0** |
| `stale-symbol-ref` | 31 | 31 |
| `stale-file-ref` | 12 | 12 |
| `fragile-anchor` | 3 | 3 |
| `number-drift` | 2 | 2 |
| **Total** | **106** | **48** |

The 58 import lies were all one mechanical mistake: a sub-tree scan
(`scan src/lib`) resolving `../../shared/...` and `../../../open-sse/...`
to files that exist one level **above** the scan root. The same tree
scanned from the repo root reported 3, and after the fix the sub-tree
scan agrees. Suppression-only change: every other checker is
count-identical before and after, so the round removed 58 false
positives with zero recall loss.

### What the round fixed

* **Partial-snapshot absence claims.** A relative specifier that
  resolves above the scan root is now silent, in both the relative and
  alias arms of `_resolve_js_target`. A scan cannot report what it did
  not look at as missing; a sub-tree or single-file scan (the advertised
  agent loop) previously manufactured up to 51 lies on one tree.
* **Inline block comments.** `_code_text` kept only the code after a
  `*/` that closes mid-line instead of blanking the whole line, so a use
  sitting behind `/** @param ... */` counts as a use.
* **Generated expected output.** `_GHOST_GENERATED_PATH` keeps
  `ghost-export` silent under `_expected/`, `__snapshots__/`,
  `snapshots/`, `generated/`, `codegen/`, and `*.gen.*`. Reachability of
  generated code is unknowable, so neither is the claim that nobody can
  reach it.
* **Documentation diff markers.** Binding extraction and call scanning
  run on a marker-stripped copy of the line.
* **Destructured bindings.** `_doc_block_known` collects names bound by
  `{ a, b }`/`[a, b]` patterns, which is how test fixtures are written.
* **Ambient platform roots.** `customElements`, `getComputedStyle`,
  `matchMedia`, the observer/worker constructors and friends joined
  `_DOC_JS_AMBIENT_ROOTS`.
* **Doc-tooling directives.** `// @noErrors`, `@errors`, `/// file:` and
  `---cut---` mark a block as not-executed and are treated like other
  illustrative markers.
* **Build-output paths in file references.** `stale-file-ref` now shares
  the import arms' verdict: a path under a scan-ignored directory is not
  indexed, so it cannot be judged missing (20 findings removed on the
  OmniRoute tree, all generated or written at runtime —
  `dist/docs/openapi.yaml` in CLI help text, `dist/index.cjs` in a setup
  command).
* **Elided paths.** `src/.../File.tsx` is shorthand, not a claim. The
  placeholder list intended this, but the segment split turned `...` into
  empty segments and never matched (5 more findings removed on the same
  tree).
* **Baseline de-duplication.** `grounded baseline` stored duplicate
  fingerprints and its printed `total` disagreed with the file (277
  unique vs 284 entries on the same tree).
* **A silently disabled checker.** `declared_dependencies` returns
  `None` when no manifest exists anywhere above the file, and
  `stale-doc-ref` iterated it unconditionally. The exception was
  swallowed by the scanner's checker guard, so the whole checker was
  dark on every manifest-less tree. Absence of a manifest is now an
  empty declared set.

### OmniRoute, whole repository (with a starter `grounded.toml`)

Ignoring the private `_tasks` tree and the local `.freebuff` scratch dir:

| Checker | Before the round | After |
| --- | ---: | ---: |
| `stale-file-ref` | 103 | 78 |
| `fragile-anchor` | 82 | 82 |
| `stale-symbol-ref` | 55 | 55 |
| `number-drift` | 23 | 23 |
| `stale-import` | 2 | 2 |
| **Total** | **265** | **240** |

11,481 files in ~26 s; 235 unique fingerprints recorded as the adoption
baseline. Again suppression-only: only `stale-file-ref` moves, so nothing
else lost recall. `--changed` against the working tree reported 41
findings (untracked files are fully reported by design) with 199 hidden
outside changed lines.

### Residuals (measured, deliberately not fixed here)

* Platform and framework API names in comments still report in
  `stale-symbol-ref`: on the OmniRoute tree the 55 non-fixture symbol
  refs sampled as `NextResponse.rewrite()` (Next.js), `ws.recv()`,
  `getCacheDirectory()` (a Next.js internal named in a Next.js-internals
  comment), and jest/Vitest sentinels (`__anon__`, `__updateSettings__`,
  `__sessionDedupMap__`). These are the documented platform-API
  boundary: judge on sight or baseline them.
* One guarded `require` fallback inside `try { } catch {}`
  (`electron/loginManager.js`) and one fumadocs-generated `.source/server`
  import still report at repo root: JS guarded imports are not
  recognised as guarded the way Python's are, and generated content in a
  dot-directory is not in the ignore set.
* `ghost-export` on generated output inside a *test* file
  (`snapshots`-style names) is silent; genuinely dead helpers in test
  files still report, which is the intended behavior.

## Recall round: corpus rot replayed inside real repos (measured 2026-09-22)

Precision answers "are the findings real?"; recall answers "would they
still be found?". The adversarial corpus proves a checker fires in the
smallest tree that shows the behavior — it cannot say whether the
finding survives a real repository around it, where a definition two
directories away, a manifest that declares the package, or a build
directory that looks generated can silence it. Context only ever
silences, so isolation truth and repo truth are different claims.

`bench/recall.py` closes that gap: every firing corpus case is planted
into a copy of a real repo at its original relative path, one case at a
time, and the expected finding must still appear — as a delta against a
baseline scan of the pristine tree, so pre-existing findings are never
credited to the plant. A plant that may not overwrite a host file (a
fixture cannot replace the host's `pyproject.toml`) reports "not
planted" and is excluded, never counted as a miss. Prose fixtures merge
into the host's own `README.md` (the rot is just as real at the end of
real prose); the harness closes a host's dangling fence first so the
plant lands in well-formed context, and lists every such host because an
unbalanced host is a real defect. And like the scan itself, the harness
refuses to state a number at all if a checker raised.

Machine: MacBook Air (Apple silicon), Python 3.13. Measured 2026-09-22,
post fence-walk fix (see the CHANGELOG):

| Repo | Files | Caught | Missed | Not planted | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Grounded | 157 | 22 | 0 | 1 | 100% |
| flask | 94 | 22 | 0 | 1 | 100% |
| requests | 51 | 22 | 0 | 1 | 100% |
| svelte | 3,625 | 23 | 0 | 0 | 100% |

89 planted expectations, 0 misses, 0 checker errors across all 13
checkers. Reproduce: `python3 bench/recall.py <repo> [...]`.

Measured 2026-09-23, after the case-insensitivity fix (see below) —
express and flask re-verified at 100% with the enlarged case set:

| Repo | Files | Caught | Missed | Not planted | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| express | 146 | 23 | 0 | 0 | 100% |
| flask | 94 | 22 | 0 | 1 | 100% |

The harness itself had a phantom-miss bug: on case-insensitive
filesystems (macOS APFS) a `README.md` fixture merges into the host's
`Readme.md`, and expectations matched by exact path reported "no
finding on that path" for all 7 README plants on express. The harness
now tracks on-disk names (`plant` returns the real rel via directory
listing — `relative_to` is lexical and echoes the constructed name)
and translates expectations. The 7 misses became 7 catches with no
product change: the findings were always firing, the ruler was bent.
