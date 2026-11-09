from __future__ import annotations

from .actor_helpers import actor_token_variants
from .property_utils import set_props_from_mapping

_OPTIONAL_RSDB_TABLES = {}


def set_optional_rsdb_tables(tables):
    global _OPTIONAL_RSDB_TABLES
    _OPTIONAL_RSDB_TABLES = tables if isinstance(tables, dict) else {}
    return _OPTIONAL_RSDB_TABLES


def clear_optional_rsdb_tables():
    _OPTIONAL_RSDB_TABLES.clear()


def optional_rsdb(prefix):
    return (_OPTIONAL_RSDB_TABLES or {}).get(prefix, {})


def annotate_object_with_rsdb(obj, entry, prefix="rsdb"):
    """Attach available LocatorInfo/SplActorInfo metadata to an imported helper."""
    if not isinstance(entry, dict):
        return
    label_keys = []
    for key in ("Gyaml", "Name", "__RowId", "UnitConfigName", "ModelName"):
        for value in actor_token_variants(entry.get(key, "")):
            if value and value not in label_keys:
                label_keys.append(value)

    locator_table = optional_rsdb("LocatorInfo")
    splactor_table = optional_rsdb("SplActorInfo")
    locator = locator_table.get("by_rowid", {}) if isinstance(locator_table, dict) else {}
    splactor = splactor_table.get("by_rowid", {}) if isinstance(splactor_table, dict) else {}

    for key in label_keys:
        row = locator.get(key) or locator.get(str(key).casefold())
        if isinstance(row, dict):
            set_props_from_mapping(obj, prefix + "_locator", row)
            shape = str(row.get("ShapeType", "") or "").casefold()
            try:
                obj["rsdb_locator_shape_type"] = row.get("ShapeType", "")
                if shape == "sphere":
                    obj.empty_display_type = "SPHERE"
                elif shape in {"cube", "box"}:
                    obj.empty_display_type = "CUBE"
            except Exception:
                pass
            break

    for key in label_keys:
        row = splactor.get(key) or splactor.get(str(key).casefold())
        if isinstance(row, dict):
            set_props_from_mapping(obj, prefix + "_splactor", row)
            break
