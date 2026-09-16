"""Healthy module: references here must stay silent."""
import json

STORE = {}


def __getitem__(key):
    return STORE[key]


def load(path):
    # Uses `json.loads()` to parse.
    data = json.loads("{}")
    return helper(data)


def helper(data):
    # `STORE` holds the cache.
    return STORE.get(data)
