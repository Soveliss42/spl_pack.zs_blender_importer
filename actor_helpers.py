from __future__ import annotations

from pathlib import Path


def clean_actor_token(value):
    value = str(value or "").strip()
    if not value:
        return ""
    name = Path(value).name
    for suffix in (
        ".engine__actor__ActorParam.gyml",
        ".engine__actor__ActorParam.bgyml",
        ".gyml",
        ".bgyml",
        ".byml",
    ):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name or value


def actor_token_variants(value):
    """Return lookup variants for BCETT actor names."""
    variants = []

    def add(v):
        v = str(v or "").strip()
        if v and v not in variants:
            variants.append(v)

    for base in (value, clean_actor_token(value)):
        base = str(base or "").strip()
        if not base:
            continue
        add(base)
        for prefix in ("Lft_", "Mpt_"):
            if base.startswith(prefix) and len(base) > len(prefix):
                stripped = base[len(prefix):]
                add(stripped)
                add(("Mpt_" if prefix == "Lft_" else "Lft_") + stripped)
    return variants


def flatten_actor_items(value, prefix=""):
    if isinstance(value, dict):
        for key, sub in value.items():
            name = (prefix + "." + str(key)) if prefix else str(key)
            yield name, sub
            yield from flatten_actor_items(sub, name)
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            name = "%s[%d]" % (prefix, i) if prefix else "[%d]" % i
            yield name, sub
            yield from flatten_actor_items(sub, name)


def actor_first_value_by_key(entry, key_parts):
    key_parts = tuple(str(part).casefold() for part in key_parts)
    for path, value in flatten_actor_items(entry):
        lower = path.casefold()
        if all(part in lower for part in key_parts):
            return value, path
    return None, ""


def entry_tokens(entry):
    """Return normalized searchable names for a BCETT actor entry."""
    if not isinstance(entry, dict):
        return []
    values = []
    for key in ("Name", "Gyaml", "__RowId", "ModelName", "UnitConfigName"):
        value = entry.get(key)
        if value not in (None, ""):
            values.append(str(value))
            cleaned = clean_actor_token(value)
            if cleaned:
                values.append(str(cleaned))
    return values


def gfx_light_kind(entry):
    """Return Blender light type for supported Gfx*Light actor names."""
    for token in entry_tokens(entry):
        lower = token.casefold()
        if "gfx" not in lower or "light" not in lower:
            continue
        if "spot" in lower:
            return "SPOT"
        if "directional" in lower or "sun" in lower or "hemi" in lower:
            return "SUN"
        if "area" in lower:
            return "AREA"
        if "point" in lower:
            return "POINT"
        return "POINT"
    return None


def is_supported_gfx_light_actor(entry):
    return gfx_light_kind(entry) is not None


def anchor_label(entry, fallback="Anchor"):
    if not isinstance(entry, dict):
        return str(fallback)
    return str(entry.get("Name") or entry.get("Gyaml") or entry.get("__RowId") or entry.get("InstanceID") or fallback)

def add_anchor_to_index(index, hash_value, anchor):
    if isinstance(hash_value, bool):
        return
    try:
        key = int(hash_value)
    except Exception:
        return
    index.setdefault(key, anchor)

def entry_has_token(entry, token):
    needle = str(token or "").casefold()
    return bool(needle) and any(needle in str(value or "").casefold() for value in entry_tokens(entry))

def _looks_like_reference_key(key):
    text = str(key or "").lower()
    return any(token in text for token in ("hash", "id", "link", "ref", "target", "rail", "point", "to"))

def _collect_reference_values(value, path, inherited_hint=False):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = "%s.%s" % (path, key) if path else str(key)
            yield from _collect_reference_values(child, child_path, inherited_hint or _looks_like_reference_key(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _collect_reference_values(child, "%s[%d]" % (path, index), inherited_hint)
    else:
        if isinstance(value, bool):
            return
        try:
            number = int(value)
        except Exception:
            return
        if inherited_hint or number > 0xFFFFFFFF:
            yield path, number

def bancparam_reference_candidates(entry):
    if not isinstance(entry, dict):
        return []
    result=[]; seen=set()
    for param_name, param_value in entry.items():
        if not str(param_name).endswith("BancParam"):
            continue
        for value_path, ref_value in _collect_reference_values(param_value, str(param_name), False):
            key=(value_path, ref_value)
            if key not in seen:
                seen.add(key); result.append((str(param_name), value_path, ref_value))
    return result
