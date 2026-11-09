#!/usr/bin/env python3
"""Private vendored BFRES reader used by io_scene_osi.

This package is intentionally not a Blender add-on and does not register
operators, menus, bl_info, or extension manifests. Keeping it private avoids
colliding with separately installed SZS/BYML or BFRES add-ons.
"""

__all__ = []
