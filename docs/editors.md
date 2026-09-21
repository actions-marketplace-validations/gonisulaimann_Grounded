# Editor setup (VS Code / Cursor)

A thin LSP client lives in [`editors/vscode`](https://github.com/gonisulaimann/Grounded/tree/main/editors/vscode)
and is published on [Open VSX](https://open-vsx.org/extension/gonisulaimann/grounded)
(Cursor, Windsurf, VSCodium: install from the Extensions tab).
It shows grounded diagnostics inline (lie as error, drift as warning,
smell as information) plus the server's quickfix actions, for Python,
JavaScript/TypeScript (incl. React), Go, and C.

## Install

* **Cursor / Windsurf / VSCodium**: search `Grounded` in Extensions.
* **VS Code**: the Marketplace listing is pending; until then build
  from source:

```console
cd editors/vscode
npm ci && npm run compile && npx @vscode/vsce package
```

Then `Extensions → … → Install from VSIX`.

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
