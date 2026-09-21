# Grounded — Guía rápida (Español)

> Traducción resumida. El [README en inglés](https://github.com/gonisulaimann/Grounded#readme) es la referencia oficial.

**Grounded** encuentra referencias colgadas en los comentarios del código: funciones que ya no existen, archivos faltantes e importaciones rotas. Determinista, sin conexión, sin dependencias. Funciona con Python, JavaScript/TypeScript, Go y C.

## Instalación

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## Uso

```console
grounded scan .                    # analiza el repo (código 1 si hay errores)
grounded scan . --changed          # solo líneas modificadas (ideal para CI)
grounded fix . --dry-run           # vista previa de las correcciones
grounded impact mi_funcion .       # todo lo que toca un símbolo: dónde se define, quién lo importa
```

## Reglas

| ID | Severidad | Qué verifica |
|---|---|---|
| `stale-symbol-ref` | lie | un comentario nombra una función que no existe en el repo |
| `stale-import` | lie | una importación resoluble a un módulo faltante o un nombre no definido |
| `stale-file-ref` | lie | un comentario apunta a una ruta inexistente del repo |
| `number-drift` | drift | un número en un comentario contradice el código cercano |
| `fragile-anchor` | smell | anclas de línea frágiles y marcadores workaround sin ticket |

## Enlaces

- [Documentación completa](https://grounded.readthedocs.io/en/latest/) (en inglés)
- [Reportar problemas](https://github.com/gonisulaimann/Grounded/issues)
- Licencia: MIT
