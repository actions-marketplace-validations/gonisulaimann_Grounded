"""Demo service (grounded v2 reference-rot fixtures)."""
import os

TIMEOUT = 60


def connect():
    """Open the channel (contract checks belong to darglint/pydoclint)."""
    # Calls `ghost_service()` to open the channel.
    # See src/legacy/gone.py for the old handshake.
    # See src/real_module.py for current storage.
    # Compatible with requests/models.py wire format.
    # timeout is 30s for dialing.
    # See line 42 for the retry policy.
    # Joins paths with `os.path.join()`.
    return {"ok": True}


def ping():
    # Both ``__get_item__`` and get call this helper.
    return True
