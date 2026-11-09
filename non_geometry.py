# Extracted from addon.py to keep the main Blender operator module smaller.
# The functions here intentionally retain the original helper names/signatures.
# Legacy compatibility: some older extracted helpers still receive dependencies
# from addon.py at module load. New shared helpers should be imported explicitly
# from dedicated modules instead of added to this snapshot mechanism.

from __future__ import annotations

import sys
import math
import re
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from .actor_helpers import (
    clean_actor_token as _clean_actor_token,
    actor_first_value_by_key as _actor_first_value_by_key,
    entry_tokens as _entry_tokens,
    gfx_light_kind as _gfx_light_kind,
    is_supported_gfx_light_actor as _is_supported_gfx_light_actor,
    anchor_label as _anchor_label,
    entry_has_token as _entry_has_token,
    bancparam_reference_candidates as _bancparam_reference_candidates,
)
from .rsdb_helpers import annotate_object_with_rsdb as _annotate_object_with_rsdb
from .property_utils import set_props_from_mapping as _set_props_from_mapping
from .value_utils import numeric_from_any as _numeric_from_any, color_from_any as _color_from_any
from .actor_import import (
    _create_actor_root, _duplicate_linked_objects, _import_bfres_cached, _import_bfres,
    _top_level_import_roots, _set_bcett_transform_props,
    _set_bcett_transform_props_many, _object_transform_summary,
    _osi_sanitize_mesh_material_slots,
)

_ADDON_DEPENDENCIES = {
    "_bfres_fmdl_names_cached", "_current_pack_resolver", "_decompress_zstd_bytes",
    "_effective_bfres_texture_mode", "_effective_import_materials", "_effective_models_dir",
    "_find_bfres_model", "_is_full_report",
    "_pack_parse_byml", "_pack_stage_stem", "_report_append",
    "import_pack_extra_geometry",
}

def _sync_addon_dependencies(addon_module):
    missing=[]
    for name in sorted(_ADDON_DEPENDENCIES):
        if hasattr(addon_module, name):
            globals()[name] = getattr(addon_module, name)
        else:
            missing.append(name)
    return missing



# Shared collection and transform helpers are imported explicitly.  Keeping one
# implementation avoids reload-order-dependent fallback copies.
from .collection_utils import (
    safe_collection_name as _safe_collection_name,
    get_or_create_child_collection as _get_or_create_child_collection,
    move_objects_to_collection as _move_objects_to_collection,
)
from .transform import (
    to_float as _to_float,
    vector_tuple_from_any as _vector_from_any,
    raw_rotation_tuple as _raw_rotation_tuple,
    bcett_rotation_from_entry as _rotation_euler_from_entry,
)


def _disable_sky_shadow_ray_visibility(objects):
    """Disable only Object Properties > Visibility > Ray Visibility > Shadow.

    Sky objects should remain visible/renderable, but should not cast shadows.
    Apply recursively because BFRES imports often return armature/root objects whose
    renderable mesh descendants own the Cycles visibility flags.
    """
    seen = set()
    stack = list(objects or [])
    while stack:
        obj = stack.pop()
        if obj is None:
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            ptr = id(obj)
        if ptr in seen:
            continue
        seen.add(ptr)
        try:
            if hasattr(obj, "visible_shadow"):
                obj.visible_shadow = False
            obj["osi_sky_shadow_ray_disabled"] = True
        except Exception:
            pass
        try:
            stack.extend(list(obj.children))
        except Exception:
            pass


def _non_geometry_root_collection(stage_collection):
    return _get_or_create_child_collection(stage_collection, "Non-Geometry Objects")


def _non_geometry_collection(stage_collection, group_name):
    return _get_or_create_child_collection(_non_geometry_root_collection(stage_collection), group_name)


def _non_geometry_layer_collection(stage_collection, group_name, layer_label):
    """Return Non-Geometry Objects/<group>/<Layer>, creating collections as needed."""
    group_collection = _non_geometry_collection(stage_collection, group_name)
    return _get_or_create_child_collection(group_collection, _safe_collection_name(layer_label, "NoLayer"))


def _non_geometry_sound_layer_collection(stage_collection, subgroup_name, layer_label):
    """Return Non-Geometry Objects/Sound/<subgroup>/<Layer>."""
    sound_root = _non_geometry_collection(stage_collection, "Sound")
    subgroup_collection = _get_or_create_child_collection(sound_root, _safe_collection_name(subgroup_name, subgroup_name))
    return _get_or_create_child_collection(subgroup_collection, _safe_collection_name(layer_label, "NoLayer"))


def _non_geometry_rail_layer_collection(stage_collection, layer_label):
    """Return Non-Geometry Objects/Rail/<Layer> for reconstructed rail helpers."""
    return _non_geometry_layer_collection(stage_collection, "Rail", layer_label)


def _graffiti_root_collection(stage_collection):
    """Return the stage-local top-level Graffiti collection.

    Graffiti is intentionally kept as a sibling of Geometry and
    Non-Geometry Objects because it behaves like a decal/placement system
    rather than ordinary BCETT-rendered geometry or helper metadata.
    """
    return _get_or_create_child_collection(stage_collection, "Graffiti")


def _graffiti_group_collection(stage_collection, group_name="Graffiti Decals"):
    return _get_or_create_child_collection(_graffiti_root_collection(stage_collection), group_name)


def _graffiti_layer_collection(stage_collection, layer_label, group_name="Graffiti Decals"):
    """Return Graffiti/<group>/<Layer>, creating collections as needed."""
    group_collection = _graffiti_group_collection(stage_collection, group_name)
    return _get_or_create_child_collection(group_collection, _safe_collection_name(layer_label, "NoLayer"))


def _native_object_base_transform(entry, *, convert_scale_axes=False, scale_to_blender_empty_units=False):
    is_splatoon_actor = isinstance(entry, dict) and "Gyaml" in entry
    location = _vector_from_any(entry.get("Translate", {}) if isinstance(entry, dict) else {}, 0.0, splatoon_axis_order=is_splatoon_actor)
    rotation_euler = _rotation_euler_from_entry(entry)
    # Preserve Nintendo-authored scale values in Blender's visible object scale.
    # For Splatoon actors, convert stored [x,y,z] to Blender/world [x,z,y].
    # The separate display-size adjustment in _create_empty_native handles
    # Blender empty primitives being 2 units wide by default.
    scale = Vector(_vector_from_any(entry.get("Scale", {}) if isinstance(entry, dict) else {}, 1.0, splatoon_axis_order=bool(convert_scale_axes and is_splatoon_actor)))
    return location, rotation_euler, scale


def _native_safe_collection_name(actor_label, fallback):
    return _safe_collection_name(str(actor_label) if actor_label not in (None, "") else fallback, fallback)


def _is_flat_gameplay_region_actor(entry):
    # These areas are authored as flat gameplay footprints.  Their stored middle
    # component is usually a nominal thickness, so display them as planes.
    return _entry_has_token(entry, "changepaintablearea") or _entry_has_token(entry, "locatorspawnertargetcube")


def _area_visualizer_subgroup(entry):
    if _entry_has_token(entry, "changepaintablearea"):
        return "ChangePaintableArea"
    if _entry_has_token(entry, "locatorspfireworkexcludearea") or _entry_has_token(entry, "fireworkexcludearea"):
        return "LocatorSpFireworkExcludeArea"
    return None


def _non_geometry_area_collection(stage_collection, layer_label, subgroup=None):
    layer_collection = _non_geometry_layer_collection(stage_collection, "Areas", layer_label)
    if subgroup:
        return _get_or_create_child_collection(layer_collection, _safe_collection_name(subgroup, subgroup))
    return layer_collection


def _copy_native_transform_report_tuple(obj):
    return (
        tuple(round(float(v), 6) for v in obj.location),
        tuple(round(math.degrees(v), 6) for v in obj.rotation_euler),
        tuple(round(float(v), 6) for v in obj.scale),
    )


def _copy_scalar_custom_props(obj, entry, prefix="bcett"):
    """Copy useful scalar/list BCETT metadata to custom properties for debugging."""
    if not isinstance(entry, dict):
        return
    for key, value in entry.items():
        if key in {"Translate", "Rotate", "Scale", "Phive"}:
            continue
        prop_name = "%s_%s" % (prefix, str(key).replace(" ", "_"))
        try:
            if isinstance(value, (str, int, float, bool)) or value is None:
                obj[prop_name] = "" if value is None else value
            elif isinstance(value, (list, tuple)) and len(value) <= 8 and all(isinstance(v, (str, int, float, bool)) or v is None for v in value):
                obj[prop_name] = ["" if v is None else v for v in value]
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    sub_name = "%s_%s_%s" % (prefix, str(key).replace(" ", "_"), str(sub_key).replace(" ", "_"))
                    if isinstance(sub_value, (str, int, float, bool)) or sub_value is None:
                        obj[sub_name] = "" if sub_value is None else sub_value
                    elif isinstance(sub_value, (list, tuple)) and len(sub_value) <= 8 and all(isinstance(v, (str, int, float, bool)) or v is None for v in sub_value):
                        obj[sub_name] = ["" if v is None else v for v in sub_value]
        except Exception:
            pass


def _is_sound_shape_actor(entry):
    return any(token.casefold().startswith("soundshape") or token.casefold().startswith("sound_shape") for token in _entry_tokens(entry))


def _is_camera_actor(entry):
    for token in _entry_tokens(entry):
        lower = token.casefold()
        if lower.startswith("camera") or lower.startswith("snapshot") or "locatorawardphotomaker" in lower:
            return True
    return False


def _is_env_effect_locator_actor(entry):
    return any("splfieldenveffectlocator" in token.casefold() or "fieldenveffectlocator" in token.casefold() for token in _entry_tokens(entry))


def _is_area_visualizer_actor(entry):
    for token in _entry_tokens(entry):
        lower = token.casefold()
        if (
            "changepaintablearea" in lower or
            "painttargetarea" in lower or
            lower.endswith("area") or
            "area_" in lower or
            "_area" in lower or
            "rejectarea" in lower or
            "routearea" in lower or
            "hikikomoriarea" in lower or
            "fireworkexcludearea" in lower or
            "heightlimitarea" in lower or
            "spawnertarget" in lower
        ):
            return True
    return False


def _is_generic_locator_actor(entry):
    for token in _entry_tokens(entry):
        lower = token.casefold()
        if lower.startswith("locator") or lower.startswith("generallocator") or lower.startswith("mpt_"):
            return True
    return False


def _native_non_geometry_kind(entry):
    if _is_supported_gfx_light_actor(entry):
        return "LIGHT"
    if _is_sound_shape_actor(entry):
        return "SOUND"
    if _is_camera_actor(entry):
        return "CAMERA"
    if _is_env_effect_locator_actor(entry):
        return "EFFECT_LOCATOR"
    if _is_area_visualizer_actor(entry):
        return "AREA"
    if _is_generic_locator_actor(entry):
        return "LOCATOR"
    return None


def _create_empty_native(entry, actor_label, stage_collection, group_name, empty_type="PLAIN_AXES", display_size=1.0, report_lines=None, report_tag="NON_GEOMETRY_EMPTY", *, collection_override=None, convert_scale_axes=True, scale_to_blender_empty_units=False):
    location, rotation_euler, scale = _native_object_base_transform(entry, convert_scale_axes=convert_scale_axes, scale_to_blender_empty_units=scale_to_blender_empty_units)
    name = _native_safe_collection_name(actor_label, group_name)
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = empty_type
    # Blender cube/sphere empty displays are 2 units wide at display_size=1.
    # Use display_size=0.5 for visualizers whose final displayed dimensions
    # should equal Nintendo's converted object scale while leaving obj.scale
    # readable as the authored [x,z,y] dimensions.
    visual_display_size = float(display_size)
    if scale_to_blender_empty_units:
        visual_display_size *= 0.5
    obj.empty_display_size = float(max(0.05, visual_display_size))
    obj.location = location
    obj.rotation_euler = rotation_euler
    obj.scale = scale
    _copy_scalar_custom_props(obj, entry)
    _annotate_object_with_rsdb(obj, entry)
    layer_label = entry.get("Layer", "NoLayer") if isinstance(entry, dict) else "NoLayer"
    target_collection = collection_override or _non_geometry_layer_collection(stage_collection, group_name, layer_label)
    _move_objects_to_collection([obj], target_collection)
    if report_lines is not None:
        coll_label = "%s/%s" % (group_name, _safe_collection_name(layer_label, "NoLayer"))
        if collection_override is not None:
            coll_label = collection_override.name
        report_lines.append("%s actor=%r layer=%r collection=%r loc=%s rot_deg=%s scale=%s empty_type=%s display_size=%s scale_axes=xzy object_scale_is_nintendo_dims=True final_extent_units=True" % (
            report_tag, actor_label, layer_label, coll_label,
            tuple(round(float(v), 6) for v in obj.location),
            tuple(round(math.degrees(v), 6) for v in obj.rotation_euler),
            tuple(round(float(v), 6) for v in obj.scale), empty_type, round(float(obj.empty_display_size), 6),
        ))
    return obj


def _import_gfx_light(entry, actor_label, layer_label, stage_collection, report_lines):
    is_splatoon_actor = isinstance(entry, dict) and "Gyaml" in entry
    location = _vector_from_any(entry.get("Translate", {}), 0.0, splatoon_axis_order=is_splatoon_actor)
    rotation_euler = _rotation_euler_from_entry(entry)
    scale = _vector_from_any(entry.get("Scale", {}), 1.0, splatoon_axis_order=False)
    light_type = _gfx_light_kind(entry) or "POINT"

    color_value, color_path = _actor_first_value_by_key(entry, ("diffuse", "color"))
    if color_value is None:
        color_value, color_path = _actor_first_value_by_key(entry, ("color",))
    color = _color_from_any(color_value, (1.0, 1.0, 1.0))

    energy = None
    energy_path = ""
    for parts in (("intensity",), ("power",), ("energy",), ("brightness",), ("diffuse", "power")):
        value, path = _actor_first_value_by_key(entry, parts)
        energy = _numeric_from_any(value, None)
        if energy is not None:
            energy_path = path
            break
    if energy is None:
        energy = 1000.0 if light_type != "SUN" else 1.0
    elif light_type != "SUN" and energy <= 20.0:
        # Small game-light intensities are otherwise nearly invisible in Blender.
        energy *= 1000.0

    radius = None
    radius_path = ""
    for parts in (("radius",), ("range",), ("distance",), ("atten",)):
        value, path = _actor_first_value_by_key(entry, parts)
        radius = _numeric_from_any(value, None)
        if radius is not None:
            radius_path = path
            break
    if radius is None:
        radius = max(1.0, float(scale[0]) if scale else 1.0)

    light_name = _safe_collection_name(str(actor_label), "GfxLight")
    light_data = bpy.data.lights.new(light_name, type=light_type)
    light_data.color = color
    light_data.energy = float(max(0.0, energy))
    try:
        light_data.cycles.max_bounces = 12
    except Exception:
        pass
    if light_type in {"POINT", "SPOT"}:
        light_data.shadow_soft_size = float(max(0.001, radius))
    if light_type == "SPOT":
        spot_value, spot_path = _actor_first_value_by_key(entry, ("spot", "angle"))
        if spot_value is None:
            spot_value, spot_path = _actor_first_value_by_key(entry, ("angle",))
        spot_angle = _numeric_from_any(spot_value, None)
        if spot_angle is not None:
            # Accept either radians or degrees; BCETT values near/above pi are likely degrees.
            light_data.spot_size = math.radians(spot_angle) if abs(spot_angle) > math.pi * 2.0 else float(spot_angle)
        else:
            spot_path = ""
        blend_value, blend_path = _actor_first_value_by_key(entry, ("blend",))
        blend = _numeric_from_any(blend_value, None)
        if blend is not None:
            light_data.spot_blend = max(0.0, min(1.0, float(blend)))
    else:
        spot_path = ""
        blend_path = ""
    if light_type == "AREA":
        size_value, size_path = _actor_first_value_by_key(entry, ("size",))
        size = _numeric_from_any(size_value, None)
        if size is None:
            size = radius
        light_data.size = float(max(0.001, size))
    else:
        size_path = ""

    obj = bpy.data.objects.new(light_name, light_data)
    obj.location = location
    obj.rotation_euler = rotation_euler
    obj.scale = scale
    _move_objects_to_collection([obj], _non_geometry_layer_collection(stage_collection, "Lights", layer_label))
    report_lines.append(
        "NON_GEOMETRY_LIGHT actor=%r layer=%r collection=%r type=%s loc=%s rot_deg=%s scale=%s color=%s color_path=%r energy=%s energy_path=%r radius=%s radius_path=%r spot_path=%r blend_path=%r size_path=%r max_bounces=%s"
        % (
            actor_label, layer_label, "Lights/%s" % _safe_collection_name(layer_label, "NoLayer"), light_type,
            tuple(round(float(v), 6) for v in obj.location),
            tuple(round(math.degrees(v), 6) for v in obj.rotation_euler),
            tuple(round(float(v), 6) for v in obj.scale),
            tuple(round(float(v), 6) for v in color), color_path,
            round(float(light_data.energy), 6), energy_path,
            round(float(radius), 6), radius_path, spot_path, blend_path, size_path,
            getattr(getattr(light_data, "cycles", None), "max_bounces", "unsupported"),
        )
    )
    return obj



