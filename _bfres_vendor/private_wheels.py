"""Private wheel loader for io_scene_osi's vendored BFRES dependencies.

This module intentionally avoids permanently adding bundled wheel files to
``sys.path``. Native extension modules cannot be imported reliably from inside a
zip wheel, and leaving wheel paths in Blender's global import path can shadow or
break other add-ons. Instead, we extract the platform-compatible wheel to a
private directory and add that directory only for the duration of the import.
"""

from __future__ import annotations

import importlib
import platform
import sys
import zipfile
from pathlib import Path
from types import ModuleType

_VENDOR_DIR = Path(__file__).resolve().parent
_WHEELS_DIR = _VENDOR_DIR / "wheels"
_EXTRACT_ROOT = _VENDOR_DIR / "_wheel_site"


def _platform_tags() -> list[str]:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return ["macosx", "arm64" if machine in {"arm64", "aarch64"} else "x86_64"]
    if sys.platform.startswith("win"):
        return ["win", "amd64" if machine in {"amd64", "x86_64"} else machine]
    if sys.platform.startswith("linux"):
        return ["manylinux", "x86_64" if machine in {"amd64", "x86_64"} else machine]
    return []


def _matching_wheel(distribution: str) -> Path | None:
    if not _WHEELS_DIR.is_dir():
        return None

    tags = _platform_tags()
    prefix = distribution.replace("-", "_").lower()
    candidates: list[Path] = []
    for wheel in sorted(_WHEELS_DIR.glob("*.whl")):
        name = wheel.name.lower()
        if not name.startswith(prefix):
            continue
        if tags and not all(tag in name for tag in tags):
            continue
        candidates.append(wheel)
    return candidates[0] if candidates else None


def _extract_wheel(wheel: Path) -> Path:
    target = _EXTRACT_ROOT / wheel.stem
    marker = target / ".io_scene_osi_extracted"
    if marker.exists():
        return target

    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(target)
    marker.write_text(wheel.name, encoding="utf-8")
    return target


def import_private(distribution: str, module_name: str | None = None) -> ModuleType:
    """Import a vendored module without permanently modifying global sys.path.

    ``distribution`` is the wheel/distribution name, e.g. ``texture2ddecoder``.
    ``module_name`` defaults to the same value.
    """
    module_name = module_name or distribution

    # Reuse an already-loaded module if Blender or another add-on has provided a
    # compatible copy. This avoids replacing global module state.
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    wheel = _matching_wheel(distribution)
    if wheel is None:
        return importlib.import_module(module_name)

    site_dir = str(_extract_wheel(wheel))
    inserted = False
    if site_dir not in sys.path:
        sys.path.insert(0, site_dir)
        inserted = True
    try:
        return importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(site_dir)
            except ValueError:
                pass
