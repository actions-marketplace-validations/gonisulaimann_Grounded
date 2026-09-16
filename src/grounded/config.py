"""Config loading (grounded.toml / pyproject.toml), stdlib only."""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

from .checkers import CHECKERS

DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    "dist", "build", ".next", "out", "coverage", ".nyc_output",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "vendor", "third_party",
}

DEFAULT_IGNORE_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "poetry.lock", "Pipfile.lock",
}

DEFAULT_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}


class Config:
    def __init__(
        self,
        enabled: set[str] | None = None,
        ignore_dirs: set[str] | None = None,
        ignore_files: set[str] | None = None,
        fail_on: str = "lie",
    ):
        self.enabled = set(enabled) if enabled else set(CHECKERS)
        self.ignore_dirs = set(ignore_dirs) if ignore_dirs else set(DEFAULT_IGNORE_DIRS)
        self.ignore_files = set(ignore_files) if ignore_files else set(DEFAULT_IGNORE_FILES)
        self.fail_on = fail_on

    @classmethod
    def load(cls, root: Path, explicit: str | None = None) -> "Config":
        data: dict = {}
        cfg_file: Path | None = None
        if explicit:
            cfg_file = Path(explicit)
            if cfg_file.exists():
                data = _read_toml(cfg_file)
        else:
            for name in ("grounded.toml", ".grounded.toml", "pyproject.toml"):
                cand = root / name
                if cand.exists():
                    raw = _read_toml(cand)
                    if name == "pyproject.toml":
                        raw = (raw.get("tool") or {}).get("grounded", {})
                    data = raw
                    cfg_file = cand
                    break
        enabled = None
        if "enable" in data:
            enabled = {str(x) for x in data["enable"]}
        elif "enabled" in data:
            enabled = {str(x) for x in data["enabled"]}
        if "disable" in data:
            dis = {str(x) for x in data["disable"]}
            enabled = (enabled or set(CHECKERS)) - dis
        if enabled is not None:
            enabled = {e for e in enabled if e in CHECKERS} or set(CHECKERS)
        ignore_dirs = set(DEFAULT_IGNORE_DIRS)
        if "ignore_dirs" in data:
            ignore_dirs |= {str(x) for x in data["ignore_dirs"]}
        ignore_files = set(DEFAULT_IGNORE_FILES)
        if "ignore_files" in data:
            ignore_files |= {str(x) for x in data["ignore_files"]}
        fail_on = str(data.get("fail_on", data.get("fail-on", "lie")))
        if fail_on not in ("lie", "drift", "smell", "never"):
            fail_on = "lie"
        return cls(enabled=enabled, ignore_dirs=ignore_dirs, ignore_files=ignore_files, fail_on=fail_on)


def _read_toml(path: Path) -> dict:
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as fh:
            val = tomllib.load(fh)
            return val if isinstance(val, dict) else {}
    except (OSError, ValueError):
        return {}