def _import_sound_shape(entry, actor_label, layer_label, stage_collection, report_lines):
    location, rotation_euler, scale = _native_object_base_transform(entry, convert_scale_axes=True)
    name = _native_safe_collection_name(actor_label, "SoundShape")
    speaker = bpy.data.speakers.new(name)
    # Keep defaults safe; map any obvious scalar fields when present.
    vol_value, vol_path = _actor_first_value_by_key(entry, ("volume",))
    volume = _numeric_from_any(vol_value, None)
    if volume is not None:
        try:
            speaker.volume = max(0.0, float(volume))
        except Exception:
            pass
    pitch_value, pitch_path = _actor_first_value_by_key(entry, ("pitch",))
    pitch = _numeric_from_any(pitch_value, None)
    if pitch is not None:
        try:
            speaker.pitch = max(0.01, float(pitch))
        except Exception:
            pass
    obj = bpy.data.objects.new(name, speaker)
    obj.location = location
    obj.rotation_euler = rotation_euler
    obj.scale = scale
    _copy_scalar_custom_props(obj, entry)
    _annotate_object_with_rsdb(obj, entry)
    _move_objects_to_collection([obj], _non_geometry_sound_layer_collection(stage_collection, "Sounds", layer_label))

    # Optional visible volume helper so SoundShape_Box/Sphere has a footprint.
    helper = None
    gyaml = str(entry.get("Gyaml", "") if isinstance(entry, dict) else "")
    if "box" in gyaml.casefold() or "sphere" in gyaml.casefold():
        helper_type = "SPHERE" if "sphere" in gyaml.casefold() else "CUBE"
        helper = bpy.data.objects.new(name + "_Volume", None)
        helper.empty_display_type = helper_type
        helper.empty_display_size = 1.0
        helper.location = location
        helper.rotation_euler = rotation_euler
        helper.scale = scale
        _copy_scalar_custom_props(helper, entry)
        _annotate_object_with_rsdb(helper, entry)
        _move_objects_to_collection([helper], _non_geometry_sound_layer_collection(stage_collection, "Sound Volumes", layer_label))
    report_lines.append("NON_GEOMETRY_SOUND actor=%r layer=%r collection=%r loc=%s rot_deg=%s scale=%s volume_path=%r pitch_path=%r helper=%s" % (
        actor_label, layer_label, "Sound/Sounds/%s" % _safe_collection_name(layer_label, "NoLayer"),
        tuple(round(float(v), 6) for v in obj.location),
        tuple(round(math.degrees(v), 6) for v in obj.rotation_euler),
        tuple(round(float(v), 6) for v in obj.scale), vol_path, pitch_path, bool(helper),
    ))
    return [obj] + ([helper] if helper else [])


def _import_camera_object(entry, actor_label, layer_label, stage_collection, report_lines):
    location, rotation_euler, scale = _native_object_base_transform(entry, convert_scale_axes=True)
    name = _native_safe_collection_name(actor_label, "Camera")
    cam = bpy.data.cameras.new(name)
    fov_value, fov_path = _actor_first_value_by_key(entry, ("fov",))
    fov = _numeric_from_any(fov_value, None)
    if fov is not None:
        try:
            cam.angle = math.radians(float(fov)) if float(fov) > math.pi else float(fov)
        except Exception:
            pass
    lens_value, lens_path = _actor_first_value_by_key(entry, ("lens",))
    lens = _numeric_from_any(lens_value, None)
    if lens is not None:
        try:
            cam.lens = max(1.0, float(lens))
        except Exception:
            pass
    obj = bpy.data.objects.new(name, cam)
    obj.location = location
    obj.rotation_euler = rotation_euler
    obj.scale = scale
    _copy_scalar_custom_props(obj, entry)
    _annotate_object_with_rsdb(obj, entry)
    _move_objects_to_collection([obj], _non_geometry_layer_collection(stage_collection, "Cameras", layer_label))
    report_lines.append("NON_GEOMETRY_CAMERA actor=%r layer=%r collection=%r loc=%s rot_deg=%s scale=%s fov_path=%r lens_path=%r" % (
        actor_label, layer_label, "Cameras/%s" % _safe_collection_name(layer_label, "NoLayer"),
        tuple(round(float(v), 6) for v in obj.location),
        tuple(round(math.degrees(v), 6) for v in obj.rotation_euler),
        tuple(round(float(v), 6) for v in obj.scale), fov_path, lens_path,
    ))
    return obj



def _mark_editor_helper_non_renderable(obj):
    """Keep helper meshes visible in the viewport but invisible to render engines.

    Some gameplay area helpers are real mesh planes/cubes so they can display as
    wire overlays in the viewport.  They are editor guides, not stage geometry,
    and must never contribute camera/shadow/diffuse/glossy visibility in Eevee
    or Cycles.
    """
    if obj is None:
        return
    stack = [obj]
    seen = set()
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        name = getattr(cur, "name_full", getattr(cur, "name", ""))
        if name in seen:
            continue
        seen.add(name)
        try:
            cur.hide_render = True
        except Exception:
            pass
        for attr in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission", "visible_shadow", "visible_volume_scatter"):
            try:
                if hasattr(cur, attr):
                    setattr(cur, attr, False)
            except Exception:
                pass
        try:
            cur["bcett_editor_helper_non_renderable"] = True
        except Exception:
            pass
        try:
            stack.extend(list(getattr(cur, "children", []) or []))
        except Exception:
            pass

