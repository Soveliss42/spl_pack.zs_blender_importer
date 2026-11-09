import math
import os
import sys
import traceback
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Euler, Matrix, Vector

from . import byml
from . import sarc
from .session import ImportSession
from . import lifecycle
from .pack_context import PackContext
from .romfs_resolver import RomfsResolver
from .rsdb_loader import RsdbRegistry
from . import transform as _transform_utils
from .transform import (
    to_float as _to_float,
    vector_tuple_from_any as _vector_from_any,
    raw_rotation_tuple as _raw_rotation_tuple,
    bcett_rotation_from_entry as _rotation_euler_from_entry,
)
from . import rails as _osi_rails
from . import background as _osi_background
from .collection_utils import (
    safe_collection_name as _safe_collection_name,
    get_or_create_child_collection as _get_or_create_child_collection,
    move_objects_to_collection as _move_objects_to_collection,
)
from .report_utils import (
    compact_import_report_lines as _compact_import_report_lines,
    minimal_import_report_lines as _minimal_import_report_lines,
    is_texture_debug_report_line as _is_texture_debug_report_line,
    full_import_report_lines as _full_import_report_lines,
    select_import_report_lines as _select_import_report_lines_shared,
)
from .pack_resources import (
    PackResourceResolver,
    normalize_pack_resource_name as _normalize_pack_resource_name,
    candidate_name_variants as _candidate_name_variants,
    pack_resource_candidates as _pack_resource_candidates,
)
from .actor_helpers import (
    clean_actor_token as _clean_actor_token,
    actor_token_variants as _actor_token_variants,
    flatten_actor_items as _flatten_actor_items,
    actor_first_value_by_key as _actor_first_value_by_key,
    entry_tokens as _entry_tokens,
    gfx_light_kind as _gfx_light_kind,
    is_supported_gfx_light_actor as _is_supported_gfx_light_actor,
)
from .property_utils import (
    idprop_safe as _idprop_safe,
    set_props_from_mapping as _set_props_from_mapping,
)
from .rsdb_helpers import (
    optional_rsdb as _optional_rsdb,
    annotate_object_with_rsdb as _annotate_object_with_rsdb,
    set_optional_rsdb_tables as _set_optional_rsdb_tables,
    clear_optional_rsdb_tables as _clear_optional_rsdb_tables,
)

try:
    import zstandard as _osi_zstandard
except Exception:
    _osi_zstandard = None

# Patched: do not add bundled BFRES wheels to Blender's global sys.path.
# Native dependencies are now loaded on demand by
# io_scene_osi._bfres_vendor.private_wheels, which extracts only the compatible
# wheel to a private directory and removes the temporary path after import.

stageFile = ""
stageFileBytes = None
stagePackPath = ""
stagePackFiles = {}
stagePackResolver = None
scenario = 0
import_mode = "FULL"
skip_non_renderable_actors = True
import_geometry = True
import_non_geometry_objects = True
import_graffiti = True
import_background = False
import_pack_extra_geometry = False
# Backward-compatible names retained for older generated code paths.
skip_gfx_actors = True
skip_change_paintable_area = True
report_verbosity = "COMPACT"
last_import_report_path = ""
last_actor_info_table_path = ""
last_actor_info_table_alias_count = 0


class ActorInfoTableError(RuntimeError):
    """Raised when a required ActorInfo/MapObjReplicaInfo table cannot be loaded."""

from .value_utils import numeric_from_any as _numeric_from_any, color_from_any as _color_from_any


def osi_colbox(self, data, expand_property):
    box = self.box()
    split = box.split(factor=0.5)
    row = split.row(align=True)
    row.prop(
        data,
        expand_property,
        icon="TRIA_DOWN" if getattr(data, expand_property) else "TRIA_RIGHT",
        icon_only=True,
        emboss=False,
    )
    row.label(text=getattr(data.rna_type, expand_property)[1]["name"])
    return box, split


class osiAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    romfs_root_path: bpy.props.StringProperty(
        name="Splatoon 3 RomFS Root",
        description="Folder containing a Splatoon 3 RomFS dump. The importer uses RomFS/Model for BFRES files and RomFS/RSDB/ActorInfo.Product.b10.rstbl.byml.zs for ActorInfo.",
        subtype="DIR_PATH",
    )


    import_tex_mode: bpy.props.EnumProperty(
        name="Texture Import Mode",
        items=(
            ("OFF", "Off", "Skip texture/BNTX import for faster model-only loading."),
            ("EMBEDDED", "Embedded", "Import textures embedded in the model file."),
            ("TEX_FILE", ".Tex File", "Import textures from a .Tex file next to the model."),
            ("TEX_FOLDER", "Tex Folder", "Import textures from a Tex folder in the parent directory."),
        ),
        default="EMBEDDED",
    )

    component_selector: bpy.props.BoolProperty(
        name="Use Component Selector",
        description="Use the BFRES component selector for textures. Enable if colors look wrong.",
        default=True,
    )

    custom_normals: bpy.props.BoolProperty(
        name="Custom Normals",
        description="Use the BFRES n0 attribute to compute normals.",
        default=True,
    )

    lod_index: bpy.props.IntProperty(
        name="LOD Index",
        description="The BFRES LOD index to import. Lower is more detailed.",
        default=0,
        min=0,
        max=255,
    )

    import_anims: bpy.props.BoolProperty(
        name="Import Base Animation Data",
        description="Import data and the first frame of animations as actions.",
        default=False,
    )

    add_fake_user: bpy.props.BoolProperty(
        name="Add Fake User",
        description="Add fake user to imported images/actions to prevent deletion on save.",
        default=False,
    )


    def draw(self, context):
        box = self.layout.box()
        box.label(text="General Options:", icon="FILE_FOLDER")
        box.prop(self, "romfs_root_path")

        root_status = _romfs_status(self)
        if root_status:
            for icon, text in root_status:
                box.label(text=text, icon=icon)
        else:
            box.label(text="Select the Splatoon 3 RomFS folder containing Model and RSDB subfolders.", icon="ERROR")

        box.label(text="BFRES Import Options:", icon="MESH_DATA")
        box.prop(self, "import_tex_mode")
        box.prop(self, "component_selector")
        box.prop(self, "custom_normals")
        box.prop(self, "lod_index")
        box.prop(self, "import_anims")
        box.prop(self, "add_fake_user")


def _addon_preferences():
    return bpy.context.preferences.addons[__package__].preferences


def _fmdb_model_candidates(fmdb_path):
    """Return likely BFRES/resource names from an ActorInfo Fmdb path."""
    fmdb = str(fmdb_path or "").strip()
    if not fmdb:
        return []
    p = Path(fmdb.replace("\\", "/"))
    stem = p.stem
    names = []
    def add(value):
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)
    add(stem)
    # Common Splatoon 3 layout: Work/Model/.../<bfres name>/output/<model name>.fmdb
    parts = p.parts
    if "output" in parts:
        idx = parts.index("output")
        if idx > 0:
            add(parts[idx - 1])
    # Also try each non-generic path component from right to left. Some dumps name
    # BFRES files by the pack/folder rather than by the FMDL stem.
    for part in reversed(parts[:-1]):
        if part not in {"Work", "Model", "Object", "Field", "MapParts", "VSGame", "Coop", "Mission", "output"}:
            add(part)
    return names


def _target_fmdl_names_from_actorinfo(entry, actor_info=None):
    """Return FMDL/model names to keep when a BFRES contains multiple models.

    ActorInfo's Fmdb path usually points to the specific model inside the BFRES
    pack, for example .../<pack>/output/<model>.fmdb.  The BFRES filename may be
    <pack>.bfres, but the individual FMDL to import is normally <model>.
    """
    targets = []

    def add(value):
        value = str(value or "").strip()
        if value and value not in targets:
            targets.append(value)

    if actor_info and isinstance(entry, dict):
        for lookup_key in _actorinfo_lookup_keys(entry):
            row = actor_info.get(lookup_key)
            if not row:
                continue
            fmdb = str(row.get("Fmdb", "") or "").replace("\\", "/")
            if fmdb:
                p = Path(fmdb)
                add(p.stem)
            add(row.get("ModelName", ""))
            add(row.get("Name", ""))
            add(row.get("__RowId", ""))

    # Fallbacks for non-ActorInfo cases and actors whose model name equals the
    # BYML token.  These are lower priority than the Fmdb stem.
    if isinstance(entry, dict):
        for key in ("ModelName", "UnitConfigName", "Name", "Gyaml"):
            add(_clean_actor_token(entry.get(key, "")))
            add(entry.get(key, ""))

    return targets


def _actorinfo_lookup_keys(entry):
    keys = []
    for key in ("__RowId", "Name", "Gyaml", "UnitConfigName", "ModelName"):
        value = entry.get(key) if isinstance(entry, dict) else None
        for candidate in _actor_token_variants(value):
            if candidate and candidate not in keys:
                keys.append(candidate)
    return keys



def _field_root_actor_name(entry):
    """Return exact Fld_* stage-root actor name, or empty for normal actors.

    Field-root actors such as Fld_Yagara/Fld_Spider00 represent the stage root
    model.  ActorInfo/MapObj alias tables can contain related DObj_/FldObj_
    mappings in the same BFRES pack, but those must not be imported as children
    of the field root actor.
    """
    if not isinstance(entry, dict):
        return ""
    for key in ("Gyaml", "Name", "__RowId", "ModelName", "UnitConfigName"):
        name = _clean_actor_token(entry.get(key, ""))
        if name.startswith("Fld_") and not name.startswith(("FldObj_", "FldBG_")):
            return name
    return ""

def _candidate_model_names(entry, actor_info=None):
    # SMO BYML uses UnitConfigName/ModelName. Splatoon 3 BCETT usually stores
    # actor row ids in Gyaml/Name; ActorInfo maps those row ids to Fmdb model
    # resources. Prefer Fmdb-derived resource names, then fall back to raw BYML.
    names = []
    def add(value):
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)

    if actor_info and isinstance(entry, dict):
        for lookup_key in _actorinfo_lookup_keys(entry):
            row = actor_info.get(lookup_key)
            if row:
                for candidate in _fmdb_model_candidates(row.get("Fmdb", "")):
                    add(candidate)
                # Keep the row id as a final candidate; useful for actors whose
                # BFRES really is named after the row id.
                add(row.get("__RowId", ""))

    for key in ("UnitConfigName", "ModelName", "Name", "Gyaml"):
        value = entry.get(key, "") if isinstance(entry, dict) else ""
        for candidate in _actor_token_variants(value):
            add(candidate)
    return names


