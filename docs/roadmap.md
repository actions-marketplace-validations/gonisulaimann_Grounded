# Roadmap

Where Grounded goes next, in evidence order. Nothing here graduates,
ships, or expands on age or enthusiasm: every item names its falsifier
— the measurement that would prove it done, or prove it wrong.

## Standing rules (do not renegotiate per feature)

- Precision over recall. A silence must be evidence-based; a finding
  must be a mechanical contradiction.
- No LLM in the detection engine. Ever.
- No new checker, language, or default-on promotion without a corpus
  case plus a real-repo measurement.
- A partial snapshot never claims absence for what it did not index.
- Stdlib only, offline, deterministic. Test counts and corpus results
  are the only admissible progress metrics.

## Now (v0.16.x patch line)

- [x] Repair round: config errors loud, project-root file scans,
  MCP/LSP partial-snapshot fixes, staged `--changed`, quotepath,
  symlink guard, HTML slots, TOML subset, tsconfig cycles.
- [x] Graduate `unclosed-fence` (measured: 11,564 Markdown files, 0 FP,
  differential fuzz against CommonMark).
- [x] Backfill the missing v0.16.0 `darwin-amd64` asset; audit green.
- [ ] Metasyntactic calls, completed builtins, narrative-framed doc
  blocks (measured: svelte 8→6, rich 36→17).

## Next: recall evidence for the two oldest opt-ins

`stale-doc-ref` and `ghost-export` have precision corpora and
suppression-only real-repo rounds. What they lack is **recall**: proof
they fire on real rot, not just planted fixtures. The falsifier is a
documented real-repo true positive per checker (like svelte's
`strip_link` for ghost-export), or an honest "none found in N files
across M repos" entry. Until then, both stay opt-in.

## Next: measured close-outs of documented residuals

- JS `require` inside `try/catch` (compat fallback, seen: OmniRoute's
  `electron/loginManager.js`). Needs a code-mask-aware try-range
  tracker plus a corpus pair (guarded silent, unguarded fires) plus a
  real-repo A/B showing no recall loss. Not attempted without an
  OmniRoute-scale tree to measure against.
- Generated content in dot-directories (`.source/`, seen: fumadocs).
  Needs a curated, evidenced list — never a blanket dot-dir rule
  (`.github` is not generated).
- Non-English illustrative markers (17 translated rich READMEs).
  Explicitly last: 9+ languages of markers is overfitting surface.

## Then: distribution, not features

- `npx grounded` / install friction only on evidence of demand.
- The `grounded-fences` pre-commit hook (shipped) is the adoption
  wedge: per-file gating for the checker whose corruption is
  file-scoped.

## Non-goals (will not build)

Graph databases, claim standards, trust scores, SaaS, an LLM judge,
new languages without corpora, MCP tool growth beyond the current
three, T/F/U verdicts. Each was proposed and refused with reasons in
the project history; re-proposing one requires new evidence, not new
enthusiasm.

## How to propose work

Open the change with its measurement plan: corpus case first, then the
real-repo A/B (before/after counts, every delta classified TP/FP).
Suppression-only changes need a positive control proving recall
survived. See `corpus/README.md` and `bench/recall.py`.
