"""Transform conversion helpers.

Keeping transform logic centralized prevents BCETT actor transforms, helper
objects, graffiti/decal planes, and pack scene components from silently using
slightly different axis conversions.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence, Tuple

from mathutils import Euler, Matrix, Vector


def raw_rotation_tuple(data, default=0.0) -> Tuple[float, float, float]:
    if isinstance(data, Mapping):
        return tuple(float(data.get(k, default) or default) for k in ("X", "Y", "Z"))
    if isinstance(data, (list, tuple)) and len(data) >= 3:
        return (float(data[0]), float(data[1]), float(data[2]))
    return (float(default), float(default), float(default))


def bcett_rotation_from_entry(entry) -> Euler:
    raw = raw_rotation_tuple((entry or {}).get("Rotate", {}) if isinstance(entry, Mapping) else {}, 0.0)
    try:
        game_matrix = Euler(raw, "XYZ").to_matrix()
        blender_matrix = Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0))) @ game_matrix @ Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))
        return blender_matrix.to_euler("XYZ")
    except Exception:
        return Euler(tuple(value * math.pi / 180 for value in raw), "XYZ")


def raw_or_degrees_rotation_from_entry(entry) -> Euler:
    raw = raw_rotation_tuple((entry or {}).get("Rotate", {}) if isinstance(entry, Mapping) else {}, 0.0)
    return Euler(tuple(value * math.pi / 180 for value in raw), "XYZ")


def vector_from_any(value, default=0.0, splatoon_axis_order=True) -> Vector:
    if isinstance(value, Mapping):
        x = float(value.get("X", default) or default)
        y = float(value.get("Y", default) or default)
        z = float(value.get("Z", default) or default)
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        x, y, z = (float(value[0]), float(value[1]), float(value[2]))
    else:
        x = y = z = float(default)
    return Vector((x, -z, y)) if splatoon_axis_order else Vector((x, y, z))


def to_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return float(default)


def vector_tuple_from_any(data, default=0.0, splatoon_axis_order=False):
    """Compatibility tuple form used by legacy importer call sites."""
    if isinstance(data, (list, tuple)):
        values=list(data[:3])+[default,default,default]
        x=to_float(values[0], default); second=to_float(values[1], default); third=to_float(values[2], default)
        return (x, -third, second) if splatoon_axis_order else (x, second, third)
    if isinstance(data, Mapping):
        return (to_float(data.get("X",default),default), to_float(data.get("Y",default),default), to_float(data.get("Z",default),default))
    return (float(default), float(default), float(default))