def _candidate_model_paths_from_actorinfo(models_dir, entry, actor_info=None):
    """Return concrete relative Fmdb-derived file candidates when available."""
    if not actor_info or not isinstance(entry, dict):
        return []
    suffixes = (".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres", ".szs", ".zs")
    result = []
    for lookup_key in _actorinfo_lookup_keys(entry):
        row = actor_info.get(lookup_key)
        if not row:
            continue
        fmdb = str(row.get("Fmdb", "") or "").replace("\\", "/")
        if not fmdb:
            continue
        p = Path(fmdb)
        stem = p.stem
        parent = p.parent
        # Same output directory, replacing .fmdb with BFRES suffix.
        for suffix in suffixes:
            result.append(models_dir / parent / (stem + suffix))
        # Parent folder before /output/ is another common BFRES pack name.
        parts = p.parts
        if "output" in parts:
            idx = parts.index("output")
            if idx > 0:
                pack = parts[idx - 1]
                pack_parent = Path(*parts[:idx]) if idx else Path()
                for suffix in suffixes:
                    result.append(models_dir / pack_parent / (pack + suffix))
    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for path in result:
        key = str(path)
        if key not in seen:
            seen.add(key); unique.append(path)
    return unique











def _rotation_debug_line(actor_label, entry, rotation_euler, source="actor"):
    raw = _raw_rotation_tuple(entry.get("Rotate", {}) if isinstance(entry, dict) else {}, 0.0)
    raw_deg = tuple(round(math.degrees(v), 6) for v in raw) if isinstance(entry, dict) and "Gyaml" in entry else tuple(round(v, 6) for v in raw)
    conv_rad = tuple(round(v, 6) for v in rotation_euler)
    conv_deg = tuple(round(math.degrees(v), 6) for v in rotation_euler)
    return "ROT_DEBUG actor=%r source=%s raw=%s raw_deg=%s converted_rad=%s converted_deg=%s order=XYZ method=matrix_axis_basis" % (
        actor_label, source, tuple(round(v, 6) for v in raw), raw_deg, conv_rad, conv_deg
    )


def _objects_from_stage_root(root, scenario_index):
    # Original SMO layout: root is a scenario list, each scenario has ObjectList.
    if isinstance(root, list):
        if scenario_index < 0 or scenario_index >= len(root):
            raise IndexError("Scenario %d is outside the available range 0-%d." % (scenario_index, len(root) - 1))
        scenario_data = root[scenario_index]
        if not isinstance(scenario_data, dict):
            raise ValueError("Scenario %d is not a BYML dictionary." % scenario_index)
        return scenario_data.get("ObjectList", []), "SMO ObjectList"

    # Splatoon 3 BCETT layout: root is a dictionary with Actors.
    if isinstance(root, dict) and isinstance(root.get("Actors"), list):
        return root.get("Actors", []), "Splatoon 3 BCETT Actors"

    keys = sorted(root.keys()) if isinstance(root, dict) else type(root).__name__
    raise ValueError("Unsupported BYML stage schema. Expected SMO scenario list or Splatoon 3 BCETT root with Actors; got %s." % keys)


def _actor_label(entry, fallback="Actor"):
    if isinstance(entry, dict):
        return str(entry.get("Name") or entry.get("Gyaml") or entry.get("__RowId") or fallback)
    return str(fallback)


def _anchor_label(entry, fallback="Anchor"):
    if not isinstance(entry, dict):
        return str(fallback)
    return str(entry.get("Name") or entry.get("Gyaml") or entry.get("__RowId") or entry.get("InstanceID") or fallback)


def _add_anchor_to_index(index, hash_value, anchor):
    if isinstance(hash_value, bool):
        return
    try:
        key = int(hash_value)
    except Exception:
        return
    index.setdefault(key, anchor)


def _build_banc_anchor_index(root, objects):
    """Index only BCETT Rails and Rails/Points by Hash/SRTHash for BancParam links.

    Actor hashes are intentionally ignored here.  BancParam placement overrides
    should only anchor to rail data; otherwise ordinary actor-to-actor references
    can accidentally move unrelated objects.
    """
    index = {}
    rails = root.get("Rails", []) if isinstance(root, dict) else []
    if isinstance(rails, list):
        for rail_index, rail in enumerate(rails):
            if not isinstance(rail, dict):
                continue
            rail_label = _anchor_label(rail, "Rail_%04d" % rail_index)
            rail_anchor = {
                "kind": "Rail",
                "label": rail_label,
                "entry": rail,
                "translate": rail.get("Translate"),
                "rotate": rail.get("Rotate") or rail.get("Rotation"),
                "layer": rail.get("Layer", "NoLayer"),
            }
            _add_anchor_to_index(index, rail.get("Hash"), rail_anchor)
            _add_anchor_to_index(index, rail.get("SRTHash"), rail_anchor)

            points = rail.get("Points", [])
            if not isinstance(points, list):
                continue
            for point_index, point in enumerate(points):
                if not isinstance(point, dict):
                    continue
                point_anchor = {
                    "kind": "RailPoint",
                    "label": "%s.Point[%d]" % (rail_label, point_index),
                    "entry": point,
                    "parent": rail,
                    "translate": point.get("Translate"),
                    "rotate": point.get("Rotate") or point.get("Rotation"),
                    "parent_rotate": rail.get("Rotate") or rail.get("Rotation"),
                    "layer": point.get("Layer", rail.get("Layer", "NoLayer")),
                }
                _add_anchor_to_index(index, point.get("Hash"), point_anchor)
                _add_anchor_to_index(index, point.get("SRTHash"), point_anchor)
    return index


def _looks_like_reference_key(key):
    text = str(key or "").lower()
    return any(token in text for token in ("hash", "id", "link", "ref", "target", "rail", "point", "to"))


def _collect_reference_values(value, path, inherited_hint=False):
    """Yield (path, int_value) for plausible hash/id references inside BancParam data."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = "%s.%s" % (path, key) if path else str(key)
            yield from _collect_reference_values(child, child_path, inherited_hint or _looks_like_reference_key(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _collect_reference_values(child, "%s[%d]" % (path, index), inherited_hint)
    else:
        if isinstance(value, bool):
            return
        try:
            number = int(value)
        except Exception:
            return
        # Most BCETT object/rail hashes are 64-bit.  Key-name hints allow some
        # 32-bit SRTHash/ID references without treating weights/counts as links.
        if inherited_hint or number > 0xFFFFFFFF:
            yield path, number


def _bancparam_reference_candidates(entry):
    """Return plausible hash/id references found in any *BancParam dictionary."""
    if not isinstance(entry, dict):
        return []
    result = []
    seen = set()
    for param_name, param_value in entry.items():
        if not str(param_name).endswith("BancParam"):
            continue
        for value_path, ref_value in _collect_reference_values(param_value, str(param_name), False):
            key = (value_path, ref_value)
            if key not in seen:
                seen.add(key)
                result.append((str(param_name), value_path, ref_value))
    return result


def _apply_bancparam_anchor_override(entry, location, rotation_euler, anchor_index, report_lines, actor_label):
    """Generic *BancParam link resolver.

    If a *BancParam contains a plausible hash/ID that resolves to a rail
    or rail point with Translate data, use that anchor's translation for placement.  If
    the anchor itself has Rotate/Rotation data, use that rotation too; otherwise
    keep the actor's own rotation.  If no hash/ID value or no match exists, leave
    the normal transform unchanged and record the fallback in the import report.
    """
    candidates = _bancparam_reference_candidates(entry)
    if not candidates:
        if any(str(k).endswith("BancParam") for k in getattr(entry, "keys", lambda: [])()):
            report_lines.append("BANC_ANCHOR normal actor=%r reason=no_hash_id_value_in_BancParam" % actor_label)
        return location, rotation_euler, False

    for param_name, value_path, ref_value in candidates:
        anchor = anchor_index.get(ref_value)
        if not anchor:
            continue
        translate = anchor.get("translate")
        if translate is None:
            report_lines.append(
                "BANC_ANCHOR unresolved actor=%r param=%s ref_path=%s ref=%s matched=%s:%s reason=rail_anchor_has_no_translate"
                % (actor_label, param_name, value_path, ref_value, anchor.get("kind"), anchor.get("label"))
            )
            continue
        new_location = _vector_from_any(translate, 0.0, splatoon_axis_order=True)
        new_rotation = rotation_euler
        rotate = anchor.get("rotate")
        if rotate is not None:
            new_rotation = _rotation_euler_from_entry({"Gyaml": "__RailAnchor__", "Rotate": rotate})
            rotation_note = "anchor_rotation_applied"
        else:
            rotation_note = "actor_rotation_kept"
        report_lines.append(
            "BANC_ANCHOR applied actor=%r param=%s ref_path=%s ref=%s anchor=%s:%s layer=%r location=%s %s"
            % (actor_label, param_name, value_path, ref_value, anchor.get("kind"), anchor.get("label"), anchor.get("layer"), tuple(round(v, 6) for v in new_location), rotation_note)
        )
        return new_location, new_rotation, True

    report_lines.append(
        "BANC_ANCHOR normal actor=%r reason=no_rail_reference_resolved candidates=%s"
        % (actor_label, [(path, value) for _, path, value in candidates])
    )
    return location, rotation_euler, False


def _effective_bfres_texture_mode(prefs):
    """Map file-import UI mode to the vendored BFRES texture mode."""
    mode = str(globals().get("import_mode", "FULL") or "FULL").upper()
    if mode in {"MATERIALS", "NONE"}:
        return "OFF"
    return getattr(prefs, "import_tex_mode", "EMBEDDED")


def _effective_import_materials():
    """Return False only for the fastest no-material/no-texture mode."""
    return str(globals().get("import_mode", "FULL") or "FULL").upper() != "NONE"


def _bfres_operator_from_preferences(prefs, name_prefix="", report_messages=None, texture_diag_owner="BCETT"):
    # The vendored BFRES importer expects to read these properties from its
    # Blender operator.  A SimpleNamespace is enough for programmatic imports.
    def _report(_type, message):
        level = next(iter(_type)) if isinstance(_type, set) and _type else str(_type)
        text = "[%s] %s" % (level, message)
        if report_messages is not None:
            report_messages.append(text)
        print(text)

    return SimpleNamespace(
        import_tex_mode=_effective_bfres_texture_mode(prefs),
        import_materials=_effective_import_materials(),
        copy_bone_transforms=False,
        component_selector=prefs.component_selector,
        custom_normals=prefs.custom_normals,
        lod_index=prefs.lod_index,
        name_prefix=name_prefix,
        add_fake_user=prefs.add_fake_user,
        import_anims=prefs.import_anims,
        texture_diag_owner=texture_diag_owner,
        import_phase=texture_diag_owner,
        report_texture_details=_is_full_report(),
        report=_report,
    )


_MODEL_SEARCH_CACHE = {}
_BFRES_FMDL_INDEX_CACHE = {}
_ACTOR_INFO_CACHE = {}
_RSDB_TABLE_CACHE = {}
_OPTIONAL_RSDB_TABLES = {}  # compatibility mirror; authoritative state is rsdb_helpers.py



def _is_full_report():
    return str(globals().get("report_verbosity", "COMPACT") or "COMPACT").upper() in {"FULL", "DEBUG_TEXTURE"}


def _select_import_report_lines(report_lines):
    return _select_import_report_lines_shared(report_lines, globals().get("report_verbosity", "COMPACT"))


def _report_append(report_lines, line, *, full_only=False):
    if report_lines is None:
        return
    if full_only and not _is_full_report():
        return
    report_lines.append(line)


def _progress_update(actor_index, total, step=25):
    """Batch progress updates to avoid UI/console overhead for every actor."""
    if total <= 0:
        return
    if actor_index == 0 or actor_index + 1 == total or ((actor_index + 1) % int(step or 25) == 0):
        try:
            bpy.context.window_manager.progress_update(actor_index + 1)
        except Exception:
            pass
        try:
            print("OSI import progress: %d/%d actors" % (actor_index + 1, total))
        except Exception:
            pass



def _find_bfres_model(models_dir, names, concrete_paths=None):
    # Prefer concrete ActorInfo Fmdb paths, then root-level names, then a
    # recursive stem search for asset dumps that preserve Work/Model folders.
    suffixes = (".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres", ".szs", ".zs")
    for candidate in concrete_paths or []:
        if candidate.is_file():
            return candidate

    for name in names:
        for suffix in suffixes:
            candidate = models_dir / (name + suffix)
            if candidate.is_file():
                return candidate

    cache_key = str(models_dir.resolve())
    cache = _MODEL_SEARCH_CACHE.get(cache_key)
    if cache is None:
        cache = {}
        for suffix in suffixes:
            for path in models_dir.rglob("*" + suffix):
                stem = path.name[:-len(suffix)]
                keys = {stem, stem.casefold(), path.name, path.name.casefold()}
                for key in keys:
                    cache.setdefault(key, path)
        _MODEL_SEARCH_CACHE[cache_key] = cache
    for name in names:
        for key in (str(name), str(name).casefold()):
            found = cache.get(key)
            if found and found.is_file():
                return found
    return None



def _bfres_fmdl_names_cached(path):
    """Return FMDL names for a BFRES/BFRES.ZS path, cached by file path/mtime/size."""
    try:
        path = Path(path)
        stat = path.stat()
        key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return None
    cached = _BFRES_FMDL_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from ._bfres_vendor.importing import Importer as _OsiBfresImporter
        import io
        raw = path.read_bytes()
        if raw.startswith(b"\x28\xb5\x2f\xfd"):
            if _osi_zstandard is None:
                return None
            raw = _osi_zstandard.ZstdDecompressor().decompress(raw)
        if not raw.startswith(b"FRES"):
            return None
        from ._bfres_vendor.bfrespy.res_file import ResFile
        bfres = ResFile(io.BytesIO(raw))
        names = tuple(str(getattr(fmdl, "name", "")) for fmdl in bfres.models.values())
        _BFRES_FMDL_INDEX_CACHE[key] = names
        return names
    except Exception:
        return None


def _validate_byml_bytes(data, path, *, compressed=False):
    """Validate that bytes look like BYML before the BYML parser consumes them."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 16:
        kind = "decompressed " if compressed else ""
        raise ActorInfoTableError("%sBYML table %s is too small or empty." % (kind, path))
    magic = bytes(data[:2])
    if magic not in (b"BY", b"YB"):
        kind = "decompressed " if compressed else ""
        raise ActorInfoTableError(
            "%sBYML table %s has invalid magic %r; expected b'BY' or b'YB'." % (kind, path, magic)
        )
    return bytes(data)


