# Grounded — Guia rápido (Português BR)

> Tradução resumida. O [README em inglês](https://github.com/gonisulaimann/Grounded#readme) é a referência oficial.

**Grounded** encontra referências pendentes nos comentários do código: funções que não existem mais, arquivos faltantes e importações quebradas. Determinístico, offline, sem dependências. Funciona com Python, JavaScript/TypeScript, Go e C.

## Instalação

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## Uso

```console
grounded scan .                    # analisa o repo (código 1 se houver erros)
grounded scan . --changed          # só linhas modificadas (ideal para CI)
grounded fix . --dry-run           # pré-visualiza as correções
grounded impact minha_funcao .     # tudo que toca um símbolo: onde é definido, quem importa
```

## Regras

| ID | Severidade | O que verifica |
|---|---|---|
| `stale-symbol-ref` | lie | comentário nomeia função inexistente no repo |
| `stale-import` | lie | importação resolvível para módulo faltante ou nome não definido |
| `stale-file-ref` | lie | comentário aponta para caminho inexistente no repo |
| `number-drift` | drift | número em comentário contradiz o código próximo |
| `fragile-anchor` | smell | âncoras de linha frágeis e marcadores workaround sem ticket |

## Links

- [Documentação completa](https://grounded.readthedocs.io/en/latest/) (em inglês)
- [Reportar problemas](https://github.com/gonisulaimann/Grounded/issues)
- Licença: MIT
