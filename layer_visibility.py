"""Splatoon 3 layer/category visibility controls for the 3D View sidebar."""

from __future__ import annotations

import re

import bpy

LAYER_LABELS = (
    ("Cmn", "Cmn"),
    ("Pnt", "Turf War"),
    ("Var", "Splat Zones"),
    ("Vlf", "Tower Control"),
    ("Vgl", "Rainmaker"),
    ("Vcl", "Clam Blitz"),
    ("Tcl", "Tricolor"),
    ("Background", "Background"),
    ("Day", "Day"),
    ("Night", "Night"),
    ("Sunset", "Sunset"),
)

CATEGORY_LABELS = (
    ("geometry", "Geometry"),
    ("graffiti", "Graffiti"),
    ("rails", "Rails"),
    ("locators", "Locators"),
    ("areas", "Areas"),
    ("cameras", "Cameras"),
    ("background", "Background"),
    ("environment", "Environment"),
    ("lights", "Lights"),
    ("helpers", "Helpers"),
)

_LAYER_CATEGORY_OVERRIDES = {
    # Background is intentionally a first-class selector tab, not a sub-toggle
    # under Cmn/Pnt/Var/etc.
    "Background": ("background",),
    # Rendering tabs operate only on the render-context collections.
    "Day": ("geometry", "environment", "lights"),
    "Night": ("geometry", "environment", "lights"),
    "Sunset": ("geometry", "environment", "lights"),
}

def _categories_for_layer(layer_key: str):
    if layer_key in _LAYER_CATEGORY_OVERRIDES:
        return _LAYER_CATEGORY_OVERRIDES[layer_key]
    cats = [k for k, _ in CATEGORY_LABELS if k not in {"background", "environment", "lights"}]
    # Cameras are authored in the common layer.  Avoid showing/toggling camera
    # collections from mode-specific tabs.
    if layer_key != "Cmn":
        cats = [k for k in cats if k != "cameras"]
    return tuple(cats)

_LAYER_KEYS = tuple(k for k, _ in LAYER_LABELS)
_CATEGORY_KEYS = tuple(k for k, _ in CATEGORY_LABELS)


def _prop_name(layer_key: str, category_key: str) -> str:
    return "spl_layer_vis_%s_%s" % (layer_key.lower(), category_key)


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _collection_paths():
    root = bpy.context.scene.collection if bpy.context and bpy.context.scene else None
    if root is None:
        return []
    out = []

    def visit(col, path):
        next_path = path + [col.name]
        out.append((col, next_path))
        for child in col.children:
            visit(child, next_path)

    visit(root, [])
    return out


def _path_text(path) -> str:
    return "/".join(str(p) for p in path)


def _path_segments(path):
    segs = []
    for part in path:
        text = str(part)
        segs.append(text)
        # Blender collection names commonly get ".001" suffixes.
        segs.append(re.sub(r"\.\d+$", "", text))
    return segs


def _matches_layer(path, layer_key: str) -> bool:
    text = _norm(_path_text(path))
    if layer_key == "Background":
        return "background" in text
    if layer_key in {"Day", "Night", "Sunset"}:
        wanted_rendering = "rendering" + _norm(layer_key)
        return wanted_rendering in text or _norm(layer_key) in [_norm(s) for s in _path_segments(path)]
    wanted = _norm(layer_key)
    aliases = {wanted}
    label = dict(LAYER_LABELS).get(layer_key)
    if label:
        aliases.add(_norm(label))
    for seg in _path_segments(path):
        n = _norm(seg)
        if n in aliases:
            return True
        # Some collection names include the layer as a prefix/suffix, e.g. "Locators/Pnt" or "Pnt_Rail".
        if n.startswith(wanted) or n.endswith(wanted):
            return True
    return False


