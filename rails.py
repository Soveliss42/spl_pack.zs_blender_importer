"""Actor-linked BCETT rail handling for the Splatoon 3 stage importer.

Rails[] entries in BCETT are path data.  They are not imported blindly as
standalone renderables; actors that reference rails decide how a path should be
visualized:

* spl__InkRailBancParam / InkRailOnline -> reconstructed InkRail geometry
* spl__GachiyaguraBancParam -> Gachiyagura rail visual helpers
* spl__ailift__AILiftBancParam -> motion-path helper

This module is the stable public boundary for rail dispatch.  The lower-level
construction helpers currently live in non_geometry.py because they share the
same Blender collection/material utilities; callers should use this module
instead of binding underscore-prefixed non_geometry helpers directly.
"""

from __future__ import annotations

from . import non_geometry as _ng


def is_liftrail_inkrail_actor(entry, model_path=None):
    return _ng._is_liftrail_inkrail_actor(entry, model_path)


def is_gachiyagura_bancparam_actor(entry):
    return _ng._is_gachiyagura_bancparam_actor(entry)


def is_ailift_bancparam_actor(entry):
    return _ng._is_ailift_bancparam_actor(entry)


def try_import_liftrail_inkrail_geometry(entry, actor_label, layer_label, model_path, prefs, import_cache, report_lines, stage_collection, banc_anchor_index):
    return _ng._try_import_liftrail_inkrail_geometry(
        entry,
        actor_label,
        layer_label,
        model_path,
        prefs,
        import_cache,
        report_lines,
        stage_collection,
        banc_anchor_index,
    )


def try_import_gachiyagura_rail_geometry(entry, actor_label, layer_label, prefs, import_cache, report_lines, stage_collection, banc_anchor_index):
    return _ng._try_import_gachiyagura_rail_geometry(
        entry,
        actor_label,
        layer_label,
        prefs,
        import_cache,
        report_lines,
        stage_collection,
        banc_anchor_index,
    )


def try_import_motion_path_rail_geometry(entry, actor_label, layer_label, prefs, import_cache, report_lines, stage_collection, banc_anchor_index, kind="motion"):
    return _ng._try_import_motion_path_rail_geometry(
        entry,
        actor_label,
        layer_label,
        prefs,
        import_cache,
        report_lines,
        stage_collection,
        banc_anchor_index,
        kind=kind,
    )


def geometry_rail_layer_collection(stage_collection, layer_label):
    return _ng._geometry_rail_layer_collection(stage_collection, layer_label)


def cleanup_inkrail_full_templates(import_cache, report_lines):
    return _ng._cleanup_inkrail_full_templates(import_cache, report_lines)


def cleanup_legacy_inkrail_collections(stage_collection, report_lines):
    cleanup = getattr(_ng, "_cleanup_legacy_inkrail_collections", None)
    if callable(cleanup):
        return cleanup(stage_collection, report_lines)
    return 0


def rail_array_skip_report(report_lines):
    """Record the current policy for BCETT Rails[]."""
    report_lines.append("RAIL_ARRAY_SKIPPED reason=actor_linked_rail_import_only module=rails")
