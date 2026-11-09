import logging

import bpy
import mathutils
from bpy_extras.node_shader_utils import PrincipledBSDFWrapper, ShaderImageTextureWrapper

from .bfrespy.common import ResString
from .bfrespy.models.material import Material

log = logging.getLogger(__name__)

_OSI_MATERIAL_CACHE = {}


def _diag(operator, message, level="INFO"):
    try:
        if operator is not None and hasattr(operator, "report"):
            operator.report({level}, message)
    except Exception:
        pass


def _image_valid(image):
    try:
        return image is not None and getattr(image, "name", None) in bpy.data.images and not bool(getattr(image, "is_missing", False))
    except ReferenceError:
        return False
    except Exception:
        return False


def _material_images_valid(mat):
    try:
        if mat is None or mat.name not in bpy.data.materials:
            return False
        if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            return True
        for node in mat.node_tree.nodes:
            if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
                img = getattr(node, "image", None)
                if img is not None and not _image_valid(img):
                    return False
        return True
    except ReferenceError:
        return False
    except Exception:
        return False


def _safe_repr_short(value, limit=300):
    try:
        text = repr(value)
    except Exception as exc:
        text = "<repr failed %s>" % (type(exc).__name__,)
    if len(text) > limit:
        return text[:limit] + "..."
    return text

def _image_state(image):
    try:
        return {
            "name": getattr(image, "name", None),
            "valid": _image_valid(image),
            "size": tuple(getattr(image, "size", ()) or ()),
            "is_missing": bool(getattr(image, "is_missing", False)),
            "packed": bool(getattr(image, "packed_file", None)),
            "source": str(getattr(image, "source", "")),
            "filepath": str(getattr(image, "filepath", "")),
        }
    except ReferenceError:
        return {"name": "<removed>", "valid": False, "error": "ReferenceError"}
    except Exception as exc:
        return {"name": "<unknown>", "valid": False, "error": repr(exc)}



def _mapping_get(mapping, key, default=None):
    """Dictionary-like get() that also supports bfrespy ResDict objects.

    Several BFRES containers implement __getitem__/items/key_from_value but not
    dict.get().  Material import should treat missing values as optional rather
    than failing the whole texture slot.
    """
    if mapping is None:
        return default
    try:
        getter = getattr(mapping, "get", None)
        if callable(getter):
            return getter(key, default)
    except Exception:
        pass
    try:
        return mapping[key]
    except Exception:
        pass
    key_s = str(key)
    try:
        for k, v in mapping.items():
            if k == key or str(k) == key_s:
                return v
    except Exception:
        pass
    try:
        for k in mapping.keys():
            if k == key or str(k) == key_s:
                try:
                    return mapping[k]
                except Exception:
                    return default
    except Exception:
        pass
    return default


def _mapping_has(mapping, key):
    return _mapping_get(mapping, key, None) is not None


def _mapping_keys(mapping):
    if mapping is None:
        return []
    try:
        return list(mapping.keys())
    except Exception:
        pass
    try:
        return [k for k, _v in mapping.items()]
    except Exception:
        pass
    return []


def _resolve_texture_image(texture_dict, tex_name, name_prefix=""):
    """Resolve a texture reference without trusting stale Blender name lookups.

    Prefer the per-BFRES texture_map object returned by the current BNTX import.
    Fall back to bpy.data.images only if the found image is still valid.
    """
    tex_name_str = str(tex_name)
    image = None
    source = "missing"
    try:
        if texture_dict is not None:
            if _mapping_has(texture_dict, tex_name):
                image = _mapping_get(texture_dict, tex_name)
                source = "texture_map_exact"
            elif _mapping_has(texture_dict, tex_name_str):
                image = _mapping_get(texture_dict, tex_name_str)
                source = "texture_map_string"
            else:
                for key, value in texture_dict.items():
                    if str(key) == tex_name_str:
                        image = value
                        source = "texture_map_keystr"
                        break
    except Exception:
        image = None
        source = "texture_map_error"
    if image is not None:
        if _image_valid(image):
            return image, source
        return None, source + "_invalid"
    for candidate, src in ((name_prefix + tex_name_str, "bpy_prefixed"), (tex_name_str, "bpy_unprefixed")):
        try:
            image = bpy.data.images.get(candidate)
        except Exception:
            image = None
        if image is not None and _image_valid(image):
            return image, src
    return None, source

