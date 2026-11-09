import io
import logging
import struct
import sys
from pathlib import Path

# Patched: bundled wheels are loaded privately on demand by private_wheels.py.


import bpy
try:
    import zstandard
except ImportError as exc:
    zstandard = None
    _ZSTANDARD_IMPORT_ERROR = exc

from . import yaz0
from .bfrespy.res_file import ResFile
from .bone_anim_importer import BoneAnimationImporter
from .exceptions import MalformedFileError, UnsupportedFileTypeError
from .model_importer import ModelImporter

log = logging.getLogger(__name__)

_OSI_BNTX_TEXTURE_CACHE = {}  # diagnostics only; never stores bpy.types.Image objects


def _osi_diag_report(operator, message, level="INFO"):
    try:
        if hasattr(operator, "report"):
            operator.report({level}, message)
    except Exception:
        pass


def _osi_image_is_valid(image):
    try:
        return image is not None and getattr(image, "name", None) in bpy.data.images and not bool(getattr(image, "is_missing", False))
    except ReferenceError:
        return False
    except Exception:
        return False


def _osi_valid_image_count(mapping):
    ok = 0
    bad = 0
    for image in (mapping or {}).values():
        if _osi_image_is_valid(image):
            ok += 1
        else:
            bad += 1
    return ok, bad


