# Grounded — Краткое руководство (Русский)

> Краткий перевод. Официальным источником является [README на английском](https://github.com/gonisulaimann/Grounded#readme).

**Grounded** находит висячие ссылки в комментариях кода: несуществующие функции, отсутствующие файлы, сломанные импорты. Детерминирован, работает офлайн, ноль зависимостей. Поддерживает Python, JavaScript/TypeScript, Go и C.

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

## Ссылки

- [Полная документация](https://grounded.readthedocs.io/en/latest/) (на английском)
- [Сообщить о проблеме](https://github.com/gonisulaimann/Grounded/issues)
- Лицензия: MIT
