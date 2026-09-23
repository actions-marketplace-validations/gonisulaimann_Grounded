"""Config loading (grounded.toml / pyproject.toml), stdlib only."""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

from .checkers import CHECKERS, DEFAULT_ENABLED

DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    "dist", "build", ".next", "out", "coverage", ".nyc_output",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "vendor", "third_party",
}

DEFAULT_IGNORE_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "poetry.lock", "Pipfile.lock",
}

DEFAULT_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".c", ".h"}


class ConfigError(ValueError):
    """Unreadable or missing config: usage/environment error (exit 2),
    never silent defaults. A config the user named must either load or
    fail loudly — defaults would mask the typo with a green build."""


class Config:
    def __init__(
        self,
        enabled: set[str] | None = None,
        ignore_dirs: set[str] | None = None,
        ignore_files: set[str] | None = None,
        fail_on: str = "lie",
        path_aliases: dict[str, list[str]] | None = None,
    ):
        # Explicit empty sets are meaningful (run nothing, ignore nothing
        # extra): only None means "defaults". `if x` would conflate the two.
        self.enabled = set(enabled) if enabled is not None else set(DEFAULT_ENABLED)
        self.ignore_dirs = set(ignore_dirs) if ignore_dirs is not None else set(DEFAULT_IGNORE_DIRS)
        self.ignore_files = set(ignore_files) if ignore_files is not None else set(DEFAULT_IGNORE_FILES)
        self.fail_on = fail_on
        self.path_aliases = dict(path_aliases) if path_aliases else {}

    @classmethod
    def load(cls, root: Path, explicit: str | None = None) -> "Config":
        data: dict = {}
        cfg_file: Path | None = None
        if explicit:
            cfg_file = Path(explicit)
            if not cfg_file.exists():
                raise ConfigError(f"config file does not exist: {explicit}")
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
        if enabled is not None:
            # Unknown ids fail, exactly like --enable: a typo'd id that
            # silently runs a different set masks drift with a green build.
            unknown = sorted(e for e in enabled if e not in CHECKERS)
            if unknown:
                raise ConfigError(
                    f"unknown checker id(s) in {cfg_file or 'config'}: "
                    f"{', '.join(unknown)}. Known: {', '.join(sorted(CHECKERS))}")
        if "disable" in data:
            dis = {str(x) for x in data["disable"]}
            unknown = sorted(d for d in dis if d not in CHECKERS)
            if unknown:
                raise ConfigError(
                    f"unknown checker id(s) in {cfg_file or 'config'}: "
                    f"{', '.join(unknown)}. Known: {', '.join(sorted(CHECKERS))}")
            enabled = (enabled if enabled is not None else set(DEFAULT_ENABLED)) - dis
        ignore_dirs = set(DEFAULT_IGNORE_DIRS)
        if "ignore_dirs" in data:
            ignore_dirs |= {str(x) for x in data["ignore_dirs"]}
        ignore_files = set(DEFAULT_IGNORE_FILES)
        if "ignore_files" in data:
            ignore_files |= {str(x) for x in data["ignore_files"]}
        fail_on = str(data.get("fail_on", data.get("fail-on", "lie")))
        if fail_on not in ("lie", "drift", "smell", "never"):
            fail_on = "lie"
        path_aliases: dict[str, list[str]] = {}
        raw_aliases = data.get("path_aliases", data.get("path-aliases", {}))
        if isinstance(raw_aliases, dict):
            for key, val in raw_aliases.items():
                if isinstance(val, str):
                    path_aliases[str(key)] = [val]
                elif isinstance(val, list):
                    path_aliases[str(key)] = [str(v) for v in val if isinstance(v, (str, int, float))]
        return cls(enabled=enabled, ignore_dirs=ignore_dirs, ignore_files=ignore_files, fail_on=fail_on,
                   path_aliases=path_aliases)


def _read_toml(path: Path) -> dict:
    if tomllib is not None:
        try:
            with open(path, "rb") as fh:
                val = tomllib.load(fh)
                return val if isinstance(val, dict) else {}
        except OSError as exc:
            raise ConfigError(f"config file unreadable: {path} ({exc})") from exc
        except ValueError as exc:
            raise ConfigError(f"config file is not valid TOML: {path} ({exc})") from exc
    # Python 3.10 has no tomllib: fall back to the strict subset reader
    # (grounded/toml_compat.py). Outside the subset reads as unreadable,
    # exactly like a corrupt file on newer Pythons.
    from .toml_compat import loads as compat_loads
    try:
        val = compat_loads(path.read_text(encoding="utf-8"))
        return val if isinstance(val, dict) else {}
    except OSError as exc:
        raise ConfigError(f"config file unreadable: {path} ({exc})") from exc
    except ValueError as exc:
        raise ConfigError(f"config file is not valid TOML: {path} ({exc})") from exc
