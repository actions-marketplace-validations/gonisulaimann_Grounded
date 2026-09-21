# Firewall demo (30 seconds)

`firewall.sh` reproduces the magic moment end to end, offline, and
asserts its own outcome:

1. A clean tree scans silent (smoke detector at rest).
2. An "agent" renames `get_user` → `get_account` in `core.py` only.
3. `grounded scan . --changed` flags the stale import in `views.py` —
   a file the agent never touched — because the diff touched the
   symbol the lie names (rename-fallout expansion).
4. `grounded impact` maps the blast radius for the real rename.

```console
./demo/firewall.sh                  # uses `grounded` on PATH
GROUNDED="python3 -m grounded.cli" ./demo/firewall.sh  # dev checkout
```

Runs in about a second (dominated by half a dozen Python startups);
the 30-second budget leaves room to read the output aloud. Exits 0
with `DEMO PASS` only if the lie is caught, 1 otherwise. Requires
`git` (the `--changed` gate refuses to guess outside a repo).

The README animation (`firewall.gif`, 74 KiB) is rendered from live
output by `render_gif.py` (stdlib + Pillow + ffmpeg), never staged:
it aborts unless the lie is caught. Regenerate with
`python3 demo/render_gif.py`; the tape (`firewall.tape`) documents the
paced steps for VHS users.
