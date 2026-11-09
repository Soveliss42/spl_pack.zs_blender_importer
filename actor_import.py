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

from .collection_utils import (
    safe_collection_name as _safe_collection_name,
    get_or_create_child_collection as _get_or_create_child_collection,
    move_objects_to_collection as _move_objects_to_collection,
)
from .actor_helpers import (
    clean_actor_token as _clean_actor_token, entry_tokens as _entry_tokens,
    anchor_label as _anchor_label, add_anchor_to_index as _add_anchor_to_index,
)
from .property_utils import idprop_safe as _idprop_safe
from .transform import vector_from_any as _vector_from_any

_ADDON_DEPENDENCIES = {
    "_bfres_fmdl_names_cached", "_bfres_operator_from_preferences",
    "_effective_bfres_texture_mode", "_effective_import_materials",
    "_is_full_report", "_report_append",
}

def _sync_addon_dependencies(addon_module):
    missing=[]
    for name in sorted(_ADDON_DEPENDENCIES):
        if hasattr(addon_module, name):
            globals()[name] = getattr(addon_module, name)
        else:
            missing.append(name)
    return missing


def _geometry_root_collection(stage_collection):
    """Return the top-level Geometry collection for BCETT-rendered objects.

    BCETT layer labels such as Cmn/Pnt/Var/Vgl are still preserved, but they
    now live under one stage-local Geometry parent instead of directly under the
    stage collection. This keeps imported mesh/collection-instance content
    separate from Non-Geometry Objects and pack-derived helper data.
    """
    return _get_or_create_child_collection(stage_collection, "Geometry")


def _layer_collection_for_entry(stage_collection, entry):
    layer_value = None
    if isinstance(entry, dict):
        layer_value = entry.get("Layer")
    layer_name = _safe_collection_name(layer_value, "NoLayer")
    return _get_or_create_child_collection(_geometry_root_collection(stage_collection), layer_name)



def _build_actor_hash_index(objects):
    """Index BCETT actors by Hash/SRTHash for non-BancParam actor links.

    This is intentionally separate from the Rails-only BancParam index.  Sponge
    SafePosLinks point at GeneralLocator actors in BCETT data, so sponge corner
    inference needs actor lookup without changing generic BancParam behavior.
    """
    index = {}
    for actor_index, actor in enumerate(objects or []):
        if not isinstance(actor, dict):
            continue
        label = _anchor_label(actor, "Actor_%04d" % actor_index)
        anchor = {
            "kind": "Actor",
            "label": label,
            "entry": actor,
            "translate": actor.get("Translate"),
            "rotate": actor.get("Rotate") or actor.get("Rotation"),
            "layer": actor.get("Layer", "NoLayer"),
        }
        _add_anchor_to_index(index, actor.get("Hash"), anchor)
        _add_anchor_to_index(index, actor.get("SRTHash"), anchor)
    return index

def _is_sponge_actor(entry):
    if not isinstance(entry, dict):
        return False
    return any("sponge" in token.casefold() for token in _entry_tokens(entry)) or any(
        "sponge" in str(key).casefold() for key in entry.keys()
    )




def _sponge_actor_tokens(entry):
    tokens = []
    if not isinstance(entry, dict):
        return tokens
    for key in ("Gyaml", "Name", "__RowId", "ModelName", "UnitConfigName"):
        try:
            value = entry.get(key, "")
        except Exception:
            value = ""
        for candidate in (value, _clean_actor_token(value)):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in tokens:
                tokens.append(candidate)
    return tokens


def _sponge_model_override(models_dir, entry):
    """Return a sponge-specific (model_path, target_names) override when known.

    ActorInfo can collapse multiple sponge actor variants to the generic
    Obj_Sponge model pack.  Tall/stand sponge actors need the rectangle stand
    BFRES even though the FMDL inside that pack is also named Obj_Sponge.
    """
    if not _is_sponge_actor(entry):
        return None, None
    toks = [t.casefold() for t in _sponge_actor_tokens(entry)]
    combined = " ".join(toks)
    if any(k in combined for k in ("spongetall", "rectangle", "rectanglestand", "stand")):
        pack_name = "Obj_SpongeRectangleStand"
        target_names = ["Obj_Sponge"]
    else:
        pack_name = "Obj_Sponge"
        target_names = ["Obj_Sponge"]
    suffixes = (".bfres.zs", ".sbfres.zs", ".bfres", ".sbfres", ".szs", ".zs")
    try:
        for suffix in suffixes:
            candidate = Path(models_dir) / (pack_name + suffix)
            if candidate.is_file():
                return candidate, target_names
        # Fallback to the existing recursive finder if present in addon globals.
        finder = globals().get("_find_bfres_model")
        if callable(finder):
            candidate = finder(models_dir, [pack_name], [])
            if candidate is not None:
                return candidate, target_names
    except Exception:
        pass
    return None, target_names


def _sponge_inflate_max_multiplier_for_entry(entry):
    """Return additive inflate multiplier for sponge actor variants.

    The runtime scale factor is ``1 + multiplier * spl_sponge_inflate``.
    Standard small sponges keep the established 2.0 multiplier.  Tall/stand
    sponges are visually much larger at rest, so cap their inflate travel to
    1.2 as requested.
    """
    try:
        toks = [str(t).casefold() for t in _sponge_actor_tokens(entry)]
        combined = " ".join(toks)
        if any(k in combined for k in ("spongetall", "rectangle", "rectanglestand", "stand")):
            return 1.2
    except Exception:
        pass
    return 2.0

