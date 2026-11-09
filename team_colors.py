"""TeamColorDataSet UI and test-material helpers.

This module intentionally keeps the first TeamColorDataSet implementation small:
it loads RomFS/RSDB/TeamColorDataSet.Product.b10.rstbl.byml.zs, exposes a
Blender scene selector, and drives four visible test cubes from the selected
Neutral/Alpha/Bravo/Charlie colors.  Imported stage objects can link materials into the same cache so team-colored
materials update without requiring a re-import.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import bpy
from mathutils import Vector, Matrix

from . import byml

try:
    import zstandard as _osi_zstandard
except Exception:  # Blender without the optional zstandard module.
    _osi_zstandard = None

_TEAM_COLOR_CACHE: Dict[str, Any] = {
    "records": [],
    "items": [],
    "by_id": {},
    "source": "",
    "error": "",
}
_LOADING = False
_COLOR_FIELDS = (
    ("neutral", "NeutralColor", "Neutral"),
    ("alpha", "AlphaTeamColor", "Alpha"),
    ("bravo", "BravoTeamColor", "Bravo"),
    ("charlie", "CharlieTeamColor", "Charlie"),
)
_TEST_CUBE_NAMES = {
    "neutral": "Spl3 TeamColor Test - Neutral",
    "alpha": "Spl3 TeamColor Test - Alpha",
    "bravo": "Spl3 TeamColor Test - Bravo",
    "charlie": "Spl3 TeamColor Test - Charlie",
}


# Conservative whitelist for objects/materials that should currently respond to
# TeamColorDataSet.  Many ordinary stage Hoian_UBER materials expose generic
# team-color shader parameters, so params alone are intentionally ignored.
_RECOLOR_NAME_PATTERNS = (
    "bunker", "sponge", "gachihokogoal", "gachihokocheckpoint",
    "gachiasarigoal", "checkpointshachi", "obj_checkpointshachi", "clamgreat", "obj_clamgreat", "gachisignboad",
    "gachisignboard", "m_body",
)
_DYNAMIC_RECOLOR_PATTERNS = (
    "inkrail", "obj_inkrail__m_paint", "inkrailsmall",
    "gachiyagurarail", "painttargetarea", "painttargetarea_cube",
)
_TEAM_MASK_TEXTURE_TOKENS = ("_tcl", "tcl", "_2cl", "2cl")
_IGNORE_TEAM_TEXTURE_TOKENS = ("paintdummy", "paint_dummy")
_DEFAULT_TEAM_COLOR_NAME_TOKEN = "yellowpurple"
_DEFAULT_TEAM_COLOR_TAG_TOKEN = "versusregular"



def _addon_preferences():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except Exception:
        return None


def _romfs_root() -> Optional[Path]:
    prefs = _addon_preferences()
    value = getattr(prefs, "romfs_root_path", "") if prefs is not None else ""
    if not value:
        return None
    try:
        path = Path(bpy.path.abspath(value))
    except Exception:
        path = Path(value)
    return path if path.exists() else None


def _iter_table_candidates() -> Iterable[Path]:
    root = _romfs_root()
    if not root:
        return []
    rsdb = root / "RSDB"
    if not rsdb.is_dir():
        return []
    suffixes = (
        ".Product.b10.rstbl.byml.zs",
        ".Product.b10.rstbl.byml",
        ".Product.rstbl.byml.zs",
        ".Product.rstbl.byml",
    )
    out: List[Path] = []
    seen = set()
    for suffix in suffixes:
        for path in rsdb.glob("TeamColorDataSet*" + suffix):
            if path.name.startswith("._"):
                continue
            key = str(path.resolve()) if path.exists() else str(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
    return out


def _read_byml_or_zs(path: Path) -> bytes:
    data = path.read_bytes()
    lower = path.name.casefold()
    if lower.endswith(".zs") or data.startswith(b"\x28\xb5\x2f\xfd"):
        if _osi_zstandard is None:
            raise RuntimeError("zstandard is not available, so compressed TeamColorDataSet .zs files cannot be read")
        try:
            data = _osi_zstandard.ZstdDecompressor().decompress(data)
        except Exception:
            with _osi_zstandard.ZstdDecompressor().stream_reader(__import__("io").BytesIO(data)) as reader:
                chunks = []
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                data = b"".join(chunks)
    if data[:2] not in (b"BY", b"YB"):
        raise RuntimeError("decoded file is not BYML")
    return data


def _rows_from_root(root: Any) -> List[Dict[str, Any]]:
    if isinstance(root, list):
        return [r for r in root if isinstance(r, dict)]
    if isinstance(root, dict):
        for key in ("Rows", "rows", "Data", "data", "Values", "values"):
            value = root.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        values = list(root.values())
        if values and all(isinstance(v, dict) for v in values):
            rows = []
            for key, row in root.items():
                row = dict(row)
                row.setdefault("__RowId", key)
                rows.append(row)
            return rows
    return []


def _rowid(row: Dict[str, Any]) -> str:
    for key in ("__RowId", "RowId", "rowid", "RowID", "Name", "name"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _display_name_from_rowid(rowid: str) -> str:
    text = str(rowid or "")
    text = text.replace("\\", "/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    for suffix in (
        ".game__gfx__parameter__teamcolordataset.gyml",
        ".game__gfx__parameter__TeamColorDataSet.gyml",
        ".gyml",
        ".bgyml",
        ".byml",
    ):
        if text.casefold().endswith(suffix.casefold()):
            text = text[: -len(suffix)]
            break
    return text or rowid or "TeamColorDataSet"


def _flatten_tags(value: Any) -> List[str]:
    out: List[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_flatten_tags(item))
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(_flatten_tags(item))
    return [str(v) for v in out if str(v)]


def _tags(row: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for key in ("Tag", "Tags", "tag", "tags"):
        if key in row:
            tags.extend(_flatten_tags(row.get(key)))
    return sorted(set(tags), key=lambda s: s.casefold())


def _find_case_insensitive(row: Dict[str, Any], wanted: str) -> Any:
    if wanted in row:
        return row[wanted]
    wanted_fold = wanted.casefold()
    for key, value in row.items():
        if str(key).casefold() == wanted_fold:
            return value
    return None


def _coerce_float_channel(value: Any, default: float = 1.0) -> float:
    try:
        f = float(value)
    except Exception:
        return default
    # Some game data stores bytes, most stores 0..1 floats.  Accept either.
    if f > 1.0:
        f /= 255.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _color_tuple(value: Any, fallback: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    if isinstance(value, dict):
        def channel(*names, default=1.0):
            for name in names:
                for key, val in value.items():
                    if str(key).casefold() == name.casefold():
                        return _coerce_float_channel(val, default)
            return default
        return (
            channel("R", "Red", "X", "r", default=fallback[0]),
            channel("G", "Green", "Y", "g", default=fallback[1]),
            channel("B", "Blue", "Z", "b", default=fallback[2]),
            channel("A", "Alpha", "W", "a", default=fallback[3]),
        )
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        alpha = value[3] if len(value) >= 4 else fallback[3]
        return (
            _coerce_float_channel(value[0], fallback[0]),
            _coerce_float_channel(value[1], fallback[1]),
            _coerce_float_channel(value[2], fallback[2]),
            _coerce_float_channel(alpha, fallback[3]),
        )
    if isinstance(value, str):
        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)]
        if len(nums) >= 3:
            alpha = nums[3] if len(nums) >= 4 else fallback[3]
            return (_coerce_float_channel(nums[0], fallback[0]), _coerce_float_channel(nums[1], fallback[1]), _coerce_float_channel(nums[2], fallback[2]), _coerce_float_channel(alpha, fallback[3]))
    return fallback


def _extract_color(row: Dict[str, Any], key: str, fallback: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    value = _find_case_insensitive(row, key)
    if value is not None:
        return _color_tuple(value, fallback)
    # Some rows may nest a dictionary of color fields.
    for container_key in ("Color", "Colors", "Value", "Values"):
        nested = _find_case_insensitive(row, container_key)
        if isinstance(nested, dict):
            value = _find_case_insensitive(nested, key)
            if value is not None:
                return _color_tuple(value, fallback)
    return fallback


def _make_record(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    rid = _rowid(row)
    name = _display_name_from_rowid(rid)
    tags = _tags(row)
    fallbacks = {
        "neutral": (0.45, 0.45, 0.45, 1.0),
        "alpha": (1.0, 0.35, 0.15, 1.0),
        "bravo": (0.1, 0.45, 1.0, 1.0),
        "charlie": (0.2, 0.9, 0.35, 1.0),
    }
    colors = {short: _extract_color(row, field, fallbacks[short]) for short, field, _label in _COLOR_FIELDS}
    return {
        "id": "TC_%04d" % index,
        "rowid": rid,
        "name": name,
        "tags": tags,
        "tag_label": ", ".join(tags) if tags else "untagged",
        "colors": colors,
    }


def load_team_color_records(force: bool = False) -> List[Dict[str, Any]]:
    global _LOADING
    if _TEAM_COLOR_CACHE.get("records") and not force:
        return list(_TEAM_COLOR_CACHE["records"])
    if _LOADING:
        return list(_TEAM_COLOR_CACHE.get("records") or [])
    _LOADING = True
    try:
        _TEAM_COLOR_CACHE.update({"records": [], "items": [], "by_id": {}, "source": "", "error": ""})
        candidates = list(_iter_table_candidates())
        if not candidates:
            _TEAM_COLOR_CACHE["error"] = "TeamColorDataSet table not found; set the Splatoon 3 RomFS root in add-on preferences."
            return []
        errors = []
        rows: List[Dict[str, Any]] = []
        source = ""
        for path in candidates:
            try:
                root = byml.Byml(_read_byml_or_zs(path)).parse()
                rows = _rows_from_root(root)
                source = str(path)
                if rows:
                    break
            except Exception as exc:
                errors.append("%s: %s" % (path, exc))
        if not rows:
            _TEAM_COLOR_CACHE["error"] = "Could not load TeamColorDataSet rows: " + "; ".join(errors)
            return []
        records = [_make_record(row, i) for i, row in enumerate(rows)]
        records.sort(key=lambda rec: (rec.get("tag_label", "").casefold(), rec.get("name", "").casefold()))
        # Reassign stable sorted identifiers for display order.
        for i, rec in enumerate(records):
            rec["id"] = "TC_%04d" % i
        items = []
        by_id = {}
        for rec in records:
            by_id[rec["id"]] = rec
            display = "%s  |  %s" % (rec["tag_label"], rec["name"])
            desc = rec.get("rowid", "")
            items.append((rec["id"], display, desc))
        _TEAM_COLOR_CACHE.update({"records": records, "items": items, "by_id": by_id, "source": source, "error": ""})
        return list(records)
    finally:
        _LOADING = False


def _default_team_color_record(records: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    records = records if records is not None else list(_TEAM_COLOR_CACHE.get("records") or [])
    fallback = None
    for rec in records:
        name = str(rec.get("name", "")).casefold().replace("_", "").replace("-", "").replace(" ", "")
        rowid = str(rec.get("rowid", "")).casefold().replace("_", "").replace("-", "").replace(" ", "")
        tags = " ".join(str(t) for t in rec.get("tags", [])).casefold().replace("_", "").replace("-", "").replace(" ", "")
        if _DEFAULT_TEAM_COLOR_TAG_TOKEN in tags and _DEFAULT_TEAM_COLOR_NAME_TOKEN in (name + " " + rowid):
            return rec
        if _DEFAULT_TEAM_COLOR_NAME_TOKEN in (name + " " + rowid):
            fallback = fallback or rec
    return fallback or (records[0] if records else None)


def _ensure_default_team_color_selection(scene=None) -> None:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return
    try:
        selected = getattr(scene, "spl_team_color_set", getattr(scene, "osi_team_color_set", ""))
    except Exception:
        selected = ""
    by_id = _TEAM_COLOR_CACHE.get("by_id") or {}
    if selected and selected != "__NONE__" and selected in by_id:
        return
    rec = _default_team_color_record()
    if not rec:
        return
    try:
        if hasattr(scene, "spl_team_color_set"):
            scene.spl_team_color_set = rec["id"]
        if hasattr(scene, "osi_team_color_set"):
            scene.osi_team_color_set = rec["id"]
    except Exception:
        pass


def _enum_items(self, context):
    try:
        load_team_color_records(False)
    except Exception as exc:
        _TEAM_COLOR_CACHE["error"] = str(exc)
    items = list(_TEAM_COLOR_CACHE.get("items") or [])
    if not items:
        return [("__NONE__", "No TeamColorDataSet rows loaded", _TEAM_COLOR_CACHE.get("error", ""))]
    return items


def _selected_record(scene=None) -> Optional[Dict[str, Any]]:
    scene = scene or bpy.context.scene
    load_team_color_records(False)
    _ensure_default_team_color_selection(scene)
    selected = getattr(scene, "spl_team_color_set", getattr(scene, "osi_team_color_set", ""))
    return (_TEAM_COLOR_CACHE.get("by_id") or {}).get(selected)


def _ensure_material(name: str, color: Tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    try:
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
        if bsdf is not None:
            if "Base Color" in bsdf.inputs:
                bsdf.inputs["Base Color"].default_value = color
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = color[3]
        mat.blend_method = "BLEND" if color[3] < 1.0 else "OPAQUE"
        mat.use_screen_refraction = False
    except Exception:
        pass
    try:
        mat["spl_team_color_managed"] = True
        mat["osi_team_color_managed"] = True
    except Exception:
        pass
    return mat




def normalize_team_role(value: Any) -> str:
    """Return one of neutral/alpha/bravo/charlie for BCETT TeamCmp values."""
    if value is None:
        return "neutral"
    text = str(value).strip()
    if not text:
        return "neutral"
    folded = text.casefold()
    aliases = {
        "none": "neutral",
        "neutral": "neutral",
        "teamneutral": "neutral",
        "0": "neutral",
        "alpha": "alpha",
        "alphateam": "alpha",
        "teamalpha": "alpha",
        "a": "alpha",
        "1": "alpha",
        "bravo": "bravo",
        "bravoteam": "bravo",
        "teambravo": "bravo",
        "b": "bravo",
        "2": "bravo",
        "charlie": "charlie",
        "charlieteam": "charlie",
        "teamcharlie": "charlie",
        "c": "charlie",
        "3": "charlie",
    }
    cleaned = re.sub(r"[^a-z0-9]+", "", folded)
    if cleaned in aliases:
        return aliases[cleaned]
    if "charlie" in cleaned:
        return "charlie"
    if "bravo" in cleaned:
        return "bravo"
    if "alpha" in cleaned:
        return "alpha"
    if "neutral" in cleaned:
        return "neutral"
    return folded


def _team_role_label(role: str) -> str:
    role = normalize_team_role(role)
    return {"neutral": "Neutral", "alpha": "Alpha", "bravo": "Bravo", "charlie": "Charlie"}.get(role, str(role).title())


def color_for_role(role: Any, scene=None) -> Tuple[float, float, float, float]:
    """Resolve a team role through the currently selected TeamColorDataSet."""
    role = normalize_team_role(role)
    rec = _selected_record(scene)
    if not rec:
        fallback = {"neutral": (0.45,0.45,0.45,1.0), "alpha": (1.0,0.35,0.15,1.0), "bravo": (0.1,0.45,1.0,1.0), "charlie": (0.2,0.9,0.35,1.0)}
        return fallback.get(role, (1.0,1.0,1.0,1.0))
    return tuple((rec.get("colors") or {}).get(role) or (1.0,1.0,1.0,1.0))


def _text_has_any(text: Any, patterns) -> bool:
    folded = str(text or "").casefold()
    return any(p in folded for p in patterns)


def _object_or_material_whitelisted(obj: bpy.types.Object | None = None, mat: bpy.types.Material | None = None) -> bool:
    values = []
    for src in (obj, mat):
        if src is None:
            continue
        values.append(getattr(src, "name", ""))
        try:
            values.extend(str(src.get(k, "")) for k in ("bcett_actor", "spl_team_color_whitelist", "osi_team_color_whitelist", "spl_team_color_kind", "osi_team_color_kind"))
        except Exception:
            pass
    return any(_text_has_any(v, _RECOLOR_NAME_PATTERNS) for v in values)


def _object_or_material_dynamic(obj: bpy.types.Object | None = None, mat: bpy.types.Material | None = None) -> bool:
    values = []
    for src in (obj, mat):
        if src is None:
            continue
        values.append(getattr(src, "name", ""))
        try:
            values.extend(str(src.get(k, "")) for k in ("bcett_actor", "spl_dynamic_team_color", "osi_dynamic_team_color", "spl_team_color_kind", "osi_team_color_kind"))
        except Exception:
            pass
    return any(_text_has_any(v, _DYNAMIC_RECOLOR_PATTERNS) for v in values)



def _mat_name(mat: bpy.types.Material | None) -> str:
    return str(getattr(mat, "name", "") if mat is not None else "").casefold()


def _obj_name(obj: bpy.types.Object | None) -> str:
    return str(getattr(obj, "name", "") if obj is not None else "").casefold()


def _obj_team_text(obj: bpy.types.Object | None) -> str:
    """Object name plus import metadata used for team-color whitelists.

    Imported BFRES child objects often have names such as Obj_ClamBoxNet while
    the BCETT actor that identifies the gameplay object is GachiasariGoal.  The
    material allow-list therefore needs the stored actor/model metadata too.
    """
    if obj is None:
        return ""
    parts = [getattr(obj, "name", "")]
    try:
        for key in ("bcett_actor", "bcett_model", "bcett_model_path", "spl_team_color_kind", "osi_team_color_kind"):
            v = obj.get(key, "")
            if v not in (None, ""):
                parts.append(str(v))
    except Exception:
        pass
    return " ".join(parts).casefold()


def _is_inkrail_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    if "inkrail" in text or "obj_inkrail" in text:
        return True
    try:
        return bool(obj is not None and _has_inkrail_parent_chain(obj))
    except Exception:
        return False


def _is_inkrail_paint_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    if _inkrail_part_kind(obj) == "main":
        return True
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    return "m_paint" in text or "obj_inkrail__m_paint" in text or "inkrail_paint" in text


def _is_inkrail_small_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    if _inkrail_part_kind(obj) == "small":
        return True
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    return "inkrailsmall" in text or "inkrail_small" in text or "small" in text and "inkrail" in text


def _is_inkrail_segment_object(obj: bpy.types.Object | None) -> bool:
    """Return True only for OSI-generated InkRail connection helpers.

    Neutral InkRails should hide only the lightweight segment cylinders created
    by the InkRail importer.  Imported node/template objects such as
    Obj_InkRail_VS, Obj_InkRailBase, Obj_InkRailSmall, Nozzle, NozzleSmall,
    Obj_InkRail_VSTrans, their armatures, and all node empties must remain
    visible.  Therefore this intentionally does *not* use names, material text,
    or the broad spl_team_color_kind tag as a visibility test.  The sole hideable
    objects are those explicitly marked by non_geometry.py as InkRail segment
    helpers.
    """
    if obj is None:
        return False
    try:
        return bool(obj.get("bcett_inkrail_segment_helper", False)) and str(obj.get("bcett_rail_kind", "")).casefold() == "inkrail"
    except Exception:
        return False


def _is_inkrail_segment_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    if _is_inkrail_segment_object(obj):
        return True
    # If this is a tagged InkRail node/root object, do not infer segment status
    # from broad material/object text.  Only explicit segment helpers are hidden
    # in Neutral.
    try:
        if obj is not None and (obj.get("bcett_inkrail_reconstructed", False) or obj.get("bcett_inkrail_part", None) is not None or _has_inkrail_parent_chain(obj)):
            return False
    except Exception:
        pass
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return "inkrail" in compact and ("line" in compact or "segment" in compact or "beam" in compact or "inkrailseg" in compact)




def _is_inkrail_object(obj: bpy.types.Object | None) -> bool:
    """Return True for InkRail sequence roots, node roots, imported node parts,
    and OSI-generated InkRail segment helpers.

    This is intentionally object-marker based so other rail/segment helper types
    keep their existing behavior.
    """
    if obj is None:
        return False
    try:
        return bool(
            obj.get("bcett_inkrail_sequence_root", False)
            or obj.get("bcett_inkrail_node_root", False)
            or obj.get("bcett_inkrail_reconstructed", False)
            or obj.get("bcett_inkrail_segment_helper", False)
            or str(obj.get("bcett_rail_kind", "")).casefold() == "inkrail"
            or str(obj.get("spl_team_color_kind", "")).casefold().startswith("inkrail")
            or _has_inkrail_parent_chain(obj)
        )
    except Exception:
        return False


def _nearest_inkrail_sequence_root(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    cur = obj
    for _ in range(64):
        if cur is None:
            break
        try:
            if bool(cur.get("bcett_inkrail_sequence_root", False)):
                return cur
        except Exception:
            pass
        cur = getattr(cur, "parent", None)
    return None




def _has_inkrail_parent_chain(obj: bpy.types.Object | None) -> bool:
    """True when obj or any parent belongs to an InkRail reconstruction."""
    cur = obj
    for _ in range(64):
        if cur is None:
            break
        try:
            if bool(
                cur.get("bcett_inkrail_sequence_root", False)
                or cur.get("bcett_inkrail_node_root", False)
                or cur.get("bcett_inkrail_reconstructed", False)
                or cur.get("bcett_inkrail_segment_helper", False)
                or str(cur.get("bcett_rail_kind", "")).casefold() == "inkrail"
                or str(cur.get("spl_team_color_kind", "")).casefold().startswith("inkrail")
            ):
                return True
        except Exception:
            pass
        cur = getattr(cur, "parent", None)
    return False
def _make_material_slot_unique_for_object(obj: bpy.types.Object | None, slot_index: int) -> bool:
    """Copy one material slot for object-local editing, preserving graph/data.

    InkRail active-team changes are per rail.  Imported InkRail parts are cloned
    from cached BFRES templates and can share material datablocks across rails;
    generated segment helpers can also share a procedural material.  If a shared
    material is recolored/alpha-edited directly, all rails change.  Copying the
    slot on first InkRail update gives each visible mesh its own material state.
    """
    if obj is None:
        return False
    data = getattr(obj, "data", None)
    mats = getattr(data, "materials", None)
    if not mats or slot_index < 0 or slot_index >= len(mats):
        return False
    mat = mats[slot_index]
    if mat is None:
        return False
    try:
        if bool(mat.get("bcett_inkrail_material_unique", False)) and str(mat.get("bcett_inkrail_material_owner", "")) == obj.name:
            return False
    except Exception:
        pass
    try:
        new_mat = mat.copy()
        new_mat.name = "%s__%s" % (mat.name, obj.name[:48])
        new_mat["bcett_inkrail_material_unique"] = True
        new_mat["bcett_inkrail_material_owner"] = obj.name
        # Keep graph locked so UI/team refresh updates values only.
        new_mat["osi_team_color_graph_locked"] = True
        new_mat["spl_team_color_graph_locked"] = True
        mats[slot_index] = new_mat
        return True
    except Exception:
        return False


def _ensure_inkrail_object_materials_unique(obj: bpy.types.Object | None) -> None:
    if obj is None or not _is_inkrail_object(obj):
        return
    data = getattr(obj, "data", None)
    mats = getattr(data, "materials", None)
    if not mats:
        return
    for i, mat in enumerate(list(mats)):
        try:
            if mat is not None and (_is_inkrail_mat(mat, obj) or _is_inkrail_segment_object(obj)):
                _make_material_slot_unique_for_object(obj, i)
        except Exception:
            pass


def _force_visible_inkrail_node_material(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> None:
    """Undo accidental alpha/visibility edits on imported InkRail node parts.

    Neutral should hide only explicit generated InkRail segment helpers.  Node
    parts such as Obj_InkRail_VS, Obj_InkRailSmall, bases, nozzles, and
    VSTrans must remain visible and opaque enough to display their base color.
    """
    if mat is None or _is_inkrail_segment_object(obj):
        return
    try:
        dc = tuple(getattr(mat, "diffuse_color", (1.0, 1.0, 1.0, 1.0)))
        if len(dc) >= 4 and float(dc[3]) <= 0.01:
            mat.diffuse_color = (float(dc[0]), float(dc[1]), float(dc[2]), 1.0)
    except Exception:
        pass
    try:
        if mat.use_nodes and mat.node_tree is not None:
            bsdf = _first_principled_bsdf(mat)
            if bsdf is not None and "Alpha" in bsdf.inputs:
                try:
                    if float(bsdf.inputs["Alpha"].default_value) <= 0.01:
                        bsdf.inputs["Alpha"].default_value = 1.0
                except Exception:
                    pass
    except Exception:
        pass


def _inkrail_part_kind(obj: bpy.types.Object | None) -> str:
    try:
        if obj is not None:
            part = str(obj.get("bcett_inkrail_part", "")).casefold()
            if part:
                return part
            cur = getattr(obj, "parent", None)
            for _ in range(16):
                if cur is None:
                    break
                part = str(cur.get("bcett_inkrail_part", "")).casefold()
                if part:
                    return part
                cur = getattr(cur, "parent", None)
    except Exception:
        pass
    return ""


def _is_inkrail_node_color_object(obj: bpy.types.Object | None) -> bool:
    """True for InkRail objects whose visible color is driven by active team.

    Only the main node-0 rail body and node-1+ small rail bodies should become
    team-tinted when active and black/base-colored when neutral.  Nozzles, bases,
    VSTrans parts, and generated segment helpers have separate rules.
    """
    if obj is None or _is_inkrail_segment_object(obj):
        return False
    part = _inkrail_part_kind(obj)
    if part in {"main", "small"}:
        return True
    if part in {"base", "nozzle", "small_nozzle", "trans"}:
        return False
    return False


def _inkrail_target_object_color(role: str, color: Tuple[float, float, float, float], obj: bpy.types.Object | None, mat: bpy.types.Material | None = None) -> Tuple[float, float, float, float]:
    role = normalize_team_role(role)
    if _is_inkrail_segment_object(obj):
        return (float(color[0]), float(color[1]), float(color[2]), 0.0 if role == "neutral" else max(float(color[3]), 0.85))
    if _is_inkrail_node_color_object(obj) or _is_inkrail_black_when_neutral_mat(mat, obj):
        if role == "neutral":
            return (0.0, 0.0, 0.0, 1.0)
        return (float(color[0]) * 0.55, float(color[1]) * 0.55, float(color[2]) * 0.55, max(float(color[3]), 1.0))
    return tuple(float(v) for v in color)


def _ensure_inkrail_object_color_graph(mat: bpy.types.Material | None, obj: bpy.types.Object | None) -> bool:
    """Make an InkRail material read per-object color instead of shared node values.

    This is the key per-rail fix: images and material datablocks may remain shared,
    but each object has its own Object.color value.  The material graph is patched
    once to use Object Info -> Color for existing Spl/OSI Team Color consumers, or
    for simple procedural segment materials, directly for Principled Base Color.
    No materials are copied or replaced here.
    """
    if mat is None or obj is None:
        return False
    if not (_is_inkrail_mat(mat, obj) or _is_inkrail_segment_object(obj)):
        return False
    # Older patches could mark a material as object-color driven even when the
    # BSDF Base Color was not actually linked to Object Info -> Color.  Validate
    # the graph each time so Cycles cannot keep using stale team-color node values
    # while the viewport appears correct from object display color.
    already_marked = False
    try:
        already_marked = bool(mat.get("bcett_inkrail_uses_object_color", False))
    except Exception:
        already_marked = False
    changed = False
    try:
        mat.use_nodes = True
        tree = mat.node_tree
        if tree is None:
            return False
        if already_marked:
            try:
                bsdf0 = _first_principled_bsdf(mat)
                base0 = bsdf0.inputs.get("Base Color") if bsdf0 is not None and hasattr(bsdf0.inputs, "get") else (bsdf0.inputs["Base Color"] if bsdf0 is not None and "Base Color" in bsdf0.inputs else None)
                if base0 is not None and any(getattr(l.from_node, "name", "") == "Spl InkRail Object Color" for l in base0.links):
                    return False
            except Exception:
                pass
        obj_node = tree.nodes.get("Spl InkRail Object Color") or tree.nodes.new(type="ShaderNodeObjectInfo")
        obj_node.name = "Spl InkRail Object Color"
        obj_node.label = "Spl InkRail Object Color"
        obj_color = obj_node.outputs.get("Color") if hasattr(obj_node.outputs, "get") else None
        if obj_color is None:
            return False
        # Replace existing Spl/OSI team-color RGB node consumers with Object Info.
        for team_name in ("Spl Team Color", "OSI Team Color"):
            node = tree.nodes.get(team_name)
            if node is None or not getattr(node, "outputs", None):
                continue
            out = node.outputs[0]
            for link in list(out.links):
                to_socket = link.to_socket
                try:
                    tree.links.remove(link)
                    tree.links.new(obj_color, to_socket)
                    changed = True
                except Exception:
                    pass
        bsdf = _first_principled_bsdf(mat)
        if bsdf is not None:
            # Simple/procedural helper materials may not have a Spl Team Color node.
            # For generated segments and imported InkRail main/small bodies, object
            # color is the safest per-object control.  Do not apply to nozzles/base/trans.
            if (_is_inkrail_segment_object(obj) or _is_inkrail_node_color_object(obj) or _is_inkrail_black_when_neutral_mat(mat, obj)) and "Base Color" in bsdf.inputs:
                base_input = bsdf.inputs["Base Color"]
                has_obj_link = any(getattr(l.from_node, "name", "") == "Spl InkRail Object Color" for l in base_input.links)
                if not has_obj_link:
                    for link in list(base_input.links):
                        try:
                            tree.links.remove(link)
                        except Exception:
                            pass
                    try:
                        tree.links.new(obj_color, base_input)
                        changed = True
                    except Exception:
                        pass
            if "Alpha" in bsdf.inputs and not _is_inkrail_segment_object(obj):
                try:
                    bsdf.inputs["Alpha"].default_value = 1.0
                except Exception:
                    pass
        mat["bcett_inkrail_uses_object_color"] = True
        mat["osi_team_color_graph_locked"] = True
        mat["spl_team_color_graph_locked"] = True
        changed = True
    except Exception:
        pass
    return changed


def _set_inkrail_object_color(obj: bpy.types.Object | None, role: str, color: Tuple[float, float, float, float], mat: bpy.types.Material | None = None) -> bool:
    if obj is None:
        return False
    if not (_is_inkrail_object(obj) or _is_inkrail_segment_object(obj) or _is_inkrail_mat(mat, obj)):
        return False
    target = _inkrail_target_object_color(role, color, obj, mat)
    try:
        obj.color = target
        obj["bcett_inkrail_object_color_role"] = normalize_team_role(role)
        return True
    except Exception:
        return False


def _force_inkrail_descendant_object_colors(root: bpy.types.Object | None, role: str, scene=None) -> int:
    """Apply InkRail Object.color to every mesh/child in a controlling subtree.

    Cycles evaluates Object Info -> Color on the actual mesh object being
    rendered, not on the selected empty/armature parent.  Node 0 imports include
    armature/template roots with mesh descendants, so neutral restoration must
    touch every descendant mesh object and verify its material graph links Base
    Color to Object Info -> Color.
    """
    if root is None:
        return 0
    role = normalize_team_role(role)
    color = color_for_role(role, scene)
    changed = 0
    for obj in [root] + list(_iter_object_descendants(root)):
        try:
            if not (_is_inkrail_object(obj) or _has_inkrail_parent_chain(obj) or _is_inkrail_segment_object(obj)):
                continue
            mats = list(getattr(getattr(obj, "data", None), "materials", []) or [])
            if not mats:
                # Parent empties/armatures still get color for viewport consistency.
                if _set_inkrail_object_color(obj, role, color, None):
                    changed += 1
                continue
            for mat in mats:
                try:
                    if _set_inkrail_object_color(obj, role, color, mat):
                        changed += 1
                    _ensure_inkrail_object_color_graph(mat, obj)
                    if role == "neutral" and not _is_inkrail_segment_object(obj):
                        _force_visible_inkrail_node_material(mat, obj)
                except Exception:
                    pass
        except Exception:
            pass
    return changed

def _is_inkrail_nozzle_small_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return "nozzlesmall" in compact or "objnozzlesmall" in compact


def _is_inkrail_neutral_base_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    part = _inkrail_part_kind(obj)
    if part in {"main", "small"}:
        return True
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if _is_inkrail_nozzle_small_mat(mat, obj):
        return True
    return "objinkrailsmall" in compact or "objinkrail" in compact or _is_inkrail_small_mat(mat, obj) or _is_inkrail_paint_mat(mat, obj)




def _is_inkrail_black_when_neutral_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    part = _inkrail_part_kind(obj)
    if part in {"main", "small"}:
        return True
    if part in {"base", "nozzle", "small_nozzle", "trans"}:
        return False
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if "nozzle" in compact or "base" in compact or "vstrans" in compact or "trans" in compact:
        return False
    return "objinkrailvs" in compact or "objinkrailsmall" in compact or "inkrailsmall" in compact


def _set_inkrail_neutral_base_black(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    """Neutral Obj_InkRail/Obj_InkRailSmall use their authored black base color.

    Only generated InkRail segment helpers should go transparent on Neutral.  The
    imported node pieces remain visible; for the main rail/small rail pieces the
    visible neutral color should be black rather than the neutral team swatch.
    """
    if mat is None or not _is_inkrail_black_when_neutral_mat(mat, obj):
        return False
    changed = False
    try:
        mat.diffuse_color = (0.0, 0.0, 0.0, 1.0)
        mat.blend_method = "OPAQUE"
        changed = True
    except Exception:
        pass
    try:
        if mat.use_nodes and mat.node_tree is not None:
            bsdf = _first_principled_bsdf(mat)
            if bsdf is not None:
                if "Base Color" in bsdf.inputs:
                    bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
                    changed = True
                if "Alpha" in bsdf.inputs:
                    bsdf.inputs["Alpha"].default_value = 1.0
                    changed = True
            for name in ("Spl Team Color", "OSI Team Color"):
                node = mat.node_tree.nodes.get(name)
                if node is not None and getattr(node, "outputs", None):
                    try:
                        node.outputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
                        changed = True
                    except Exception:
                        pass
    except Exception:
        pass
    return changed
def _is_gachiyagurarail_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return "gachiyagurarail" in compact or ("gachiyagura" in compact and "rail" in compact)


def _is_gachiyagurarail_segment_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return _is_gachiyagurarail_mat(mat, obj) and any(k in compact for k in ("segment", "line", "beam", "railsegment", "rail_segment", "curve", "path", "seg"))


def _is_gachiyagurarail_node_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    text = _mat_name(mat) + " " + _obj_name(obj) + " " + _obj_team_text(obj)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if not _is_gachiyagurarail_mat(mat, obj):
        return False
    if _is_gachiyagurarail_segment_mat(mat, obj):
        return False
    return any(k in compact for k in ("node", "point", "joint", "sphere", "ctrl", "control", "terminal", "mnode"))


def _is_gachiasari_barrier_mat(mat: bpy.types.Material | None) -> bool:
    text = _mat_name(mat)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return ("barrier" in compact and ("mbarrier" in compact or compact.endswith("barrier") or "barrierbody" in compact)) or "barrier__m_barrier" in text or "m_barrier" in text


def _is_goalshachi_or_checkpoint_emission_mat(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    text = _mat_name(mat) + " " + _obj_team_text(obj)
    return any(k in text for k in ("gachihokocheckpoint", "gachihokogoal", "goalshachi", "checkpointshachi", "obj_checkpointshachi"))


def _material_allowed_for_team_color(mat: bpy.types.Material | None, obj: bpy.types.Object | None = None) -> bool:
    """Precise whitelist to avoid recoloring ordinary field/stage geometry."""
    if mat is None:
        return False
    mat_text = _mat_name(mat)
    obj_text = _obj_team_text(obj)
    combined = mat_text + " " + obj_text
    # Dynamic/neutral-by-default helpers.  GachiYaguraRail nodes and segments
    # are both driven by the active team color.
    if _is_gachiyagurarail_mat(mat, obj):
        return True
    if "painttargetarea" in combined:
        return True
    # InkRail: only paint, small, and generated segment/line materials.
    if _is_inkrail_mat(mat, obj):
        if _is_inkrail_nozzle_small_mat(mat, obj):
            return False
        return _is_inkrail_paint_mat(mat, obj) or _is_inkrail_small_mat(mat, obj) or _is_inkrail_segment_mat(mat, obj)
    # GachiAsariGoal: only the specifically named barrier material.  Match the
    # actor/model case-insensitively, but do not broaden to unrelated Asari text.
    if "gachiasarigoal" in combined:
        return _is_gachiasari_barrier_mat(mat)
    # Bunker/Sponge whole object variants are currently intended to recolor.
    if "bunker" in combined or "sponge" in combined:
        return True
    # Explicit goal/checkpoint/sign/clam cases.
    if any(k in combined for k in ("gachihokogoal", "gachihokocheckpoint", "hokosignboard", "goalshachi", "checkpointshachi", "obj_checkpointshachi", "clamgreat", "obj_clamgreat", "gachisignboad", "gachisignboard")):
        if "gachisign" in combined:
            return "m_body" in mat_text or "body" in mat_text
        return True
    return False


def _is_full_recolor_role(mat: bpy.types.Material | None, role: str, obj: bpy.types.Object | None = None) -> bool:
    role = normalize_team_role(role)
    if _is_inkrail_nozzle_small_mat(mat, obj):
        return False
    if _is_inkrail_segment_mat(mat, obj):
        return True
    # Obj_InkRail / Obj_InkRailSmall should mix base color with active team color
    # when activated, not become a flat team-color material.
    if _is_gachiyagurarail_mat(mat, obj):
        return True
    if "painttargetarea" in (_mat_name(mat) + " " + _obj_name(obj)):
        return True
    return False

def _image_node_name(node) -> str:
    parts = [getattr(node, "name", ""), getattr(node, "label", "")]
    img = getattr(node, "image", None)
    if img is not None:
        parts.append(getattr(img, "name", ""))
        parts.append(getattr(img, "filepath", ""))
    return " ".join(str(p) for p in parts if p)


def _find_team_mask_socket(mat: bpy.types.Material):
    """Return a Tcl/2cl texture Color socket when present.

    Tcl and 2cl textures are team-color masks.  Splatoon's mask mix must use the
    texture Color output as the mix Fac input; using Alpha breaks imported and
    reloaded appearances for these materials.
    """
    try:
        if not mat or not mat.use_nodes or mat.node_tree is None:
            return None
        for node in mat.node_tree.nodes:
            if getattr(node, "bl_idname", "") != "ShaderNodeTexImage":
                continue
            text = _image_node_name(node).casefold()
            if any(t in text for t in _IGNORE_TEAM_TEXTURE_TOKENS):
                continue
            if any(t in text for t in _TEAM_MASK_TEXTURE_TOKENS):
                if "Color" in node.outputs:
                    return node.outputs["Color"]
                if "Alpha" in node.outputs:
                    return node.outputs["Alpha"]
    except Exception:
        return None
    return None


def material_has_team_color_params(mat: bpy.types.Material) -> bool:
    """Legacy broad detector retained for diagnostics only.

    Do not use this alone for recoloring; too many ordinary stage materials expose
    the same generic Hoian_UBER parameter names.
    """
    if mat is None:
        return False
    try:
        keys = list(mat.keys())
    except Exception:
        return False
    needles = (
        "team_color", "teamcolor", "display_team_type", "two_comp_paint_team",
        "my_team_color", "alpha_team_color", "bravo_team_color", "charlie_team_color",
    )
    return any(any(n in str(k).casefold() for n in needles) for k in keys)


def material_should_link_team_color(mat: bpy.types.Material, obj: bpy.types.Object | None = None) -> bool:
    if mat is None:
        return False
    # GachiYaguraRail nodes and segments are dynamic team-color helpers.
    if _is_gachiyagurarail_mat(mat, obj):
        return True
    if bool(mat.get("spl_team_color_managed", mat.get("osi_team_color_managed", False))):
        return True
    return _material_allowed_for_team_color(mat, obj)

def _ensure_rgb_node(tree, name: str, color: Tuple[float, float, float, float]):
    node = tree.nodes.get(name)
    if node is None:
        node = tree.nodes.new(type="ShaderNodeRGB")
        node.name = name
        node.label = name
    try:
        node.outputs[0].default_value = color
    except Exception:
        pass
    return node


def _first_principled_bsdf(mat: bpy.types.Material):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return None
    for node in mat.node_tree.nodes:
        if getattr(node, "type", "") == "BSDF_PRINCIPLED" or "Principled" in getattr(node, "name", ""):
            return node
    return mat.node_tree.nodes.get("Principled BSDF")


def _find_named_image_socket(mat: bpy.types.Material, tokens) -> Any:
    try:
        if not mat or not mat.use_nodes or mat.node_tree is None:
            return None
        toks = tuple(str(t).casefold() for t in tokens)
        for node in mat.node_tree.nodes:
            if getattr(node, "bl_idname", "") != "ShaderNodeTexImage":
                continue
            text = _image_node_name(node).casefold()
            if any(t in text for t in toks):
                if "Color" in node.outputs:
                    return node.outputs["Color"]
                if "Alpha" in node.outputs:
                    return node.outputs["Alpha"]
    except Exception:
        pass
    return None


def _repair_tcl_mask_color_fac_links(mat: bpy.types.Material) -> bool:
    """Repair Tcl/2cl mask links so Color, not Alpha, drives the team mask Fac."""
    changed = False
    try:
        if not mat or not mat.use_nodes or mat.node_tree is None:
            return False
        tree = mat.node_tree
        mask_nodes = []
        for node in tree.nodes:
            if getattr(node, "bl_idname", "") != "ShaderNodeTexImage":
                continue
            text = _image_node_name(node).casefold()
            if any(t in text for t in _IGNORE_TEAM_TEXTURE_TOKENS):
                continue
            if any(t in text for t in _TEAM_MASK_TEXTURE_TOKENS):
                mask_nodes.append(node)
        if not mask_nodes:
            return False
        for mix_name in ("Spl Team Color Mask Mix", "OSI Team Color Mask Mix"):
            mix = tree.nodes.get(mix_name)
            if mix is None or not getattr(mix, "inputs", None):
                continue
            fac = mix.inputs[0]
            for link in list(fac.links):
                src = getattr(link, "from_node", None)
                if src in mask_nodes and getattr(link.from_socket, "name", "") == "Alpha" and "Color" in src.outputs:
                    tree.links.remove(link)
                    tree.links.new(src.outputs["Color"], fac)
                    changed = True
    except Exception:
        return changed
    return changed


def _link_team_emission(tree, bsdf, team_node, mat, obj) -> None:
    """Drive selected GachiHoko checkpoint emissions from team color.

    GachihokoGoal itself intentionally keeps its authored _Emm texture/color
    unchanged; only checkpoint/GoalShachi-style materials should have their
    emission multiplied by the selected team color.
    """
    if not _is_goalshachi_or_checkpoint_emission_mat(mat, obj):
        return
    try:
        if "gachihokogoal" in (_mat_name(mat) + " " + _obj_team_text(obj)):
            return
    except Exception:
        pass
    if "Emission Color" not in bsdf.inputs:
        return
    emm_socket = _find_named_image_socket(mat, ("_emm", "emm"))
    if emm_socket is None:
        emm_socket = team_node.outputs[0]
    mix = tree.nodes.get("Spl Team Emission Mix")
    if mix is None:
        mix = tree.nodes.new(type="ShaderNodeMixRGB")
        mix.name = "Spl Team Emission Mix"
        mix.label = "Spl Team Emission Mix"
    try:
        mix.blend_type = "MULTIPLY"
        mix.inputs[0].default_value = 1.0
    except Exception:
        pass
    try:
        c1 = mix.inputs.get("Color1") if hasattr(mix.inputs, "get") else mix.inputs[1]
        c2 = mix.inputs.get("Color2") if hasattr(mix.inputs, "get") else mix.inputs[2]
        for sock in (c1, c2, bsdf.inputs["Emission Color"]):
            for link in list(sock.links):
                tree.links.remove(link)
        tree.links.new(emm_socket, c1)
        tree.links.new(team_node.outputs[0], c2)
        tree.links.new(mix.outputs[0], bsdf.inputs["Emission Color"])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = max(float(bsdf.inputs["Emission Strength"].default_value), 1.0)
        try:
            base = getattr(bsdf, "location", None)
            if base is not None:
                mix.location = (float(base.x) - 220, float(base.y) - 180)
        except Exception:
            pass
    except Exception:
        pass


def _unlink_gachihokogoal_team_emission(mat: bpy.types.Material, obj: bpy.types.Object | None = None) -> None:
    """For GachihokoGoal, preserve authored _Emm emission and only remove team tint."""
    try:
        if "gachihokogoal" not in (_mat_name(mat) + " " + _obj_team_text(obj)):
            return
        if not mat or not mat.use_nodes or mat.node_tree is None:
            return
        tree = mat.node_tree
        bsdf = _first_principled_bsdf(mat)
        if bsdf is None:
            return
        if "Emission Strength" in bsdf.inputs:
            try:
                cur = float(bsdf.inputs["Emission Strength"].default_value)
            except Exception:
                cur = 0.0
            if cur <= 0.0:
                bsdf.inputs["Emission Strength"].default_value = 1.0
        if "Emission Color" not in bsdf.inputs:
            return
        emi = bsdf.inputs["Emission Color"]
        bad = []
        for link in list(emi.links):
            n = getattr(link.from_node, "name", "")
            if str(n).startswith(("Spl Team", "OSI Team")) or "Team Emission" in str(n):
                bad.append(link)
        if not bad:
            return
        emm_socket = _find_named_image_socket(mat, ("_emm", "emm"))
        for link in bad:
            tree.links.remove(link)
        if emm_socket is not None and not emi.links:
            tree.links.new(emm_socket, emi)
    except Exception:
        pass


def _update_team_color_values_only(mat: bpy.types.Material, role: str, color: Tuple[float, float, float, float], obj: bpy.types.Object | None = None) -> bool:
    """Non-destructive refresh used by UI buttons, selector callbacks, and file-load paths.

    It updates existing color value nodes/properties only.  It must not rebuild,
    relayout, remove, duplicate, or reconnect the imported shader graph except
    for narrow repair paths.  Procedural helper materials created by OSI have no
    team-color value node, so their Principled color/alpha is updated directly.
    """
    if mat is None:
        return False
    role = normalize_team_role(role)
    changed = False
    target_color = color
    inkrail_context = bool(obj is not None and (_is_inkrail_object(obj) or _is_inkrail_segment_object(obj) or _is_inkrail_mat(mat, obj)))
    # InkRail colors are per object/rail.  Do not mutate shared material colors
    # for InkRail node parts; store the desired color on obj.color and use an
    # Object Info node in the shared material graph.
    if inkrail_context:
        target_color = _inkrail_target_object_color(role, color, obj, mat)
        _set_inkrail_object_color(obj, role, color, mat)
        _ensure_inkrail_object_color_graph(mat, obj)
    try:
        if not inkrail_context:
            mat.diffuse_color = target_color
        mat["spl_team_color_role"] = role
        mat["spl_team_color_label"] = _team_role_label(role)
        mat["osi_team_color_role"] = role
        mat["osi_team_color_label"] = _team_role_label(role)
        mat["osi_team_color_graph_locked"] = True
        mat["spl_team_color_graph_locked"] = True
        if _is_inkrail_segment_mat(mat, obj):
            mat["spl_inkrail_segment_visibility_role"] = role
            mat.blend_method = "BLEND"
            try:
                mat.show_transparent_back = True
            except Exception:
                pass
        changed = True
    except Exception:
        pass
    try:
        if mat.use_nodes and mat.node_tree is not None:
            has_value_node = False
            for name in ("Spl Team Color", "OSI Team Color"):
                node = mat.node_tree.nodes.get(name)
                if node is not None and getattr(node, "outputs", None):
                    has_value_node = True
                    try:
                        if not inkrail_context:
                            node.outputs[0].default_value = color
                            changed = True
                    except Exception:
                        pass
            # Direct-color procedural helpers: GachiYaguraRail nodes/segments and
            # PaintTargetArea outlines use team color exactly; InkRail segments
            # use team color exactly when active and alpha 0 when neutral.
            if (not has_value_node) and (_is_gachiyagurarail_mat(mat, obj) or "painttargetarea" in (_mat_name(mat) + " " + _obj_name(obj)) or _is_inkrail_segment_mat(mat, obj) or (_is_inkrail_mat(mat, obj) and _is_inkrail_neutral_base_mat(mat, obj))):
                bsdf = _first_principled_bsdf(mat)
                if bsdf is not None:
                    try:
                        if not inkrail_context:
                            if "Base Color" in bsdf.inputs:
                                bsdf.inputs["Base Color"].default_value = target_color
                                changed = True
                            if "Alpha" in bsdf.inputs:
                                bsdf.inputs["Alpha"].default_value = float(target_color[3])
                                changed = True
                    except Exception:
                        pass
            _repair_tcl_mask_color_fac_links(mat)
            _unlink_gachihokogoal_team_emission(mat, obj)
    except Exception:
        pass
    return changed


def _configure_material_nodes_for_team_color(mat: bpy.types.Material, role: str, color: Tuple[float, float, float, float], obj: bpy.types.Object | None = None) -> bool:
    """Tint the existing base-color texture by the selected team color.

    This intentionally preserves the imported Alb texture/detail by multiplying
    the existing Base Color source with the active team color.  It is a first
    pass for BFRES team-color materials; the original texture link is preserved
    behind the OSI mix node so the selected color can be updated live.
    """
    if mat is None:
        return False
    try:
        mat.diffuse_color = color
        mat["spl_team_color_managed"] = True
        mat["spl_team_color_role"] = normalize_team_role(role)
        mat["spl_team_color_label"] = _team_role_label(role)
        # Backward-compatible aliases for scenes imported by older versions.
        mat["osi_team_color_managed"] = True
        mat["osi_team_color_role"] = normalize_team_role(role)
        mat["osi_team_color_label"] = _team_role_label(role)
    except Exception:
        pass
    try:
        mat.use_nodes = True
        tree = mat.node_tree
        if tree is None:
            return False
        bsdf = _first_principled_bsdf(mat)
        if bsdf is None or "Base Color" not in bsdf.inputs:
            return False
        base_input = bsdf.inputs["Base Color"]
        team_node = _ensure_rgb_node(tree, "Spl Team Color", color)
        original_node = tree.nodes.get("Spl Original Base Color") or tree.nodes.get("OSI Original Base Color")
        mask_socket = _find_team_mask_socket(mat)
        full_recolor = _is_full_recolor_role(mat, role, obj)
        mix_name = "Spl Team Color Mask Mix" if (mask_socket is not None and not full_recolor) else "Spl Team Color Mix"
        mix = tree.nodes.get(mix_name) or tree.nodes.get("Spl Team Color Mask Mix") or tree.nodes.get("Spl Team Color Mix") or tree.nodes.get("OSI Team Color Multiply") or tree.nodes.get("OSI Team Color Mask Mix")
        # Capture the existing base-color source before inserting our node.
        original_socket = None
        existing_links = list(base_input.links)
        for link in existing_links:
            if str(getattr(link.from_node, "name", "")).startswith(("OSI Team Color", "Spl Team Color")):
                continue
            original_socket = link.from_socket
        if original_socket is None:
            # Reuse an already-captured original source if present.
            if mix is not None and "Color1" in mix.inputs and mix.inputs["Color1"].links:
                original_socket = mix.inputs["Color1"].links[0].from_socket
        if original_socket is None:
            original_color = tuple(base_input.default_value) if hasattr(base_input, "default_value") else (1,1,1,1)
            original_node = _ensure_rgb_node(tree, "Spl Original Base Color", original_color)
            original_socket = original_node.outputs[0]
        if mix is None:
            mix = tree.nodes.new(type="ShaderNodeMixRGB")
        mix.name = mix_name
        mix.label = mix_name
        try:
            mix.blend_type = "MIX"
            if full_recolor:
                mix.inputs[0].default_value = 1.0
            elif mask_socket is not None and not full_recolor:
                mix.inputs[0].default_value = 0.0
            else:
                mix.inputs[0].default_value = 0.35
        except Exception:
            pass
        if mask_socket is not None and not full_recolor:
            try:
                for link in list(mix.inputs[0].links):
                    tree.links.remove(link)
                tree.links.new(mask_socket, mix.inputs[0])
            except Exception:
                pass
        # Remove non-OSI links to Base Color, then link through the mix node.
        for link in list(base_input.links):
            if not str(getattr(link.from_node, "name", "")).startswith(("OSI Team Color", "Spl Team Color")):
                tree.links.remove(link)
        # Ensure original/team links exist.
        def has_link(to_socket, from_node_name=None):
            for link in to_socket.links:
                if from_node_name is None or getattr(link.from_node, "name", "") == from_node_name:
                    return True
            return False
        color1 = mix.inputs.get("Color1") if hasattr(mix.inputs, "get") else mix.inputs[1]
        color2 = mix.inputs.get("Color2") if hasattr(mix.inputs, "get") else mix.inputs[2]
        if not color1.links:
            tree.links.new(original_socket, color1)
        if not has_link(color2, "Spl Team Color"):
            for link in list(color2.links):
                tree.links.remove(link)
            tree.links.new(team_node.outputs[0], color2)
        if not base_input.links:
            tree.links.new(mix.outputs[0], base_input)
        if "Alpha" in bsdf.inputs:
            try:
                bsdf.inputs["Alpha"].default_value = color[3]
            except Exception:
                pass
        try:
            _repair_tcl_mask_color_fac_links(mat)
        except Exception:
            pass
        try:
            _link_team_emission(tree, bsdf, team_node, mat, obj)
        except Exception:
            pass
        try:
            _unlink_gachihokogoal_team_emission(mat, obj)
        except Exception:
            pass
        try:
            mat["osi_team_color_graph_locked"] = True
            mat["spl_team_color_graph_locked"] = True
            mat["osi_team_color_graph_version"] = 2
            mat["spl_team_color_graph_version"] = 2
        except Exception:
            pass
        mat.blend_method = "BLEND" if color[3] < 0.999 else getattr(mat, "blend_method", "OPAQUE")
        try:
            # Layout: mix where Alb/base texture was, with mask/alb/team stacked to the left.
            base_loc = getattr(original_socket.node, "location", None) if original_socket is not None else None
            if base_loc is not None:
                old_x, old_y = float(base_loc.x), float(base_loc.y)
                mix.location = (old_x, old_y)
                team_node.location = (old_x - 220, old_y - 140)
                try:
                    original_socket.node.location = (old_x - 220, old_y)
                except Exception:
                    pass
                if mask_socket is not None:
                    try:
                        mask_socket.node.location = (old_x - 220, old_y + 140)
                    except Exception:
                        pass
        except Exception:
            pass
        return True
    except Exception as exc:
        try:
            mat["osi_team_color_error"] = str(exc)
        except Exception:
            pass
        return False


def apply_team_color_to_material(mat: bpy.types.Material, role: Any, scene=None, require_team_params: bool = True, obj: bpy.types.Object | None = None, value_only: bool = False) -> bool:
    if mat is None:
        return False
    role = normalize_team_role(role)
    if require_team_params and not material_should_link_team_color(mat, obj):
        return False
    if _is_inkrail_nozzle_small_mat(mat, obj):
        return False
    color = color_for_role(role, scene)
    if _is_inkrail_segment_mat(mat, obj):
        # Segments are hidden for neutral rails and direct team-colored when active.
        return _update_team_color_values_only(mat, role, color, obj=obj)
    if role == "neutral" and _is_inkrail_mat(mat, obj) and _is_inkrail_neutral_base_mat(mat, obj):
        try:
            if mat.use_nodes:
                _repair_tcl_mask_color_fac_links(mat)
        except Exception:
            pass
        if obj is not None and (_is_inkrail_node_color_object(obj) or _is_inkrail_black_when_neutral_mat(mat, obj)):
            changed = _set_inkrail_object_color(obj, role, color, mat)
            try:
                _ensure_inkrail_object_color_graph(mat, obj)
                _force_visible_inkrail_node_material(mat, obj)
            except Exception:
                pass
            return changed
        changed = _set_inkrail_neutral_base_black(mat, obj)
        try:
            _force_visible_inkrail_node_material(mat, obj)
        except Exception:
            pass
        return changed
    if value_only or bool(mat.get("osi_team_color_graph_locked", mat.get("spl_team_color_graph_locked", False))):
        updated = _update_team_color_values_only(mat, role, color, obj=obj)
        # If this material was marked managed by an older scene but has no team
        # color value node at all, fall back to one controlled build.
        try:
            has_value_node = bool(mat.use_nodes and mat.node_tree and (mat.node_tree.nodes.get("Spl Team Color") or mat.node_tree.nodes.get("OSI Team Color")))
        except Exception:
            has_value_node = False
        # Live/custom-property updates must never create, delete, copy, or relink
        # material datablocks or shader graphs.  If an InkRail part has no managed
        # team node yet, _update_team_color_values_only still updates the material
        # diffuse/Principled values directly.
        if value_only or has_value_node:
            return updated
    return _configure_material_nodes_for_team_color(mat, role, color, obj=obj)




def _ensure_spl_active_team_first(obj: bpy.types.Object | None) -> None:
    """Keep spl_active_team as the first visible custom property on team-driven objects."""
    if obj is None:
        return
    try:
        if obj.get("spl_active_team", None) is None:
            return
        role = normalize_team_role(obj.get("spl_active_team"))
        keys = [str(k) for k in obj.keys() if str(k) not in ("_RNA_UI", "spl_active_team")]
        values = []
        for k in keys:
            try:
                values.append((k, obj[k]))
            except Exception:
                pass
        try:
            del obj["spl_active_team"]
        except Exception:
            pass
        obj["spl_active_team"] = role
        for k, v in values:
            try:
                cur = obj.get(k, None)
                if cur != v:
                    obj[k] = v
            except Exception:
                try:
                    obj[k] = v
                except Exception:
                    pass
    except Exception:
        pass

def _effective_object_team_role(obj: bpy.types.Object) -> str:
    """Resolve the active team for an object from its controlling root.

    InkRail children inherit the sequence root.  Other actor descendants inherit
    the nearest/highest team-assignment root, so a parent Empty can drive all
    child meshes and helper objects.  Direct edits to a child are promoted by the
    watcher/apply button before this function is called.
    """
    try:
        root = _nearest_team_assignment_root(obj)
    except Exception:
        root = obj
    for cur in (root, obj):
        for key in ("spl_active_team", "bcett_team"):
            try:
                if cur is not None and cur.get(key, None) is not None:
                    return normalize_team_role(cur.get(key))
            except Exception:
                pass
    return "neutral"




def _object_has_team_color_material(obj: bpy.types.Object | None) -> bool:
    if obj is None:
        return False
    try:
        mats = list(getattr(getattr(obj, "data", None), "materials", []) or [])
        return any(material_should_link_team_color(m, obj) for m in mats)
    except Exception:
        return False


def _object_has_team_assignment(obj: bpy.types.Object | None) -> bool:
    if obj is None:
        return False
    try:
        return bool(
            obj.get("spl_active_team", None) is not None
            or obj.get("spl_team_color_enabled", obj.get("osi_team_color_enabled", False))
            or obj.get("spl_dynamic_team_color", obj.get("osi_dynamic_team_color", False))
            or _is_inkrail_object(obj)
            or _object_has_team_color_material(obj)
        )
    except Exception:
        return False


def _iter_object_descendants(obj: bpy.types.Object | None):
    if obj is None:
        return
    stack = list(getattr(obj, "children", []) or [])
    seen = set()
    while stack:
        child = stack.pop(0)
        ident = getattr(child, "name_full", getattr(child, "name", ""))
        if ident in seen:
            continue
        seen.add(ident)
        yield child
        try:
            stack[0:0] = list(getattr(child, "children", []) or [])
        except Exception:
            pass


def _nearest_team_assignment_root(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    """Return the object whose spl_active_team should control this object.

    For InkRail, this is always the sequence root.  For regular actors, climb to
    the highest parent in the local actor hierarchy that already exposes team
    assignment/team-color metadata.  This supports descendant-up editing: changing
    a mesh child custom property can update the entire actor/rail.
    """
    if obj is None:
        return None
    try:
        seq = _nearest_inkrail_sequence_root(obj)
        if seq is not None:
            return seq
    except Exception:
        pass
    best = obj if _object_has_team_assignment(obj) else None
    cur = getattr(obj, "parent", None)
    for _ in range(64):
        if cur is None:
            break
        try:
            if _object_has_team_assignment(cur):
                best = cur
        except Exception:
            pass
        cur = getattr(cur, "parent", None)
    return best or obj


def _descendant_relevant_for_team(obj: bpy.types.Object | None, force_from_root: bool = False) -> bool:
    if obj is None:
        return False
    if force_from_root:
        return True
    return _object_has_team_assignment(obj)


def _sync_active_team_to_subtree(root: bpy.types.Object | None, role: str, scene=None, value_only: bool = True) -> int:
    """Set one active team on root and all relevant descendants, then refresh.

    This is deliberately value-only: it does not copy, delete, or replace material
    datablocks.  It makes ID custom-property edits behave like the View3D button
    while preserving per-object Object Color updates for InkRail meshes.
    """
    if root is None:
        return 0
    role = normalize_team_role(role)
    changed = 0
    objs = [root] + list(_iter_object_descendants(root))
    # A team-enabled root should drive all descendants in that actor/rail, even
    # meshes that do not expose spl_active_team themselves.
    root_drives_all = bool(
        _is_inkrail_object(root)
        or root.get("spl_team_color_enabled", root.get("osi_team_color_enabled", False))
        or root.get("spl_dynamic_team_color", root.get("osi_dynamic_team_color", False))
        or root.get("spl_active_team", None) is not None
    )
    for obj in objs:
        try:
            relevant = root_drives_all or _descendant_relevant_for_team(obj)
            if not relevant:
                continue
            obj["spl_active_team"] = role
            _ensure_spl_active_team_first(obj)
            for k in ("spl_team", "osi_team", "osi_active_team"):
                try:
                    if k in obj:
                        del obj[k]
                except Exception:
                    pass
            if hasattr(obj, "spl_team_assignment") and getattr(obj, "spl_team_assignment", None) != role:
                try:
                    obj.spl_team_assignment = role
                except Exception:
                    pass
            changed += apply_team_color_to_object(obj, scene, value_only=value_only, allow_material_copy=False, propagate_children=False)
        except Exception:
            pass
    try:
        if _is_inkrail_object(root):
            changed += _force_inkrail_descendant_object_colors(root, role, scene)
    except Exception:
        pass
    return changed

def _sync_object_team_enum(obj: bpy.types.Object, role: str, update_custom: bool = True) -> None:
    try:
        role = normalize_team_role(role)
        obj["spl_active_team"] = role
        _ensure_spl_active_team_first(obj)
        # Do not expose legacy duplicate custom props.
        for k in ("spl_team", "osi_team", "osi_active_team"):
            try:
                if k in obj:
                    del obj[k]
            except Exception:
                pass
        if update_custom and hasattr(obj, "spl_team_assignment"):
            obj.spl_team_assignment = role
    except Exception:
        pass

def apply_team_color_to_object(obj: bpy.types.Object, scene=None, value_only: bool = False, allow_material_copy: bool = False, propagate_children: bool = True) -> int:
    """Apply the currently selected TeamColorDataSet to one imported object."""
    if obj is None:
        return 0
    role = _effective_object_team_role(obj)
    changed = 0
    # Do not copy/replace materials from spl_active_team callbacks.  Creating or
    # swapping material datablocks during property changes/undo can leave Blender's
    # depsgraph with invalid material IDs and can explode memory.  Import/link paths
    # may opt in once, but live team changes are value-only.
    if allow_material_copy:
        try:
            _ensure_inkrail_object_materials_unique(obj)
        except Exception:
            pass
    try:
        if obj.get("bcett_team", None) is None:
            obj["bcett_team"] = role
        obj["spl_active_team"] = role
        _ensure_spl_active_team_first(obj)
        for k in ("spl_team", "osi_team", "osi_active_team"):
            try:
                if k in obj:
                    del obj[k]
            except Exception:
                pass
    except Exception:
        pass
    # Neutral InkRail should hide only explicit generated segment helper meshes.
    # All imported node empties, armatures, bases, VS/Trans roots, nozzles, and
    # small rail parts remain visible regardless of active team.
    try:
        if _is_inkrail_object(obj) and not _is_inkrail_segment_object(obj):
            obj.hide_viewport = False
            obj.hide_render = False
    except Exception:
        pass
    data = getattr(obj, "data", None)
    mats = getattr(data, "materials", None)
    if mats:
        for mat in mats:
            try:
                if _is_inkrail_object(obj) or _is_inkrail_segment_object(obj):
                    _set_inkrail_object_color(obj, role, color_for_role(role, scene), mat)
            except Exception:
                pass
            if apply_team_color_to_material(mat, role, scene, require_team_params=True, obj=obj, value_only=value_only):
                changed += 1
            # Neutral imported InkRail node parts keep their authored/base material
            # and must remain visible.  This is not applied to generated segment
            # helpers, whose visibility is object-marker driven below.
            if role == "neutral" and _is_inkrail_object(obj) and not _is_inkrail_segment_object(obj):
                _force_visible_inkrail_node_material(mat, obj)
        # InkRail segments are visible only when the active team is non-neutral.
        # Node objects themselves must always remain visible, even when Neutral.
        try:
            if _is_inkrail_segment_object(obj):
                hidden = (role == "neutral")
                obj.hide_viewport = hidden
                obj.hide_render = hidden
            elif any(_is_inkrail_mat(mat, obj) for mat in mats):
                obj.hide_viewport = False
                obj.hide_render = False
        except Exception:
            pass
    # Parent-level dynamic controls: if an empty is tagged/assigned, push its active
    # team to child node/segment mesh helpers without requiring per-child editing.
    if not propagate_children:
        try:
            if _is_inkrail_object(obj):
                changed += _force_inkrail_descendant_object_colors(obj, role, scene)
        except Exception:
            pass
        return changed
    parent_dynamic = bool(obj.get("spl_dynamic_team_color", obj.get("osi_dynamic_team_color", False)) or obj.get("spl_team_color_enabled", obj.get("osi_team_color_enabled", False)))
    for child in getattr(obj, "children", []) or []:
        child_mats = list(getattr(getattr(child, "data", None), "materials", []) or [])
        child_relevant = parent_dynamic or child.get("spl_dynamic_team_color", child.get("osi_dynamic_team_color", False)) or child.get("spl_team_color_enabled", child.get("osi_team_color_enabled", False)) or any(material_should_link_team_color(m, child) for m in child_mats)
        if child_relevant:
            try:
                child["spl_active_team"] = role
                _ensure_spl_active_team_first(child)
                for k in ("spl_team", "osi_team", "osi_active_team"):
                    try:
                        if k in child:
                            del child[k]
                    except Exception:
                        pass
                if hasattr(child, "spl_team_assignment"):
                    child.spl_team_assignment = role
            except Exception:
                pass
            changed += apply_team_color_to_object(child, scene, value_only=value_only, allow_material_copy=False, propagate_children=True)
    return changed


def apply_team_colors_to_scene(scene=None, value_only: bool = True, source_obj: bpy.types.Object | None = None) -> int:
    """Update imported/team-managed objects from current per-object active teams.

    The optional source object supports descendant-up edits: if the selected mesh
    or node has spl_active_team changed, Apply Team Colors promotes that value to
    the controlling actor/rail root before refreshing the subtree.
    """
    scene = scene or bpy.context.scene
    count = 0
    roots = []
    seen = set()

    if source_obj is not None:
        try:
            if source_obj.get("spl_active_team", None) is not None:
                root = _nearest_team_assignment_root(source_obj)
                if root is not None:
                    role = normalize_team_role(source_obj.get("spl_active_team"))
                    root["spl_active_team"] = role
                    _ensure_spl_active_team_first(root)
        except Exception:
            pass

    for obj in list(bpy.data.objects):
        try:
            if not _object_has_team_assignment(obj):
                continue
            root = _nearest_team_assignment_root(obj) or obj
            key = getattr(root, "name_full", root.name)
            if key in seen:
                continue
            seen.add(key)
            roots.append(root)
        except Exception:
            pass

    for root in roots:
        try:
            role = normalize_team_role(root.get("spl_active_team", root.get("bcett_team", "neutral")))
            count += _sync_active_team_to_subtree(root, role, scene, value_only=value_only)
        except Exception:
            pass

    # Also update any managed non-InkRail materials that may not currently be assigned
    # to a tagged object.  InkRail remains object-color/per-root driven.
    for mat in list(bpy.data.materials):
        if bool(mat.get("spl_team_color_managed", mat.get("osi_team_color_managed", False))):
            if _is_inkrail_mat(mat, None):
                continue
            role = mat.get("spl_team_color_role", mat.get("osi_team_color_role", "neutral"))
            if apply_team_color_to_material(mat, role, scene, require_team_params=False, value_only=value_only):
                count += 1
    return count


def link_imported_object_team_colors(objects: Iterable[bpy.types.Object], team_role: Any = None, scene=None) -> int:
    """Mark whitelisted imported objects for live team-color updates and apply the active color."""
    scene = scene or bpy.context.scene
    changed = 0
    for obj in objects or []:
        if obj is None:
            continue
        role = normalize_team_role(team_role if team_role is not None else obj.get("bcett_team", obj.get("spl_active_team", "neutral")))
        data = getattr(obj, "data", None)
        mats = list(getattr(data, "materials", []) or [])
        whitelisted = _object_or_material_whitelisted(obj, None) or any(_object_or_material_whitelisted(obj, m) for m in mats)
        dynamic = _object_or_material_dynamic(obj, None) or any(_object_or_material_dynamic(obj, m) for m in mats)
        if not (whitelisted or dynamic):
            # Preserve TeamCmp metadata, but do not recolor generic stage geometry.
            try:
                obj["bcett_team"] = role
                obj["spl_active_team"] = role
                for k in ("spl_team", "osi_team", "osi_active_team"):
                    try:
                        if k in obj:
                            del obj[k]
                    except Exception:
                        pass
            except Exception:
                pass
            continue
        if dynamic:
            # Dynamic objects now default their editable active team to the imported BCETT team.
            role = normalize_team_role(team_role if team_role is not None else obj.get("bcett_team", role))
        try:
            obj["bcett_team"] = obj.get("bcett_team", role)
            obj["spl_team_color_enabled"] = bool(whitelisted)
            obj["osi_team_color_enabled"] = bool(whitelisted)
            obj["spl_dynamic_team_color"] = bool(dynamic)
            obj["osi_dynamic_team_color"] = bool(dynamic)
            obj["spl_active_team"] = role
            _ensure_spl_active_team_first(obj)
            for k in ("spl_team", "osi_team", "osi_active_team"):
                try:
                    if k in obj:
                        del obj[k]
                except Exception:
                    pass
            if hasattr(obj, "spl_team_assignment"):
                obj.spl_team_assignment = role
        except Exception:
            pass
        changed += apply_team_color_to_object(obj, scene, allow_material_copy=True)
    return changed

def apply_selected_team_color(scene=None, create_missing_cubes: bool = False) -> bool:
    scene = scene or bpy.context.scene
    rec = _selected_record(scene)
    if not rec:
        return False
    try:
        scene["spl_team_color_selected_name"] = rec.get("name", "")
        scene["spl_team_color_selected_rowid"] = rec.get("rowid", "")
        scene["spl_team_color_selected_tags"] = rec.get("tag_label", "")
    except Exception:
        pass
    colors = rec.get("colors") or {}
    for short, _field, label in _COLOR_FIELDS:
        color = tuple(colors.get(short) or (1.0, 1.0, 1.0, 1.0))
        mat = _ensure_material("Spl3 TeamColor %s" % label, color)
        obj = bpy.data.objects.get(_TEST_CUBE_NAMES[short])
        if obj is None and create_missing_cubes:
            # Arrange the four cubes in a visible row, all translated +20 Z.
            index = [k for k, _f, _l in _COLOR_FIELDS].index(short)
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-3.0 + index * 2.0, 0.0, 20.0))
            obj = bpy.context.object
            obj.name = _TEST_CUBE_NAMES[short]
            obj.data.name = _TEST_CUBE_NAMES[short] + " Mesh"
            try:
                obj["osi_team_color_role"] = short
                obj["osi_team_color_test_cube"] = True
            except Exception:
                pass
        if obj is not None:
            obj.color = color
            if getattr(obj, "data", None) is not None and hasattr(obj.data, "materials"):
                obj.data.materials.clear()
                obj.data.materials.append(mat)
    try:
        apply_team_colors_to_scene(scene)
    except Exception:
        pass
    return True


def _team_color_update(self, context):
    try:
        apply_selected_team_color(context.scene, create_missing_cubes=False)
    except Exception:
        pass



_SPONGE_INFLATE_CACHE: Dict[str, float] = {}
_SPONGE_INFLATE_OBJECT_CACHE: Dict[str, float] = {}
_SPONGE_INFLATE_PENDING: Dict[str, tuple] = {}
_SPONGE_INFLATE_UPDATE_LOCK = False
_SPONGE_INFLATE_TIMER_ENABLED = False


def _object_key(obj):
    try:
        return str(getattr(obj, "name_full", obj.name))
    except Exception:
        return str(id(obj))


def _float_prop(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _looks_like_sponge_object(obj) -> bool:
    """Loose sponge detection for manual/debug application.

    Some v0.8.6x imports visibly expose spl_sponge_inflate on objects that are
    not carrying bcett_sponge_inflate_enabled, which made all manual fallback
    operators report 0 targets.  The manual path should be permissive: if an
    object is explicitly named/tagged like a sponge or exposes the property, let
    the apply routine attempt to find body/parts descendants.
    """
    if obj is None:
        return False
    try:
        if obj.get("spl_sponge_inflate", None) is not None:
            return True
        if obj.get("bcett_sponge_root", None) is not None:
            return True
        if bool(obj.get("bcett_sponge_inflate_enabled", False)):
            return True
    except Exception:
        pass
    try:
        combo = (str(getattr(obj, "name", "")) + " " + str(getattr(getattr(obj, "data", None), "name", "")) + " " + str(obj.get("bcett_sponge_mesh_kind", ""))).casefold()
        if "sponge" in combo:
            return True
    except Exception:
        pass
    return False


def _nearest_sponge_root(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    """Return the controlling sponge actor root for an object.

    Sponge children expose spl_sponge_inflate for descendant-up edits, but most
    of them are not the controlling root and do not carry
    bcett_sponge_inflate_enabled.  v0.8.62 could therefore treat a selected
    BFRES/model root or mesh child as its own root and skip the update because
    that object was not enabled.  Resolve the explicit bcett_sponge_root link
    first, then prefer enabled ancestors; only fall back to the object itself
    when no better root is known.
    """
    if obj is None:
        return None
    try:
        linked_name = obj.get("bcett_sponge_root", None)
        if linked_name:
            linked = bpy.data.objects.get(str(linked_name))
            if linked is not None:
                return linked
    except Exception:
        pass
    cur = obj
    prop_fallback = None
    for _ in range(96):
        if cur is None:
            break
        try:
            if bool(cur.get("bcett_sponge_inflate_enabled", False)):
                return cur
            if prop_fallback is None and cur.get("spl_sponge_inflate", None) is not None:
                prop_fallback = cur
            linked_name = cur.get("bcett_sponge_root", None)
            if linked_name:
                linked = bpy.data.objects.get(str(linked_name))
                if linked is not None:
                    return linked
        except Exception:
            pass
        cur = getattr(cur, "parent", None)
    if prop_fallback is not None:
        return prop_fallback
    try:
        if _looks_like_sponge_object(obj):
            return obj
    except Exception:
        pass
    return obj


def _sponge_descendants(root: bpy.types.Object | None):
    """Return all objects controlled by a sponge root.

    BFRES imports are usually parented under the actor root, but some import
    paths/cache duplications can leave the visible body/parts mesh or armature
    objects outside the direct descendant tree while still tagging them with
    bcett_sponge_root.  Earlier live inflate code only walked descendants, so
    a property edit could be detected and logged while no visible sponge object
    was actually mutated.  Include both descendants and globally tagged objects,
    preserving order and avoiding duplicates.
    """
    if root is None:
        return []
    result = []
    seen = set()

    def _add(obj):
        if obj is None:
            return
        try:
            key = getattr(obj, "name_full", obj.name)
        except Exception:
            key = str(id(obj))
        if key in seen:
            return
        seen.add(key)
        result.append(obj)

    _add(root)
    try:
        for obj in _iter_object_descendants(root):
            _add(obj)
    except Exception:
        pass

    root_names = set()
    try:
        root_names.add(str(root.name))
        root_names.add(str(getattr(root, "name_full", root.name)))
    except Exception:
        pass
    try:
        root_base_names = set()
        for rn in list(root_names):
            root_base_names.add(str(rn).split('.', 1)[0])
        for obj in list(bpy.data.objects):
            try:
                linked = obj.get("bcett_sponge_root", None)
            except Exception:
                linked = None
            if linked is not None and str(linked) in root_names:
                _add(obj)
                continue
            # Loose fallback for objects that lost the explicit root tag: if they
            # are parented under the root or are visibly sponge body/parts objects
            # near an untagged selected sponge root, include them.  This only runs
            # for roots already chosen by a sponge property/operator.
            try:
                if getattr(obj, "parent", None) is root:
                    _add(obj)
                    continue
                combo = (str(getattr(obj, "name", "")) + " " + str(getattr(getattr(obj, "data", None), "name", "")) + " " + str(obj.get("bcett_sponge_mesh_kind", ""))).casefold()
                if ("body__m_sponge" in combo or "parts__m_sponge" in combo or "obj_sponge" in combo) and getattr(obj, "parent", None) is not None:
                    cur = obj.parent
                    for _ in range(32):
                        if cur is root:
                            _add(obj)
                            break
                        cur = getattr(cur, "parent", None)
                        if cur is None:
                            break
            except Exception:
                pass
    except Exception:
        pass
    return result


def _sponge_bbox_half(root: bpy.types.Object | None) -> Vector:
    try:
        value = root.get("bcett_sponge_bbox_half", (0.5, 0.5, 0.5))
        return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        return Vector((0.5, 0.5, 0.5))


def _sponge_visual_offset(root: bpy.types.Object | None) -> Vector:
    try:
        value = root.get("bcett_sponge_visual_offset_local", root.get("bcett_sponge_offset_local", (0.0, 0.0, 0.0)))
        return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        return Vector((0.0, 0.0, 0.0))


def _sponge_body_center(root: bpy.types.Object | None) -> Vector:
    try:
        value = root.get("bcett_sponge_body_center_local", root.get("bcett_sponge_visual_offset_local", root.get("bcett_sponge_offset_local", (0.0, 0.0, 0.0))))
        return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        return Vector((0.0, 0.0, 0.0))


def _sponge_inflate_max_multiplier(root: bpy.types.Object | None) -> float:
    try:
        return float(root.get("bcett_sponge_inflate_max_multiplier", 2.0))
    except Exception:
        return 2.0


def _sponge_axis_low_high_with_anchors(center_value: float, half_value: float) -> tuple[float, float]:
    try:
        half_abs = abs(float(half_value))
        low = float(center_value) - half_abs
        high = float(center_value) + half_abs
    except Exception:
        return 0.0, 0.0
    span = abs(high - low)
    if span <= 1e-8:
        return 0.0, 0.0
    tol = max(1e-5, span * 0.125)
    if abs(low) <= tol and abs(high) > tol:
        return 0.0, span
    if abs(high) <= tol and abs(low) > tol:
        return -span, 0.0
    if abs(low) <= tol and abs(high) <= tol:
        return 0.0, 0.0
    return low, high


def _sponge_pose_bone_corner_root_local(root: bpy.types.Object | None, arm_obj: bpy.types.Object, pb, body_center: Vector, half: Vector) -> Vector:
    try:
        value = pb.get("bcett_sponge_corner_root_local", None)
        if value is not None and len(value) >= 3:
            return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        pass
    tail = str(getattr(pb, "name", "") or "").casefold().split("parts_", 1)[-1]
    low_x, high_x = _sponge_axis_low_high_with_anchors(body_center.x, half.x)
    low_y, high_y = _sponge_axis_low_high_with_anchors(body_center.y, half.y)
    low_z, high_z = _sponge_axis_low_high_with_anchors(body_center.z, half.z)
    # Corrected unrotated Blender-local mapping:
    # 1st char L/R -> low/high X; 2nd char B/T -> low/high Z;
    # 3rd char B/F -> high/low Y.  Collapsed sides produce zero travel and
    # their opposite side uses the full span, including vertical axes.
    x = low_x if tail.startswith("l") else (high_x if tail.startswith("r") else (high_x if abs(high_x) >= abs(low_x) else low_x))
    z = low_z if len(tail) >= 2 and tail[1] == "b" else (high_z if len(tail) >= 2 and tail[1] == "t" else (high_z if abs(high_z) >= abs(low_z) else low_z))
    y = high_y if len(tail) >= 3 and tail[2] == "b" else (low_y if len(tail) >= 3 and tail[2] == "f" else (high_y if abs(high_y) >= abs(low_y) else low_y))
    corner = Vector((x, y, z))
    try:
        rest = (pb.bone.head_local + pb.bone.tail_local) * 0.5
        rest_root = root.matrix_world.inverted() @ (arm_obj.matrix_world @ rest)
    except Exception:
        rest_root = Vector((0.0, 0.0, 0.0))
    try:
        signs = _sponge_parts_bone_signs_from_name(str(pb.name), rest_root - body_center)
        pb["bcett_sponge_rest_root_local"] = (float(rest_root.x), float(rest_root.y), float(rest_root.z))
        pb["bcett_sponge_signs"] = (float(signs.x), float(signs.y), float(signs.z))
        pb["bcett_sponge_corner_root_local"] = (float(corner.x), float(corner.y), float(corner.z))
    except Exception:
        pass
    return corner



def _sponge_parts_corner_inset(root: bpy.types.Object | None) -> float:
    """Return a small fixed inset for fixed-size sponge corner parts.

    The body mesh scales, but the corner parts do not.  Treating each parts
    bone as if it lived exactly on the scalable body corner makes the fixed
    corner pieces drift slightly outside the body as inflate increases.  This
    inset keeps the bone target slightly inside the moving body corner so the
    fixed-size piece maintains an absolute offset from that corner.
    """
    try:
        return max(0.0, float(root.get("bcett_sponge_parts_corner_inset", 0.03)))
    except Exception:
        return 0.03


def _sponge_compensate_parts_corner_root_local(root: bpy.types.Object | None, corner: Vector) -> Vector:
    """Inset non-anchored parts travel slightly toward the sponge origin.

    `corner` is a root-local body-corner travel vector before the global inflate
    multiplier is applied.  Anchored axes are exactly zero and remain zero.
    """
    try:
        inset = _sponge_parts_corner_inset(root)
        if inset <= 0.0:
            return Vector(corner)
        out = Vector(corner)
        for axis in range(3):
            value = float(out[axis])
            if abs(value) <= 1e-8:
                out[axis] = 0.0
                continue
            # Keep the correction small and never invert/collapse the travel.
            corr = min(abs(value) * 0.20, inset)
            out[axis] = value - (corr if value > 0.0 else -corr)
        return out
    except Exception:
        return Vector(corner)


def _sponge_blender_vector_to_pose_vector(vec: Vector) -> Vector:
    """Convert desired Blender-space bone vector to sponge BFRES pose space.

    Sponge pose channels use X, Z, -Y relative to Blender XYZ.  All sponge
    inflate target math remains in Blender/root space; only the final value
    written to pose_bone.location is converted here.
    """
    try:
        v = Vector(vec)
        return Vector((float(v.x), float(v.z), float(-v.y)))
    except Exception:
        return Vector((0.0, 0.0, 0.0))


def _sponge_root_delta_to_armature_pose_delta(root: bpy.types.Object | None, arm_obj: bpy.types.Object | None, delta: Vector) -> Vector:
    """Convert a sponge-root-local delta into the armature object's local/pose location space.

    Sponge inflate corner travel is computed from the sponge/body bounds in the
    sponge root's local space, where wall/floor anchored sides are detected.
    Pose-bone location, however, is evaluated in the armature object's local
    space.  For rotated sponges, writing the root-local delta directly to
    pose_bone.location sends the parts in the wrong Blender-world direction.
    """
    try:
        if root is None or arm_obj is None:
            blender_local = Vector((float(delta.x), float(delta.y), float(delta.z)))
        else:
            world_delta = root.matrix_world.to_3x3() @ delta
            blender_local = arm_obj.matrix_world.inverted().to_3x3() @ world_delta
        return _sponge_blender_vector_to_pose_vector(blender_local)
    except Exception:
        return _sponge_blender_vector_to_pose_vector(Vector((float(delta.x), float(delta.y), float(delta.z))))


def _sponge_vector_world_from_root(root: bpy.types.Object | None, delta: Vector) -> Vector:
    try:
        if root is None:
            return Vector((float(delta.x), float(delta.y), float(delta.z)))
        return root.matrix_world.to_3x3() @ delta
    except Exception:
        return Vector((float(delta.x), float(delta.y), float(delta.z)))

def _sponge_parts_bone_signs_from_name(name: str, base: Vector) -> Vector:
    tail = str(name or "").casefold().split("parts_", 1)[-1]
    # Fallback sign semantics.  Actual inflate deltas use body-bound targets so
    # an anchored side can correctly have zero movement.
    sx = -1.0 if tail.startswith("l") else (1.0 if tail.startswith("r") else (1.0 if base.x >= 0.0 else -1.0))
    sy = 1.0 if len(tail) >= 3 and tail[2] == "b" else (-1.0 if len(tail) >= 3 and tail[2] == "f" else (1.0 if base.y >= 0.0 else -1.0))
    sz = 1.0 if len(tail) >= 2 and tail[1] == "t" else (-1.0 if len(tail) >= 2 and tail[1] == "b" else (1.0 if base.z >= 0.0 else -1.0))
    return Vector((sx, sy, sz))



def _clear_sponge_inflate_driver_target(id_owner, data_path: str, indices=(0, 1, 2)) -> int:
    removed = 0
    for idx in indices:
        try:
            id_owner.driver_remove(data_path, idx)
            removed += 1
        except Exception:
            pass
    return removed

def _sponge_base_scale(obj: bpy.types.Object) -> Vector:
    try:
        value = obj.get("bcett_sponge_base_scale", None)
        if value is not None and len(value) >= 3:
            return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        pass
    try:
        obj["bcett_sponge_base_scale"] = (float(obj.scale.x), float(obj.scale.y), float(obj.scale.z))
    except Exception:
        pass
    return Vector((float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)))



def _sponge_pose_bone_base_location(pb) -> Vector:
    try:
        value = pb.get("bcett_sponge_base_location", None)
        if value is not None and len(value) >= 3:
            return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        pass
    loc = Vector(pb.location)
    try:
        pb["bcett_sponge_base_location"] = (float(loc.x), float(loc.y), float(loc.z))
    except Exception:
        pass
    return loc


def _sponge_debug_enabled(root=None) -> bool:
    try:
        if root is not None and bool(root.get("bcett_sponge_debug_inflate", False)):
            return True
    except Exception:
        pass
    try:
        scene = getattr(bpy.context, "scene", None)
        if scene is not None and bool(scene.get("bcett_sponge_debug_inflate", False)):
            return True
    except Exception:
        pass
    return False


def _sponge_obj_summary(obj):
    try:
        return "%s:%s loc=%s scale=%s parent=%s root_tag=%s kind=%s" % (
            obj.name,
            getattr(obj, "type", None),
            tuple(round(float(v), 6) for v in getattr(obj, "location", (0, 0, 0))),
            tuple(round(float(v), 6) for v in getattr(obj, "scale", (0, 0, 0))),
            getattr(getattr(obj, "parent", None), "name", None),
            obj.get("bcett_sponge_root", None),
            obj.get("bcett_sponge_mesh_kind", None),
        )
    except Exception as exc:
        return "<summary failed %r>" % (exc,)


_SPONGE_INFO_LINES = []


def _sponge_info_line(message):
    try:
        text = str(message)
    except Exception:
        text = repr(message)
    # Keep Info output readable: store all lines for explicit operators, but
    # print only concise summary/failure lines unless debug is enabled.
    _SPONGE_INFO_LINES.append(text)
    try:
        scene = getattr(bpy.context, "scene", None)
        debug = bool(scene is not None and scene.get("bcett_sponge_debug_inflate", False))
    except Exception:
        debug = False
    if debug or text.startswith(("SPONGE_APPLY_END", "SPONGE_EXPLICIT_END", "SPONGE_BODY_APPLY_FAILED", "SPONGE_ARMATURE_APPLY_FAILED", "SPONGE_PARTS_MESH_RESET_FAILED")):
        try:
            print("OSI " + text)
        except Exception:
            pass


def _drain_sponge_info_lines(limit=48):
    out = list(_SPONGE_INFO_LINES[:limit])
    try:
        del _SPONGE_INFO_LINES[:]
    except Exception:
        pass
    return out


def _collect_sponge_inflate_targets(root):
    """Return explicit body mesh, parts mesh, and parts armature targets.

    This is intentionally more deterministic than the older target count path:
    diagnostics proved that _sponge_descendants(root) can find the relevant
    objects, but the final apply still returned zero.  Classify the discovered
    objects once and apply directly to the classified targets.
    """
    body_meshes = []
    parts_meshes = []
    parts_armatures = []
    seen = set()

    def _add_unique(seq, obj):
        if obj is None:
            return
        key = getattr(obj, "name_full", getattr(obj, "name", str(id(obj))))
        if key in seen and seq is not parts_armatures:
            return
        if obj not in seq:
            seq.append(obj)

    for obj in _sponge_descendants(root):
        try:
            otype = getattr(obj, "type", None)
            name = str(getattr(obj, "name", ""))
            data_name = str(getattr(getattr(obj, "data", None), "name", ""))
            kind = str(obj.get("bcett_sponge_mesh_kind", ""))
            combo = (name + " " + data_name + " " + kind).casefold()
            if otype == "MESH":
                if kind.casefold() == "body" or "body__m_sponge" in combo:
                    _add_unique(body_meshes, obj)
                elif kind.casefold() == "parts" or "parts__m_sponge" in combo:
                    _add_unique(parts_meshes, obj)
                elif "body" in combo and "parts" not in combo:
                    _add_unique(body_meshes, obj)
            elif otype == "ARMATURE":
                try:
                    if any(str(pb.name).startswith("parts_") for pb in obj.pose.bones):
                        _add_unique(parts_armatures, obj)
                except Exception:
                    pass
        except Exception as exc:
            _sponge_info_line("SPONGE_APPLY_CLASSIFY_FAILED obj=%r error=%r" % (getattr(obj, "name", None), exc))
    return body_meshes, parts_meshes, parts_armatures

def _sponge_debug_print(root, message):
    try:
        if _sponge_debug_enabled(root):
            print("OSI SPONGE_DEBUG " + str(message))
    except Exception:
        pass


def apply_sponge_inflate_to_root(root: bpy.types.Object | None, inflate: float) -> int:
    """Approximate sponge inflation using direct body scale and parts bones.

    v0.8.68 diagnostics showed target discovery found body__M_Sponge,
    parts__M_Sponge, and Obj_Sponge, but the final apply returned 0.  This
    version applies to explicitly classified targets and reports the exact
    target names through operator-visible diagnostics.
    """
    if root is None:
        _sponge_info_line("SPONGE_APPLY_ABORT root=None")
        return 0
    try:
        inflate = max(0.0, min(1.0, float(inflate)))
    except Exception:
        inflate = 0.0

    half = _sponge_bbox_half(root)
    max_mult = _sponge_inflate_max_multiplier(root)
    body_center = _sponge_body_center(root)
    scale_factor = 1.0 + max_mult * inflate
    controlled_objects = _sponge_descendants(root)
    body_meshes, parts_meshes, parts_armatures = _collect_sponge_inflate_targets(root)
    try:
        root["bcett_sponge_inflate_controlled_count"] = int(len(controlled_objects))
        root["bcett_sponge_inflate_body_targets"] = ",".join(obj.name for obj in body_meshes)
        root["bcett_sponge_inflate_parts_mesh_targets"] = ",".join(obj.name for obj in parts_meshes)
        root["bcett_sponge_inflate_parts_armatures"] = ",".join(obj.name for obj in parts_armatures)
    except Exception:
        pass

    _sponge_info_line(
        "SPONGE_APPLY_BEGIN root=%r value=%.4f controlled=%d body=%r parts_mesh=%r parts_arm=%r scale_factor=%.4f" % (
            getattr(root, "name", None), inflate, len(controlled_objects),
            [obj.name for obj in body_meshes], [obj.name for obj in parts_meshes],
            [obj.name for obj in parts_armatures], scale_factor))

    changed = 0
    for obj in body_meshes:
        try:
            _clear_sponge_inflate_driver_target(obj, "scale", (0, 1, 2))
            base = _sponge_base_scale(obj)
            before = tuple(round(float(v), 6) for v in obj.scale)
            obj.scale = Vector((base.x * scale_factor, base.y * scale_factor, base.z * scale_factor))
            obj.update_tag(refresh={'OBJECT'})
            after = tuple(round(float(v), 6) for v in obj.scale)
            _sponge_info_line("SPONGE_BODY_APPLY_ORIGIN_OFFSET_SCALE obj=%r scale_factor=%.4f before=%s after=%s" % (
                obj.name, scale_factor, before, after))
            changed += 1
        except Exception as exc:
            _sponge_info_line("SPONGE_BODY_APPLY_FAILED obj=%r error=%r" % (getattr(obj, "name", None), exc))

    for obj in parts_meshes:
        try:
            _clear_sponge_inflate_driver_target(obj, "scale", (0, 1, 2))
            base = _sponge_base_scale(obj)
            before = tuple(round(float(v), 6) for v in obj.scale)
            obj.scale = Vector((base.x, base.y, base.z))
            obj.update_tag(refresh={'OBJECT'})
            after = tuple(round(float(v), 6) for v in obj.scale)
            _sponge_info_line("SPONGE_PARTS_MESH_RESET obj=%r base=%s before=%s after=%s" % (
                obj.name, tuple(round(float(v), 6) for v in base), before, after))
            changed += 1
        except Exception as exc:
            _sponge_info_line("SPONGE_PARTS_MESH_RESET_FAILED obj=%r error=%r" % (getattr(obj, "name", None), exc))

    for obj in parts_armatures:
        applied_bones = 0
        try:
            for pb in obj.pose.bones:
                name = str(pb.name)
                if not name.startswith("parts_"):
                    continue
                _clear_sponge_inflate_driver_target(pb, "location", (0, 1, 2))
                before = tuple(round(float(v), 6) for v in pb.location)
                base = _sponge_pose_bone_base_location(pb)
                corner_root_raw = _sponge_pose_bone_corner_root_local(root, obj, pb, body_center, half)
                corner_root = _sponge_compensate_parts_corner_root_local(root, corner_root_raw)
                corner_pose = _sponge_root_delta_to_armature_pose_delta(root, obj, corner_root)
                target = base + (corner_pose * (max_mult * inflate))
                pb.location = target
                after = tuple(round(float(v), 6) for v in pb.location)
                corner_world = _sponge_vector_world_from_root(root, corner_root)
                if _sponge_debug_enabled(root):
                    _sponge_info_line("SPONGE_BONE_APPLY arm=%r bone=%r base=%s corner_root=%s corner_pose=%s corner_world=%s before=%s after=%s" % (
                        obj.name, name, tuple(round(float(v), 6) for v in base),
                        tuple(round(float(v), 6) for v in corner_root),
                        tuple(round(float(v), 6) for v in corner_pose),
                        tuple(round(float(v), 6) for v in corner_world), before, after))
                applied_bones += 1
            obj.update_tag(refresh={'OBJECT'})
            changed += applied_bones
            _sponge_info_line("SPONGE_ARMATURE_APPLY arm=%r bones=%d" % (obj.name, applied_bones))
        except Exception as exc:
            _sponge_info_line("SPONGE_ARMATURE_APPLY_FAILED obj=%r error=%r" % (getattr(obj, "name", None), exc))

    try:
        root["spl_sponge_inflate"] = inflate
        root["bcett_sponge_inflate_last_changed"] = int(changed)
    except Exception:
        pass
    try:
        root.update_tag(refresh={'OBJECT'})
    except Exception:
        pass
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
    except Exception:
        pass
    _sponge_info_line("SPONGE_APPLY_END root=%r value=%.4f changed=%d" % (getattr(root, "name", None), inflate, changed))
    return changed


def _set_object_sponge_inflate_idprop(obj, value: float):
    global _SPONGE_INFLATE_UPDATE_LOCK
    if obj is None:
        return
    try:
        _SPONGE_INFLATE_UPDATE_LOCK = True
        obj["spl_sponge_inflate"] = float(value)
        try:
            obj.id_properties_ui("spl_sponge_inflate").update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description="Approximate sponge inflation")
        except Exception:
            pass
    finally:
        _SPONGE_INFLATE_UPDATE_LOCK = False
_SPONGE_INFLATE_TIMER_ENABLED = False


def _object_sponge_inflate_property_update(self, context):
    """RNA update callback for the visible Object.spl_sponge_inflate property.

    Imported sponges still expose the ID custom property users have been editing.
    This callback mirrors the RNA value into that custom property and immediately
    updates the nearest sponge root.  The polling path below remains necessary
    for direct edits to the Custom Properties list, because ID properties do not
    reliably run RNA update callbacks.
    """
    if _SPONGE_INFLATE_UPDATE_LOCK:
        return
    try:
        value = max(0.0, min(1.0, float(getattr(self, "spl_sponge_inflate", 0.0))))
    except Exception:
        value = 0.0
    root = _nearest_sponge_root(self)
    if root is None:
        return
    try:
        _set_object_sponge_inflate_idprop(self, value)
        _set_object_sponge_inflate_idprop(root, value)
        changed = apply_sponge_inflate_to_root(root, value)
        _SPONGE_INFLATE_CACHE[_object_key(root)] = value
        _SPONGE_INFLATE_OBJECT_CACHE[_object_key(self)] = value
        for child in _sponge_descendants(root):
            try:
                if child is not self:
                    _set_object_sponge_inflate_idprop(child, value)
                    if hasattr(child, "spl_sponge_inflate"):
                        setattr(child, "spl_sponge_inflate", value)
                    _SPONGE_INFLATE_OBJECT_CACHE[_object_key(child)] = value
            except Exception:
                pass
        try:
            if changed:
                root.update_tag(refresh={'OBJECT'})
                for child in _sponge_descendants(root):
                    child.update_tag(refresh={'OBJECT'})
        except Exception:
            pass
    except Exception:
        pass


def _apply_sponge_inflate_value_to_tree(root: bpy.types.Object | None, value: float, source_obj: bpy.types.Object | None = None) -> int:
    """Apply a sponge inflate value and mirror it to visible ID custom properties.

    This helper intentionally does not rely on Blender RNA properties or drivers.
    It is safe for timer/depsgraph polling of the visible ID custom property that
    users edit in Object Properties > Custom Properties or with console syntax
    like: bpy.context.object["spl_sponge_inflate"] = 1.
    """
    if root is None:
        return 0
    try:
        value = max(0.0, min(1.0, float(value)))
    except Exception:
        value = 0.0
    changed = apply_sponge_inflate_to_root(root, value)
    try:
        root["bcett_sponge_inflate_applied_value"] = float(value)
    except Exception:
        pass
    try:
        _SPONGE_INFLATE_CACHE[_object_key(root)] = float(value)
    except Exception:
        pass
    for child in _sponge_descendants(root):
        try:
            _set_object_sponge_inflate_idprop(child, value)
            _SPONGE_INFLATE_OBJECT_CACHE[_object_key(child)] = float(value)
        except Exception:
            pass
    try:
        root.update_tag(refresh={'OBJECT'})
        for child in _sponge_descendants(root):
            child.update_tag(refresh={'OBJECT'})
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    except Exception:
        pass
    return changed


def _queue_sponge_inflate_changes(scene=None) -> int:
    """Detect spl_sponge_inflate edits and queue sponge updates.

    This now mirrors the spl_active_team design: the depsgraph/timer path only
    detects user-visible ID-property changes and records pending work.  The
    timer/render_pre path performs the actual mutations.  This is safer than
    updating armatures directly from the detector, and it lets sponge inflation
    reuse the same proven live-update cadence as team colors.
    """
    queued = 0
    try:
        active_obj = getattr(bpy.context, "object", None)
    except Exception:
        active_obj = None

    def _idprop_value(obj, default=0.0):
        try:
            if obj is not None and obj.get("spl_sponge_inflate", None) is not None:
                return max(0.0, min(1.0, _float_prop(obj.get("spl_sponge_inflate", default), default)))
        except Exception:
            pass
        return max(0.0, min(1.0, _float_prop(default, 0.0)))

    # Prefer the active object when it belongs to a sponge.  Console edits like
    # bpy.context.object["spl_sponge_inflate"] = 1 do not always produce a
    # reliable ID-property update event, so the active object's value is treated
    # as authoritative each tick.
    try:
        active_root = _nearest_sponge_root(active_obj) if active_obj is not None else None
        if active_root is not None and (bool(active_root.get("bcett_sponge_inflate_enabled", False)) or _looks_like_sponge_object(active_root) or _looks_like_sponge_object(active_obj)) and active_obj.get("spl_sponge_inflate", None) is not None:
            value = _idprop_value(active_obj, _idprop_value(active_root, 0.0))
            applied = _float_prop(active_root.get("bcett_sponge_inflate_applied_value", _SPONGE_INFLATE_CACHE.get(_object_key(active_root), -9999.0)), -9999.0)
            obj_key = _object_key(active_obj)
            obj_cached = _SPONGE_INFLATE_OBJECT_CACHE.get(obj_key, None)
            if obj_cached is None or abs(float(value) - float(obj_cached)) > 1e-6 or abs(float(value) - float(applied)) > 1e-6:
                _SPONGE_INFLATE_PENDING[_object_key(active_root)] = (active_root, value, active_obj)
                if _sponge_debug_enabled(active_root):
                    _sponge_debug_print(active_root, "QUEUE_ACTIVE root=%r source=%r value=%.4f cached=%r applied=%.4f" % (getattr(active_root, "name", None), getattr(active_obj, "name", None), value, obj_cached, applied))
                queued += 1
    except Exception:
        pass

    # Scan all sponge properties exactly like spl_active_team does.  Any edited
    # descendant can queue the controlling sponge root; the edited object wins.
    for obj in list(bpy.data.objects):
        try:
            if obj.get("spl_sponge_inflate", None) is None:
                continue
            root = _nearest_sponge_root(obj)
            if root is None or not (bool(root.get("bcett_sponge_inflate_enabled", False)) or _looks_like_sponge_object(root) or _looks_like_sponge_object(obj)):
                continue
            try:
                root["bcett_sponge_inflate_enabled"] = True
            except Exception:
                pass
            value = _idprop_value(obj, _idprop_value(root, 0.0))
            key = _object_key(obj)
            cached = _SPONGE_INFLATE_OBJECT_CACHE.get(key, None)
            applied = _float_prop(root.get("bcett_sponge_inflate_applied_value", _SPONGE_INFLATE_CACHE.get(_object_key(root), -9999.0)), -9999.0)
            if cached is not None and abs(float(value) - float(cached)) <= 1e-6 and abs(float(value) - float(applied)) <= 1e-6:
                continue
            _SPONGE_INFLATE_PENDING[_object_key(root)] = (root, value, obj)
            if _sponge_debug_enabled(root):
                _sponge_debug_print(root, "QUEUE_SCAN root=%r source=%r value=%.4f cached=%r applied=%.4f" % (getattr(root, "name", None), getattr(obj, "name", None), value, cached, applied))
            queued += 1
        except Exception:
            pass
    return queued


def _process_pending_sponge_inflate_changes(scene=None, force: bool = False) -> int:
    """Apply queued sponge inflate updates outside depsgraph callbacks."""
    if _ACTIVE_TEAM_RENDERING and not force:
        return 0
    if not _SPONGE_INFLATE_PENDING:
        return 0
    pending = list(_SPONGE_INFLATE_PENDING.values())
    _SPONGE_INFLATE_PENDING.clear()
    changed_total = 0
    for root, value, source_obj in pending:
        try:
            # Resolve the root from the source object again in case undo or
            # selection changes altered the hierarchy since queueing.
            if source_obj is not None and getattr(source_obj, "name", None) in bpy.data.objects:
                root = _nearest_sponge_root(source_obj) or root
            if root is None or getattr(root, "name", None) not in bpy.data.objects:
                continue
            queued_value = max(0.0, min(1.0, float(value)))
            # Pending entries can be stale: explicit/manual apply may have already
            # written a newer value to the root or edited child.  Re-read the
            # current visible custom property at apply time so an old queued 0.0
            # cannot overwrite the selected sponge after a manual/nonzero apply.
            current_value = _explicit_sponge_inflate_value(root, source_obj)
            applied_value = _float_prop(root.get("bcett_sponge_inflate_applied_value", _SPONGE_INFLATE_CACHE.get(_object_key(root), current_value)), current_value)
            if abs(float(current_value) - float(applied_value)) <= 1e-6 and abs(float(queued_value) - float(current_value)) > 1e-6:
                continue
            value = current_value
            changed = _apply_sponge_inflate_value_to_tree(root, value, source_obj)
            changed_total += changed
            try:
                print("OSI SPONGE_INFLATE_APPLY root=%r source=%r value=%.4f changed=%d controlled_count=%r" % (root.name, getattr(source_obj, "name", None), value, changed, root.get("bcett_sponge_inflate_controlled_count", None)))
            except Exception:
                pass
        except ReferenceError:
            pass
        except Exception as exc:
            try:
                print("OSI SPONGE_INFLATE_APPLY_FAILED root=%r error=%r" % (getattr(root, "name", None), exc))
            except Exception:
                pass
    return changed_total

def _sponge_inflate_timer():
    if not _SPONGE_INFLATE_TIMER_ENABLED:
        return None
    try:
        if not _ACTIVE_TEAM_RENDERING:
            _queue_sponge_inflate_changes(getattr(bpy.context, "scene", None))
            _process_pending_sponge_inflate_changes(getattr(bpy.context, "scene", None), force=False)
    except Exception:
        pass
    return 0.10


_ACTIVE_TEAM_CACHE: Dict[str, str] = {}
_ACTIVE_TEAM_PENDING: Dict[str, tuple] = {}
_ACTIVE_TEAM_HANDLER_RUNNING = False
_ACTIVE_TEAM_TIMER_ENABLED = False
_ACTIVE_TEAM_RENDERING = False


def _cache_active_team_subtree(root: bpy.types.Object | None) -> None:
    """Record current spl_active_team values after a propagation pass.

    This keeps descendant-up edits repeatable.  Without refreshing the cache for
    every touched descendant, a child could be considered already handled after
    the first edit and subsequent edits on that same child would be ignored.
    """
    if root is None:
        return
    for obj in [root] + list(_iter_object_descendants(root)):
        try:
            if obj.get("spl_active_team", None) is None:
                continue
            key = str(getattr(obj, "name_full", obj.name))
            _ACTIVE_TEAM_CACHE[key] = normalize_team_role(obj.get("spl_active_team"))
        except Exception:
            pass


def _queue_active_team_custom_prop_changes(scene=None) -> int:
    """Detect spl_active_team edits and queue subtree updates.

    This function intentionally does not mutate object trees, materials, or view
    layers.  It is safe to call from a depsgraph handler because all real work is
    deferred to a timer or render_pre handler.
    """
    queued = 0
    for obj in list(bpy.data.objects):
        try:
            if obj.get("spl_active_team", None) is None:
                continue
            role = normalize_team_role(obj.get("spl_active_team"))
            key = str(getattr(obj, "name_full", obj.name))
            if _ACTIVE_TEAM_CACHE.get(key) == role:
                continue
            root = _nearest_team_assignment_root(obj) or obj
            root_key = str(getattr(root, "name_full", root.name))
            # Descendant-up behavior: the edited object's value wins for the
            # controlling actor/rail root.  Store the source object so repeated
            # edits on the same child remain valid.
            _ACTIVE_TEAM_PENDING[root_key] = (root, role, obj)
            queued += 1
        except Exception:
            pass
    return queued


def _process_pending_active_team_changes(scene=None, force: bool = False) -> int:
    """Apply queued team changes outside depsgraph update callbacks."""
    global _ACTIVE_TEAM_HANDLER_RUNNING
    if _ACTIVE_TEAM_HANDLER_RUNNING:
        return 0
    if _ACTIVE_TEAM_RENDERING and not force:
        return 0
    if not _ACTIVE_TEAM_PENDING:
        return 0
    _ACTIVE_TEAM_HANDLER_RUNNING = True
    changed = 0
    try:
        scn = scene or getattr(bpy.context, "scene", None)
        pending = list(_ACTIVE_TEAM_PENDING.values())
        _ACTIVE_TEAM_PENDING.clear()
        for root, role, source_obj in pending:
            try:
                # Resolve again in case undo/delete changed object identity.
                if source_obj is not None and getattr(source_obj, "name", None) in bpy.data.objects:
                    root = _nearest_team_assignment_root(source_obj) or root
                if root is None or getattr(root, "name", None) not in bpy.data.objects:
                    continue
                role = normalize_team_role(role)
                root["spl_active_team"] = role
                _ensure_spl_active_team_first(root)
                changed += _sync_active_team_to_subtree(root, role, scn, value_only=True)
                _cache_active_team_subtree(root)
            except ReferenceError:
                pass
            except Exception:
                pass
    finally:
        _ACTIVE_TEAM_HANDLER_RUNNING = False
    return changed


def _watch_active_team_custom_props(scene=None, depsgraph=None):
    """Queue colors when a user edits spl_active_team.

    ID custom properties do not have a normal RNA update callback.  Earlier
    builds performed the full color update inside depsgraph_update_post, which
    could mutate objects/materials while Blender was building render depsgraphs.
    That is unsafe.  This watcher only queues work; a timer performs mutations
    once Blender returns to the main event loop.
    """
    try:
        _queue_active_team_custom_prop_changes(scene)
        _queue_sponge_inflate_changes(scene)
    except Exception:
        pass


def _active_team_timer():
    if not _ACTIVE_TEAM_TIMER_ENABLED:
        return None
    try:
        _queue_active_team_custom_prop_changes(getattr(bpy.context, "scene", None))
        _process_pending_active_team_changes(getattr(bpy.context, "scene", None), force=False)
        if not _ACTIVE_TEAM_RENDERING:
            _queue_sponge_inflate_changes(getattr(bpy.context, "scene", None))
            _process_pending_sponge_inflate_changes(getattr(bpy.context, "scene", None), force=False)
    except Exception:
        pass
    return 0.25


def _active_team_render_pre(scene=None, depsgraph=None):
    """Flush pending team edits before render, then freeze live watcher work."""
    global _ACTIVE_TEAM_RENDERING
    try:
        _queue_active_team_custom_prop_changes(scene)
        _process_pending_active_team_changes(scene, force=True)
        _queue_sponge_inflate_changes(scene)
        _process_pending_sponge_inflate_changes(scene, force=True)
    except Exception:
        pass
    _ACTIVE_TEAM_RENDERING = True


def _active_team_render_done(scene=None, depsgraph=None):
    global _ACTIVE_TEAM_RENDERING
    _ACTIVE_TEAM_RENDERING = False


def _append_handler_once(handlers, fn):
    if fn not in handlers:
        handlers.append(fn)


def _remove_handler_all(handlers, fn):
    while fn in handlers:
        handlers.remove(fn)



def _ensure_osi_live_update_handlers(scene=None):
    try:
        _install_active_team_handler()
    except Exception:
        pass

def _install_active_team_handler():
    global _ACTIVE_TEAM_TIMER_ENABLED, _SPONGE_INFLATE_TIMER_ENABLED
    _append_handler_once(bpy.app.handlers.depsgraph_update_post, _watch_active_team_custom_props)
    try:
        _append_handler_once(bpy.app.handlers.load_post, _ensure_osi_live_update_handlers)
    except Exception:
        pass
    _append_handler_once(bpy.app.handlers.render_pre, _active_team_render_pre)
    _append_handler_once(bpy.app.handlers.render_post, _active_team_render_done)
    _append_handler_once(bpy.app.handlers.render_cancel, _active_team_render_done)
    _ACTIVE_TEAM_TIMER_ENABLED = True
    _SPONGE_INFLATE_TIMER_ENABLED = True
    try:
        if not bpy.app.timers.is_registered(_active_team_timer):
            bpy.app.timers.register(_active_team_timer, first_interval=0.25, persistent=True)
    except Exception:
        try:
            bpy.app.timers.register(_active_team_timer, first_interval=0.25)
        except Exception:
            pass

    try:
        if not bpy.app.timers.is_registered(_sponge_inflate_timer):
            bpy.app.timers.register(_sponge_inflate_timer, first_interval=0.10, persistent=True)
    except Exception:
        try:
            bpy.app.timers.register(_sponge_inflate_timer, first_interval=0.10)
        except Exception:
            pass


def _remove_active_team_handler():
    global _ACTIVE_TEAM_TIMER_ENABLED, _ACTIVE_TEAM_RENDERING, _SPONGE_INFLATE_TIMER_ENABLED
    _ACTIVE_TEAM_TIMER_ENABLED = False
    _SPONGE_INFLATE_TIMER_ENABLED = False
    _ACTIVE_TEAM_RENDERING = False
    try:
        _SPONGE_INFLATE_PENDING.clear()
    except Exception:
        pass
    _remove_handler_all(bpy.app.handlers.depsgraph_update_post, _watch_active_team_custom_props)
    try:
        _remove_handler_all(bpy.app.handlers.load_post, _ensure_osi_live_update_handlers)
    except Exception:
        pass
    _remove_handler_all(bpy.app.handlers.render_pre, _active_team_render_pre)
    _remove_handler_all(bpy.app.handlers.render_post, _active_team_render_done)
    _remove_handler_all(bpy.app.handlers.render_cancel, _active_team_render_done)
    try:
        if bpy.app.timers.is_registered(_active_team_timer):
            bpy.app.timers.unregister(_active_team_timer)
    except Exception:
        pass
    try:
        if bpy.app.timers.is_registered(_sponge_inflate_timer):
            bpy.app.timers.unregister(_sponge_inflate_timer)
    except Exception:
        pass

def _object_team_assignment_update(self, context):
    try:
        value = normalize_team_role(getattr(self, "spl_team_assignment", self.get("spl_active_team", "neutral")))
        self["spl_active_team"] = value
        _ensure_spl_active_team_first(self)
        for k in ("spl_team", "osi_team", "osi_active_team"):
            try:
                if k in self:
                    del self[k]
            except Exception:
                pass
        root = _nearest_team_assignment_root(self) or self
        _sync_active_team_to_subtree(root, value, context.scene, value_only=True)
    except Exception:
        pass


class OSI_OT_reload_team_colors(bpy.types.Operator):
    bl_idname = "osi.reload_team_colors"
    bl_label = "Reload Team Colors"
    bl_description = "Reload TeamColorDataSet rows from the selected Splatoon 3 RomFS RSDB folder"

    def execute(self, context):
        prev_id = getattr(context.scene, "spl_team_color_set", getattr(context.scene, "osi_team_color_set", ""))
        prev_rowid = ""
        try:
            prev_rec = (_TEAM_COLOR_CACHE.get("by_id") or {}).get(prev_id)
            if prev_rec:
                prev_rowid = prev_rec.get("rowid", "")
        except Exception:
            pass
        records = load_team_color_records(True)
        if records:
            chosen = None
            for rec in records:
                if rec.get("id") == prev_id or (prev_rowid and rec.get("rowid") == prev_rowid):
                    chosen = rec
                    break
            if chosen is None and not prev_id:
                chosen = _default_team_color_record(records)
            if chosen is not None:
                try:
                    if hasattr(context.scene, "spl_team_color_set"):
                        context.scene.spl_team_color_set = chosen["id"]
                    if hasattr(context.scene, "osi_team_color_set"):
                        context.scene.osi_team_color_set = chosen["id"]
                except Exception:
                    pass
            # Preserve the current selector if the previous row cannot be found; only refresh colors.
            apply_selected_team_color(context.scene, create_missing_cubes=False)
            self.report({"INFO"}, "Loaded %d TeamColorDataSet rows" % len(records))
            return {"FINISHED"}
        self.report({"WARNING"}, _TEAM_COLOR_CACHE.get("error", "No TeamColorDataSet rows loaded"))
        return {"CANCELLED"}


class OSI_OT_reload_object_teams(bpy.types.Operator):
    bl_idname = "osi.reload_object_teams"
    bl_label = "Reload Object Teams"
    bl_description = "Reset whitelisted/dynamic objects to their imported TeamCmp/default active teams, then reapply colors"

    def execute(self, context):
        changed = 0
        for obj in list(bpy.data.objects):
            if obj.get("spl_team_color_enabled", obj.get("osi_team_color_enabled", False)) or obj.get("spl_dynamic_team_color", obj.get("osi_dynamic_team_color", False)):
                role = normalize_team_role(obj.get("bcett_team", "neutral"))
                _sync_object_team_enum(obj, role, update_custom=True)
                changed += apply_team_color_to_object(obj, context.scene, value_only=True)
        self.report({"INFO"}, "Reloaded %d object team assignment(s)" % changed)
        return {"FINISHED"}




def sponge_inflate_info_diagnostics(source_obj=None, selected_only: bool = False, max_names: int = 14):
    """Return concise sponge diagnostics for Blender Info reports."""
    lines = []
    try:
        root = _nearest_sponge_root(source_obj) if source_obj is not None else None
        value = _explicit_sponge_inflate_value(root, source_obj) if root is not None else 0.0
        body_meshes, parts_meshes, parts_armatures = _collect_sponge_inflate_targets(root) if root is not None else ([], [], [])
        lines.append("SPONGE_DIAG selected=%r root=%r value=%.4f selected_only=%r" % (
            getattr(source_obj, "name", None), getattr(root, "name", None), value, bool(selected_only)))
        if root is not None:
            lines.append("SPONGE_DIAG targets body=%r parts_mesh=%r parts_arm=%r" % (
                [getattr(o, "name", None) for o in body_meshes],
                [getattr(o, "name", None) for o in parts_meshes],
                [getattr(o, "name", None) for o in parts_armatures]))
            lines.append("SPONGE_DIAG props root_value=%r applied=%r enabled=%r" % (
                root.get("spl_sponge_inflate", None),
                root.get("bcett_sponge_inflate_applied_value", None),
                root.get("bcett_sponge_inflate_enabled", None)))
        else:
            lines.append("SPONGE_DIAG no sponge root resolved")
    except Exception as exc:
        lines.append("SPONGE_DIAG fatal_error=%r" % (exc,))
    cleaned = []
    for line in lines:
        text = str(line)
        if len(text) > 900:
            text = text[:897] + "..."
        cleaned.append(text)
    return cleaned

def apply_sponge_inflate_from_scene(scene=None, source_obj=None, selected_only: bool = False) -> int:
    """Manual and button-driven fallback for applying sponge inflation.

    ID custom-property changes do not always wake Blender's depsgraph/timer in
    every editing path. This function intentionally performs a direct scan and
    apply, and is used by explicit operators and the Apply Team Colors refresh
    path. It also emits console diagnostics so we can distinguish "watcher did
    not fire" from "apply fired but targeted the wrong objects".
    """
    roots = {}
    try:
        if selected_only and source_obj is not None:
            root = _nearest_sponge_root(source_obj)
            if root is not None and (bool(root.get("bcett_sponge_inflate_enabled", False)) or _looks_like_sponge_object(root) or _looks_like_sponge_object(source_obj)):
                try:
                    root["bcett_sponge_inflate_enabled"] = True
                except Exception:
                    pass
                roots[_object_key(root)] = (root, source_obj)
        else:
            for obj in list(bpy.data.objects):
                try:
                    if not _looks_like_sponge_object(obj):
                        continue
                    root = _nearest_sponge_root(obj)
                    if root is None or not (bool(root.get("bcett_sponge_inflate_enabled", False)) or _looks_like_sponge_object(root) or _looks_like_sponge_object(obj)):
                        continue
                    try:
                        root["bcett_sponge_inflate_enabled"] = True
                    except Exception:
                        pass
                    roots[_object_key(root)] = (root, obj)
                except Exception:
                    pass
            if source_obj is not None:
                root = _nearest_sponge_root(source_obj)
                if root is not None and (bool(root.get("bcett_sponge_inflate_enabled", False)) or _looks_like_sponge_object(root) or _looks_like_sponge_object(source_obj)):
                    try:
                        root["bcett_sponge_inflate_enabled"] = True
                    except Exception:
                        pass
                    roots[_object_key(root)] = (root, source_obj)
    except Exception as exc:
        try:
            print("OSI SPONGE_MANUAL_SCAN_FAILED error=%r" % (exc,))
        except Exception:
            pass
    changed_total = 0
    for root, src in list(roots.values()):
        try:
            value_src = src if src is not None and src.get("spl_sponge_inflate", None) is not None else root
            value = _float_prop(value_src.get("spl_sponge_inflate", root.get("spl_sponge_inflate", 0.0)), 0.0)
            value = max(0.0, min(1.0, float(value)))
            changed = _apply_sponge_inflate_value_to_tree(root, value, src)
            changed_total += changed
            try:
                print("OSI SPONGE_MANUAL_APPLY root=%r source=%r value=%.4f changed=%d controlled_count=%r" % (
                    getattr(root, "name", None), getattr(src, "name", None), value, changed, root.get("bcett_sponge_inflate_controlled_count", None)))
            except Exception:
                pass
        except Exception as exc:
            try:
                print("OSI SPONGE_MANUAL_APPLY_FAILED root=%r error=%r" % (getattr(root, "name", None), exc))
            except Exception:
                pass
    return changed_total



def _explicit_sponge_roots_for_scene(source_obj=None, selected_only: bool = False):
    """Return sponge roots using the same resolution diagnostics use.

    This bypasses the older manual scan path, whose diagnostics could find
    candidates while its second-pass filters still returned zero targets.
    """
    roots = []
    seen = set()

    def _add_root(root):
        if root is None:
            return
        try:
            key = _object_key(root)
        except Exception:
            key = getattr(root, "name_full", getattr(root, "name", str(id(root))))
        if key in seen:
            return
        seen.add(key)
        roots.append(root)

    if selected_only:
        _add_root(_nearest_sponge_root(source_obj))
        return roots

    try:
        if source_obj is not None:
            _add_root(_nearest_sponge_root(source_obj))
    except Exception:
        pass
    try:
        for obj in list(bpy.data.objects):
            try:
                if bool(obj.get("bcett_sponge_inflate_enabled", False)) or obj.get("bcett_sponge_bbox_half", None) is not None:
                    _add_root(_nearest_sponge_root(obj))
                    continue
                if obj.get("bcett_sponge_root", None) is not None or obj.get("bcett_sponge_mesh_kind", None) is not None:
                    _add_root(_nearest_sponge_root(obj))
                    continue
                if obj.get("spl_sponge_inflate", None) is not None and _looks_like_sponge_object(obj):
                    _add_root(_nearest_sponge_root(obj))
            except Exception:
                pass
    except Exception:
        pass
    return roots


def _explicit_sponge_inflate_value(root, source_obj=None) -> float:
    """Use the edited object's value when present, otherwise the root value."""
    default = 0.0
    try:
        default = _float_prop(root.get("spl_sponge_inflate", 0.0), 0.0) if root is not None else 0.0
    except Exception:
        default = 0.0
    try:
        if source_obj is not None and source_obj.get("spl_sponge_inflate", None) is not None:
            return max(0.0, min(1.0, _float_prop(source_obj.get("spl_sponge_inflate", default), default)))
    except Exception:
        pass
    try:
        return max(0.0, min(1.0, _float_prop(root.get("spl_sponge_inflate", default), default)))
    except Exception:
        return max(0.0, min(1.0, float(default)))


