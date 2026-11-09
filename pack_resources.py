"""Pack/RomFS resource name normalization and byte resolution."""
from __future__ import annotations

import io
from pathlib import Path

try:
    import zstandard as _zstandard
except Exception:
    _zstandard = None


def normalize_pack_resource_name(value):
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def candidate_name_variants(text):
    raw = normalize_pack_resource_name(text)
    if not raw: return []
    variants=[]
    def add(v):
        v=normalize_pack_resource_name(v)
        if v and v not in variants: variants.append(v)
    add(raw); add(raw[5:] if raw.startswith("Work/") else "Work/" + raw)
    for v in list(variants):
        if v.endswith(".gyml"): add(v[:-5]+".bgyml"); add(v[:-5]+".byml")
        elif v.endswith(".bgyml"): add(v[:-6]+".gyml"); add(v[:-6]+".byml")
        elif v.endswith(".byml"): add(v[:-5]+".bgyml"); add(v[:-5]+".gyml")
        elif v.endswith(".json"): add(v[:-5]+".byml"); add(v[:-5]+".bgyml")
    for v in list(variants):
        if v.startswith("Banc/Scene/") and v.endswith(".bcett.json"): add("Banc/" + Path(v).name[:-5] + ".byml")
        if v.startswith("Work/Banc/Scene/") and v.endswith(".bcett.json"): add("Banc/" + Path(v).name[:-5] + ".byml")
    for v in list(variants):
        nv=normalize_pack_resource_name(v)
        if "/Bake/Source/Scene/" not in ("/"+nv) and not nv.startswith("Bake/Source/Scene/"): continue
        filename=Path(nv).name; marker=".game__gfx__bake__SceneBakeSource"
        if marker not in filename: continue
        bake_key=filename.split(marker,1)[0]
        if not bake_key: continue
        parts=bake_key.split("_"); stage_key=bake_key
        if len(parts)>=4 and parts[-1] in {"Day","Night","Sunset"}: stage_key="_".join(parts[:-2])
        for key in (bake_key, stage_key):
            if not key: continue
            for suffix in ("", ".bntx", ".bntx.zs"): add("Bake/Scene/"+key+suffix)
            for suffix in ("", ".bntx", ".bntx.zs"): add("Bake/Scene/"+stage_key+"/"+key+suffix)
    return variants


def pack_resource_candidates(resource_ref):
    return candidate_name_variants(resource_ref)


def _decompress_zstd(data, path):
    if _zstandard is None:
        raise RuntimeError("Cannot read compressed RomFS resource %s: Python module 'zstandard' is unavailable." % path)
    dctx=_zstandard.ZstdDecompressor()
    try: return dctx.decompress(data)
    except Exception as first_error:
        try:
            with dctx.stream_reader(io.BytesIO(data)) as reader:
                chunks=[]
                while True:
                    chunk=reader.read(1024*1024)
                    if not chunk: break
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as second_error:
            raise RuntimeError("Failed to decompress %s: %s; streaming fallback also failed: %s" % (path, first_error, second_error))


class PackResourceResolver:
    def __init__(self, pack_files=None, romfs_root=None, pack_path=""):
        self.pack_files=pack_files or {}; self.romfs_root=Path(romfs_root) if romfs_root else None; self.pack_path=str(pack_path or "")
        self._pack_basename=None; self._romfs_basename=None; self._decompressed_archive_cache={}
    def candidates(self, resource_ref): return candidate_name_variants(resource_ref)
    def _ensure_pack_basename_cache(self):
        if self._pack_basename is None:
            cache={}
            for name in self.pack_files: cache.setdefault(Path(name).name.casefold(), []).append(name)
            self._pack_basename=cache
        return self._pack_basename
    def _ensure_romfs_basename_cache(self):
        if self._romfs_basename is None:
            cache={}; root=self.romfs_root
            if root and root.is_dir():
                try:
                    for path in root.rglob("*"):
                        if path.is_file(): cache.setdefault(path.name.casefold(), []).append(path)
                except Exception: pass
            self._romfs_basename=cache
        return self._romfs_basename
    def _read_romfs_file(self, path):
        data=Path(path).read_bytes(); lower=str(path).casefold()
        if lower.endswith(".zs") or data.startswith(b"\x28\xb5\x2f\xfd"): data=_decompress_zstd(data,path)
        return data
    def _romfs_candidate_paths(self,candidate):
        if not (self.romfs_root and self.romfs_root.is_dir()): return []
        c=normalize_pack_resource_name(candidate); out=[]
        def add(rel):
            rel=normalize_pack_resource_name(rel)
            if rel:
                p=self.romfs_root/rel
                if p not in out: out.append(p)
        add(c); add(c[5:] if c.startswith("Work/") else "Work/"+c)
        return out
    def resolve(self, resource_ref, *, allow_basename_search=True, allow_directories=False):
        candidates=self.candidates(resource_ref)
        for cand in candidates:
            if cand in self.pack_files: return {"location":"pack","name":cand,"path":cand,"bytes":self.pack_files[cand],"candidates":candidates}
        for cand in candidates:
            for path in self._romfs_candidate_paths(cand):
                if path.is_file():
                    try: return {"location":"romfs","name":str(path.relative_to(self.romfs_root)),"path":str(path),"bytes":self._read_romfs_file(path),"candidates":candidates}
                    except Exception as exc: return {"location":"romfs_error","name":str(path),"path":str(path),"bytes":None,"error":str(exc),"candidates":candidates}
                if allow_directories and path.is_dir():
                    try: return {"location":"romfs_dir","name":str(path.relative_to(self.romfs_root)),"path":str(path),"bytes":b"","candidates":candidates}
                    except Exception as exc: return {"location":"romfs_error","name":str(path),"path":str(path),"bytes":None,"error":str(exc),"candidates":candidates}
        if allow_basename_search:
            for cand in candidates:
                matches=self._ensure_pack_basename_cache().get(Path(cand).name.casefold()) or []
                if matches:
                    name=sorted(matches)[0]; return {"location":"pack_basename","name":name,"path":name,"bytes":self.pack_files[name],"candidates":candidates}
            for cand in candidates:
                matches=self._ensure_romfs_basename_cache().get(Path(cand).name.casefold()) or []
                if matches:
                    path=sorted(matches,key=lambda p:(len(str(p)),str(p)))[0]
                    try: return {"location":"romfs_basename","name":str(path.relative_to(self.romfs_root)),"path":str(path),"bytes":self._read_romfs_file(path),"candidates":candidates}
                    except Exception as exc: return {"location":"romfs_error","name":str(path),"path":str(path),"bytes":None,"error":str(exc),"candidates":candidates}
        return {"location":"missing","name":None,"path":None,"bytes":None,"candidates":candidates}