def _sponge_safepos_refs(entry):
    """Return hash values referenced by sponge SafePosLinks metadata."""
    refs = []
    seen = set()

    def add(value):
        if isinstance(value, bool):
            return
        try:
            number = int(value)
        except Exception:
            return
        if number not in seen:
            seen.add(number)
            refs.append(number)

    if not isinstance(entry, dict):
        return refs

    param = entry.get("spl__SpongeBancParam")
    if isinstance(param, dict):
        links = param.get("SafePosLinks")
        if isinstance(links, (list, tuple)):
            for value in links:
                add(value)
        else:
            add(links)

    # The same links are often duplicated in the generic Links array as
    # {Name: SafePosLinks, Dst: <hash>}.
    links = entry.get("Links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            if str(link.get("Name", "")) == "SafePosLinks":
                add(link.get("Dst"))
    return refs


def _mesh_bbox_in_root_space(imported_objects, actor_root):
    """Return (min_vec, max_vec) for mesh corners in actor-root local space."""
    inv = actor_root.matrix_world.inverted()
    mins = Vector((float("inf"), float("inf"), float("inf")))
    maxs = Vector((float("-inf"), float("-inf"), float("-inf")))
    found = False
    for obj in imported_objects:
        if getattr(obj, "type", None) != "MESH" or not getattr(obj, "bound_box", None):
            continue
        for corner in obj.bound_box:
            local = inv @ (obj.matrix_world @ Vector(corner))
            mins.x = min(mins.x, local.x)
            mins.y = min(mins.y, local.y)
            mins.z = min(mins.z, local.z)
            maxs.x = max(maxs.x, local.x)
            maxs.y = max(maxs.y, local.y)
            maxs.z = max(maxs.z, local.z)
            found = True
    if not found:
        return None, None
    return mins, maxs


def _sign_from_value(value, epsilon=1e-5):
    if value > epsilon:
        return 1.0
    if value < -epsilon:
        return -1.0
    return 0.0


def _snap_sponge_offset_half_units(vec, step=0.5, epsilon=1e-6):
    """Snap sponge SafePos offsets to stable half-unit increments.

    Multiplayer sponge offsets observed so far resolve to exact 0.5-unit
    increments.  The imported mesh bounds are slightly noisy, so snapping keeps
    the root-bone replacement from inheriting BFRES bounding-box drift while
    still allowing any axis, including ceiling/free-side cases, to resolve from
    the SafePos direction logic.
    """
    try:
        st = float(step)
        if st <= 0.0:
            st = 0.5
    except Exception:
        st = 0.5
    vals = []
    for comp in (getattr(vec, "x", 0.0), getattr(vec, "y", 0.0), getattr(vec, "z", 0.0)):
        try:
            snapped = round(float(comp) / st) * st
            if abs(snapped) < epsilon:
                snapped = 0.0
            vals.append(snapped)
        except Exception:
            vals.append(0.0)
    return Vector(vals)


def _sponge_blender_vector_to_pose_vector(vec):
    """Convert a Blender-space vector to sponge BFRES pose-bone vector space.

    Sponge skeleton pose channels use the same axis basis discrepancy seen in
    imported object locations: a desired Blender XYZ translation must be written
    to pose channels as (X, Z, -Y).  Keep all SafePos/inflate math in Blender
    space, then use this only at the final bone-channel write.
    """
    try:
        v = Vector(vec)
        return Vector((float(v.x), float(v.z), float(-v.y)))
    except Exception:
        return Vector((0.0, 0.0, 0.0))


def _sponge_armature_root_pose_bones(arm_obj):
    """Return the exact sponge armature root pose bone.

    Sponge rigs use a real deform/control bone named ``root`` above beat/root_sub.
    The SafePos visual offset replacement should target that bone specifically,
    not whichever bone happens to be parentless first.
    """
    try:
        pose = getattr(arm_obj, "pose", None)
        bones = getattr(pose, "bones", None)
        if bones is None:
            return []
        # Exact name first: this is the intended target described by the rig.
        try:
            pb = bones.get("root")
            if pb is not None:
                return [pb]
        except Exception:
            pass
        for pb in bones:
            try:
                if str(pb.name).casefold() == "root":
                    return [pb]
            except Exception:
                pass
        # Last-resort fallback only.  Avoid parts_* and prefer parentless bones.
        roots = []
        for pb in bones:
            try:
                name = str(pb.name).casefold()
                if name.startswith("parts_"):
                    continue
                if getattr(pb, "parent", None) is None:
                    roots.append(pb)
            except Exception:
                pass
        return roots[:1]
    except Exception:
        return []


def _sponge_collect_armatures(actor_root, imported_objects):
    """Find sponge armatures from the import batch, hierarchy, and mesh modifiers."""
    seen = set()
    out = []

    def add(obj):
        try:
            if obj is None or getattr(obj, "type", None) != "ARMATURE":
                return
            key = getattr(obj, "name", None) or id(obj)
            if key in seen:
                return
            seen.add(key)
            out.append(obj)
        except Exception:
            pass

    for obj in imported_objects or []:
        add(obj)
        try:
            for mod in getattr(obj, "modifiers", []) or []:
                if getattr(mod, "type", None) == "ARMATURE":
                    add(getattr(mod, "object", None))
        except Exception:
            pass
    try:
        stack = list(getattr(actor_root, "children_recursive", []) or [])
    except Exception:
        stack = []
    for obj in stack:
        add(obj)
        try:
            for mod in getattr(obj, "modifiers", []) or []:
                if getattr(mod, "type", None) == "ARMATURE":
                    add(getattr(mod, "object", None))
        except Exception:
            pass
    return out


def _apply_sponge_visual_offset_to_root_bones(actor_root, imported_objects, local_offset):
    """Apply sponge visual offset to armature pose bone ``root``.

    This mirrors the earlier working mesh-offset framework, but writes the
    resulting Blender-space visual offset to the armature bone named ``root``
    instead of to mesh vertex data.  The value is snapped to 0.5-unit increments
    to remove noisy BFRES bbox fractions while keeping the direction derived
    from SafePos locators and actor rotation.
    """
    applied = []
    if actor_root is None:
        return applied
    try:
        desired_world_vec = actor_root.matrix_world.to_3x3() @ Vector(local_offset)
        desired_world_vec = _snap_sponge_offset_half_units(desired_world_vec)
    except Exception:
        return applied
    for obj in _sponge_collect_armatures(actor_root, imported_objects):
        try:
            roots = _sponge_armature_root_pose_bones(obj)
            if not roots:
                continue
            # Earlier mesh-offset placement converted the Blender-space offset
            # through each target object's local axes before applying it.  Use
            # the same relationship for the armature root bone; for unrotated
            # sponges this matches Blender XYZ directly, while rotated sponges
            # keep their horizontal wall/corner direction.
            blender_local_delta = obj.matrix_world.inverted().to_3x3() @ desired_world_vec
            blender_local_delta = _snap_sponge_offset_half_units(blender_local_delta)
            pose_delta = _sponge_blender_vector_to_pose_vector(blender_local_delta)
            pose_delta = _snap_sponge_offset_half_units(pose_delta)
            for pb in roots:
                if str(pb.name) != "root":
                    continue
                before = Vector(pb.location)
                pb.location = before + pose_delta
                try:
                    pb.matrix_basis.translation = pb.location
                except Exception:
                    pass
                pb["bcett_sponge_root_bone_visual_offset"] = _serializable_vector3(pose_delta)
                pb["bcett_sponge_root_bone_visual_offset_world"] = _serializable_vector3(desired_world_vec)
                pb["bcett_sponge_root_bone_visual_offset_blender_local"] = _serializable_vector3(blender_local_delta)
                pb["bcett_sponge_root_bone_base_location"] = _serializable_vector3(pb.location)
                pb["bcett_sponge_root_bone_offset_space"] = "pose_xyz_from_blender_xzy_neg_y"
                applied.append((getattr(obj, "name", "<armature>"), str(pb.name), tuple(round(float(v), 6) for v in pose_delta), tuple(round(float(v), 6) for v in before), tuple(round(float(v), 6) for v in pb.location), tuple(round(float(v), 6) for v in desired_world_vec)))
            if applied:
                obj["bcett_sponge_visual_offset_root_bone_applied"] = True
                obj["bcett_sponge_visual_offset_root_bone_name"] = "root"
                obj["bcett_sponge_visual_offset_root_bone_space"] = "pose_xyz_from_blender_xzy_neg_y"
                obj.update_tag(refresh={'OBJECT', 'DATA'})
        except Exception:
            pass
    try:
        actor_root.update_tag(refresh={'OBJECT', 'DATA'})
    except Exception:
        pass
    return applied


def _apply_sponge_body_origin_offset_mesh_setup(actor_root, imported_objects, local_offset):
    """Prepare body meshes so object-scale inflation pivots at the sponge root.

    This is intentionally body-only.  The old shared mesh-offset/unbake path is
    gone: parts stay skinned to the armature root/parts bones, while body mesh
    vertices receive a local-space zero-state offset so the body object's origin
    remains the growth anchor.  Therefore ordinary object scale expands away
    from the floor/wall/corner instead of through it.
    """
    applied = []
    if actor_root is None:
        return applied
    try:
        # Convert actor-local SafePos offset into a Blender world vector, then
        # into each body mesh object's own local coordinates.  Applying actor
        # local_offset directly to vertices is only correct for unrotated bodies;
        # this keeps rotated wall/corner sponges consistent too.
        desired_world = actor_root.matrix_world.to_3x3() @ Vector(local_offset)
    except Exception:
        return applied

    for obj in imported_objects or []:
        try:
            if getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
                continue
            combo = _sponge_mesh_kind_name(obj)
            if "body" not in combo or "parts" in combo:
                continue

            mesh = obj.data
            marker = "bcett_sponge_body_origin_offset_baked_local"
            target_local = obj.matrix_world.inverted_safe().to_3x3() @ desired_world

            old = obj.get(marker, None)
            if old is not None and len(old) >= 3:
                old_vec = Vector((float(old[0]), float(old[1]), float(old[2])))
                delta = target_local - old_vec
            else:
                delta = target_local

            verts_changed = 0
            if abs(delta.x) > 1e-8 or abs(delta.y) > 1e-8 or abs(delta.z) > 1e-8:
                for vert in mesh.vertices:
                    vert.co = vert.co + delta
                    verts_changed += 1
                try:
                    mesh.update()
                except Exception:
                    pass

            removed_mods = []
            for mod in list(getattr(obj, "modifiers", []) or []):
                try:
                    if getattr(mod, "type", None) == "ARMATURE":
                        removed_mods.append(getattr(mod, "name", "<armature>"))
                        obj.modifiers.remove(mod)
                except Exception:
                    pass

            obj[marker] = _serializable_vector3(target_local)
            obj["bcett_sponge_body_origin_offset_baked_world"] = _serializable_vector3(desired_world)
            obj["bcett_sponge_body_origin_offset_baked_actor_local"] = _serializable_vector3(local_offset)
            obj["bcett_sponge_body_origin_offset_mesh_setup"] = True
            obj["bcett_sponge_body_origin_offset_removed_armature_modifiers"] = ",".join(removed_mods)
            obj["bcett_sponge_body_inflate_mode"] = "object_scale_body_vertices_offset_from_root_origin"
            obj["bcett_sponge_base_scale"] = (float(obj.scale.x), float(obj.scale.y), float(obj.scale.z))
            applied.append((
                obj.name,
                "body_vertex_offset",
                tuple(round(float(v), 6) for v in target_local),
                tuple(round(float(v), 6) for v in desired_world),
                verts_changed,
                removed_mods,
            ))
        except Exception as exc:
            applied.append((getattr(obj, "name", "<mesh>"), "failed", repr(exc)))
    return applied

def _apply_sponge_safepos_bbox_correction(entry, actor_root, imported_objects, actor_hash_index, report_lines, actor_label):
    """Offset sponge visual roots from the game actor origin using SafePos directions.

    Sponge actor origins appear to be the correct growth/scale anchor.  The linked
    SafePosLinks/GeneralLocator actors describe the directions in which the sponge
    can expand: corner sponges usually have two links, wall sponges three, and open
    sponges four.  Instead of moving the actor root, keep the root at the game
    origin and offset the imported BFRES visual roots in actor-local space.

    Each linked locator contributes one equal-weight direction.  Horizontal offset
    is the average of the per-axis direction signs times the mesh half extents;
    the vertical offset remains the imported mesh half-height so the not-inflated
    default mesh sits above the growth origin.  This preserves the already-correct
    corner placement while avoiding the old corner-style offset for wall/open
    sponges.
    """
    if not _is_sponge_actor(entry):
        return False

    refs = _sponge_safepos_refs(entry)
    if not refs:
        report_lines.append("SPONGE_SAFEPOS normal actor=%r reason=no_SafePosLinks" % actor_label)
        return False

    anchors = []
    unresolved = []
    for ref in refs:
        anchor = actor_hash_index.get(ref)
        if not anchor:
            unresolved.append(ref)
            continue
        if anchor.get("translate") is None:
            unresolved.append(ref)
            continue
        anchors.append((ref, anchor))

    if not anchors:
        report_lines.append(
            "SPONGE_SAFEPOS normal actor=%r reason=no_resolved_locator refs=%s" % (actor_label, refs)
        )
        return False

    bbox_min, bbox_max = _mesh_bbox_in_root_space(imported_objects, actor_root)
    if bbox_min is None:
        report_lines.append(
            "SPONGE_SAFEPOS normal actor=%r reason=no_mesh_bbox resolved_refs=%s" % (actor_label, [ref for ref, _ in anchors])
        )
        return False

    half = (bbox_max - bbox_min) * 0.5
    rot_inv = actor_root.rotation_euler.to_matrix().inverted()
    root_location = Vector(actor_root.location)

    local_offsets = []
    axis_signs = {"x": [], "y": []}
    axis_vote_sums = {"x": 0.0, "y": 0.0}
    direction_votes = []
    z_sign_sum = 0.0
    contributing = 0
    local_vectors = []
    for ref, anchor in anchors:
        loc = Vector(_vector_from_any(anchor.get("translate"), 0.0, splatoon_axis_order=True))
        local = rot_inv @ (loc - root_location)
        local_offsets.append((ref, anchor.get("label"), tuple(round(v, 6) for v in local)))
        if local.length <= 1e-6:
            continue
        local_vectors.append(local)
        sx = _sign_from_value(local.x)
        sy = _sign_from_value(local.y)
        sz = _sign_from_value(local.z)
        if sx:
            axis_signs["x"].append(sx)
            axis_vote_sums["x"] += sx
        if sy:
            axis_signs["y"].append(sy)
            axis_vote_sums["y"] += sy
        direction_votes.append((sx, sy, sz))
        z_sign_sum += sz
        contributing += 1

    def _sign_from_total(total, epsilon=1e-5):
        total = float(total)
        if total > epsilon:
            return 1.0
        if total < -epsilon:
            return -1.0
        return 0.0

    # The correction should move the default visual mesh toward the center of the
    # locator pattern.  For two-link corner sponges, sign voting can fail when
    # the two locators are diagonal but each has a small opposite component after
    # rotation/conversion; use the average locator vector directly.  For three
    # links, normal sign cancellation is desired: the paired wall axis cancels
    # and the remaining side survives.  Four-link floor sponges cancel both axes.
    if len(local_vectors) == 2:
        center = Vector((0.0, 0.0, 0.0))
        for vec in local_vectors:
            center += vec
        center /= 2.0
        avg_x = _sign_from_total(center.x)
        avg_y = _sign_from_total(center.y)
        classification = "corner_2_locator_center_sign"
    else:
        avg_x = _sign_from_total(axis_vote_sums.get("x", 0.0))
        avg_y = _sign_from_total(axis_vote_sums.get("y", 0.0))
        classification = "wall_or_floor_sign_cancellation"

    avg_sign = Vector((
        avg_x,
        avg_y,
        (z_sign_sum / float(contributing)) if contributing else 0.0,
    ))

    raw_local_offset = Vector((avg_sign.x * abs(half.x), avg_sign.y * abs(half.y), abs(half.z)))
    local_offset = _snap_sponge_offset_half_units(raw_local_offset)

    # Apply the stabilized sponge offset model:
    #   * parts/armature placement comes from the armature pose bone named root;
    #   * body meshes receive a body-only vertex offset so object scale uses the
    #     actor origin as the inflate pivot.
    body_origin_offsets = _apply_sponge_body_origin_offset_mesh_setup(actor_root, imported_objects, local_offset)
    visual_roots = _apply_sponge_visual_offset_to_root_bones(actor_root, imported_objects, local_offset)

    scaled_offset = Vector((local_offset.x * actor_root.scale.x, local_offset.y * actor_root.scale.y, local_offset.z * actor_root.scale.z))
    world_offset = actor_root.rotation_euler.to_matrix() @ scaled_offset
    try:
        actor_root["bcett_sponge_offset_method"] = "SafePosRootBonePartsBodyVertexPivot"
        actor_root["bcett_sponge_offset_local"] = _serializable_vector3(local_offset)
        actor_root["bcett_sponge_offset_world"] = _serializable_vector3(world_offset)
        actor_root["bcett_sponge_bbox_half"] = _serializable_vector3(half)
        actor_root["bcett_sponge_origin_location"] = _serializable_vector3(actor_root.location)
        actor_root["bcett_sponge_safepos_direction_average"] = _serializable_vector3(avg_sign)
        actor_root["bcett_sponge_direction_votes"] = repr(direction_votes)
        actor_root["bcett_sponge_axis_vote_sums"] = repr(axis_vote_sums)
        for child_obj in imported_objects or []:
            child_obj["bcett_sponge_offset_method"] = "SafePosRootBonePartsBodyVertexPivot"
            child_obj["bcett_sponge_offset_local"] = _serializable_vector3(local_offset)
            child_obj["bcett_sponge_offset_world"] = _serializable_vector3(world_offset)
    except Exception:
        pass

    try:
        # Mesh vertex data is no longer offset, so raw mesh bounds do not include
        # the armature root-bone visual offset.  Use the SafePos offset as the
        # visible body center for inflate corner travel; the half extents still
        # come from the unmodified body mesh bounds.
        body_center, body_half = local_offset, half
        max_multiplier = _sponge_inflate_max_multiplier_for_entry(entry)
        _setup_sponge_inflate_controls(actor_root, imported_objects, body_half, local_offset, body_center, max_multiplier=max_multiplier)
        _install_sponge_inflate_drivers(actor_root, imported_objects, body_half, report_lines, actor_label, body_center)
    except Exception:
        pass

    report_lines.append(
        "SPONGE_SAFEPOS applied actor=%r refs=%s unresolved=%s local_offsets=%s axis_signs=%s axis_vote_sums=%s classification=%s bbox_half=%s avg_direction=%s visual_local_offset=%s raw_visual_local_offset=%s visual_world_offset=%s origin_loc=%s body_origin_offsets=%s visual_roots=%s"
        % (
            actor_label,
            [(ref, anchor.get("label")) for ref, anchor in anchors],
            unresolved,
            local_offsets,
            axis_signs,
            axis_vote_sums,
            classification,
            tuple(round(v, 6) for v in half),
            tuple(round(v, 6) for v in avg_sign),
            tuple(round(v, 6) for v in local_offset),
            tuple(round(v, 6) for v in raw_local_offset),
            tuple(round(float(v), 6) for v in world_offset),
            tuple(round(float(v), 6) for v in actor_root.location),
            body_origin_offsets,
            visual_roots,
        )
    )
    return True




def _sponge_mesh_kind_name(obj):
    try:
        return (getattr(obj, "name", "") + " " + getattr(getattr(obj, "data", None), "name", "") + " " + str(obj.get("bcett_sponge_mesh_kind", ""))).casefold()
    except Exception:
        return ""


def _compute_sponge_body_bounds_local(actor_root, imported_objects):
    """Return visible body-mesh bounds in actor-root local space after mesh offset."""
    if actor_root is None:
        return None
    mins = [None, None, None]
    maxs = [None, None, None]
    try:
        root_inv = actor_root.matrix_world.inverted()
    except Exception:
        return None
    found = False
    for obj in imported_objects or []:
        try:
            if getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
                continue
            combo = _sponge_mesh_kind_name(obj)
            if "parts" in combo:
                continue
            if "body" not in combo and "sponge" not in combo:
                continue
            mw = obj.matrix_world
            for vert in obj.data.vertices:
                v = root_inv @ (mw @ vert.co)
                vals = (float(v.x), float(v.y), float(v.z))
                for i, val in enumerate(vals):
                    mins[i] = val if mins[i] is None else min(mins[i], val)
                    maxs[i] = val if maxs[i] is None else max(maxs[i], val)
                found = True
        except Exception:
            pass
    if not found:
        return None
    center = Vector(((mins[0] + maxs[0]) * 0.5, (mins[1] + maxs[1]) * 0.5, (mins[2] + maxs[2]) * 0.5))
    half = Vector(((maxs[0] - mins[0]) * 0.5, (maxs[1] - mins[1]) * 0.5, (maxs[2] - mins[2]) * 0.5))
    return center, half


def _sponge_pose_bone_rest_root_local(actor_root, arm_obj, pb):
    try:
        # Use the rest bone head/tail midpoint rather than pb.location.  The
        # pose location is usually a zero delta, while the rest midpoint tells us
        # which cube corner/side this parts_* controller belongs to.
        rest = (pb.bone.head_local + pb.bone.tail_local) * 0.5
    except Exception:
        try:
            rest = Vector(pb.bone.head_local)
        except Exception:
            rest = Vector((0.0, 0.0, 0.0))
    try:
        return actor_root.matrix_world.inverted() @ (arm_obj.matrix_world @ rest)
    except Exception:
        return rest


def _signs_from_rest_position(rest_pos, center, fallback_name=None):
    # For sponge parts bones, the BFRES names are more reliable than inferred
    # rest positions because the visible sponge mesh is offset from the growth
    # origin while the armature display remains near the origin.  The observed
    # cube naming is: L/R = X faces, final B/F = +Y/-Y faces, middle B/T =
    # bottom/top Z faces.  Use the rest position only as a fallback.
    try:
        if str(fallback_name or "").casefold().startswith("parts_"):
            nx, ny, nz = _sponge_parts_bone_signs(fallback_name or "", rest_pos - center)
            return Vector((nx, ny, nz))
    except Exception:
        pass
    def sg(v):
        try:
            if float(v) > 0.00001:
                return 1.0
            if float(v) < -0.00001:
                return -1.0
        except Exception:
            pass
        return 0.0
    signs = Vector((sg(rest_pos.x - center.x), sg(rest_pos.y - center.y), sg(rest_pos.z - center.z)))
    if signs.x == 0.0 or signs.y == 0.0 or signs.z == 0.0:
        try:
            nx, ny, nz = _sponge_parts_bone_signs(fallback_name or "", rest_pos - center)
            if signs.x == 0.0: signs.x = nx
            if signs.y == 0.0: signs.y = ny
            if signs.z == 0.0: signs.z = nz
        except Exception:
            pass
    return signs


def _driver_target_prop_path(prop_name):
    return '["%s"]' % str(prop_name).replace('"', '\\"')


def _sponge_parts_bone_signs(name, base_location=None):
    """Return approximate corner signs for a sponge parts_* pose bone.

    This is only a fallback.  Sponge inflation now primarily chooses the target
    side from the visible body bounds.  The BFRES labels as observed in Blender
    are:
      L/R -> low/high X side
      middle B/T -> low/high Z side
      final B/F -> high/low Y side
    """
    lname = str(name or "").casefold()
    tail = lname.split("parts_", 1)[-1]
    sx = -1.0 if tail.startswith("l") else (1.0 if tail.startswith("r") else 0.0)
    sy = 0.0
    sz = 0.0
    if len(tail) >= 3:
        sy = 1.0 if tail[2] == "b" else (-1.0 if tail[2] == "f" else 0.0)
        sz = 1.0 if tail[1] == "t" else (-1.0 if tail[1] == "b" else 0.0)
    if base_location is not None:
        try:
            if sx == 0.0:
                sx = _sign_from_value(float(base_location.x))
            if sy == 0.0:
                sy = _sign_from_value(float(base_location.y))
            if sz == 0.0:
                sz = _sign_from_value(float(base_location.z))
        except Exception:
            pass
    return sx, sy, sz




def _sponge_axis_low_high_with_anchors(center_value, half_value):
    """Return low/high travel coordinates with one-sided anchors handled.

    Sponge body meshes are visually offset away from contacted surfaces.  If one
    bound of an axis is effectively on the sponge origin plane, that side should
    collapse to zero travel and the opposite side should travel the full span.
    This is intentionally applied to all axes, including Z, so floor/ceiling
    aligned sponges expand upward/downward by the doubled one-sided distance.
    """
    try:
        half_abs = abs(float(half_value))
        low = float(center_value) - half_abs
        high = float(center_value) + half_abs
    except Exception:
        return 0.0, 0.0
    span = abs(high - low)
    if span <= 1e-8:
        return 0.0, 0.0
    # BFRES sponge bounds are not numerically exact after conversion.  Values
    # that are small compared with the sponge side length are treated as the
    # contacted/anchored plane.
    tol = max(1e-5, span * 0.125)
    if abs(low) <= tol and abs(high) > tol:
        return 0.0, span
    if abs(high) <= tol and abs(low) > tol:
        return -span, 0.0
    if abs(low) <= tol and abs(high) <= tol:
        return 0.0, 0.0
    return low, high


def _sponge_parts_bone_target_from_bounds(name, body_center, half):
    """Pick the visible-body side/corner coordinate controlled by a parts_* bone.

    The target coordinate is in sponge-root local space and is used as the scale
    delta when the body mesh scales about the root.  Collapsed/anchored sides
    use zero travel, and the opposite side uses the full side span, not just the
    symmetric half extent.
    """
    tail = str(name or "").casefold().split("parts_", 1)[-1]
    low_x, high_x = _sponge_axis_low_high_with_anchors(body_center.x, half.x)
    low_y, high_y = _sponge_axis_low_high_with_anchors(body_center.y, half.y)
    low_z, high_z = _sponge_axis_low_high_with_anchors(body_center.z, half.z)
    # Corrected unrotated Blender-local mapping:
    # 1st char L/R -> low/high X; 2nd char B/T -> low/high Z;
    # 3rd char B/F -> high/low Y.  These are root-local coordinates; actor
    # rotation is applied by Blender through the sponge root.
    x = low_x if tail.startswith("l") else (high_x if tail.startswith("r") else (high_x if abs(high_x) >= abs(low_x) else low_x))
    z = low_z if len(tail) >= 2 and tail[1] == "b" else (high_z if len(tail) >= 2 and tail[1] == "t" else (high_z if abs(high_z) >= abs(low_z) else low_z))
    y = high_y if len(tail) >= 3 and tail[2] == "b" else (low_y if len(tail) >= 3 and tail[2] == "f" else (high_y if abs(high_y) >= abs(low_y) else low_y))
    return Vector((x, y, z))

def _add_single_prop_driver(id_owner, data_path, index, root, prop_name, expression):
    try:
        # Remove old driver on the same target so reinstalling the add-on or
        # re-importing does not stack duplicate drivers.
        try:
            id_owner.driver_remove(data_path, index)
        except Exception:
            pass
        fcurve = id_owner.driver_add(data_path, index)
        drv = fcurve.driver
        drv.type = 'SCRIPTED'
        while drv.variables:
            drv.variables.remove(drv.variables[0])
        var = drv.variables.new()
        var.name = 'infl'
        var.type = 'SINGLE_PROP'
        var.targets[0].id = root
        var.targets[0].data_path = _driver_target_prop_path(prop_name)
        drv.expression = expression
        return True
    except Exception:
        return False


def _remove_sponge_inflate_drivers(id_owner, data_path, indices):
    """Remove old driver-based sponge inflate targets from an ID owner."""
    removed = 0
    for idx in indices:
        try:
            id_owner.driver_remove(data_path, idx)
            removed += 1
        except Exception:
            pass
    return removed


def _install_sponge_inflate_drivers(actor_root, imported_objects, bbox_half, report_lines=None, actor_label=None, body_center=None):
    """Prepare direct-update sponge inflate metadata and remove old drivers.

    This function intentionally keeps its historical name because the importer
    calls it from the sponge finalization path, but v0.8.57 no longer installs
    Blender drivers.  Drivers made it hard to verify whether new code was active
    and could leave stale expressions across .blend reloads.  Instead, the live
    watcher in team_colors.py directly updates body scale and parts_* pose bones.
    """
    if actor_root is None:
        return 0
    prop_name = "spl_sponge_inflate"
    removed_drivers = 0
    metadata_count = 0
    try:
        actor_root[prop_name] = float(actor_root.get(prop_name, 0.0) or 0.0)
        actor_root.id_properties_ui(prop_name).update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description="Approximate sponge inflation: body scales, parts bones follow corners")
    except Exception:
        pass
    half = Vector((float(bbox_half.x), float(bbox_half.y), float(bbox_half.z)))
    try:
        if body_center is None:
            cv = actor_root.get("bcett_sponge_body_center_local", actor_root.get("bcett_sponge_visual_offset_local", actor_root.get("bcett_sponge_offset_local", (0.0, 0.0, 0.0))))
            body_center = Vector((float(cv[0]), float(cv[1]), float(cv[2])))
        else:
            body_center = Vector((float(body_center.x), float(body_center.y), float(body_center.z)))
    except Exception:
        body_center = Vector((0.0, 0.0, 0.0))
    try:
        vo = actor_root.get("bcett_sponge_visual_offset_local", actor_root.get("bcett_sponge_offset_local", (0.0, 0.0, 0.0)))
        visual_offset = Vector((float(vo[0]), float(vo[1]), float(vo[2])))
    except Exception:
        visual_offset = Vector((0.0, 0.0, 0.0))
    max_mult = 2.0
    bone_lines = []
    for obj in imported_objects or []:
        try:
            if getattr(obj, "type", None) == "MESH":
                combo = (obj.name + " " + getattr(getattr(obj, "data", None), "name", "") + " " + str(obj.get("bcett_sponge_mesh_kind", ""))).casefold()
                removed_drivers += _remove_sponge_inflate_drivers(obj, "scale", (0, 1, 2))
                if "parts" in combo:
                    obj["bcett_sponge_base_scale"] = _serializable_vector3(obj.scale)
                    metadata_count += 1
                    continue
                if "body" in combo or "sponge" in combo:
                    base = Vector((float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)))
                    obj["bcett_sponge_base_scale"] = _serializable_vector3(base)
                    metadata_count += 1
            elif getattr(obj, "type", None) == "ARMATURE":
                for pb in obj.pose.bones:
                    name = str(pb.name)
                    if not name.startswith("parts_"):
                        continue
                    removed_drivers += _remove_sponge_inflate_drivers(pb, "location", (0, 1, 2))
                    # Keep the visible imported zero state authoritative.
                    # The sponge visual mesh offset is already baked into the body/parts
                    # mesh vertex data before inflate metadata is installed.  Physically
                    # translating pose bones here applies a second skinning transform to
                    # the parts mesh and misaligns the imported zero state.  Store the
                    # current imported pose as the runtime base; spl_sponge_inflate then
                    # adds expansion travel from that already-correct pose.
                    zero_pose = Vector(pb.location)
                    base = Vector(pb.location)
                    rest_pos = _sponge_pose_bone_rest_root_local(actor_root, obj, pb)
                    signs_v = _signs_from_rest_position(rest_pos, body_center, name)
                    corner = _sponge_parts_bone_target_from_bounds(name, body_center, half)
                    pb["bcett_sponge_base_location"] = _serializable_vector3(base)
                    pb["bcett_sponge_zero_pose_import_aligned"] = False
                    pb["bcett_sponge_zero_pose_offset"] = _serializable_vector3(zero_pose)
                    pb["bcett_sponge_rest_root_local"] = _serializable_vector3(rest_pos)
                    pb["bcett_sponge_corner_root_local"] = _serializable_vector3(corner)
                    pb["bcett_sponge_signs"] = _serializable_vector3(signs_v)
                    pb["bcett_sponge_direct_inflate"] = True
                    metadata_count += 1
                    if len(bone_lines) < 64:
                        bone_lines.append((name, base, rest_pos, corner, signs_v))
        except Exception:
            pass
    if report_lines is not None:
        report_lines.append("SPONGE_INFLATE_SETUP actor=%r root=%r mode='direct_update' metadata=%d removed_drivers=%d bbox_half=%s body_center=%s body_min=%s body_max=%s visual_offset=%s max_multiplier=%s" % (
            actor_label,
            getattr(actor_root, "name", "<none>"),
            metadata_count,
            removed_drivers,
            tuple(round(float(v), 6) for v in half),
            tuple(round(float(v), 6) for v in body_center),
            tuple(round(float(v), 6) for v in (body_center - Vector((abs(half.x), abs(half.y), abs(half.z))))),
            tuple(round(float(v), 6) for v in (body_center + Vector((abs(half.x), abs(half.y), abs(half.z))))),
            tuple(round(float(v), 6) for v in visual_offset),
            max_mult,
        ))
        for name, base, rest_pos, corner, signs_v in bone_lines:
            report_lines.append("SPONGE_INFLATE_BONE actor=%r root=%r bone=%r base_pose=%s rest_root=%s target_delta=%s signs=%s space='pose_location_direct'" % (
                actor_label,
                getattr(actor_root, "name", "<none>"),
                name,
                tuple(round(float(v), 6) for v in base),
                tuple(round(float(v), 6) for v in rest_pos),
                tuple(round(float(v), 6) for v in corner),
                tuple(round(float(v), 6) for v in signs_v),
            ))
    return metadata_count

