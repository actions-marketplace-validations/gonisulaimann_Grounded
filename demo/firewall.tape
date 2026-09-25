# Paced recording of demo/firewall.sh's exact steps (same commands, pauses
# added so a viewer can read). $G is pinned grounded 0.14.0 via uvx.
# Render: vhs demo/firewall.tape
Output demo/firewall.gif
Set Shell bash
Set Width 980
Set Height 470
Set FontSize 15
Set Padding 10
Hide
Type "D=$(mktemp -d) && cd $D && git init -q . && git config user.email demo@grounded && git config user.name demo && G='uvx --from grounded-lint==0.14.0 grounded' && mkdir pkg && printf 'def get_user(uid):\n    return uid\n' > pkg/core.py"
Enter
Show
Sleep 1s
Type "printf 'from .core import get_user\n\n\ndef show(uid):\n    return get_user(uid)\n' > pkg/views.py && touch pkg/__init__.py && git add -A && git commit -qm baseline && clear"
Enter
Sleep 1s
Type "$G scan . --no-color --quiet"
Enter
Sleep 2s
Type "# agent renames get_user -> get_account in core.py only"
Enter
Sleep 1s
Type "printf 'def get_account(uid):\n    return uid\n' > pkg/core.py"
Enter
Sleep 1s
Type "$G scan . --changed --no-color"
Enter
Sleep 5s
Type "$G impact get_user ."
Enter
Sleep 5s
