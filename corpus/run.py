"""Precision corpus: planted staleness with exact expected findings.

Each case in cases/<id>/ is a fixture tree plus expected.json:

    {"enable": ["stale-doc-ref"] | null,
     "expect": [{"checker": "...", "path": "...", "line": 7,
                 "contains": "substring of the finding title"}]}

"enable": null means default flags. Comparison is exact set equality:
missing findings (recall) and extra findings (precision) both fail.
Exit 0 only when every case matches exactly.

Run:  python3 corpus/run.py   (repo root, stdlib only)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from grounded.config import Config
from grounded.scanner import scan_root


def run_case(case: Path) -> tuple[list[tuple], list[tuple], dict]:
    manifest = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    enable = manifest.get("enable")
    config = Config() if enable is None else Config(enabled=set(enable))
    findings, _, _ = scan_root(case, config)
    actual = {(f.checker, f.path, f.line, f.title) for f in findings}
    expected = manifest.get("expect", [])
    ok, missing, extra = True, [], []
    want_keys = set()
    for e in expected:
        key = (e["checker"], e["path"], e["line"])
        want_keys.add(key)
        hit = [a for a in actual if a[:3] == key and e.get("contains", "") in a[3]]
        if not hit:
            ok = False
            missing.append(key)
    for a in sorted(actual):
        if a[:3] not in want_keys:
            ok = False
            extra.append(a[:3] + (a[3][:60],))
    return ok, missing, extra, {"enable": enable, "n": len(actual)}


def main() -> int:
    cases = sorted(p for p in (REPO / "corpus" / "cases").iterdir() if p.is_dir())
    if not cases:
        print("corpus: no cases")
        return 1
    failures = 0
    per_checker: dict[str, dict[str, int]] = {}
    for case in cases:
        try:
            ok, missing, extra, info = run_case(case)
        except Exception as exc:
            ok, missing, extra, info = False, [], [], {"enable": "?", "n": 0}
            print(f"[FAIL] {case.name} (harness error: {exc})")
            failures += 1
            continue
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case.name} (enable={info['enable']}, findings={info['n']})")
        for m in missing:
            print(f"    missing: {m}")
        for x in extra:
            print(f"    extra:   {x}")
        if not ok:
            failures += 1
        manifest = json.loads((case / "expected.json").read_text(encoding="utf-8"))
        for e in manifest.get("expect", []):
            cell = per_checker.setdefault(e["checker"], {"tp": 0, "fp": 0, "fn": 0})
            cell["tp" if (e["checker"], e["path"], e["line"]) not in missing else "fn"] += 1
        for x in extra:
            per_checker.setdefault(x[0], {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
    print("\nchecker      TP  FP  FN  precision  recall")
    for cid in sorted(per_checker):
        c = per_checker[cid]
        prec = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 1.0
        rec = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 1.0
        print(f"{cid:12} {c['tp']:3} {c['fp']:3} {c['fn']:3}  {prec:.2f}      {rec:.2f}")
    print(f"\ncorpus: {len(cases) - failures}/{len(cases)} cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