def _matches_category(path, category_key: str) -> bool:
    text = _norm(_path_text(path))
    segs = [_norm(s) for s in _path_segments(path)]
    name = _norm(path[-1] if path else "")

    if category_key == "geometry":
        return "geometry" in segs or "geometry" in text
    if category_key == "graffiti":
        return "graffiti" in text
    if category_key == "rails":
        return any(s in text for s in ("rail", "inkrail", "liftrail", "gachiyagura"))
    if category_key == "locators":
        return any(s in text for s in ("locator", "locators"))
    if category_key == "areas":
        return any(s in text for s in ("area", "painttargetarea", "changepaintablearea"))
    if category_key == "cameras":
        return any(s in text for s in ("camera", "cameras", "snapshot"))
    if category_key == "background":
        return "background" in text
    if category_key == "environment":
        return any(s in text for s in ("environment", "envparams", "skysphere", "sky", "water", "ocean"))
    if category_key == "lights":
        return any(s in text for s in ("mainlight", "light", "lighting"))
    if category_key == "helpers":
        return any(s in text for s in ("helper", "helpers", "debug", "teamcolor", "painttargetoutline")) or name.startswith("spl3teamcolor")
    return False


def _matching_collections(layer_key: str, category_key: str):
    matches = []
    for col, path in _collection_paths():
        if _matches_layer(path, layer_key) and _matches_category(path, category_key):
            # Do not hide the scene root itself.
            if col != bpy.context.scene.collection:
                matches.append(col)
    return matches



def _layer_collections_by_collection():
    """Map Collection datablocks to all LayerCollection wrappers in the active view layer."""
    mapping = {}
    view_layer = getattr(bpy.context, "view_layer", None) if bpy.context else None
    root = getattr(view_layer, "layer_collection", None) if view_layer is not None else None
    if root is None:
        return mapping

    def visit(layer_col):
        col = getattr(layer_col, "collection", None)
        if col is not None:
            mapping.setdefault(col, []).append(layer_col)
        for child in getattr(layer_col, "children", []) or []:
            visit(child)

    visit(root)
    return mapping


def _set_collection_excluded(col, excluded: bool, layer_map=None) -> int:
    """Set Exclude from View Layer for every active LayerCollection wrapper."""
    count = 0
    layer_map = layer_map if layer_map is not None else _layer_collections_by_collection()
    for layer_col in layer_map.get(col, []) or []:
        try:
            layer_col.exclude = bool(excluded)
            count += 1
        except Exception:
            pass
    return count


def _apply_visibility_target(scene=None, layer_key: str | None = None, category_key: str | None = None):
    scene = scene or bpy.context.scene
    if scene is None:
        return 0
    count = 0
    layer_map = _layer_collections_by_collection()
    layer_keys = [layer_key] if layer_key in _LAYER_KEYS else list(_LAYER_KEYS)
    for lk in layer_keys:
        cats = _categories_for_layer(lk)
        category_keys = [category_key] if category_key in cats else list(cats)
        for ck in category_keys:
            prop = _prop_name(lk, ck)
            visible = bool(getattr(scene, prop, True))
            for col in _matching_collections(lk, ck):
                # Toggle Exclude from View Layer rather than only collection
                # hide_viewport, so hidden layers are omitted from the active
                # ViewLayer/render dependency graph as users expect.
                count += _set_collection_excluded(col, not visible, layer_map)
    return count


def apply_visibility(scene=None):
    """Full refresh used by the Apply Layer Visibility button."""
    return _apply_visibility_target(scene, None, None)


def apply_visibility_targeted(scene=None, layer_key: str | None = None, category_key: str | None = None):
    """Refresh exactly one layer/category toggle."""
    return _apply_visibility_target(scene, layer_key, category_key)


def _update_visibility_targeted(layer_key: str, category_key: str):
    def _update(self, context):
        try:
            apply_visibility_targeted(context.scene, layer_key, category_key)
        except Exception:
            pass
    return _update


def _update_visibility(self, context):
    # Kept for compatibility with older registered properties.  New properties
    # use _update_visibility_targeted so a single toggle does not refresh all
    # other tabs/categories.
    try:
        apply_visibility(context.scene)
    except Exception:
        pass


class SPL3_OT_apply_layer_visibility(bpy.types.Operator):
    bl_idname = "spl3.apply_layer_visibility"
    bl_label = "Apply Layer Visibility"
    bl_description = "Apply the Spl3 layer/category visibility matrix to matching imported collections"

    def execute(self, context):
        count = apply_visibility(context.scene)
        self.report({"INFO"}, "Updated %d collection visibility setting(s)" % count)
        return {"FINISHED"}


