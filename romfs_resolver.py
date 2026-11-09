"""RomFS and Work/ path resolution helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class RomfsResolver:
    romfs_root: Optional[Path] = None
    pack_context: object = None
    _basename_cache: Dict[str, List[Path]] = field(default_factory=dict)
    _indexed: bool = False

    def _variants(self, ref: str) -> List[str]:
        ref = str(ref or "").replace("\\", "/").lstrip("/")
        out = []
        def add(v):
            v = str(v or "").replace("\\", "/").lstrip("/")
            if v and v not in out:
                out.append(v)
        add(ref)
        if ref.startswith("Work/"):
            add(ref[5:])
        else:
            add("Work/" + ref)
        for ext in (".zs", ".byml", ".bgyml", ".gyml", ".bntx"):
            if ref and not ref.endswith(ext):
                add(ref + ext)
                add(ref + ext + ".zs")
        return out

    def _index(self):
        if self._indexed:
            return
        root = Path(self.romfs_root) if self.romfs_root else None
        if root and root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    self._basename_cache.setdefault(path.name.casefold(), []).append(path)
        self._indexed = True

    def resolve_path(self, ref: str, basename_fallback: bool = True):
        pack = self.pack_context
        for cand in self._variants(ref):
            if pack is not None and hasattr(pack, "files") and cand in pack.files:
                return {"location": "pack", "path": cand, "bytes": pack.files[cand]}
            root = Path(self.romfs_root) if self.romfs_root else None
            if root:
                p = root / cand
                if p.exists():
                    return {"location": "romfs", "path": str(p), "bytes": None}
        if basename_fallback:
            self._index()
            for cand in self._variants(ref):
                matches = self._basename_cache.get(Path(cand).name.casefold()) or []
                if matches:
                    p = sorted(matches, key=lambda x: (len(str(x)), str(x)))[0]
                    return {"location": "romfs_basename", "path": str(p), "bytes": None}
        return {"location": "missing", "path": None, "bytes": None}