def _apply_sponge_inflate_explicit(root, value: float, report_fn=None) -> int:
    """Apply sponge inflate using already-classified target lists directly.

    The operator diagnostics already prove target discovery can find the body
    mesh and parts armature.  This function uses those lists directly and emits
    per-object rejection/failure lines through the Info-window reporter.
    """
    def _emit(msg):
        try:
            _sponge_info_line(str(msg))
        except Exception:
            pass
        if report_fn is not None:
            try:
                report_fn(str(msg)[:900])
            except Exception:
                pass

    if root is None:
        _emit("SPONGE_EXPLICIT_ABORT root=None")
        return 0
    try:
        value = max(0.0, min(1.0, float(value)))
    except Exception:
        value = 0.0

    half = _sponge_bbox_half(root)
    max_mult = _sponge_inflate_max_multiplier(root)
    body_center = _sponge_body_center(root)
    scale_factor = 1.0 + max_mult * value
    body_meshes, parts_meshes, parts_armatures = _collect_sponge_inflate_targets(root)
    _emit("SPONGE_EXPLICIT_BEGIN root=%r value=%.4f body=%r parts_mesh=%r parts_arm=%r scale_factor=%.4f" % (
        getattr(root, "name", None), value,
        [getattr(o, "name", None) for o in body_meshes],
        [getattr(o, "name", None) for o in parts_meshes],
        [getattr(o, "name", None) for o in parts_armatures],
        scale_factor))

    changed = 0
    if not body_meshes:
        _emit("SPONGE_EXPLICIT_REJECT no_body_mesh_targets descendants=%r" % ([getattr(o, "name", None) for o in _sponge_descendants(root)[:20]],))
    if not parts_armatures:
        _emit("SPONGE_EXPLICIT_REJECT no_parts_armature_targets descendants=%r" % ([getattr(o, "name", None) for o in _sponge_descendants(root)[:20]],))

    for obj in body_meshes:
        try:
            if getattr(obj, "type", None) != "MESH":
                _emit("SPONGE_BODY_REJECT obj=%r reason=not_mesh type=%r" % (getattr(obj, "name", None), getattr(obj, "type", None)))
                continue
            base = _sponge_base_scale(obj)
            before = tuple(round(float(v), 6) for v in obj.scale)
            obj.scale = Vector((base.x * scale_factor, base.y * scale_factor, base.z * scale_factor))
            obj.update_tag(refresh={'OBJECT'})
            after = tuple(round(float(v), 6) for v in obj.scale)
            _emit("SPONGE_BODY_APPLY_DIRECT_ORIGIN_OFFSET_SCALE obj=%r scale_factor=%.4f before=%s after=%s" % (obj.name, scale_factor, before, after))
            changed += 1
        except Exception as exc:
            _emit("SPONGE_BODY_APPLY_DIRECT_FAILED obj=%r error=%r" % (getattr(obj, "name", None), exc))

    for obj in parts_meshes:
        try:
            if getattr(obj, "type", None) != "MESH":
                _emit("SPONGE_PARTS_MESH_REJECT obj=%r reason=not_mesh type=%r" % (getattr(obj, "name", None), getattr(obj, "type", None)))
                continue
            base = _sponge_base_scale(obj)
            before = tuple(round(float(v), 6) for v in obj.scale)
            obj.scale = Vector((base.x, base.y, base.z))
            obj.update_tag(refresh={'OBJECT', 'DATA'})
            after = tuple(round(float(v), 6) for v in obj.scale)
            _emit("SPONGE_PARTS_MESH_RESET_DIRECT obj=%r base=%s before=%s after=%s" % (obj.name, tuple(round(float(v), 6) for v in base), before, after))
            changed += 1
        except Exception as exc:
            _emit("SPONGE_PARTS_MESH_RESET_DIRECT_FAILED obj=%r error=%r" % (getattr(obj, "name", None), exc))

    for arm in parts_armatures:
        try:
            if getattr(arm, "type", None) != "ARMATURE":
                _emit("SPONGE_ARMATURE_REJECT obj=%r reason=not_armature type=%r" % (getattr(arm, "name", None), getattr(arm, "type", None)))
                continue
            applied_bones = 0
            for pb in arm.pose.bones:
                name = str(pb.name)
                if not name.startswith("parts_"):
                    continue
                try:
                    base = _sponge_pose_bone_base_location(pb)
                    corner_root_raw = _sponge_pose_bone_corner_root_local(root, arm, pb, body_center, half)
                    corner_root = _sponge_compensate_parts_corner_root_local(root, corner_root_raw)
                    corner_pose = _sponge_root_delta_to_armature_pose_delta(root, arm, corner_root)
                    before = tuple(round(float(v), 6) for v in pb.location)
                    pb.location = base + (corner_pose * (max_mult * value))
                    after = tuple(round(float(v), 6) for v in pb.location)
                    corner_world = _sponge_vector_world_from_root(root, corner_root)
                    if _sponge_debug_enabled(root):
                        _emit("SPONGE_BONE_APPLY_DIRECT arm=%r bone=%r base=%s corner_root=%s corner_pose=%s corner_world=%s before=%s after=%s" % (
                            arm.name, name, tuple(round(float(v), 6) for v in base),
                            tuple(round(float(v), 6) for v in corner_root),
                            tuple(round(float(v), 6) for v in corner_pose),
                            tuple(round(float(v), 6) for v in corner_world), before, after))
                    applied_bones += 1
                except Exception as exc:
                    _emit("SPONGE_BONE_APPLY_DIRECT_FAILED arm=%r bone=%r error=%r" % (getattr(arm, "name", None), name, exc))
            arm.update_tag(refresh={'OBJECT', 'DATA'})
            try:
                if arm.data is not None:
                    arm.data.update_tag(refresh={'DATA'})
            except Exception:
                pass
            _emit("SPONGE_ARMATURE_APPLY_DIRECT arm=%r bones=%d" % (arm.name, applied_bones))
            changed += applied_bones
        except Exception as exc:
            _emit("SPONGE_ARMATURE_APPLY_DIRECT_FAILED obj=%r error=%r" % (getattr(arm, "name", None), exc))

    try:
        root["spl_sponge_inflate"] = float(value)
        root["bcett_sponge_inflate_applied_value"] = float(value)
        root["bcett_sponge_inflate_last_changed"] = int(changed)
        root["bcett_sponge_inflate_body_targets"] = ",".join(obj.name for obj in body_meshes)
        root["bcett_sponge_inflate_parts_armatures"] = ",".join(obj.name for obj in parts_armatures)
        _SPONGE_INFLATE_CACHE[_object_key(root)] = float(value)
        _SPONGE_INFLATE_PENDING.pop(_object_key(root), None)
        for child in _sponge_descendants(root):
            try:
                _set_object_sponge_inflate_idprop(child, value)
                _SPONGE_INFLATE_OBJECT_CACHE[_object_key(child)] = float(value)
            except Exception:
                pass
    except Exception:
        pass
    try:
        root.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
    except Exception:
        pass
    _emit("SPONGE_EXPLICIT_END root=%r value=%.4f changed=%d" % (getattr(root, "name", None), value, changed))
    return changed

