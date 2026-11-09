"""Add-on lifecycle and cache cleanup helpers.

These helpers are intentionally conservative. They do not delete user-visible
Blender Image or Material datablocks. They only clear Python/module caches that
can outlive an add-on reload and keep stale StructRNA references.
"""
from __future__ import annotations

import sys
from typing import Callable, Iterable, Optional


def _package_root() -> str:
    return __name__.rsplit('.', 1)[0]


def _vendor_prefix() -> str:
    return _package_root() + '._bfres_vendor'


def _safe_clear_mapping(obj) -> bool:
    try:
        if hasattr(obj, 'clear'):
            obj.clear()
            return True
    except Exception:
        return False
    return False


def clear_runtime_caches(log: Optional[Callable[[str], None]] = None) -> int:
    """Clear private Python caches that may contain stale Blender datablocks.

    Returns the number of cache containers that were cleared. This is safe to
    call before/after every import and during register/unregister.
    """
    cleared = 0
    root = _package_root()
    prefixes = (root, _vendor_prefix())
    cache_attr_names = {
        '_OSI_BNTX_TEXTURE_CACHE',
        '_OSI_MATERIAL_CACHE',
        '_BFRES_FMDL_NAMES_CACHE',
        '_ACTOR_INFO_CACHE',
        '_RSDB_TABLE_CACHE',
        '_OPTIONAL_RSDB_TABLES',
        'stringcache',
    }
    for mod_name, module in list(sys.modules.items()):
        if not module or not any(mod_name == p or mod_name.startswith(p + '.') for p in prefixes):
            continue
        for attr_name in cache_attr_names:
            if hasattr(module, attr_name):
                if _safe_clear_mapping(getattr(module, attr_name)):
                    cleared += 1
                    if log:
                        log("CACHE_CLEARED module=%r attr=%r" % (mod_name, attr_name))
        # Clear obviously named private cache dicts/lists without touching class
        # constants or imported modules.
        for attr_name, value in list(getattr(module, '__dict__', {}).items()):
            if attr_name in cache_attr_names:
                continue
            lowered = attr_name.lower()
            if ('cache' in lowered or 'cached' in lowered) and attr_name.startswith('_'):
                if isinstance(value, (dict, list, set)) and _safe_clear_mapping(value):
                    cleared += 1
                    if log:
                        log("CACHE_CLEARED module=%r attr=%r" % (mod_name, attr_name))
    return cleared


def purge_vendor_modules(log: Optional[Callable[[str], None]] = None) -> int:
    """Remove vendored BFRES/BNTX modules from sys.modules.

    This is used around add-on reloads so Blender does not keep old class/function
    objects or module globals from a previous installed zip. It is not intended to
    delete Blender datablocks.
    """
    prefix = _vendor_prefix()
    names = [name for name in list(sys.modules) if name == prefix or name.startswith(prefix + '.')]
    for name in sorted(names, key=len, reverse=True):
        try:
            del sys.modules[name]
            if log:
                log("MODULE_PURGED module=%r" % name)
        except Exception:
            pass
    return len(names)