def _import_flat_area_plane(entry, actor_label, layer_label, stage_collection, report_lines, collection_override=None):
    location, rotation_euler, final_scale = _native_object_base_transform(entry, convert_scale_axes=True, scale_to_blender_empty_units=False)
    width = max(0.001, abs(float(final_scale.x)))
    depth = max(0.001, abs(float(final_scale.y)))
    sx = float(final_scale.x) if abs(float(final_scale.x)) > 1e-8 else 1.0
    sy = float(final_scale.y) if abs(float(final_scale.y)) > 1e-8 else 1.0
    # Build the plane mesh in inverse-scaled local units, then set obj.scale to
    # Nintendo's converted [x,z,y].  This preserves the final imported footprint
    # while displaying the authored dimensions in Blender's Scale fields.
    local_width = width / abs(sx)
    local_depth = depth / abs(sy)
    name = _native_safe_collection_name(actor_label, "FlatArea")
    verts = [(-local_width * 0.5, -local_depth * 0.5, 0.0), (local_width * 0.5, -local_depth * 0.5, 0.0), (local_width * 0.5, local_depth * 0.5, 0.0), (-local_width * 0.5, local_depth * 0.5, 0.0)]
    faces = [(0, 1, 2, 3)]
    mesh = bpy.data.meshes.new(name + "_PlaneMesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler = rotation_euler
    obj.scale = final_scale
    obj.display_type = "WIRE"
    obj.show_in_front = True
    _mark_editor_helper_non_renderable(obj)
    _copy_scalar_custom_props(obj, entry)
    _annotate_object_with_rsdb(obj, entry)
    obj["non_geometry_flat_area"] = True
    obj["nintendo_scale_xyz_to_blender_final_xyz"] = [float(final_scale.x), float(final_scale.y), float(final_scale.z)]
    target_collection = collection_override or _non_geometry_area_collection(stage_collection, layer_label)
    _move_objects_to_collection([obj], target_collection)
    report_lines.append("NON_GEOMETRY_AREA_PLANE actor=%r layer=%r collection=%r loc=%s rot_deg=%s final_dims=%s stored_y_as_real_z=%s object_scale=%s display=PLANE scale_axes=xzy object_scale_is_nintendo_dims=True" % (
        actor_label, layer_label, target_collection.name,
        tuple(round(float(v), 6) for v in obj.location),
        tuple(round(math.degrees(v), 6) for v in obj.rotation_euler),
        (round(width, 6), round(depth, 6), 0.0),
        round(float(final_scale.z), 6),
        tuple(round(float(v), 6) for v in obj.scale),
    ))
    return obj



def _is_paint_target_area_cube_actor(entry):
    return any("painttargetarea_cube" in token.casefold() for token in _entry_tokens(entry))


def _paint_target_outline_collection(stage_collection, layer_label):
    root = _get_or_create_child_collection(stage_collection, "PaintTargetArea Outlines")
    return _get_or_create_child_collection(root, _safe_collection_name(layer_label, "NoLayer"))


def _create_paint_target_area_top_outline(entry, actor_label, layer_label, area_obj, stage_collection, report_lines):
    """Create temporary visual helpers along the top border of PaintTargetArea_Cube.

    This is a provisional debug visualization, not the final in-game object/effect
    mapping.  The pink nodes and translucent segments sit exactly on the cube's
    top perimeter in the imported object transform.
    """
    if area_obj is None:
        return []
    try:
        sx = abs(float(area_obj.scale.x))
        sy = abs(float(area_obj.scale.y))
        sz = abs(float(area_obj.scale.z))
    except Exception:
        return []
    if sx <= 1e-6 or sy <= 1e-6:
        return []
    local_corners = [
        Vector((-sx * 0.5, -sy * 0.5, sz * 0.5)),
        Vector(( sx * 0.5, -sy * 0.5, sz * 0.5)),
        Vector(( sx * 0.5,  sy * 0.5, sz * 0.5)),
        Vector((-sx * 0.5,  sy * 0.5, sz * 0.5)),
    ]
    try:
        rot = area_obj.rotation_euler.to_matrix().to_4x4()
        base = Matrix.Translation(Vector(area_obj.location)) @ rot
        world_corners = [base @ corner for corner in local_corners]
    except Exception:
        world_corners = [Vector(area_obj.location) + corner for corner in local_corners]
    collection = _paint_target_outline_collection(stage_collection, layer_label)
    mat_node = _rail_make_material("Spl_PaintTargetArea_Node_NeutralTeam", (0.45, 0.45, 0.45), 0.95)
    mat_seg = _rail_make_material("Spl_PaintTargetArea_Segment_NeutralTeam_Translucent", (0.45, 0.45, 0.45), 0.45)
    helpers = []
    root = bpy.data.objects.new(_safe_collection_name("%s.PaintTargetAreaTopOutline" % actor_label), None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.4
    try:
        root.location = sum(world_corners, Vector((0.0, 0.0, 0.0))) / 4.0
    except Exception:
        root.location = Vector(area_obj.location)
    _move_objects_to_collection([root], collection)
    try:
        root["bcett_paint_target_area_top_outline"] = True
        root["bcett_source_area_object"] = area_obj.name
        root["spl_dynamic_team_color"] = True
        root["spl_active_team"] = "neutral"
        root["spl_team_color_kind"] = "painttargetarea"
        root["osi_dynamic_team_color"] = True
    except Exception:
        pass
    helpers.append(root)
    radius = max(0.06, min(sx, sy) * 0.035)
    seg_radius = max(0.025, min(sx, sy) * 0.012)
    for idx, corner in enumerate(world_corners):
        node = _rail_create_sphere("%s.paint_target_top.node%02d" % (actor_label, idx), corner, radius, mat_node, collection)
        if node is not None:
            _parent_keep_world(node, root)
            try:
                node["bcett_paint_target_area_top_node"] = True
                node["spl_dynamic_team_color"] = True
                node["osi_dynamic_team_color"] = True
                node["spl_team_color_kind"] = "painttargetarea"
                node["spl_active_team"] = "neutral"
                node["bcett_paint_target_area_corner_index"] = int(idx)
                node["bcett_source_area_object"] = area_obj.name
            except Exception:
                pass
            helpers.append(node)
    for idx in range(4):
        seg = _rail_create_cylinder_between(
            "%s.paint_target_top.seg%02d_%02d" % (actor_label, idx, (idx + 1) % 4),
            world_corners[idx],
            world_corners[(idx + 1) % 4],
            seg_radius,
            mat_seg,
            collection,
            alpha_note="provisional_paint_target_area_top_border",
        )
        if seg is not None:
            _parent_keep_world(seg, root)
            try:
                seg["bcett_paint_target_area_top_segment"] = True
                seg["spl_dynamic_team_color"] = True
                seg["osi_dynamic_team_color"] = True
                seg["spl_team_color_kind"] = "painttargetarea"
                seg["spl_active_team"] = "neutral"
                seg["bcett_paint_target_area_segment_index"] = int(idx)
                seg["bcett_source_area_object"] = area_obj.name
            except Exception:
                pass
            helpers.append(seg)
    report_lines.append(
        "PAINT_TARGET_AREA_TOP_OUTLINE actor=%r layer=%r helpers=%d corners=%s color=neutral_team translucent_segments=True"
        % (actor_label, layer_label, len(helpers), [tuple(round(float(v), 6) for v in c) for c in world_corners])
    )
    return helpers


def _import_area_visualizer(entry, actor_label, layer_label, stage_collection, report_lines):
    subgroup = _area_visualizer_subgroup(entry)
    collection = _non_geometry_area_collection(stage_collection, layer_label, subgroup)
    if _is_flat_gameplay_region_actor(entry):
        return _import_flat_area_plane(entry, actor_label, layer_label, stage_collection, report_lines, collection)
    group = "Areas"
    empty_type = "CUBE"
    display_size = 1.0
    if any("sphere" in token.casefold() for token in _entry_tokens(entry)):
        empty_type = "SPHERE"
    area_obj = _create_empty_native(entry, actor_label, stage_collection, group, empty_type, display_size, report_lines, "NON_GEOMETRY_AREA", collection_override=collection, convert_scale_axes=True, scale_to_blender_empty_units=True)
    try:
        _mark_editor_helper_non_renderable(area_obj)
    except Exception:
        pass
    if empty_type == "CUBE" and _is_paint_target_area_cube_actor(entry):
        try:
            _create_paint_target_area_top_outline(entry, actor_label, layer_label, area_obj, stage_collection, report_lines)
        except Exception as exc:
            report_lines.append("PAINT_TARGET_AREA_TOP_OUTLINE_FAILED actor=%r layer=%r error=%s" % (actor_label, layer_label, exc))
    return area_obj


def _import_locator_object(entry, actor_label, layer_label, stage_collection, report_lines):
    tokens = [token.casefold() for token in _entry_tokens(entry)]
    if any("cube" in token or "target" in token for token in tokens):
        empty_type = "CUBE"
    elif any("sphere" in token for token in tokens):
        empty_type = "SPHERE"
    else:
        empty_type = "ARROWS"
    return _create_empty_native(entry, actor_label, stage_collection, "Locators", empty_type, 1.0, report_lines, "NON_GEOMETRY_LOCATOR", scale_to_blender_empty_units=True)


def _import_effect_locator_object(entry, actor_label, layer_label, stage_collection, report_lines):
    return _create_empty_native(entry, actor_label, stage_collection, "Effect Locators", "SINGLE_ARROW", 1.5, report_lines, "NON_GEOMETRY_EFFECT_LOCATOR")


def _import_native_non_geometry(entry, actor_label, layer_label, stage_collection, report_lines):
    kind = _native_non_geometry_kind(entry)
    if kind == "LIGHT":
        return [_import_gfx_light(entry, actor_label, layer_label, stage_collection, report_lines)]
    if kind == "SOUND":
        return _import_sound_shape(entry, actor_label, layer_label, stage_collection, report_lines)
    if kind == "CAMERA":
        return [_import_camera_object(entry, actor_label, layer_label, stage_collection, report_lines)]
    if kind == "EFFECT_LOCATOR":
        return [_import_effect_locator_object(entry, actor_label, layer_label, stage_collection, report_lines)]
    if kind == "AREA":
        return [_import_area_visualizer(entry, actor_label, layer_label, stage_collection, report_lines)]
    if kind == "LOCATOR":
        return [_import_locator_object(entry, actor_label, layer_label, stage_collection, report_lines)]
    return []
def _is_non_renderable_actor(entry):
    """Return True for BCETT helper/control actors that normally do not resolve to BFRES models."""
    helper_exact = {
        "changepaintablearea",
        "mpt_playerdead",
        "painttargetarea_cube",
    }
    helper_prefixes = (
        "locator",
        "generallocator",
        "sound",
        "se",
        "snapshot",
    )
    helper_contains = (
        "changepaintablearea",
        "painttargetarea",
        "rejectarea",
        "routearea",
        "routetargetpoint",
        "spawner",
        "fireworkexcludearea",
        "hikikomoriarea",
    )
    for token in _entry_tokens(entry):
        lower = token.casefold()
        if lower in helper_exact:
            return True
        if lower.startswith(helper_prefixes):
            return True
        if any(part in lower for part in helper_contains):
            return True
    return False

def _is_liftrail_inkrail_actor(entry, model_path=None):
    """Return True for LiftRail/InkRail actors reconstructed as rail helpers."""
    tokens = [t.casefold() for t in _entry_tokens(entry)] if isinstance(entry, dict) else []
    if model_path is not None:
        try:
            tokens.append(Path(model_path).stem.casefold())
        except Exception:
            pass
    joined = " ".join(tokens)
    return ("inkrail" in joined) or ("liftrail" in joined)


def _rail_points_from_anchor(anchor):
    if not anchor:
        return None, None
    if anchor.get("kind") == "Rail":
        rail = anchor.get("entry")
    elif anchor.get("kind") == "RailPoint":
        rail = anchor.get("parent")
    else:
        rail = None
    if not isinstance(rail, dict):
        return None, None
    points = rail.get("Points")
    if not isinstance(points, list) or len(points) < 2:
        return rail, None
    usable = [p for p in points if isinstance(p, dict) and p.get("Translate") is not None]
    if len(usable) < 2:
        return rail, None
    return rail, usable


def _rail_for_bancparam_actor(entry, banc_anchor_index, report_lines, actor_label):
    for param_name, value_path, ref_value in _bancparam_reference_candidates(entry):
        anchor = banc_anchor_index.get(ref_value)
        rail, points = _rail_points_from_anchor(anchor)
        if rail is not None and points is not None:
            report_lines.append(
                "INKRAIL_RAIL_RESOLVED actor=%r param=%s ref_path=%s ref=%s anchor=%s:%s points=%d"
                % (actor_label, param_name, value_path, ref_value, anchor.get("kind"), anchor.get("label"), len(points))
            )
            return rail, points
    report_lines.append("INKRAIL_RAIL_UNRESOLVED actor=%r reason=no_BancParam_rail_with_2_points" % actor_label)
    return None, None


def _rail_point_location(point):
    return _vector_from_any(point.get("Translate", {}), 0.0, splatoon_axis_order=True)


def _rail_point_rotation(point, fallback_rotation):
    rotate = point.get("Rotate") or point.get("Rotation")
    if rotate is None:
        return fallback_rotation
    try:
        return _rotation_euler_from_entry({"Gyaml": "__InkRailPoint__", "Rotate": rotate})
    except Exception:
        return fallback_rotation


def _rotation_toward_point(origin, target, fallback_rotation):
    """Aim the nozzle authored local +Z axis toward target, preserving Y as up.

    In-game/direct-inspection testing showed the nozzle mesh points straight up
    when its Blender rotation is zero.  That means the authored forward axis is
    local +Z, not +Y.  Use a track quaternion from +Z to the rail segment
    direction so each nozzle visibly points at its own neighboring node.
    """
    try:
        direction = Vector(target) - Vector(origin)
        if direction.length <= 1e-6:
            return fallback_rotation
        return direction.normalized().to_track_quat("Z", "Y").to_euler()
    except Exception:
        return fallback_rotation


def _inkrail_offset_location(loc):
    """InkRail visual assets sit 0.5 Blender units above Banc rail points.

    This is intentionally InkRail-only; other rails keep their existing point
    transforms.
    """
    try:
        v = Vector(loc)
        v.z += 0.5
        return v
    except Exception:
        try:
            return Vector((float(loc[0]), float(loc[1]), float(loc[2]) + 0.5))
        except Exception:
            return Vector((0.0, 0.0, 0.5))


def _inkrail_part_candidates(kind):
    table = {
        "main": ["Obj_InkRail_VS", "obj_inkrail_vs"],
        "trans": ["Obj_InkRail_VSTrans", "obj_inkrail_vstrans"],
        "base": ["Obj_InkRailBase_VS", "obj_inkrailbase_vs"],
        "nozzle": ["Obj_Nozzle_VS", "obj_nozzle_vs"],
        "small": ["Obj_InkRailSmall_Coop", "obj_inkrailsmall_coop", "Obj_InkRailSmall_VS", "obj_inkrailsmall_vs"],
        "small_nozzle": ["Obj_NozzleSmall_VS", "obj_nozzlesmall_vs"],
    }
    return table.get(kind, [])


# InkRail parts are authored together in Obj_InkRail_VS.bfres.zs.  Importing each
# FMDL as a targeted standalone model can lose whole-BFRES-relative transforms.
# Testing indicates the VS small-center model is authored smaller than the in-game
# rail center, while Obj_InkRailSmall_Coop matches the expected visual size.  Use
# the Coop center variant for reconstructed VS/LiftRail centers, while keeping VS
# nozzle variants for nozzles.
_INKRAIL_SMALL_CENTER_VISUAL_SCALE = 1.0
_INKRAIL_FULL_TEMPLATE_CACHE_PREFIX = "__inkrail_full_bfres_template__"


def _inkrail_parent_keep_world(child, parent):
    try:
        mw = child.matrix_world.copy()
        child.parent = parent
        child.matrix_world = mw
    except Exception:
        try:
            child.parent = parent
            child.matrix_parent_inverse = parent.matrix_world.inverted()
        except Exception:
            pass


def _inkrail_node_root(entry, actor_label, node_index, location, sequence_root, stage_collection, layer_label, report_lines):
    root_name = "%s.node%02d" % (actor_label, int(node_index))
    world_location = Vector(location)
    node_root = bpy.data.objects.new(_safe_collection_name(root_name), None)
    node_root.empty_display_type = "PLAIN_AXES"
    node_root.empty_display_size = 0.35
    node_root.rotation_euler = Euler((0.0, 0.0, 0.0), "XYZ")
    node_root.scale = Vector((1.0, 1.0, 1.0))
    if sequence_root is not None:
        try:
            # The sequence root is intentionally placed at node 0.  Node empties
            # must therefore store local offsets from that root, not another copy
            # of the world-space node position.  Using sequence_root.location is
            # more reliable than sequence_root.matrix_world during construction,
            # because Blender may not have evaluated the newly-linked parent yet.
            root_world = Vector(getattr(sequence_root, "location", (0.0, 0.0, 0.0)))
            node_root.parent = sequence_root
            node_root.matrix_parent_inverse.identity()
            try:
                inv_rot = sequence_root.rotation_euler.to_matrix().inverted()
                node_root.location = inv_rot @ (world_location - root_world)
            except Exception:
                node_root.location = world_location - root_world
        except Exception:
            try:
                node_root.parent = sequence_root
                node_root.matrix_parent_inverse.identity()
                node_root.location = world_location - Vector(getattr(sequence_root, "location", (0.0, 0.0, 0.0)))
            except Exception:
                node_root.location = world_location
    else:
        node_root.location = world_location
    try:
        node_root["bcett_inkrail_node_root"] = True
        node_root["bcett_inkrail_node_index"] = int(node_index)
        node_root["bcett_inkrail_sequence_parent"] = sequence_root.name if sequence_root is not None else ""
        node_root["bcett_inkrail_node_world_location"] = [float(v) for v in world_location]
        node_root["spl_dynamic_team_color"] = True
        node_root["osi_dynamic_team_color"] = True
        node_root["spl_active_team"] = "neutral"
        node_root["spl_team_color_kind"] = "inkrail_node"
    except Exception:
        pass
    _copy_scalar_custom_props(node_root, entry)
    _annotate_object_with_rsdb(node_root, entry)
    _set_bcett_transform_props(node_root, entry, actor_label, world_location, Euler((0.0, 0.0, 0.0), "XYZ"), Vector((1.0, 1.0, 1.0)))
    _move_objects_to_collection([node_root], _non_geometry_rail_layer_collection(stage_collection, layer_label))
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    report_lines.append(
        "INKRAIL_NODE_ROOT actor=%r node=%d loc=%s local_loc=%s parent=%r"
        % (
            actor_label,
            int(node_index),
            tuple(round(float(v), 6) for v in world_location),
            tuple(round(float(v), 6) for v in node_root.location),
            sequence_root.name if sequence_root is not None else None,
        )
    )
    return node_root



def _inkrail_part_name_matches(obj, targets):
    try:
        name = str(getattr(obj, "name", "")).casefold()
    except Exception:
        return False
    if "." in name:
        # Remove Blender numeric suffixes only for exact/end checks; keep the
        # original string too because BFRES imports may prepend a template prefix.
        base_no_suffix = name.rsplit(".", 1)[0]
    else:
        base_no_suffix = name
    for target in targets or []:
        t = str(target).casefold()
        if not t:
            continue
        if name == t or base_no_suffix == t:
            return True
        if name.endswith(t) or base_no_suffix.endswith(t):
            return True
        if t in name:
            return True
    return False


def _inkrail_collect_object_tree(root, all_objects):
    object_set = set(all_objects or [])
    collected = []
    stack = [root]
    seen = set()
    while stack:
        obj = stack.pop()
        if obj in seen:
            continue
        seen.add(obj)
        collected.append(obj)
        try:
            children = [child for child in object_set if child.parent == obj]
        except Exception:
            children = []
        stack.extend(children)
    return collected




def _tag_inkrail_part_descendants(imported_objects, kind, node_index, node_root):
    """Tag every duplicated InkRail part object, not just top roots.

    The BFRES template duplication returns armatures plus mesh children.  Neutral
    visibility/material rules must recognize the child meshes too; otherwise they
    can be alpha-muted by broad material-name heuristics even while their armature
    root remains visible.
    """
    part_kind = str(kind or "")
    team_kind = "inkrail_nozzle" if "nozzle" in part_kind.casefold() else "inkrail"
    seq_name = ""
    node_name = ""
    try:
        node_name = node_root.name if node_root is not None else ""
        seq_name = node_root.parent.name if node_root is not None and node_root.parent is not None else ""
    except Exception:
        pass
    for obj in list(imported_objects or []):
        if obj is None:
            continue
        try:
            obj["bcett_inkrail_part"] = part_kind
            obj["bcett_inkrail_node_index"] = int(node_index)
            obj["bcett_inkrail_reconstructed"] = True
            obj["bcett_rail_kind"] = "inkrail"
            obj["bcett_inkrail_sequence_parent"] = seq_name
            obj["bcett_inkrail_node_parent"] = node_name
            obj["spl_dynamic_team_color"] = True
            obj["osi_dynamic_team_color"] = True
            obj["spl_active_team"] = "neutral"
            obj["spl_team_color_kind"] = team_kind
            if part_kind == "small":
                obj["bcett_inkrail_small_center_visual_scale"] = float(_INKRAIL_SMALL_CENTER_VISUAL_SCALE)
            obj.hide_viewport = False
            obj.hide_render = False
        except Exception:
            pass
def _inkrail_full_template_key(model_path, prefs):
    return (
        _INKRAIL_FULL_TEMPLATE_CACHE_PREFIX,
        str(Path(model_path).resolve()),
        _effective_bfres_texture_mode(prefs),
        _effective_import_materials(),
        bool(getattr(prefs, "component_selector", True)),
        bool(getattr(prefs, "custom_normals", True)),
        int(getattr(prefs, "lod_index", 0)),
        bool(getattr(prefs, "import_anims", False)),
    )


def _inkrail_full_template_objects(model_path, prefs, import_cache, report_lines):
    key = _inkrail_full_template_key(model_path, prefs)
    cached = import_cache.get(key)
    if cached and cached.get("objects"):
        return list(cached.get("objects", [])), list(cached.get("messages", [])), True

    imported, messages = _import_bfres(
        str(model_path),
        prefs,
        name_prefix="InkRailFullTemplate_",
        target_model_names=None,
        texture_diag_owner="INKRAIL_TEMPLATE",
    )
    for obj in imported or []:
        _osi_sanitize_mesh_material_slots(obj, report_lines, context="INKRAIL_TEMPLATE_IMPORT")
        try:
            obj.hide_viewport = True
            obj.hide_render = True
            obj["bcett_inkrail_full_template"] = True
        except Exception:
            pass
    root_summary = _object_transform_summary(imported) if _is_full_report() else "suppressed"
    import_cache[key] = {"objects": list(imported or []), "messages": list(messages or []), "root_summary": root_summary}
    if _is_full_report():
        report_lines.append(
            "INKRAIL_FULL_TEMPLATE_STORE path=%s objects=%d roots=[%s]"
            % (Path(model_path).name, len(imported or []), root_summary)
        )
    return list(imported or []), list(messages or []), False


def _duplicate_inkrail_part_from_full_template(model_path, prefs, import_cache, report_lines, kind):
    targets = _inkrail_part_candidates(kind)
    template_objects, messages, cache_hit = _inkrail_full_template_objects(model_path, prefs, import_cache, report_lines)
    roots = _top_level_import_roots(template_objects)
    matching_roots = [root for root in roots if _inkrail_part_name_matches(root, targets)]
    if not matching_roots:
        # Some importer versions place the FMDL name on a mesh child rather than
        # the top armature.  If so, climb to the containing template root and
        # duplicate that whole hierarchy.
        root_by_child = {}
        for root in roots:
            for obj in _inkrail_collect_object_tree(root, template_objects):
                root_by_child[obj] = root
        seen = set()
        for obj in template_objects:
            if _inkrail_part_name_matches(obj, targets):
                root = root_by_child.get(obj, obj)
                if root not in seen:
                    matching_roots.append(root)
                    seen.add(root)
    if not matching_roots:
        report_lines.append(
            "INKRAIL_FULL_TEMPLATE_PART_MISSING part=%s targets=%s template_roots=%s"
            % (kind, targets, [getattr(root, "name", "<unnamed>") for root in roots])
        )
        return [], messages, cache_hit

    selected = []
    seen = set()
    for root in matching_roots:
        for obj in _inkrail_collect_object_tree(root, template_objects):
            if obj not in seen:
                selected.append(obj)
                seen.add(obj)
    duplicated = _duplicate_linked_objects(selected, report_lines=report_lines)
    for obj in duplicated:
        try:
            obj.hide_viewport = False
            obj.hide_render = False
            obj["bcett_inkrail_from_full_bfres_template"] = True
        except Exception:
            pass
    if _is_full_report():
        report_lines.append(
            "INKRAIL_FULL_TEMPLATE_PART_DUP part=%s targets=%s template_cache_hit=%s selected=%d duplicated=%d roots=[%s]"
            % (kind, targets, cache_hit, len(selected), len(duplicated), _object_transform_summary(duplicated))
        )
    return duplicated, messages, cache_hit



def _cleanup_inkrail_full_templates(import_cache, report_lines):
    """Remove temporary full-BFRES InkRail template objects after reconstruction.

    Reconstructed rail parts are duplicated from the template and tagged with
    bcett_inkrail_from_full_bfres_template.  The original hidden template roots
    are only needed during import; leaving them in the scene clutters the
    Outliner and can look like stray reference Obj_InkRail objects.
    """
    removed_objects = 0
    removed_collections = 0
    cache_keys = []
    try:
        cache_items = list((import_cache or {}).items())
    except Exception:
        cache_items = []
    for key, value in cache_items:
        try:
            if isinstance(key, tuple) and key and key[0] == _INKRAIL_FULL_TEMPLATE_CACHE_PREFIX:
                cache_keys.append(key)
                for obj in list((value or {}).get("objects", []) or []):
                    try:
                        if obj is not None and obj.name in bpy.data.objects and bool(obj.get("bcett_inkrail_full_template", False)):
                            bpy.data.objects.remove(obj, do_unlink=True)
                            removed_objects += 1
                    except Exception:
                        pass
        except Exception:
            pass
    for key in cache_keys:
        try:
            import_cache.pop(key, None)
        except Exception:
            pass

    # Remove any leftover hidden template objects that may not be reachable from
    # the cache, but avoid touching duplicated rail parts.
    for obj in list(bpy.data.objects):
        try:
            if bool(obj.get("bcett_inkrail_full_template", False)) and not bool(obj.get("bcett_inkrail_from_full_bfres_template", False)):
                bpy.data.objects.remove(obj, do_unlink=True)
                removed_objects += 1
        except Exception:
            pass

    # Clean up temporary template/reference collections if any were created by
    # the BFRES importer while linking Obj_InkRail_VS.  Some Blender sessions
    # leave a base-level Obj_InkRail_VS collection even after the tagged hidden
    # template roots are removed.  Reconstructed rail parts are moved into the
    # stage Rail collection, so these base-level template collections can be
    # removed once any remaining hidden/template-only objects are unlinked.
    inkrail_collection_bases = {
        "obj_inkrail_vs",
        "obj_inkrail_vstrans",
        "obj_inkrailbase_vs",
        "obj_inkrailsmall_vs",
        "obj_inkrailsmall_coop",
        "obj_nozzle_vs",
        "obj_nozzlesmall_vs",
    }
    for col in list(bpy.data.collections):
        try:
            name = str(col.name)
            base_name = name.split(".", 1)[0].casefold()
            is_template_collection = ("InkRailFullTemplate" in name or _INKRAIL_FULL_TEMPLATE_CACHE_PREFIX in name)
            is_leftover_base_obj_inkrail = base_name in inkrail_collection_bases
            if not (is_template_collection or is_leftover_base_obj_inkrail):
                continue
            for obj in list(col.objects):
                try:
                    is_template_obj = bool(obj.get("bcett_inkrail_full_template", False)) and not bool(obj.get("bcett_inkrail_from_full_bfres_template", False))
                    is_hidden_template_obj = bool(getattr(obj, "hide_viewport", False)) and ("InkRailFullTemplate" in str(obj.name) or base_name in inkrail_collection_bases)
                    if is_template_obj or is_hidden_template_obj:
                        bpy.data.objects.remove(obj, do_unlink=True)
                        removed_objects += 1
                    else:
                        try:
                            col.objects.unlink(obj)
                        except Exception:
                            pass
                except Exception:
                    pass
            if len(col.objects) == 0 and len(col.children) == 0:
                bpy.data.collections.remove(col)
                removed_collections += 1
        except Exception:
            pass
    if removed_objects or removed_collections or cache_keys:
        try:
            report_lines.append("INKRAIL_FULL_TEMPLATE_CLEANUP removed_objects=%d removed_collections=%d cleared_cache_entries=%d" % (removed_objects, removed_collections, len(cache_keys)))
        except Exception:
            pass

def _import_inkrail_part(entry, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, kind, node_index, location, rotation_euler, node_root=None):
    targets = _inkrail_part_candidates(kind)
    if not targets:
        return 0
    part_label = "%s.%s.node%d" % (actor_label, kind, int(node_index))
    try:
        imported_objects, bfres_messages, cache_hit = _duplicate_inkrail_part_from_full_template(
            model_path,
            prefs,
            import_cache,
            report_lines,
            kind,
        )
    except Exception as exc:
        report_lines.append("INKRAIL_PART_FAILED actor=%r part=%s node=%d targets=%s error=%s" % (actor_label, kind, int(node_index), targets, exc))
        return 0
    for msg in bfres_messages:
        report_lines.append("BFRES actor=%r path=%s %s" % (part_label, Path(model_path).name, msg))
    if not imported_objects:
        report_lines.append("INKRAIL_PART_MISSING actor=%r part=%s node=%d targets=%s" % (actor_label, kind, int(node_index), targets))
        return 0

    # v0.7.85: no per-part wrapper empties.  Every rail has one sequence empty,
    # each rail point has one node empty, and all BFRES part roots at that point
    # are parented directly under that node.  The final part rotation is applied
    # to the visible imported BFRES root objects, so Blender's Object Rotation UI
    # shows the same per-part/nozzle aim rotation that the reconstruction uses.
    top_roots = list(_top_level_import_roots(imported_objects))
    if node_root is None:
        node_root = _inkrail_node_root(entry, actor_label, node_index, location, None, stage_collection, layer_label, report_lines)

    rot_deg = tuple(round(math.degrees(float(v)), 6) for v in rotation_euler)
    top_summaries = []
    for obj in top_roots:
        # Parent each visible BFRES root under the shared node empty, but then
        # explicitly compose its world matrix from the node transform plus the
        # part/nozzle aim rotation.  v0.7.85 set local zero/rotation before
        # parenting; depending on Blender parent-inverse state that could leave
        # the visible roots at the scene origin.
        try:
            # Parent under the shared node empty and let Blender inherit the
            # node transform normally.  Do not assign matrix_world here: during
            # import Blender may not have evaluated the newly-created node
            # parent yet, so composing from node_root.matrix_world can collapse
            # the part back to the scene origin.  Keeping local location zero
            # with an identity parent inverse makes the part follow the node
            # location after dependency-graph evaluation.
            obj.parent = node_root
            obj.matrix_parent_inverse.identity()
            obj.location = (0.0, 0.0, 0.0)
            obj.rotation_euler = rotation_euler
            if kind == "small":
                obj.scale = Vector(obj.scale) * _INKRAIL_SMALL_CENTER_VISUAL_SCALE
            # Keep every visible InkRail part root exactly on its node empty.
            # This is intentionally a corrective pass after parenting: depending
            # on how Blender preserved a duplicated template root's parent inverse,
            # node 0 could otherwise inherit the sequence-root translation and
            # also keep a stale local node offset, making it appear translated
            # twice from the origin.  Later nodes already used the desired
            # effective transform, but this keeps all nodes on the same rule.
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            try:
                desired_world = node_root.matrix_world.translation.copy()
                current_world = obj.matrix_world.translation.copy()
                if (current_world - desired_world).length > 1.0e-5:
                    obj.location = node_root.matrix_world.inverted() @ desired_world
                    try:
                        bpy.context.view_layer.update()
                    except Exception:
                        pass
                    obj["bcett_inkrail_node_position_corrected"] = True
            except Exception:
                pass
        except Exception:
            try:
                obj.parent = None
                obj.location = Vector(location)
                obj.rotation_euler = rotation_euler
            except Exception:
                pass
        try:
            obj["bcett_inkrail_part"] = kind
            obj["bcett_inkrail_node_index"] = int(node_index)
            obj["bcett_inkrail_reconstructed"] = True
            obj["bcett_inkrail_visible_rotation_deg"] = [float(v) for v in rot_deg]
            obj["bcett_inkrail_transform_applied_to"] = "visible_bfres_root_object_local_transform"
            obj["spl_dynamic_team_color"] = True
            obj["osi_dynamic_team_color"] = True
            obj["spl_active_team"] = "neutral"
            obj["spl_team_color_kind"] = "inkrail_nozzle" if "nozzle" in str(kind).casefold() else "inkrail"
            if kind == "small":
                obj["bcett_inkrail_small_center_visual_scale"] = float(_INKRAIL_SMALL_CENTER_VISUAL_SCALE)
        except Exception:
            pass
        try:
            # Ensure diagnostics see the inherited node transform immediately.
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            top_summaries.append("%s:loc=%s world_loc=%s rot_deg=%s world_rot_deg=%s scale=%s type=%s" % (
                obj.name,
                tuple(round(float(v), 6) for v in obj.location),
                tuple(round(float(v), 6) for v in obj.matrix_world.translation),
                tuple(round(math.degrees(float(v)), 6) for v in obj.rotation_euler),
                tuple(round(math.degrees(float(v)), 6) for v in obj.matrix_world.to_euler()),
                tuple(round(float(v), 6) for v in obj.scale),
                getattr(obj, 'type', '?'),
            ))
        except Exception:
            pass

    _tag_inkrail_part_descendants(imported_objects, kind, node_index, node_root)
    _set_bcett_transform_props_many(imported_objects, entry, actor_label, location, rotation_euler, Vector((1.0, 1.0, 1.0)))
    for obj in imported_objects:
        try:
            obj["bcett_inkrail_sequence_parent"] = node_root.parent.name if node_root.parent is not None else ""
            obj["bcett_inkrail_node_parent"] = node_root.name
        except Exception:
            pass
    _move_objects_to_collection(imported_objects, _non_geometry_rail_layer_collection(stage_collection, layer_label))
    report_lines.append(
        "INKRAIL_PART_IMPORTED actor=%r part=%s node=%d targets=%s node_root=%r loc=%s visible_rot_deg=%s objects=%d cache_hit=%s top_roots=[%s]"
        % (
            actor_label,
            kind,
            int(node_index),
            targets,
            node_root.name if node_root is not None else None,
            tuple(round(float(v), 6) for v in Vector(location)),
            rot_deg,
            len(imported_objects),
            cache_hit,
            "; ".join(top_summaries[:4]),
        )
    )
    if kind == "small":
        try:
            root_scales = [tuple(round(float(v), 6) for v in obj.scale) for obj in top_roots]
            report_lines.append("INKRAIL_SMALL_SCALE_DIAG actor=%r node=%d top_root_scales=%s visual_scale_correction=%s note=small_center_only_nozzle_scale_unchanged" % (actor_label, int(node_index), root_scales, _INKRAIL_SMALL_CENTER_VISUAL_SCALE))
        except Exception:
            pass
    return len(imported_objects)

def _try_import_liftrail_inkrail_geometry(entry, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, banc_anchor_index):
    if not _is_liftrail_inkrail_actor(entry, model_path):
        return None
    rail, points = _rail_for_bancparam_actor(entry, banc_anchor_index, report_lines, actor_label)
    if not points:
        return None
    actor_rotation = _rotation_euler_from_entry(entry)
    node_locations = [_inkrail_offset_location(_rail_point_location(point)) for point in points]
    imported_count = 0
    rail_collection = _non_geometry_rail_layer_collection(stage_collection, layer_label)
    sequence_location = Vector(node_locations[0]) if node_locations else Vector((0.0, 0.0, 0.0))
    sequence_root = _create_actor_root(entry, actor_label, sequence_location, Euler((0.0, 0.0, 0.0), "XYZ"), Vector((1.0, 1.0, 1.0)))
    try:
        sequence_root["bcett_inkrail_sequence_root"] = True
        sequence_root["bcett_inkrail_node_count"] = int(len(node_locations))
        sequence_root["bcett_inkrail_model"] = Path(model_path).name
        sequence_root["spl_dynamic_team_color"] = True
        sequence_root["osi_dynamic_team_color"] = True
        sequence_root["spl_active_team"] = "neutral"
        sequence_root["spl_team_color_kind"] = "inkrail"
    except Exception:
        pass
    _set_bcett_transform_props(sequence_root, entry, actor_label, sequence_location, Euler((0.0, 0.0, 0.0), "XYZ"), Vector((1.0, 1.0, 1.0)))
    _move_objects_to_collection([sequence_root], rail_collection)
    imported_count = 1
    report_lines.append(
        "INKRAIL_RECONSTRUCT actor=%r layer=%r model=%s nodes=%d rule=base_intermediate_end collection=Non-Geometry/Rail toggle=non_geometry sequence_root=%r root_loc=%s"
        % (actor_label, layer_label, Path(model_path).name, len(node_locations), sequence_root.name, tuple(round(float(v), 6) for v in sequence_root.location))
    )
    node_roots = {idx: _inkrail_node_root(entry, actor_label, idx, loc, sequence_root, stage_collection, layer_label, report_lines) for idx, loc in enumerate(node_locations)}

    # Node 0: main/trans/base are static at the base node.  The large nozzle aims
    # toward node 1.
    base_rotation = _rail_point_rotation(points[0], actor_rotation)
    for kind in ("main", "trans", "base"):
        imported_count += _import_inkrail_part(entry, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, kind, 0, node_locations[0], base_rotation, node_root=node_roots[0])
    imported_count += _import_inkrail_part(
        entry,
        actor_label,
        layer_label,
        model_path,
        prefs,
        import_cache,
        report_lines,
        stage_collection,
        "nozzle",
        0,
        node_locations[0],
        _rotation_toward_point(node_locations[0], node_locations[1], base_rotation),
        node_root=node_roots[0],
    )

    # Nodes 1..N-1: small body at every following node.  Intermediate nodes get
    # one small nozzle toward each neighbor; the last node gets only the backward
    # nozzle.
    for node_index in range(1, len(node_locations)):
        point_rotation = _rail_point_rotation(points[node_index], actor_rotation)
        loc = node_locations[node_index]
        imported_count += _import_inkrail_part(entry, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, "small", node_index, loc, point_rotation, node_root=node_roots[node_index])
        imported_count += _import_inkrail_part(
            entry,
            actor_label,
            layer_label,
            model_path,
            prefs,
            import_cache,
            report_lines,
            stage_collection,
            "small_nozzle",
            node_index,
            loc,
            _rotation_toward_point(loc, node_locations[node_index - 1], point_rotation),
            node_root=node_roots[node_index],
        )
        if node_index + 1 < len(node_locations):
            imported_count += _import_inkrail_part(
                entry,
                actor_label,
                layer_label,
                model_path,
                prefs,
                import_cache,
                report_lines,
                stage_collection,
                "small_nozzle",
                node_index,
                loc,
                _rotation_toward_point(loc, node_locations[node_index + 1], point_rotation),
                node_root=node_roots[node_index],
            )

    # Lightweight translucent rail/beam helpers between consecutive InkRail nodes.
    # These represent the in-game line effect until the exact effect asset is mapped.
    try:
        mat_line = _rail_make_material("Spl_Rail_InkRail_Line_Translucent", (0.35, 0.85, 1.0), 0.45)
        for seg_index in range(len(node_locations) - 1):
            seg = _rail_create_cylinder_between(
                "%s.inkrail.seg%02d_%02d" % (actor_label, seg_index, seg_index + 1),
                node_locations[seg_index],
                node_locations[seg_index + 1],
                0.08,
                mat_line,
                rail_collection,
                alpha_note="team_color_candidate_inkrail_effect_line",
            )
            if seg is not None:
                _parent_keep_world(seg, sequence_root)
                try:
                    seg["bcett_rail_segment_index"] = int(seg_index)
                    seg["bcett_rail_kind"] = "inkrail"
                    seg["bcett_inkrail_segment_helper"] = True
                    seg["spl_dynamic_team_color"] = True
                    seg["osi_dynamic_team_color"] = True
                    seg["spl_active_team"] = "neutral"
                    seg["spl_team_color_kind"] = "inkrail_segment"
                    seg.hide_viewport = True
                    seg.hide_render = True
                except Exception:
                    pass
                imported_count += 1
        report_lines.append("INKRAIL_SEGMENTS_IMPORTED actor=%r layer=%r segments=%d" % (actor_label, layer_label, max(0, len(node_locations) - 1)))
    except Exception as exc:
        report_lines.append("INKRAIL_SEGMENTS_FAILED actor=%r layer=%r error=%s" % (actor_label, layer_label, exc))
    return imported_count


def _rail_layer_from_entry(rail, fallback="NoLayer"):
    if isinstance(rail, dict):
        layer = rail.get("Layer") or rail.get("LayerName")
        if layer:
            return str(layer)
        pts = rail.get("Points")
        if isinstance(pts, list):
            for pt in pts:
                if isinstance(pt, dict):
                    layer = pt.get("Layer") or pt.get("LayerName")
                    if layer:
                        return str(layer)
    return str(fallback or "NoLayer")


def _rail_visual_tokens(rail):
    tokens = []
    def add(v):
        if v is None:
            return
        try:
            text = str(v)
        except Exception:
            return
        if text:
            tokens.append(text.casefold())
    if isinstance(rail, dict):
        for key in ("Name", "Gyaml", "RailType", "Type", "__RowId", "UnitConfigName"):
            add(rail.get(key))
        for value in rail.values():
            if isinstance(value, str):
                add(value)
    return " ".join(tokens)


def _rail_visual_kind(rail):
    text = _rail_visual_tokens(rail)
    if "gachiyagura" in text or "yagura" in text or "tower" in text:
        return "gachiyagura"
    if "liftrail" in text or "inkrail" in text or "vliftrail" in text or "vlift" in text:
        return "liftrail"
    return "generic"


def _rail_make_material(name, color, alpha=1.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    rgba = (float(color[0]), float(color[1]), float(color[2]), float(alpha))
    try:
        # Set both viewport diffuse color and shader-node color.  The helper
        # meshes are generated procedurally and have no texture; if only alpha
        # is set on the Principled node, Blender can display them as default
        # white in material preview/render.
        mat.diffuse_color = rgba
        mat.use_nodes = True
        if alpha < 1.0:
            mat.blend_method = 'BLEND'
            mat.show_transparent_back = True
            try:
                mat.use_screen_refraction = True
            except Exception:
                pass
        else:
            try:
                mat.blend_method = 'OPAQUE'
            except Exception:
                pass
        bsdf = mat.node_tree.nodes.get('Principled BSDF') if mat.node_tree else None
        if bsdf is not None:
            for input_name in ('Base Color', 'Alpha'):
                try:
                    if input_name == 'Base Color':
                        bsdf.inputs[input_name].default_value = rgba
                    else:
                        bsdf.inputs[input_name].default_value = float(alpha)
                except Exception:
                    pass
            # Generated InkRail segment helpers are recolored per rail through
            # Object.color.  Build the Object Info -> Principled Base Color link
            # at material creation time so the first team change works even if a
            # later team-color repair/update pass has not run yet.
            if 'inkrail_line' in str(name).casefold():
                try:
                    tree = mat.node_tree
                    obj_node = tree.nodes.get('Spl InkRail Object Color') or tree.nodes.new(type='ShaderNodeObjectInfo')
                    obj_node.name = 'Spl InkRail Object Color'
                    obj_node.label = 'Spl InkRail Object Color'
                    color_out = obj_node.outputs.get('Color') if hasattr(obj_node.outputs, 'get') else None
                    base_in = bsdf.inputs.get('Base Color') if hasattr(bsdf.inputs, 'get') else None
                    if color_out is not None and base_in is not None:
                        for link in list(base_in.links):
                            tree.links.remove(link)
                        tree.links.new(color_out, base_in)
                    mat['bcett_inkrail_uses_object_color'] = True
                    mat['osi_team_color_graph_locked'] = True
                    mat['spl_team_color_graph_locked'] = True
                except Exception:
                    pass
    except Exception:
        pass
    try:
        folded = str(name).casefold()
        if any(tok in folded for tok in ("gachiyagura", "painttargetarea", "inkrail_line")):
            mat["spl_dynamic_team_color"] = True
            mat["osi_dynamic_team_color"] = True
            mat["spl_team_color_managed"] = True
            mat["osi_team_color_managed"] = True
            mat["spl_team_color_role"] = "neutral"
            mat["osi_team_color_role"] = "neutral"
            mat["spl_team_color_kind"] = name
            mat["osi_team_color_kind"] = name
    except Exception:
        pass
    return mat


def _rail_create_sphere(name, location, radius, material, collection):
    try:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=float(radius), location=Vector(location))
        obj = bpy.context.object
        obj.name = name
        obj.data.name = name + "Mesh"
        if material is not None:
            obj.data.materials.append(material)
            try:
                obj.color = material.diffuse_color
            except Exception:
                pass
        _move_objects_to_collection([obj], collection)
        return obj
    except Exception:
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "SPHERE"
        obj.empty_display_size = float(radius) * 2.0
        obj.location = Vector(location)
        collection.objects.link(obj)
        return obj


def _rail_create_cylinder_between(name, a, b, radius, material, collection, alpha_note=None):
    a = Vector(a); b = Vector(b)
    mid = (a + b) * 0.5
    direction = b - a
    length = direction.length
    if length <= 1e-6:
        return None
    try:
        rot = direction.normalized().to_track_quat('Z', 'Y').to_euler()
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=float(radius), depth=float(length), location=mid, rotation=rot)
        obj = bpy.context.object
        obj.name = name
        obj.data.name = name + "Mesh"
        obj.rotation_euler = rot
        try:
            obj.matrix_world = Matrix.Translation(mid) @ rot.to_matrix().to_4x4()
            obj.dimensions.z = float(length)
            bpy.context.view_layer.update()
        except Exception:
            pass
        if material is not None:
            obj.data.materials.append(material)
        if alpha_note:
            obj["bcett_rail_alpha_note"] = alpha_note
        _move_objects_to_collection([obj], collection)
        return obj
    except Exception:
        return None


def _rail_hash_values(rail):
    values = []
    if isinstance(rail, dict):
        for key in ("Hash", "SRTHash"):
            value = rail.get(key)
            if isinstance(value, bool):
                continue
            try:
                values.append(int(value))
            except Exception:
                pass
    return values


def _rail_make_root(rail, label, layer_label, kind, collection, location=None):
    root = bpy.data.objects.new(_safe_collection_name(label), None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.6
    if location is not None:
        try:
            root.location = Vector(location)
        except Exception:
            pass
    collection.objects.link(root)
    try:
        root["bcett_rail_sequence_root"] = True
        root["bcett_rail_kind"] = kind
        root["bcett_layer"] = layer_label
        root["bcett_rail_hashes"] = ",".join(str(v) for v in _rail_hash_values(rail))
        _set_props_from_mapping(root, "rail", rail)
    except Exception:
        pass
    return root


def _parent_keep_world(child, parent):
    try:
        mw = child.matrix_world.copy()
        child.parent = parent
        child.matrix_world = mw
    except Exception:
        try:
            child.parent = parent
        except Exception:
            pass



def _import_liftrail_inkrail_geometry_for_rail(rail, rail_index, label, layer_label, prefs, import_cache, report_lines, stage_collection):
    """Build InkRail/LiftRail visuals directly from a BCETT Rails[] entry.

    Older reconstruction only ran through actor BancParam references, which meant
    Cmn actor-referenced LiftRails received InkRail meshes while layer-specific
    Rails-array entries fell back to generic helpers.  This path uses the Rails[]
    point data directly so Pnt/Var/Vlf/etc. get the same node-based visuals.
    """
    if not isinstance(rail, dict):
        return 0
    points = rail.get("Points")
    usable = [p for p in points or [] if isinstance(p, dict) and p.get("Translate") is not None]
    if len(usable) < 2:
        return 0
    try:
        models_dir = _effective_models_dir(prefs)
        model_path = _find_bfres_model(models_dir, ["Obj_InkRail_VS", "InkRailOnline", "Obj_InkRail_VSTrans"], [])
    except Exception:
        model_path = None
    if model_path is None:
        report_lines.append("INKRAIL_RAIL_ARRAY_FALLBACK index=%d label=%r layer=%r reason=Obj_InkRail_VS_model_not_found" % (int(rail_index), label, layer_label))
        return 0

    actor_label = "%s.Rails[%d]" % (label, int(rail_index))
    actor_rotation = _rotation_euler_from_entry(rail)
    node_locations = [_inkrail_offset_location(_rail_point_location(point)) for point in usable]
    rail_collection = _non_geometry_rail_layer_collection(stage_collection, layer_label)
    sequence_location = Vector(node_locations[0]) if node_locations else Vector((0.0, 0.0, 0.0))
    sequence_root = _create_actor_root(rail, actor_label, sequence_location, Euler((0.0, 0.0, 0.0), "XYZ"), Vector((1.0, 1.0, 1.0)))
    try:
        sequence_root.name = _safe_collection_name("%s.InkRailOnline" % label)
        sequence_root["bcett_inkrail_sequence_root"] = True
        sequence_root["bcett_inkrail_from_rails_array"] = True
        sequence_root["bcett_inkrail_node_count"] = int(len(node_locations))
        sequence_root["bcett_inkrail_model"] = Path(model_path).name
        sequence_root["bcett_rail_array_index"] = int(rail_index)
        sequence_root["bcett_layer"] = layer_label
        sequence_root["spl_dynamic_team_color"] = True
        sequence_root["osi_dynamic_team_color"] = True
        sequence_root["spl_active_team"] = "neutral"
        sequence_root["spl_team_color_kind"] = "inkrail"
        _set_props_from_mapping(sequence_root, "rail", rail)
    except Exception:
        pass
    _set_bcett_transform_props(sequence_root, rail, actor_label, sequence_location, Euler((0.0, 0.0, 0.0), "XYZ"), Vector((1.0, 1.0, 1.0)))
    _move_objects_to_collection([sequence_root], rail_collection)
    imported_count = 1
    report_lines.append(
        "INKRAIL_RAIL_ARRAY_RECONSTRUCT index=%d label=%r layer=%r model=%s nodes=%d collection='Non-Geometry Objects/Rail/%s' rule=rails_array_direct"
        % (int(rail_index), label, layer_label, Path(model_path).name, len(node_locations), layer_label)
    )

    node_roots = {idx: _inkrail_node_root(rail, actor_label, idx, loc, sequence_root, stage_collection, layer_label, report_lines) for idx, loc in enumerate(node_locations)}
    base_rotation = _rail_point_rotation(usable[0], actor_rotation)
    for part_kind in ("main", "trans", "base"):
        imported_count += _import_inkrail_part(rail, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, part_kind, 0, node_locations[0], base_rotation, node_root=node_roots[0])
    imported_count += _import_inkrail_part(rail, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, "nozzle", 0, node_locations[0], _rotation_toward_point(node_locations[0], node_locations[1], base_rotation), node_root=node_roots[0])

    for node_index in range(1, len(node_locations)):
        point_rotation = _rail_point_rotation(usable[node_index], actor_rotation)
        loc = node_locations[node_index]
        imported_count += _import_inkrail_part(rail, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, "small", node_index, loc, point_rotation, node_root=node_roots[node_index])
        imported_count += _import_inkrail_part(rail, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, "small_nozzle", node_index, loc, _rotation_toward_point(loc, node_locations[node_index - 1], point_rotation), node_root=node_roots[node_index])
        if node_index + 1 < len(node_locations):
            imported_count += _import_inkrail_part(rail, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, "small_nozzle", node_index, loc, _rotation_toward_point(loc, node_locations[node_index + 1], point_rotation), node_root=node_roots[node_index])

    mat_line = _rail_make_material("Spl_Rail_LiftRail_Line_Translucent", (0.35, 0.85, 1.0), 0.45)
    for i in range(len(node_locations) - 1):
        obj = _rail_create_cylinder_between("%s.seg%02d_%02d" % (label, i, i + 1), node_locations[i], node_locations[i + 1], 0.08, mat_line, rail_collection, alpha_note="team_color_candidate_liftrail_effect_line")
        if obj is not None:
            _parent_keep_world(obj, sequence_root)
            try:
                obj["bcett_rail_segment_index"] = int(i)
                obj["bcett_rail_kind"] = "liftrail"
                obj["bcett_rail_array_index"] = int(rail_index)
            except Exception:
                pass
            imported_count += 1
    return imported_count

def _import_generic_bcett_rail(rail, rail_index, stage_collection, prefs, import_cache=None, report_lines=None):
    # Defaults keep the Rails[] importer tolerant of stale Blender reloads or older call sites
    # that existed during the module split.  The normal path passes both explicitly.
    if import_cache is None:
        import_cache = {}
    if report_lines is None:
        report_lines = []
    if not isinstance(rail, dict):
        return 0
    points = rail.get("Points")
    usable = [p for p in points or [] if isinstance(p, dict) and p.get("Translate") is not None]
    if len(usable) < 2:
        report_lines.append("RAIL_ARRAY_SKIPPED index=%d reason=less_than_2_points" % int(rail_index))
        return 0
    layer_label = _rail_layer_from_entry(rail)
    kind = _rail_visual_kind(rail)
    label = _anchor_label(rail, "Rail_%04d" % int(rail_index))
    collection = _non_geometry_rail_layer_collection(stage_collection, layer_label)
    locs = [_rail_point_location(p) for p in usable]
    if kind == "liftrail":
        made = _import_liftrail_inkrail_geometry_for_rail(rail, rail_index, label, layer_label, prefs, import_cache, report_lines, stage_collection)
        if made:
            report_lines.append("RAIL_ARRAY_IMPORTED index=%d label=%r layer=%r kind=liftrail nodes=%d objects=%d collection='Non-Geometry Objects/Rail/%s' note=inkrail_meshes_from_rails_array_no_node_spheres" % (int(rail_index), label, layer_label, len(locs), int(made), layer_label))
            return int(made)
    root = _rail_make_root(rail, "%s.%s" % (label, kind), layer_label, kind, collection, location=locs[0] if locs else None)
    if kind == "gachiyagura":
        try:
            root["spl_dynamic_team_color"] = True
            root["spl_active_team"] = "neutral"
            root["spl_team_color_kind"] = "gachiyagurarail"
            root["osi_dynamic_team_color"] = True
        except Exception:
            pass
        node_radius = 0.22
        line_radius = 0.055
        line_alpha = 0.38
        mat_node = _rail_make_material("Spl_Rail_Gachiyagura_Node", (0.45, 0.45, 0.45), 1.0)
        mat_line = _rail_make_material("Spl_Rail_Gachiyagura_Line_Translucent", (0.45, 0.45, 0.45), line_alpha)
    elif kind == "liftrail":
        # LiftRail already has type-specific InkRail visual reconstruction; generic
        # node spheres are misleading/noisy for this rail type.  Keep only the
        # lightweight segment hint until the actual in-game effect line asset is
        # identified.
        node_radius = 0.0
        line_radius = 0.04
        line_alpha = 0.45
        mat_node = None
        mat_line = _rail_make_material("Spl_Rail_LiftRail_Line_Translucent", (0.35, 0.85, 1.0), line_alpha)
    else:
        node_radius = 0.14
        line_radius = 0.025
        line_alpha = 0.35
        mat_node = _rail_make_material("Spl_Rail_Generic_Node", (0.8, 0.8, 0.8), 1.0)
        mat_line = _rail_make_material("Spl_Rail_Generic_Line_Translucent", (0.8, 0.8, 0.8), line_alpha)
    count = 1
    if kind != "liftrail":
        for i, loc in enumerate(locs):
            obj = _rail_create_sphere("%s.node%02d" % (label, i), loc, node_radius, mat_node, collection)
            if obj is not None:
                _parent_keep_world(obj, root)
                try:
                    obj["bcett_rail_node_index"] = int(i)
                    obj["bcett_rail_kind"] = kind
                    _set_props_from_mapping(obj, "rail_point", usable[i])
                except Exception:
                    pass
                count += 1
    for i in range(len(locs)-1):
        obj = _rail_create_cylinder_between("%s.seg%02d_%02d" % (label, i, i+1), locs[i], locs[i+1], line_radius, mat_line, collection, alpha_note="team_color_candidate_effect_line")
        if obj is not None:
            _parent_keep_world(obj, root)
            try:
                obj["bcett_rail_segment_index"] = int(i)
                obj["bcett_rail_kind"] = kind
                if str(kind).casefold() == "gachiyagura":
                    obj["spl_dynamic_team_color"] = True
                    obj["osi_dynamic_team_color"] = True
                    obj["spl_active_team"] = "neutral"
                    obj["spl_team_color_kind"] = "gachiyagurarail"
            except Exception:
                pass
            count += 1

    if kind == "gachiyagura":
        # In-game tower rail visuals appear as a paired rail: a duplicate copy rotated
        # 180 degrees around the stage origin.  Rotate the generated helper geometry
        # positions directly so it stays independent of object-parent transform order.
        locs_rot = [Vector((-float(v.x), -float(v.y), float(v.z))) for v in locs]
        duplicate_rule = "rotated_180_z_about_origin"
        root_rot = root
        try:
            root["bcett_rail_duplicate"] = duplicate_rule
            root["bcett_rail_duplicate_merged_into_root"] = True
            root["bcett_rail_duplicate_node0_shared"] = True
        except Exception:
            pass
        # Mirrored GachiYagura shares node 0 with the original rail; only mirror
        # nodes 1..N and the segment geometry.
        for i, loc in enumerate(locs_rot):
            if i == 0:
                continue
            obj = _rail_create_sphere("%s.rot180.node%02d" % (label, i), loc, node_radius, mat_node, collection)
            if obj is not None:
                _parent_keep_world(obj, root_rot)
                try:
                    obj["bcett_rail_node_index"] = int(i)
                    obj["bcett_rail_kind"] = kind
                    obj["bcett_rail_duplicate"] = duplicate_rule
                    _set_props_from_mapping(obj, "rail_point", usable[i])
                except Exception:
                    pass
                count += 1
        for i in range(len(locs_rot)-1):
            obj = _rail_create_cylinder_between("%s.rot180.seg%02d_%02d" % (label, i, i+1), locs_rot[i], locs_rot[i+1], line_radius, mat_line, collection, alpha_note="team_color_candidate_effect_line_rot180")
            if obj is not None:
                _parent_keep_world(obj, root_rot)
                try:
                    obj["bcett_rail_segment_index"] = int(i)
                    obj["bcett_rail_kind"] = kind
                    obj["bcett_rail_duplicate"] = duplicate_rule
                    if str(kind).casefold() == "gachiyagura":
                        obj["spl_dynamic_team_color"] = True
                        obj["osi_dynamic_team_color"] = True
                        obj["spl_active_team"] = "neutral"
                        obj["spl_team_color_kind"] = "gachiyagurarail"
                except Exception:
                    pass
                count += 1
        count += 1
        report_lines.append("RAIL_ARRAY_DUPLICATE_ROT180 index=%d label=%r layer=%r kind=gachiyagura nodes=%d" % (int(rail_index), label, layer_label, len(locs_rot)))

    report_lines.append("RAIL_ARRAY_IMPORTED index=%d label=%r layer=%r kind=%s nodes=%d objects=%d collection='Non-Geometry Objects/Rail/%s' note=type_specific_rail_visuals_effect_assets_pending" % (int(rail_index), label, layer_label, kind, len(locs), count, layer_label))
    return count


def _import_bcett_rails_array(root, stage_collection, prefs, import_cache, report_lines):
    rails = root.get("Rails", []) if isinstance(root, dict) else []
    if not isinstance(rails, list):
        return 0, set()
    count = 0
    imported_hashes = set()
    for idx, rail in enumerate(rails):
        try:
            made = _import_generic_bcett_rail(rail, idx, stage_collection, prefs, import_cache, report_lines)
            if made:
                count += int(made)
                imported_hashes.update(_rail_hash_values(rail))
        except Exception as exc:
            report_lines.append("RAIL_ARRAY_FAILED index=%d error=%s" % (int(idx), exc))
    report_lines.append("RAIL_ARRAY_SUMMARY rails=%d imported_objects=%d imported_hashes=%d" % (len(rails), int(count), len(imported_hashes)))
    return count, imported_hashes

def _looks_like_resource_ref(value):
    if not isinstance(value, str):
        return False
    text = value.replace("\\", "/").strip()
    if not text:
        return False
    markers = ("Work/", "Banc/", "Scene/", "Gyml/", "Graffiti/", "SceneComponent/", "Spectator/", "Pack/", "Model/")
    suffixes = (".gyml", ".bgyml", ".byml", ".json", ".pack.zs", ".bfres", ".bfres.zs", ".bntx", ".bntx.zs", ".ftxb", ".ftxb.zs", ".fsnb", ".fskb")
    return text.startswith(markers) or text.endswith(suffixes)


def _collect_resource_refs(value, *, limit=128):
    refs = []
    seen = set()
    def add(v):
        if _looks_like_resource_ref(v):
            k = v.strip()
            if k not in seen and len(refs) < limit:
                seen.add(k)
                refs.append(k)
    def walk(v):
        if len(refs) >= limit:
            return
        if isinstance(v, str):
            add(v)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
                if len(refs) >= limit:
                    break
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
                if len(refs) >= limit:
                    break
    walk(value)
    return refs


def _report_pack_reference_resolutions(files, source_path, data, report_lines, *, tag="SCENE_REF"):
    refs = _collect_resource_refs(data)
    if not refs:
        return 0
    resolver = _current_pack_resolver(files)
    resolved_count = 0
    for ref in refs:
        resolved = resolver.resolve(ref, allow_basename_search=True, allow_directories=True)
        if resolved.get("bytes") is not None:
            resolved_count += 1
            report_lines.append("%s_RESOLVED source=%r ref=%r location=%s path=%r" % (tag, source_path, ref, resolved.get("location"), resolved.get("name")))
        else:
            report_lines.append("%s_UNRESOLVED source=%r ref=%r candidates=%s" % (tag, source_path, ref, resolved.get("candidates")))
    return resolved_count

def _camera_euler_from_pack(rot_dict):
    # Pack camera fixed params appear in degrees.  Convert by using the existing
    # standard dict path, then keep custom props so this can be refined later.
    raw = _raw_rotation_tuple(rot_dict or {}, 0.0)
    return Euler(tuple(math.radians(v) for v in raw), "XYZ")


def _import_pack_fixed_camera(name, data, source_path, parent_collection, report_lines):
    cam_data = bpy.data.cameras.new(_safe_collection_name(name))
    obj = bpy.data.objects.new(_safe_collection_name(name), cam_data)
    parent_collection.objects.link(obj)
    if isinstance(data, dict):
        obj.location = _vector_from_any(data.get("Pos", {}), 0.0, splatoon_axis_order=True)
        obj.rotation_euler = _camera_euler_from_pack(data.get("Rot", {}))
        fovy = _numeric_from_any(data.get("FovY"), None)
        if fovy is not None:
            try:
                cam_data.angle = math.radians(float(fovy))
            except Exception:
                pass
        obj["splatoon_source"] = source_path
        _set_props_from_mapping(obj, "camera_param", data)
    report_lines.append("SCENE_CAMERA imported path=%r object=%r" % (source_path, obj.name))
    return obj


def _import_pack_cameras(files, camera_ref, stage_collection, report_lines):
    source_name, camera_param = _pack_parse_byml(files, camera_ref, report_lines, required=True)
    if not isinstance(camera_param, dict):
        return 0
    cam_col = _get_or_create_child_collection(_get_or_create_child_collection(_get_or_create_child_collection(stage_collection, "Non-Geometry Objects"), "Cameras"), "Cmn")
    count = 0
    # Import fixed camera resources referenced by the versus camera component.
    for key in ("MiniMapCamera", "PaintResultCamera"):
        ref = camera_param.get(key)
        fixed_name, fixed_param = _pack_parse_byml(files, ref, report_lines, required=False)
        if isinstance(fixed_param, dict):
            _import_pack_fixed_camera(key, fixed_param, fixed_name, cam_col, report_lines)
            count += 1
        elif ref:
            report_lines.append("SCENE_CAMERA_REF_ONLY key=%r ref=%r" % (key, ref))
    # Preserve non-BYML resource links on an empty for future camera animation work.
    empty = bpy.data.objects.new("VersusCamera Pack Links", None)
    empty.empty_display_type = "CAMERA"
    empty.empty_display_size = 2.0
    cam_col.objects.link(empty)
    empty["splatoon_source"] = source_name or str(camera_ref)
    _set_props_from_mapping(empty, "versus_camera", camera_param)
    count += 1
    report_lines.append("SCENE_CAMERA_LINKS imported path=%r object=%r" % (source_name, empty.name))
    return count



def _pack_files_from_archive_bytes(data, source_name, report_lines=None):
    """Return normalized SARC entries from a pack payload, accepting Zstd-wrapped SARC."""
    try:
        payload = bytes(data or b"")
        if payload[:4] != b"SARC":
            payload = _decompress_zstd_bytes(payload, source_name)
        return {name.replace("\\", "/"): payload for name, payload in sarc.iter_sarc_files(payload)}
    except Exception as exc:
        _report_append(report_lines, "SCENE_SKYBOX_PACK_PARSE_FAILED path=%r error=%s" % (source_name, exc))
        return {}


def _skybox_candidate_refs(sky_data):
    """Build conservative actor-pack/actor-param candidates from a SkySphere block."""
    refs = []
    seen = set()

    def add(ref):
        if not ref:
            return
        text = str(ref).replace("\\", "/").strip()
        if not text:
            return
        if text not in seen:
            seen.add(text)
            refs.append(text)

    # Preserve explicit resource references first.
    for ref in _collect_resource_refs(sky_data):
        add(ref)

    # Also inspect short symbolic values such as "sky_Foo" or "Sky_Foo".
    def walk(v):
        if isinstance(v, str):
            text = v.strip().replace("\\", "/")
            if text and ("sky_" in text.casefold() or text.casefold().startswith("sky")):
                add(text)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
    walk(sky_data)

    expanded = []
    seen_expanded = set()
    def add_expanded(ref):
        if ref and ref not in seen_expanded:
            seen_expanded.add(ref)
            expanded.append(ref)

    for ref in refs:
        add_expanded(ref)
        stem = Path(ref).name
        for suffix in (".engine__actor__ActorParam.gyml", ".engine__actor__ActorParam.bgyml", ".pack.zs", ".pack", ".gyml", ".bgyml"):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        if stem:
            add_expanded("Pack/Actor/%s.pack.zs" % stem)
            add_expanded("Actor/%s.pack.zs" % stem)
            add_expanded("Work/Actor/%s.engine__actor__ActorParam.gyml" % stem)
            add_expanded("Work/Actor/%s.engine__actor__ActorParam.bgyml" % stem)
    return expanded


def _parse_actor_param_from_pack_bytes(pack_bytes, source_name, report_lines):
    files = _pack_files_from_archive_bytes(pack_bytes, source_name, report_lines)
    if not files:
        return None, None, files
    candidates = [n for n in files if n.casefold().endswith((".engine__actor__actorparam.gyml", ".engine__actor__actorparam.bgyml"))]
    if not candidates:
        candidates = [n for n in files if n.casefold().endswith(("actorparam.gyml", "actorparam.bgyml", ".gyml", ".bgyml"))]
    for name in sorted(candidates, key=lambda n: (0 if "actorparam" in n.casefold() else 1, len(n), n)):
        try:
            return name, byml.Byml(files[name]).parse(), files
        except Exception as exc:
            _report_append(report_lines, "SCENE_SKYBOX_ACTOR_PARAM_PARSE_FAILED path=%r entry=%r error=%s" % (source_name, name, exc))
    return None, None, files


def _model_names_from_resource_refs(refs):
    names = []
    def add(name):
        if name and name not in names:
            names.append(name)
    for ref in refs or []:
        text = str(ref).replace("\\", "/").strip()
        low = text.casefold()
        if not text:
            continue
        if any(low.endswith(suf) for suf in (".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres", ".zs")) or "/model/" in low or low.startswith("model/") or low.startswith("work/model/"):
            stem = Path(text).name
            for suffix in (".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres", ".zs"):
                if stem.casefold().endswith(suffix):
                    stem = stem[:-len(suffix)]
                    break
            add(stem)
    return names


def _find_skybox_model_path(actor_data, actor_pack_files, models_dir, report_lines, source_name):
    refs = []
    refs.extend(_collect_resource_refs(actor_data))
    for name in actor_pack_files or {}:
        if any(name.casefold().endswith(suf) for suf in (".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres")):
            refs.append(name)
    names = _model_names_from_resource_refs(refs)
    # Fallback to actor pack/param stems; sky actors frequently share their model stem.
    for ref in (source_name,):
        stem = Path(str(ref)).name
        for suffix in (".pack.zs", ".pack", ".engine__actor__ActorParam.gyml", ".engine__actor__ActorParam.bgyml"):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        if stem and stem not in names:
            names.append(stem)
    concrete = []
    resolver = _current_pack_resolver()
    for ref in refs:
        if not str(ref).casefold().endswith((".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres", ".zs")):
            continue
        resolved = resolver.resolve(ref, allow_basename_search=True, allow_directories=False)
        if resolved.get("path") and resolved.get("location", "").startswith("romfs"):
            concrete.append(Path(resolved.get("path")))
            _report_append(report_lines, "SCENE_SKYBOX_MODEL_REF_RESOLVED source=%r ref=%r location=%s path=%r" % (source_name, ref, resolved.get("location"), resolved.get("name")))
        elif resolved.get("bytes") is not None:
            _report_append(report_lines, "SCENE_SKYBOX_MODEL_REF_PACK_ONLY source=%r ref=%r location=%s path=%r reason=bfres_import_requires_filesystem_path" % (source_name, ref, resolved.get("location"), resolved.get("name")))
        else:
            _report_append(report_lines, "SCENE_SKYBOX_MODEL_REF_UNRESOLVED source=%r ref=%r candidates=%s" % (source_name, ref, resolved.get("candidates")))
    found = _find_bfres_model(models_dir, names, concrete)
    if found:
        _report_append(report_lines, "SCENE_SKYBOX_MODEL_FOUND source=%r names=%s path=%r" % (source_name, names, str(found)))
    else:
        _report_append(report_lines, "SCENE_SKYBOX_MODEL_MISSING source=%r names=%s refs=%s" % (source_name, names, refs[:16]))
    return found, names


def _import_rendering_skybox(data, source_path, files, env_col, prefs, models_dir, import_cache, report_lines):
    if not isinstance(data, dict):
        return 0
    lighting = data.get("Lighting", {}) if isinstance(data.get("Lighting"), dict) else {}
    sky = None
    if isinstance(lighting.get("SkySphere"), dict):
        sky = lighting.get("SkySphere")
    elif isinstance(data.get("SkySphere"), dict):
        sky = data.get("SkySphere")
    if not isinstance(sky, dict) or not sky:
        _report_append(report_lines, "SCENE_SKYBOX none path=%r reason=no_skysphere_section" % source_path)
        return 0

    resolver = _current_pack_resolver(files)
    candidates = _skybox_candidate_refs(sky)
    _report_append(report_lines, "SCENE_SKYBOX_CANDIDATES path=%r candidates=%s" % (source_path, candidates))
    actor_data = None
    actor_source = None
    actor_pack_files = {}
    for ref in candidates:
        resolved = resolver.resolve(ref, allow_basename_search=True, allow_directories=False)
        if resolved.get("bytes") is None:
            _report_append(report_lines, "SCENE_SKYBOX_ACTOR_UNRESOLVED source=%r ref=%r candidates=%s" % (source_path, ref, resolved.get("candidates")))
            continue
        name = resolved.get("name") or str(ref)
        payload = resolved.get("bytes")
        low = str(name).casefold()
        try:
            if low.endswith(".pack.zs") or low.endswith(".pack"):
                entry_name, parsed, pack_files = _parse_actor_param_from_pack_bytes(payload, name, report_lines)
                if isinstance(parsed, dict):
                    actor_data = parsed
                    actor_source = "%s::%s" % (name, entry_name)
                    actor_pack_files = pack_files
                    _report_append(report_lines, "SCENE_SKYBOX_ACTOR_PACK_RESOLVED source=%r ref=%r location=%s path=%r actor_param=%r" % (source_path, ref, resolved.get("location"), name, entry_name))
                    break
            else:
                parsed = byml.Byml(payload).parse()
                if isinstance(parsed, dict):
                    actor_data = parsed
                    actor_source = name
                    _report_append(report_lines, "SCENE_SKYBOX_ACTOR_PARAM_RESOLVED source=%r ref=%r location=%s path=%r" % (source_path, ref, resolved.get("location"), name))
                    break
        except Exception as exc:
            _report_append(report_lines, "SCENE_SKYBOX_ACTOR_PARSE_FAILED source=%r ref=%r path=%r error=%s" % (source_path, ref, name, exc))
    if not isinstance(actor_data, dict):
        _report_append(report_lines, "SCENE_SKYBOX none path=%r reason=no_actor_param_resolved candidates=%s" % (source_path, candidates))
        return 0

    bfres_path, target_names = _find_skybox_model_path(actor_data, actor_pack_files, models_dir, report_lines, actor_source or source_path)
    sky_params_col = _get_or_create_child_collection(env_col, "SkySphereParams")
    sky_col = _get_or_create_child_collection(env_col, "Sky")
    helper = bpy.data.objects.new(_safe_collection_name(Path(str(source_path)).stem + " SkySphere Params"), None)
    helper.empty_display_type = "SPHERE"
    helper.empty_display_size = 6.0
    sky_params_col.objects.link(helper)
    helper["splatoon_source"] = source_path
    helper["skybox_actor_source"] = actor_source or ""
    _set_props_from_mapping(helper, "sky_sphere", sky)
    _set_props_from_mapping(helper, "sky_actor", actor_data)
    count = 1
    if not bfres_path:
        _report_append(report_lines, "SCENE_SKYBOX_PARAMS imported path=%r object=%r reason=model_not_found" % (source_path, helper.name))
        return count

    try:
        imported_objects, bfres_messages, _cache_hit = _import_bfres_cached(
            str(bfres_path), prefs, import_cache, report_lines, "Skybox", name_prefix="Skybox_", target_model_names=None, texture_diag_owner="PACK_SCENE_SKYBOX"
        )
    except Exception as exc:
        _report_append(report_lines, "SCENE_SKYBOX_BFRES_FAILED path=%r bfres=%r error=%s" % (source_path, str(bfres_path), exc))
        return count
    for obj in imported_objects:
        try:
            obj["splatoon_source"] = source_path
            obj["skybox_actor_source"] = actor_source or ""
            obj["skybox_bfres"] = str(bfres_path)
            obj["skybox_model_candidates"] = list(target_names or [])
        except Exception:
            pass
    _move_objects_to_collection(imported_objects, sky_col)
    _disable_sky_shadow_ray_visibility(imported_objects)
    for msg in bfres_messages:
        _report_append(report_lines, "SCENE_SKYBOX_BFRES path=%s %s" % (bfres_path.name, msg))
    _report_append(report_lines, "SCENE_SKYBOX_IMPORTED path=%r actor=%r bfres=%r objects=%d" % (source_path, actor_source, str(bfres_path), len(imported_objects)))
    count += len(imported_objects)
    return count


def _sun_rotation_from_lat_lon(latitude, longitude):
    """Convert Nintendo MainLight Latitude/Longitude fields to Blender Euler rotation.

    These values appear to be authored as direct rotation controls rather than
    spherical coordinates: Blender XYZ rotation is (90 - Latitude, 0, Longitude).
    """
    lat = float(latitude if latitude is not None else 45.0)
    lon = float(longitude if longitude is not None else 0.0)
    return Euler((math.radians(90.0 - lat), 0.0, math.radians(lon)), 'XYZ')


def _rendering_collection_name(source_path):
    stem = Path(str(source_path or "Rendering")).stem
    # Strip Blender-unfriendly suffixes while keeping names like RenderingDay.
    for suffix in (".bgyml", ".gyml", ".byml"):
        if stem.casefold().endswith(suffix):
            stem = stem[:-len(suffix)]
    return _safe_collection_name(stem or "Rendering")


def _import_rendering_env(data, source_path, env_col, report_lines, files=None, prefs=None, models_dir=None, import_cache=None):
    if not isinstance(data, dict):
        return 0
    count = 0
    lighting = data.get("Lighting", {}) if isinstance(data.get("Lighting"), dict) else {}
    main = lighting.get("MainLight", {}) if isinstance(lighting.get("MainLight"), dict) else {}
    if main:
        # Keep imported environment items directly under the Rendering* collection
        # so View3D Day/Night/Sunset toggles can affect the whole rendering set.
        light_data = bpy.data.lights.new(_safe_collection_name(Path(str(source_path)).stem + " MainLight"), type="SUN")
        obj = bpy.data.objects.new(light_data.name, light_data)
        env_col.objects.link(obj)
        light_data.color = _color_from_any(main.get("Color"), (1.0, 1.0, 1.0))
        intens = _numeric_from_any(main.get("Intens"), 1.0)
        if intens is not None:
            light_data.energy = float(intens)
        obj.rotation_euler = _sun_rotation_from_lat_lon(main.get("Latitude", 45.0), main.get("Longitude", 0.0))
        obj["splatoon_source"] = source_path
        obj["osi_rendering_set"] = _rendering_collection_name(source_path)
        _set_props_from_mapping(obj, "main_light", main)
        count += 1
        report_lines.append("SCENE_ENV_LIGHT imported path=%r collection=%r object=%r" % (source_path, env_col.name, obj.name))
    if files is not None and prefs is not None and models_dir is not None and import_cache is not None:
        count += _import_rendering_skybox(data, source_path, files, env_col, prefs, models_dir, import_cache, report_lines)
    helper = bpy.data.objects.new(_safe_collection_name(Path(str(source_path)).stem + " Env Params"), None)
    helper.empty_display_type = "SPHERE"
    helper.empty_display_size = 3.0
    env_col.objects.link(helper)
    helper["splatoon_source"] = source_path
    helper["osi_rendering_set"] = _rendering_collection_name(source_path)
    _set_props_from_mapping(helper, "env", data)
    count += 1
    report_lines.append("SCENE_ENV_PARAMS imported path=%r collection=%r object=%r" % (source_path, env_col.name, helper.name))
    return count


def _is_rendering_env_path(name):
    text = str(name or "").replace('\\', '/')
    base = Path(text).name
    low = base.casefold()
    if not low.startswith("rendering"):
        return False
    return low.endswith((".bgyml", ".gyml", ".byml"))


def _parse_pack_byml_payload(name, payload, report_lines):
    try:
        return byml.Byml(payload).parse()
    except Exception as exc:
        _report_append(report_lines, "SCENE_RENDERING_PARSE_FAILED path=%r error=%s" % (name, exc))
        return None


def _import_pack_environment(files, field_env_ref, stage_collection, report_lines, prefs=None, models_dir=None, import_cache=None):
    env_root = _get_or_create_child_collection(_get_or_create_child_collection(stage_collection, "Non-Geometry Objects"), "Environment")
    count = 0
    imported_rendering_paths = set()
    source_name, field_env = _pack_parse_byml(files, field_env_ref, report_lines, required=True)
    if isinstance(field_env, dict):
        helper = bpy.data.objects.new("FieldEnv Pack Links", None)
        helper.empty_display_type = "SPHERE"
        helper.empty_display_size = 2.0
        env_root.objects.link(helper)
        helper["splatoon_source"] = source_name or str(field_env_ref)
        _set_props_from_mapping(helper, "field_env", field_env)
        _report_pack_reference_resolutions(files, source_name or str(field_env_ref), field_env, report_lines, tag="SCENE_ENV_REF")
        count += 1
        env_items = []
        for key, ref in field_env.items():
            if str(key).startswith("EnvSet") and ref not in (None, ""):
                env_items.append((str(key), ref))
        # Prefer the usual ordering, then any additional EnvSet* entries in the file.
        preferred_order = {"EnvSetDay": 0, "EnvSetNight": 1, "EnvSetSunset": 2}
        env_items.sort(key=lambda item: (preferred_order.get(item[0], 100), item[0]))
        for key, ref in env_items:
            render_name, render_data = _pack_parse_byml(files, ref, report_lines, required=False)
            if isinstance(render_data, dict):
                # Name Blender collections from the FieldEnv entries (EnvSetDay,
                # EnvSetNight, etc.) instead of raw Rendering*.bgyml filenames.
                sub_col = _get_or_create_child_collection(env_root, _safe_collection_name(key, "EnvSet"))
                imported_rendering_paths.add(str(render_name or ref or key).casefold())
                count += _import_rendering_env(render_data, render_name or key, sub_col, report_lines, files=files, prefs=prefs, models_dir=models_dir, import_cache=import_cache)

    # Some stages include Rendering*.bgyml files that are not explicitly listed
    # in FieldEnv refs. Import each present Rendering* file into its own
    # Environment child collection so Day/Night/Sunset/extra render contexts can
    # be toggled independently.
    for name in sorted(files):
        if not _is_rendering_env_path(name):
            continue
        key = str(name).casefold()
        if key in imported_rendering_paths:
            continue
        data = _parse_pack_byml_payload(name, files[name], report_lines)
        if not isinstance(data, dict):
            continue
        sub_col = _get_or_create_child_collection(env_root, _rendering_collection_name(name))
        imported_rendering_paths.add(key)
        count += _import_rendering_env(data, name, sub_col, report_lines, files=files, prefs=prefs, models_dir=models_dir, import_cache=import_cache)
    if imported_rendering_paths:
        report_lines.append("SCENE_ENV_RENDERING_SUMMARY collections=%s count=%d" % (sorted(imported_rendering_paths), len(imported_rendering_paths)))
    return count


def _candidate_stage_bfres_paths(models_dir, stem):
    names = []
    stem = str(stem or "").strip()
    stripped = stem
    for prefix in ("Vss_", "Coop_", "Mission_"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    for n in ("Fld_%s" % stem, "Fld_%s" % stripped, stem, stripped):
        if n and n not in names:
            names.append(n)
    paths = []
    for n in names:
        found = _find_bfres_model(models_dir, [n])
        if found and found not in paths:
            paths.append(found)
    return paths


def _water_model_targets(path):
    terms = ("water", "ocean", "sea", "lake", "pond", "river")
    names = _bfres_fmdl_names_cached(path) or ()
    return [n for n in names if any(t in str(n).casefold() for t in terms)]


def _import_pack_water(files, stage_collection, prefs, models_dir, import_cache, report_lines):
    # Import explicit Ocean.bgyml helpers under Non-Geometry/Environment,
    # and try to import water-like FMDLs from the corresponding Fld_<stage>.bfres.zs
    # only when pack extra geometry is enabled.
    water_col = _get_or_create_child_collection(_get_or_create_child_collection(stage_collection, "Non-Geometry Objects"), "Environment")
    count = 0
    ocean_entries = sorted(n for n in files if n.startswith("Gyml/") and "Ocean" in Path(n).name and n.endswith(".bgyml"))
    for name in ocean_entries:
        try:
            data = byml.Byml(files[name]).parse()
        except Exception as exc:
            report_lines.append("SCENE_WATER_PARSE_FAILED path=%r error=%s" % (name, exc))
            continue
        helper = bpy.data.objects.new(_safe_collection_name(Path(name).stem + " Params"), None)
        helper.empty_display_type = "CUBE"
        helper.empty_display_size = 4.0
        water_col.objects.link(helper)
        helper["splatoon_source"] = name
        _set_props_from_mapping(helper, "ocean", data if isinstance(data, dict) else {"value": data})
        count += 1
        report_lines.append("SCENE_WATER_PARAMS imported path=%r object=%r" % (name, helper.name))
    stem = _pack_stage_stem()
    imported_any_mesh = False
    if not bool(globals().get("import_geometry", True)):
        report_lines.append("SCENE_WATER_GEOMETRY_SKIPPED stage=%r reason=geometry_disabled" % stem)
        return count
    if not bool(globals().get("import_pack_extra_geometry", False)):
        # Scene extras should be metadata-only by default.  Record candidate water
        # meshes without invoking the BFRES/material pipeline; users can enable
        # Import Pack Extra Geometry when they explicitly want these objects.
        for bfres_path in _candidate_stage_bfres_paths(models_dir, stem):
            targets = _water_model_targets(bfres_path)
            if targets:
                report_lines.append("SCENE_WATER_GEOMETRY_SKIPPED bfres=%r targets=%s reason=pack_extras_read_only_default" % (str(bfres_path), targets))
            else:
                report_lines.append("SCENE_WATER_NO_TARGETS bfres=%r reason=no_water_like_fmdl_names" % str(bfres_path))
        if not ocean_entries:
            report_lines.append("SCENE_WATER none stage=%r reason=no_ocean_bgyml_and_pack_extra_geometry_disabled" % stem)
        return count
    for bfres_path in _candidate_stage_bfres_paths(models_dir, stem):
        targets = _water_model_targets(bfres_path)
        if not targets:
            report_lines.append("SCENE_WATER_NO_TARGETS bfres=%r reason=no_water_like_fmdl_names" % str(bfres_path))
            continue
        try:
            imported_objects, bfres_messages, _cache_hit = _import_bfres_cached(
                str(bfres_path), prefs, import_cache, report_lines, "PackWater", name_prefix="Water_", target_model_names=targets, texture_diag_owner="PACK_SCENE_WATER"
            )
        except Exception as exc:
            report_lines.append("SCENE_WATER_BFRES_FAILED bfres=%r targets=%s error=%s" % (str(bfres_path), targets, exc))
            continue
        for obj in imported_objects:
            try:
                obj["splatoon_source"] = str(bfres_path)
                obj["water_targets"] = list(targets)
            except Exception:
                pass
        _move_objects_to_collection(imported_objects, water_col)
        for msg in bfres_messages:
            report_lines.append("SCENE_WATER_BFRES path=%s %s" % (bfres_path.name, msg))
        report_lines.append("SCENE_WATER_IMPORTED bfres=%r targets=%s objects=%d" % (str(bfres_path), targets, len(imported_objects)))
        imported_any_mesh = imported_any_mesh or bool(imported_objects)
        count += len(imported_objects)
    if not ocean_entries and not imported_any_mesh:
        report_lines.append("SCENE_WATER none stage=%r reason=no_ocean_bgyml_and_no_water_like_fmdl_targets" % stem)
    return count


# v0.8.05 actor-linked rail import helpers.  Rails[] entries are path data;
# visuals are created only when an actor BancParam links to a rail/rail point.
def _geometry_rail_layer_collection(stage_collection, layer_label):
    geometry_root = _get_or_create_child_collection(stage_collection, "Geometry")
    rail_root = _get_or_create_child_collection(geometry_root, "Rail")
    return _get_or_create_child_collection(rail_root, _safe_collection_name(layer_label, "NoLayer"))

# Preserve existing call sites while routing rail helpers to Geometry/Rail.
def _non_geometry_rail_layer_collection(stage_collection, layer_label):
    return _geometry_rail_layer_collection(stage_collection, layer_label)


def _bancparam_key_text(entry):
    if not isinstance(entry, dict):
        return ""
    parts = []
    for key, value in entry.items():
        key_text = str(key)
        if "BancParam" in key_text:
            parts.append(key_text.casefold())
        # Some parsed BYML variants keep the class name in a nested mapping or
        # string value rather than as the direct dictionary key.  Include only
        # BancParam-looking values here; avoid actor/model names so e.g.
        # YaguraPlatform with spl__ailift__AILiftBancParam cannot be mistaken
        # for the tower rail.
        if isinstance(value, str) and "BancParam" in value:
            parts.append(value.casefold())
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                sub_key_text = str(sub_key)
                if "BancParam" in sub_key_text:
                    parts.append(sub_key_text.casefold())
                if isinstance(sub_value, str) and "BancParam" in sub_value:
                    parts.append(sub_value.casefold())
    return " ".join(parts)


def _is_gachiyagura_bancparam_actor(entry):
    # Class-first classification only.  Actor/model names containing "Yagura"
    # are not sufficient: Hiagari has AILift actors named YaguraPlatform/Road.
    text = _bancparam_key_text(entry)
    return "gachiyagura" in text and "ailift" not in text


def _is_ailift_bancparam_actor(entry):
    # Class-first classification only.  Name-token fallback caused Yagura*
    # AILift objects to be routed through the Gachiyagura rail visualizer.
    text = _bancparam_key_text(entry)
    return "ailift" in text


def _make_actor_linked_rail_root(entry, actor_label, layer_label, kind, collection, rail=None, location=None):
    root = bpy.data.objects.new(_safe_collection_name("%s.%sRailPath" % (actor_label, kind)), None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.7
    if location is not None:
        try:
            root.location = Vector(location)
        except Exception:
            pass
    collection.objects.link(root)
    try:
        root["bcett_actor_linked_rail"] = True
        root["bcett_rail_kind"] = kind
        root["bcett_layer"] = layer_label
        root["bcett_actor_label"] = actor_label
        if isinstance(rail, dict):
            root["bcett_rail_hashes"] = ",".join(str(v) for v in _rail_hash_values(rail))
            _set_props_from_mapping(root, "rail", rail)
    except Exception:
        pass
    return root


def _import_path_visual_from_points(points, actor_label, layer_label, stage_collection, report_lines, *, kind="motion", rail=None, duplicate_rot180=False, node_radius=0.10, line_radius=0.035, node_color=(0.8, 0.8, 0.8), line_color=(0.8, 0.8, 0.8), line_alpha=0.35, point_transform_matrix=None, point_space="world"):
    stage_name_for_rail = str(getattr(stage_collection, "name", "") or "")
    if not points or len(points) < 2:
        return 0
    raw_locs = [Vector(_rail_point_location(point)) for point in points]
    if point_transform_matrix is not None:
        locs = [point_transform_matrix @ loc for loc in raw_locs]
    else:
        locs = list(raw_locs)
    collection = _geometry_rail_layer_collection(stage_collection, layer_label)
    if str(kind).casefold() == "gachiyagura":
        node_color = (0.45, 0.45, 0.45)
        line_color = (0.45, 0.45, 0.45)
    root = _make_actor_linked_rail_root(None, actor_label, layer_label, kind, collection, rail=rail, location=locs[0])
    if str(kind).casefold() == "gachiyagura":
        try:
            root["spl_dynamic_team_color"] = True
            root["spl_active_team"] = "neutral"
            root["spl_team_color_kind"] = "gachiyagurarail"
            root["osi_dynamic_team_color"] = True
        except Exception:
            pass
    mat_node = _rail_make_material("Spl_Rail_%s_Node" % kind, node_color, 1.0) if node_radius and node_radius > 0 else None
    mat_line = _rail_make_material("Spl_Rail_%s_Line" % kind, line_color, line_alpha)
    count = 1
    for i, loc in enumerate(locs):
        if node_radius and node_radius > 0:
            obj = _rail_create_sphere("%s.%s.node%02d" % (actor_label, kind, i), loc, node_radius, mat_node, collection)
            if obj is not None:
                _parent_keep_world(obj, root)
                try:
                    obj["bcett_rail_node_index"] = int(i)
                    obj["bcett_rail_kind"] = kind
                    if str(kind).casefold() == "gachiyagura":
                        obj["spl_dynamic_team_color"] = True
                        obj["osi_dynamic_team_color"] = True
                        obj["spl_active_team"] = "neutral"
                    _set_props_from_mapping(obj, "rail_point", points[i])
                except Exception:
                    pass
                count += 1
    for i in range(len(locs) - 1):
        obj = _rail_create_cylinder_between("%s.%s.seg%02d_%02d" % (actor_label, kind, i, i + 1), locs[i], locs[i + 1], line_radius, mat_line, collection, alpha_note="actor_linked_motion_path")
        if obj is not None:
            _parent_keep_world(obj, root)
            try:
                obj["bcett_rail_segment_index"] = int(i)
                obj["bcett_rail_kind"] = kind
                if str(kind).casefold() == "gachiyagura":
                    obj["spl_dynamic_team_color"] = True
                    obj["osi_dynamic_team_color"] = True
                    obj["spl_active_team"] = "neutral"
                    obj["spl_team_color_kind"] = "gachiyagurarail"
            except Exception:
                pass
            count += 1
    if duplicate_rot180:
        # Most Gachiyagura routes mirror as an extension from the shared start
        # node.  AutoWalk is a known exception: its mirrored tower route uses a
        # direct Y-value mirror rather than a 180 degree pivot around node 0.
        pivot = Vector(locs[0])
        if "autowalk" in stage_name_for_rail.casefold():
            locs_rot = [Vector((float(v.x), -float(v.y), float(v.z))) for v in locs]
            duplicate_rule = "mirrored_y_autowalk"
        else:
            locs_rot = [Vector((2.0 * pivot.x - float(v.x), 2.0 * pivot.y - float(v.y), float(v.z))) for v in locs]
            duplicate_rule = "rotated_180_z_about_node0"
        # Mirror helpers are part of the same actor-linked GachiYaguraRail, so
        # keep them under the original root instead of creating a second empty.
        root_rot = root
        try:
            root["bcett_rail_duplicate"] = duplicate_rule
            root["bcett_rail_duplicate_pivot"] = tuple(round(float(x), 6) for x in pivot)
            root["bcett_rail_duplicate_merged_into_root"] = True
        except Exception:
            pass
        for i, loc in enumerate(locs_rot):
            if i == 0:
                continue
            if node_radius and node_radius > 0:
                obj = _rail_create_sphere("%s.%s.rot180.node%02d" % (actor_label, kind, i), loc, node_radius, mat_node, collection)
                if obj is not None:
                    _parent_keep_world(obj, root_rot)
                    try:
                        obj["bcett_rail_node_index"] = int(i)
                        obj["bcett_rail_kind"] = kind
                        obj["bcett_rail_duplicate"] = duplicate_rule
                        if str(kind).casefold() == "gachiyagura":
                            obj["spl_dynamic_team_color"] = True
                            obj["osi_dynamic_team_color"] = True
                            obj["spl_active_team"] = "neutral"
                            obj["spl_team_color_kind"] = "gachiyagurarail"
                    except Exception:
                        pass
                    count += 1
        for i in range(len(locs_rot) - 1):
            obj = _rail_create_cylinder_between("%s.%s.rot180.seg%02d_%02d" % (actor_label, kind, i, i + 1), locs_rot[i], locs_rot[i + 1], line_radius, mat_line, collection, alpha_note="actor_linked_motion_path_" + duplicate_rule)
            if obj is not None:
                _parent_keep_world(obj, root_rot)
                try:
                    obj["bcett_rail_segment_index"] = int(i)
                    obj["bcett_rail_kind"] = kind
                    obj["bcett_rail_duplicate"] = duplicate_rule
                    if str(kind).casefold() == "gachiyagura":
                        obj["spl_dynamic_team_color"] = True
                        obj["osi_dynamic_team_color"] = True
                        obj["spl_active_team"] = "neutral"
                        obj["spl_team_color_kind"] = "gachiyagurarail"
                except Exception:
                    pass
                count += 1
    report_lines.append("ACTOR_LINKED_RAIL_IMPORTED actor=%r layer=%r kind=%s nodes=%d objects=%d collection='Geometry/Rail/%s' duplicate_rot180=%s point_space=%s root_loc=%s" % (actor_label, layer_label, kind, len(locs), count, layer_label, bool(duplicate_rot180), point_space, tuple(round(float(v), 6) for v in root.location)))
    if point_transform_matrix is not None:
        try:
            report_lines.append("ACTOR_LINKED_RAIL_POINT_TRANSFORM actor=%r layer=%r kind=%s rule=actor_local_points_to_world raw_first=%s world_first=%s raw_last=%s world_last=%s" % (
                actor_label, layer_label, kind,
                tuple(round(float(v), 6) for v in raw_locs[0]),
                tuple(round(float(v), 6) for v in locs[0]),
                tuple(round(float(v), 6) for v in raw_locs[-1]),
                tuple(round(float(v), 6) for v in locs[-1]),
            ))
        except Exception:
            pass
    return count


def _try_import_gachiyagura_rail_geometry(entry, actor_label, layer_label, prefs, import_cache, report_lines, stage_collection, banc_anchor_index):
    rail, points = _rail_for_bancparam_actor(entry, banc_anchor_index, report_lines, actor_label)
    if not points:
        return 0
    return _import_path_visual_from_points(points, actor_label, layer_label, stage_collection, report_lines, kind="gachiyagura", rail=rail, duplicate_rot180=True, node_radius=0.22, line_radius=0.055, node_color=(0.15, 0.85, 0.20), line_color=(0.15, 0.85, 0.20), line_alpha=0.38)


def _try_import_motion_path_rail_geometry(entry, actor_label, layer_label, prefs, import_cache, report_lines, stage_collection, banc_anchor_index, kind="motion"):
    rail, points = _rail_for_bancparam_actor(entry, banc_anchor_index, report_lines, actor_label)
    if not points:
        return 0
    # Non-InkRail / non-Gachiyagura rails are movement paths for the actor that
    # references them.  Their authored rail point translations/rotations are
    # interpreted in the actor's local frame, then transformed into world space
    # for the debug motion-path helper.  InkRail and Gachiyagura retain their
    # specialized path interpretation above.
    try:
        actor_loc, actor_rot, actor_scale = _native_object_base_transform(entry, convert_scale_axes=True)
        point_matrix = Matrix.LocRotScale(actor_loc, actor_rot, actor_scale)
    except Exception:
        point_matrix = None
    return _import_path_visual_from_points(points, actor_label, layer_label, stage_collection, report_lines, kind=kind, rail=rail, duplicate_rot180=False, node_radius=0.08, line_radius=0.025, node_color=(0.8, 0.8, 0.8), line_color=(0.8, 0.8, 0.8), line_alpha=0.30, point_transform_matrix=point_matrix, point_space="actor_local" if point_matrix is not None else "world_fallback")
