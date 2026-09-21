# Grounded for VS Code / Cursor

Thin LSP client for the grounded reference-integrity server. The
extension is a ~150-line wrapper: diagnostics (lie as error, drift as
warning, smell as information) plus the server's quickfix actions, for
Python, JavaScript/TypeScript (incl. React), Go, and C.

Published on [Open VSX](https://open-vsx.org/extension/gonisulaimann/grounded)
(Cursor, Windsurf, VSCodium). VS Code Marketplace listing is pending;
until then see `PUBLISHING.md`.

`.h` headers open as C in stock VS Code and are covered. If you use
Microsoft's C/C++ extension, headers may open as C++, which grounded
does not parse: diagnostics simply stay silent there.

## Requirements (honest version)

The extension does **not** bundle grounded and never installs anything
on its own. You need Python 3.10+ and the server, either:

* on PATH: `pip install grounded-lint` (or `brew install
  gonisulaimann/tap/grounded`, or `pipx install grounded-lint`), or
* via the `grounded.serverPath` setting (exact binary path), or
* via `uvx` (the extension asks first; nothing downloads silently).

Without any of these, the extension shows an error with a link to the
install guide instead of guessing.

## Install until marketplace listing

```console
npm ci && npm run compile && npx @vscode/vsce package
```

Then `Extensions → … → Install from VSIX`. See `PUBLISHING.md` for the
maintainer release flow (VS Code Marketplace + Open VSX for Cursor).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `grounded.serverPath` | `""` | Exact binary path. Empty: PATH lookup, then the uvx offer. |
| `grounded.allowUvx` | `false` | Skip the prompt and always use `uvx --from grounded-lint grounded lsp`. |
