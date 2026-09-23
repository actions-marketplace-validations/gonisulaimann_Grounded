# Grounded — Guia rápido (Português BR)

> Tradução resumida. O [README em inglês](https://github.com/gonisulaimann/Grounded#readme) é a referência oficial.

**Grounded** é um firewall de integridade de referências: encontra comentários, exemplos documentados, importações e strings de configuração que contradizem o repositório — funções que não existem mais, arquivos faltantes, importações quebradas, exemplos que não funcionam. Determinístico, offline, sem dependências. Funciona com Python, JavaScript/TypeScript, Go e C.

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
| `stale-entrypoint` | lie | um script de `pyproject` ou bin/main de `package.json` aponta para algo inexistente no repo |
| `stale-mock-ref` | lie | uma string de `@patch` nomeia um símbolo ausente do módulo |
| `unclosed-fence` | lie | uma cerca Markdown que o renderizador não fecha — o resto aparece como código |

Oito dessas 13 regras rodam por padrão; as outras cinco são opt-in
(`--enable <id>`).

## Links

- [Documentação completa](https://grounded.readthedocs.io/en/latest/) (em inglês)
- [Reportar problemas](https://github.com/gonisulaimann/Grounded/issues)
- Licença: MIT