class Importer:
    def __init__(self, operator, filepath):
        self.operator = operator
        self.bfres: ResFile

        # Extract path information.
        self.filepath = Path(filepath)
        self.directory = self.filepath.parent
        self.filename = self.filepath.name
        self.fileext = self.filepath.suffix.upper()
        self.texture_map = {}
        # Create work directories for temporary files.

    def run(self):
        with open(self.filepath, "rb") as f:
            return self._load_stream(f)

    def run_file(self, filepath: str | Path):
        with open(filepath, "rb") as f:
            return self._load_stream(f)

    def _load_stream(self, raw) -> set:
        """Check if the stream is decompressed, and then check if it's an archive"""
        # Ensure to have a stream with decompressed data.
        magic = raw.read(4)
        raw.seek(0, io.SEEK_SET)
        match magic:
            # Uncompressed
            case b"FRES":
                return self._import_bfres(raw)
            case b"BNTX":
                return self._import_bntx(raw)
            # Archive
            case b"SARC":
                r = self._get_from_sarc(raw)
                return self._load_stream(r)
            # Compressed
            case b"\x28\xb5\x2f\xfd":  # zstd
                if zstandard is None:
                    raise ImportError("The BFRES .zs importer requires the Python zstandard module in Blender. Install zstandard into Blender Python or use an uncompressed .bfres file.") from _ZSTANDARD_IMPORT_ERROR
                dctx = zstandard.ZstdDecompressor()
                r = dctx.decompress(raw.read())
                return self._load_stream(io.BytesIO(r))
            case b"Yaz0":
                r = yaz0.decompress(raw)
                return self._load_stream(r)
            case _:
                raise UnsupportedFileTypeError(magic)

    def _import_bfres(self, stream):
        """Import a BFRES file, and return 'FINISHED' if it succeeds"""
        bfres = ResFile(stream)
        self.bfres = bfres

        # Read and import embedded/external textures unless texture import is disabled.
        # This is the fastest mode for stage layout work and avoids decoder cost.
        if self.operator.import_tex_mode != "OFF":
            if getattr(self.operator, "report_texture_details", False) and hasattr(self.operator, "report"):
                try:
                    self.operator.report({"INFO"}, "BFRES_TEXTURE_SOURCES_DETAIL owner=%r file=%r import_tex_mode=%r external_files=%s models=%s" % (getattr(self.operator, "texture_diag_owner", "UNKNOWN"), self.filename, self.operator.import_tex_mode, [str(getattr(n, "key", "")) for n in bfres.external_files], [str(getattr(m, "name", "")) for m in bfres.models.values()]))
                except Exception:
                    pass
            for node in bfres.external_files:
                self._import_embed(node, bfres.name)

        if self.operator.import_tex_mode == "TEX_FILE":
            texpath = Path(self.filepath).stem
            ext = Path(self.filepath).suffix
            texpath = texpath + ".Tex" + ext
            if Path(texpath).is_file():
                log.info("Importing linked file: %s", texpath)
                self.run_file(texpath)

        if self.operator.import_tex_mode == "TEX_FOLDER":
            texfolder = Path(self.filepath).parents[1].joinpath("Tex")
            ext = Path(self.filepath).suffix
            for model in bfres.models.values():
                for fmat in model.materials.values():
                    for tex in fmat.texture_refs:
                        texpath = texfolder.joinpath(tex.name).with_suffix(".bntx" + ext)
                        log.debug("Looking for: %s", texpath)
                        if texpath.is_file():
                            log.info("Importing file from Tex folder: %s", texpath)
                            self.run_file(texpath)

        if self.operator.import_anims:
            anim_imp = BoneAnimationImporter(self)
            anim_imp._import_animations(bfres)

        target_names = [str(name) for name in getattr(self.operator, "target_model_names", []) or [] if str(name)]
        selected_models = list(bfres.models.values())
        if target_names:
            target_set = {name.casefold() for name in target_names}
            selected_models = [fmdl for fmdl in bfres.models.values() if str(getattr(fmdl, "name", "")).casefold() in target_set]
            if not selected_models:
                # Some assets use decorated FMDL names.  As a fallback, allow an
                # exact target to match the start/end of the model name, but do
                # not import every model from the BFRES when there is no match.
                selected_models = [
                    fmdl for fmdl in bfres.models.values()
                    if any(
                        str(getattr(fmdl, "name", "")).casefold().startswith(t + "_")
                        or str(getattr(fmdl, "name", "")).casefold().endswith("_" + t)
                        for t in target_set
                    )
                ]
            if not selected_models:
                available = ", ".join(str(getattr(fmdl, "name", "")) for fmdl in bfres.models.values())
                message = "No matching FMDL in %s. Wanted %s; available: %s" % (self.filename, target_names, available)
                log.warning(message)
                if hasattr(self.operator, "report"):
                    self.operator.report({"WARNING"}, message)
                return {"FINISHED"}

        # If there's more than one selected model, add them to a collection.
        if len(selected_models) > 1:
            collection = bpy.data.collections.new(name=bfres.name)
            bpy.context.scene.collection.children.link(collection)
        else:
            collection = bpy.context.scene.collection

        model_imp = ModelImporter(self)
        for fmdl in selected_models:
            before_objs = set(bpy.context.scene.objects)
            try:
                model_imp.convert_fmdl(fmdl, collection)
            except Exception as exc:
                msg = "Skipped FMDL '%s' in '%s' because model import failed: %s" % (
                    getattr(fmdl, "name", "<unnamed>"), self.filename, exc
                )
                log.warning(msg, exc_info=True)
                if hasattr(self.operator, "report"):
                    self.operator.report({"WARNING"}, msg)
                continue
            after_objs = set(bpy.context.scene.objects)
            made = len(after_objs - before_objs)
            if hasattr(self.operator, "report"):
                self.operator.report({"INFO"}, "Imported FMDL '%s' from '%s' (%d Blender object(s))." % (getattr(fmdl, "name", "<unnamed>"), self.filename, made))

        return {"FINISHED"}

    def _import_bntx(self, stream):
        """Import a bntx file, and return 'FINISHED' if it succeeds"""
        if self.operator.import_tex_mode == "OFF":
            if hasattr(self.operator, "report"):
                self.operator.report({"INFO"}, "Skipped BNTX textures because texture import mode is Off.")
            return {"FINISHED"}

        from . import bntx
        from .texture_importer import import_textures
        import hashlib
        import io as _io

        try:
            data = stream.read()
            key = hashlib.sha1(data).hexdigest()
            owner = getattr(self.operator, "texture_diag_owner", "UNKNOWN")
            # v0.8.36: allow the lower texture importer to reuse live Image datablocks
            # by decoded texture identity.  This avoids stage imports creating dozens
            # of packed duplicates such as Obj_Bunker01_M_Body_Thc.018 while still
            # clearing caches at the start of each import session via lifecycle.py.
            _osi_diag_report(self.operator, "TEXTURE_CACHE_DEDUPE_ENABLED owner=%r key=%s" % (owner, key[:12]))
            bntx_ = bntx.BNTX(_io.BytesIO(data))
            textures = import_textures(bntx_, self.operator)
            if getattr(self.operator, "report_texture_details", False):
                _osi_diag_report(self.operator, "BNTX_TEXTURE_MAP_DETAIL owner=%r key=%s imported_keys=%s" % (owner, key[:12], [str(k) for k in list(textures.keys())[:100]]))
            self.texture_map.update(textures)
            _osi_diag_report(self.operator, "TEXTURE_CACHE_DEDUPE_STORE_CURRENT owner=%r key=%s images=%d" % (owner, key[:12], len(textures)))
        except Exception as exc:
            log.warning("Skipping BNTX textures because texture decoding failed: %s", exc, exc_info=True)
            if hasattr(self.operator, "report"):
                self.operator.report({"WARNING"}, f"Skipped BFRES/BNTX textures: {exc}")

        return {"FINISHED"}

    def _import_embed(self, node, name):
        """Import an embedded file in the ResFile"""
        name = node.key
        file = node.value
        if name.endswith(".txt"):
            # embed text blend file
            obj = bpy.data.texts.new(name=name)
            obj.write(file.data.decode("utf-8"))
        elif file.data:
            try:
                self._load_stream(io.BytesIO(file.data))
            except UnsupportedFileTypeError as ex:
                log.debug("Embedded file '%s' is of unsupported type '%s'", name, ex.magic)
        else:
            log.debug("Embedded file '%s' is empty", name)

    @staticmethod
    def _get_from_sarc(raw: io.BytesIO | io.BufferedReader):
        """Attempt to return a FRES file from a SARC archive."""
        log.debug("Extracting from SARC")
        raw.seek(6, io.SEEK_CUR)
        bom = raw.read(2)
        endianness = ">" if bom == b"\xFE\xFF" else "<"
        raw.seek(4, io.SEEK_CUR)
        offs = struct.unpack(endianness + "I", raw.read(4))[0]
        raw.seek(10, io.SEEK_CUR)
        num_nodes = struct.unpack(endianness + "H", raw.read(2))[0]
        raw.seek(4, io.SEEK_CUR)
        files = []
        for _ in range(num_nodes):
            raw.seek(8, io.SEEK_CUR)
            start_offs = struct.unpack(endianness + "I", raw.read(4))[0]
            end_offs = struct.unpack(endianness + "I", raw.read(4))[0]
            files.append((start_offs, end_offs))
        for fileoff in files:
            raw.seek(fileoff[0] + offs, io.SEEK_SET)
            if raw.read(4) == b"FRES":
                raw.seek(-4, io.SEEK_CUR)
                return io.BytesIO(raw.read(fileoff[1] - fileoff[0]))
        raise MalformedFileError("Embedded SARC file does not contain FRES")

    @staticmethod
    def _add_object_to_collecton(obj, collection_name):
        """Add an object to a collection, and create it if it does not already exist."""
        group = bpy.data.collections.get(collection_name, bpy.data.collections.new(name=collection_name))

        # Link the provided object to it.
        if obj.name not in group.objects:
            group.objects.link(obj)