def _setup_sponge_inflate_controls(actor_root, imported_objects, bbox_half, visual_offset, body_center=None, max_multiplier=2.0):
    """Tag sponge hierarchy with metadata used by the live inflate watcher.

    spl_sponge_inflate intentionally remains an ID custom property, not an
    Object RNA property.  Registering an RNA property with the same name hides
    the editable Custom Properties entry in Blender's Object Properties UI.
    The live updater polls this ID property, matching the workflow used by
    spl_active_team.
    """
    if actor_root is None:
        return

    def _ensure_inflate_idprop(obj, value=0.0):
        try:
            obj["spl_sponge_inflate"] = float(obj.get("spl_sponge_inflate", value) or value)
            try:
                obj.id_properties_ui("spl_sponge_inflate").update(
                    min=0.0, max=1.0, soft_min=0.0, soft_max=1.0,
                    description="Approximate sponge inflation; 0 is default, 1 is fully inflated"
                )
            except Exception:
                pass
        except Exception:
            pass

    try:
        _ensure_inflate_idprop(actor_root, 0.0)
        actor_root["bcett_sponge_inflate_enabled"] = True
        actor_root["bcett_sponge_bbox_half"] = _serializable_vector3(bbox_half)
        actor_root["bcett_sponge_visual_offset_local"] = _serializable_vector3(visual_offset)
        if body_center is not None:
            actor_root["bcett_sponge_body_center_local"] = _serializable_vector3(body_center)
            actor_root["bcett_sponge_body_min_local"] = _serializable_vector3(Vector((body_center.x - abs(bbox_half.x), body_center.y - abs(bbox_half.y), body_center.z - abs(bbox_half.z))))
            actor_root["bcett_sponge_body_max_local"] = _serializable_vector3(Vector((body_center.x + abs(bbox_half.x), body_center.y + abs(bbox_half.y), body_center.z + abs(bbox_half.z))))
        actor_root["bcett_sponge_inflate_max_multiplier"] = float(max_multiplier)
    except Exception:
        pass
    for obj in imported_objects or []:
        try:
            obj["bcett_sponge_root"] = actor_root.name
            _ensure_inflate_idprop(obj, float(actor_root.get("spl_sponge_inflate", 0.0) or 0.0))
            if getattr(obj, "type", None) == "MESH":
                lname = obj.name.casefold()
                dname = getattr(getattr(obj, "data", None), "name", "").casefold()
                combo = lname + " " + dname
                if "parts" in combo:
                    obj["bcett_sponge_mesh_kind"] = "parts"
                elif "body" in combo or "sponge" in combo:
                    obj["bcett_sponge_mesh_kind"] = "body"
                if "bcett_sponge_base_scale" not in obj:
                    obj["bcett_sponge_base_scale"] = _serializable_vector3(obj.scale)
            elif getattr(obj, "type", None) == "ARMATURE":
                obj["bcett_sponge_armature"] = True
                try:
                    for pb in obj.pose.bones:
                        if str(pb.name).startswith("parts_"):
                            pb["bcett_sponge_base_location"] = _serializable_vector3(pb.location)
                            pb["bcett_sponge_visual_offset_local"] = _serializable_vector3(visual_offset)
                except Exception:
                    pass
        except Exception:
            pass
    # Ensure the live sponge/team custom-property watcher is running after an
    # import, even if Blender did not restart the add-on registration hooks.
    try:
        from . import team_colors as _osi_team_colors
        _osi_team_colors._install_active_team_handler()
    except Exception:
        pass


