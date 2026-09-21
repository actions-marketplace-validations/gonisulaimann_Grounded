# Publishing the extension (maintainer)

Both listings need publisher accounts, so publishing is manual, not CI.
No marketplace links or badges go anywhere until the listings are live.

## 1. Version

Bump `version` in `editors/vscode/package.json` together with the
PyPI release (the extension carries no code dependency on the server
version, but matching numbers avoid confusion).

## 2. Package

```console
cd editors/vscode
npm ci
npm run compile
npx @vscode/vsce package   # produces grounded-<version>.vsix
```

Install the `.vsix` locally first (`Install from VSIX`) and confirm
diagnostics appear on a file with a known finding before publishing.

Also before first publish: add a 128x128 `icon.png` and
`"icon": "icon.png"` in `package.json`. Listings without an icon look
abandoned; `vsce` only warns, so this is easy to forget.

## 3. VS Code Marketplace

One-time: `npx @vscode/vsce create-publisher gonisulaimann`
(needs a Microsoft account + Personal Access Token, see
`https://code.visualstudio.com/api/working-with-extensions/publishing-extension`).

```console
npx @vscode/vsce publish --pat <token>
```

## 4. Open VSX (Cursor, Windsurf, VSCodium)

One-time: account at `https://open-vsx.org`, namespace claim for
`gonisulaimann`, token from the profile page
(`https://github.com/eclipse/openvsx/wiki/Publishing-Extensions`).

```console
npx ovsx publish grounded-<version>.vsix --pat <token>
```

(`npx -p ovsx ovsx publish …` fetches the CLI on demand; or
`npm i -g ovsx`.)