class SPL3_OT_show_all_layers(bpy.types.Operator):
    bl_idname = "spl3.show_all_layers"
    bl_label = "Show All"
    bl_description = "Enable all Spl3 layer/category visibility toggles"

    def execute(self, context):
        scene = context.scene
        for layer_key in _LAYER_KEYS:
            for category_key in _categories_for_layer(layer_key):
                try:
                    setattr(scene, _prop_name(layer_key, category_key), True)
                except Exception:
                    pass
        apply_visibility(scene)
        return {"FINISHED"}


class SPL3_OT_hide_all_layers(bpy.types.Operator):
    bl_idname = "spl3.hide_all_layers"
    bl_label = "Hide All"
    bl_description = "Disable all Spl3 layer/category visibility toggles"

    def execute(self, context):
        scene = context.scene
        for layer_key in _LAYER_KEYS:
            for category_key in _categories_for_layer(layer_key):
                try:
                    setattr(scene, _prop_name(layer_key, category_key), False)
                except Exception:
                    pass
        apply_visibility(scene)
        return {"FINISHED"}


class SPL3_OT_set_layer_visibility(bpy.types.Operator):
    bl_idname = "spl3.set_layer_visibility"
    bl_label = "Set Layer Visibility"
    bl_description = "Show or hide all category toggles for one Spl3 layer"

    layer_key: bpy.props.StringProperty(default="")
    visible: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        scene = context.scene
        layer_key = str(self.layer_key or "")
        if layer_key not in _LAYER_KEYS:
            self.report({"WARNING"}, "Unknown layer: %s" % layer_key)
            return {"CANCELLED"}
        for category_key in _categories_for_layer(layer_key):
            try:
                setattr(scene, _prop_name(layer_key, category_key), bool(self.visible))
            except Exception:
                pass
        apply_visibility(scene)
        return {"FINISHED"}


class SPL3_PT_layer_selector(bpy.types.Panel):
    bl_label = "Layer Selector"
    bl_idname = "SPL3_PT_layer_selector"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Spl3"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        row = layout.row(align=True)
        row.operator(SPL3_OT_apply_layer_visibility.bl_idname, icon="FILE_REFRESH")
        row.operator(SPL3_OT_show_all_layers.bl_idname, icon="HIDE_OFF")
        row.operator(SPL3_OT_hide_all_layers.bl_idname, icon="HIDE_ON")

        layout.label(text="Rows are game layers; columns are imported categories.")
        for layer_key, layer_label in LAYER_LABELS:
            box = layout.box()
            row = box.row(align=True)
            row.label(text="%s  (%s)" % (layer_label, layer_key), icon="OUTLINER_COLLECTION")
            op = row.operator(SPL3_OT_set_layer_visibility.bl_idname, text="Show Layer", icon="HIDE_OFF")
            op.layer_key = layer_key
            op.visible = True
            op = row.operator(SPL3_OT_set_layer_visibility.bl_idname, text="Hide Layer", icon="HIDE_ON")
            op.layer_key = layer_key
            op.visible = False
            grid = box.grid_flow(columns=3, even_columns=True, even_rows=False, align=True)
            labels = dict(CATEGORY_LABELS)
            for category_key in _categories_for_layer(layer_key):
                category_label = labels.get(category_key, category_key)
                prop = _prop_name(layer_key, category_key)
                grid.prop(scene, prop, text=category_label, toggle=True)


_CLASSES = (
    SPL3_OT_apply_layer_visibility,
    SPL3_OT_show_all_layers,
    SPL3_OT_hide_all_layers,
    SPL3_OT_set_layer_visibility,
    SPL3_PT_layer_selector,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for layer_key, layer_label in LAYER_LABELS:
        labels = dict(CATEGORY_LABELS)
        for category_key in _categories_for_layer(layer_key):
            category_label = labels.get(category_key, category_key)
            prop = _prop_name(layer_key, category_key)
            setattr(
                bpy.types.Scene,
                prop,
                bpy.props.BoolProperty(
                    name="%s %s" % (layer_label, category_label),
                    description="Show/hide %s collections for %s (%s)" % (category_label, layer_label, layer_key),
                    default=True,
                    update=_update_visibility_targeted(layer_key, category_key),
                ),
            )


def unregister():
    for layer_key, _ in LAYER_LABELS:
        for category_key in _categories_for_layer(layer_key):
            prop = _prop_name(layer_key, category_key)
            if hasattr(bpy.types.Scene, prop):
                delattr(bpy.types.Scene, prop)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
