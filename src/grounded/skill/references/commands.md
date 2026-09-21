# Commands reference

Condensed CLI surface for agents. Flags not listed here do not exist;
do not invent invocations.

```console
grounded scan [PATH] [--format terminal|json|sarif|html] [--output FILE]
              [--fail-on lie|drift|smell|never]
              [--enable ID,...] [--disable ID,...]
              [--baseline FILE] [--show-baselined]
              [--changed [BASE]] [--cache [FILE]] [--jobs N]
              [--config FILE] [--no-color] [--quiet]
grounded baseline [PATH] [--output FILE]
grounded fix [PATH] [--dry-run]
grounded impact SYMBOL [PATH] [--format terminal|json]
grounded list [PATH]
grounded explain [CHECKER]
grounded init [--force]
grounded init-agent [--claude|--cursor|--aider] [--skill] [--skill-project]
               [--force] [--dry-run]
grounded mcp [--root .]
grounded lsp
```

Contracts:

* `scan <file>` checks one file, exit 1 on findings, 0 when clean.
* `scan` never writes. Only `fix` (without `--dry-run`) writes, and only
  the lines of unambiguous findings.
* `impact` never writes. `mcp` and `lsp` never write.
* Machine output: `--format json` (scripts), `--format sarif` (code
  scanning). Terminal output is for humans; parse JSON instead.
* `init-agent --skill` installs this skill to `~/.claude/skills/grounded`
  (all projects); `--skill-project` installs to `.claude/skills/grounded`
  (this repo only). Bare `init-agent` never writes outside the repo.
