"""Structured import report helpers for the Splatoon 3 stage importer.

This module is intentionally Blender-light.  It keeps report collection and
phase accounting separate from the importer so future code can emit structured
records without building ad-hoc strings everywhere.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional


def _fmt_value(value):
    if isinstance(value, str):
        return repr(value)
    return repr(value)


@dataclass
class ImportReport:
    """Append-compatible report object with structured event helpers.

    Existing importer code expects a plain list and calls ``append`` directly.
    This class preserves that API while adding ``event`` and ``phase`` helpers.
    """

    full_report_check: Optional[Callable[[], bool]] = None
    lines: List[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    def append(self, line: str) -> None:
        self.lines.append(str(line))
        token = str(line).split(" ", 1)[0] if line else "EMPTY"
        self.counts[token] += 1

    def extend(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.append(line)

    def __iter__(self):
        return iter(self.lines)

    def __len__(self):
        return len(self.lines)

    def __getitem__(self, item):
        return self.lines[item]

    def is_full(self) -> bool:
        try:
            return bool(self.full_report_check and self.full_report_check())
        except Exception:
            return False

    def event(self, tag: str, *, full_only: bool = False, **fields) -> None:
        if full_only and not self.is_full():
            return
        if fields:
            payload = " ".join("%s=%s" % (key, _fmt_value(value)) for key, value in fields.items())
            self.append("%s %s" % (tag, payload))
        else:
            self.append(tag)

    def phase_start(self, name: str, **fields) -> None:
        self.event("PHASE_START", phase=name, **fields)

    def phase_end(self, name: str, **fields) -> None:
        self.event("PHASE_END", phase=name, **fields)

    @contextmanager
    def phase(self, name: str, start_fields: Optional[Dict] = None, end_fields: Optional[Callable[[], Dict]] = None):
        self.phase_start(name, **(start_fields or {}))
        try:
            yield self
        except Exception as exc:
            fields = end_fields() if callable(end_fields) else {}
            fields.update({"status": "FAILED", "error": str(exc)})
            self.phase_end(name, **fields)
            raise
        else:
            fields = end_fields() if callable(end_fields) else {}
            fields.setdefault("status", "OK")
            self.phase_end(name, **fields)