def _top_level_import_roots(objects):
    """Return imported objects whose parent is outside this import batch.

    BFRES imports may create meshes, armatures, empties, and nested children.
    Stage transforms must be applied once to the actor root, not to each child,
    otherwise child mesh offsets get rotated/translated a second time.
    """
    object_set = set(objects)
    roots = [obj for obj in objects if obj.parent not in object_set]
    return roots or list(objects)


def _object_transform_summary(objects, limit=4):
    rows = []
    for obj in _top_level_import_roots(objects)[:limit]:
        try:
            rot = tuple(round(math.degrees(v), 3) for v in obj.rotation_euler)
        except Exception:
            rot = ()
        try:
            loc = tuple(round(float(v), 3) for v in obj.location)
            scale = tuple(round(float(v), 3) for v in obj.scale)
        except Exception:
            loc = ()
            scale = ()
        rows.append("%s:loc=%s rot_deg=%s scale=%s" % (obj.name, loc, rot, scale))
    if len(_top_level_import_roots(objects)) > limit:
        rows.append("...+%d more roots" % (len(_top_level_import_roots(objects)) - limit))
    return "; ".join(rows) or "none"


def _actor_root_name(entry, fallback_name):
    if isinstance(entry, dict):
        for key in ("Name", "Gyaml", "__RowId"):
            value = entry.get(key)
            if value not in (None, ""):
                return _safe_collection_name(value, fallback_name)
    return _safe_collection_name(fallback_name, "Actor")