class OSI_OT_apply_team_colors_to_scene(bpy.types.Operator):
    bl_idname = "osi.apply_team_colors_to_scene"
    bl_label = "Apply Team Colors to Scene"
    bl_description = "Update imported team-color materials from the currently selected TeamColorDataSet"

    def execute(self, context):
        count = apply_team_colors_to_scene(context.scene, source_obj=getattr(context, "object", None))
        # Also flush sponge inflate ID-property edits from the same known-working
        # manual refresh path. This is not the primary live mechanism, but it
        # provides a reliable fallback while the ID-property watcher is debugged.
        source = getattr(context, "object", None)
        sponge_count = apply_sponge_inflate_from_scene(context.scene, source_obj=source, selected_only=False)
        if sponge_count == 0:
            for line in sponge_inflate_info_diagnostics(source, selected_only=False)[:8]:
                self.report({"INFO"}, line)
        self.report({"INFO"}, "Updated %d team-color material link(s), %d sponge inflate target(s)" % (count, sponge_count))
        return {"FINISHED"}


class OSI_OT_apply_sponge_inflate_to_selected(bpy.types.Operator):
    bl_idname = "osi.apply_sponge_inflate_to_selected"
    bl_label = "Apply Sponge Inflate to Selected"
    bl_description = "Apply the selected sponge object's spl_sponge_inflate custom property to its body and parts bones"

    def execute(self, context):
        source = getattr(context, "object", None)
        for line in sponge_inflate_info_diagnostics(source, selected_only=True):
            self.report({"INFO"}, line)
        root = _nearest_sponge_root(source)
        value = _explicit_sponge_inflate_value(root, source)
        count = _apply_sponge_inflate_explicit(root, value, report_fn=lambda msg: self.report({"INFO"}, msg))
        # Drain any queued diagnostic lines that were not reported inline.
        for line in _drain_sponge_info_lines(12):
            if line.startswith(("SPONGE_DIAG", "SPONGE_EXPLICIT_BEGIN", "SPONGE_EXPLICIT_END", "SPONGE_BODY_APPLY_DIRECT", "SPONGE_BODY_APPLY_FAILED", "SPONGE_APPLY_END")):
                self.report({"INFO"}, line[:900])
        self.report({"INFO"}, "Applied sponge inflate to %d target(s)" % count)
        return {"FINISHED"}