def report_material_input_detail(operator, fmdl_name, fmat, texture_dict):
    if not getattr(operator, "report_texture_details", False):
        return
    try:
        texture_refs = [str(getattr(ref, "name", "")) for ref in getattr(fmat, "texture_refs", []) or []]
        texture_lookup = []
        for tex_name in texture_refs:
            image, source = _resolve_texture_image(texture_dict, tex_name, getattr(operator, "name_prefix", ""))
            texture_lookup.append({"texture": tex_name, "source": source, "image": _image_state(image) if image is not None else None})
        shader_assign = getattr(fmat, "shader_assign", None)
        sampler_assigns = getattr(shader_assign, "sampler_assigns", {}) if shader_assign is not None else {}
        operator.report({"INFO"}, "MATERIAL_INPUT_DETAIL owner=%r fmdl=%r material=%r shader_archive=%r shader_model=%r texture_refs=%s sampler_assigns=%s texture_lookup=%s shaderparams=%s renderinfos=%s" % (
            getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmdl_name), str(getattr(fmat, "name", "<unnamed>")),
            str(getattr(shader_assign, "shader_archive_name", "")), str(getattr(shader_assign, "shader_model_name", "")),
            _safe_repr_short(texture_refs, 1000), _safe_repr_short(dict((str(k), str(v)) for k, v in getattr(sampler_assigns, "items", lambda: [])()), 1000),
            _safe_repr_short(texture_lookup, 2000), _safe_repr_short([str(k) for k in _mapping_keys(getattr(fmat, "shaderparams", None))], 1000), _safe_repr_short([str(k) for k in _mapping_keys(getattr(fmat, "renderinfos", None))], 1000)
        ))
    except Exception as exc:
        _diag(operator, "MATERIAL_INPUT_DETAIL_FAILED owner=%r fmdl=%r material=%r error=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmdl_name), str(getattr(fmat, "name", "<unnamed>")), exc), "WARNING")

def report_material_output_detail(operator, fmdl_name, fmat, mat):
    if not getattr(operator, "report_texture_details", False):
        return
    try:
        nodes = []
        if mat is not None and getattr(mat, "use_nodes", False) and mat.node_tree is not None:
            for node in mat.node_tree.nodes:
                if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
                    nodes.append({"node": getattr(node, "name", ""), "label": getattr(node, "label", ""), "image": _image_state(getattr(node, "image", None))})
        operator.report({"INFO"}, "MATERIAL_OUTPUT_DETAIL owner=%r fmdl=%r material=%r blender_material=%r valid_images=%s image_nodes=%s" % (
            getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmdl_name), str(getattr(fmat, "name", "<unnamed>")),
            getattr(mat, "name", None), _material_images_valid(mat), _safe_repr_short(nodes, 2000)
        ))
    except Exception as exc:
        _diag(operator, "MATERIAL_OUTPUT_DETAIL_FAILED owner=%r fmdl=%r material=%r error=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmdl_name), str(getattr(fmat, "name", "<unnamed>")), exc), "WARNING")

TEX_WRAPPER = {
    "_a0": "base_color_texture",
    "_r0": "roughness_texture",
    "_m0": "metallic_texture",
    "_n0": "normalmap_texture",
    "_e0": "emission_color_texture",
    "_op0": "alpha_texture",
}
TEXCOORD_SELECT = {
    "_ao0": "ao",
    "_cp0": "comppaint",
    "_e0": "emmmap",
    "_fm0": "sfxmask",
    "_m0": "mtlmap",
    "_r0": "rghmap",
    "_re0": "res0",
    "_re1": "res1",
    "_re2": "res2",
    "_su0": "teamcolormap",
    "_t0": "trsmap",  # unsure if this is opacity or trm
}


def import_material(fmat: Material, texture_dict, name_prefix, operator=None) -> bpy.types.Material:
    """Import specified material from fmat."""
    try:
        tex_names = tuple(str(ref.name) for ref in getattr(fmat, "texture_refs", []) or [])
        sampler_names = tuple(sorted((str(k), str(v)) for k, v in getattr(fmat.shader_assign, "sampler_assigns", {}).items()))
        shader_name = str(getattr(fmat.shader_assign, "shader_archive_name", ""))
        cache_key = (str(fmat.name), shader_name, tex_names, sampler_names)
        # Do not reuse cached Blender Material objects because their image nodes can
        # retain stale bpy.types.Image references after pack-stage imports.
        if cache_key in _OSI_MATERIAL_CACHE:
            _OSI_MATERIAL_CACHE.pop(cache_key, None)
            _diag(operator, "MATERIAL_CACHE_BYPASS owner=%r material=%r key=%r reason=no_live_material_reuse" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), cache_key))
    except Exception:
        cache_key = None
    if getattr(operator, "report_texture_details", False):
        _diag(operator, "MATERIAL_CREATE_DETAIL owner=%r material=%r name_prefix=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(getattr(fmat, "name", "<unnamed>")), str(name_prefix)))
    mat = bpy.data.materials.new(name=name_prefix + fmat.name)
    mat.use_nodes = True
    mat_wrap = PrincipledBSDFWrapper(mat, is_readonly=False)
    __add_custom_properties(fmat, mat)

    mappingnode = None
    # TODO: debug to see why removing tex_sampler breaks this
    texture_refs = list(getattr(fmat, "texture_refs", []) or [])
    for i, (sampler, tex_sampler) in enumerate(fmat.samplers.items()):
        try:
            if i >= len(texture_refs):
                _diag(operator, "MATERIAL_TEXTURE_MISSING owner=%r material=%r sampler=%r source=no_texture_ref_for_sampler" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), str(sampler)), "WARNING")
                continue
            tex_name = texture_refs[i].name
            tex_name_str = str(tex_name)
            image, image_source = _resolve_texture_image(texture_dict, tex_name, name_prefix)

            if image is None:
                log.warning("Missing Texture: '%s'", tex_name_str)
                _diag(operator, "MATERIAL_TEXTURE_MISSING owner=%r material=%r texture=%r source=%r texture_map_keys_sample=%s bpy_prefixed_valid=%s bpy_unprefixed_valid=%s" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), tex_name_str, image_source, _safe_repr_short([str(k) for k in _mapping_keys(texture_dict)[:20]], 500), _image_valid(bpy.data.images.get(name_prefix + tex_name_str)), _image_valid(bpy.data.images.get(tex_name_str))), "WARNING")
                continue

            tex_sampler_name = fmat.shader_assign.sampler_assigns.key_from_value(ResString(sampler))

            # Get the bpy Texture Wrapper from the sampler name
            tex_helper_name = TEX_WRAPPER.get(tex_sampler_name)
            if tex_helper_name:
                tex_helper: ShaderImageTextureWrapper = getattr(mat_wrap, tex_helper_name)
                try:
                    tex_helper.image = image
                except Exception as exc:
                    _diag(operator, "MATERIAL_TEXTURE_ASSIGN_FAILED owner=%r material=%r texture=%r sampler=%r image=%r error=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), tex_name_str, str(tex_sampler_name), getattr(image, "name", "<removed>"), exc), "WARNING")
                    continue
            else:
                # Add the texture as a node not connected to anything if no samplers match
                tex_node = mat.node_tree.nodes.new(type="ShaderNodeTexImage")
                log.warning("Unused texture: %s", tex_name_str)
                tex_node.location = mathutils.Vector(mat_wrap._grid_to_location(1, -2))
                tex_node.label = f"{tex_sampler_name} {tex_name_str}"
                try:
                    tex_node.image = image
                except Exception as exc:
                    _diag(operator, "MATERIAL_TEXTURE_ASSIGN_FAILED owner=%r material=%r texture=%r sampler=%r image=%r error=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), tex_name_str, str(tex_sampler_name), getattr(image, "name", "<removed>"), exc), "WARNING")
                    continue
                set_uv_coords(mat, tex_sampler_name, tex_node)
                continue

            # Mapping
            tex_mtx0_param = _mapping_get(getattr(fmat, "shaderparams", None), "tex_mtx0")
            if mat.get("MP_tex_mtx0_mode") and tex_mtx0_param is not None and getattr(tex_mtx0_param, "data", None):
                if mappingnode is None:
                    loc = mat["MP_tex_mtx0_translation"]
                    tex_helper.translation = mathutils.Vector((loc[0], loc[1], 0))

                    rot = mat["MP_tex_mtx0_rotation"]
                    tex_helper.rotation = mathutils.Vector((0, 0, rot))

                    scale = mat["MP_tex_mtx0_scaling"]
                    scalemode = mat["MP_tex_mtx0_mode"]
                    # XXX: Needs improvement
                    match scalemode:
                        case "MODE_MAYA":
                            scalevec = (1 / scale[0], 1 / scale[1], 1)
                        case _:
                            scalevec = (scale[0], scale[1], 1)
                    tex_helper.scale = mathutils.Vector(scalevec)

                    mappingnode = tex_helper.node_mapping
                else:
                    mat.node_tree.links.new(mappingnode.outputs["Vector"], tex_helper.node_image.inputs["Vector"])

            set_uv_coords(mat, tex_sampler_name, tex_helper.node_image)
        except Exception as exc:
            _diag(operator, "MATERIAL_TEXTURE_SLOT_FAILED owner=%r material=%r sampler=%r error=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), str(sampler), exc), "WARNING")
            continue

    try:
        match fmat.shader_assign.shader_archive_name:
            case "Hoian_UBER":
                __shader_hoian(mat, mat_wrap)
    except Exception as exc:
        _diag(operator, "MATERIAL_SHADER_POSTPROCESS_FAILED owner=%r material=%r shader_archive=%r error=%r action=keep_partial_material" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), str(getattr(fmat.shader_assign, "shader_archive_name", "")), exc), "WARNING")

    try:
        if cache_key is not None:
            mat["osi_material_cache_key"] = repr(cache_key)
            _diag(operator, "MATERIAL_CACHE_STORE_META owner=%r material=%r key=%r note=no_blender_material_object_cached" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(fmat.name), cache_key))
    except Exception:
        pass
    return mat