def _create_actor_root(entry, fallback_name, location, rotation_euler, scale):
    """Create one Blender root empty for a stage actor transform."""
    root = bpy.data.objects.new(_actor_root_name(entry, fallback_name), None)
    bpy.context.scene.collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.5
    root.location = location
    root.rotation_euler = rotation_euler
    root.scale = scale
    return root


def _serializable_vector3(value):
    """Return a plain [x, y, z] list for Blender custom properties."""
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except Exception:
        return [0.0, 0.0, 0.0]



def _serializable_matrix4(value):
    """Return a plain row-major 4x4 matrix list for Blender custom properties."""
    try:
        return [float(value[r][c]) for r in range(4) for c in range(4)]
    except Exception:
        return [1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0]

def _raw_vector_property(entry, key, default=0.0):
    """Return the raw BYML vector without Splatoon axis conversion."""
    if not isinstance(entry, dict):
        return [default, default, default]
    return _serializable_vector3(_vector_from_any(entry.get(key, {}), default, splatoon_axis_order=False))



def _team_metadata_value_to_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _collect_bcett_team_metadata(entry):
    """Collect TeamCmp/team metadata from an actor entry for later team-color linking.

    Splatoon 3 BCETT actor data can store team selection in nested components,
    typically through names like TeamCmp and keys such as Team, Neutral, Alpha,
    Bravo, Charlie, etc.  The exact schema differs by actor, so this deliberately
    records a compact path/value map instead of trying to resolve TeamColorDataSet
    immediately.
    """
    if not isinstance(entry, dict):
        return {}
    team_words = ("teamcmp", "team_cmp", "team", "neutral", "alpha", "bravo", "charlie", "delta", "foxtrot")
    paths = {}
    team_values = []
    component_paths = []

    def is_scalar(v):
        return isinstance(v, (str, int, float, bool)) or v is None

    def walk(value, path, in_team_context=False, depth=0):
        if depth > 8:
            return
        key_name = path[-1] if path else ""
        key_l = str(key_name).casefold()
        now_team_context = in_team_context or any(word in key_l for word in team_words)
        if now_team_context and path:
            joined = ".".join(str(p) for p in path)
            if joined not in component_paths:
                component_paths.append(joined)
        if isinstance(value, dict):
            for k, v in value.items():
                child_path = path + [k]
                child_key_l = str(k).casefold()
                child_context = now_team_context or any(word in child_key_l for word in team_words)
                if child_context and is_scalar(v):
                    joined = ".".join(str(p) for p in child_path)
                    text = _team_metadata_value_to_text(v)
                    paths[joined] = text
                    if ("team" in child_key_l or text.casefold() in ("neutral", "alpha", "bravo", "charlie", "delta", "foxtrot")) and text:
                        if text not in team_values:
                            team_values.append(text)
                walk(v, child_path, child_context, depth + 1)
        elif isinstance(value, list):
            for i, v in enumerate(value[:128]):
                walk(v, path + [i], now_team_context, depth + 1)
        elif now_team_context and path and is_scalar(value):
            joined = ".".join(str(p) for p in path)
            text = _team_metadata_value_to_text(value)
            paths[joined] = text
            if text and text not in team_values:
                team_values.append(text)

    walk(entry, [], False, 0)
    # Prefer explicit TeamCmp subtree if present, but keep the path map generic.
    explicit_teamcmp = [p for p in paths if "teamcmp" in p.casefold() or "team_cmp" in p.casefold()]
    result = {}
    if paths:
        result["paths"] = paths
        result["team_values"] = team_values[:32]
        result["component_paths"] = component_paths[:64]
        result["teamcmp_paths"] = explicit_teamcmp[:64]
    return result