class OSI_OT_apply_sponge_inflate_to_scene(bpy.types.Operator):
    bl_idname = "osi.apply_sponge_inflate_to_scene"
    bl_label = "Apply Sponge Inflate to Scene"
    bl_description = "Apply all visible spl_sponge_inflate custom properties in the scene"

    def execute(self, context):
        source = getattr(context, "object", None)
        for line in sponge_inflate_info_diagnostics(source, selected_only=False):
            self.report({"INFO"}, line)
        count = 0
        roots = _explicit_sponge_roots_for_scene(source_obj=source, selected_only=False)
        self.report({"INFO"}, "SPONGE_EXPLICIT_SCENE roots=%d names=%r" % (len(roots), [getattr(r, "name", None) for r in roots[:20]]))
        for root in roots:
            value = _explicit_sponge_inflate_value(root, source if _nearest_sponge_root(source) is root else None)
            count += _apply_sponge_inflate_explicit(root, value, report_fn=lambda msg: self.report({"INFO"}, msg))
        for line in _drain_sponge_info_lines(12):
            if line.startswith(("SPONGE_DIAG", "SPONGE_EXPLICIT_BEGIN", "SPONGE_EXPLICIT_END", "SPONGE_BODY_APPLY_DIRECT", "SPONGE_BODY_APPLY_FAILED", "SPONGE_APPLY_END")):
                self.report({"INFO"}, line[:900])
        self.report({"INFO"}, "Applied sponge inflate to %d target(s)" % count)
        return {"FINISHED"}


