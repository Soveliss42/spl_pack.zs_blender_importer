bl_info = {
    "name": "Splatoon 3 Stage Importer",
    "description": "Import Splatoon 3 stages from bcett.byml or RomFS Pack/Scene .pack.zs archives using .bfres.zs models.",
    "author": "Soveliss42, using Philippus229's SMO Importer & Ranidspace's bfres importer, built with ChatGPT",
    "version": (0, 8, 96),
    "blender": (3, 6, 0),
    "location": "File > Import",
    "warning": "Community port; stage import coverage is still experimental.",
    "wiki_url": "n/a",
    "tracker_url": "n/a",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    try:
        from . import lifecycle as _osi_lifecycle
        _osi_lifecycle.clear_runtime_caches()
        _osi_lifecycle.purge_vendor_modules()
    except Exception:
        pass
    # Reload foundational/shared helper modules first.  Split importer modules
    # consume these helpers and addon.py binds selected split-module functions.
    # Keeping this order explicit prevents stale in-memory wrappers from being
    # rebound over freshly imported shared helpers during Blender add-on reloads.
    for _module in (
        "lifecycle", "actor_helpers", "property_utils", "value_utils", "rsdb_helpers",
        "collection_utils", "report_utils", "pack_resources",
        "report", "session", "transform", "pack_context", "romfs_resolver",
        "rsdb_loader", "byml", "sarc", "bfres_import_bridge",
    ):
        if _module in locals():
            importlib.reload(locals()[_module])
    for _module in ("addon", "actor_import", "non_geometry", "rails", "team_colors", "layer_visibility", "background", "graffiti", "importing"):
        if _module in locals():
            importlib.reload(locals()[_module])
    # addon.py may have been reloaded while the split modules were still their
    # previous in-memory versions. Refresh only names actually owned by those
    # split modules; core/shared helper aliases are deliberately protected.
    try:
        if "addon" in locals() and "actor_import" in locals() and "non_geometry" in locals():
            addon._install_split_module_helpers(actor_import, non_geometry, replace_owned=True)
            addon._sync_split_module_dependencies(actor_import, non_geometry)
    except Exception:
        pass

import bpy
from . import lifecycle, actor_helpers, property_utils, value_utils, rsdb_helpers, collection_utils, report_utils, pack_resources, addon, importing, actor_import, non_geometry, rails, team_colors, layer_visibility, background, graffiti, byml, sarc, report, session, transform, pack_context, romfs_resolver, rsdb_loader, bfres_import_bridge

# First-load path: keep addon.py bound to the final module objects imported above.
try:
    addon._install_split_module_helpers(actor_import, non_geometry, replace_owned=True)
    addon._sync_split_module_dependencies(actor_import, non_geometry)
except Exception:
    pass

classes = (
    addon.osiAddonPreferences,
    importing.ImportOperator,
    importing.ImportPackOperator,
)


def register():
    try:
        lifecycle.clear_runtime_caches()
        lifecycle.purge_vendor_modules()
    except Exception:
        pass
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.UILayout.osi_colbox = addon.osi_colbox
    team_colors.register()
    layer_visibility.register()
    bpy.types.TOPBAR_MT_file_import.append(importing.ImportOperator.menu_func)
    bpy.types.TOPBAR_MT_file_import.append(importing.ImportPackOperator.menu_func)


def unregister():
    try:
        lifecycle.clear_runtime_caches()
    except Exception:
        pass
    bpy.types.TOPBAR_MT_file_import.remove(importing.ImportPackOperator.menu_func)
    bpy.types.TOPBAR_MT_file_import.remove(importing.ImportOperator.menu_func)
    try:
        team_colors.unregister()
    except Exception:
        pass
    try:
        layer_visibility.unregister()
    except Exception:
        pass
    if hasattr(bpy.types.UILayout, "osi_colbox"):
        del bpy.types.UILayout.osi_colbox
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    try:
        lifecycle.clear_runtime_caches()
        lifecycle.purge_vendor_modules()
    except Exception:
        pass
