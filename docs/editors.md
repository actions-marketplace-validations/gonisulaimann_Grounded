# Editor setup (VS Code / Cursor)

A thin LSP client lives in [`editors/vscode`](https://github.com/gonisulaimann/Grounded/tree/main/editors/vscode).
It shows grounded diagnostics inline (lie as error, drift as warning,
smell as information) plus the server's quickfix actions, for Python,
JavaScript/TypeScript (incl. React), Go, and C.

## Install (until marketplace listing)

The extension is not published yet: no Marketplace or Open VSX links
exist. Until then, build and install from source:

```console
cd editors/vscode
npm ci && npm run compile && npx @vscode/vsce package
```

Then `Extensions → … → Install from VSIX` (works in VS Code and Cursor).

## Requirements (honest version)

The extension never bundles, downloads, or installs grounded on its
own. It resolves the server in this order:

1. `grounded.serverPath` setting (exact binary path).
2. `grounded` on PATH (`pip install grounded-lint`, `brew install
   gonisulaimann/tap/grounded`, or `pipx install grounded-lint`;
   needs Python 3.10+).
3. `uvx --from grounded-lint grounded lsp`, only if you accept the
   prompt (or set `grounded.allowUvx`). First use downloads the
   package; nothing is fetched silently.
4. Otherwise an error with a link to the install guide.

After installing the server, run `Grounded: Restart Server` from the
command palette.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `grounded.serverPath` | `""` | Exact binary path. Empty: PATH lookup, then the uvx offer. |
| `grounded.allowUvx` | `false` | Skip the prompt and always use uvx. |

## Other editors

Any editor with a generic LSP client (Neovim, Zed, Emacs eglot, Helix)
can run `grounded lsp` over stdio; see [Agents](agents.md#lsp-server)
for a Neovim snippet. A files-based setup (no extension at all) is
`grounded init-agent`.