def _extract_primary_bcett_team(entry):
    """Return the explicit TeamCmp.Team value when present, else a best-effort role."""
    if not isinstance(entry, dict):
        return "neutral"
    candidates = []
    def add(v):
        if v not in (None, ""):
            text = str(v)
            if text not in candidates:
                candidates.append(text)
    def walk(value, path=(), depth=0):
        if depth > 8:
            return
        if isinstance(value, dict):
            # Exact/preferred TeamCmp.Team-style paths first.
            for k, v in value.items():
                key_l = str(k).casefold()
                path_l = ".".join(str(p).casefold() for p in path + (k,))
                if key_l == "team" and ("teamcmp" in path_l or "team_cmp" in path_l):
                    add(v)
            for k, v in value.items():
                walk(v, path + (k,), depth + 1)
        elif isinstance(value, list):
            for i, v in enumerate(value[:128]):
                walk(v, path + (i,), depth + 1)
    walk(entry)
    if not candidates:
        meta = _collect_bcett_team_metadata(entry)
        for v in meta.get("team_values", []) if isinstance(meta, dict) else []:
            add(v)
    if not candidates:
        return "neutral"
    raw = candidates[0]
    try:
        from . import team_colors as _osi_team_colors
        return _osi_team_colors.normalize_team_role(raw)
    except Exception:
        return str(raw).strip().casefold() or "neutral"

