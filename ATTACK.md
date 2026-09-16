# ATTACK.md — adversarial evaluation of the `grounded` thesis

> **Follow-up (v2 rebuild, same session):** the verdict below stands for v1
> and motivated a rebuild instead of a defense. v2 deletes the 4 redundant
> checkers, rebuilds symbol/file detection with import maps, scope proxies,
> repo-scope gating, and 6 evidence-driven suppression rules, and re-runs
> this exact benchmark: **844 → 42 findings; requests 46→4 (0 lies),
> axios 86→0, django 712→38 (1 lie, a confirmed true rename-rot
> `CookieTests.test_cookie_max_length()` vs real `test_max_cookie_length`)**.
> 42/42 tests green. v1's Outcome C is preserved as history; v2 makes the
> narrowed claim "quiet reference-rot detector, measured" — see README.
> Small-n warning from the author: lie precision rests on few remaining
> lies; the honest claim is quiet+right on 3,200 files, not a proven rate.

Date: 2026-09-16/17. Author role: unfriendly senior engineer / skeptical
maintainer / competing product. Mission: **try to disprove the thesis**,
not to defend it. Ugly results preserved. Uncertain items labeled as such.

## 1. Original thesis (under test)

> Comments, docstrings, and documentation contain falsifiable claims about a
> codebase. A deterministic, offline, snapshot-only tool can verify those
> claims against the repository state and identify provably false or stale
> claims. The niche is relatively empty vs dependency time-travel,
> supply-chain forensics, design-token drift, stale-comment tooling,
> conventional static analysis, and LLM review.

## 2. Falsifiable hypotheses formulated before testing

- H1: param/return/raises checkers are already solved (darglint, pydoclint,
  eslint-plugin-jsdoc, Ruff D417) with strictly better precision.
- H2: commented-code is already solved (Ruff ERA001, eslint equivalents).
- H3: stale-symbol-ref precision collapses on real repos (dynamics,
  re-exports, externals, fields/attrs, prose).
- H4: number-drift / stale-file-ref are too rare to matter.
- H5: snapshot-only is insufficient; the valuable cases need history
  (the research field moved post-hoc → just-in-time for a reason).
- H6: lie/drift/smell severities do not survive contact with real repos.
- H7: zero-dep/offline is a limitation, not a moat (regex parsing unsound).
- H8: the niche was not empty; yesterday's search missed the terminology
  ("docstring linter", "JSDoc validation", "obsolete comment detection").

## 3. Competitor inspection (all verified by docs and/or execution)

| `grounded` checker | Incumbent (verified) | Verdict |
|---|---|---|
| param-mismatch (py) | **darglint** DAR101/DAR102 (executed: finds same 3 findings on our fixture, plus types/yields/noqa) | REDUNDANT, incumbent strictly superior |
| param-mismatch (py) | **pydoclint** DOC10x/DOC20x (executed on fixture: same + type consistency) | REDUNDANT |
| param-mismatch (py) | **Ruff D417** (executed: flags missing args) | REDUNDANT (partial) |
| param-mismatch (js) | **eslint-plugin-jsdoc** check-param-names + require-param (executed on fixture: exact TP, zero noise, autofix, destructured/rest handling) | REDUNDANT, incumbent strictly superior |
| raises-mismatch (py) | **darglint DAR402** (docs-verified: "exception not explicitly raised") | REDUNDANT |
| raises-mismatch (py) | **pydoclint DOC502/DOC503** (docs-verified) | REDUNDANT |
| raises-mismatch (js) | **eslint-plugin-jsdoc require-throws** (docs-verified) | REDUNDANT |
| return-mismatch (py) | **darglint DAR201/DAR202**, **pydoclint DOC201/202** (executed/docs) | REDUNDANT |
| return-mismatch (js) | **eslint-plugin-jsdoc require-returns-check** (docs-verified) | REDUNDANT |
| commented-code | **Ruff ERA001** (executed: flags our fixture per-line) | REDUNDANT |
| fragile-anchor (terms) | **ESLint no-warning-comments** (docs-verified: todo/fixme/xxx) | PARTIAL overlap; ticket-linkage/anchors unique but low-value |
| stale-file-ref | **lychee** (docs-verified: md/html links incl. `--offline` local files) — docs only, not code comments | ADJACENT, not competitive for code comments |
| stale-symbol-ref | **Sphinx nitpick** (docs-verified: RST cross-refs) — docs only | ADJACENT, not competitive |
| semantic drift | JIT research field: DeepJIT/JITDATA, CUP/CUP2 (4M samples), CoCC, OutComDeter, CARL-CCI — all use commit diffs; field explicitly abandoned pure post-hoc snapshot as insufficient | THESIS-WEAKENING (history necessary for semantics) |
| dead code (general) | vulture / Knip / ts-prune / coverage (docs-verified; different question: unused code vs dangling refs) | NOT competitive, correctly distinguished |

