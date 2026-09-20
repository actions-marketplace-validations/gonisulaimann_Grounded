"""LSP server subset over stdio (stdlib only): live reference diagnostics.

Implements the LSP 3.17 framing (Content-Length headers) and a focused
method set:

- initialize / initialized / shutdown / exit
- textDocument/didOpen, didChange (full sync), didSave
- textDocument/publishDiagnostics (server push; lie=Error, drift=Warning,
  smell=Information)
- textDocument/codeAction (unique file/symbol fixes as WorkspaceEdits)

The repo index builds once per workspace root and is reused; each edit
re-runs only the open file's checkers (milliseconds). Findings identical
to `grounded scan` by construction: same checkers, same index.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import unquote
from urllib.request import url2pathname

from . import __version__

_SEV = {"lie": 1, "drift": 2, "smell": 3}


def uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        return Path(url2pathname(unquote(uri[7:])))
    return Path(uri)


def path_to_uri(path: Path) -> str:
    from urllib.parse import quote
    from urllib.request import pathname2url
    return "file://" + quote(pathname2url(str(path.resolve())))


class LspServer:
    def __init__(self) -> None:
        self.root: Path | None = None
        self.index = None
        self.config = None
        self.docs: dict[str, str] = {}  # uri -> text
        self.shutdown_requested = False

    # -- workspace ------------------------------------------------------

    def _ensure_index(self, doc_uri: str):
        from .config import Config
        from .scanner import collect_files
        from .repo_index import RepoIndex
        path = uri_to_path(doc_uri)
        root = self.root or (path.parent if path.suffix else Path.cwd())
        if self.index is None or getattr(self, "_root", None) != root:
            try:
                files = collect_files(root, Config.load(root))
            except OSError:
                files = []
            texts: dict[str, str] = {}
            for f in files:
                try:
                    texts[str(f)] = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
            self.index = RepoIndex(root.resolve(), [f for f in files if str(f) in texts], texts)
            self.config = Config.load(root)
            self._root = root
        return root

    def _rel_of(self, uri: str, root: Path) -> tuple[Path, str]:
        path = uri_to_path(uri)
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        return path, rel

    def _reindex_doc(self, uri: str, text: str | None) -> None:
        """Patch the index from a buffer (or disk when text is None).

        Unsaved renames must be visible to peer files immediately; waiting
        for save leaves a window where the index contradicts the editor.
        """
        root = self._ensure_index(uri)
        if self.index is None:
            return
        path, rel = self._rel_of(uri, root)
        if text is None:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                self.index.forget_file(rel)
                return
        self.index._index_one(rel, path.suffix.lower(), text)

    def _publish_all(self, write) -> None:
        for uri in list(self.docs):
            self._publish(uri, write)

    # -- diagnostics ----------------------------------------------------

    def _diagnose(self, uri: str, text: str | None) -> list[dict]:
        from .parsers import parse_file
        from .scanner import apply_suppressions
        root = self._ensure_index(uri)
        path, rel = self._rel_of(uri, root)
        body = text if text is not None else self.docs.get(uri, "")
        if body is None:
            return []
        facts = parse_file(path, rel, body)
        if facts is None:
            return []
        from .checkers import CHECKERS
        findings = []
        for cid in sorted(self.config.enabled):
            fn = CHECKERS.get(cid)
            if fn is None:
                continue
            try:
                findings.extend(fn(facts, self.index) or [])
            except Exception:
                continue
        findings, _ = apply_suppressions(findings, {rel: facts})
        diags = []
        for f in findings:
            lines = body.splitlines()
            start_char, end_char = 0, 0
            if 1 <= f.line <= len(lines):
                span = f.claim.strip().strip("`")
                at = lines[f.line - 1].find(span)
                if at >= 0:
                    start_char, end_char = at, at + len(span)
                else:
                    end_char = len(lines[f.line - 1])
            diags.append({
                "range": {"start": {"line": f.line - 1, "character": start_char},
                          "end": {"line": f.end_line - 1, "character": end_char}},
                "severity": _SEV.get(f.severity, 4),
                "code": f.checker,
                "source": "grounded",
                "message": f"{f.title} Evidence: {f.evidence}",
            })
        return diags

    def _publish(self, uri: str, write) -> None:
        write({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
               "params": {"uri": uri, "diagnostics": self._diagnose(uri, None)}})

    # -- code actions ---------------------------------------------------

    def _actions(self, uri: str, diag: dict) -> list[dict]:
        from .fix import apply_fixes, apply_symbol_fixes, file_fix_candidates, symbol_fix_candidates
        from .parsers import parse_file
        root = self._ensure_index(uri)
        path, rel = self._rel_of(uri, root)
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return []
        body = self.docs.get(uri, "")
        facts = parse_file(path, rel, body)
        if facts is None:
            return []
        from .checkers import CHECKERS
        findings = []
        for cid in sorted(self.config.enabled):
            fn = CHECKERS.get(cid)
            if fn is None:
                continue
            try:
                findings.extend(fn(facts, self.index) or [])
            except Exception:
                continue
        code = (diag.get("code") or "")
        line = int(diag.get("range", {}).get("start", {}).get("line", -1)) + 1
        lines = body.splitlines()
        buffer = {rel: lines}
        actions = []
        for f in findings:
            if f.checker != code or f.line != line:
                continue
            if f.checker == "stale-file-ref":
                cands = file_fix_candidates([f], root, buffer)
                if len(cands) != 1:
                    continue
                _, replacement, ln = cands[0]
                new_line = self._rewrite_line(lines, ln, f.claim, replacement)
                if new_line is None:
                    continue
                actions.append(self._edit_action(
                    f"Fix stale path to {replacement}", uri, ln, new_line))
            elif f.checker == "stale-symbol-ref":
                cands = symbol_fix_candidates([f], root, self.index, buffer)
                if len(cands) != 1:
                    continue
                _, old_seg, new_seg, ln = cands[0]
                span = f.claim.strip()
                if old_seg not in span:
                    continue
                new_line = self._rewrite_line(lines, ln, span, span.replace(old_seg, new_seg, 1))
                if new_line is None:
                    continue
                actions.append(self._edit_action(
                    f"Rename to {new_seg}()", uri, ln, new_line))
        return actions

    @staticmethod
    def _rewrite_line(lines: list[str], ln: int, old: str, new: str) -> str | None:
        if not (1 <= ln <= len(lines)) or not old or old not in lines[ln - 1]:
            return None
        return lines[ln - 1].replace(old, new, 1)

    @staticmethod
    def _edit_action(title: str, uri: str, ln: int, new_text: str) -> dict:
        return {
            "title": title,
            "kind": "quickfix",
            "diagnostics": [],
            "edit": {"changes": {uri: [{
                "range": {"start": {"line": ln - 1, "character": 0},
                          "end": {"line": ln - 1, "character": 10 ** 9}},
                "newText": new_text,
            }]}},
        }

    # -- protocol ---------------------------------------------------------

    def handle(self, msg: dict, write) -> str:
        """Returns 'exit' when the server should terminate, else 'continue'."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return "continue"
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        def respond(result) -> None:
            write({"jsonrpc": "2.0", "id": req_id, "result": result})

        def fail(code: int, message: str) -> None:
            if req_id is not None:
                write({"jsonrpc": "2.0", "id": req_id,
                       "error": {"code": code, "message": message}})

        if method == "initialize":
            folders = (params.get("workspaceFolders") or [{}])
            uri = params.get("rootUri") or (folders[0].get("uri") if folders else None)
            if uri:
                try:
                    self.root = uri_to_path(uri)
                except Exception:
                    self.root = None
            respond({
                "capabilities": {
                    "textDocumentSync": 1,
                    "codeActionProvider": True,
                },
                "serverInfo": {"name": "grounded", "version": __version__},
            })
            return "continue"
        if method in ("initialized",):
            return "continue"
        if method == "exit":
            return "exit"
        if method == "shutdown":
            self.shutdown_requested = True
            if req_id is not None:
                respond(None)
            return "continue"
        if method == "textDocument/didOpen":
            doc = params.get("textDocument", {})
            uri = doc.get("uri", "")
            self.docs[uri] = doc.get("text", "")
            self._reindex_doc(uri, self.docs[uri])
            self._publish_all(write)
            return "continue"
        if method == "textDocument/didChange":
            uri = params.get("textDocument", {}).get("uri", "")
            changes = params.get("contentChanges", [])
            if changes and "text" in changes[-1]:
                self.docs[uri] = changes[-1]["text"]
            self._reindex_doc(uri, self.docs.get(uri))
            self._publish_all(write)
            return "continue"
        if method == "textDocument/didSave":
            uri = params.get("textDocument", {}).get("uri", "")
            if "text" in params:
                self.docs[uri] = params["text"]
            self._reindex_doc(uri, self.docs.get(uri))
            self._publish_all(write)
            return "continue"
        if method == "textDocument/didClose":
            uri = params.get("textDocument", {}).get("uri", "")
            self.docs.pop(uri, None)
            # Closed without save abandons the buffer: revert the index to
            # disk truth (or forget the file if it never existed there).
            self._reindex_doc(uri, None)
            self._publish_all(write)
            return "continue"
        if method == "textDocument/codeAction":
            uri = params.get("textDocument", {}).get("uri", "")
            diags = (params.get("context", {}) or {}).get("diagnostics", [])
            actions: list[dict] = []
            for d in diags:
                actions.extend(self._actions(uri, d))
            if req_id is not None:
                respond(actions)
            return "continue"
        if req_id is not None:
            fail(-32601, f"method not found: {method}")
        return "continue"


def serve() -> int:
    server = LspServer()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    log = sys.stderr

    def write(msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        stdout.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        stdout.flush()

    def read_message():
        headers: dict[str, str] = {}
        while True:
            line = stdin.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                break
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode().strip().lower()] = v.decode().strip()
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            return {"jsonrpc": "2.0", "id": None,
                    "method": "__bad_frame__"}
        if length <= 0 or length > 50_000_000:
            return {"jsonrpc": "2.0", "id": None, "method": "__bad_frame__"}
        try:
            return json.loads(stdin.read(length).decode("utf-8"))
        except ValueError:
            return {"jsonrpc": "2.0", "id": None, "method": "__bad_frame__"}

    while True:
        msg = read_message()
        if msg is None:
            break
        try:
            if isinstance(msg, dict) and msg.get("method") == "__bad_frame__":
                log.write("grounded lsp: bad frame\n")
                continue
            verdict = server.handle(msg, write)
        except Exception as exc:  # never break the stream
            log.write(f"grounded lsp: internal error: {exc}\n")
            continue
        if verdict == "exit":
            break
    return 0