class OSI_OT_set_all_sponges_inflate(bpy.types.Operator):
    bl_idname = "osi.set_all_sponges_inflate"
    bl_label = "Set All Sponge Inflate"
    bl_description = "Set spl_sponge_inflate on every sponge root and update the scene immediately"

    value: bpy.props.FloatProperty(name="Inflate", default=0.0, min=0.0, max=1.0)

    def execute(self, context):
        value = max(0.0, min(1.0, float(self.value)))
        roots = _explicit_sponge_roots_for_scene(source_obj=getattr(context, "object", None), selected_only=False)
        changed_roots = 0
        changed_targets = 0
        for root in roots:
            try:
                root["spl_sponge_inflate"] = value
                changed_roots += 1
            except Exception:
                continue
            changed_targets += _apply_sponge_inflate_explicit(root, value)
        self.report({"INFO"}, "Set %d sponge(s) to %.1f; updated %d target(s)" % (changed_roots, value, changed_targets))
        return {"FINISHED"}


class OSI_OT_create_team_color_test_cubes(bpy.types.Operator):
    bl_idname = "osi.create_team_color_test_cubes"
    bl_label = "Create/Update Team Color Test Cubes"
    bl_description = "Create four test cubes at Z +20 and link them to Neutral, Alpha, Bravo, and Charlie colors from the selected set"

    def execute(self, context):
        if not _selected_record(context.scene):
            records = load_team_color_records(False)
            if records:
                try:
                    default_rec = _default_team_color_record(records) or records[0]
                    if hasattr(context.scene, "spl_team_color_set"):
                        context.scene.spl_team_color_set = default_rec["id"]
                    if hasattr(context.scene, "osi_team_color_set"):
                        context.scene.osi_team_color_set = default_rec["id"]
                except Exception:
                    pass
        if apply_selected_team_color(context.scene, create_missing_cubes=True):
            self.report({"INFO"}, "Team color test cubes updated")
            return {"FINISHED"}
        self.report({"WARNING"}, _TEAM_COLOR_CACHE.get("error", "No TeamColorDataSet row selected"))
        return {"CANCELLED"}


