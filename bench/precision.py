"""Real-repo precision gate: every finding on pinned repos is classified.

`corpus/run.py` proves behavior in the smallest tree; `bench/recall.py`
proves planted rot still fires inside real repos. This harness closes the
third side: on real, unmodified repos pinned to exact commits, **every**
finding the default checker set reports must already be labeled in
`bench/precision/ledger.json` as a true positive (TP) or a false
positive (FP). The gate fails on:

* an **unlabeled** finding (new behavior nobody has classified yet),
* a finding labeled **FP** (a known false alarm the change did not fix
  is allowed only while listed in `known_fp`; see below),
* a **vanished TP** (a labeled true positive that stopped firing: a
  recall regression).

Only lies and drifts are gated by default (`--min-severity`): smells are
style notes, not contradictions. Findings are keyed by (repo, path, checker, claim), not line numbers, so
unrelated edits to the ledger's repos cannot churn it; the repos are
pinned by SHA anyway.

    python3 bench/precision.py [--repos-dir DIR] [--only NAME,...]
                               [--update-unlabeled] [--json OUT]

`--repos-dir` holds clones named after the manifest entries (cloned or
fetched to the pinned SHA when missing). `--update-unlabeled` appends
unlabeled findings to the ledger as `"verdict": "?"` for a human to
classify; a `?` entry still fails the gate. Stdlib only.

Precision is reported as TP / (TP + FP) over all labeled findings that
fired, per checker and overall.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
HERE = Path(__file__).resolve().parent / "precision"
MANIFEST = HERE / "repos.json"
LEDGER = HERE / "ledger.json"
RANK = {"smell": 1, "drift": 2, "lie": 3}


def _git(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def ensure_checkout(entry: dict, repos_dir: Path) -> Path:
    """A clone of entry['url'] at entry['sha'] (shallow, fetched by SHA)."""
    dest = repos_dir / entry["name"]
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        _git(["init", "-q"], cwd=dest)
        _git(["remote", "add", "origin", entry["url"]], cwd=dest)
    try:
        head = _git(["rev-parse", "HEAD"], cwd=dest)
    except subprocess.CalledProcessError:
        head = ""
    if head != entry["sha"]:
        _git(["fetch", "-q", "--depth", "1", "origin", entry["sha"]], cwd=dest)
        _git(["checkout", "-q", "--force", entry["sha"]], cwd=dest)
    return dest


def scan(path: Path) -> tuple[list[dict], int]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "grounded.cli", "scan", str(path),
         "--format", "json", "--no-color", "--fail-on", "never"],
        capture_output=True, text=True, env=env)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"scan of {path} exited {proc.returncode}: {proc.stderr[-400:]}")
    errors = proc.stderr.count("checker error:")
    return json.loads(proc.stdout or "[]"), errors


def key_of(repo: str, f: dict) -> str:
    return f"{repo}::{f['path']}::{f['checker']}::{f.get('claim', '')}"


def load_ledger() -> dict:
    if not LEDGER.exists():
        return {"entries": {}}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def save_ledger(ledger: dict) -> None:
    ledger["entries"] = dict(sorted(ledger["entries"].items()))
    LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repos-dir", default=os.environ.get("GROUNDED_PRECISION_REPOS",
                                                          str(REPO / ".precision-repos")))
    ap.add_argument("--only", default="", help="comma-separated manifest names")
    ap.add_argument("--update-unlabeled", action="store_true",
                    help="append unlabeled findings to the ledger as verdict '?'")
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--min-severity", choices=["lie", "drift", "smell"], default="drift",
                    help="lowest severity the gate classifies (default: drift; smells "
                         "are style notes, not contradictions)")
    args = ap.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    only = {s for s in args.only.split(",") if s}
    entries = [e for e in manifest["repos"] if not only or e["name"] in only]
    ledger = load_ledger()
    labels: dict[str, dict] = ledger["entries"]
    repos_dir = Path(args.repos_dir)

    unlabeled: list[str] = []
    fp_hits: list[str] = []
    seen: set[str] = set()
    per_checker: dict[str, dict[str, int]] = {}
    per_repo: list[dict] = []
    total_errors = 0
    for e in entries:
        path = ensure_checkout(e, repos_dir)
        findings, errors = scan(path)
        findings = [f for f in findings if RANK.get(f["severity"], 0) >= RANK[args.min_severity]]
        total_errors += errors
        tp = fp = unl = 0
        for f in findings:
            k = key_of(e["name"], f)
            if k in seen:
                continue  # same claim twice in one file: one label covers both
            seen.add(k)
            cell = per_checker.setdefault(f["checker"], {"tp": 0, "fp": 0, "unlabeled": 0})
            verdict = labels.get(k, {}).get("verdict")
            if verdict == "TP":
                tp += 1
                cell["tp"] += 1
            elif verdict == "FP":
                fp += 1
                cell["fp"] += 1
                fp_hits.append(k)
            else:
                unl += 1
                cell["unlabeled"] += 1
                unlabeled.append(k)
                if args.update_unlabeled and k not in labels:
                    labels[k] = {"verdict": "?", "severity": f["severity"],
                                 "line": f["line"], "title": f["title"], "note": ""}
        per_repo.append({"repo": e["name"], "findings": len(findings),
                         "tp": tp, "fp": fp, "unlabeled": unl,
                         "checker_errors": errors})
        print(f"{e['name']:<14} findings={len(findings):<4} tp={tp:<3} fp={fp:<3} "
              f"unlabeled={unl:<3} errors={errors}")

    scanned = {e["name"] for e in entries}
    vanished = [k for k, v in labels.items()
                if v.get("verdict") == "TP" and k.split("::", 1)[0] in scanned and k not in seen]

    if args.update_unlabeled:
        save_ledger(ledger)

    tp_all = sum(c["tp"] for c in per_checker.values())
    fp_all = sum(c["fp"] for c in per_checker.values())
    precision = tp_all / (tp_all + fp_all) if (tp_all + fp_all) else 1.0
    print()
    print(f"{'checker':<20} {'tp':>4} {'fp':>4} {'?':>4}  precision")
    for name, c in sorted(per_checker.items()):
        denom = c["tp"] + c["fp"]
        p = f"{c['tp'] / denom:.3f}" if denom else "  -  "
        print(f"{name:<20} {c['tp']:>4} {c['fp']:>4} {c['unlabeled']:>4}  {p}")
    print(f"\noverall precision {precision:.3f} (tp={tp_all}, fp={fp_all}) "
          f"across {len(entries)} repos")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "precision": precision, "tp": tp_all, "fp": fp_all,
            "repos": per_repo, "per_checker": per_checker,
            "unlabeled": unlabeled, "fp_hits": fp_hits, "vanished_tp": vanished,
        }, indent=2), encoding="utf-8")

    known_fp = set(ledger.get("known_fp", []))
    new_fp = [k for k in fp_hits if k not in known_fp]
    failed = False
    for label, items in (("unlabeled finding", unlabeled),
                         ("false positive not in known_fp", new_fp),
                         ("true positive vanished (recall regression)", vanished)):
        for k in items:
            print(f"GATE: {label}: {k}")
            failed = True
    if total_errors:
        print(f"GATE: {total_errors} checker error(s): scans incomplete")
        failed = True
    print("precision gate:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
