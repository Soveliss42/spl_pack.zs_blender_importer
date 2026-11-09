"""Shared permissive value-conversion helpers used by importer modules."""
from __future__ import annotations


def numeric_from_any(value, default=None):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except Exception:
            return default
    if isinstance(value, dict):
        for key in ("Value", "value", "X", "x"):
            if key in value:
                return numeric_from_any(value.get(key), default)
    return default


def color_from_any(value, default=(1.0, 1.0, 1.0)):
    if isinstance(value, dict):
        vals=[value.get(k) for k in ("R","G","B")]
        if any(v is None for v in vals):
            vals=[value.get(k) for k in ("r","g","b")]
    elif isinstance(value, (list, tuple)):
        vals=list(value[:3])
    else:
        return tuple(default)
    if len(vals) < 3:
        return tuple(default)
    try:
        nums=[float(v) for v in vals[:3]]
    except Exception:
        return tuple(default)
    if max(nums) > 1.0:
        nums=[max(0.0,min(1.0,v/255.0)) for v in nums]
    else:
        nums=[max(0.0,min(1.0,v)) for v in nums]
    return tuple(nums)
