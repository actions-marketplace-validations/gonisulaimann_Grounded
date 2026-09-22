"""Views."""
from .core import get_user


def show(uid):
    return get_user(uid)
