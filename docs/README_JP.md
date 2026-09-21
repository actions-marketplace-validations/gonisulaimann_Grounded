# Grounded — クイックガイド（日本語）

> 要約翻訳です。[英語の README](../README.md)が正式なリファレンスです。

**Grounded** はコードコメント内の宙ぶらりん参照を検出します：存在しない関数、欠落ファイル、壊れたインポート。決定的、オフライン、依存関係ゼロ。Python、JavaScript/TypeScript、Go、C に対応。

## インストール

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## 使い方

```console
grounded scan .                    # リポジトリをスキャン（検出時は終了コード 1）
grounded scan . --changed          # 変更行のみ（CI に最適）
grounded fix . --dry-run           # 修正内容をプレビュー
grounded impact my_function .      # シンボルに関する全情報：定義・インポート元
```

## ルール

| ID | 重要度 | 内容 |
|---|---|---|
| `stale-symbol-ref` | lie | コメントが指す関数がリポジトリに存在しない |
| `stale-import` | lie | 解決可能なインポートが欠落モジュールや未定義名を指す |
| `stale-file-ref` | lie | コメントが指すパスがリポジトリに存在しない |
| `number-drift` | drift | コメント内の数値が隣接コードと矛盾する |
| `fragile-anchor` | smell | 脆弱な行番号アンカー、チケットなしの workaround マーカー |

## リンク

- [完全なドキュメント](https://grounded.readthedocs.io/en/latest/)（英語）
- [問題を報告](https://github.com/gonisulaimann/Grounded/issues)
- ライセンス：MIT
