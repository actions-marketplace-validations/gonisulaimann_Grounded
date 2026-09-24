import sys

import json

# Backwards-compatible alias: pkg.packages.json.* is json.*
for mod in list(sys.modules):
    if mod == "json" or mod.startswith("json."):
        sys.modules[f"pkg.packages.{mod}"] = sys.modules[mod]