def _set_bcett_team_props(obj, entry):
    if obj is None:
        return
    meta = _collect_bcett_team_metadata(entry)
    try:
        team_role = _extract_primary_bcett_team(entry)
        obj["spl_active_team"] = team_role
        obj["bcett_team"] = team_role
        for k in ("spl_team", "osi_team", "osi_active_team"):
            try:
                if k in obj:
                    del obj[k]
            except Exception:
                pass
        obj["bcett_team_metadata"] = _idprop_safe(meta)
        obj["bcett_team_paths"] = _idprop_safe(meta.get("paths", {}))
        obj["bcett_team_values"] = _idprop_safe(meta.get("team_values", []))
        obj["bcett_team_component_paths"] = _idprop_safe(meta.get("component_paths", []))
        obj["bcett_teamcmp_paths"] = _idprop_safe(meta.get("teamcmp_paths", []))
    except Exception:
        pass


def _set_bcett_transform_props(obj, entry, actor_label, location, rotation_euler, scale):
    """Store original/converted BCETT transform metadata on an object.

    Blender child mesh objects often remain local-zero under an actor root, and
    collection-instance sources are intentionally local.  These custom
    properties preserve the actor's stage transform on every imported object
    without relying on Blender's local transform UI.  Layer is deliberately not
    stored because the collection hierarchy already records it.
    """
    if obj is None:
        return
    try:
        obj["bcett_actor"] = str(actor_label)
        _set_bcett_team_props(obj, entry)
        obj["bcett_translate_raw"] = _raw_vector_property(entry, "Translate", 0.0)
        obj["bcett_rotate_raw"] = _raw_vector_property(entry, "Rotate", 0.0)
        obj["bcett_scale_raw"] = _raw_vector_property(entry, "Scale", 1.0)
        obj["bcett_translate_converted"] = _serializable_vector3(location)
        obj["bcett_rotate_converted_rad"] = _serializable_vector3(rotation_euler)
        obj["bcett_rotate_converted_deg"] = [math.degrees(float(v)) for v in rotation_euler]
        obj["bcett_scale_converted"] = _serializable_vector3(scale)
        srthash = _srthash_value(entry)
        if srthash is not None:
            obj["bcett_srthash"] = str(srthash)
        if isinstance(entry, dict):
            h = entry.get("Hash")
            if h not in (None, ""):
                obj["bcett_hash"] = str(h)
    except Exception:
        pass


def _set_bcett_transform_props_many(objects, entry, actor_label, location, rotation_euler, scale):
    for obj in objects or []:
        _set_bcett_transform_props(obj, entry, actor_label, location, rotation_euler, scale)
    try:
        from . import team_colors as _osi_team_colors
        _osi_team_colors.link_imported_object_team_colors(objects or [], _extract_primary_bcett_team(entry), bpy.context.scene)
    except Exception:
        pass


def _parent_roots_to_actor_root(imported_objects, actor_root):
    """Parent only top-level BFRES roots under actor_root.

    Do not touch child meshes individually. This preserves the BFRES local
    hierarchy while applying the BCETT/SMO actor transform exactly once.
    """
    for obj in _top_level_import_roots(imported_objects):
        obj.parent = actor_root
        obj.matrix_parent_inverse.identity()


def _osi_make_placeholder_material(name="OSI_TextureFallback"):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
    return mat


def _osi_image_datablock_valid(image):
    try:
        return image is not None and getattr(image, "name", None) in bpy.data.images and not bool(getattr(image, "is_missing", False))
    except ReferenceError:
        return False
    except Exception:
        return False


def _osi_material_has_invalid_images(mat):
    try:
        if mat is None or getattr(mat, "name", None) not in bpy.data.materials:
            return True
        if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            return False
        for node in mat.node_tree.nodes:
            if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
                img = getattr(node, "image", None)
                if img is not None and not _osi_image_datablock_valid(img):
                    return True
        return False
    except ReferenceError:
        return True
    except Exception:
        return False


def _osi_sanitize_mesh_material_slots(obj, report_lines=None, context="CACHE_DUPLICATE"):
    try:
        data = getattr(obj, "data", None)
        slots = getattr(data, "materials", None)
        if not slots:
            return
        fallback = None
        for idx, mat in enumerate(list(slots)):
            if _osi_material_has_invalid_images(mat):
                if fallback is None:
                    fallback = _osi_make_placeholder_material()
                old = getattr(mat, "name", "<removed>") if mat is not None else "<none>"
                slots[idx] = fallback
                _report_append(report_lines, "%s_MATERIAL_PLACEHOLDER object=%r slot=%d old_material=%r reason=invalid_or_stale_image_node" % (context, getattr(obj, "name", "<unnamed>"), idx, old))
    except Exception as exc:
        _report_append(report_lines, "%s_MATERIAL_SANITIZE_FAILED object=%r error=%s" % (context, getattr(obj, "name", "<unnamed>"), exc))


def _duplicate_linked_objects(template_objects, report_lines=None):
    """Create linked duplicates of an imported BFRES/FMDL object batch.

    Mesh/armature data can be shared, but material slots are sanitized on the
    duplicate path so cached template objects cannot propagate stale Blender
    Image datablocks through copied material nodes.
    """
    mapping = {}
    for obj in template_objects:
        dup = obj.copy()
        dup.data = obj.data
        dup.animation_data_clear()
        bpy.context.scene.collection.objects.link(dup)
        _osi_sanitize_mesh_material_slots(dup, report_lines, context="BFRES_CACHE_DUPLICATE")
        mapping[obj] = dup

    for src, dup in mapping.items():
        if src.parent in mapping:
            dup.parent = mapping[src.parent]
            dup.matrix_parent_inverse = src.matrix_parent_inverse.copy()
        else:
            dup.parent = None
            dup.matrix_parent_inverse.identity()
        for modifier in dup.modifiers:
            target = getattr(modifier, "object", None)
            if target in mapping:
                try:
                    modifier.object = mapping[target]
                except Exception:
                    pass
        for constraint in dup.constraints:
            target = getattr(constraint, "target", None)
            if target in mapping:
                try:
                    constraint.target = mapping[target]
                except Exception:
                    pass
    return list(mapping.values())


def _cache_key_for_bfres(filepath, prefs, target_model_names=None):
    return (
        str(Path(filepath).resolve()),
        tuple(str(name).casefold() for name in (target_model_names or []) if str(name)),
        _effective_bfres_texture_mode(prefs),
        _effective_import_materials(),
        bool(getattr(prefs, "component_selector", True)),
        bool(getattr(prefs, "custom_normals", True)),
        int(getattr(prefs, "lod_index", 0)),
        bool(getattr(prefs, "import_anims", False)),
        "no_live_image_material_cache_v2_no_material_template_reuse",
    )


