"""Per-import session state.

The add-on historically used several module-level caches.  ImportSession gives
one explicit place for caches and report state, making it easier to clear or
isolate data between imports and between BCETT geometry versus pack extras.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .report import ImportReport
from . import lifecycle


@dataclass
class ImportSession:
    prefs: Any = None
    models_dir: Any = None
    romfs_root: Any = None
    pack_context: Any = None
    report: ImportReport = field(default_factory=ImportReport)

    # Explicit cache namespaces.  Texture/material caches remain disabled by
    # default in current builds because Blender Image datablocks are fragile
    # across import passes.
    bfres_geometry_cache: Dict[Any, Any] = field(default_factory=dict)
    srthash_instance_cache: Dict[Any, Any] = field(default_factory=dict)
    resolved_path_cache: Dict[Any, Any] = field(default_factory=dict)
    rsdb_tables: Dict[str, Any] = field(default_factory=dict)
    pack_component_cache: Dict[Any, Any] = field(default_factory=dict)

    texture_cache_enabled: bool = False
    material_cache_enabled: bool = False

    def cache(self, name: str) -> Dict[Any, Any]:
        return getattr(self, name)

    def clear_geometry_runtime_caches(self) -> None:
        self.bfres_geometry_cache.clear()
        self.srthash_instance_cache.clear()

    def close(self) -> None:
        """Release transient import-session and vendored runtime caches."""
        self.bfres_geometry_cache.clear()
        self.srthash_instance_cache.clear()
        self.resolved_path_cache.clear()
        self.pack_component_cache.clear()
        # Keep RSDB tables only inside this now-discarded session object. Clear
        # vendored/module caches that can survive an add-on reload.
        self.rsdb_tables.clear()
        try:
            cleared = lifecycle.clear_runtime_caches()
            self.report.event("IMPORT_SESSION_CLOSED", cleared_runtime_caches=cleared)
        except Exception as exc:
            try:
                self.report.event("IMPORT_SESSION_CLOSE_FAILED", error=str(exc))
            except Exception:
                pass

    def report_lines(self):
        return self.report.lines