def _decompress_zstd_bytes(data, path):
    """Decompress Zstandard data, supporting frames without a stored content size."""
    if _osi_zstandard is None:
        raise ActorInfoTableError(
            "Cannot read compressed ActorInfo/MapObjReplicaInfo table %s: Python module 'zstandard' is not available in Blender. "
            "Use the uncompressed .byml table or install zstandard into Blender's Python." % path
        )
    dctx = _osi_zstandard.ZstdDecompressor()
    try:
        return dctx.decompress(data)
    except Exception as first_error:
        # Some .zs files do not advertise their decompressed size.  Streaming
        # decompression handles those; preserve the original error for diagnosis
        # if streaming also fails.
        try:
            import io
            with dctx.stream_reader(io.BytesIO(data)) as reader:
                chunks = []
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as second_error:
            raise ActorInfoTableError(
                "Failed to decompress ActorInfo/MapObjReplicaInfo table %s as Zstandard: %s; streaming fallback also failed: %s"
                % (path, first_error, second_error)
            )


def _expanded_pref_path(value):
    """Return a Path from a Blender preference path, or None when unset."""
    value = str(value or "").strip()
    if not value:
        return None
    return Path(bpy.path.abspath(value))


def _romfs_root_path(prefs):
    """Return the configured RomFS root path, or None when unset."""
    return _expanded_pref_path(getattr(prefs, "romfs_root_path", ""))


def _romfs_model_dir(root):
    return Path(root) / "Model"


def _romfs_actor_info_path(root):
    return Path(root) / "RSDB" / "ActorInfo.Product.b10.rstbl.byml.zs"


def _romfs_status(prefs):
    """Return UI status rows for a configured RomFS root."""
    root = _romfs_root_path(prefs)
    if not root:
        return []
    rows = []
    if not root.is_dir():
        return [("ERROR", "Invalid Splatoon 3 RomFS folder: %s" % root)]
    models_dir = _romfs_model_dir(root)
    actor_info = _romfs_actor_info_path(root)
    rows.append(("FILE_TICK", "RomFS Root exists: %s" % root))
    rows.append((("FILE_TICK" if models_dir.is_dir() else "ERROR"), "RomFS Model folder %s: %s" % (("found" if models_dir.is_dir() else "missing"), models_dir)))
    rows.append((("FILE_TICK" if actor_info.is_file() else "ERROR"), "RomFS ActorInfo %s: %s" % (("found" if actor_info.is_file() else "missing"), actor_info)))
    rsdb_dir = _romfs_rsdb_dir(root)
    if rsdb_dir.is_dir():
        optional = ["GraffitiParam", "LocatorInfo", "WallaObjInfo", "SplActorInfo", "TeamColorDataSet", "TeamColorOffset"]
        found = sum(1 for prefix in optional if list(rsdb_dir.glob(prefix + "*.byml*")))
        rows.append((("FILE_TICK" if found else "INFO"), "Optional RSDB stage-link tables found: %d/%d" % (found, len(optional))))
    return rows


def _effective_models_dir(prefs, *, validate_romfs=True):
    """Return the BFRES model folder derived from Splatoon 3 RomFS Root."""
    root = _romfs_root_path(prefs)
    if not root:
        if validate_romfs:
            raise FileNotFoundError(
                "Splatoon 3 RomFS folder has not been selected. "
                "Open Edit > Preferences > Add-ons > Splatoon 3 Stage Importer and select the Splatoon 3 RomFS folder."
            )
        return Path()
    models_dir = _romfs_model_dir(root)
    if root.is_dir() and models_dir.is_dir():
        return models_dir
    if validate_romfs:
        if not root.is_dir():
            raise FileNotFoundError(
                "The selected Splatoon 3 RomFS folder does not exist or is not a directory: %s. "
                "Open Edit > Preferences > Add-ons > Splatoon 3 Stage Importer and select the correct Splatoon 3 RomFS folder."
                % root
            )
        raise FileNotFoundError(
            "Unable to locate the requested model folder in the selected Splatoon 3 RomFS: %s. "
            "Verify that the correct Splatoon 3 RomFS folder is selected in the Splatoon 3 Stage Importer add-on preferences. "
            "Expected layout: <RomFS>/Model."
            % models_dir
        )
    return models_dir


def _effective_actor_info_explicit_path(prefs, models_dir=None):
    """Return the ActorInfo table derived from Splatoon 3 RomFS Root."""
    root = _romfs_root_path(prefs)
    if root and root.is_dir():
        actor_info = _romfs_actor_info_path(root)
        if actor_info.is_file():
            return actor_info
        raise ActorInfoTableError(
            "Unable to load ActorInfo because the selected Splatoon 3 RomFS is missing the expected ActorInfo table: %s. "
            "Verify that the correct Splatoon 3 RomFS folder is selected in the Splatoon 3 Stage Importer add-on preferences. "
            "Expected layout: <RomFS>/RSDB/ActorInfo.Product.b10.rstbl.byml.zs."
            % actor_info
        )
    raise ActorInfoTableError(
        "Splatoon 3 RomFS folder has not been selected. "
        "Open Edit > Preferences > Add-ons > Splatoon 3 Stage Importer and select the Splatoon 3 RomFS folder."
    )


def _read_byml_or_zs(path):
    """Read a plain BYML file or a Zstandard-compressed .byml.zs file."""
    path = Path(path)
    data = path.read_bytes()
    lower = str(path).casefold()
    is_zs = data.startswith(b"\x28\xb5\x2f\xfd") or lower.endswith(".zs") or lower.endswith(".byml.zs")
    if is_zs:
        data = _decompress_zstd_bytes(data, path)
        return _validate_byml_bytes(data, path, compressed=True)
    return _validate_byml_bytes(data, path, compressed=False)



def _romfs_rsdb_dir(root):
    return Path(root) / "RSDB"


