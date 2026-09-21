# Grounded — Kurzanleitung (Deutsch)

> Gekürzte Übersetzung. Die [englische README](../README.md) ist maßgeblich.

**Grounded** findet baumelnde Referenzen in Code-Kommentaren: Funktionen, die nicht mehr existieren, fehlende Dateien, defekte Imports. Deterministisch, offline, keine Abhängigkeiten. Unterstützt Python, JavaScript/TypeScript, Go und C.

## Installation

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## Verwendung

```console
grounded scan .                    # scannt das Repo (Exit-Code 1 bei Fehlern)
grounded scan . --changed          # nur geänderte Zeilen (ideal für CI)
grounded fix . --dry-run           # zeigt Korrekturen vorab an
grounded impact meine_funktion .   # alles zu einem Symbol: Definitionen, Importeure
```

## Regeln

| ID | Schweregrad | Prüfung |
|---|---|---|
| `stale-symbol-ref` | lie | Kommentar nennt eine Funktion, die es im Repo nicht gibt |
| `stale-import` | lie | auflösbarer Import auf fehlendes Modul oder undefinierten Namen |
| `stale-file-ref` | lie | Kommentar verweist auf nicht existierenden Pfad im Repo |
| `number-drift` | drift | Zahl im Kommentar widerspricht dem umgebenden Code |
| `fragile-anchor` | smell | fragile Zeilenanker und Workaround-Marker ohne Ticket |

## Links

- [Vollständige Dokumentation](https://grounded.readthedocs.io/en/latest/) (auf Englisch)
- [Fehler melden](https://github.com/gonisulaimann/Grounded/issues)
- Lizenz: MIT
