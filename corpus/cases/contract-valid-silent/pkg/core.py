import os

# Default port is 3000 (override with PORT).
port = int(os.getenv("PORT", 3000))

# DEPRECATED: use get_account(uid) instead.
def get_account(uid):
    return uid

# Caller must hold _lock.
class Store:
    def __init__(self):
        self._lock = 1
