"""BFRES import boundary helpers.

The bridge keeps the vendor importer isolated from BCETT/pack logic.  The
current add-on still calls the legacy functions in addon.py, but this module is
available for new code paths and records the intended boundary explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Tuple


@dataclass
class BfresImportRequest:
    filepath: str
    actor_label: str = ""
    name_prefix: str = ""
    target_model_names: tuple = ()
    owner: str = "BCETT"

    @property
    def cache_key(self):
        return (str(Path(self.filepath).resolve()), tuple(self.target_model_names), self.owner)