def _iter_rsdb_table_candidates(prefs, table_prefix):
    """Yield RomFS/RSDB Product table candidates for one RSDB prefix."""
    seen = set()
    root = _romfs_root_path(prefs)
    bases = []
    if root and root.is_dir():
        bases.append(_romfs_rsdb_dir(root))
    suffixes = (".Product.b10.rstbl.byml.zs", ".Product.b10.rstbl.byml", ".Product.rstbl.byml.zs", ".Product.rstbl.byml")
    for base in bases:
        if not (base and Path(base).is_dir()):
            continue
        for suffix in suffixes:
            for path in Path(base).glob(str(table_prefix) + "*" + suffix):
                if path.name.startswith("._"):
                    continue
                key = str(path.resolve()) if path.exists() else str(path)
                if key not in seen:
                    seen.add(key)
                    yield path


def _load_rsdb_product_table(prefs, table_prefix, *, required=False, report_lines=None):
    """Load a Splatoon 3 RSDB Product table by prefix, returning parsed rows or None."""
    candidates = list(_iter_rsdb_table_candidates(prefs, table_prefix))
    errors = []
    for path in candidates:
        if not path.is_file():
            continue
        cache_key = str(path.resolve())
        if cache_key in _RSDB_TABLE_CACHE:
            rows = _RSDB_TABLE_CACHE[cache_key]
            _report_append(report_lines, "RSDB_TABLE cached prefix=%r path=%r rows=%d" % (table_prefix, str(path), len(rows) if hasattr(rows, "__len__") else -1))
            return rows
        try:
            raw = _read_byml_or_zs(path)
            rows = byml.Byml(raw).parse()
            _RSDB_TABLE_CACHE[cache_key] = rows
            _report_append(report_lines, "RSDB_TABLE loaded prefix=%r path=%r rows=%d" % (table_prefix, str(path), len(rows) if hasattr(rows, "__len__") else -1))
            return rows
        except Exception as exc:
            errors.append("%s: %s" % (path, exc))
    if required:
        root = _romfs_root_path(prefs)
        if not root:
            raise ActorInfoTableError(
                "Required RSDB table %s could not be loaded because the Splatoon 3 RomFS folder has not been selected. "
                "Open Edit > Preferences > Add-ons > Splatoon 3 Stage Importer and select the Splatoon 3 RomFS folder."
                % table_prefix
            )
        raise ActorInfoTableError(
            "Required RSDB table %s was not found or could not be loaded in the selected Splatoon 3 RomFS. "
            "Verify that the correct RomFS folder is selected in the Splatoon 3 Stage Importer add-on preferences. Candidates=%s errors=%s"
            % (table_prefix, [str(p) for p in candidates], errors)
        )
    if candidates:
        _report_append(report_lines, "RSDB_TABLE_FAILED prefix=%r errors=%s" % (table_prefix, errors))
    else:
        _report_append(report_lines, "RSDB_TABLE_MISSING prefix=%r reason=no_candidates" % table_prefix)
    return None


def _rows_by_key(rows, key):
    out = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and key in row:
                value = row.get(key)
                out.setdefault(value, row)
                try:
                    out.setdefault(int(value), row)
                except Exception:
                    pass
                if isinstance(value, str):
                    out.setdefault(value.casefold(), row)
                    out.setdefault(_clean_actor_token(value), row)
                    out.setdefault(_clean_actor_token(value).casefold(), row)
    return out


def _load_optional_rsdb_tables(prefs, report_lines=None):
    """Load optional RSDB tables useful to stage/pack imports and cache compact indexes."""
    global _OPTIONAL_RSDB_TABLES
    prefixes = ("GraffitiParam", "LocatorInfo", "WallaObjInfo", "SplActorInfo", "TeamColorDataSet", "TeamColorOffset")
    tables = {}
    for prefix in prefixes:
        rows = _load_rsdb_product_table(prefs, prefix, required=False, report_lines=report_lines)
        if rows is None:
            continue
        entry = {"rows": rows, "by_rowid": _rows_by_key(rows, "__RowId")}
        if prefix == "GraffitiParam":
            entry["by_hash"] = _rows_by_key(rows, "Hash")
        tables[prefix] = entry
    _OPTIONAL_RSDB_TABLES = tables
    _set_optional_rsdb_tables(tables)
    return tables


def _merge_walla_obj_info_into_actor_info(actor_info, report_lines=None):
    """Expose WallaObjInfo Fmdb rows through the same alias table as ActorInfo."""
    table = _optional_rsdb("WallaObjInfo")
    rows = table.get("rows") if isinstance(table, dict) else None
    if not isinstance(rows, list) or not isinstance(actor_info, dict):
        return 0
    added = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get("FmdbPath"):
            continue
        compat = dict(row)
        compat.setdefault("Fmdb", row.get("FmdbPath"))
        for alias in _actor_info_row_aliases(compat):
            if alias and alias not in actor_info:
                actor_info[alias] = compat
                actor_info[alias.casefold()] = compat
                added += 1
    if added:
        _report_append(report_lines, "RSDB_WALLA_ALIAS integrated rows=%d aliases_added=%d" % (len(rows), added))
    return added


# Graffiti implementation lives in graffiti.py; install it into this module's
# globals so existing operator/UI code and saved property names remain stable.
# Reload the split module on add-on reload so Blender does not keep an older
# graffiti helper signature in sys.modules after installing a patched ZIP.
from . import graffiti as _osi_graffiti_impl
try:
    import importlib as _osi_importlib
    _osi_graffiti_impl = _osi_importlib.reload(_osi_graffiti_impl)
except Exception:
    pass
_osi_graffiti_impl.install_into(globals())

def _store_team_color_rsdb_scene_metadata(report_lines=None):
    """Store compact TeamColorDataSet/TeamColorOffset RSDB data on the Blender scene for future linking."""
    scene = bpy.context.scene
    for prefix in ("TeamColorDataSet", "TeamColorOffset"):
        table = _optional_rsdb(prefix)
        rows = table.get("rows") if isinstance(table, dict) else None
        if not isinstance(rows, list):
            continue
        try:
            scene["splatoon_rsdb_%s_count" % prefix] = len(rows)
            scene["splatoon_rsdb_%s_rowids" % prefix] = [str(r.get("__RowId", "")) for r in rows[:128] if isinstance(r, dict)]
            # Store a compact sample rather than the entire table to avoid bloating .blend files.
            scene["splatoon_rsdb_%s_sample" % prefix] = _idprop_safe(rows[:8])
            _report_append(report_lines, "RSDB_TEAMCOLOR_STORED prefix=%r rows=%d scene_props=compact" % (prefix, len(rows)))
        except Exception as exc:
            _report_append(report_lines, "RSDB_TEAMCOLOR_STORE_FAILED prefix=%r error=%s" % (prefix, exc))

def _iter_actor_info_table_candidates(explicit, stage_dir, models_dir):
    """Yield explicit and auto-detected ActorInfo/MapObjReplicaInfo tables."""
    seen = set()

    def add(path):
        if not path:
            return
        path = Path(path)
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        yield path

    if explicit:
        yield from add(Path(bpy.path.abspath(explicit)))

    prefixes = ("ActorInfo.Product", "MapObjReplicaInfo.Product")
    # Prefer compressed b10/product tables when both compressed and plain files
    # exist, but plain BYML remains a fallback candidate.
    suffixes = (".rstbl.byml.zs", ".rstbl.byml")
    for base in (stage_dir, models_dir):
        if not base:
            continue
        base = Path(base)
        if not base.exists():
            continue
        for prefix in prefixes:
            for suffix in suffixes:
                for path in base.glob(prefix + "*" + suffix):
                    yield from add(path)


def _actor_info_row_aliases(row):
    aliases = []
    for key in ("__RowId", "Name", "Gyaml", "ModelName", "UnitConfigName"):
        value = row.get(key, "")
        for candidate in (value, _clean_actor_token(value)):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in aliases:
                aliases.append(candidate)
    fmdb = str(row.get("Fmdb", "") or "")
    if fmdb:
        for candidate in _fmdb_model_candidates(fmdb):
            if candidate and candidate not in aliases:
                aliases.append(candidate)
    return aliases


def _load_actor_info_table(prefs, models_dir, *, required=False):
    # Explicit preference wins; otherwise auto-detect a table next to the stage
    # file or inside the models folder.  Cache parsed tables by path and build
    # aliases up front so per-actor lookups are cheap.
    global last_actor_info_table_path, last_actor_info_table_alias_count
    explicit_path = _effective_actor_info_explicit_path(prefs, models_dir)
    explicit = str(explicit_path) if explicit_path else ""
    stage_dir = Path(stageFile).parent if stageFile else Path()
    candidates = list(_iter_actor_info_table_candidates(explicit, stage_dir, models_dir))
    errors = []
    loaded_any_candidate = False
    for path in candidates:
        if not path.is_file():
            errors.append("%s: file not found" % path)
            continue
        loaded_any_candidate = True
        cache_key = str(path.resolve())
        if cache_key in _ACTOR_INFO_CACHE:
            table = _ACTOR_INFO_CACHE[cache_key]
            last_actor_info_table_path = str(path)
            last_actor_info_table_alias_count = len(table)
            if not table:
                errors.append("%s: cached table is empty" % path)
                continue
            print("Using cached Splatoon 3 actor/replica table: %s (%d aliases)" % (path, len(table)))
            return table
        try:
            raw = _read_byml_or_zs(path)
            rows = byml.Byml(raw).parse()
            if not isinstance(rows, list):
                raise ActorInfoTableError("Parsed table root is %s, expected list of rows." % type(rows).__name__)
            table = {}
            row_count = 0
            rows_with_fmdb = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_count += 1
                if row.get("Fmdb"):
                    rows_with_fmdb += 1
                for alias in _actor_info_row_aliases(row):
                    table.setdefault(alias, row)
                    table.setdefault(alias.casefold(), row)
            if row_count <= 0:
                raise ActorInfoTableError("Parsed table has no dictionary rows.")
            if not table:
                raise ActorInfoTableError("Parsed table produced zero aliases from %d rows." % row_count)
            # ActorInfo/MapObj tables are only useful for BFRES resolution when
            # at least some rows contain Fmdb paths.  Failing here avoids the
            # silent all-raw-name import seen with bad .byml.zs reads.
            if rows_with_fmdb <= 0:
                raise ActorInfoTableError("Parsed table has %d rows but zero Fmdb model paths." % row_count)
            _ACTOR_INFO_CACHE[cache_key] = table
            last_actor_info_table_path = str(path)
            last_actor_info_table_alias_count = len(table)
            print("Loaded Splatoon 3 actor/replica table: %s (%d rows, %d aliases, %d Fmdb rows)" % (path, row_count, len(table), rows_with_fmdb))
            return table
        except Exception as exc:
            detail = "%s: %s" % (path, str(exc) or exc.__class__.__name__)
            errors.append(detail)
            print("Failed to load actor/replica table: %s" % path)
            traceback.print_exc()
            # Explicit table failures are never silently skipped.  Auto-detected
            # failures can fall through to a later candidate, e.g. compressed
            # table fails but plain .byml exists.
            if explicit:
                break
    if required:
        if not candidates:
            raise ActorInfoTableError(
                "No ActorInfo/MapObjReplicaInfo Product*.rstbl.byml(.zs) table was found. "
                "Set Splatoon 3 RomFS Root to the folder containing RSDB/ActorInfo.Product.b10.rstbl.byml.zs."
            )
        if not loaded_any_candidate:
            raise ActorInfoTableError("ActorInfo/MapObjReplicaInfo table candidate was found but not readable: " + "; ".join(errors))
        raise ActorInfoTableError("Failed to load a usable ActorInfo/MapObjReplicaInfo table: " + "; ".join(errors))
    return {}





