"""Graffiti import/fold implementation.

This module is installed into addon.py globals to preserve Blender add-on
registration behavior while keeping the large graffiti subsystem out of the UI
module.
"""

_GRAFFITI_SOURCE = r'''
def _graffiti_param_for_type(type_value):
    table = _optional_rsdb("GraffitiParam")
    if not isinstance(table, dict):
        return None
    by_hash = table.get("by_hash", {}) or {}
    try:
        return by_hash.get(int(type_value))
    except Exception:
        return by_hash.get(type_value)


def _resolve_graffiti_texture(param_row, report_lines=None):
    if not isinstance(param_row, dict):
        return None
    resolver = _current_pack_resolver()
    for key in ("TexturePath", "OpaTexturePath"):
        ref = param_row.get(key)
        if not ref:
            continue
        resolved = resolver.resolve(ref, allow_basename_search=True, allow_directories=False)
        if resolved.get("path") and resolved.get("location") != "missing":
            _report_append(report_lines, "GRAFFITI_TEXTURE_RESOLVED ref=%r location=%s path=%r" % (ref, resolved.get("location"), resolved.get("name") or resolved.get("path")))
            return resolved
    return None

_GRAFFITI_BFRES_TEXTURE_CACHE = {}
_GRAFFITI_IMAGE_CACHE = {}
_GRAFFITI_MATERIAL_CACHE = {}



def _strip_blender_numeric_suffix(name):
    try:
        text = str(name or "")
        if len(text) > 4 and text[-4] == "." and text[-3:].isdigit():
            return text[:-4]
        return text
    except Exception:
        return str(name)


def _graffiti_image_cache_key(group, texture_name):
    return (str(group or ""), str(texture_name or ""))


def _register_or_reuse_graffiti_image(image, group, texture_name, report_lines=None):
    """Return a canonical shared Image datablock for one graffiti texture."""
    if image is None or not texture_name:
        return image
    key = _graffiti_image_cache_key(group, texture_name)
    cached = _GRAFFITI_IMAGE_CACHE.get(key)
    try:
        if cached and cached.name in bpy.data.images:
            if cached is not image:
                _report_append(report_lines, "GRAFFITI_IMAGE_CACHE_HIT group=%r texture=%r image=%r duplicate=%r" % (group, texture_name, cached.name, getattr(image, 'name', '')))
            return cached
    except Exception:
        pass
    key_text = "%s|%s" % key
    # Prefer an existing image tagged by a previous import in this Blender session.
    try:
        for existing in bpy.data.images:
            if existing is image:
                continue
            if existing.get("osi_graffiti_image_key") == key_text:
                _GRAFFITI_IMAGE_CACHE[key] = existing
                _report_append(report_lines, "GRAFFITI_IMAGE_CACHE_REUSE_EXISTING group=%r texture=%r image=%r duplicate=%r" % (group, texture_name, existing.name, getattr(image, 'name', '')))
                return existing
    except Exception:
        pass
    try:
        image["osi_graffiti_image_key"] = key_text
        image["osi_graffiti_group"] = str(group or "")
        image["osi_graffiti_texture"] = str(texture_name or "")
    except Exception:
        pass
    _GRAFFITI_IMAGE_CACHE[key] = image
    _report_append(report_lines, "GRAFFITI_IMAGE_CACHE_STORE group=%r texture=%r image=%r" % (group, texture_name, getattr(image, 'name', '')))
    return image


def _canonicalize_graffiti_texture_map(texture_map, group, report_lines=None):
    if not isinstance(texture_map, dict):
        return {}
    out = {}
    for texture_name, image in texture_map.items():
        out[texture_name] = _register_or_reuse_graffiti_image(image, group, texture_name, report_lines)
    return out


def _graffiti_material_cache_key(group, texture_name, alpha_texture_name, image=None, alpha_image=None):
    # Include texture names and group as the stable identity, with image names as a fallback
    # for placeholder/partially resolved paths.
    tex = str(texture_name or _strip_blender_numeric_suffix(getattr(image, "name", "")))
    alpha = str(alpha_texture_name or _strip_blender_numeric_suffix(getattr(alpha_image, "name", "")))
    return (str(group or ""), tex, alpha)


def _make_graffiti_material_cached(image, alpha_image=None, meta=None, report_lines=None):
    meta = meta if isinstance(meta, dict) else {}
    group = meta.get("group", "")
    texture_name = meta.get("texture", "")
    alpha_texture_name = meta.get("alpha_texture", "")
    key = _graffiti_material_cache_key(group, texture_name, alpha_texture_name, image, alpha_image)
    cached = _GRAFFITI_MATERIAL_CACHE.get(key)
    try:
        if cached and cached.name in bpy.data.materials:
            _report_append(report_lines, "GRAFFITI_MATERIAL_CACHE_HIT group=%r texture=%r alpha=%r material=%r" % (group, texture_name, alpha_texture_name, cached.name))
            return cached
    except Exception:
        pass
    key_text = "%s|%s|%s" % key
    try:
        for existing in bpy.data.materials:
            if existing.get("osi_graffiti_material_key") == key_text:
                _GRAFFITI_MATERIAL_CACHE[key] = existing
                _report_append(report_lines, "GRAFFITI_MATERIAL_CACHE_REUSE_EXISTING group=%r texture=%r alpha=%r material=%r" % (group, texture_name, alpha_texture_name, existing.name))
                return existing
    except Exception:
        pass
    mat_name = "OSI_Graffiti_%s" % str(texture_name or _strip_blender_numeric_suffix(getattr(image, "name", "Decal")))
    mat = _make_graffiti_material(image, alpha_image, name=mat_name)
    try:
        mat["osi_graffiti_material_key"] = key_text
        mat["osi_graffiti_group"] = str(group or "")
        mat["osi_graffiti_texture"] = str(texture_name or "")
        mat["osi_graffiti_alpha_texture"] = str(alpha_texture_name or "")
    except Exception:
        pass
    _GRAFFITI_MATERIAL_CACHE[key] = mat
    _report_append(report_lines, "GRAFFITI_MATERIAL_CACHE_STORE group=%r texture=%r alpha=%r material=%r" % (group, texture_name, alpha_texture_name, mat.name))
    return mat


def _graffiti_texture_name_from_ref(ref):
    """Return the texture resource name used inside Grf_*.bfres from an RSDB path."""
    if not ref:
        return ""
    try:
        name = Path(str(ref).replace("\\", "/")).name
    except Exception:
        name = str(ref).replace("\\", "/").split("/")[-1]
    for suffix in (".ftxb.zs", ".bntx.zs", ".bfres.zs", ".ftxb", ".bntx", ".bfres", ".zs"):
        if name.casefold().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _graffiti_group_from_ref(ref):
    """Extract Grf_* folder/group from Work/Model/Graffiti/<group>/... paths."""
    if not ref:
        return ""
    parts = [part for part in str(ref).replace("\\", "/").split("/") if part]
    for i, part in enumerate(parts):
        if part.casefold() == "graffiti" and i + 1 < len(parts):
            return parts[i + 1]
    for part in parts:
        if part.startswith("Grf_"):
            return part
    return ""


def _resolve_graffiti_bfres_path(group_name, report_lines=None):
    if not group_name:
        return None
    resolver = _current_pack_resolver()
    candidates = [
        "Model/%s.bfres.zs" % group_name,
        "Model/%s.bfres" % group_name,
        "Model/Graffiti/%s.bfres.zs" % group_name,
        "Model/Graffiti/%s.bfres" % group_name,
        "%s.bfres.zs" % group_name,
        "%s.bfres" % group_name,
    ]
    for ref in candidates:
        resolved = resolver.resolve(ref, allow_basename_search=True, allow_directories=False)
        path = resolved.get("path")
        if path and resolved.get("location") != "missing":
            return path
    _report_append(report_lines, "GRAFFITI_BFRES_MISSING group=%r candidates=%r" % (group_name, candidates[:3]))
    return None


def _load_graffiti_bfres_texture_map(group_name, prefs, report_lines=None):
    """Import only embedded BNTX textures from Grf_*.bfres and return name -> Image."""
    if not group_name:
        return {}
    cache_key = str(group_name)
    cached = _GRAFFITI_BFRES_TEXTURE_CACHE.get(cache_key)
    if isinstance(cached, dict):
        live = {}
        for name, image in cached.items():
            try:
                if image and image.name in bpy.data.images:
                    live[name] = image
            except Exception:
                pass
        if live:
            live = _canonicalize_graffiti_texture_map(live, group_name, report_lines)
            _GRAFFITI_BFRES_TEXTURE_CACHE[cache_key] = live
            return live
    path = _resolve_graffiti_bfres_path(group_name, report_lines)
    if not path:
        return {}
    try:
        from ._bfres_vendor.importing import Importer as _BfresImporter
        op = _bfres_operator_from_preferences(prefs, name_prefix="Graffiti_%s_" % group_name, report_messages=report_lines, texture_diag_owner="GRAFFITI")
        # Force texture import on for graffiti texture atlases, but select a nonexistent model.
        # The BFRES importer loads embedded BNTX before FMDL selection, so this imports images
        # without creating unwanted Grf_* mesh objects.
        op.import_tex_mode = "AUTO"
        op.target_model_names = ["__OSI_GRAFFITI_TEXTURES_ONLY__"]
        importer = _BfresImporter(op, str(path))
        importer.run()
        texture_map = dict(getattr(importer, "texture_map", {}) or {})
        texture_map = _canonicalize_graffiti_texture_map(texture_map, group_name, report_lines)
        _GRAFFITI_BFRES_TEXTURE_CACHE[cache_key] = texture_map
        _report_append(report_lines, "GRAFFITI_BFRES_TEXTURES_LOADED group=%r path=%r textures=%d" % (group_name, str(path), len(texture_map)))
        return texture_map
    except Exception as exc:
        _report_append(report_lines, "GRAFFITI_BFRES_TEXTURES_FAILED group=%r path=%r error=%s" % (group_name, str(path), exc))
        return {}


def _graffiti_alpha_candidates_from_alb(alb_name, explicit_opa_name=""):
    """Return likely opacity/mask texture names for a graffiti *_Alb texture."""
    candidates = []
    if explicit_opa_name:
        candidates.append(explicit_opa_name)
    if alb_name:
        replacements = (
            ("_Alb", "_Opa"), ("_ALB", "_OPA"), ("_alb", "_opa"),
            ("_Alb", "_Alpha"), ("_Alb", "_Mask"),
        )
        for old, new in replacements:
            if alb_name.endswith(old):
                candidates.append(alb_name[: -len(old)] + new)
        candidates.append(alb_name + "_Opa")
    out = []
    for name in candidates:
        if name and name not in out:
            out.append(name)
    return out


def _resolve_graffiti_bfres_image(param_row, group_names, prefs, report_lines=None):
    """Resolve graffiti Type -> RSDB texture basename -> image in Grf_General/Grf_stage BFRES."""
    if not isinstance(param_row, dict):
        return None, None
    alb_name = _graffiti_texture_name_from_ref(param_row.get("TexturePath"))
    explicit_opa_name = _graffiti_texture_name_from_ref(param_row.get("OpaTexturePath"))
    opa_candidates = _graffiti_alpha_candidates_from_alb(alb_name, explicit_opa_name)
    preferred_group = _graffiti_group_from_ref(param_row.get("TexturePath") or param_row.get("OpaTexturePath"))
    groups = []
    for group in (preferred_group, *(group_names or [])):
        if group and group not in groups:
            groups.append(group)
    for group in groups:
        texture_map = _load_graffiti_bfres_texture_map(group, prefs, report_lines)
        if not texture_map:
            continue
        image = texture_map.get(alb_name) if alb_name else None
        alpha_image = None
        alpha_name = ""
        for candidate in opa_candidates:
            alpha_image = texture_map.get(candidate)
            if alpha_image:
                alpha_name = candidate
                break
        if image:
            image = _register_or_reuse_graffiti_image(image, group, alb_name, report_lines)
            if alpha_image and alpha_name:
                alpha_image = _register_or_reuse_graffiti_image(alpha_image, group, alpha_name, report_lines)
            alpha_source = "explicit" if explicit_opa_name and alpha_name == explicit_opa_name else ("sibling_opa" if alpha_name else "none")
            _report_append(report_lines, "GRAFFITI_BFRES_TEXTURE_RESOLVED group=%r texture=%r alpha=%r alpha_texture=%r alpha_source=%s image=%r" % (group, alb_name, bool(alpha_image), alpha_name, alpha_source, getattr(image, 'name', '')))
            return image, {"group": group, "texture": alb_name, "alpha_texture": alpha_name, "alpha_source": alpha_source}
    if alb_name:
        _report_append(report_lines, "GRAFFITI_BFRES_TEXTURE_MISSING texture=%r groups=%r alpha_candidates=%r" % (alb_name, groups, opa_candidates))
    return None, None


def _graffiti_image_size_tuple(image):
    try:
        size = getattr(image, "size", None)
        if size and len(size) >= 2:
            return (int(size[0]), int(size[1]))
    except Exception:
        pass
    return None


def _graffiti_size_like_fields(mapping):
    """Compact diagnostic view of RSDB/BYML fields that might encode decal size."""
    if not isinstance(mapping, dict):
        return {}
    needles = ("scale", "size", "width", "height", "radius", "rect", "bounds", "extent", "offset")
    out = {}
    for key, value in mapping.items():
        try:
            k = str(key)
        except Exception:
            continue
        kl = k.casefold()
        if any(n in kl for n in needles):
            try:
                out[k] = _idprop_safe(value)
            except Exception:
                out[k] = repr(value)
    return out


def _graffiti_mask_bbox(image, threshold=0.01, max_pixels=4194304):
    """Return a compact bbox/coverage diagnostic for alpha or opacity images.

    For RGBA images, alpha is used. For RGB/grayscale opacity textures, max RGB
    is used. This is diagnostic-only and intentionally bounded so very large
    textures do not make import reports unusably slow.
    """
    size = _graffiti_image_size_tuple(image)
    if not size:
        return None
    w, h = size
    if w <= 0 or h <= 0 or (w * h) > int(max_pixels):
        return {"size": size, "skipped": "too_large_or_invalid"}
    try:
        pixels = list(image.pixels[:])
    except Exception as exc:
        return {"size": size, "error": repr(exc)}
    if not pixels:
        return {"size": size, "empty": True}
    channels = max(1, int(round(len(pixels) / float(max(1, w * h)))))
    min_x = w
    min_y = h
    max_x = -1
    max_y = -1
    count = 0
    total = w * h
    for i in range(total):
        base = i * channels
        try:
            if channels >= 4:
                v = float(pixels[base + 3])
            elif channels >= 3:
                v = max(float(pixels[base]), float(pixels[base + 1]), float(pixels[base + 2]))
            else:
                v = float(pixels[base])
        except Exception:
            continue
        if v > threshold:
            x = i % w
            y = i // w
            if x < min_x: min_x = x
            if y < min_y: min_y = y
            if x > max_x: max_x = x
            if y > max_y: max_y = y
            count += 1
    if count <= 0:
        return {"size": size, "channels": channels, "coverage": 0.0, "bbox_px": None}
    bw = max_x - min_x + 1
    bh = max_y - min_y + 1
    return {
        "size": size,
        "channels": channels,
        "threshold": float(threshold),
        "bbox_px": (int(min_x), int(min_y), int(max_x), int(max_y)),
        "bbox_size_px": (int(bw), int(bh)),
        "bbox_aspect": round(float(bw) / float(bh), 6) if bh else None,
        "bbox_frac": (round(float(bw) / float(w), 6), round(float(bh) / float(h), 6)),
        "coverage": round(float(count) / float(total), 6),
    }


def _graffiti_mesh_bounds_diag(mesh):
    try:
        verts = list(getattr(mesh, "vertices", []) or [])
        if not verts:
            return None
        xs = [float(v.co.x) for v in verts]
        ys = [float(v.co.y) for v in verts]
        zs = [float(v.co.z) for v in verts]
        return {
            "min": (round(min(xs), 6), round(min(ys), 6), round(min(zs), 6)),
            "max": (round(max(xs), 6), round(max(ys), 6), round(max(zs), 6)),
            "size": (round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6), round(max(zs) - min(zs), 6)),
        }
    except Exception as exc:
        return {"error": repr(exc)}


def _make_graffiti_material(image, alpha_image=None, name="OSI_Graffiti_Decal_BFRES"):
    mat = bpy.data.materials.new(name=name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 0.8)
    try:
        mat.use_nodes = True
        mat.blend_method = "BLEND"
        mat.show_transparent_back = True
        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        tex_node = nodes.new(type="ShaderNodeTexImage")
        tex_node.image = image
        mat.node_tree.links.new(tex_node.outputs.get("Color"), bsdf.inputs.get("Base Color"))
        if alpha_image:
            alpha_node = nodes.new(type="ShaderNodeTexImage")
            alpha_node.image = alpha_image
            try:
                # Graffiti opacity masks are usually BC4/greyscale images with
                # no meaningful image alpha channel, so prefer Color over Alpha.
                alpha_node.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            alpha_out = alpha_node.outputs.get("Color") or alpha_node.outputs.get("Alpha")
            if alpha_out and bsdf.inputs.get("Alpha"):
                mat.node_tree.links.new(alpha_out, bsdf.inputs.get("Alpha"))
        elif tex_node.outputs.get("Alpha") and bsdf.inputs.get("Alpha"):
            mat.node_tree.links.new(tex_node.outputs.get("Alpha"), bsdf.inputs.get("Alpha"))
    except Exception:
        pass
    return mat


def _extract_graffiti_group_set(data):
    if not isinstance(data, dict):
        return []
    info = data.get("GraffitiInfo") if isinstance(data.get("GraffitiInfo"), dict) else data
    groups = info.get("GroupSetList") if isinstance(info, dict) else None
    if isinstance(groups, list):
        return [str(g) for g in groups if g]
    return []


def _row_first_value(row, keys, default=None):
    if not isinstance(row, dict):
        return default
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return default


def _row_vector3(row, scalar_keys, vector_keys=(), default=0.0):
    """Read a vector from either separate scalar fields or a nested/list field."""
    if not isinstance(row, dict):
        return (float(default), float(default), float(default))
    for key in vector_keys:
        value = row.get(key)
        if isinstance(value, dict):
            return (
                _to_float(value.get("X", value.get("x", default)), default),
                _to_float(value.get("Y", value.get("y", default)), default),
                _to_float(value.get("Z", value.get("z", default)), default),
            )
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return (_to_float(value[0], default), _to_float(value[1], default), _to_float(value[2], default))
    x_keys, y_keys, z_keys = scalar_keys
    return (
        _to_float(_row_first_value(row, x_keys, default), default),
        _to_float(_row_first_value(row, y_keys, default), default),
        _to_float(_row_first_value(row, z_keys, default), default),
    )


def _safe_set_idprop(obj, key, value):
    try:
        obj[key] = _idprop_safe(value)
    except Exception:
        try:
            obj[key] = repr(value)
        except Exception:
            pass


def _graffiti_raw_transform(row):
    raw_pos = _row_vector3(
        row,
        (("PosX", "ActorPosX", "TranslateX", "X"), ("PosY", "ActorPosY", "TranslateY", "Y"), ("PosZ", "ActorPosZ", "TranslateZ", "Z")),
        vector_keys=("Pos", "Position", "Translate", "ActorTranslate"),
        default=0.0,
    )
    raw_rot = _row_vector3(
        row,
        (("RotX", "ActorRotX", "RotateX", "RotationX"), ("RotY", "ActorRotY", "RotateY", "RotationY"), ("RotZ", "ActorRotZ", "RotateZ", "RotationZ")),
        vector_keys=("Rot", "Rotate", "Rotation", "ActorRotate", "ActorRot"),
        default=0.0,
    )
    uniform = _row_first_value(row, ("Scale", "ActorScale", "Size", "GraffitiScale"), None)
    raw_scale = _row_vector3(
        row,
        (("ScaleX", "ActorScaleX"), ("ScaleY", "ActorScaleY"), ("ScaleZ", "ActorScaleZ")),
        # Do not pass scalar Scale as a vector key.  Graffiti BYML commonly
        # stores Scale as a single float; treating it as vector-like caused the
        # diagnostic raw_scale to misleadingly remain (1, 1, 1).
        vector_keys=("ActorScale",),
        default=1.0,
    )
    if uniform is not None and not isinstance(uniform, (list, tuple, dict)):
        u = _graffiti_scale_component(uniform, 1.0)
        raw_scale = (u, u, u)
    return raw_pos, raw_rot, raw_scale, uniform


def _graffiti_scale_component(value, default=1.0):
    try:
        if isinstance(value, (list, tuple, dict)):
            return float(default)
        v = _to_float(value, default)
        if not math.isfinite(v):
            return float(default)
        return v
    except Exception:
        return float(default)


def _graffiti_param_scale(param_row):
    if not isinstance(param_row, dict):
        return 1.0
    value = param_row.get("Scale")
    try:
        v = float(value)
        if math.isfinite(v) and v > 0.0:
            return v
    except Exception:
        pass
    return 1.0



def _graffiti_pixels_to_world(px):
    """Convert graffiti texture pixels to Blender/world units.

    Equivalent to the former experimental formula:
        (px / 60) * 0.72
    written exactly as px * 3 / 250.
    BYML/actor scale is applied before mesh construction by the caller.
    """
    try:
        return float(px) * 3.0 / 250.0
    except Exception:
        return 0.0


def _graffiti_dimensions_from_row(row, image=None, prefs=None, param_row=None):
    """Return final decal plane dimensions.

    For texture-backed graffiti, BYML scale is baked directly into the mesh
    before FoldInfo seams are built. Object scale remains identity.
    """
    param_scale = _graffiti_param_scale(param_row)
    raw_pos, raw_rot, raw_scale, uniform = _graffiti_raw_transform(row)
    if uniform is not None and not isinstance(uniform, (list, tuple, dict)):
        row_scale = abs(_to_float(uniform, 1.0))
    else:
        sx = abs(_graffiti_scale_component(raw_scale[0], 1.0))
        sy = abs(_graffiti_scale_component(raw_scale[1], sx or 1.0))
        row_scale = max(sx, sy)
    row_scale = max(0.001, row_scale)

    # v0.7.65 sizing: use one universal pixel-density rule for all graffiti,
    # applied before FoldInfo seam construction for folded meshes:
    #     world_size = texture_pixels * 3 / 250 * BYML_scale
    # This is exactly equivalent to the prior px/60 * 0.72 * BYML_scale test,
    # but bakes the scale into the mesh so FoldInfo coordinates are evaluated
    # against the final sized plane.
    try:
        if image is not None and getattr(image, "size", None):
            iw, ih = image.size
            iw = float(iw or 0.0)
            ih = float(ih or 0.0)
            if iw > 0.0 and ih > 0.0:
                return max(0.001, _graffiti_pixels_to_world(iw) * row_scale), max(0.001, _graffiti_pixels_to_world(ih) * row_scale), "texture_pixels_3_over_250_byml_scale", row_scale, param_scale
    except Exception:
        pass

    # Only use explicit row dimensions as a fallback when no texture image was
    # resolved, because the current experiment is intended to test pixel-derived
    # sizing for all resolved graffiti.
    explicit_w = _row_first_value(row, ("Width", "SizeX", "ScaleX", "GraffitiWidth"), None)
    explicit_h = _row_first_value(row, ("Height", "SizeY", "ScaleY", "GraffitiHeight"), None)
    if explicit_w is not None and explicit_h is not None:
        width = max(0.01, abs(_to_float(explicit_w, 1.0)))
        height = max(0.01, abs(_to_float(explicit_h, 1.0)))
        return width * row_scale * param_scale, height * row_scale * param_scale, "explicit_width_height_byml_scale", row_scale, param_scale

    # Fallback when no texture image has been resolved: preserve the old unit
    # texture-aspect plane so import remains possible, but mark the source.
    aspect = 1.0
    try:
        if image is not None and getattr(image, "size", None):
            iw, ih = image.size
            if ih:
                aspect = max(0.01, float(iw) / float(ih))
    except Exception:
        aspect = 1.0
    base = max(0.001, param_scale)
    return max(0.001, base * aspect), max(0.001, base), "fallback_object_scale_texture_aspect", row_scale, param_scale

def _graffiti_location_from_row(row):
    # Graffiti rows use game coordinates. Match the BCETT [X, Z, -Y] -> Blender [X, Y, Z] convention.
    raw_pos, _raw_rot, _raw_scale, _uniform = _graffiti_raw_transform(row)
    x, y, z = raw_pos
    return Vector((x, -z, y))


def _graffiti_rotation_from_row(row):
    _raw_pos, raw, _raw_scale, _uniform = _graffiti_raw_transform(row)
    try:
        game_matrix = Euler(raw, "XYZ").to_matrix()
        blender_matrix = _SPLATOON_AXIS_MATRIX @ game_matrix @ _SPLATOON_AXIS_MATRIX.inverted()
        # Graffiti decal planes are created in Blender's XY plane, but the BYML
        # rotations appear to describe a decal-space plane whose X reference is
        # offset by +90 degrees from Blender's primitive plane.  Applying this
        # basis correction makes common floor decals with raw X=-90 land near
        # Blender X=0, and wall decals with raw X=0 land near Blender X=90.
        decal_basis = Matrix.Rotation(math.radians(90.0), 3, "X")
        return (blender_matrix @ decal_basis).to_euler("XYZ")
    except Exception:
        try:
            return Euler((raw[0] + math.radians(90.0), raw[1], raw[2]), "XYZ")
        except Exception:
            return Euler((math.radians(90.0), 0.0, 0.0), "XYZ")



def _graffiti_fold_items(row):
    if not isinstance(row, dict):
        return []
    folds = row.get("GraffitiFoldInfo")
    return folds if isinstance(folds, list) else []


def _graffiti_bool_value(value, default=None):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _graffiti_fold_angle_value(item):
    if not isinstance(item, dict):
        return None
    for key in ("Angle", "FoldAngle", "Rotation", "Rot"):
        if key not in item:
            continue
        try:
            value = float(item.get(key))
            if not math.isfinite(value):
                return None
            if abs(value) > (2.0 * math.pi + 0.001):
                value = math.radians(value)
            return value
        except Exception:
            continue
    return None


def _graffiti_fold_coord_to_mesh(value, dim):
    """Map FoldInfo XY into the generated decal mesh's local 2D plane.

    The current best interpretation is that FoldInfo XY already describes the
    unrotated local decal plane used by Blender.  If the value looks like a
    normalized UV coordinate and not a plausible local coordinate, map 0..1 to
    the mesh dimension.  Otherwise keep the authored local value and clamp only
    extreme outliers so an infinite seam can still cut the visible rectangle.
    """
    try:
        v = float(value)
    except Exception:
        v = 0.0
    if not math.isfinite(v):
        v = 0.0
    half = 0.5 * max(0.001, float(dim))
    # Prefer centered local coordinates. Values in -half..half are already in
    # the active Blender mesh coordinate system after object rotation is cleared.
    if -half - 1e-5 <= v <= half + 1e-5:
        return v
    # If the value is only valid as UV-like 0..1, map it to local coordinates.
    if 0.0 <= v <= 1.0 and not (-half <= v <= half):
        return (v - 0.5) * float(dim)
    # Keep nearby authored values because FoldInfo endpoints need not reach the
    # texture edge; the seam is extended as an infinite line during clipping.
    limit = max(half * 4.0, 4.0)
    return max(-limit, min(limit, v))


def _graffiti_fold_axis_from_item(item, width, height):
    if not isinstance(item, dict):
        return None
    if all(k in item for k in ("P0X", "P0Y", "P1X", "P1Y")):
        p0 = Vector((_graffiti_fold_coord_to_mesh(item.get("P0X"), width), _graffiti_fold_coord_to_mesh(item.get("P0Y"), height)))
        p1 = Vector((_graffiti_fold_coord_to_mesh(item.get("P1X"), width), _graffiti_fold_coord_to_mesh(item.get("P1Y"), height)))
        if (p1 - p0).length > 1e-6:
            return p0, p1
    cx = _graffiti_fold_coord_to_mesh(item.get("CenterPointX", 0.0), width)
    cy = _graffiti_fold_coord_to_mesh(item.get("CenterPointY", 0.0), height)
    angle = _graffiti_fold_angle_value(item) or 0.0
    direction = Vector((math.cos(angle), math.sin(angle)))
    if direction.length <= 1e-6:
        direction = Vector((1.0, 0.0))
    direction.normalize()
    length = max(width, height) * 2.0
    center = Vector((cx, cy))
    return center - direction * length, center + direction * length


def _graffiti_fold_side_2d(point, p0, axis_dir):
    try:
        return axis_dir.x * (point.y - p0.y) - axis_dir.y * (point.x - p0.x)
    except Exception:
        return 0.0


def _graffiti_fold_clip_polygon(poly, p0, axis_dir, keep_positive=True, eps=1e-7):
    if not poly:
        return []
    result = []
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        da = _graffiti_fold_side_2d(a, p0, axis_dir)
        db = _graffiti_fold_side_2d(b, p0, axis_dir)
        a_inside = da >= -eps if keep_positive else da <= eps
        b_inside = db >= -eps if keep_positive else db <= eps
        if a_inside:
            result.append(Vector((a.x, a.y)))
        if a_inside != b_inside:
            denom = da - db
            if abs(denom) > eps:
                t = da / denom
                result.append(a + (b - a) * t)
    return result


def _graffiti_clean_poly(poly, eps=1e-6):
    out = []
    for pt in poly or []:
        v = Vector((pt.x, pt.y))
        if not out or (v - out[-1]).length > eps:
            out.append(v)
    if len(out) > 1 and (out[0] - out[-1]).length <= eps:
        out.pop()
    changed = True
    while changed and len(out) >= 3:
        changed = False
        cleaned = []
        n = len(out)
        for i, pt in enumerate(out):
            prev = out[(i - 1) % n]
            nxt = out[(i + 1) % n]
            a = pt - prev
            b = nxt - pt
            if a.length <= eps or b.length <= eps:
                changed = True
                continue
            cross = abs(a.x * b.y - a.y * b.x)
            if cross <= eps and a.dot(b) >= 0.0:
                changed = True
                continue
            cleaned.append(pt)
        out = cleaned
    return out


def _graffiti_uv_from_local_2d(pt, hw, hh):
    try:
        return ((pt.x + hw) / max(2.0 * hw, 1e-6), (pt.y + hh) / max(2.0 * hh, 1e-6))
    except Exception:
        return (0.0, 0.0)


def _graffiti_rotate_point_around_axis(point, axis_point, axis_dir, angle):
    try:
        if abs(float(angle)) <= 1e-8:
            return point
        axis = axis_dir.normalized()
        mat = Matrix.Rotation(float(angle), 4, axis)
        return axis_point + (mat @ (point - axis_point))
    except Exception:
        return point


def _graffiti_effective_fold_angle(fd):
    """Return a seam-local fold angle.

    FoldInfo gives a seam and an authored fold amount, but the sign needs to be
    interpreted relative to the side being rotated.  Use the original decal
    plane normal and seam direction to build a hinge frame, then mirror the
    angle for the negative side so the same authored angle folds away from the
    original plane instead of around a global/object-ish basis.
    """
    try:
        base = float(fd.get("angle", 0.0))
        if not bool(fd.get("rotate_positive_side", False)):
            return -base
        return base
    except Exception:
        return fd.get("angle", 0.0)


def _graffiti_apply_fold_rotations(pt2, rotations):
    pt3 = Vector((pt2.x, pt2.y, 0.0))
    for fd in rotations or []:
        pt3 = _graffiti_rotate_point_around_axis(pt3, fd["p0_3d"], fd["axis_dir_3d"], _graffiti_effective_fold_angle(fd))
    return (pt3.x, pt3.y, pt3.z)


def _graffiti_apply_inverse_fold_rotations(pt3, rotations):
    """Undo a panel's fold rotations so that panel can remain on the original flat plane.

    This is used to preserve the pre-fold graffiti placement/orientation for an
    anchor panel, usually the largest panel or the one associated with
    CenterPointX/Y.  The other panels keep their relative folds, but the whole
    folded mesh is rolled back into the same local plane orientation as the flat
    placeholder used before fold deformation.
    """
    out = Vector((pt3[0], pt3[1], pt3[2]))
    for fd in reversed(rotations or []):
        out = _graffiti_rotate_point_around_axis(out, fd["p0_3d"], fd["axis_dir_3d"], -_graffiti_effective_fold_angle(fd))
    return (out.x, out.y, out.z)


def _graffiti_fold_center_points(fold_defs):
    pts = []
    for fd in fold_defs or []:
        item = fd.get("item") if isinstance(fd, dict) else None
        if not isinstance(item, dict):
            continue
        if "CenterPointX" in item or "CenterPointY" in item:
            try:
                cx = _graffiti_fold_coord_to_mesh(item.get("CenterPointX", 0.0), 1.0)
                cy = _graffiti_fold_coord_to_mesh(item.get("CenterPointY", 0.0), 1.0)
                # Re-map against active dimensions when present on fd.
                if "mesh_width" in fd and "mesh_height" in fd:
                    cx = _graffiti_fold_coord_to_mesh(item.get("CenterPointX", 0.0), fd.get("mesh_width", 1.0))
                    cy = _graffiti_fold_coord_to_mesh(item.get("CenterPointY", 0.0), fd.get("mesh_height", 1.0))
                pts.append(Vector((cx, cy)))
            except Exception:
                pass
    return pts



def _graffiti_fold_center_point_for_fd(fd):
    """Return the FoldInfo CenterPoint in local texture/mesh coordinates."""
    try:
        item = fd.get("item") if isinstance(fd, dict) else None
        if not isinstance(item, dict):
            return None
        if "CenterPointX" not in item and "CenterPointY" not in item:
            return None
        w = float(fd.get("mesh_width", 1.0)) if isinstance(fd, dict) else 1.0
        h = float(fd.get("mesh_height", 1.0)) if isinstance(fd, dict) else 1.0
        return Vector((
            _graffiti_fold_coord_to_mesh(item.get("CenterPointX", 0.0), w),
            _graffiti_fold_coord_to_mesh(item.get("CenterPointY", 0.0), h),
        ))
    except Exception:
        return None


def _graffiti_panel_records(panels):
    records = []
    for i, panel in enumerate(panels or []):
        poly = _graffiti_clean_poly(panel.get("poly") or [])
        if len(poly) >= 3:
            records.append({
                "id": int(i),
                "panel": panel,
                "poly": poly,
                "area": float(_graffiti_poly_area(poly)),
                "centroid": _graffiti_poly_centroid(poly),
            })
    return records



class _GraffitiPanelState:
    """Cached final-panel state for one graffiti decal.

    Panelization is comparatively small but the same derived data is needed by
    folding, topology diagnostics, and report diagnostics.  Keep records,
    adjacency, centerpoint assignments, and id maps together so callers do not
    rebuild or accidentally diverge on panel ids.
    """
    __slots__ = (
        "panels", "fold_defs", "eps", "records", "rec_by_id",
        "adjacency", "edge_diag", "centers", "panel_to_centers",
        "fold_to_panel", "center_diag",
    )

    def __init__(self, panels, fold_defs, eps=1e-5):
        self.panels = panels or []
        self.fold_defs = fold_defs or []
        self.eps = eps
        self.records = _graffiti_panel_records(self.panels)
        self.rec_by_id = {int(r["id"]): r for r in self.records}
        self.adjacency = {int(r["id"]): [] for r in self.records}
        self.edge_diag = []
        self.centers = {}
        self.panel_to_centers = {int(r["id"]): [] for r in self.records}
        self.fold_to_panel = {}
        self.center_diag = []
        if self.records and self.fold_defs:
            self.adjacency, self.edge_diag = _graffiti_build_panel_adjacency(self.records, self.fold_defs, eps=eps)
            self.centers, self.panel_to_centers, self.fold_to_panel, self.center_diag = _graffiti_panel_centerpoint_membership(self.records, self.fold_defs, eps=eps)

    @property
    def has_adjacency(self):
        return any(self.adjacency.values())


def _graffiti_panel_state(panels, fold_defs, eps=1e-5):
    return _GraffitiPanelState(panels, fold_defs, eps=eps)


def _graffiti_single_fold_centerpoint_basis_candidates(pt):
    """Return alternate 2D FoldInfo centerpoint bases for one-fold fallback.

    A few one-fold graffiti appear to store FoldInfo CenterPoint in a rotated or
    mirrored 2D basis relative to the final panel polygons.  Keep this as a
    fallback only: callers should use these candidates only after the authored
    point fails final-leaf containment.
    """
    try:
        x = float(pt.x)
        y = float(pt.y)
    except Exception:
        return []
    raw = [
        ("identity", (x, y)),
        ("rot90_ccw", (-y, x)),
        ("rot90_cw", (y, -x)),
        ("rot180", (-x, -y)),
        ("swap_xy", (y, x)),
        ("swap_xy_neg", (-y, -x)),
        ("mirror_y", (x, -y)),
        ("mirror_x", (-x, y)),
    ]
    out = []
    seen = set()
    for name, pair in raw:
        key = (round(pair[0], 8), round(pair[1], 8))
        if key in seen:
            continue
        seen.add(key)
        out.append((name, Vector((pair[0], pair[1]))))
    return out


def _graffiti_choose_single_fold_centerpoint_basis(original_center, records, eps=1e-5):
    """Choose a transformed centerpoint for one-fold graffiti if original misses.

    Returns (center, basis_name, chosen_panel_id, diagnostic).  Only strict
    final-leaf containment is considered viable.  When several candidate bases
    are viable, choose the one whose containing final panel is smallest, matching
    the observation that the authored CenterPoint usually marks the folded flap.
    """
    diag = {
        "basis_fallback_attempted": False,
        "basis_fallback_reason": None,
        "basis_candidates": [],
        "basis_selected": None,
    }
    if original_center is None or not records:
        diag["basis_fallback_reason"] = "missing_center_or_records"
        return original_center, "identity", None, diag

    # First report whether the original point is actually outside final panels.
    original_strict = []
    original_boundary = []
    for r in records or []:
        try:
            if _graffiti_point_in_poly(original_center, r["poly"], eps=eps):
                original_boundary.append(int(r["id"]))
            if _graffiti_point_in_poly_strict(original_center, r["poly"], eps=eps):
                original_strict.append(int(r["id"]))
        except Exception:
            pass
    diag["original_strict_containing_panels"] = list(original_strict)
    diag["original_boundary_containing_panels"] = list(original_boundary)
    if original_strict or original_boundary:
        diag["basis_fallback_reason"] = "original_centerpoint_contained"
        return original_center, "identity", None, diag

    diag["basis_fallback_attempted"] = True
    rec_by_id = {int(r["id"]): r for r in records or []}
    viable = []
    for basis_name, cand in _graffiti_single_fold_centerpoint_basis_candidates(original_center):
        strict = []
        boundary = []
        nearest = None
        nearest_dist = None
        for r in records or []:
            try:
                dist = float((r["centroid"] - cand).length)
                if nearest_dist is None or dist < nearest_dist:
                    nearest_dist = dist
                    nearest = int(r["id"])
                if _graffiti_point_in_poly(cand, r["poly"], eps=eps):
                    boundary.append(int(r["id"]))
                if _graffiti_point_in_poly_strict(cand, r["poly"], eps=eps):
                    strict.append(int(r["id"]))
            except Exception:
                pass
        areas = {int(pid): float(rec_by_id[int(pid)].get("area", 0.0)) for pid in strict if int(pid) in rec_by_id}
        diag["basis_candidates"].append({
            "basis": basis_name,
            "center_point": (round(float(cand.x), 6), round(float(cand.y), 6)),
            "strict_containing_panels": list(strict),
            "boundary_inclusive_containing_panels": list(boundary),
            "nearest_panel": nearest,
            "nearest_distance": None if nearest_dist is None else round(float(nearest_dist), 6),
            "strict_panel_areas": {int(k): round(float(v), 6) for k, v in areas.items()},
        })
        for pid in strict:
            if int(pid) in rec_by_id:
                viable.append((float(rec_by_id[int(pid)].get("area", 0.0)), basis_name, cand, int(pid)))

    if viable:
        viable.sort(key=lambda item: (item[0], item[1], item[3]))
        area, basis_name, cand, pid = viable[0]
        diag["basis_selected"] = {
            "basis": basis_name,
            "assigned_center_panel": int(pid),
            "panel_area": round(float(area), 6),
            "center_point": (round(float(cand.x), 6), round(float(cand.y), 6)),
            "selection_rule": "smallest_strict_containing_panel",
        }
        diag["basis_fallback_reason"] = "selected_viable_basis_after_original_outside_final_panels"
        return cand, basis_name, int(pid), diag

    diag["basis_fallback_reason"] = "no_viable_basis_candidate"
    return original_center, "identity", None, diag

def _graffiti_panel_centerpoint_membership(records, fold_defs, eps=1e-5):
    """Map fold centerpoints to finalized leaf panel records.

    This must run only after every FoldInfo seam has split the original graffiti
    rectangle into final leaf polygons.  Assignment uses strict polygon interior
    containment first; seam-inclusive containment is retained only as a
    diagnostic because it can legitimately include multiple panels that share a
    boundary at/near the centerpoint.
    """
    leaf_records = list(records or [])
    centers = {}
    panel_to_centers = {int(r["id"]): [] for r in leaf_records}
    fold_to_panel = {}
    diagnostics = []
    single_fold_basis_fallback = len(fold_defs or []) == 1
    for fd in fold_defs or []:
        try:
            idx = int(fd.get("index", len(centers)))
        except Exception:
            idx = len(centers)
        original_c = _graffiti_fold_center_point_for_fd(fd)
        c = original_c
        basis_name = "identity"
        basis_diag = None
        basis_forced_panel = None
        if single_fold_basis_fallback:
            c2, basis_name, basis_forced_panel, basis_diag = _graffiti_choose_single_fold_centerpoint_basis(original_c, leaf_records, eps=eps)
            c = c2
        centers[idx] = c
        containing_strict = []
        containing_boundary_inclusive = []
        interior_scores = {}
        nearest = None
        nearest_dist = None
        for r in leaf_records:
            dist = float((r["centroid"] - c).length) if c is not None else float("inf")
            if c is not None:
                boundary_dist = _graffiti_point_poly_boundary_distance(c, r["poly"])
                if _graffiti_point_in_poly(c, r["poly"], eps=eps):
                    containing_boundary_inclusive.append(int(r["id"]))
                if _graffiti_point_in_poly_strict(c, r["poly"], eps=eps):
                    containing_strict.append(int(r["id"]))
                    interior_scores[int(r["id"])] = float(boundary_dist)
            if nearest_dist is None or dist < nearest_dist:
                nearest_dist = dist
                nearest = int(r["id"])
        if basis_forced_panel is not None and basis_forced_panel in containing_strict:
            chosen = int(basis_forced_panel)
            reason = "single_fold_basis_fallback_smallest_strict_panel"
        elif containing_strict:
            # Prefer the final panel where the centerpoint is most interior; tie-break by area.
            rec_by_id = {int(r["id"]): r for r in leaf_records}
            chosen = max(containing_strict, key=lambda pid: (interior_scores.get(int(pid), 0.0), float(rec_by_id.get(int(pid), {}).get("area", 0.0)), -int(pid)))
            reason = "strict_final_leaf_contains"
        else:
            chosen = nearest
            reason = "nearest_final_leaf_after_no_strict_contains" if nearest is not None else "missing_center"
        if chosen is not None:
            panel_to_centers.setdefault(int(chosen), []).append(idx)
            fold_to_panel[idx] = int(chosen)
        diag_item = {
            "fold_index": idx,
            "center_point": None if c is None else (round(float(c.x), 6), round(float(c.y), 6)),
            "original_center_point": None if original_c is None else (round(float(original_c.x), 6), round(float(original_c.y), 6)),
            "centerpoint_basis": basis_name,
            "membership_scope": "final_leaf_panels_after_all_splits",
            "final_leaf_panel_count": len(leaf_records),
            "strict_containing_panels": list(containing_strict),
            "boundary_inclusive_containing_panels": list(containing_boundary_inclusive),
            # Keep legacy key populated with strict matches so old report readers
            # no longer mistake seam-boundary overlaps for real panel containment.
            "containing_panels": list(containing_strict),
            "nearest_panel": nearest,
            "nearest_distance": None if nearest_dist is None or not math.isfinite(nearest_dist) else round(float(nearest_dist), 6),
            "assigned_center_panel": chosen,
            "assignment_reason": reason,
        }
        if basis_diag is not None:
            diag_item["single_fold_basis_fallback"] = basis_diag
        diagnostics.append(diag_item)
    return centers, panel_to_centers, fold_to_panel, diagnostics


def _graffiti_build_panel_adjacency(records, fold_defs, eps=1e-5):
    adjacency = {int(r["id"]): [] for r in records or []}
    edge_diag = []
    for ia in range(len(records or [])):
        ra = records[ia]
        for ib in range(ia + 1, len(records or [])):
            rb = records[ib]
            shared = _graffiti_shared_edge_overlap_2d(ra["poly"], rb["poly"], eps=eps)
            shared_len = sum(float(x.get("length", 0.0)) for x in shared)
            if shared_len <= eps:
                continue
            fd, seam_score = _graffiti_fold_for_shared_edge(shared, fold_defs, eps=eps)
            if fd is None:
                edge_diag.append({"panels": (ra["id"], rb["id"]), "shared_length": round(shared_len, 6), "fold_index": None, "warning": "no_matching_fold_line"})
                continue
            edge = {"to": rb["id"], "fold": fd, "shared_length": shared_len, "seam_score": seam_score}
            redge = {"to": ra["id"], "fold": fd, "shared_length": shared_len, "seam_score": seam_score}
            adjacency[ra["id"]].append(edge)
            adjacency[rb["id"]].append(redge)
            edge_diag.append({"panels": (ra["id"], rb["id"]), "shared_length": round(shared_len, 6), "fold_index": int(fd.get("index", -1)), "seam_score": round(seam_score, 6)})
    return adjacency, edge_diag


def _graffiti_component_from_edge_side(start_id, blocked_a, blocked_b, adjacency):
    """Return the graph component reachable from start_id without crossing one edge."""
    seen = set()
    stack = [int(start_id)]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for edge in adjacency.get(cur, []):
            nxt = int(edge.get("to"))
            if (cur == int(blocked_a) and nxt == int(blocked_b)) or (cur == int(blocked_b) and nxt == int(blocked_a)):
                continue
            if nxt not in seen:
                stack.append(nxt)
    return seen


def _graffiti_choose_centerpoint_anchor(records, fold_defs, panel_to_centers, fold_to_panel, adjacency):
    """Choose panel 0: the panel containing no FoldInfo centerpoints."""
    if not records:
        return None, "no_valid_panels", []
    zero = [r for r in records if not panel_to_centers.get(int(r["id"]))]
    # Prefer a no-centerpoint panel adjacent to fold 0 but not containing fold 0's centerpoint.
    fold0_center_panel = fold_to_panel.get(0)
    fold0_adjacent = set()
    for r in records:
        rid = int(r["id"])
        for edge in adjacency.get(rid, []):
            try:
                if int((edge.get("fold") or {}).get("index", -9999)) == 0:
                    fold0_adjacent.add(rid)
                    fold0_adjacent.add(int(edge.get("to")))
            except Exception:
                pass
    candidates = [r for r in zero if int(r["id"]) in fold0_adjacent and int(r["id"]) != fold0_center_panel]
    if candidates:
        best = max(candidates, key=lambda r: (float(r["area"]), -int(r["id"])))
        return best, "no_centerpoint_fold0_adjacent_not_fold0_center", [int(r["id"]) for r in zero]
    if zero:
        # If there is no fold0-adjacent zero-center panel, use the largest no-centerpoint panel.
        best = max(zero, key=lambda r: (float(r["area"]), -int(r["id"])))
        return best, "largest_no_centerpoint_panel", [int(r["id"]) for r in zero]
    # Conservative fallback for malformed data.
    best = max(records, key=lambda r: (float(r["area"]), -int(r["id"])))
    return best, "fallback_largest_panel_no_zero_centerpoint", []





def _graffiti_poly_area(poly):
    try:
        area = 0.0
        n = len(poly or [])
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            area += a.x * b.y - b.x * a.y
        return abs(area) * 0.5
    except Exception:
        return 0.0


def _graffiti_poly_centroid(poly):
    try:
        pts = poly or []
        if not pts:
            return Vector((0.0, 0.0))
        sx = sum(float(p.x) for p in pts)
        sy = sum(float(p.y) for p in pts)
        return Vector((sx / len(pts), sy / len(pts)))
    except Exception:
        return Vector((0.0, 0.0))


def _graffiti_point_in_poly(pt, poly, eps=1e-6):
    try:
        pts = poly or []
        if len(pts) < 3:
            return False
        # Treat points on boundaries as contained.
        for i in range(len(pts)):
            a = pts[i]
            b = pts[(i + 1) % len(pts)]
            ab = b - a
            ap = pt - a
            if ab.length > eps:
                t = max(0.0, min(1.0, ap.dot(ab) / ab.dot(ab)))
                closest = a + ab * t
                if (closest - pt).length <= eps * 10.0:
                    return True
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            pi = pts[i]
            pj = pts[j]
            if ((pi.y > pt.y) != (pj.y > pt.y)):
                x_inter = (pj.x - pi.x) * (pt.y - pi.y) / max((pj.y - pi.y), eps) + pi.x
                if pt.x < x_inter:
                    inside = not inside
            j = i
        return inside
    except Exception:
        return False



def _graffiti_point_segment_distance_2d(pt, a, b):
    try:
        ab = b - a
        if ab.length <= 1e-12:
            return float((pt - a).length)
        t = max(0.0, min(1.0, (pt - a).dot(ab) / ab.dot(ab)))
        closest = a + ab * t
        return float((closest - pt).length)
    except Exception:
        return float("inf")


def _graffiti_point_poly_boundary_distance(pt, poly):
    try:
        pts = poly or []
        if len(pts) < 2:
            return float("inf")
        return min(_graffiti_point_segment_distance_2d(pt, pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))
    except Exception:
        return float("inf")


def _graffiti_point_in_poly_strict(pt, poly, eps=1e-6):
    """Return True only when pt is inside the finalized leaf polygon interior.

    Unlike _graffiti_point_in_poly(), boundary/seam hits are intentionally not
    treated as containment.  FoldInfo CenterPoint assignment needs exactly one
    finalized panel whenever possible; seam-inclusive tests can report several
    adjacent panels for a point near a hinge or hinge intersection.
    """
    try:
        if pt is None:
            return False
        pts = poly or []
        if len(pts) < 3:
            return False
        if _graffiti_point_poly_boundary_distance(pt, pts) <= eps:
            return False
        # Standard ray test after excluding boundary points.
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            pi = pts[i]
            pj = pts[j]
            if ((pi.y > pt.y) != (pj.y > pt.y)):
                denom = (pj.y - pi.y)
                if abs(denom) <= eps:
                    j = i
                    continue
                x_inter = (pj.x - pi.x) * (pt.y - pi.y) / denom + pi.x
                if pt.x < x_inter:
                    inside = not inside
            j = i
        return bool(inside)
    except Exception:
        return False



def _graffiti_cross2(a, b):
    try:
        return float(a.x) * float(b.y) - float(a.y) * float(b.x)
    except Exception:
        return 0.0


def _graffiti_segment_overlap_length_2d(a, b, c, d, eps=1e-5):
    """Return overlapping length for nearly-colinear 2D segments."""
    try:
        ab = b - a
        cd = d - c
        lab = ab.length
        lcd = cd.length
        if lab <= eps or lcd <= eps:
            return 0.0
        # Parallel and colinear checks, scaled by segment length.
        if abs(_graffiti_cross2(ab, cd)) > eps * max(lab, lcd, 1.0):
            return 0.0
        if abs(_graffiti_cross2(ab, c - a)) > eps * max(lab, 1.0):
            return 0.0
        if abs(_graffiti_cross2(ab, d - a)) > eps * max(lab, 1.0):
            return 0.0
        u = ab.normalized()
        a0 = 0.0
        a1 = lab
        c0 = (c - a).dot(u)
        c1 = (d - a).dot(u)
        lo = max(min(a0, a1), min(c0, c1))
        hi = min(max(a0, a1), max(c0, c1))
        return max(0.0, float(hi - lo))
    except Exception:
        return 0.0


def _graffiti_line_segment_overlap_length_2d(line_p, line_dir, a, b, eps=1e-5):
    """Return segment length that lies on an infinite line."""
    try:
        ab = b - a
        lab = ab.length
        if lab <= eps:
            return 0.0
        d = line_dir.copy()
        if d.length <= eps:
            return 0.0
        d.normalize()
        if abs(_graffiti_cross2(d, ab)) > eps * max(lab, 1.0):
            return 0.0
        if abs(_graffiti_cross2(d, a - line_p)) > eps * max(lab, 1.0):
            return 0.0
        if abs(_graffiti_cross2(d, b - line_p)) > eps * max(lab, 1.0):
            return 0.0
        return float(lab)
    except Exception:
        return 0.0


def _graffiti_panel_topology_diagnostics(panels, fold_defs=None, eps=1e-5, panel_state=None):
    """Summarize panel adjacency/connectivity immediately after panelization.

    This is diagnostic-only.  It does not change fold propagation.  It is meant
    to catch cases where arbitrary seam splitting creates visually disconnected
    panel islands before any fold rotations are applied.
    """
    try:
        valid = []
        for i, panel in enumerate(panels or []):
            poly = _graffiti_clean_poly(panel.get("poly") or [])
            if len(poly) >= 3:
                valid.append({
                    "id": int(i),
                    "poly": poly,
                    "area": float(_graffiti_poly_area(poly)),
                    "centroid": _graffiti_poly_centroid(poly),
                })
        n = len(valid)
        adjacency = {p["id"]: [] for p in valid}
        edge_links = []
        for ia in range(n):
            pa = valid[ia]
            ea = [(pa["poly"][k], pa["poly"][(k + 1) % len(pa["poly"])]) for k in range(len(pa["poly"]))]
            for ib in range(ia + 1, n):
                pb = valid[ib]
                eb = [(pb["poly"][k], pb["poly"][(k + 1) % len(pb["poly"])]) for k in range(len(pb["poly"]))]
                overlap_total = 0.0
                overlap_edges = 0
                for a, b in ea:
                    for c, d in eb:
                        ol = _graffiti_segment_overlap_length_2d(a, b, c, d, eps=eps)
                        if ol > eps:
                            overlap_total += ol
                            overlap_edges += 1
                if overlap_total > eps:
                    rec_a = {"panel": pb["id"], "overlap_edges": int(overlap_edges), "overlap_length": round(float(overlap_total), 6)}
                    rec_b = {"panel": pa["id"], "overlap_edges": int(overlap_edges), "overlap_length": round(float(overlap_total), 6)}
                    adjacency[pa["id"]].append(rec_a)
                    adjacency[pb["id"]].append(rec_b)
                    edge_links.append({"panels": (pa["id"], pb["id"]), "overlap_edges": int(overlap_edges), "overlap_length": round(float(overlap_total), 6)})
        seen = set()
        comps = []
        for p in valid:
            pid = p["id"]
            if pid in seen:
                continue
            stack = [pid]
            seen.add(pid)
            comp = []
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in adjacency.get(cur, []):
                    nid = int(nb.get("panel"))
                    if nid not in seen:
                        seen.add(nid)
                        stack.append(nid)
            comps.append(sorted(comp))
        comps.sort(key=lambda c: (len(c), c), reverse=True)
        seam_links = []
        for fd in fold_defs or []:
            linked = []
            for p in valid:
                poly = p["poly"]
                overlap_total = 0.0
                overlap_edges = 0
                for k in range(len(poly)):
                    ol = _graffiti_line_segment_overlap_length_2d(fd.get("p0_2d", Vector((0.0, 0.0))), fd.get("axis_dir_2d", Vector((1.0, 0.0))), poly[k], poly[(k + 1) % len(poly)], eps=eps)
                    if ol > eps:
                        overlap_total += ol
                        overlap_edges += 1
                if overlap_total > eps:
                    linked.append({"panel": p["id"], "overlap_edges": int(overlap_edges), "overlap_length": round(float(overlap_total), 6)})
            seam_links.append({
                "fold_index": int(fd.get("index", -1)),
                "linked_panel_count": len(linked),
                "linked_panels": linked,
                "warning": "expected_two_neighbor_panels" if len(linked) not in (0, 2) else None,
            })
        isolated = [pid for pid, links in adjacency.items() if not links]
        warnings = []
        if len(comps) > 1:
            warnings.append("panel_graph_disconnected")
        if isolated:
            warnings.append("isolated_panels_present")
        bad_seams = [s.get("fold_index") for s in seam_links if s.get("warning")]
        if bad_seams:
            warnings.append("seam_links_not_two_panels")
        return {
            "panel_count": int(n),
            "edge_link_count": int(len(edge_links)),
            "connected_component_count": int(len(comps)),
            "connected_components": comps,
            "isolated_panels": isolated,
            "disconnected": bool(len(comps) > 1),
            "adjacency": {int(k): v for k, v in adjacency.items()},
            "edge_links": edge_links,
            "seam_links": seam_links,
            "seam_link_warnings": bad_seams,
            "panel_centroids": {p["id"]: (round(float(p["centroid"].x), 6), round(float(p["centroid"].y), 6)) for p in valid},
            "panel_areas": {p["id"]: round(float(p["area"]), 6) for p in valid},
            "warnings": warnings,
        }
    except Exception as ex:
        return {"error": str(ex)}

def _graffiti_choose_anchor_panel(panels, fold_defs):
    """Choose which final panel preserves the original flat graffiti plane.

    Prefer a panel containing CenterPointX/Y from FoldInfo when available, with
    largest area breaking ties.  If no center point is usable, preserve the
    largest panel, matching the observation that the pre-fold flat decal often
    aligns with the eventual largest folded panel.
    """
    cleaned = []
    for i, panel in enumerate(panels or []):
        poly = _graffiti_clean_poly(panel.get("poly") or [])
        if len(poly) >= 3:
            cleaned.append((i, panel, poly, _graffiti_poly_area(poly), _graffiti_poly_centroid(poly)))
    if not cleaned:
        return None, "none", None
    centers = _graffiti_fold_center_points(fold_defs)
    if centers:
        best = None
        best_score = None
        for i, panel, poly, area, centroid in cleaned:
            contains = any(_graffiti_point_in_poly(c, poly, eps=1e-5) for c in centers)
            dist = min((centroid - c).length for c in centers)
            # containing panels beat non-containing panels; larger area breaks ties.
            score = (0 if contains else 1, round(dist, 6), -area)
            if best_score is None or score < best_score:
                best_score = score
                best = (i, panel, poly, area, centroid)
        if best is not None:
            return best[1], "centerpoint_contains_or_nearest", [(round(c.x, 6), round(c.y, 6)) for c in centers]
    best = max(cleaned, key=lambda x: x[3])
    return best[1], "largest_panel", None




def _graffiti_panel_normal_from_verts(points):
    """Return a simple polygon normal from folded panel vertices."""
    try:
        pts = [Vector((float(v[0]), float(v[1]), float(v[2]))) for v in (points or [])]
        if len(pts) < 3:
            return Vector((0.0, 0.0, 1.0))
        normal = Vector((0.0, 0.0, 0.0))
        n = len(pts)
        for i in range(n):
            a = pts[i]
            b = pts[(i + 1) % n]
            normal.x += (a.y - b.y) * (a.z + b.z)
            normal.y += (a.z - b.z) * (a.x + b.x)
            normal.z += (a.x - b.x) * (a.y + b.y)
        if normal.length <= 1e-8:
            a = pts[1] - pts[0]
            b = pts[2] - pts[0]
            normal = a.cross(b)
        if normal.length <= 1e-8:
            return Vector((0.0, 0.0, 1.0))
        normal.normalize()
        return normal
    except Exception:
        return Vector((0.0, 0.0, 1.0))


def _graffiti_panel_fold_diagnostic(poly, rotations, folded_points=None):
    """Diagnostic comparing final folded panel vertices against the original flat plane."""
    try:
        flat_points = [(float(p.x), float(p.y), 0.0) for p in (poly or [])]
        if folded_points is None:
            folded_points = [_graffiti_apply_fold_rotations(p, rotations or []) for p in (poly or [])]
        max_delta = 0.0
        for f, g in zip(flat_points, folded_points or []):
            dv = Vector((float(g[0]) - f[0], float(g[1]) - f[1], float(g[2]) - f[2]))
            max_delta = max(max_delta, dv.length)
        normal_after = _graffiti_panel_normal_from_verts(folded_points)
        normal_before = Vector((0.0, 0.0, 1.0))
        try:
            delta_deg = math.degrees(normal_before.angle(normal_after))
        except Exception:
            delta_deg = 0.0
        return {
            "max_delta_from_flat": round(float(max_delta), 6),
            "normal_before": (0.0, 0.0, 1.0),
            "normal_after": tuple(round(float(v), 6) for v in normal_after),
            "normal_delta_deg": round(float(delta_deg), 6),
        }
    except Exception as ex:
        return {"error": str(ex)}


def _graffiti_normalize_parallel_fold_defs(fold_defs):
    """Prepare near-parallel fold defs without changing authored direction/sign.

    Earlier revisions flipped reversed seam endpoints to match a reference axis.
    That made sorting easier, but it also changed the sign convention consumed by
    _graffiti_effective_fold_angle() and could make multi-fold graffiti such as
    Cmn_0046 bend the same way twice.  For 0.7.51, keep authored P0/P1,
    IsRightFold, and angle interpretation intact; only report whether a seam is
    anti-parallel to the first seam.  Sorting code may use a reference normal,
    but the fold transform itself must remain raw-signed.
    """
    if not fold_defs:
        return fold_defs, []
    ref = fold_defs[0].get("axis_dir_2d", Vector((1.0, 0.0))).copy()
    if ref.length <= 1e-6:
        ref = Vector((1.0, 0.0))
    ref.normalize()
    notes = []
    out = []
    for fd in fold_defs:
        fd2 = dict(fd)
        d = fd2.get("axis_dir_2d", ref).copy()
        if d.length <= 1e-6:
            d = ref.copy()
        d.normalize()
        antiparallel = bool(d.dot(ref) < 0.0)
        out.append(fd2)
        notes.append({
            "index": fd2.get("index"),
            "axis_antiparallel_to_chain_ref": antiparallel,
            "axis_flipped_to_chain_ref": False,
            "raw_axis_direction_preserved": True,
            "raw_angle_sign_preserved": True,
        })
    return out, notes


def _graffiti_assign_cumulative_parallel_chain(panels, fold_defs, anchor_panel=None):
    """Assign cumulative rotations using projection-sorted seam adjacency.

    FoldInfo array order is treated as metadata, not adjacency.  The geometric
    chain is derived by projecting seam centers and panel centroids onto the
    chain axis perpendicular to the fold seams.

    Anchor selection for this branch test:
      * If fold 0 is at either end of the projection-sorted seam chain, anchor
        the outer panel adjacent to fold 0 ("panel 0" in authored strip terms).
      * If fold 0 is interior, find fold 1's side relative to fold 0 and choose
        the panel neighboring fold 0 on the opposite side as the anchor.  Then
        grow two cumulative branches outward from that interior anchor.

    IsRightFold remains ignored here: raw authored angles are applied directly.
    """
    if not panels or not fold_defs:
        return False, "missing_panels_or_folds", []
    try:
        ref = fold_defs[0]["axis_dir_2d"].copy()
        if ref.length <= 1e-6:
            return False, "bad_reference_axis", []
        ref.normalize()
        n = Vector((-ref.y, ref.x))

        # Sort seams by their geometric position, not FoldInfo array index.
        seam_data = [(float(fd["p0_2d"].dot(n)), fd) for fd in fold_defs]
        seam_data.sort(key=lambda x: x[0])
        seam_projection_by_index = {int(fd.get("index", i)): float(sp) for i, (sp, fd) in enumerate(seam_data)}
        seam_order_by_projection = [int(fd.get("index", i)) for i, (_sp, fd) in enumerate(seam_data)]

        panel_data = []
        for panel in panels:
            poly = _graffiti_clean_poly(panel.get("poly") or [])
            if len(poly) < 3:
                continue
            proj = float(_graffiti_poly_centroid(poly).dot(n))
            area = float(_graffiti_poly_area(poly))
            panel_data.append((proj, area, panel))
        if not panel_data:
            return False, "no_valid_panels", []
        panel_data.sort(key=lambda x: x[0])

        min_panel = panel_data[0][2]
        max_panel = panel_data[-1][2]
        fold0_pos = seam_projection_by_index.get(0)
        if fold0_pos is None:
            # Unusual data: fall back to first fold def's projection.
            fold0_pos = float(fold_defs[0]["p0_2d"].dot(n))
        fold0_rank = None
        for i, (_sp, fd) in enumerate(seam_data):
            if int(fd.get("index", -999999)) == 0:
                fold0_rank = i
                break
        if fold0_rank is None:
            fold0_rank = 0

        fold1_pos = seam_projection_by_index.get(1)
        anchor_reason = None
        anchor_branch_mode = "fold0_endpoint"

        def _closest_panel(predicate, key_func):
            candidates = [(proj, area, panel) for proj, area, panel in panel_data if predicate(proj)]
            if not candidates:
                return None
            return sorted(candidates, key=lambda item: key_func(item[0]))[0][2]

        if fold0_rank <= 0:
            # Fold 0 is the minimum-projection end seam: anchor the outer panel
            # below it.  If centroid tolerances fail, use the minimum panel.
            step_anchor = _closest_panel(lambda pr: pr < fold0_pos - 1e-8, lambda pr: abs(pr - fold0_pos)) or min_panel
            anchor_reason = "fold0_min_endpoint_outer_panel"
        elif fold0_rank >= len(seam_data) - 1:
            # Fold 0 is the maximum-projection end seam: anchor the outer panel
            # above it.
            step_anchor = _closest_panel(lambda pr: pr > fold0_pos + 1e-8, lambda pr: abs(pr - fold0_pos)) or max_panel
            anchor_reason = "fold0_max_endpoint_outer_panel"
        else:
            # Fold 0 is interior.  Find the side of fold 1 relative to fold 0;
            # choose the fold-0-neighboring panel on the opposite side as the
            # fixed anchor, producing two branches that grow outward from it.
            anchor_branch_mode = "fold0_interior_two_branch"
            if fold1_pos is not None and fold1_pos < fold0_pos:
                step_anchor = _closest_panel(lambda pr: pr > fold0_pos + 1e-8, lambda pr: abs(pr - fold0_pos)) or max_panel
                anchor_reason = "fold0_interior_anchor_opposite_fold1_positive_side"
            elif fold1_pos is not None and fold1_pos > fold0_pos:
                step_anchor = _closest_panel(lambda pr: pr < fold0_pos - 1e-8, lambda pr: abs(pr - fold0_pos)) or min_panel
                anchor_reason = "fold0_interior_anchor_opposite_fold1_negative_side"
            else:
                # If fold 1 is missing or coincident, choose the larger adjacent
                # panel around fold 0 as a conservative fallback.
                neg = _closest_panel(lambda pr: pr < fold0_pos - 1e-8, lambda pr: abs(pr - fold0_pos))
                pos = _closest_panel(lambda pr: pr > fold0_pos + 1e-8, lambda pr: abs(pr - fold0_pos))
                if neg is not None and pos is not None:
                    neg_area = next((a for pr, a, pan in panel_data if pan is neg), 0.0)
                    pos_area = next((a for pr, a, pan in panel_data if pan is pos), 0.0)
                    step_anchor = neg if neg_area >= pos_area else pos
                else:
                    step_anchor = neg or pos or min_panel
                anchor_reason = "fold0_interior_adjacent_larger_panel_fallback"

        anchor_poly = _graffiti_clean_poly(step_anchor.get("poly") or [])
        anchor_proj = float(_graffiti_poly_centroid(anchor_poly).dot(n)) if anchor_poly else float(panel_data[0][0])
        anchor_on_min_side = anchor_proj <= (panel_data[0][0] + panel_data[-1][0]) * 0.5

        seam_projection_map = {int(fd.get("index", i)): round(float(sp), 6) for i, (sp, fd) in enumerate(seam_data)}
        diag = []

        for panel_order, (panel_proj, _area, panel) in enumerate(panel_data):
            rotations = []
            crossed = []
            if abs(panel_proj - anchor_proj) <= 1e-8:
                panel["rotations"] = []
                panel["chain_projection"] = float(panel_proj)
                panel["chain_panel_order"] = int(panel_order)
                panel["chain_crossed_seams"] = []
                panel["chain_anchor_panel"] = bool(panel is step_anchor)
                diag.append({
                    "panel_order": int(panel_order),
                    "panel_projection": round(float(panel_proj), 6),
                    "panel_area": round(float(_area), 6),
                    "anchor_projection": round(float(anchor_proj), 6),
                    "anchor_on_min_side": bool(anchor_on_min_side),
                    "fold0_projection_rank": int(fold0_rank),
                    "fold0_projection": round(float(fold0_pos), 6),
                    "fold1_projection": None if fold1_pos is None else round(float(fold1_pos), 6),
                    "seam_projections": dict(seam_projection_map),
                    "seam_order_by_projection": list(seam_order_by_projection),
                    "crossed_seams": [],
                    "parent_panel_order": None,
                    "rotation_count": 0,
                    "step_chain_anchor": True,
                    "anchor_reason": anchor_reason,
                    "anchor_branch_mode": anchor_branch_mode,
                    "chain_mode": "projection_order_fold0_anchor_two_branch_ignore_isrightfold_inverted",
                })
                continue

            # Generic branch traversal from the selected anchor.  This works for
            # both endpoint anchors and interior anchors: panels on either side
            # cross only the seams between their projection and the anchor.
            if panel_proj > anchor_proj:
                crossings = [(sp, fd) for sp, fd in seam_data if sp > anchor_proj + 1e-8 and sp <= panel_proj + 1e-8]
                crossings = sorted({id(fd): (sp, fd) for sp, fd in crossings}.values(), key=lambda x: x[0])
            else:
                crossings = [(sp, fd) for sp, fd in seam_data if sp < anchor_proj - 1e-8 and sp >= panel_proj - 1e-8]
                crossings = sorted({id(fd): (sp, fd) for sp, fd in crossings}.values(), key=lambda x: x[0], reverse=True)

            prev_rots = []
            for seam_proj, fd in crossings:
                fd2 = dict(fd)
                raw_rotate_positive = bool(fd2.get("rotate_positive_side", False))
                panel_is_positive = bool(panel_proj >= seam_proj)
                anchor_is_positive = bool(anchor_proj >= seam_proj)

                # Ignore IsRightFold and original rotate-positive selection in
                # this cumulative-chain test.  Force _graffiti_effective_fold_angle()
                # to use the transform-side convention by making the effective
                # side positive.  The multi-fold handedness is currently globally
                # inverted relative to the authored raw angle, so store the
                # original authored angle for diagnostics and negate the angle
                # used by vertex deformation.  Moving panel selection is already
                # determined by projection-crossing membership above.
                fd2["chain_authored_angle"] = float(fd2.get("angle", 0.0))
                fd2["angle"] = -float(fd2.get("angle", 0.0))
                fd2["chain_multifold_direction_inverted"] = True
                fd2["rotate_positive_side"] = True
                fd2["rotated_side"] = "projection_chain_downstream"
                fd2["chain_ignore_isrightfold"] = True
                fd2["chain_projection_order_mode"] = True
                fd2["chain_panel_side_rule"] = "projection_order_fold0_anchor_two_branch"
                fd2["chain_raw_rotated_side"] = "positive" if raw_rotate_positive else "negative"
                fd2["chain_effective_rotated_side"] = "projection_chain_downstream"
                fd2["chain_rotate_positive_overridden"] = bool(not raw_rotate_positive)
                fd2["chain_multifold_isrightfold_inverted"] = False
                fd2["chain_parent_local_isrightfold_mode"] = False
                fd2["chain_raw_signed_angle_preserved"] = True
                fd2["chain_panel_side"] = "positive" if panel_is_positive else "negative"
                fd2["chain_anchor_side"] = "positive" if anchor_is_positive else "negative"
                fd2["chain_seam_projection"] = float(seam_proj)
                fd2["chain_anchor_branch_mode"] = anchor_branch_mode

                # Transform later hinge axes by earlier crossed rotations so the
                # next seam lives in the already-folded parent frame.
                p0 = fd2.get("p0_3d", Vector((fd2["p0_2d"].x, fd2["p0_2d"].y, 0.0)))
                p1 = fd2.get("p1_3d", Vector((fd2["p1_2d"].x, fd2["p1_2d"].y, 0.0)))
                p0t = Vector((p0.x, p0.y, p0.z))
                p1t = Vector((p1.x, p1.y, p1.z))
                for prev in prev_rots:
                    p0t = _graffiti_rotate_point_around_axis(p0t, prev["p0_3d"], prev["axis_dir_3d"], _graffiti_effective_fold_angle(prev))
                    p1t = _graffiti_rotate_point_around_axis(p1t, prev["p0_3d"], prev["axis_dir_3d"], _graffiti_effective_fold_angle(prev))
                axis3 = p1t - p0t
                if axis3.length <= 1e-6:
                    axis3 = fd2.get("axis_dir_3d", Vector((1.0, 0.0, 0.0))).copy()
                axis3.normalize()
                fd2["p0_3d"] = p0t
                fd2["p1_3d"] = p1t
                fd2["axis_dir_3d"] = axis3
                fd2["chain_axis_transformed"] = bool(prev_rots)
                rotations.append(fd2)
                prev_rots.append(fd2)
                crossed.append(fd2.get("index"))

            panel["rotations"] = rotations
            panel["chain_projection"] = float(panel_proj)
            panel["chain_panel_order"] = int(panel_order)
            panel["chain_crossed_seams"] = crossed
            panel["chain_anchor_panel"] = bool(panel is step_anchor)
            if panel_proj > anchor_proj:
                parent_panel_order = panel_order - 1
            else:
                parent_panel_order = panel_order + 1
            if parent_panel_order < 0 or parent_panel_order >= len(panel_data):
                parent_panel_order = None
            diag.append({
                "panel_order": int(panel_order),
                "panel_projection": round(float(panel_proj), 6),
                "panel_area": round(float(_area), 6),
                "anchor_projection": round(float(anchor_proj), 6),
                "anchor_on_min_side": bool(anchor_on_min_side),
                "fold0_projection_rank": int(fold0_rank),
                "fold0_projection": round(float(fold0_pos), 6),
                "fold1_projection": None if fold1_pos is None else round(float(fold1_pos), 6),
                "seam_projections": dict(seam_projection_map),
                "seam_order_by_projection": list(seam_order_by_projection),
                "crossed_seams": list(crossed),
                "parent_panel_order": parent_panel_order,
                "rotation_count": len(rotations),
                "step_chain_anchor": bool(panel is step_anchor),
                "anchor_reason": anchor_reason if panel is step_anchor else None,
                "anchor_branch_mode": anchor_branch_mode,
                "chain_mode": "projection_order_fold0_anchor_two_branch_ignore_isrightfold_inverted",
                "axes_transformed": [bool(r.get("chain_axis_transformed")) for r in rotations],
                "raw_angle_deg": [round(math.degrees(float(r.get("chain_authored_angle", r.get("angle", 0.0)))), 6) for r in rotations],
                "effective_angle_deg": [round(math.degrees(_graffiti_effective_fold_angle(r)), 6) for r in rotations],
                "angle_inverted_for_multifold": [bool(r.get("chain_multifold_direction_inverted", False)) for r in rotations],
                "is_right_fold": [bool(r.get("is_right_fold", False)) for r in rotations],
                "isrightfold_ignored": [bool(r.get("chain_ignore_isrightfold", False)) for r in rotations],
                "angle_inverted_by_isrightfold": [False for _r in rotations],
                "raw_rotated_side": [r.get("chain_raw_rotated_side") for r in rotations],
                "effective_rotated_side": [r.get("chain_effective_rotated_side") for r in rotations],
                "panel_side": [r.get("chain_panel_side") for r in rotations],
                "anchor_side": [r.get("chain_anchor_side") for r in rotations],
                "panel_side_rule": [r.get("chain_panel_side_rule") for r in rotations],
                "parent_panel_local_isrightfold_mode": [bool(r.get("chain_parent_local_isrightfold_mode")) for r in rotations],
                "rotate_positive_overridden": [bool(r.get("chain_rotate_positive_overridden")) for r in rotations],
                "multifold_isrightfold_inverted": [bool(r.get("chain_multifold_isrightfold_inverted")) for r in rotations],
                "axis_dir_3d": [tuple(round(float(v), 6) for v in r.get("axis_dir_3d", Vector((0.0, 0.0, 0.0)))) for r in rotations],
                "axis_origin_3d": [tuple(round(float(v), 6) for v in r.get("p0_3d", Vector((0.0, 0.0, 0.0)))) for r in rotations],
            })
        return True, "cumulative_projection_fold0_anchor_two_branch_ignore_isrightfold_inverted_chain", diag
    except Exception as ex:
        return False, "exception:%s" % ex, []


def _graffiti_shared_edge_overlap_2d(poly_a, poly_b, eps=1e-5):
    """Return shared boundary overlap records between two panel polygons."""
    records = []
    try:
        pa = _graffiti_clean_poly(poly_a or [])
        pb = _graffiti_clean_poly(poly_b or [])
        if len(pa) < 3 or len(pb) < 3:
            return records
        for ia in range(len(pa)):
            a0 = pa[ia]
            a1 = pa[(ia + 1) % len(pa)]
            for ib in range(len(pb)):
                b0 = pb[ib]
                b1 = pb[(ib + 1) % len(pb)]
                ol = _graffiti_segment_overlap_length_2d(a0, a1, b0, b1, eps=eps)
                if ol > eps:
                    records.append({"edge_a": ia, "edge_b": ib, "length": float(ol), "a0": a0, "a1": a1, "b0": b0, "b1": b1})
    except Exception:
        pass
    return records


def _graffiti_fold_for_shared_edge(edge_records, fold_defs, eps=1e-5):
    """Identify which FoldInfo seam owns a shared panel boundary."""
    best_fd = None
    best_len = 0.0
    try:
        for fd in fold_defs or []:
            total = 0.0
            line_p = fd.get("p0_2d", Vector((0.0, 0.0)))
            line_d = fd.get("axis_dir_2d", Vector((1.0, 0.0)))
            for rec in edge_records or []:
                # Either panel edge is enough; for robust diagnostics include both.
                total += _graffiti_line_segment_overlap_length_2d(line_p, line_d, rec.get("a0"), rec.get("a1"), eps=eps)
                total += _graffiti_line_segment_overlap_length_2d(line_p, line_d, rec.get("b0"), rec.get("b1"), eps=eps)
            if total > best_len:
                best_len = float(total)
                best_fd = fd
    except Exception:
        return None, 0.0
    if best_len <= eps:
        return None, 0.0
    return best_fd, best_len


def _graffiti_transform_fold_def_through_parent(fd, parent_rotations, *, mode="graph_parent_panel"):
    """Copy a fold def and move its hinge axis into the already-folded parent frame."""
    fd2 = dict(fd)
    try:
        fd2["chain_authored_angle"] = float(fd2.get("angle", 0.0))
        # Match the currently-working multi-fold sign policy: isRightFold is
        # ignored and the authored angle is globally inverted for multi-fold
        # graffiti. The graph solver changes only which seams are crossed and
        # the frame in which each child seam is evaluated.
        fd2["angle"] = -float(fd2.get("angle", 0.0))
        fd2["chain_multifold_direction_inverted"] = True
    except Exception:
        pass
    fd2["rotate_positive_side"] = True
    fd2["rotated_side"] = mode
    fd2["chain_ignore_isrightfold"] = True
    fd2["chain_graph_adjacency_mode"] = True
    fd2["chain_panel_side_rule"] = "panel_adjacency_graph_parent_frame"
    fd2["chain_effective_rotated_side"] = mode
    fd2["chain_rotate_positive_overridden"] = True
    fd2["chain_parent_local_isrightfold_mode"] = False
    fd2["chain_multifold_isrightfold_inverted"] = False
    fd2["chain_raw_signed_angle_preserved"] = True
    p0 = fd2.get("p0_3d", Vector((fd2.get("p0_2d", Vector((0.0, 0.0))).x, fd2.get("p0_2d", Vector((0.0, 0.0))).y, 0.0)))
    p1 = fd2.get("p1_3d", Vector((fd2.get("p1_2d", Vector((1.0, 0.0))).x, fd2.get("p1_2d", Vector((1.0, 0.0))).y, 0.0)))
    p0t = Vector((float(p0.x), float(p0.y), float(p0.z)))
    p1t = Vector((float(p1.x), float(p1.y), float(p1.z)))
    for prev in parent_rotations or []:
        p0t = _graffiti_rotate_point_around_axis(p0t, prev["p0_3d"], prev["axis_dir_3d"], _graffiti_effective_fold_angle(prev))
        p1t = _graffiti_rotate_point_around_axis(p1t, prev["p0_3d"], prev["axis_dir_3d"], _graffiti_effective_fold_angle(prev))
    axis3 = p1t - p0t
    if axis3.length <= 1e-6:
        axis3 = fd2.get("axis_dir_3d", Vector((1.0, 0.0, 0.0))).copy()
    if axis3.length <= 1e-6:
        axis3 = Vector((1.0, 0.0, 0.0))
    axis3.normalize()
    fd2["p0_3d"] = p0t
    fd2["p1_3d"] = p1t
    fd2["axis_dir_3d"] = axis3
    fd2["chain_axis_transformed"] = bool(parent_rotations)
    return fd2




def _graffiti_assign_centerpoint_outward_chain(panels, fold_defs, anchor_panel=None, panel_state=None):
    """Universal FoldInfo CenterPoint solver for folded graffiti.

    Rules:
    * each FoldInfo CenterPoint identifies the side/panel branch moved by that fold;
    * panel 0 is the panel containing no FoldInfo centerpoints;
    * folding starts at panel 0 and traverses outward through adjacent panels;
    * when panel 0 has multiple adjacent branches, each branch is processed independently.
    """
    if not panels or not fold_defs:
        return False, "missing_panels_or_folds", []
    try:
        state = panel_state or _graffiti_panel_state(panels, fold_defs)
        records = state.records
        if not records:
            return False, "no_valid_panels", []
        adjacency = state.adjacency
        edge_diag = state.edge_diag
        if not state.has_adjacency:
            return False, "no_panel_adjacency_edges", []
        centers = state.centers
        panel_to_centers = state.panel_to_centers
        fold_to_panel = state.fold_to_panel
        center_diag = state.center_diag
        rec_by_id = state.rec_by_id

        anchor_rec = None
        anchor_reason = None
        zero_center_panels = []
        if anchor_panel is not None:
            anchor_rec = next((r for r in records if r["panel"] is anchor_panel), None)
            if anchor_rec is not None and not panel_to_centers.get(int(anchor_rec["id"])):
                anchor_reason = "provided_anchor_no_centerpoint"
            else:
                anchor_rec = None
        if anchor_rec is None:
            anchor_rec, anchor_reason, zero_center_panels = _graffiti_choose_centerpoint_anchor(records, fold_defs, panel_to_centers, fold_to_panel, adjacency)
        if anchor_rec is None:
            return False, "no_anchor_panel", []
        anchor_id = int(anchor_rec["id"])

        for r in records:
            panel = r["panel"]
            panel["rotations"] = []
            panel["chain_crossed_seams"] = []
            panel["chain_anchor_panel"] = False
            panel["chain_graph_parent_panel"] = None
            panel["chain_graph_depth"] = None
            panel["chain_graph_mode"] = True
            panel["chain_centerpoint_mode"] = True
        rec_by_id[anchor_id]["panel"]["chain_anchor_panel"] = True
        rec_by_id[anchor_id]["panel"]["chain_graph_depth"] = 0

        # Choose directed parent/child edges outward from panel 0.  A candidate
        # edge is preferred when the graph component beyond the edge contains
        # that fold's CenterPoint-assigned panel.  This encodes "all panels on
        # the CenterPoint side of the seam are affected".
        seen = {anchor_id}
        parent = {anchor_id: None}
        parent_edge = {}
        depth = {anchor_id: 0}
        queue = [anchor_id]
        traversal_diag = []
        deferred_edges = []

        while queue:
            cur = queue.pop(0)
            edges = sorted(adjacency.get(cur, []), key=lambda e: (int((e.get("fold") or {}).get("index", 999999)), -float(e.get("shared_length", 0.0)), int(e.get("to", -1))))
            for edge in edges:
                nxt = int(edge.get("to"))
                if nxt in seen:
                    continue
                fd = edge.get("fold") or {}
                fold_idx = int(fd.get("index", -1))
                center_panel = fold_to_panel.get(fold_idx)
                comp = _graffiti_component_from_edge_side(nxt, cur, nxt, adjacency)
                center_in_child_component = bool(center_panel in comp) if center_panel is not None else False
                rec = {
                    "from": int(cur),
                    "to": int(nxt),
                    "fold_index": int(fold_idx),
                    "center_panel": center_panel,
                    "child_component": sorted(int(x) for x in comp),
                    "center_in_child_component": bool(center_in_child_component),
                    "accepted": bool(center_in_child_component),
                }
                if center_in_child_component:
                    seen.add(nxt)
                    parent[nxt] = cur
                    parent_edge[nxt] = edge
                    depth[nxt] = depth[cur] + 1
                    queue.append(nxt)
                    traversal_diag.append(rec)
                else:
                    deferred_edges.append((cur, edge, rec))
                    traversal_diag.append(rec)

        # If a graph has ambiguous/cyclic geometry or imperfect centerpoint
        # containment, force remaining panels in deterministic order so imports
        # still complete, but record the fallback clearly.
        forced = []
        progress = True
        while len(seen) < len(records) and progress:
            progress = False
            for cur in sorted(list(seen)):
                for edge in sorted(adjacency.get(cur, []), key=lambda e: (int((e.get("fold") or {}).get("index", 999999)), -float(e.get("shared_length", 0.0)), int(e.get("to", -1)))):
                    nxt = int(edge.get("to"))
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    parent[nxt] = cur
                    parent_edge[nxt] = edge
                    depth[nxt] = depth[cur] + 1
                    queue.append(nxt)
                    forced.append({"from": int(cur), "to": int(nxt), "fold_index": int((edge.get("fold") or {}).get("index", -1)), "reason": "forced_after_centerpoint_directed_traversal_stalled"})
                    progress = True
                    break
                if progress:
                    break
        if len(seen) < len(records):
            return False, "panel_graph_disconnected_from_centerpoint_anchor:%d/%d" % (len(seen), len(records)), []

        # Assign cumulative rotations.  Sibling branches from panel 0 inherit
        # only panel 0's empty rotation list, so each direction is handled in
        # sequence without contaminating other branches.
        ordered_ids = sorted(seen, key=lambda pid: (depth.get(pid, 0), pid))
        for pid in ordered_ids:
            if pid == anchor_id:
                continue
            par = parent.get(pid)
            edge = parent_edge.get(pid) or {}
            parent_panel = rec_by_id[par]["panel"]
            panel = rec_by_id[pid]["panel"]
            parent_rots = list(parent_panel.get("rotations") or [])
            fd = edge.get("fold")
            fd2 = _graffiti_transform_fold_def_through_parent(fd, parent_rots, mode="centerpoint_outward_child")
            fd2["chain_centerpoint_mode"] = True
            fd2["chain_centerpoint_panel0"] = int(anchor_id)
            fd2["chain_graph_parent_panel"] = int(par)
            fd2["chain_graph_child_panel"] = int(pid)
            fd2["chain_graph_depth"] = int(depth.get(pid, 0))
            fd2["chain_graph_shared_length"] = float(edge.get("shared_length", 0.0))
            try:
                fidx = int(fd2.get("index", -1))
                fd2["chain_fold_center_panel"] = fold_to_panel.get(fidx)
                cpt = centers.get(fidx)
                fd2["chain_fold_center_point"] = None if cpt is None else (float(cpt.x), float(cpt.y))
            except Exception:
                pass
            panel["rotations"] = parent_rots + [fd2]
            panel["chain_crossed_seams"] = list(parent_panel.get("chain_crossed_seams") or []) + [fd2.get("index")]
            panel["chain_graph_parent_panel"] = int(par)
            panel["chain_graph_depth"] = int(depth.get(pid, 0))
            panel["chain_anchor_panel"] = False
            panel["chain_centerpoint_mode"] = True

        diag = []
        for order, r in enumerate(records):
            panel = r["panel"]
            rotations = list(panel.get("rotations") or [])
            diag.append({
                "panel_order": int(order),
                "panel_id": int(r["id"]),
                "panel_area": round(float(r["area"]), 6),
                "panel_centroid": (round(float(r["centroid"].x), 6), round(float(r["centroid"].y), 6)),
                "panel_centerpoints": list(panel_to_centers.get(int(r["id"]), [])),
                "contains_any_centerpoint": bool(panel_to_centers.get(int(r["id"]))),
                "step_chain_anchor": bool(r["id"] == anchor_id),
                "anchor_panel_id": int(anchor_id),
                "anchor_reason": anchor_reason if r["id"] == anchor_id else None,
                "zero_centerpoint_panels": list(zero_center_panels),
                "chain_mode": "centerpoint_outward_panel0_universal",
                "parent_panel": panel.get("chain_graph_parent_panel"),
                "graph_depth": panel.get("chain_graph_depth"),
                "crossed_seams": list(panel.get("chain_crossed_seams") or []),
                "rotation_count": len(rotations),
                "raw_angle_deg": [round(math.degrees(float(rr.get("chain_authored_angle", rr.get("angle", 0.0)))), 6) for rr in rotations],
                "effective_angle_deg": [round(math.degrees(_graffiti_effective_fold_angle(rr)), 6) for rr in rotations],
                "angle_inverted_for_multifold": [bool(rr.get("chain_multifold_direction_inverted", False)) for rr in rotations],
                "isrightfold_ignored": [bool(rr.get("chain_ignore_isrightfold", False)) for rr in rotations],
                "axis_dir_3d": [tuple(round(float(v), 6) for v in rr.get("axis_dir_3d", Vector((0.0, 0.0, 0.0)))) for rr in rotations],
                "axis_origin_3d": [tuple(round(float(v), 6) for v in rr.get("p0_3d", Vector((0.0, 0.0, 0.0)))) for rr in rotations],
            })
        summary = {
            "centerpoint_membership": center_diag,
            "edge_diagnostics": edge_diag,
            "directed_traversal": traversal_diag,
            "forced_edges": forced,
            "anchor_panel_id": int(anchor_id),
            "anchor_reason": anchor_reason,
            "zero_centerpoint_panels": list(zero_center_panels),
        }
        diag.append({"chain_mode_summary": summary})
        reason = "centerpoint_outward_panel0_universal"
        if forced:
            reason += "_with_forced_edges"
        return True, reason, diag
    except Exception as ex:
        return False, "exception:%s" % ex, []

def _graffiti_assign_adjacency_graph_chain(panels, fold_defs, anchor_panel=None):
    """Assign fold rotations by traversing the true panel adjacency graph.

    This is intended for multi-fold graffiti whose seams are not all parallel.
    Unlike projection-chain mode, each child hinge axis is first transformed
    through the parent panel's accumulated rotations before the child fold is
    appended. This lets non-parallel branches fold in their parent panel's frame.
    """
    if not panels or not fold_defs:
        return False, "missing_panels_or_folds", []
    try:
        valid = []
        for i, panel in enumerate(panels or []):
            poly = _graffiti_clean_poly(panel.get("poly") or [])
            if len(poly) >= 3:
                valid.append({"id": int(i), "panel": panel, "poly": poly, "area": float(_graffiti_poly_area(poly)), "centroid": _graffiti_poly_centroid(poly)})
        if not valid:
            return False, "no_valid_panels", []
        anchor_rec = None
        if anchor_panel is not None:
            anchor_rec = next((r for r in valid if r["panel"] is anchor_panel), None)
        if anchor_rec is None:
            anchor_rec = max(valid, key=lambda r: r["area"])
        adjacency = {r["id"]: [] for r in valid}
        edge_diag = []
        for ia in range(len(valid)):
            ra = valid[ia]
            for ib in range(ia + 1, len(valid)):
                rb = valid[ib]
                shared = _graffiti_shared_edge_overlap_2d(ra["poly"], rb["poly"])
                shared_len = sum(float(x.get("length", 0.0)) for x in shared)
                if shared_len <= 1e-5:
                    continue
                fd, seam_score = _graffiti_fold_for_shared_edge(shared, fold_defs)
                if fd is None:
                    edge_diag.append({"panels": (ra["id"], rb["id"]), "shared_length": round(shared_len, 6), "fold_index": None, "warning": "no_matching_fold_line"})
                    continue
                edge = {"to": rb["id"], "fold": fd, "shared_length": shared_len, "seam_score": seam_score}
                redge = {"to": ra["id"], "fold": fd, "shared_length": shared_len, "seam_score": seam_score}
                adjacency[ra["id"]].append(edge)
                adjacency[rb["id"]].append(redge)
                edge_diag.append({"panels": (ra["id"], rb["id"]), "shared_length": round(shared_len, 6), "fold_index": int(fd.get("index", -1)), "seam_score": round(seam_score, 6)})
        if not any(adjacency.values()):
            return False, "no_panel_adjacency_edges", []

        rec_by_id = {r["id"]: r for r in valid}
        anchor_id = anchor_rec["id"]
        for r in valid:
            r["panel"]["rotations"] = []
            r["panel"]["chain_crossed_seams"] = []
            r["panel"]["chain_anchor_panel"] = False
            r["panel"]["chain_graph_parent_panel"] = None
        rec_by_id[anchor_id]["panel"]["chain_anchor_panel"] = True
        rec_by_id[anchor_id]["panel"]["chain_graph_depth"] = 0
        rec_by_id[anchor_id]["panel"]["chain_graph_parent_panel"] = None
        rec_by_id[anchor_id]["panel"]["chain_graph_mode"] = True

        seen = {anchor_id}
        queue = [anchor_id]
        parent = {anchor_id: None}
        parent_edge = {}
        depth = {anchor_id: 0}
        while queue:
            cur = queue.pop(0)
            # Prefer strongest/longest shared edge first for deterministic trees.
            for edge in sorted(adjacency.get(cur, []), key=lambda e: (-float(e.get("shared_length", 0.0)), int(e.get("to", -1)))):
                nxt = int(edge.get("to"))
                if nxt in seen:
                    continue
                seen.add(nxt)
                parent[nxt] = cur
                parent_edge[nxt] = edge
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)

        if len(seen) < len(valid):
            return False, "panel_graph_disconnected_from_anchor:%d/%d" % (len(seen), len(valid)), []

        # Assign rotations in BFS depth order so each parent is ready.
        ordered_ids = sorted(seen, key=lambda pid: (depth.get(pid, 0), pid))
        for pid in ordered_ids:
            if pid == anchor_id:
                continue
            par = parent.get(pid)
            edge = parent_edge.get(pid) or {}
            parent_panel = rec_by_id[par]["panel"]
            panel = rec_by_id[pid]["panel"]
            parent_rots = list(parent_panel.get("rotations") or [])
            fd = edge.get("fold")
            fd2 = _graffiti_transform_fold_def_through_parent(fd, parent_rots, mode="panel_adjacency_graph_child")
            fd2["chain_graph_parent_panel"] = int(par)
            fd2["chain_graph_child_panel"] = int(pid)
            fd2["chain_graph_depth"] = int(depth.get(pid, 0))
            fd2["chain_graph_shared_length"] = float(edge.get("shared_length", 0.0))
            panel["rotations"] = parent_rots + [fd2]
            panel["chain_crossed_seams"] = list(parent_panel.get("chain_crossed_seams") or []) + [fd2.get("index")]
            panel["chain_graph_parent_panel"] = int(par)
            panel["chain_graph_depth"] = int(depth.get(pid, 0))
            panel["chain_graph_mode"] = True
            panel["chain_anchor_panel"] = False

        diag = []
        for order, r in enumerate(valid):
            panel = r["panel"]
            rotations = list(panel.get("rotations") or [])
            diag.append({
                "panel_order": int(order),
                "panel_id": int(r["id"]),
                "panel_area": round(float(r["area"]), 6),
                "panel_centroid": (round(float(r["centroid"].x), 6), round(float(r["centroid"].y), 6)),
                "step_chain_anchor": bool(r["id"] == anchor_id),
                "anchor_panel_id": int(anchor_id),
                "anchor_reason": "anchor_panel_from_existing_selection_or_largest" if r["id"] == anchor_id else None,
                "chain_mode": "panel_adjacency_graph_parent_frame_ignore_isrightfold_inverted",
                "parent_panel_order": panel.get("chain_graph_parent_panel"),
                "graph_depth": int(panel.get("chain_graph_depth", 0)),
                "crossed_seams": list(panel.get("chain_crossed_seams") or []),
                "rotation_count": len(rotations),
                "axes_transformed": [bool(rot.get("chain_axis_transformed")) for rot in rotations],
                "raw_angle_deg": [round(math.degrees(float(rot.get("chain_authored_angle", rot.get("angle", 0.0)))), 6) for rot in rotations],
                "effective_angle_deg": [round(math.degrees(_graffiti_effective_fold_angle(rot)), 6) for rot in rotations],
                "angle_inverted_for_multifold": [bool(rot.get("chain_multifold_direction_inverted", False)) for rot in rotations],
                "isrightfold_ignored": [bool(rot.get("chain_ignore_isrightfold", False)) for rot in rotations],
                "axis_dir_3d": [tuple(round(float(v), 6) for v in rot.get("axis_dir_3d", Vector((0.0, 0.0, 0.0)))) for rot in rotations],
                "axis_origin_3d": [tuple(round(float(v), 6) for v in rot.get("p0_3d", Vector((0.0, 0.0, 0.0)))) for rot in rotations],
            })
        # Store graph diagnostics on anchor too for idprops/report compactness.
        for r in valid:
            r["panel"]["chain_graph_edge_diagnostics"] = edge_diag
        return True, "panel_adjacency_graph_parent_frame_ignore_isrightfold_inverted", diag
    except Exception as ex:
        return False, "exception:%s" % ex, []

def _graffiti_line_rect_intersections(p0, axis_dir, hw, hh, eps=1e-7):
    """Return unique intersections of an infinite 2D seam line with the rectangle."""
    points = []
    try:
        d = axis_dir.normalized()
    except Exception:
        return []
    candidates = []
    if abs(d.x) > eps:
        for x in (-hw, hw):
            t = (x - p0.x) / d.x
            y = p0.y + d.y * t
            if -hh - eps <= y <= hh + eps:
                candidates.append(Vector((x, max(-hh, min(hh, y)))))
    if abs(d.y) > eps:
        for y in (-hh, hh):
            t = (y - p0.y) / d.y
            x = p0.x + d.x * t
            if -hw - eps <= x <= hw + eps:
                candidates.append(Vector((max(-hw, min(hw, x)), y)))
    for pt in candidates:
        if not any((pt - old).length <= 1e-5 for old in points):
            points.append(pt)
    return points


def _graffiti_projection_range_for_rect(axis_dir, hw, hh):
    n = Vector((-axis_dir.y, axis_dir.x))
    corners = (Vector((-hw, -hh)), Vector((hw, -hh)), Vector((hw, hh)), Vector((-hw, hh)))
    vals = [c.dot(n) for c in corners]
    return min(vals), max(vals), n


def _graffiti_force_interior_seam(fd, hw, hh, margin_frac=0.02):
    """Clamp an out-of-bounds/tangent seam to an interior parallel seam.

    This is a conservative recovery for authored fold endpoints that identify a
    hinge direction but do not cross the generated texture-aspect rectangle.
    It preserves hinge direction while moving the seam just inside the panel
    bounds so a diagnostic/test fold is visible instead of silently producing one
    unsplit quad.
    """
    d = fd["axis_dir_2d"].normalized()
    lo, hi, n = _graffiti_projection_range_for_rect(d, hw, hh)
    proj = fd["p0_2d"].dot(n)
    span = max(hi - lo, 1e-6)
    margin = span * float(margin_frac)
    clamped = max(lo + margin, min(hi - margin, proj))
    if abs(clamped - proj) <= 1e-8:
        # If the seam is tangent/on an edge, nudge it inward toward the center.
        mid = (lo + hi) * 0.5
        if abs(proj - lo) < abs(proj - hi):
            clamped = lo + margin
        elif abs(proj - hi) < abs(proj - lo):
            clamped = hi - margin
        else:
            clamped = mid
    offset = n * (clamped - proj)
    fd2 = dict(fd)
    fd2["p0_2d"] = fd["p0_2d"] + offset
    fd2["p1_2d"] = fd["p1_2d"] + offset
    fd2["p0_3d"] = Vector((fd2["p0_2d"].x, fd2["p0_2d"].y, 0.0))
    fd2["p1_3d"] = Vector((fd2["p1_2d"].x, fd2["p1_2d"].y, 0.0))
    fd2["force_split_offset"] = (float(offset.x), float(offset.y))
    fd2["force_split_projection_original"] = float(proj)
    fd2["force_split_projection_clamped"] = float(clamped)
    return fd2




def _graffiti_build_fold_defs_from_items(raw_folds, width, height):
    """Build FoldInfo seam definitions for a specific final mesh size.

    Keep this separate from mesh construction so sizing experiments can decide
    the final local rectangle first, then rebuild all seam axes against that
    final rectangle instead of reusing pre-scale axes.
    """
    fold_defs = []
    for fold_index, item in enumerate(raw_folds or []):
        angle = _graffiti_fold_angle_value(item)
        if angle is None or abs(angle) <= 1e-8:
            continue
        axis = _graffiti_fold_axis_from_item(item, width, height)
        if axis is None:
            continue
        p0, p1 = axis
        axis_dir = p1 - p0
        if axis_dir.length <= 1e-6:
            continue
        axis_dir.normalize()
        is_right_fold = _graffiti_bool_value(item.get("IsRightFold") if isinstance(item, dict) else None, True)
        # RightFold selects the stationary side, so the opposite side rotates.
        rotate_positive_side = not bool(is_right_fold)
        p0_3d = Vector((p0.x, p0.y, 0.0))
        p1_3d = Vector((p1.x, p1.y, 0.0))
        axis_dir_3d = p1_3d - p0_3d
        if axis_dir_3d.length <= 1e-6:
            axis_dir_3d = Vector((1.0, 0.0, 0.0))
        axis_dir_3d.normalize()
        plane_normal_3d = Vector((0.0, 0.0, 1.0))
        hinge_side_3d = plane_normal_3d.cross(axis_dir_3d)
        if hinge_side_3d.length <= 1e-6:
            hinge_side_3d = Vector((0.0, 1.0, 0.0))
        hinge_side_3d.normalize()
        fold_defs.append({
            "index": fold_index,
            "item": item,
            "angle": angle,
            "p0_2d": p0,
            "p1_2d": p1,
            "axis_dir_2d": axis_dir,
            "p0_3d": p0_3d,
            "p1_3d": p1_3d,
            "axis_dir_3d": axis_dir_3d,
            "hinge_normal_3d": plane_normal_3d,
            "hinge_side_3d": hinge_side_3d,
            "is_right_fold": is_right_fold,
            "rotate_positive_side": rotate_positive_side,
            "rotated_side": "positive" if rotate_positive_side else "negative",
            "mesh_width": float(width),
            "mesh_height": float(height),
        })
    return fold_defs
def _graffiti_build_mesh(mesh_name, width, height, row, report_lines=None, source_name="", layer_label="", index=-1, prefs=None, full_report_enabled=False):
    """Build a texture-aspect graffiti mesh, optionally split/folded by FoldInfo.

    FoldInfo seams are interpreted in the unrotated local decal plane.  Each seam
    is extended as an infinite line across the texture rectangle, then panels on
    the fold side are rotated around that seam in local mesh space.
    """
    mesh = bpy.data.meshes.new(mesh_name)
    hw = 0.5 * max(0.01, float(width))
    hh = 0.5 * max(0.01, float(height))
    raw_folds = _graffiti_fold_items(row)
    fold_defs = _graffiti_build_fold_defs_from_items(raw_folds, width, height)
    # Sizing is already final before this point. FoldInfo is used only for seam construction and folding.
    base_poly = [Vector((-hw, -hh)), Vector((hw, -hh)), Vector((hw, hh)), Vector((-hw, hh))]

    if not fold_defs:
        verts = [(v.x, v.y, 0.0) for v in base_poly]
        faces = [(0, 1, 2, 3)]
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        mesh.from_pydata(verts, [], faces)
        try:
            uv_layer = mesh.uv_layers.new(name="UVMap")
            for loop, uv in zip(uv_layer.data, uvs):
                loop.uv = uv
        except Exception:
            pass
        mesh.update()
        return mesh, {"folded_mesh": False, "fold_count_raw": len(raw_folds), "fold_count_used": 0, "mesh_strategy": "flat_texture_pixels_3_over_250_byml_scale", "panel_count": 1, "fallback_reason": None}

    # Detect whether seams are a near-parallel ordered chain. The same splitter
    # handles arbitrary seams, but this diagnostic is useful for step folds.
    parallel = True
    ref = fold_defs[0]["axis_dir_2d"]
    projections = []
    for fd in fold_defs:
        d = fd["axis_dir_2d"]
        if abs(abs(ref.dot(d)) - 1.0) > 0.05:
            parallel = False
        normal = Vector((-ref.y, ref.x))
        projections.append(round(fd["p0_2d"].dot(normal), 6))
    ordered = list(range(len(fold_defs)))
    chain_axis_notes = []
    if len(fold_defs) > 1:
        # FoldInfo array order is authoritative for multi-fold chains.  Do not
        # sort or flip seam definitions by geometric projection; preserve the
        # authored fold array, P0/P1 direction, IsRightFold, and angle sign.
        fold_defs, chain_axis_notes = _graffiti_normalize_parallel_fold_defs(fold_defs)

    panels = [{"poly": base_poly, "rotations": [], "side_history": []}]
    split_events = []
    fallback_reason = None

    def _split_with_fd(current_panels, fd_use):
        next_panels = []
        split_count = 0
        for panel in current_panels:
            poly = panel.get("poly") or []
            if len(poly) < 3:
                continue
            pos_poly = _graffiti_clean_poly(_graffiti_fold_clip_polygon(poly, fd_use["p0_2d"], fd_use["axis_dir_2d"], keep_positive=True))
            neg_poly = _graffiti_clean_poly(_graffiti_fold_clip_polygon(poly, fd_use["p0_2d"], fd_use["axis_dir_2d"], keep_positive=False))
            if len(pos_poly) >= 3 and len(neg_poly) >= 3:
                split_count += 1
            for side_name, is_positive, clipped in (("positive", True, pos_poly), ("negative", False, neg_poly)):
                if len(clipped) < 3:
                    continue
                rotations = list(panel.get("rotations") or [])
                if is_positive == fd_use["rotate_positive_side"]:
                    rotations.append(fd_use)
                next_panels.append({"poly": clipped, "rotations": rotations, "side_history": list(panel.get("side_history") or []) + [(fd_use["index"], side_name)]})
        return next_panels, split_count

    applied_fold_defs = []
    for fd in fold_defs:
        intersections_before = _graffiti_line_rect_intersections(fd["p0_2d"], fd["axis_dir_2d"], hw, hh)
        next_panels, split_count = _split_with_fd(panels, fd)
        fd_used = fd
        force_split_used = False
        intersections_after = intersections_before
        if split_count <= 0:
            fd_forced = _graffiti_force_interior_seam(fd, hw, hh)
            forced_intersections = _graffiti_line_rect_intersections(fd_forced["p0_2d"], fd_forced["axis_dir_2d"], hw, hh)
            retry_panels, retry_split_count = _split_with_fd(panels, fd_forced)
            if retry_split_count > 0 and retry_panels:
                next_panels = retry_panels
                split_count = retry_split_count
                fd_used = fd_forced
                force_split_used = True
                intersections_after = forced_intersections
                fallback_reason = fallback_reason or "force_interior_seam_after_no_split"
        if next_panels:
            panels = next_panels
        applied_fold_defs.append(fd_used)
        split_events.append({
            "index": fd["index"],
            "split_panels": split_count,
            "panels_after": len(panels),
            "force_split_used": force_split_used,
            "intersection_count_before": len(intersections_before),
            "intersections_before": [(round(v.x, 6), round(v.y, 6)) for v in intersections_before],
            "intersection_count_after": len(intersections_after),
            "intersections_after": [(round(v.x, 6), round(v.y, 6)) for v in intersections_after],
            "force_split_offset": tuple(round(float(x), 6) for x in fd_used.get("force_split_offset", (0.0, 0.0))),
        })
    fold_defs_for_output = applied_fold_defs or fold_defs
    panel_state = _graffiti_panel_state(panels, fold_defs_for_output) if fold_defs_for_output else None
    panel_topology_diagnostics = _graffiti_panel_topology_diagnostics(panels, fold_defs_for_output, panel_state=panel_state) if bool(full_report_enabled) else {}
    # Centerpoint-driven fold handling. FoldInfo CenterPoint
    # identifies the side/panel branch moved by that fold.  Panel 0 is selected
    # as the panel containing no FoldInfo centerpoints, and all other panels are
    # folded by traversing outward from panel 0 through the panel adjacency graph.
    anchor_panel = None
    anchor_reason = None
    anchor_points = None
    chain_applied = False
    chain_reason = None
    chain_panel_diagnostics = []
    graph_chain_attempted = True
    graph_chain_applied = False
    graph_chain_reason = None
    centerpoint_chain_attempted = True
    centerpoint_chain_applied = False
    centerpoint_chain_reason = None
    if fold_defs_for_output:
        centerpoint_chain_applied, centerpoint_chain_reason, chain_panel_diagnostics = _graffiti_assign_centerpoint_outward_chain(panels, fold_defs_for_output, None, panel_state=panel_state)
        chain_applied = centerpoint_chain_applied
        chain_reason = centerpoint_chain_reason
        graph_chain_applied = centerpoint_chain_applied
        graph_chain_reason = centerpoint_chain_reason
        # Fallback only if the centerpoint graph cannot be built at all, so a
        # malformed decal still imports instead of disappearing.
        if not centerpoint_chain_applied:
            anchor_panel, anchor_reason, anchor_points = _graffiti_choose_anchor_panel(panels, fold_defs_for_output)
            if len(fold_defs_for_output) > 1:
                if not bool(parallel):
                    graph_chain_applied, graph_chain_reason, chain_panel_diagnostics = _graffiti_assign_adjacency_graph_chain(panels, fold_defs_for_output, anchor_panel)
                    chain_applied = graph_chain_applied
                    chain_reason = "centerpoint_fallback:%s -> %s" % (centerpoint_chain_reason, graph_chain_reason)
                if not graph_chain_applied:
                    chain_applied, legacy_reason, chain_panel_diagnostics = _graffiti_assign_cumulative_parallel_chain(panels, fold_defs_for_output, anchor_panel)
                    chain_reason = "centerpoint_fallback:%s -> legacy:%s" % (centerpoint_chain_reason, legacy_reason)
            else:
                # Do not use the old one-fold split-side/IsRightFold path.
                # If the centerpoint solver cannot assign a one-fold decal, keep
                # the panelized mesh but leave panels unrotated instead of falling
                # back to legacy rotations/roll behavior.
                for _p in panels:
                    try:
                        _p["rotations"] = []
                        _p["chain_crossed_seams"] = []
                        _p["chain_anchor_panel"] = False
                    except Exception:
                        pass
                chain_reason = "centerpoint_fallback:%s -> single_fold_legacy_disabled" % centerpoint_chain_reason
    else:
        chain_reason = "no_fold_defs"
    if anchor_panel is None:
        anchor_panel = next((p for p in panels if bool(p.get("chain_anchor_panel"))), None)
        anchor_reason = chain_reason
        anchor_points = None
    anchor_rotations = list(anchor_panel.get("rotations") or []) if anchor_panel else []
    # Keep the selected anchor as a diagnostic only. The previous
    # anchor-inverse correction tried to roll the whole folded result back into
    # the anchor panel plane, but reports/visual checks showed that it did not
    # affect one-fold orientation and made multi-fold graffiti such as 0046
    # worse.  Folding now deforms vertices strictly in the original flat local
    # graffiti plane; the object transform remains the sole final placement
    # transform.
    anchor_inverse_applied = False
    # The universal centerpoint solver owns all folded-graffiti
    # deformation, including one-fold graffiti.  Disable the old one-fold
    # ROLL_TARGET_NORMAL / post-fold seam roll path so single-fold decals no
    # longer receive a second global correction after panel deformation.
    roll_mode = "ROLL_DISABLED_CENTERPOINT_SOLVER"
    primary_effective_fold_angle_deg = None
    primary_raw_fold_angle_deg = None
    if fold_defs_for_output:
        try:
            primary_effective_fold_angle_deg = math.degrees(_graffiti_effective_fold_angle(fold_defs_for_output[0]))
            primary_raw_fold_angle_deg = math.degrees(float(fold_defs_for_output[0].get("angle", 0.0)))
        except Exception:
            primary_effective_fold_angle_deg = None
            primary_raw_fold_angle_deg = None
    multi_fold_target_roll_disabled = True
    post_fold_roll_deg = 0.0
    post_fold_roll_axis = None
    post_fold_roll_origin = None
    if abs(post_fold_roll_deg) > 1e-8 and fold_defs_for_output:
        try:
            post_fold_roll_axis = fold_defs_for_output[0].get("axis_dir_3d", Vector((1.0, 0.0, 0.0))).copy()
            if post_fold_roll_axis.length <= 1e-6:
                post_fold_roll_axis = Vector((1.0, 0.0, 0.0))
            post_fold_roll_axis.normalize()
            post_fold_roll_origin = fold_defs_for_output[0].get("p0_3d", Vector((0.0, 0.0, 0.0))).copy()
        except Exception:
            post_fold_roll_axis = None
            post_fold_roll_origin = None
    verts = []
    uvs = []
    faces = []
    panel_vertex_counts = []
    panel_rotation_counts = []
    panel_anchor_flags = []
    panel_side_histories = []
    panel_fold_diagnostics = []
    panel_chain_crossed_seams = []
    fold_transform_stage_diagnostics = {
        "flat_basis_normal": (0.0, 0.0, 1.0),
        "anchor_inverse_applied": False,
        "post_fold_roll_mode": roll_mode,
        "post_fold_roll_deg": round(float(post_fold_roll_deg), 6),
        "post_fold_roll_source": "disabled_centerpoint_solver",
        "multi_fold_target_roll_disabled": bool(multi_fold_target_roll_disabled),
        "target_roll_policy": "disabled; centerpoint solver handles one-fold and multi-fold deformation",
        "primary_effective_fold_angle_deg": round(float(primary_effective_fold_angle_deg), 6) if primary_effective_fold_angle_deg is not None else None,
        "primary_raw_fold_angle_deg": round(float(primary_raw_fold_angle_deg), 6) if primary_raw_fold_angle_deg is not None else None,
        "post_fold_roll_axis": tuple(round(float(v), 6) for v in post_fold_roll_axis) if post_fold_roll_axis is not None else None,
        "post_fold_roll_origin": tuple(round(float(v), 6) for v in post_fold_roll_origin) if post_fold_roll_origin is not None else None,
        "double_application_watch": bool(abs(post_fold_roll_deg) > 1e-8 and bool(fold_defs_for_output)),
        "double_application_note": "post_roll_disabled; no separate one-fold roll correction is applied",
    }
    for panel in panels:
        poly = _graffiti_clean_poly(panel.get("poly") or [])
        if len(poly) < 3:
            continue
        is_anchor = panel is anchor_panel
        face = []
        panel_folded_points = []
        panel_pre_roll_points = []
        for pt in poly:
            face.append(len(verts))
            folded_pre_roll = _graffiti_apply_fold_rotations(pt, panel.get("rotations") or [])
            folded_pt = folded_pre_roll
            if post_fold_roll_axis is not None and post_fold_roll_origin is not None:
                fpv = Vector((folded_pre_roll[0], folded_pre_roll[1], folded_pre_roll[2]))
                fpv = _graffiti_rotate_point_around_axis(fpv, post_fold_roll_origin, post_fold_roll_axis, math.radians(post_fold_roll_deg))
                folded_pt = (fpv.x, fpv.y, fpv.z)
            panel_pre_roll_points.append(folded_pre_roll)
            panel_folded_points.append(folded_pt)
            verts.append(folded_pt)
            uvs.append(_graffiti_uv_from_local_2d(pt, hw, hh))
        faces.append(tuple(face))
        panel_vertex_counts.append(len(poly))
        panel_rotation_counts.append(len(panel.get("rotations") or []))
        panel_anchor_flags.append(bool(is_anchor))
        panel_side_histories.append(list(panel.get("side_history") or []))
        panel_chain_crossed_seams.append(list(panel.get("chain_crossed_seams") or []))
        pre_roll_diag = _graffiti_panel_fold_diagnostic(poly, panel.get("rotations") or [], folded_points=panel_pre_roll_points)
        post_roll_diag = _graffiti_panel_fold_diagnostic(poly, panel.get("rotations") or [], folded_points=panel_folded_points)
        diag = dict(post_roll_diag)
        diag["pre_roll"] = pre_roll_diag
        diag["post_roll"] = post_roll_diag
        diag["post_roll_applied"] = bool(post_fold_roll_axis is not None and abs(post_fold_roll_deg) > 1e-8)
        diag["is_anchor"] = bool(is_anchor)
        diag["rotation_count"] = len(panel.get("rotations") or [])
        diag["rotation_indices"] = [r.get("index") for r in (panel.get("rotations") or [])]
        diag["rotation_effective_angle_deg"] = [round(math.degrees(_graffiti_effective_fold_angle(r)), 6) for r in (panel.get("rotations") or [])]
        diag["rotation_raw_angle_deg"] = [round(math.degrees(float(r.get("angle", 0.0))), 6) for r in (panel.get("rotations") or [])]
        diag["rotation_raw_rotated_side"] = [r.get("chain_raw_rotated_side", ("positive" if bool(r.get("rotate_positive_side", False)) else "negative")) for r in (panel.get("rotations") or [])]
        diag["rotation_effective_rotated_side"] = ["positive" if bool(r.get("rotate_positive_side", False)) else "negative" for r in (panel.get("rotations") or [])]
        diag["rotation_parent_side"] = [r.get("chain_parent_side") for r in (panel.get("rotations") or [])]
        diag["rotation_parent_panel_local_isrightfold_mode"] = [bool(r.get("chain_parent_local_isrightfold_mode", False)) for r in (panel.get("rotations") or [])]
        diag["rotation_side_overridden"] = [bool(r.get("chain_rotate_positive_overridden", False)) for r in (panel.get("rotations") or [])]
        diag["side_history"] = list(panel.get("side_history") or [])
        diag["chain_crossed_seams"] = list(panel.get("chain_crossed_seams") or [])
        panel_fold_diagnostics.append(diag)
    if len(verts) < 3 or not faces:
        fallback_reason = "fold_clipped_all_panels"
        verts = [(v.x, v.y, 0.0) for v in base_poly]
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        faces = [(0, 1, 2, 3)]
        panel_vertex_counts = [4]
        panel_rotation_counts = [0]
        strategy = "texture_space_fold_fallback_flat"
    else:
        strategy = "texture_space_panelized_parallel_chain" if parallel else "texture_space_panelized_arbitrary_split"
    mesh.from_pydata(verts, [], faces)
    try:
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for poly in mesh.polygons:
            for loop_idx in poly.loop_indices:
                uv_layer.data[loop_idx].uv = uvs[mesh.loops[loop_idx].vertex_index]
    except Exception:
        pass
    mesh.update()
    axes = []
    raw_axes = []
    summaries = []
    for fd in fold_defs_for_output:
        raw = fd["item"] if isinstance(fd.get("item"), dict) else {}
        raw_axes.append(((raw.get("P0X"), raw.get("P0Y")), (raw.get("P1X"), raw.get("P1Y"))))
        axes.append(((round(fd["p0_2d"].x, 6), round(fd["p0_2d"].y, 6)), (round(fd["p1_2d"].x, 6), round(fd["p1_2d"].y, 6))))
        summaries.append({
            "index": fd["index"],
            "angle_deg": round(math.degrees(fd["angle"]), 6),
            "effective_angle_deg": round(math.degrees(_graffiti_effective_fold_angle(fd)), 6),
            "is_right_fold": fd["is_right_fold"],
            "rotated_side": fd["rotated_side"],
            "hinge_axis": tuple(round(float(v), 6) for v in fd.get("axis_dir_3d", Vector((1.0, 0.0, 0.0)))),
            "hinge_normal": tuple(round(float(v), 6) for v in fd.get("hinge_normal_3d", Vector((0.0, 0.0, 1.0)))),
            "hinge_side": tuple(round(float(v), 6) for v in fd.get("hinge_side_3d", Vector((0.0, 1.0, 0.0)))),
            "force_split_offset": tuple(round(float(x), 6) for x in fd.get("force_split_offset", (0.0, 0.0))),
            "center_point_raw": (raw.get("CenterPointX"), raw.get("CenterPointY")),
            "raw_rotate_positive_side": bool(fd.get("rotate_positive_side", False)),
            "raw_signed_angle_preserved": True,
            "sign_or_side_normalized": False,
        })
    return mesh, {
        "folded_mesh": True,
        "fold_count_raw": len(raw_folds),
        "fold_count_used": len(fold_defs_for_output),
        "fold_angle": fold_defs_for_output[0]["angle"] if fold_defs_for_output else None,
        "fold_axes_raw_xy": raw_axes,
        "fold_axes_mesh_xy": axes,
        "mesh_strategy": strategy,
        "panel_count": len(faces),
        "panel_vertex_counts": panel_vertex_counts,
        "panel_rotation_counts": panel_rotation_counts,
        "panel_anchor_flags": panel_anchor_flags if 'panel_anchor_flags' in locals() else [],
        "panel_side_histories": panel_side_histories if 'panel_side_histories' in locals() else [],
        "panel_chain_crossed_seams": panel_chain_crossed_seams if 'panel_chain_crossed_seams' in locals() else [],
        "panel_fold_diagnostics": panel_fold_diagnostics if 'panel_fold_diagnostics' in locals() else [],
        "panel_topology_diagnostics": panel_topology_diagnostics if 'panel_topology_diagnostics' in locals() else {},
        "fold_transform_stage_diagnostics": fold_transform_stage_diagnostics if 'fold_transform_stage_diagnostics' in locals() else {},
        "cumulative_chain_applied": chain_applied if 'chain_applied' in locals() else False,
        "cumulative_chain_reason": chain_reason if 'chain_reason' in locals() else None,
        "cumulative_chain_panel_diagnostics": chain_panel_diagnostics if 'chain_panel_diagnostics' in locals() else [],
        "centerpoint_chain_attempted": centerpoint_chain_attempted if 'centerpoint_chain_attempted' in locals() else False,
        "centerpoint_chain_applied": centerpoint_chain_applied if 'centerpoint_chain_applied' in locals() else False,
        "centerpoint_chain_reason": centerpoint_chain_reason if 'centerpoint_chain_reason' in locals() else None,
        "graph_chain_attempted": graph_chain_attempted if 'graph_chain_attempted' in locals() else False,
        "graph_chain_applied": graph_chain_applied if 'graph_chain_applied' in locals() else False,
        "graph_chain_reason": graph_chain_reason if 'graph_chain_reason' in locals() else None,
        "chain_axis_notes": chain_axis_notes if 'chain_axis_notes' in locals() else [],
        "anchor_reason": anchor_reason if 'anchor_reason' in locals() else None,
        "anchor_points": anchor_points if 'anchor_points' in locals() else None,
        "anchor_rotation_count": len(anchor_rotations) if 'anchor_rotations' in locals() else 0,
        "anchor_inverse_applied": False,
        "fold_basis_mode": "flat_local_vertex_deform_centerpoint_outward_panel0_universal_no_legacy_single_fold_roll",
        "post_fold_seam_roll_mode": roll_mode if 'roll_mode' in locals() else "ROLL_0",
        "post_fold_seam_roll_deg": post_fold_roll_deg if 'post_fold_roll_deg' in locals() else 0.0,
        "post_fold_seam_roll_axis": tuple(round(float(v), 6) for v in post_fold_roll_axis) if 'post_fold_roll_axis' in locals() and post_fold_roll_axis is not None else None,
        "split_events": split_events,
        "parallel_chain": parallel,
        "seam_order_projections": projections,
        "fold_summary": summaries,
        "fallback_reason": fallback_reason,
        "texture_space_bounds": (-hw, -hh, hw, hh),
    }

def _create_graffiti_decal_or_empty(row, layer_label, source_name, index, collection, report_lines, group_names=None, prefs=None, full_report_enabled=False):
    param = _graffiti_param_for_type(row.get("Type"))
    tex = _resolve_graffiti_texture(param, report_lines) if param else None
    graffiti_image = None
    graffiti_image_meta = None
    if param and prefs is not None:
        graffiti_image, graffiti_image_meta = _resolve_graffiti_bfres_image(param, group_names or [], prefs, report_lines)
    width, height, size_source, row_scale_used, param_scale_used = _graffiti_dimensions_from_row(row, graffiti_image, prefs=prefs, param_row=param)
    mesh, fold_mesh_meta = _graffiti_build_mesh("Graffiti_%s_%04d_Mesh" % (_safe_collection_name(layer_label), index), width, height, row, report_lines=report_lines, source_name=source_name, layer_label=layer_label, index=index, prefs=prefs)
    obj = bpy.data.objects.new("Graffiti_%s_%04d" % (_safe_collection_name(layer_label), index), mesh)
    raw_pos, raw_rot, raw_scale, raw_uniform_scale = _graffiti_raw_transform(row)
    obj.location = _graffiti_location_from_row(row)
    obj.rotation_euler = _graffiti_rotation_from_row(row)
    has_foldinfo = bool(isinstance(fold_mesh_meta, dict) and int(fold_mesh_meta.get("fold_count_raw") or 0) > 0)
    actor_scale_baked_into_mesh = bool(str(size_source) == "texture_pixels_3_over_250_byml_scale")
    graffiti_world_scale_formula = "texture_pixels * 3 / 250 * byml_scale"
    try:
        # BYML scale is baked into the mesh before FoldInfo seam construction.
        # Keep object scale at identity so folded and flat graffiti share the
        # same final-size path.
        obj.scale = (1.0, 1.0, 1.0)
    except Exception:
        pass
    _safe_set_idprop(obj, "splatoon_source", source_name)
    _safe_set_idprop(obj, "graffiti_layer", layer_label)
    _safe_set_idprop(obj, "graffiti_type", row.get("Type", ""))
    _safe_set_idprop(obj, "graffiti_bind_instance_id", row.get("BindInstanceId", ""))
    _safe_set_idprop(obj, "graffiti_texture_resolved", bool(graffiti_image or (tex and tex.get("path"))))
    _safe_set_idprop(obj, "graffiti_raw_pos", list(raw_pos))
    _safe_set_idprop(obj, "graffiti_raw_rot", list(raw_rot))
    _safe_set_idprop(obj, "graffiti_raw_scale", list(raw_scale))
    _safe_set_idprop(obj, "graffiti_raw_uniform_scale", raw_uniform_scale)
    _safe_set_idprop(obj, "graffiti_plane_width", width)
    _safe_set_idprop(obj, "graffiti_plane_height", height)
    _safe_set_idprop(obj, "graffiti_plane_size_source", size_source)
    _safe_set_idprop(obj, "graffiti_row_scale_used", row_scale_used)
    _safe_set_idprop(obj, "graffiti_param_scale_used", param_scale_used)
    _safe_set_idprop(obj, "graffiti_world_scale_formula", graffiti_world_scale_formula)
    _safe_set_idprop(obj, "graffiti_has_foldinfo", has_foldinfo)
    _safe_set_idprop(obj, "graffiti_actor_scale_baked_into_mesh", actor_scale_baked_into_mesh)
    if isinstance(fold_mesh_meta, dict):
        for _k, _v in fold_mesh_meta.items():
            _safe_set_idprop(obj, "graffiti_%s" % _k, _v)
    try:
        normal = obj.rotation_euler.to_matrix() @ Vector((0.0, 0.0, 1.0))
        _safe_set_idprop(obj, "graffiti_plane_normal", [float(normal.x), float(normal.y), float(normal.z)])
    except Exception:
        pass
    _report_append(report_lines, "GRAFFITI_TRANSFORM source=%r layer=%r index=%d raw_pos=%r raw_rot=%r raw_scale=%r row_scale=%.6g param_scale=%.6g scale_formula=%r loc=%r rot_deg=%r plane=(%.6g,%.6g) object_scale=(%.6g,%.6g,%.6g) size_source=%s has_foldinfo=%r actor_scale_baked_into_mesh=%r basis_x_offset_deg=90" % (source_name, layer_label, index, raw_pos, raw_rot, raw_scale, row_scale_used, param_scale_used, graffiti_world_scale_formula, tuple(round(v, 6) for v in obj.location), tuple(round(math.degrees(v), 6) for v in obj.rotation_euler), width, height, obj.scale.x, obj.scale.y, obj.scale.z, size_source, has_foldinfo, actor_scale_baked_into_mesh))
    graffiti_alpha_image = None
    if graffiti_image_meta and graffiti_image_meta.get("alpha_texture"):
        try:
            alpha_groups = []
            for group in (graffiti_image_meta.get("group"), *(group_names or [])):
                if group and group not in alpha_groups:
                    alpha_groups.append(group)
            for group in alpha_groups:
                graffiti_alpha_image = _load_graffiti_bfres_texture_map(group, prefs, report_lines).get(graffiti_image_meta.get("alpha_texture"))
                if graffiti_alpha_image:
                    graffiti_alpha_image = _register_or_reuse_graffiti_image(graffiti_alpha_image, group, graffiti_image_meta.get("alpha_texture"), report_lines)
                    break
        except Exception:
            graffiti_alpha_image = None
    mesh_bounds_diag = _graffiti_mesh_bounds_diag(mesh)
    image_size_diag = _graffiti_image_size_tuple(graffiti_image)
    alpha_size_diag = _graffiti_image_size_tuple(graffiti_alpha_image)
    alpha_bbox_diag = _graffiti_mask_bbox(graffiti_alpha_image or graffiti_image)
    try:
        effective_size_diag = (round(float((mesh_bounds_diag or {}).get("size", (width, height, 0.0))[0]) * float(obj.scale.x), 6), round(float((mesh_bounds_diag or {}).get("size", (width, height, 0.0))[1]) * float(obj.scale.y), 6))
    except Exception:
        effective_size_diag = None
    _safe_set_idprop(obj, "graffiti_size_diag_mesh_bounds", mesh_bounds_diag)
    _safe_set_idprop(obj, "graffiti_size_diag_image_size", image_size_diag)
    _safe_set_idprop(obj, "graffiti_size_diag_alpha_size", alpha_size_diag)
    _safe_set_idprop(obj, "graffiti_size_diag_alpha_bbox", alpha_bbox_diag)
    _safe_set_idprop(obj, "graffiti_size_diag_effective_size", effective_size_diag)
    _report_append(report_lines, "GRAFFITI_SIZE_DIAGNOSTIC source=%r layer=%r index=%d type=%r mesh=%r has_foldinfo=%r size_source=%r row_scale=%.6g param_scale=%.6g scale_formula=%r actor_scale_baked_into_mesh=%r plane=(%.6g,%.6g) object_scale=(%.6g,%.6g,%.6g) effective_size=%r mesh_bounds=%r texture=%r texture_size=%r alpha_texture=%r alpha_size=%r alpha_bbox=%r" % (source_name, layer_label, index, row.get("Type", ""), obj.name, has_foldinfo, size_source, row_scale_used, param_scale_used, graffiti_world_scale_formula, actor_scale_baked_into_mesh, width, height, obj.scale.x, obj.scale.y, obj.scale.z, effective_size_diag, mesh_bounds_diag, (graffiti_image_meta or {}).get("texture", ""), image_size_diag, (graffiti_image_meta or {}).get("alpha_texture", ""), alpha_size_diag, alpha_bbox_diag))
    if isinstance(fold_mesh_meta, dict) and (fold_mesh_meta.get("folded_mesh") or fold_mesh_meta.get("fold_count_raw")):
        _report_append(report_lines, "GRAFFITI_FOLD_SUMMARY source=%r layer=%r index=%d mesh=%r folded=%r raw_count=%r used_count=%r strategy=%r basis=%r bounds=%r panels=%r parallel=%r chain_applied=%r chain_reason=%r anchor_reason=%r fallback=%r" % (source_name, layer_label, index, obj.name, fold_mesh_meta.get("folded_mesh"), fold_mesh_meta.get("fold_count_raw"), fold_mesh_meta.get("fold_count_used"), fold_mesh_meta.get("mesh_strategy"), fold_mesh_meta.get("fold_basis_mode"), fold_mesh_meta.get("texture_space_bounds"), fold_mesh_meta.get("panel_count"), fold_mesh_meta.get("parallel_chain"), fold_mesh_meta.get("cumulative_chain_applied"), fold_mesh_meta.get("cumulative_chain_reason"), fold_mesh_meta.get("anchor_reason"), fold_mesh_meta.get("fallback_reason")))
        _report_append(report_lines, "GRAFFITI_FOLD_SEAMS source=%r layer=%r index=%d mesh=%r raw_axes=%r mesh_axes=%r projections=%r split_events=%r summary=%r" % (source_name, layer_label, index, obj.name, fold_mesh_meta.get("fold_axes_raw_xy"), fold_mesh_meta.get("fold_axes_mesh_xy"), fold_mesh_meta.get("seam_order_projections"), fold_mesh_meta.get("split_events"), fold_mesh_meta.get("fold_summary")))
        _report_append(report_lines, "GRAFFITI_FOLD_PANELS source=%r layer=%r index=%d mesh=%r panel_vertices=%r panel_rotations=%r panel_anchor=%r panel_chain_crossed=%r chain_axis_notes=%r anchor_points=%r anchor_rotation_count=%r anchor_inverse_applied=%r post_roll=%r post_roll_axis=%r" % (source_name, layer_label, index, obj.name, fold_mesh_meta.get("panel_vertex_counts"), fold_mesh_meta.get("panel_rotation_counts"), fold_mesh_meta.get("panel_anchor_flags"), fold_mesh_meta.get("panel_chain_crossed_seams"), fold_mesh_meta.get("chain_axis_notes"), fold_mesh_meta.get("anchor_points"), fold_mesh_meta.get("anchor_rotation_count"), fold_mesh_meta.get("anchor_inverse_applied"), (fold_mesh_meta.get("post_fold_seam_roll_mode"), fold_mesh_meta.get("post_fold_seam_roll_deg")), fold_mesh_meta.get("post_fold_seam_roll_axis")))
        if bool(full_report_enabled):
            _report_append(report_lines, "GRAFFITI_FOLD_DEBUG source=%r layer=%r index=%d mesh=%r panel_deltas=%r topology=%r transform_stages=%r chain_panels=%r" % (source_name, layer_label, index, obj.name, fold_mesh_meta.get("panel_fold_diagnostics"), fold_mesh_meta.get("panel_topology_diagnostics"), fold_mesh_meta.get("fold_transform_stage_diagnostics"), fold_mesh_meta.get("cumulative_chain_panel_diagnostics")))
        if int(fold_mesh_meta.get("fold_count_used") or 0) > 1:
            _panel_diags = list(fold_mesh_meta.get("panel_fold_diagnostics") or [])
            _chain_diags = list(fold_mesh_meta.get("cumulative_chain_panel_diagnostics") or [])
            _max_panels = max(len(_panel_diags), len(_chain_diags))
            for _pid in range(_max_panels):
                _pd = _panel_diags[_pid] if _pid < len(_panel_diags) else {}
                _cd = _chain_diags[_pid] if _pid < len(_chain_diags) else {}
                _report_append(report_lines, "GRAFFITI_MULTIFOLD_PANEL_DIAG source=%r layer=%r index=%d mesh=%r panel=%d chain=%r fold=%r" % (source_name, layer_label, index, obj.name, _pid, _cd, _pd))
        _topo = fold_mesh_meta.get("panel_topology_diagnostics") if isinstance(fold_mesh_meta, dict) else None
        if _topo:
            _report_append(report_lines, "GRAFFITI_PANEL_TOPOLOGY source=%r layer=%r index=%d mesh=%r topology=%r" % (source_name, layer_label, index, obj.name, _topo))
    if isinstance(param, dict):
        _set_props_from_mapping(obj, "graffiti_param", param)
    if graffiti_image_meta:
        obj["graffiti_texture_group"] = str(graffiti_image_meta.get("group", ""))
        obj["graffiti_texture_name"] = str(graffiti_image_meta.get("texture", ""))
        obj["graffiti_alpha_texture_name"] = str(graffiti_image_meta.get("alpha_texture", ""))
        obj["graffiti_alpha_source"] = str(graffiti_image_meta.get("alpha_source", ""))
    if tex and tex.get("path"):
        obj["graffiti_texture_path"] = str(tex.get("path"))
        obj["graffiti_texture_location"] = str(tex.get("location"))
    if not graffiti_image and not (tex and tex.get("path")):
        guessed = None
        if isinstance(param, dict):
            guessed = param.get("TexturePath") or param.get("OpaTexturePath") or param.get("ResourcePath")
        if guessed:
            obj["graffiti_texture_guess"] = str(guessed)
        _report_append(report_lines, "GRAFFITI_TEXTURE_PLACEHOLDER source=%r layer=%r index=%d type=%r guess=%r reason=texture_unresolved_or_unsupported" % (source_name, layer_label, index, row.get("Type"), guessed))
    # Always assign a visible material.  Real graffiti textures often resolve to
    # external/FTXB resources that this importer does not decode yet.
    if graffiti_image:
        alpha_image = graffiti_alpha_image
        mat = _make_graffiti_material_cached(graffiti_image, alpha_image, meta=graffiti_image_meta, report_lines=report_lines)
        obj["graffiti_material_mode"] = "bfres_texture"
        _report_append(report_lines, "GRAFFITI_ALPHA_APPLIED source=%r layer=%r index=%d texture=%r alpha_texture=%r alpha_used=%r alpha_source=%r" % (source_name, layer_label, index, graffiti_image_meta.get("texture", ""), graffiti_image_meta.get("alpha_texture", ""), bool(alpha_image), graffiti_image_meta.get("alpha_source", "")))
    else:
        mat_name = "OSI_Graffiti_Decal_TextureResolved" if tex and tex.get("path") else "OSI_Graffiti_Decal_Placeholder"
        mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
        mat.diffuse_color = (0.25, 0.55, 1.0, 0.35) if not (tex and tex.get("path")) else (1.0, 1.0, 1.0, 0.65)
        try:
            mat.use_nodes = True
            mat.blend_method = "BLEND"
            mat.show_transparent_back = True
        except Exception:
            pass
        obj["graffiti_material_mode"] = "placeholder" if not (tex and tex.get("path")) else "resolved_metadata_only"
    obj.data.materials.append(mat)
    _set_props_from_mapping(obj, "graffiti", row)
    collection.objects.link(obj)
    return obj


def _extract_graffiti_rows(data):
    if not isinstance(data, dict):
        return []
    info = data.get("GraffitiInfo") if isinstance(data.get("GraffitiInfo"), dict) else data
    rows = info.get("GraffitiObjInfo") if isinstance(info, dict) else None
    return rows if isinstance(rows, list) else []


def _import_pack_graffiti(files, graffiti_ref, stage_collection, report_lines, full_report_enabled=False):
    # Import pack graffiti as lightweight decal planes with RSDB GraffitiParam metadata.
    prefs = bpy.context.preferences.addons[__package__].preferences
    graffiti_root = _graffiti_root_collection(stage_collection)
    graffiti_root["osi_collection_role"] = "graffiti"
    graffiti_root["osi_graffiti_stage_collection"] = getattr(stage_collection, "name", "")
    graffiti_col = _graffiti_group_collection(stage_collection, "Graffiti Decals")
    graffiti_col["osi_collection_role"] = "graffiti_decals"
    count = 0
    refs = []
    name, data = _pack_parse_byml(files, graffiti_ref, report_lines, required=False)
    if isinstance(data, dict):
        common = data.get("Common")
        if common:
            refs.append(("Cmn", common))
        rule = data.get("Rule")
        if isinstance(rule, dict):
            for layer, ref in sorted(rule.items()):
                refs.append((str(layer), ref))
    if not refs:
        for fname in sorted(n for n in files if n.startswith("Graffiti/") and n.endswith(".byml")):
            layer = Path(fname).stem.split("_")[-1]
            refs.append((layer, fname))
    for layer, ref in refs:
        src, gdata = _pack_parse_byml(files, ref, report_lines, required=False)
        group_names = _extract_graffiti_group_set(gdata)
        if group_names:
            _report_append(report_lines, "GRAFFITI_GROUP_SET layer=%r groups=%r" % (layer, group_names))
        rows = _extract_graffiti_rows(gdata)
        if not rows:
            _report_append(report_lines, "GRAFFITI_EMPTY layer=%r ref=%r" % (layer, ref))
            continue
        layer_col = _get_or_create_child_collection(graffiti_col, _safe_collection_name(layer, "NoLayer"))
        layer_col["osi_collection_role"] = "graffiti_layer"
        layer_col["osi_graffiti_layer"] = str(layer)
        layer_col["osi_graffiti_source"] = str(src or ref)
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            try:
                _create_graffiti_decal_or_empty(row, layer, src or str(ref), i, layer_col, report_lines, group_names=group_names, prefs=prefs, full_report_enabled=full_report_enabled)
                count += 1
            except Exception as exc:
                _report_append(report_lines, "GRAFFITI_IMPORT_FAILED layer=%r ref=%r index=%d error=%s" % (layer, ref, i, exc))
        _report_append(report_lines, "GRAFFITI_IMPORTED layer=%r ref=%r count=%d" % (layer, src or ref, len(rows)))
    return count

'''

def install_into(namespace):
    exec(compile(_GRAFFITI_SOURCE, __file__, "exec"), namespace)