def set_uv_coords(mat: bpy.types.Material, sampler: str, tex_node: bpy.types.ShaderNodeTexImage):
    selector = TEXCOORD_SELECT.get(sampler)
    if selector is None:
        return
    uv_id = mat.get("SO_texcoord_select_" + selector)
    if uv_id and uv_id != "0":
        tree = mat.node_tree

        uv_node = tree.nodes.new(type="ShaderNodeUVMap")
        uv_node.uv_map = f"_u{uv_id}"
        socket_src = uv_node.outputs["UV"]

        tree.links.new(socket_src, tex_node.inputs["Vector"])


def __add_custom_properties(fmat, mat: bpy.types.Material):
    """Add render/shader/material parameters and sampler list
    as custom properties on the Blender material object.
    """
    # Make Render Info
    for name, param in fmat.renderinfos.items():
        val = param.data
        if len(param.data) == 1:
            val = val[0]
        mat["RI_" + name] = val

    # Make Material Params
    for name, param in fmat.shaderparams.items():
        pname = "MP_" + name
        if param.type.name in {"TEX_SRT", "TEX_SRT_EX", "SRT2D", "SRT3D"}:
            if param.type.name in {"TEX_SRT", "TEX_SRT_EX"}:
                mat[pname + "_mode"] = param.data.mode.name
            mat[pname + "_scaling"] = param.data.scaling
            mat[pname + "_rotation"] = param.data.rotation
            mat[pname + "_translation"] = param.data.translation

            propman = mat.id_properties_ui(pname + "_scaling")
            propman.update(subtype="XYZ")
            propman = mat.id_properties_ui(pname + "_rotation")
            if param.type.name == "SRT3D":
                propman.update(subtype="EULER")
            else:
                propman.update(subtype="ANGLE")
            propman = mat.id_properties_ui(pname + "_translation")
            propman.update(subtype="XYZ")
        else:
            mat[pname] = param.data
            if param.type.name == "FLOAT4":
                propman = mat.id_properties_ui(pname)
                propman.update(subtype="COLOR_GAMMA", min=0, max=1, step=0.01)

    for name, val in fmat.shader_assign.shaderoptions.items():
        mat["SO_" + name] = str(val)

    mat["samplers"] = {key: str(value) for key, value in fmat.shader_assign.sampler_assigns.items()}


def __shader_hoian(mat, mat_wrap: PrincipledBSDFWrapper):
    # Small changes for Splatoon 3 shaders
    if mat.get("SO_enable_albedo_tex") == "false":
        mat_wrap.base_color = mat["MP_albedo_color"][:3]

    if mat.get("SO_enable_roughness_map") != "true":
        mat_wrap.roughness = mat["MP_roughness"]

    if mat.get("SO_enable_metalness_map") != "true":
        mat_wrap.metallic = mat["MP_metalness"]

    if mat.get("SO_enable_opacity_tex") != "true":
        mat_wrap.alpha = mat["MP_opacity"]

    if mat.get("SO_enable_emission_map") != "true":
        mat_wrap.emission_color = mat["MP_emission_color"][:3]

    if mat.get("SO_emission_color_type") == "1":
        try:
            img = mat_wrap.base_color_texture.image
            if _image_valid(img):
                mat_wrap.emission_color_texture.image = img
        except Exception:
            pass

    if mat.get("SO_enable_emission") == "true":
        mat_wrap.emission_strength = mat["MP_emission_intensity"]
    else:
        # for blender 3.6
        mat_wrap.emission_strength = 0.0