Key nuance (against self-serving reading): on `requests/api.py`, darglint
and pydoclint ALSO false-positive on the `Unpack[RequestKwargs]` pattern
(DAR102 ×14, DOC102/103). The pattern is hard for everyone. Difference:
incumbents are opt-in with per-project config + noqa and hedge ("or could
be other formatting issues"); `grounded` asserts zero-config "lie".

## 4. Real-repository experiments

Method: shallow clones into /tmp/attack (requests, axios, django);
`grounded scan` with default config; findings exported as JSON; graded
against source context. TP = claim actually false AND plausible maintainer
care. Precision = TP/(TP+FP). No favorable fixtures.

### 4a. psf/requests — 46 findings (39 lie, 3 drift, 4 smell), 37 files

Full grading (all 46, context-checked):
- 13× param-lie on `api.request` + 2× on put/patch: `**kwargs:
  Unpack[RequestKwargs]` passthrough pattern → ALL FP (15).
- stale-symbol 26: execfile (py2 builtin), todoList (Sphinx directive),
  custom_headers/allow_redirects/cookiejar (Sphinx :param: field names),
  CookieJar.clear/CookieJar/Tarfile.extractfile (stdlib), parse_set_header/
  dump_header/quote_header_value/_fix_ie_filename (werkzeug, external),
  url_parse (vendored urllib3), charset_normalizer (third-party dep),
  status_code ×3/encoding (attributes), dict_class (parameter),
  _body_position (attribute), object (builtin missing from whitelist),
  rewindable (local), response (prose), release_conn (urllib3 API) →
  25 FP. One TP: `__get_item__` doc typo (real `__getitem__`) — genuine
  catch, typo-level value.
- param-drift `is_filename`: technically true, maintainer-useless.
- fragile-anchor 4× XXX: genuine untracked markers (incl. decade-old
  digest-auth XXXs) — TP, low value; also covered by no-warning-comments.
- Tally: ~5/46 true (11%), maintainer-actionable ~1–2/46 (~3%).
  **Lie precision ≈ 1/39 (≈3%).**

### 4b. axios/axios — 86 findings (63 lie, 23 drift), 248 files, 0.29s

- return-mismatch 29: root-caused. (i) `//`-in-URL-string destroys brace
  tracking (`'https://…'` → strip eats the `{` → premature function-end →
  missed `return` → false lie; proven by trace on getAllSponsors).
  (ii) arrow implicit returns (`=> expr`, no `return` keyword; proven on
  encodeUTF8). (iii) class constructors treated as functions. Automated
  cause split: 10 has-return-keyword-missed, 4 URL-string, 4 arrow-implicit,
  2 constructor, 9 other. Precision ≤ ~30%, likely far lower.
- commented-code 17: **all 17 are English prose** (TLS/proxy explanations,
  module docs, `// This module is intended to unwrap…`, llhttp note),
  incl. 2 in `.d.ts`/`.d.cts` declaration files (never excluded).
  Precision 0%. Fix text ("Delete it — git remembers") is insulting here.
- param-mismatch 16: class-constructor pattern (`CancelToken documents
  executor`, `Axios documents instanceConfig` — params live on
  `constructor()`, not the class line), renames (`dispatchRequest(_config)`
  vs documented `config`). Systematic FP.
- stale-symbol 24: option/property names (`socketPath`, `auth.password`),
  test locals (`handlers`, `customNested`). Scope-blindness FP class.

### 4c. django/django — 712 findings (438 lie), 2977 files, ~9s (~330 files/s)

- stale-symbol 498: same FP classes as requests (spot-checked pattern).
- stale-file-ref 29 (this checker's best volume): graded 12 — template
  namespaces (`flatpages/default.html`), protocol string (`HTTP/1.1`
  matched via numeric "extension" — regex defect, ext class allows digits),
  external-project sources (`pg_config_manual.h`, `_sqlite/*.c`,
  `MySQLdb/cursors.py`), docs placeholders (`myapp/css/base.css`),
  i18n patterns (`en/LC_MESSAGES/django.po`), illustrative examples
  (`news/photos.py` — truly absent but illustrative), template-loader
  namespace for an existing file (`static/directory_index.html` exists at
  `views/templates/`). **0/12 maintainer-actionable.**
- Overall dick precision across 3 repos: ~844 findings,
  actionable ≈ 5 (≈0.6%).

### 4d. Recall check (planted TPs in /tmp/recall): 4/4 found
(file-ref ×2, number-drift ×2). Recall works; natural prevalence ≈ 0
(0 hits in 285 requests+axios files). Real but rare; wild precision
unmeasured (django file-refs suggest ~0% actionable).

### 4e. Scale
axios 248 files 0.29s; django 2977 files ~9s. Speed is genuinely good and
is the one metric that survived. Speed of wrong answers is not value.

## 5. Answers to the 15 attack questions

1. Snapshot-only useful, or history necessary? **History necessary for
   semantics** (whole field moved JIT); snapshot suffices only for
   mechanics — which still need imports/framework knowledge the snapshot
   scan lacks. Snapshot-only as architected is insufficient.
2. Are detected claims the ones developers care about? **No.** Fields,
   attrs, params, locals, externals, options, prose — mistaken for
   functions. The true hits were a typo and decade-old XXXs.
3. Can deterministic rules reach useful precision? **Not this design.**
   Measured lie precision 0–3% in the wild. Decidable subset exists but
   needs import/framework awareness (different project).
4. Do lie/drift/smell hold? **No.** 97% of "lies" were false; the labels
   assert certainty the tool does not have. Severity system is branding.
5. Is "epistemic linter" a real category? **No.** It is contract+style
   linting with a philosophy hat. No new category demonstrated.
6. Meaningfully different? **Where it overlaps, incumbents win; where it
   differs, it is mostly wrong.** 5/8 checkers redundant.
7. Zero-dep/offline: value or limitation? **Limitation.** The regex JS
   parser is unsound (URL-brace, arrow-implicit, constructors). A real
   parser (tsc/tree-sitter) is the fix; zero-dep forbids it. Self-imposed.
8. Checker architecture sound? **No.** Function-node model wrong for
   classes (ctor params), arrow-implicit returns, alias renames, kwargs
   passthrough; index lacks attrs/params/locals; resolvers lack
   imports/namespaces/frameworks; `.d.ts` unexcluded; numeric ext defect.
   Eight independent systematic defect classes found; each fix is
   whack-a-mole on a wrong abstraction.
9. Most valuable checker? **fragile-anchor (smell): only one with TPs
   and no exact incumbent** — value still low (ancient accepted markers).
10. Least defensible? **stale-symbol-ref** (flagship; 25/26 FP on
    requests across 5 systematic classes) — alternatively return-mismatch
    (mechanically broken on modern JS).
11. Maintainer-care rate? **≈0.6% overall (5/844); lie precision ≈1–3%.**
12. Uninstall triggers? **Confirmed two**: "Delete it — git remembers" on
    a TLS security explanation; 14 "lies" on a flagship API's correct
    TypedDict docs.
13. Could incumbents add this trivially? **Yes.** darglint/Ruff/eslint
    own their surfaces; file/anchor rules are small additions for them.
    No moat.
14. Niche empty, or terminology miss? **Terminology miss.** "docstring
    linter", "JSDoc validation", "dead code", "link checker", "warning
    comments", "nitpick", "obsolete/JIT comment detection" — yesterday's
    search used invented phrasing ("epistemic", "belief verifier") no
    incumbent uses.
15. Better interpretation missed yesterday? **Yes: this is contract-lint
    (solved per-ecosystem) + a thin residue of hygiene (file/anchor)
    that needs project-graph knowledge, not snapshots.** The valuable
    unsolved part is import/rename-aware reference checking (Fraco
    direction) with history — a different tool.

## 6. Assumptions: wrong vs survived

WRONG: niche empty; snapshot suffices; precision-over-recall achieved
(actual 1–3% lie precision); severities meaningful; zero-dep is a moat;
"own tree scans clean" generalizes (own tree is 9 tiny files by the
author — vacuous); examples demonstrated value (they were manufactured);
"epistemic linter" is a category.
SURVIVED (narrow): mechanical file/anchor/number claims are checkable
offline with proven recall; the tool is fast (~300+ files/s); fixtures
are good teaching material; evidence-formatted output (claim+evidence+fix)
is a good reporting habit; the negative result itself is informative.

## 7. Verdict: OUTCOME C — thesis fails as stated

The general thesis ("snapshot-only tool verifies falsifiable comment
claims") is falsified for general use: 5/8 checkers are redundant with
strictly-superior incumbents, the flagship checker runs at ~4% precision
in the wild, even the best-volume survivor (file-ref) graded 0/12
actionable, severity labels misrepresent certainty, and the architecture
(zero-dep regex parsing, def-name-only index, no imports/namespaces)
cannot reach useful precision without becoming a different project.
`grounded` as a general linter should not be adopted; this is stated
plainly per the mission. No code changes are made to pretend otherwise:
implementation frozen as an experiment; fixes deliberately NOT applied
(URL-brace stripping, arrow-implicit returns, ctor handling, numeric ext,
.d.ts exclusion, import-aware resolution) because each repairs a symptom
of a falsified approach. The honest residue (hygiene + fixtures + speed +
negative result) is documented, not shipped as victory.

## 8. Changes made / deliberately NOT made

MADE (docs only): this file; README adversarial-notice (points here).
NOT MADE (with reason): no checker repairs (would imply surviving
thesis); no default-config narrowing (would rebrand failure as focus
without precision evidence for the remainder); no new features; no
severity relabeling (cosmetic); no deletion of code (preserve experiment
+ fixtures for teaching/replication).