def _import_bfres_cached(filepath, prefs, import_cache, report_lines, actor_label, name_prefix="", target_model_names=None, texture_diag_owner="BCETT"):
    key = _cache_key_for_bfres(filepath, prefs, target_model_names)
    cached = import_cache.get(key)
    textured_material_import = bool(_effective_import_materials()) and _effective_bfres_texture_mode(prefs) != "OFF"
    if cached and textured_material_import:
        # The geometry/template cache is fast, but its mesh material slots can keep
        # Blender Material datablocks with stale Image node references.  For textured
        # imports, bypass the BFRES object cache and re-run the BFRES material path;
        # SRTHash/source collection instancing remains available after the fresh import.
        report_lines.append("BFRES_CACHE_BYPASS owner=%r actor=%r path=%s targets=%s reason=textured_materials_require_fresh_materials" % (texture_diag_owner, actor_label, Path(filepath).name, list(target_model_names or [])))
        cached = None
    if cached:
        duplicated = _duplicate_linked_objects(cached["objects"], report_lines=report_lines)
        if _is_full_report():
            report_lines.append(
                "CACHE_HIT actor=%r path=%s targets=%s duplicated_objects=%d template_roots=[%s] duplicate_roots=[%s]"
                % (actor_label, Path(filepath).name, list(target_model_names or []), len(duplicated), cached.get("root_summary", "none"), _object_transform_summary(duplicated))
            )
        report_lines.append("BFRES_CACHE_HIT owner=%r actor=%r path=%s targets=%s duplicated_objects=%d" % (texture_diag_owner, actor_label, Path(filepath).name, list(target_model_names or []), len(duplicated)))
        return duplicated, list(cached.get("messages", [])), True

    imported, messages = _import_bfres(filepath, prefs, name_prefix=name_prefix, target_model_names=target_model_names, texture_diag_owner=texture_diag_owner)
    for obj in imported or []:
        _osi_sanitize_mesh_material_slots(obj, report_lines, context="BFRES_IMPORT")
    if imported:
        root_summary = _object_transform_summary(imported) if _is_full_report() else "suppressed"
        import_cache[key] = {"objects": list(imported), "messages": list(messages), "root_summary": root_summary}
        if _is_full_report():
            report_lines.append(
                "CACHE_STORE actor=%r path=%s targets=%s template_objects=%d template_roots=[%s]"
                % (actor_label, Path(filepath).name, list(target_model_names or []), len(imported), root_summary)
            )
    return imported, messages, False


def _import_bfres(filepath, prefs, name_prefix="", target_model_names=None, texture_diag_owner="BCETT"):
    from ._bfres_vendor.importing import Importer as BFRESImporter

    before = set(bpy.context.scene.objects)
    before_images = set(bpy.data.images)
    before_materials = set(bpy.data.materials)
    messages = []
    messages.append("BFRES_IMPORT_CONTEXT owner=%r filepath=%r name_prefix=%r targets=%s images_before=%d materials_before=%d" % (texture_diag_owner, str(filepath), name_prefix, list(target_model_names or []), len(before_images), len(before_materials)))
    operator = _bfres_operator_from_preferences(prefs, name_prefix=name_prefix, report_messages=messages, texture_diag_owner=texture_diag_owner)
    operator.target_model_names = list(target_model_names or [])
    result = BFRESImporter(operator, filepath).run()
    if result != {"FINISHED"}:
        raise RuntimeError("BFRES importer failed for %s" % filepath)
    after_images = set(bpy.data.images)
    after_materials = set(bpy.data.materials)
    messages.append("BFRES_IMPORT_CONTEXT_END owner=%r filepath=%r images_after=%d images_delta=%d materials_after=%d materials_delta=%d" % (texture_diag_owner, str(filepath), len(after_images), len(after_images)-len(before_images), len(after_materials), len(after_materials)-len(before_materials)))
    imported = [obj for obj in bpy.context.scene.objects if obj not in before]
    if not imported:
        imported = list(bpy.context.selected_objects)
    return imported, messages


def _srthash_value(entry):
    """Return a stable SRTHash/Hash value for cross-layer instancing."""
    if not isinstance(entry, dict):
        return None
    for key in ("SRTHash", "Hash"):
        value = entry.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except Exception:
                return str(value)
    return None


def _srthash_instance_key(entry, actor_label, model_path, target_model_names):
    """Key identical SRTHash actors after BFRES/FMDL resolution.

    For cross-layer duplicates, actor labels can differ while the resolved mesh
    and SRTHash are identical.  Key on BFRES path + target FMDL set + SRTHash so
    Pnt/Var/Vgl copies can be collection-instanced instead of re-imported.
    """
    srthash = _srthash_value(entry)
    if srthash is None or model_path is None:
        return None
    targets = tuple(str(name).casefold() for name in (target_model_names or []) if str(name))
    if not targets:
        # If there is no explicit target list, include available FMDLs when cheap
        # so multi-model BFRES files do not accidentally share different geometry.
        fmdl_names = _bfres_fmdl_names_cached(model_path) or ()
        targets = tuple(str(name).casefold() for name in fmdl_names if str(name))
    return (
        str(Path(model_path).resolve()),
        targets,
        srthash,
    )




def _unlink_object_from_all_collections(obj):
    """Unlink an object from every visible collection without deleting it."""
    try:
        users = list(obj.users_collection)
    except Exception:
        users = []
    for coll in users:
        try:
            coll.objects.unlink(obj)
        except Exception:
            pass

def _srthash_source_collection_name(actor_label, srthash):
    base = _safe_collection_name("SRTHash_%s_%s" % (actor_label, srthash), "SRTHashInstance")
    return base[:63] if len(base) > 63 else base


def _make_srthash_source_collection(actor_label, srthash, actor_root, imported_objects):
    """Create a neutral local-space source collection for collection instances.

    The first actor remains visible as a normal actor root with its real BYML
    transform.  The source collection gets linked duplicates whose source root is
    reset to identity.  Later collection-instance objects can then own the real
    transform themselves, so selecting the instance shows its location/rotation
    instead of [0, 0, 0].
    """
    name = _srthash_source_collection_name(actor_label, srthash)
    coll = bpy.data.collections.new(name)
    coll["osi_srthash_instance_source"] = True
    coll["osi_srthash"] = str(srthash)

    source_objects = _duplicate_linked_objects([actor_root] + list(imported_objects or []))
    source_root = None
    for obj in source_objects:
        if obj.name.startswith(actor_root.name):
            source_root = obj
            break
    if source_root is None and source_objects:
        source_root = source_objects[0]

    if source_root is not None:
        source_root.location = (0.0, 0.0, 0.0)
        source_root.rotation_euler = (0.0, 0.0, 0.0)
        source_root.scale = (1.0, 1.0, 1.0)
        try:
            source_root["osi_srthash_neutral_source_root"] = True
        except Exception:
            pass

    for obj in source_objects:
        try:
            coll.objects.link(obj)
        except Exception:
            pass
        _unlink_object_from_all_collections(obj)
        try:
            coll.objects.link(obj)
        except Exception:
            pass
    return coll


def _create_srthash_collection_instance(entry, actor_label, layer_label, source_collection, stage_collection, report_lines, location, rotation_euler, scale):
    """Create a Blender collection-instance object in the requested layer."""
    name = _safe_collection_name("%s_SRTHashInstance" % actor_label, "SRTHashInstance")
    inst = bpy.data.objects.new(name, None)
    inst.empty_display_type = "PLAIN_AXES"
    inst.empty_display_size = 0.5
    inst.instance_type = 'COLLECTION'
    inst.instance_collection = source_collection
    inst.location = location
    inst.rotation_euler = rotation_euler
    inst.scale = scale
    _set_bcett_transform_props(inst, entry, actor_label, location, rotation_euler, scale)
    _move_objects_to_collection([inst], _layer_collection_for_entry(stage_collection, entry))
    try:
        inst["osi_srthash_collection_instance"] = True
        inst["osi_source_collection"] = source_collection.name
        srthash = _srthash_value(entry)
        if srthash is not None:
            inst["osi_srthash"] = str(srthash)
    except Exception:
        pass
    report_lines.append(
        "SRTHASH_INSTANCE actor=%r layer=%r source_collection=%r instance=%r srthash=%r loc=%s rot_deg=%s scale=%s"
        % (
            actor_label,
            layer_label,
            source_collection.name,
            inst.name,
            _srthash_value(entry),
            tuple(round(float(v), 6) for v in inst.location),
            tuple(round(math.degrees(v), 6) for v in inst.rotation_euler),
            tuple(round(float(v), 6) for v in inst.scale),
        )
    )
    return inst

