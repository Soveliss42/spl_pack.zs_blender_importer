"""Pure import-report filtering and summary helpers."""
from __future__ import annotations


def compact_import_report_lines(report_lines):
    kept = []
    counters = {"imported":0,"cache_hit":0,"cache_store":0,"bfres_info":0,"banc_normal":0,"ground_skip":0,"option_skip":0,"skipped_helper":0,"rotation_debug":0}
    keep_prefixes = ("MISSING BFRES", "FAILED BFRES", "NO OBJECTS")
    for line in report_lines:
        if line.startswith("IMPORTED "): counters["imported"] += 1; continue
        if line.startswith("CACHE_HIT "): counters["cache_hit"] += 1; continue
        if line.startswith("CACHE_STORE "): counters["cache_store"] += 1; continue
        if line.startswith("BFRES "):
            if "[WARNING]" in line or "[ERROR]" in line: kept.append(line)
            else: counters["bfres_info"] += 1
            continue
        if line.startswith("BANC_ANCHOR normal "): counters["banc_normal"] += 1; continue
        if line.startswith("SKIP option "): counters["option_skip"] += 1; kept.append(line); continue
        if line.startswith("SKIPPED_HELPER "): counters["skipped_helper"] += 1; kept.append(line); continue
        if line.startswith(("ROT_DEBUG ", "ROOT_DEBUG ")): counters["rotation_debug"] += 1; continue
        if line.startswith(keep_prefixes): kept.append(line); continue
        kept.append(line)
    summary = [
        "Compact report mode: routine successful actor lines suppressed.",
        "Suppressed detail counts: imported=%d cache_hits=%d cache_stores=%d bfres_info=%d bancparam_normal=%d option_skips=%d skipped_helpers=%d rotation_debug=%d" % (
            counters["imported"], counters["cache_hit"], counters["cache_store"], counters["bfres_info"], counters["banc_normal"], counters["option_skip"], counters["skipped_helper"], counters["rotation_debug"]),
    ]
    return summary + ([""] + kept if kept else ["", "No warnings, failures, unresolved anchors, or special corrections were recorded."])


def minimal_import_report_lines(report_lines):
    kept=[]; suppressed=0
    important=("MISSING BFRES","FAILED BFRES","NO OBJECTS","SKIP option","SKIPPED_HELPER","NON_GEOMETRY_")
    for line in report_lines:
        if line.startswith(important) or "[WARNING]" in line or "[ERROR]" in line: kept.append(line)
        else: suppressed += 1
    summary=["Minimal report mode: only warnings, failures, no-object cases, missing BFRES entries, explicit option skips, and skipped helpers are shown.", "Suppressed detail lines: %d" % suppressed]
    return summary + ([""] + kept if kept else ["", "No warnings, failures, missing resources, or explicit option skips were recorded."])


def is_texture_debug_report_line(line):
    text=str(line)
    prefixes=("BNTX_TEXTURE_DETAIL","BNTX_TEXTURE_PIXELS_DETAIL","BFRES_TEXTURE_SOURCES_DETAIL","TEXTURE_CACHE_HIT","TEXTURE_CACHE_STORE","TEXTURE_CACHE_INVALID","TEXTURE_CACHE_HARD_BYPASS","IMAGE_CREATE","IMAGE_NAME_COLLISION","CACHE_STORE_TEXTURE","CACHE_HIT_TEXTURE")
    return text.startswith(prefixes) or any(x in text for x in ("BNTX_TEXTURE_DETAIL","BNTX_TEXTURE_PIXELS_DETAIL","BFRES_TEXTURE_SOURCES_DETAIL"," IMAGE_CREATE"," IMAGE_NAME_COLLISION","TEXTURE_CACHE_","CACHE_STORE texture"))


def full_import_report_lines(report_lines, include_texture_debug=False):
    if include_texture_debug:
        return ["Debug Texture report mode: full report plus per-texture BFRES/BNTX diagnostics are shown.", ""] + list(report_lines)
    kept=[]; suppressed=0
    for line in report_lines:
        if is_texture_debug_report_line(line): suppressed += 1
        else: kept.append(line)
    return ["Full report mode: object-level import decisions, warnings, failures, and summaries are shown.", "Per-texture BFRES/BNTX diagnostics are suppressed here; choose Import Report = Debug Texture to include them.", "Suppressed texture debug lines: %d" % suppressed, ""] + kept


def select_import_report_lines(report_lines, verbosity="COMPACT"):
    verbosity=str(verbosity or "COMPACT").upper()
    if verbosity == "DEBUG_TEXTURE": return full_import_report_lines(report_lines, True)
    if verbosity == "FULL": return full_import_report_lines(report_lines, False)
    if verbosity == "MINIMAL": return minimal_import_report_lines(report_lines)
    return compact_import_report_lines(report_lines)
