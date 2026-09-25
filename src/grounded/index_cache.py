"""Persistent per-file index contributions, so a scan re-indexes only the
files that changed since the last one.

Each file's contribution to the RepoIndex is a pure function of its path,
suffix and text (that is what makes the parallel build possible), so it
can be stored keyed by the file's stat and replayed. The index built from
replayed contributions is the index a fresh build gives: the merge is the
one the fresh build uses, pinned by TestIndexCache.

Trust rules, in the order they are checked:
- Any load problem (missing, truncated, other tool or Python version) is
  an empty cache. A cache only ever accelerates a scan, never fails it.
- An entry is reused only when (mtime_ns, size) match the file now.
- An entry whose mtime is within RACY_NS of the moment the cache was
  written is not reused ("racily clean", the same rule git applies to its
  index): on coarse-mtime filesystems an edit in that window can keep the
  same stat.

Stored under the repository's git dir, so it is never committed and needs
no .gitignore entry. Outside a git repository there is no cache.
"""
from __future__ import annotations

import hashlib
import marshal
import os
import sys
import time
from pathlib import Path

FORMAT = 2
RACY_NS = 2_000_000_000


def _git_dir(root: Path) -> Path | None:
    for d in (root, *root.parents):
        dot = d / ".git"
        try:
            if dot.is_dir():
                return dot
            if dot.is_file():
                # Worktrees and submodules: `.git` is a file naming the real dir.
                line = dot.read_text(encoding="utf-8").strip()
                if line.startswith("gitdir:"):
                    target = Path(line[len("gitdir:"):].strip())
                    return target if target.is_absolute() else (d / target).resolve()
                return None
        except OSError:
            return None
    return None


def default_cache_path(root: Path, variant: str = "") -> Path | None:
    """Cache file for this scan root, or None outside a git repository.
    `variant` separates scans that index different file sets, so they do
    not evict each other's entries."""
    git = _git_dir(root)
    if git is None:
        return None
    digest = hashlib.sha1(f"{root}\0{variant}".encode("utf-8")).hexdigest()[:16]
    return git / "grounded" / f"index-{digest}.marshal"


def _key() -> tuple:
    from . import __version__
    # ast.parse differs across Python versions (a file one version cannot
    # parse is opaque to the index), so entries never cross versions.
    return (FORMAT, __version__, tuple(sys.version_info[:3]), sys.implementation.name)


class IndexCache:
    def __init__(self, path: Path):
        self.path = path
        self._old: dict[str, tuple[int, int, dict]] = {}
        self._new: dict[str, tuple[int, int, dict]] = {}
        self._written_ns = 0
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        try:
            data = marshal.loads(self.path.read_bytes())
        except (OSError, EOFError, ValueError, TypeError):
            return
        if not isinstance(data, dict) or data.get("key") != _key():
            return
        files = data.get("files")
        written = data.get("written_ns")
        if not isinstance(files, dict) or not isinstance(written, int):
            return
        self._old = files
        self._written_ns = written

    def lookup(self, rel: str, stat: tuple[int, int] | None) -> dict | None:
        """The stored contribution for rel, if it is still the file's."""
        old = self._old.get(rel)
        if old is None or stat is None:
            return None
        try:
            mtime_ns, size, entry = old
        except (TypeError, ValueError):
            return None
        if (mtime_ns, size) != stat or mtime_ns >= self._written_ns - RACY_NS \
                or not isinstance(entry, dict):
            return None
        self._new[rel] = old
        self.hits += 1
        return entry

    def store(self, rel: str, stat: tuple[int, int] | None, entry: dict) -> None:
        self.misses += 1
        if stat is not None:
            self._new[rel] = (stat[0], stat[1], entry)

    # Rewriting the whole file costs ~0.25 s on cpython (6.6 MB). A few
    # misses (the files being edited, racily clean ones) re-index in
    # milliseconds, so they are left for a later scan to persist.
    SAVE_MIN_MISSES = 32

    def save(self) -> None:
        """Write the entries of this scan's files (deleted files drop out).
        Skipped when too little changed to pay for the write; never raises."""
        dropped = len(self._old) - self.hits
        if not self.misses and not dropped:
            return
        if self._old and self.misses < self.SAVE_MIN_MISSES and dropped < self.SAVE_MIN_MISSES:
            return
        payload = {"key": _key(), "written_ns": time.time_ns(), "files": self._new}
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(marshal.dumps(payload))
            os.replace(tmp, self.path)
        except (OSError, ValueError):
            try:
                tmp.unlink()
            except OSError:
                pass
