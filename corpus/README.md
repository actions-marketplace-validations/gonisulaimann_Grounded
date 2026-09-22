# Precision corpus

Planted staleness with exact expected findings. This is the evidence
mechanism behind opt-in checker graduation: a checker graduates by
measured precision, and this corpus is where it is measured.

## Adding a case

1. Create `corpus/cases/<id>/` with the fixture tree (any layout).
2. Add `expected.json`:

```json
{
  "enable": ["stale-doc-ref"],
  "expect": [
    {"checker": "stale-doc-ref", "path": "README.md", "line": 7,
     "contains": "fetch_user"}
  ]
}
```

3. Run `python3 corpus/run.py`. It must print `[PASS]`.

Rules:

* `"enable": null` means default flags (for default-precision guards).
* Comparison is **exact set equality**. A missing finding (recall gap)
  and an extra finding (precision gap) both fail the case.
* `contains` matches a substring of the finding title.
* Fixtures are load-bearing: never "fix" a fixture to make the runner
  pass. If the checker is wrong, fix the checker; if the expectation
  is wrong, justify the change in the commit message.
* Keep cases minimal: one behavior per case, smallest tree that shows
  it. Name cases `<checker>-<behavior>`.
* `adv-` cases are adversarial: `adv-<name>` either plants realistic
  agent-style rot that must fire, or constructs legitimate code that
  merely looks stale and must stay silent. Both directions are
  load-bearing: a silence case passing vacuously (checker not running,
  file not collected) is a false green — check the findings count in
  the runner output, not just PASS.
