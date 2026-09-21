import os

# Default port is 8080 (override with PORT).
port = int(os.getenv("PORT", 3000))

# Caller must hold _state_lock.
class Store:
    def __init__(self):
        self._lock = 1