def _stage_collection_name():
    if stageFile:
        return _safe_collection_name(Path(stageFile).name, "ImportedStage")
    return "ImportedStage"










# Extracted helper block moved to split module (original lines around 1921).


# Extracted helper block moved to split module (original lines around 1966).


# Extracted helper block moved to split module (original lines around 2513).











def _append_texture_diag_summary(report_lines):
    """Append lightweight counts derived from diagnostic report lines."""
    if report_lines is None:
        return
    prefixes = (
        "TEXTURE_CACHE_HIT", "TEXTURE_CACHE_STORE", "TEXTURE_CACHE_INVALID",
        "MATERIAL_CACHE_HIT", "MATERIAL_CACHE_STORE", "MATERIAL_FAIL_DETAIL",
        "MATERIAL_TEXTURE_MISSING", "IMAGE_CREATE", "BFRES_IMPORT_CONTEXT",
    )
    counts = {}
    invalid_by_reason = {}
    for line in report_lines:
        for prefix in prefixes:
            if str(line).startswith(prefix):
                counts[prefix] = counts.get(prefix, 0) + 1
                if prefix in ("TEXTURE_CACHE_INVALID", "MATERIAL_FAIL_DETAIL"):
                    reason = "unknown"
                    text = str(line)
                    if "reason=" in text:
                        reason = text.split("reason=", 1)[1].split(" ", 1)[0].strip("\'\"")
                    elif "error=" in text:
                        reason = text.split("error=", 1)[1].split(" ", 1)[0].strip("\'\"")
                    invalid_by_reason[reason] = invalid_by_reason.get(reason, 0) + 1
                break
    report_lines.append("TEXTURE_DIAG_SUMMARY counts=%s invalid_or_fail_reasons=%s images_final=%d materials_final=%d" % (counts, invalid_by_reason, len(bpy.data.images), len(bpy.data.materials)))


def _addon_version_string():
    try:
        import importlib
        pkg = importlib.import_module(__package__)
        version = getattr(pkg, "bl_info", {}).get("version", None)
        if version:
            return ".".join(str(v) for v in version)
    except Exception:
        pass
    return "unknown"

def _blender_version_string():
    try:
        return "%d.%d.%d" % tuple(bpy.app.version[:3])
    except Exception:
        return str(getattr(bpy.app, "version_string", "unknown"))

def _write_import_report_file(schema_name, objects_len, models_dir, imported_count, missing_count, skipped_count, report_lines):
    """Write a persistent OSI import report into Blender's temp directory."""
    global last_import_report_path
    temp_dir = bpy.app.tempdir or tempfile.gettempdir()
    try:
        Path(temp_dir).mkdir(parents=True, exist_ok=True)
    except Exception:
        temp_dir = tempfile.gettempdir()
    stage_name = Path(stageFile).stem if stageFile else "stage"
    safe_stage = "".join(c if c.isalnum() or c in "._-" else "_" for c in stage_name) or "stage"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    verbosity = str(globals().get("report_verbosity", "COMPACT") or "COMPACT").upper()
    report_path = Path(temp_dir) / ("OSI_Import_Report_%s_%s_%s.txt" % (safe_stage, stamp, verbosity.lower()))
    selected_lines = _select_import_report_lines(report_lines)
    header = [
        "OSI / Splatoon 3 BFRES Import Report",
        "Generated: %s" % datetime.now().isoformat(timespec="seconds"),
        "Add-on version: %s" % _addon_version_string(),
        "Blender version: %s" % _blender_version_string(),
        "Stage BYML: %s" % stageFile,
        "Schema: %s" % schema_name,
        "Actors in BYML: %d" % objects_len,
        "Models dir: %s" % models_dir,
        "Summary: imported_objects=%d missing_or_failed=%d skipped=%d" % (imported_count, missing_count, skipped_count),
        "Import mode: %s" % str(globals().get("import_mode", "FULL")),
        "Skip non-renderable actors: %s" % bool(globals().get("skip_non_renderable_actors", True)),
        "Import geometry: %s" % bool(globals().get("import_geometry", True)),
        "Import non-geometry objects: %s" % bool(globals().get("import_non_geometry_objects", True)),
        "Import graffiti: %s" % bool(globals().get("import_graffiti", True)),
        "Import background: %s" % bool(globals().get("import_background", False)),
        "Report verbosity: %s" % verbosity.lower(),
        "Texture debug diagnostics: %s" % (verbosity == "DEBUG_TEXTURE"),
        "",
        "Details:",
    ]
    text = "\n".join(header + selected_lines) + "\n"
    report_path.write_text(text, encoding="utf-8")
    last_import_report_path = str(report_path)
    print("OSI import report written to: %s" % report_path)
    return str(report_path)












def _current_pack_resolver(files=None):
    resolver = globals().get("stagePackResolver", None)
    if resolver is not None:
        return resolver
    prefs = None
    root = None
    try:
        prefs = _addon_preferences()
        root = _romfs_root_path(prefs)
    except Exception:
        root = None
    return PackResourceResolver(files or globals().get("stagePackFiles", {}) or {}, root, globals().get("stagePackPath", ""))


def _pack_get_bytes(files, resource_ref):
    resolved = _current_pack_resolver(files).resolve(resource_ref)
    if resolved.get("bytes") is not None:
        return resolved.get("name"), resolved.get("bytes")
    return None, None


def _pack_resolve_bytes(files, resource_ref, report_lines=None, required=False, tag="SCENE_COMPONENT"):
    resolved = _current_pack_resolver(files).resolve(resource_ref)
    if resolved.get("bytes") is None:
        if required:
            extra = " error=%r" % resolved.get("error") if resolved.get("error") else ""
            _report_append(report_lines, "%s_MISSING ref=%r candidates=%s%s" % (tag, resource_ref, resolved.get("candidates"), extra))
        return resolved
    _report_append(report_lines, "%s_RESOLVED ref=%r location=%s path=%r" % (tag, resource_ref, resolved.get("location"), resolved.get("name")))
    return resolved


def _pack_parse_byml(files, resource_ref, report_lines=None, required=False):
    resolved = _pack_resolve_bytes(files, resource_ref, report_lines, required, tag="SCENE_COMPONENT")
    payload = resolved.get("bytes")
    name = resolved.get("name")
    if payload is None:
        return None, None
    try:
        return name, byml.Byml(payload).parse()
    except Exception as exc:
        _report_append(report_lines, "SCENE_COMPONENT_PARSE_FAILED path=%r location=%s error=%s" % (name, resolved.get("location"), exc))
        return name, None

