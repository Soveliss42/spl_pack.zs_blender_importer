"""Shared Blender collection helpers used by geometry and non-geometry importers."""
from __future__ import annotations

import bpy

_COLLECTION_CACHE = {}


def safe_collection_name(value, fallback="NoLayer"):
    name = str(value if value not in (None, "") else fallback).strip() or fallback
    return name.replace("/", "_").replace("\\", "_")


def collection_cache_key(parent, name):
    try:
        parent_key = parent.as_pointer()
    except Exception:
        parent_key = id(parent)
    return (parent_key, safe_collection_name(name))


def get_or_create_child_collection(parent, name):
    logical_name = safe_collection_name(name)
    cache_key = collection_cache_key(parent, logical_name)
    cached = _COLLECTION_CACHE.get(cache_key)
    if cached is not None:
        try:
            if cached.name in parent.children:
                return cached
        except Exception:
            pass
    existing = parent.children.get(logical_name)
    if existing:
        try:
            existing["osi_logical_collection_name"] = logical_name
        except Exception:
            pass
        _COLLECTION_CACHE[cache_key] = existing
        return existing
    for child in parent.children:
        try:
            if child.get("osi_logical_collection_name") == logical_name:
                _COLLECTION_CACHE[cache_key] = child
                return child
        except Exception:
            pass
    col = bpy.data.collections.new(logical_name)
    try:
        col["osi_logical_collection_name"] = logical_name
    except Exception:
        pass
    parent.children.link(col)
    _COLLECTION_CACHE[cache_key] = col
    return col


def move_objects_to_collection(objects, target_collection):
    for obj in objects:
        if target_collection.objects.get(obj.name) is None:
            try:
                target_collection.objects.link(obj)
            except RuntimeError:
                pass
        for col in list(obj.users_collection):
            if col != target_collection:
                try:
                    col.objects.unlink(obj)
                except RuntimeError:
                    pass


def clear_collection_cache():
    count = len(_COLLECTION_CACHE)
    _COLLECTION_CACHE.clear()
    return count
