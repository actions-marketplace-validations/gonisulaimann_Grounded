# Grounded — Guide rapide (Français)

> Traduction résumée. Le [README en anglais](https://github.com/gonisulaimann/Grounded#readme) fait référence.

**Grounded** détecte les références pendantes dans les commentaires du code : fonctions disparues, fichiers manquants, imports cassés. Déterministe, hors ligne, zéro dépendance. Python, JavaScript/TypeScript, Go et C pris en charge.

## Installation

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## Utilisation

```console
grounded scan .                    # analyse le dépôt (code 1 en cas d'erreurs)
grounded scan . --changed          # lignes modifiées uniquement (idéal en CI)
grounded fix . --dry-run           # prévisualise les corrections
grounded impact ma_fonction .      # tout ce qui touche un symbole : définitions, imports
```

## Règles

| ID | Sévérité | Vérification |
|---|---|---|
| `stale-symbol-ref` | lie | un commentaire nomme une fonction absente du dépôt |
| `stale-import` | lie | un import résoluble vers un module manquant ou un nom non défini |
| `stale-file-ref` | lie | un commentaire pointe vers un chemin inexistant du dépôt |
| `number-drift` | drift | un nombre en commentaire contredit le code adjacent |
| `fragile-anchor` | smell | ancres de ligne fragiles et marqueurs workaround sans ticket |

## Liens

- [Documentation complète](https://grounded.readthedocs.io/en/latest/) (en anglais)
- [Signaler un problème](https://github.com/gonisulaimann/Grounded/issues)
- Licence : MIT