def _pack_stage_stem():
    pack_path = str(globals().get("stagePackPath", "") or "")
    if pack_path:
        return Path(pack_path).name.replace(".pack.zs", "").replace(".pack", "")
    stage = str(globals().get("stageFile", "") or "")
    if "::" in stage:
        stage = stage.split("::", 1)[1]
    name = Path(stage).name
    for suffix in (".bcett.byml", ".byml"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return Path(name).stem


def _pack_find_main_scene_param(files):
    stem = _pack_stage_stem()
    preferred_names = [
        "Scene/%s.engine__scene__SceneParam.bgyml" % stem,
        "Scene/%s.engine__scene_SceneParam.bgyml" % stem,
    ]
    for preferred in preferred_names:
        if preferred in files:
            return preferred
    matches = [n for n in files if n.startswith("Scene/") and (n.endswith(".engine__scene__SceneParam.bgyml") or n.endswith(".engine__scene_SceneParam.bgyml"))]
    non_parent = [n for n in matches if "Parent" not in Path(n).name]
    return (sorted(non_parent or matches)[0] if matches else None)


# Extracted helper block moved to split module (original lines around 3718).


def _import_pack_scene_components(stage_collection, prefs, models_dir, import_cache, report_lines):
    files = globals().get("stagePackFiles", {}) or {}
    if not files:
        return 0
    scene_name = _pack_find_main_scene_param(files)
    if not scene_name:
        report_lines.append("SCENE_COMPONENTS none reason=no_scene_param_in_pack")
        return 0
    scene_param = None
    try:
        scene_param = byml.Byml(files[scene_name]).parse()
    except Exception as exc:
        report_lines.append("SCENE_COMPONENT_PARSE_FAILED path=%r error=%s" % (scene_name, exc))
        return 0
    components = scene_param.get("Components", {}) if isinstance(scene_param, dict) else {}
    if not isinstance(components, dict):
        report_lines.append("SCENE_COMPONENTS none path=%r reason=no_components_dict" % scene_name)
        return 0
    report_lines.append("SCENE_COMPONENTS loaded path=%r keys=%s" % (scene_name, sorted(components.keys())))
    count = 0
    for key, ref in sorted(components.items()):
        key_cf = str(key).casefold()
        if key == "VersusMapInfo":
            report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=out_of_scope" % (key, ref))
            continue
        if key == "SceneGraffitiPlacementData":
            if bool(globals().get("import_graffiti", True)):
                
                try:
                    count += _import_pack_graffiti(files, ref, stage_collection, report_lines, full_report_enabled=_is_full_report())
                except TypeError as exc:
                    if "full_report_enabled" not in str(exc):
                        raise
                    report_lines.append("GRAFFITI_IMPORT_RETRY reason=stale_helper_signature error=%s" % exc)
                    count += _import_pack_graffiti(files, ref, stage_collection, report_lines)
            else:
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=graffiti_disabled" % (key, ref))
            continue
        if key == "VersusCamera":
            if bool(globals().get("import_non_geometry_objects", True)):
                count += _import_pack_cameras(files, ref, stage_collection, report_lines)
            else:
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=non_geometry_disabled" % (key, ref))
        elif key == "SceneGfxFieldEnv":
            if bool(globals().get("import_non_geometry_objects", True)):
                count += _import_pack_environment(files, ref, stage_collection, report_lines, prefs=prefs, models_dir=models_dir, import_cache=import_cache)
            else:
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=non_geometry_disabled" % (key, ref))
        elif key == "SceneBake":
            # Recognized as the baked-lighting source, but intentionally not imported yet.
            name, data = _pack_parse_byml(files, ref, report_lines, required=False)
            if isinstance(data, dict):
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r resolved=%r reason=recognized_deferred_baked_lighting" % (key, ref, name))
            else:
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=recognized_deferred_baked_lighting" % (key, ref))
        elif key == "SceneSoundEnv":
            name, data = _pack_parse_byml(files, ref, report_lines, required=False)
            if isinstance(data, dict):
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r resolved=%r reason=recognized_deferred_sound_env" % (key, ref, name))
            else:
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=recognized_deferred_sound_env" % (key, ref))
        elif "ocean" in key_cf or "water" in key_cf:
            if bool(globals().get("import_non_geometry_objects", True)):
                count += _import_pack_water(files, stage_collection, prefs, models_dir, import_cache, report_lines)
            else:
                report_lines.append("SCENE_COMPONENT_SKIPPED key=%r ref=%r reason=non_geometry_disabled" % (key, ref))
        else:
            report_lines.append("SCENE_COMPONENT_UNKNOWN key=%r ref=%r" % (key, ref))
    # Some packs do not list ocean/water in Components.  Still attempt the safe
    # water metadata pass once, but keep it under the Non-Geometry toggle.
    if bool(globals().get("import_non_geometry_objects", True)):
        count += _import_pack_water(files, stage_collection, prefs, models_dir, import_cache, report_lines)
    else:
        report_lines.append("SCENE_WATER_SKIPPED reason=non_geometry_disabled")
    return count


# Split importer helper modules. Import here so their dependency snapshots can see
# the core helpers above.  Star imports intentionally skip private helper names,
# but the original importer still calls many underscore-prefixed helpers directly;
# install module-owned helper functions explicitly so the module split remains
# behavior-compatible after Blender reloads the add-on.
from . import actor_import as _osi_actor_import_impl
from . import non_geometry as _osi_non_geometry_impl
from . import rails as _osi_rails_impl

def _install_split_module_helpers(*_modules, replace_owned=False):
    """Bind implementation functions owned by split modules into addon.py.

    Core/shared helpers already defined in addon.py must never be overwritten by
    a split module.  This is important during Blender add-on reloads: addon.py
    can temporarily see an older in-memory actor_import/non_geometry module
    before __init__.py reloads that module.  Replacing a core helper with a
    stale wrapper was the cause of the 0.8.89-0.8.91 recursion failures.

    When *replace_owned* is true, only functions whose current implementation
    is itself owned by one of the split modules are refreshed.
    """
    _owned_modules = {getattr(_module, "__name__", "") for _module in _modules}
    for _module in _modules:
        _module_name = getattr(_module, "__name__", "")
        for _name, _value in getattr(_module, "__dict__", {}).items():
            if _name.startswith("__") or not _name.startswith("_"):
                continue
            if not callable(_value):
                continue
            if getattr(_value, "__module__", None) != _module_name:
                continue
            _current = globals().get(_name)
            if _current is None:
                globals()[_name] = _value
                continue
            if replace_owned and getattr(_current, "__module__", None) in _owned_modules:
                globals()[_name] = _value

_install_split_module_helpers(_osi_actor_import_impl, _osi_non_geometry_impl)

def _sync_split_module_dependencies(*_modules):
    """Refresh explicit addon-owned dependencies in split modules.

    Unlike the old namespace snapshot, this is deterministic and is safe after
    Blender reloads modules in any order.
    """
    missing=[]
    addon_module = sys.modules.get(__name__)
    for module in _modules:
        sync = getattr(module, "_sync_addon_dependencies", None)
        if callable(sync):
            missing.extend("%s:%s" % (getattr(module, "__name__", "?"), name) for name in sync(addon_module))
    return missing

_sync_split_module_dependencies(_osi_actor_import_impl, _osi_non_geometry_impl)



_GEOMETRY_CUSTOM_PROP_KEEP_EXACT = {
    "bcett_actor",
    "bcett_actor_label",
    "bcett_hash",
    "bcett_srthash",
    "bcett_instanceid",
    "bcett_instance_id",
    "bcett_team",
    "spl_active_team",
    "spl_sponge_inflate",
    # Runtime properties required by live team-color and sponge controls.
    "spl_dynamic_team_color",
    "spl_team_color_kind",
    "spl_team_color_enabled",
    "bcett_sponge_root",
    "bcett_sponge_mesh_kind",
    "bcett_sponge_inflate_enabled",
    "bcett_sponge_bbox_half",
    "bcett_sponge_visual_offset_local",
    "bcett_sponge_offset_local",
    "bcett_sponge_body_center_local",
    "bcett_sponge_inflate_max_multiplier",
    "bcett_sponge_parts_corner_inset",
    "bcett_sponge_base_scale",
    "bcett_sponge_inflate_applied_value",
}


def _geometry_custom_prop_keep_key(key):
    key = str(key or "")
    if not key or key == "_RNA_UI":
        return True
    if key in _GEOMETRY_CUSTOM_PROP_KEEP_EXACT:
        return True
    key_cf = key.casefold()
    # Preserve all hash-like BCETT identifiers, including future hash fields.
    if key_cf.startswith("bcett_") and "hash" in key_cf:
        return True
    return False


def _iter_collection_objects_recursive(collection):
    if collection is None:
        return
    seen = set()
    stack = [collection]
    while stack:
        col = stack.pop()
        for obj in getattr(col, "objects", ()):
            try:
                ptr = obj.as_pointer()
            except Exception:
                ptr = id(obj)
            if ptr in seen:
                continue
            seen.add(ptr)
            yield obj
        for child in getattr(col, "children", ()):
            stack.append(child)


def _trim_geometry_custom_properties(stage_collection, report_lines=None):
    """Remove bulky import/debug metadata from objects under Geometry only.

    Non-Geometry Objects, Background, Environment helpers, and other non-geometry
    categories are intentionally left untouched.  Geometry keeps identity hashes
    plus small runtime properties needed for team colors and sponge inflation.
    """
    try:
        geometry_col = stage_collection.children.get("Geometry") if stage_collection is not None else None
    except Exception:
        geometry_col = None
    if geometry_col is None:
        return 0, 0
    objects_seen = 0
    removed = 0
    for obj in _iter_collection_objects_recursive(geometry_col):
        objects_seen += 1
        try:
            keys = [str(k) for k in obj.keys() if not _geometry_custom_prop_keep_key(k)]
        except Exception:
            keys = []
        for key in keys:
            try:
                del obj[key]
                removed += 1
            except Exception:
                pass
        # Prune UI metadata for removed keys so the Custom Properties panel stays clean.
        try:
            ui = obj.get("_RNA_UI", None)
            if isinstance(ui, dict):
                for key in list(ui.keys()):
                    if not _geometry_custom_prop_keep_key(key) and key not in obj:
                        del ui[key]
        except Exception:
            pass
    try:
        if report_lines is not None:
            report_lines.append("GEOMETRY_CUSTOM_PROPS_TRIMMED objects=%d removed=%d kept_policy=identity_hash_team_sponge_runtime" % (objects_seen, removed))
    except Exception:
        pass
    return objects_seen, removed


def run_import():
    prefs = _addon_preferences()
    session = ImportSession(prefs=prefs)
    session.report.full_report_check = _is_full_report
    try:
        _cleared = lifecycle.clear_runtime_caches()
        session.report.event("IMPORT_START_CACHE_CLEANUP", cleared_runtime_caches=_cleared)
    except Exception as exc:
        session.report.event("IMPORT_START_CACHE_CLEANUP_FAILED", error=str(exc))
    # Parse ActorInfo/MapObj table once per import.  Avoid stale session cache
    # when users swap between .byml and .byml.zs while testing.
    try:
        _ACTOR_INFO_CACHE.clear()
        _RSDB_TABLE_CACHE.clear()
        _OPTIONAL_RSDB_TABLES.clear()
        _clear_optional_rsdb_tables()
    except Exception:
        pass
    models_dir = _effective_models_dir(prefs)
    session.models_dir = models_dir
    try:
        session.romfs_root = _romfs_root_path(prefs)
        session.pack_context = PackContext(globals().get("stagePackPath", ""), globals().get("stagePackFiles", {}) or {})
        session.romfs_resolver = RomfsResolver(session.romfs_root, session.pack_context)
        session.rsdb_registry = RsdbRegistry()
    except Exception:
        pass
    if not models_dir or not models_dir.is_dir():
        raise FileNotFoundError(
            "Splatoon 3 RomFS folder has not been selected or is not valid. "
            "Open Edit > Preferences > Add-ons > Splatoon 3 Stage Importer and select the Splatoon 3 RomFS folder."
        )

    if stageFileBytes is not None:
        root = byml.Byml(stageFileBytes).parse()
    else:
        with open(stageFile, "rb") as f:
            root = byml.Byml(f.read()).parse()

    objects, schema_name = _objects_from_stage_root(root, scenario)
    actor_info = _load_actor_info_table(prefs, models_dir, required=True) if isinstance(root, dict) and isinstance(root.get("Actors"), list) else {}
    # Optional RSDB tables are loaded once per import. They refine helper classification,
    # Walla object resolution, graffiti links, and future team-color linking.
    report_lines = session.report
    report_lines.event("IMPORT_SESSION", modules="report/session/pack_context/romfs_resolver/rsdb_loader/transform", texture_cache_enabled=session.texture_cache_enabled, material_cache_enabled=session.material_cache_enabled)
    _load_optional_rsdb_tables(prefs, report_lines)
    _merge_walla_obj_info_into_actor_info(actor_info, report_lines)
    _store_team_color_rsdb_scene_metadata(report_lines)
    banc_anchor_index = _build_banc_anchor_index(root, objects) if isinstance(root, dict) else {}
    actor_hash_index = _build_actor_hash_index(objects) if isinstance(root, dict) else {}
    imported_count = 0
    imported_rail_hashes = set()
    missing_count = 0
    skipped_count = 0
    if actor_info:
        report_lines.append("MAPOBJ_TABLE loaded path=%r aliases=%d" % (globals().get("last_actor_info_table_path", ""), int(globals().get("last_actor_info_table_alias_count", 0))))
    import_cache = session.bfres_geometry_cache
    srthash_instance_cache = session.srthash_instance_cache
    stage_collection = _get_or_create_child_collection(bpy.context.scene.collection, _stage_collection_name())

    print("=== OSI BFRES import report ===")
    print("Schema: %s; actors in BYML: %d; models dir: %s" % (schema_name, len(objects), models_dir))
    try:
        bpy.context.window_manager.progress_begin(0, max(1, len(objects)))
    except Exception:
        pass

    _phase_images_start = len(bpy.data.images)
    _phase_mats_start = len(bpy.data.materials)
    report_lines.phase_start("BCETT_GEOMETRY", images=_phase_images_start, materials=_phase_mats_start)

    if isinstance(root, dict):
        # v0.8.07: BCETT Rails[] are path data, not standalone renderables.
        # Import rail visuals/path helpers from actor BancParam links instead:
        # InkRailOnline -> InkRail geometry, Gachiyagura -> tower rail visuals,
        # AILift/LiftRail-linked objects -> motion path helpers.
        _osi_rails.rail_array_skip_report(report_lines)

    for actor_index, entry in enumerate(objects):
        _progress_update(actor_index, len(objects), step=25)
        actor_label = (entry.get("Name") or entry.get("Gyaml") or entry.get("__RowId") or ("Actor_%04d" % actor_index)) if isinstance(entry, dict) else ("Actor_%04d" % actor_index)
        layer_label = entry.get("Layer", "NoLayer") if isinstance(entry, dict) else "NoLayer"
        native_kind = _native_non_geometry_kind(entry)
        # Do not fast-skip native helper-looking actors before BFRES lookup.
        # Some BCETT actors classify as locators/helpers but still resolve to real
        # geometry via ActorInfo or concrete BFRES paths; this is especially
        # important when Import Non-Geometry Objects is off but Import Geometry is on.
        field_root_name = _field_root_actor_name(entry)
        if field_root_name:
            names = [field_root_name]
            concrete_paths = []
            if _is_full_report():
                report_lines.append("FIELD_ROOT_EXACT actor=%r layer=%r target=%r reason=suppress_related_actorinfo_aliases" % (actor_label, layer_label, field_root_name))
        else:
            names = _candidate_model_names(entry, actor_info)
            concrete_paths = _candidate_model_paths_from_actorinfo(models_dir, entry, actor_info)
        sponge_target_names = None
        try:
            sponge_model_path, sponge_target_names = _sponge_model_override(models_dir, entry)
        except Exception:
            sponge_model_path, sponge_target_names = (None, None)
        if sponge_model_path is not None:
            model_path = sponge_model_path
            if sponge_target_names:
                names = list(sponge_target_names)
            report_lines.append("SPONGE_MODEL_OVERRIDE actor=%r layer=%r path=%s targets=%s" % (actor_label, layer_label, model_path.name, names))
        else:
            model_path = _find_bfres_model(models_dir, names, concrete_paths)

        # Geometry-first resolution: ActorInfo/table mappings and concrete BFRES
        # files win over native helper classification.  This is important for
        # Lft_/Mpt_ wrapper actors such as Lft_FldObj_LineExceptTri, which can
        # look like locators but still point to real FMDL geometry.
        if model_path is not None and native_kind:
            report_lines.append("GEOMETRY_FIRST actor=%r layer=%r native_type=%s path=%s tried_names=%s" % (actor_label, layer_label, native_kind, model_path.name, names))

        # Actor-linked rail dispatch runs before ordinary geometry/native-helper handling.
        # Rails[] are path data; the actor BancParam decides whether the path is an
        # InkRail visual, a Gachiyagura/tower rail visual, or a generic/Ailift
        # motion path.  Gachiyagura/Ailift actors continue through normal geometry
        # import after their path helpers are created; InkRailOnline is represented
        # by its reconstructed rail geometry and does not also import as a normal BFRES.
        rail_is_inkrail = bool(model_path is not None and _osi_rails.is_liftrail_inkrail_actor(entry, model_path))
        rail_is_ailift = bool(_osi_rails.is_ailift_bancparam_actor(entry))
        rail_is_gachiyagura = bool((not rail_is_ailift) and _osi_rails.is_gachiyagura_bancparam_actor(entry))
        if rail_is_inkrail or rail_is_gachiyagura or rail_is_ailift:
            report_lines.append(
                "RAIL_ACTOR_LINK_CLASSIFY actor=%r layer=%r inkrail=%s gachiyagura=%s ailift=%s model_path=%s"
                % (actor_label, layer_label, rail_is_inkrail, rail_is_gachiyagura, rail_is_ailift, model_path.name if model_path is not None else None)
            )
            if bool(globals().get("import_geometry", True)):
                if rail_is_inkrail:
                    try:
                        inkrail_count = _osi_rails.try_import_liftrail_inkrail_geometry(entry, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, banc_anchor_index)
                    except Exception as exc:
                        inkrail_count = None
                        report_lines.append("INKRAIL_IMPORT_FAILED actor=%r layer=%r error=%s" % (actor_label, layer_label, exc))
                    if inkrail_count is not None:
                        imported_count += int(inkrail_count)
                        continue
                if rail_is_ailift:
                    try:
                        rail_helper_count = _osi_rails.try_import_motion_path_rail_geometry(entry, actor_label, layer_label, prefs, import_cache, report_lines, stage_collection, banc_anchor_index, kind='ailift')
                        imported_count += int(rail_helper_count or 0)
                    except Exception as exc:
                        report_lines.append("AILIFT_RAIL_IMPORT_FAILED actor=%r layer=%r error=%s" % (actor_label, layer_label, exc))
                elif rail_is_gachiyagura:
                    try:
                        rail_helper_count = _osi_rails.try_import_gachiyagura_rail_geometry(entry, actor_label, layer_label, prefs, import_cache, report_lines, stage_collection, banc_anchor_index)
                        imported_count += int(rail_helper_count or 0)
                    except Exception as exc:
                        report_lines.append("GACHIYAGURA_RAIL_IMPORT_FAILED actor=%r layer=%r error=%s" % (actor_label, layer_label, exc))
            else:
                report_lines.append("RAIL_ACTOR_LINK_SKIPPED actor=%r layer=%r reason=geometry_disabled inkrail=%s gachiyagura=%s ailift=%s" % (actor_label, layer_label, rail_is_inkrail, rail_is_gachiyagura, rail_is_ailift))
                if rail_is_inkrail:
                    skipped_count += 1
                    continue

        if model_path is None and native_kind:
            if bool(globals().get("import_non_geometry_objects", True)):
                report_lines.append("NATIVE_FALLBACK actor=%r layer=%r type=%s reason=no_bfres tried_names=%s concrete_paths=%s" % (actor_label, layer_label, native_kind, names, [str(p) for p in concrete_paths]))
                native_objects = _import_native_non_geometry(entry, actor_label, layer_label, stage_collection, report_lines)
                imported_count += len(native_objects)
            else:
                skipped_count += 1
                report_lines.append("SKIPPED_HELPER actor=%r layer=%r reason=non_geometry_disabled type=%s" % (actor_label, layer_label, native_kind))
            continue

        if model_path is not None and not bool(globals().get("import_geometry", True)):
            skipped_count += 1
            report_lines.append("SKIP option actor=%r layer=%r reason=geometry_disabled path=%s targets=%s" % (actor_label, layer_label, model_path.name, names))
            continue

        if model_path is None and bool(globals().get("skip_non_renderable_actors", True)) and _is_non_renderable_actor(entry):
            skipped_count += 1
            report_lines.append("SKIPPED_HELPER actor=%r layer=%r reason=non_renderable_actor tried_names=%s concrete_paths=%s" % (actor_label, layer_label, names, [str(p) for p in concrete_paths]))
            continue

        if model_path is None:
            missing_count += 1
            line = "MISSING BFRES actor=%r layer=%r tried_names=%s concrete_paths=%s" % (actor_label, layer_label, names, [str(p) for p in concrete_paths])
            report_lines.append(line)
            print(line)
            continue

        is_splatoon_actor = isinstance(entry, dict) and "Gyaml" in entry
        location = _vector_from_any(entry.get("Translate", {}), 0.0, splatoon_axis_order=is_splatoon_actor)
        rotation_euler = _rotation_euler_from_entry(entry)
        scale = _vector_from_any(entry.get("Scale", {}), 1.0, splatoon_axis_order=False)
        if banc_anchor_index and is_splatoon_actor:
            location, rotation_euler, _anchor_applied = _apply_bancparam_anchor_override(
                entry, location, rotation_euler, banc_anchor_index, report_lines, actor_label
            )
        if is_splatoon_actor and _is_full_report():
            report_lines.append(_rotation_debug_line(actor_label, entry, rotation_euler, source="actor_or_anchor"))

        target_model_names = [field_root_name] if field_root_name else (list(sponge_target_names) if sponge_target_names else _target_fmdl_names_from_actorinfo(entry, actor_info))
        srthash_key = _srthash_instance_key(entry, actor_label, model_path, target_model_names)
        if _is_sponge_actor(entry):
            if srthash_key is not None:
                report_lines.append("SPONGE_SRTHASH_DISABLED actor=%r reason=per_instance_visual_offset_and_inflate" % actor_label)
            srthash_key = None
        if srthash_key is not None and srthash_key in srthash_instance_cache:
            cache_entry = srthash_instance_cache[srthash_key]
            source_collection = cache_entry.get("collection")
            if source_collection is not None and source_collection.name in bpy.data.collections:
                instance_location = Vector(location)
                sponge_world_offset = cache_entry.get("sponge_world_offset")
                if sponge_world_offset is not None:
                    try:
                        instance_location = instance_location + Vector(sponge_world_offset)
                        report_lines.append("SPONGE_SAFEPOS instance actor=%r srthash=%r reused_world_offset=%s final_loc=%s" % (
                            actor_label, _srthash_value(entry), tuple(round(float(v), 6) for v in sponge_world_offset), tuple(round(float(v), 6) for v in instance_location)
                        ))
                    except Exception:
                        instance_location = Vector(location)
                inst = _create_srthash_collection_instance(entry, actor_label, layer_label, source_collection, stage_collection, report_lines, instance_location, rotation_euler, scale)
                if sponge_world_offset is not None:
                    try:
                        inst["bcett_sponge_offset_method"] = "SafePosLinksMeshBBoxFromSRTHashSource"
                        inst["bcett_sponge_offset_world"] = _serializable_vector3(sponge_world_offset)
                        inst["bcett_sponge_final_location"] = _serializable_vector3(instance_location)
                    except Exception:
                        pass
                imported_count += 1
                continue

        try:
            # Sponge visual offsets and inflation controls are per instance: corner,
            # wall, and floor sponges using the same BFRES can need different mesh
            # data offsets.  Do not reuse the generic BFRES template cache here;
            # otherwise the first sponge's already-offset mesh data can be reused
            # and marked as already processed for later sponge instances.  This
            # keeps geometry post-processing independent of texture/material mode.
            _bfres_import_cache = {} if _is_sponge_actor(entry) else import_cache
            if _is_sponge_actor(entry):
                report_lines.append("SPONGE_BFRES_CACHE_BYPASS actor=%r reason=per_instance_mesh_offset_and_inflate_controls" % actor_label)
            imported_objects, bfres_messages, _cache_hit = _import_bfres_cached(
                str(model_path),
                prefs,
                _bfres_import_cache,
                report_lines,
                actor_label,
                name_prefix=(target_model_names[0] + "_" if target_model_names else (names[0] + "_" if names else "")),
                target_model_names=target_model_names,
            )
        except Exception as exc:
            missing_count += 1
            line = "FAILED BFRES actor=%r layer=%r path=%s targets=%s error=%s" % (actor_label, layer_label, model_path, target_model_names, exc)
            report_lines.append(line)
            print(line)
            continue

        for msg in bfres_messages:
            report_lines.append("BFRES actor=%r path=%s %s" % (actor_label, model_path.name, msg))

        if not imported_objects:
            missing_count += 1
            line = "NO OBJECTS actor=%r layer=%r path=%s targets=%s" % (actor_label, layer_label, model_path, target_model_names)
            report_lines.append(line)
            print(line)
            continue
        # Apply the BYML actor transform once to a single root empty. Applying
        # it to every imported BFRES child causes mesh offsets to be rotated a
        # second time, leaving origins/relationship lines correct but visible
        # meshes displaced.
        fallback_root_name = target_model_names[0] if target_model_names else (names[0] if names else "Actor")
        actor_root = _create_actor_root(entry, fallback_root_name, location, rotation_euler, scale)
        _set_bcett_transform_props(actor_root, entry, actor_label, location, rotation_euler, scale)
        _set_bcett_transform_props_many(imported_objects, entry, actor_label, location, rotation_euler, scale)
        if is_splatoon_actor and _is_full_report():
            report_lines.append("ROOT_DEBUG actor=%r root=%r loc=%s rot_deg=%s scale=%s cache_hit=%s child_roots_before_parent=[%s]" % (
                actor_label, actor_root.name, tuple(round(float(v), 6) for v in actor_root.location),
                tuple(round(math.degrees(v), 6) for v in actor_root.rotation_euler),
                tuple(round(float(v), 6) for v in actor_root.scale), _cache_hit, _object_transform_summary(imported_objects)
            ))
        _parent_roots_to_actor_root(imported_objects, actor_root)
        sponge_world_offset = None
        if is_splatoon_actor:
            before_sponge_location = Vector(actor_root.location)
            if _apply_sponge_safepos_bbox_correction(entry, actor_root, imported_objects, actor_hash_index, report_lines, actor_label):
                sponge_world_offset = Vector(actor_root.location) - before_sponge_location
                _set_bcett_transform_props(actor_root, entry, actor_label, actor_root.location, rotation_euler, scale)
                _set_bcett_transform_props_many(imported_objects, entry, actor_label, actor_root.location, rotation_euler, scale)
                try:
                    # Sponge correction now keeps the game actor root at the growth
                    # origin and offsets the imported visual roots.  Preserve the
                    # detailed per-child visual-offset metadata written by
                    # actor_import instead of overwriting it with the old root-delta
                    # correction fields.
                    for child_obj in imported_objects:
                        if "bcett_sponge_offset_method" not in child_obj:
                            child_obj["bcett_sponge_offset_method"] = actor_root.get("bcett_sponge_offset_method", "SafePosLinksDirectionalVisualOffset")
                        child_obj["bcett_sponge_origin_location"] = _serializable_vector3(actor_root.location)
                except Exception:
                    pass
                report_lines.append("ROOT_DEBUG_AFTER_SPONGE actor=%r root=%r loc=%s rot_deg=%s scale=%s sponge_world_offset=%s" % (
                    actor_label, actor_root.name, tuple(round(float(v), 6) for v in actor_root.location),
                    tuple(round(math.degrees(v), 6) for v in actor_root.rotation_euler),
                    tuple(round(float(v), 6) for v in actor_root.scale),
                    tuple(round(float(v), 6) for v in sponge_world_offset),
                ))

        _move_objects_to_collection([actor_root] + imported_objects, _layer_collection_for_entry(stage_collection, entry))
        if srthash_key is not None and srthash_key not in srthash_instance_cache:
            source_collection = _make_srthash_source_collection(actor_label, _srthash_value(entry), actor_root, imported_objects)
            srthash_instance_cache[srthash_key] = {"collection": source_collection, "sponge_world_offset": tuple(float(v) for v in sponge_world_offset) if sponge_world_offset is not None else None}
            report_lines.append("SRTHASH_SOURCE actor=%r layer=%r source_collection=%r srthash=%r objects=%d sponge_world_offset=%s" % (actor_label, layer_label, source_collection.name, _srthash_value(entry), len(imported_objects) + 1, tuple(round(float(v), 6) for v in sponge_world_offset) if sponge_world_offset is not None else None))
        imported_count += len(imported_objects)
        report_lines.append("IMPORTED actor=%r layer=%r path=%s targets=%s objects=%d" % (actor_label, layer_label, model_path.name, target_model_names, len(imported_objects)))

    report_lines.phase_end("BCETT_GEOMETRY", images=len(bpy.data.images), images_delta=len(bpy.data.images)-_phase_images_start, materials=len(bpy.data.materials), materials_delta=len(bpy.data.materials)-_phase_mats_start)

    if bool(globals().get("import_background", False)):
        _phase_images_start = len(bpy.data.images)
        _phase_mats_start = len(bpy.data.materials)
        report_lines.phase_start("BACKGROUND", images=_phase_images_start, materials=_phase_mats_start)
        try:
            bg_count = _osi_background.import_background_from_field_actorparam(
                root, objects, stage_collection, prefs, models_dir, import_cache, report_lines,
                _get_or_create_child_collection, _move_objects_to_collection, _import_bfres_cached, _set_bcett_transform_props_many
            )
            imported_count += int(bg_count or 0)
            report_lines.phase_end("BACKGROUND", images=len(bpy.data.images), images_delta=len(bpy.data.images)-_phase_images_start, materials=len(bpy.data.materials), materials_delta=len(bpy.data.materials)-_phase_mats_start, imported=int(bg_count or 0))
        except Exception as exc:
            report_lines.append("BACKGROUND_FAILED error=%s" % exc)
            report_lines.phase_end("BACKGROUND", images=len(bpy.data.images), images_delta=len(bpy.data.images)-_phase_images_start, materials=len(bpy.data.materials), materials_delta=len(bpy.data.materials)-_phase_mats_start, imported="FAILED")

    if globals().get("stagePackFiles", None):
        _phase_images_start = len(bpy.data.images)
        _phase_mats_start = len(bpy.data.materials)
        report_lines.phase_start("PACK_SCENE_COMPONENTS", images=_phase_images_start, materials=_phase_mats_start)
        try:
            component_count = _import_pack_scene_components(stage_collection, prefs, models_dir, import_cache, report_lines)
            imported_count += int(component_count or 0)
            report_lines.phase_end("PACK_SCENE_COMPONENTS", images=len(bpy.data.images), images_delta=len(bpy.data.images)-_phase_images_start, materials=len(bpy.data.materials), materials_delta=len(bpy.data.materials)-_phase_mats_start, imported=int(component_count or 0))
        except Exception as exc:
            report_lines.append("SCENE_COMPONENTS_FAILED error=%s" % exc)
            report_lines.phase_end("PACK_SCENE_COMPONENTS", images=len(bpy.data.images), images_delta=len(bpy.data.images)-_phase_images_start, materials=len(bpy.data.materials), materials_delta=len(bpy.data.materials)-_phase_mats_start, imported="FAILED")
            print("SCENE_COMPONENTS_FAILED error=%s" % exc)

    try:
        _cleanup_inkrail_full_templates(import_cache, report_lines)
    except Exception as exc:
        try:
            report_lines.append("INKRAIL_FULL_TEMPLATE_CLEANUP_FAILED error=%s" % exc)
        except Exception:
            pass

    try:
        _trim_geometry_custom_properties(stage_collection, report_lines)
    except Exception as exc:
        try:
            report_lines.append("GEOMETRY_CUSTOM_PROPS_TRIM_FAILED error=%s" % exc)
        except Exception:
            pass

    print("=== OSI BFRES import report details written to report file ===")
    summary = "OSI BFRES import complete (%s): %d Blender object(s) imported, %d model reference(s) missing, %d skipped." % (schema_name, imported_count, missing_count, skipped_count)
    print(summary)
    try:
        session.close()
    except Exception as exc:
        report_lines.append("IMPORT_SESSION_CLOSE_FAILED error=%s" % exc)
    _append_texture_diag_summary(report_lines)
    report_path = _write_import_report_file(schema_name, len(objects), models_dir, imported_count, missing_count, skipped_count, report_lines.lines if hasattr(report_lines, "lines") else report_lines)
    print("Report path: %s" % report_path)
    try:
        bpy.context.window_manager.progress_end()
    except Exception:
        pass
    return imported_count, missing_count
