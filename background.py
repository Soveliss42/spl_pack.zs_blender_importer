"""Background submodel import helpers.

First-pass support for Fld_[stage] ActorParam ModelInfo entries.  The game stores
additional field/background model references in the field actor pack; this module
imports those submodels into a dedicated Background collection when requested.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Set
import io

import bpy

from . import byml, sarc
from ._bfres_vendor.bfrespy.res_file import ResFile

try:
    import zstandard as _zstd
except Exception:
    _zstd = None


def _safe_name(value: Any, fallback: str = "Background") -> str:
    text = str(value or fallback).strip() or fallback
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text[:63] or fallback


def _romfs_root_from_prefs(prefs) -> Path | None:
    value = getattr(prefs, "romfs_root_path", "") if prefs is not None else ""
    if not value:
        return None
    try:
        path = Path(bpy.path.abspath(value))
    except Exception:
        path = Path(value)
    return path if path.exists() else None


def _decompress_if_zs(data: bytes) -> bytes:
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        if _zstd is None:
            raise RuntimeError("zstandard is unavailable for ActorParam .zs files")
        try:
            return _zstd.ZstdDecompressor().decompress(data)
        except Exception:
            import io
            with _zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)) as r:
                chunks=[]
                while True:
                    c=r.read(1024*1024)
                    if not c: break
                    chunks.append(c)
                return b"".join(chunks)
    return data


def _iter_sarc_payloads(path: Path):
    data = _decompress_if_zs(path.read_bytes())
    for name, payload in sarc.iter_sarc_files(data):
        yield name.replace('\\','/'), payload


def _find_field_actor_name(objects: Iterable[dict]) -> str | None:
    for entry in objects or []:
        if not isinstance(entry, dict):
            continue
        for key in ("Gyaml", "Name", "name"):
            v = entry.get(key)
            if isinstance(v, str) and v.startswith("Fld_"):
                return v
    return None


def _parse_byml_payload(payload: bytes) -> Any:
    payload = _decompress_if_zs(payload)
    if payload[:2] not in (b"BY", b"YB"):
        return None
    return byml.Byml(payload).parse()


def _collect_strings(value: Any, out: Set[str], depth: int = 0):
    if depth > 12:
        return
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            _collect_strings(v, out, depth+1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect_strings(v, out, depth+1)


def _modelinfo_ref_from_actorparam(root: Any) -> str | None:
    try:
        comps = root.get("Components") if isinstance(root, dict) else None
        if isinstance(comps, dict):
            for key in ("ModelInfoRef", "ModelInfo", "Model"):
                value = comps.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        pass
    return None


def _component_rel_from_work_ref(ref: str, component_kind: str = "ModelInfo") -> str | None:
    if not ref:
        return None
    name = Path(str(ref).replace('\\','/')).name
    for suf in (".gyml", ".bgyml", ".byml"):
        if name.casefold().endswith(suf):
            name = name[:-len(suf)]
            break
    if not name:
        return None
    return "Component/%s/%s.bgyml" % (component_kind, name)


def _model_name_from_ref(text: Any) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None
    # Fmdb paths look like Work/Model/.../FldBG_Name/output/FldBG_Name.fmdb.
    base = Path(value.replace('\\','/')).name
    for suf in (".bfres.zs", ".bfres", ".bgyml", ".gyml", ".fmdb"):
        if base.casefold().endswith(suf):
            base = base[:-len(suf)]
            break
    if base.casefold() == "output":
        parts = value.replace('\\','/').split('/')
        if len(parts) >= 2:
            base = parts[-2]
    return base or None


def _submodel_names_from_modelinfo(root: Any) -> List[str]:
    """Return only explicit ModelInfo.SubModels entries.

    Splatoon 3 field actor packs store background FMDLs such as FldBG_Temple01
    as SubModels inside the main Fld_Temple01 model container.  The top-level
    ModelInfo.Fmdb points at the normal field model and must not be treated as
    a background candidate.
    """
    names: List[str] = []
    if not isinstance(root, dict):
        return names
    submodels = root.get("SubModels")
    if not isinstance(submodels, (list, tuple)):
        return names
    for item in submodels:
        candidate = None
        if isinstance(item, dict):
            for key in ("Fmdb", "Model", "ModelName", "FmdlName", "Name"):
                if key in item:
                    candidate = _model_name_from_ref(item.get(key))
                    if candidate:
                        break
        else:
            candidate = _model_name_from_ref(item)
        if candidate and candidate not in names:
            names.append(candidate)
    return sorted(names, key=lambda x:x.casefold())


def _submodel_names_from_actorparam(root: Any) -> List[str]:
    # Keep this intentionally permissive: ModelInfo schemas vary, and the names
    # may be nested as FmdlName/ModelName/FileName strings.
    strings: Set[str] = set()
    def walk(value: Any, in_modelinfo: bool = False, depth: int = 0):
        if depth > 12:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                now = in_modelinfo or ("modelinfo" in str(k).casefold())
                if now:
                    _collect_strings(v, strings, depth+1)
                walk(v, now, depth+1)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, in_modelinfo, depth+1)
    walk(root)
    candidates=[]
    for s in strings:
        text=str(s).strip()
        if not text:
            continue
        low=text.casefold()
        if any(skip in low for skip in (".slink", ".elink", "sound", "effect")):
            continue
        # Accept model-ish names, but skip the root Fld_* itself if it is the only token.
        name=Path(text.replace('\\','/')).name
        for suf in (".bfres.zs", ".bfres", ".bgyml", ".gyml"):
            if name.casefold().endswith(suf):
                name=name[:-len(suf)]
        if name and name not in candidates:
            candidates.append(name)
    return sorted(candidates, key=lambda x:x.casefold())


def _find_model_path(models_dir: Path, name: str) -> Path | None:
    base=Path(name.replace('\\','/')).name
    for candidate in (models_dir / (base + ".bfres.zs"), models_dir / (base + ".bfres")):
        if candidate.is_file():
            return candidate
    return None


def _bfres_model_names(path: Path) -> List[str]:
    """Read the FMDL names contained in a BFRES/BFRES.ZS without importing it.

    Background ModelInfo.SubModels name FMDLs, not necessarily BFRES files.  A
    single background BFRES can therefore satisfy several SubModels entries.
    """
    try:
        data = _decompress_if_zs(path.read_bytes())
        if data[:4] != b"FRES":
            return []
        res = ResFile(io.BytesIO(data))
        names = []
        try:
            values = res.models.values()
        except Exception:
            values = []
        for model in values:
            name = str(getattr(model, "name", "") or "").strip()
            if name and name not in names:
                names.append(name)
        return names
    except Exception:
        return []


def _background_container_map(models_dir: Path, field: str, names: List[str], report_lines) -> tuple[dict[str, Path], list[tuple[str, list[str]]]]:
    """Resolve SubModels by FMDL membership rather than BFRES filename.

    Candidate containers are the main field BFRES plus any exact BFRES whose
    filename matches a SubModels target.  Every candidate is inspected once; a
    target is then assigned to the container that actually contains that FMDL.
    Exact-name containers win ties, followed by non-field background containers,
    then the main field container.
    """
    candidates: List[Path] = []
    field_path = _find_model_path(models_dir, field)
    if field_path is not None:
        candidates.append(field_path)
    exact_by_target: dict[str, Path] = {}
    for name in names:
        path = _find_model_path(models_dir, name)
        if path is not None:
            exact_by_target[name.casefold()] = path
            if path not in candidates:
                candidates.append(path)

    inventory: list[tuple[str, list[str]]] = []
    contained_by_path: dict[Path, set[str]] = {}
    for path in candidates:
        model_names = _bfres_model_names(path)
        inventory.append((path.name, model_names))
        contained_by_path[path] = {n.casefold() for n in model_names}
        report_lines.append("BACKGROUND_CONTAINER_INVENTORY field=%r container=%s fmdls=%s" % (field, path.name, model_names))

    resolved: dict[str, Path] = {}
    for name in names:
        key = name.casefold()
        exact = exact_by_target.get(key)
        if exact is not None and key in contained_by_path.get(exact, set()):
            resolved[key] = exact
            continue
        # Prefer a discovered non-field container that actually contains the FMDL.
        for path in candidates:
            if field_path is not None and path == field_path:
                continue
            if key in contained_by_path.get(path, set()):
                resolved[key] = path
                break
        if key in resolved:
            continue
        if field_path is not None and key in contained_by_path.get(field_path, set()):
            resolved[key] = field_path

    return resolved, inventory


def import_background_from_field_actorparam(root, objects, stage_collection, prefs, models_dir, import_cache, report_lines,
                                            get_or_create_child_collection, move_objects_to_collection,
                                            import_bfres_cached, set_bcett_transform_props_many) -> int:
    field = _find_field_actor_name(objects)
    if not field:
        report_lines.append("BACKGROUND_SKIPPED reason=no_field_actor")
        return 0
    romfs = _romfs_root_from_prefs(prefs)
    if not romfs:
        report_lines.append("BACKGROUND_SKIPPED reason=no_romfs_root")
        return 0
    pack = romfs / "Pack" / "Actor" / (field + ".pack.zs")
    if not pack.is_file():
        report_lines.append("BACKGROUND_SKIPPED field=%r reason=actor_pack_not_found path=%s" % (field, pack))
        return 0
    actor_root = None
    modelinfo_root = None
    slinks=[]; elinks=[]
    payloads = {}
    for rel, payload in _iter_sarc_payloads(pack):
        rel_norm = rel.replace('\\','/')
        low=rel_norm.casefold()
        payloads[low] = payload
        if low.endswith('.slink') or '.slink.' in low: slinks.append(rel_norm)
        if low.endswith('.elink') or '.elink.' in low: elinks.append(rel_norm)
        if low.endswith('engine__actor__actorparam.bgyml') or low.endswith('engine__actor__actorparam.gyml') or low.endswith('engine__actor__actorparam.byml'):
            actor_root = _parse_byml_payload(payload)
    if actor_root is None:
        report_lines.append("BACKGROUND_SKIPPED field=%r reason=no_actorparam_in_pack" % field)
        return 0
    model_ref = _modelinfo_ref_from_actorparam(actor_root)
    if model_ref:
        rel = _component_rel_from_work_ref(model_ref, "ModelInfo")
        payload = payloads.get(str(rel or "").casefold())
        if payload is not None:
            modelinfo_root = _parse_byml_payload(payload)
            report_lines.append("BACKGROUND_MODELINFO_REF field=%r ref=%r rel=%r" % (field, model_ref, rel))
        else:
            report_lines.append("BACKGROUND_MODELINFO_REF_MISSING field=%r ref=%r rel=%r" % (field, model_ref, rel))
    names = _submodel_names_from_modelinfo(modelinfo_root) if modelinfo_root is not None else []
    if not names:
        report_lines.append("BACKGROUND_SKIPPED field=%r reason=no_modelinfo_submodels" % field)
        return 0

    # ModelInfo.SubModels identify FMDLs, not BFRES filenames.  Build an
    # inventory of plausible background containers first, then resolve each
    # SubModels target by actual FMDL membership.  This handles layouts such as
    # FldBG_Cross00.bfres.zs containing both FldBG_Cross00 and
    # Fld_Cross00SideWall.
    field_container_path = _find_model_path(models_dir, field)
    if field_container_path is None:
        report_lines.append("BACKGROUND_FIELD_CONTAINER_MISSING field=%r model=%r note=background_container_discovery_still_allowed" % (field, field))
    else:
        report_lines.append("BACKGROUND_FIELD_CONTAINER field=%r path=%s submodels=%s" % (field, field_container_path.name, names))

    container_map, container_inventory = _background_container_map(models_dir, field, names, report_lines)

    bg_col = get_or_create_child_collection(stage_collection, "Background")
    count=0
    resolved_containers=[]
    for name in names:
        # Do not duplicate the main field root, even if malformed data lists it.
        if name.casefold() == field.casefold():
            report_lines.append("BACKGROUND_SUBMODEL_SKIPPED field=%r model=%r reason=matches_field_root" % (field, name))
            continue

        path = container_map.get(name.casefold())
        if path is None:
            report_lines.append("BACKGROUND_SUBMODEL_SKIPPED field=%r model=%r reason=fmdl_not_found_in_discovered_containers inventory=%s" % (field, name, container_inventory))
            continue
        exact_path = _find_model_path(models_dir, name)
        if exact_path is not None and path == exact_path:
            resolution = "exact_target_bfres_contains_fmdl"
        elif field_container_path is not None and path == field_container_path:
            resolution = "field_container_contains_fmdl"
        else:
            resolution = "shared_background_container_contains_fmdl"

        try:
            resolved_containers.append((name, path.name, resolution))
            report_lines.append("BACKGROUND_CONTAINER_RESOLVED field=%r target=%r container=%s reason=%s" % (field, name, path.name, resolution))
            objs,msgs,_cache=import_bfres_cached(str(path), prefs, import_cache, report_lines, "Background:"+name, name_prefix=name+"_", target_model_names=[name], texture_diag_owner="BACKGROUND")
            for msg in msgs:
                report_lines.append("BFRES_BACKGROUND model=%r path=%s %s" % (name, path.name, msg))
            if objs:
                move_objects_to_collection(objs, bg_col)
                for obj in objs:
                    try:
                        obj["spl_background_submodel"] = name
                        obj["spl_background_field_actor"] = field
                        obj["spl_actor_slink_refs"] = slinks[:32]
                        obj["spl_actor_elink_refs"] = elinks[:32]
                        obj["osi_background_submodel"] = name
                        obj["osi_background_field_actor"] = field
                        obj["osi_actor_slink_refs"] = slinks[:32]
                        obj["osi_actor_elink_refs"] = elinks[:32]
                    except Exception:
                        pass
                count += len(objs)
                report_lines.append("BACKGROUND_IMPORTED field=%r model=%r objects=%d path=%s" % (field, name, len(objs), path.name))
        except Exception as exc:
            report_lines.append("BACKGROUND_FAILED field=%r model=%r error=%s" % (field, name, exc))
    report_lines.append("BACKGROUND_SUMMARY field=%r containers=%s candidates=%d imported_objects=%d" % (field, resolved_containers, len(names), count))
    return count
