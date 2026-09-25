# Grounded — Краткое руководство (Русский)

> Краткий перевод. Официальным источником является [README на английском](https://github.com/gonisulaimann/Grounded#readme).

**Grounded** — межсетевой экран целостности ссылок: он находит комментарии, примеры в документации, импорты и строки конфигурации, противоречащие репозиторию — несуществующие функции, отсутствующие файлы, сломанные импорты, устаревшие примеры. Детерминирован, работает офлайн, ноль зависимостей. Поддерживает Python, JavaScript/TypeScript, Go и C.

## Установка

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## Использование

```console
grounded scan .                    # проверка репозитория (код 1 при находках)
grounded scan . --changed          # только изменённые строки (для CI)
grounded fix . --dry-run           # предпросмотр исправлений
grounded impact my_function .      # всё о символе: определения, импорты
```

## Правила

| ID | Серьёзность | Проверка |
|---|---|---|
| `stale-symbol-ref` | lie | комментарий называет функцию, которой нет в репозитории |
| `stale-import` | lie | разрешимый импорт ведёт к отсутствующему модулю или имени |
| `stale-file-ref` | lie | комментарий указывает на несуществующий путь в репозитории |
| `number-drift` | drift | число в комментарии противоречит соседнему коду |
| `fragile-anchor` | smell | хрупкие якоря строк и маркеры workaround без тикета |
| `stale-entrypoint` | lie | скрипт `pyproject` либо bin/main `package.json` указывает на отсутствующее в репозитории |
| `stale-mock-ref` | lie | строка `@patch` называет символ, которого нет в модуле |
| `unclosed-fence` | lie | Markdown-ограждение, которое рендерер не закрывает — далее текст отображается как код |

Восемь из этих 13 правил включены по умолчанию; остальные пять — opt-in
(`--enable <id>`).

## Ссылки

- [Полная документация](https://grounded.readthedocs.io/en/latest/) (на английском)
- [Сообщить о проблеме](https://github.com/gonisulaimann/Grounded/issues)
- Лицензия: MIT
