"""Small reusable RSDB table cache helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


@dataclass
class RsdbRegistry:
    tables: Dict[str, Any] = field(default_factory=dict)
    source_paths: Dict[str, str] = field(default_factory=dict)

    def store(self, prefix: str, rows: Any, path: str = "") -> Any:
        self.tables[prefix] = rows
        if path:
            self.source_paths[prefix] = str(path)
        return rows

    def get(self, prefix: str, default=None):
        return self.tables.get(prefix, default)

    def clear(self):
        self.tables.clear()
        self.source_paths.clear()
