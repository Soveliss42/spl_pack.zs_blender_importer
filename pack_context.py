"""Pack-local virtual filesystem helpers for .pack.zs stage imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional


@dataclass
class PackContext:
    pack_path: str = ""
    files: Dict[str, bytes] = field(default_factory=dict)

    @property
    def stage_stem(self) -> str:
        name = Path(self.pack_path).name
        for suffix in (".pack.zs", ".pack", ".zs"):
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return Path(name).stem

    def get(self, name: str) -> Optional[bytes]:
        return self.files.get(name)

    def names(self) -> Iterable[str]:
        return self.files.keys()

    def by_basename(self, basename: str):
        base = Path(str(basename)).name.casefold()
        for name, data in self.files.items():
            if Path(name).name.casefold() == base:
                return name, data
        return None, None
