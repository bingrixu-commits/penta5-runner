"""Structural copy for JSON-shaped evidence payloads.

copy.deepcopy is generic: it dispatches on every object, keeps a memo dict and
re-checks __deepcopy__ hooks. The pro4 refresh deep-copies the same 50-row
history thousands of times per cycle, so that overhead dominated the profile.
This module copies dict/list/tuple recursively and returns immutable leaves
as-is, falling back to copy.deepcopy for anything else so behaviour for exotic
payloads is unchanged.
"""
from copy import deepcopy
from datetime import date, datetime

_IMMUTABLE = (str, int, float, bool, bytes, type(None), datetime, date)


def json_copy(value):
    """Deep copy of dict/list/tuple trees with immutable leaves shared."""
    kind = type(value)
    if kind is dict:
        return {key: json_copy(item) for key, item in value.items()}
    if kind is list:
        return [json_copy(item) for item in value]
    if kind is tuple:
        return tuple(json_copy(item) for item in value)
    if isinstance(value, _IMMUTABLE):
        return value
    if isinstance(value, dict):
        return {key: json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        clone = [json_copy(item) for item in value]
        return tuple(clone) if isinstance(value, tuple) else clone
    return deepcopy(value)