class OSI_PT_team_colors(bpy.types.Panel):
    bl_label = "Splatoon Team Colors"
    bl_idname = "OSI_PT_team_colors"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Spl3"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        row = layout.row(align=True)
        row.operator(OSI_OT_reload_team_colors.bl_idname, text="Reload Team Colors", icon="FILE_REFRESH")
        row.operator(OSI_OT_reload_object_teams.bl_idname, text="Reload Object Teams", icon="OUTLINER_OB_ARMATURE")
        layout.prop(scene, "spl_team_color_set" if hasattr(scene, "spl_team_color_set") else "osi_team_color_set", text="Color Set")
        rec = _selected_record(scene)
        if rec:
            layout.label(text="Tags: %s" % rec.get("tag_label", ""), icon="BOOKMARKS")
            layout.label(text="Name: %s" % rec.get("name", ""), icon="COLOR")
        elif _TEAM_COLOR_CACHE.get("error"):
            layout.label(text=_TEAM_COLOR_CACHE["error"], icon="ERROR")
        layout.operator(OSI_OT_create_team_color_test_cubes.bl_idname, icon="MESH_CUBE")
        op = layout.operator("osi.apply_team_colors_to_scene", icon="MATERIAL")
        box = layout.box()
        box.label(text="Sponge Inflate", icon="MOD_ARMATURE")
        row = box.row(align=True)
        op = row.operator(OSI_OT_set_all_sponges_inflate.bl_idname, text="Deflate All", icon="TRIA_DOWN")
        op.value = 0.0
        op = row.operator(OSI_OT_set_all_sponges_inflate.bl_idname, text="Inflate All", icon="TRIA_UP")
        op.value = 1.0
        box.operator(OSI_OT_apply_sponge_inflate_to_selected.bl_idname, icon="PLAY")
        box.operator(OSI_OT_apply_sponge_inflate_to_scene.bl_idname, icon="FILE_REFRESH")


