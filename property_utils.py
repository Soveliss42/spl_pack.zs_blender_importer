from __future__ import annotations


def idprop_safe(value, max_text=6000):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if value < -0x7FFFFFFF - 1 or value > 0x7FFFFFFF:
            return str(value)
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (list, tuple)):
        if len(value) <= 64:
            safe_items = []
            for v in value:
                sv = idprop_safe(v, max_text=max_text)
                if not (isinstance(sv, (str, int, float, bool)) or sv is None):
                    break
                safe_items.append("" if sv is None else sv)
            else:
                return safe_items
    text = repr(value)
    if len(text) > max_text:
        text = text[:max_text] + "...<truncated>"
    return text


def set_props_from_mapping(obj, prefix, mapping):
    if not isinstance(mapping, dict):
        return
    for key, value in mapping.items():
        try:
            obj["%s_%s" % (prefix, key)] = idprop_safe(value)
        except Exception:
            try:
                obj["%s_%s" % (prefix, key)] = repr(value)
            except Exception:
                pass
