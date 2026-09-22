# Grounded — Guía rápida (Español)

> Traducción resumida. El [README en inglés](https://github.com/gonisulaimann/Grounded#readme) es la referencia oficial.

**Grounded** es un cortafuegos de integridad de referencias: encuentra comentarios, ejemplos documentados, importaciones y cadenas de configuración que contradicen el repositorio — funciones que ya no existen, archivos faltantes, importaciones rotas, ejemplos que ya no funcionan. Determinista, sin conexión, sin dependencias. Funciona con Python, JavaScript/TypeScript, Go y C.

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
| `stale-entrypoint` | lie | un script de `pyproject` o un bin/main de `package.json` apunta a algo inexistente en el repo |
| `stale-mock-ref` | lie | un string de `@patch` nombra un símbolo ausente del módulo |
| `unclosed-fence` | lie | una valla Markdown que el renderizador no cierra — lo que sigue se muestra como código |

Ocho de estas 13 reglas corren por defecto; las otras cinco son opt-in
(`--enable <id>`).

## Enlaces

- [Documentación completa](https://grounded.readthedocs.io/en/latest/) (en inglés)
- [Reportar problemas](https://github.com/gonisulaimann/Grounded/issues)
- Licencia: MIT