_CLASSES = (
    OSI_OT_reload_team_colors,
    OSI_OT_reload_object_teams,
    OSI_OT_apply_team_colors_to_scene,
    OSI_OT_apply_sponge_inflate_to_selected,
    OSI_OT_apply_sponge_inflate_to_scene,
    OSI_OT_set_all_sponges_inflate,
    OSI_OT_create_team_color_test_cubes,
    OSI_PT_team_colors,
)


def register():
    # Clean up v0.8.58's temporary RNA property if Blender kept it around;
    # otherwise it hides the spl_sponge_inflate ID custom property.
    try:
        if hasattr(bpy.types.Object, "spl_sponge_inflate"):
            del bpy.types.Object.spl_sponge_inflate
    except Exception:
        pass
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.spl_team_color_set = bpy.props.EnumProperty(
        name="Team Color Set",
        description="TeamColorDataSet row to use for linked Splatoon team-color materials",
        items=_enum_items,
        update=_team_color_update,
    )
    # Legacy alias kept so existing scenes/scripts do not break immediately.
    bpy.types.Scene.osi_team_color_set = bpy.props.EnumProperty(
        name="Team Color Set (legacy)",
        description="Legacy alias for spl_team_color_set",
        items=_enum_items,
        update=_team_color_update,
    )
    bpy.types.Object.spl_team_assignment = bpy.props.EnumProperty(
        name="Active Team",
        description="Manual team assignment for whitelisted/dynamic Splatoon team-color objects",
        items=(("neutral", "Neutral", "Neutral/default team color"), ("alpha", "Alpha", "Alpha team color"), ("bravo", "Bravo", "Bravo team color"), ("charlie", "Charlie", "Charlie team color")),
        default="neutral",
        update=_object_team_assignment_update,
    )
    # Do not register bpy.types.Object.spl_sponge_inflate here.  A same-named
    # RNA property hides the ID custom property in Blender's Custom Properties
    # panel.  Sponge inflate stays as an object custom property and is watched
    # by the timer/depsgraph path below.
    try:
        load_team_color_records(False)
        _ensure_default_team_color_selection(getattr(bpy.context, "scene", None))
        _install_active_team_handler()
    except Exception:
        pass


def unregister():
    try:
        _remove_active_team_handler()
    except Exception:
        pass
    # spl_sponge_inflate is intentionally an ID custom property only.
    if hasattr(bpy.types.Scene, "spl_team_color_set"):
        del bpy.types.Scene.spl_team_color_set
    if hasattr(bpy.types.Scene, "osi_team_color_set"):
        del bpy.types.Scene.osi_team_color_set
    if hasattr(bpy.types.Object, "spl_team_assignment"):
        del bpy.types.Object.spl_team_assignment
    if hasattr(bpy.types.Object, "osi_team_assignment"):
        del bpy.types.Object.osi_team_assignment
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
