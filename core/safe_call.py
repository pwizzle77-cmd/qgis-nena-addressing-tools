# -*- coding: utf-8 -*-
"""Call something that may fail on odd data (a bad geometry, a provider without an API) and fall back."""


def attempt(func, *args, default=None):
    """``func(*args)``, or ``default`` when it raises."""
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 - the caller decides what a failure means
        return default


def succeeds(func, *args) -> bool:
    """True when ``func(*args)`` runs without raising."""
    try:
        func(*args)
    except Exception:  # noqa: BLE001 - the caller decides what a failure means
        return False
    return True
