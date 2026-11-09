from pathlib import Path
import io
import traceback

import bpy
import bpy_extras

from . import addon, sarc, byml

try:
    import zstandard as _osi_zstandard
except Exception:
    _osi_zstandard = None



def _decompress_pack_zstd(data: bytes, archive_path: Path) -> bytes:
    """Decompress a .pack.zs Zstandard payload to SARC bytes."""
    if _osi_zstandard is None:
        raise RuntimeError(
            "Cannot read selected .pack.zs because Python module 'zstandard' is not available in Blender. "
            "Install the private-wheels add-on build with zstandard support, or provide a decompressed SARC for testing."
        )
    dctx = _osi_zstandard.ZstdDecompressor()
    try:
        return dctx.decompress(data)
    except Exception as first_error:
        try:
            with dctx.stream_reader(io.BytesIO(data)) as reader:
                chunks = []
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as second_error:
            raise RuntimeError(
                "Could not decompress selected .pack.zs as Zstandard data: %s. "
                "Make sure this is an unmodified Splatoon 3 RomFS/Pack/Scene file. "
                "Streaming fallback also failed: %s" % (first_error, second_error)
            )


def _read_stage_pack_files(pack_path: str) -> dict[str, bytes]:
    """Return normalized SARC entries from a .pack.zs stage pack."""
    archive = Path(pack_path)
    if archive.is_dir():
        raise RuntimeError(
            "Selected path is a folder, not a stage pack. Please select a Splatoon 3 stage .pack.zs file "
            "from the RomFS/Pack/Scene folder, such as Vss_Hiagari04.pack.zs."
        )
    if not archive.exists():
        raise FileNotFoundError(
            "The selected stage pack could not be found: %s" % archive
        )
    if archive.suffix.casefold() != ".zs" or not archive.name.casefold().endswith(".pack.zs"):
        raise RuntimeError(
            "Selected file does not look like a Splatoon 3 stage .pack.zs file: %s. "
            "Please choose a file from RomFS/Pack/Scene, such as Vss_Hiagari04.pack.zs." % archive.name
        )
    data = archive.read_bytes()
    # Stage packs are normally Zstandard-compressed SARC files, but accept raw
    # SARC too so tests can use decompressed fixtures.
    if data[:4] != b"SARC":
        data = _decompress_pack_zstd(data, archive)
    try:
        files = list(sarc.iter_sarc_files(data))
    except Exception as exc:
        raise RuntimeError(
            "Selected file was decompressed, but the result was not a readable SARC archive: %s. "
            "Expected a Splatoon 3 RomFS/Pack/Scene/*.pack.zs stage pack." % exc
        )
    return {name.replace("\\", "/"): payload for name, payload in files}



def _normalize_pack_ref_name(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _stage_pack_stem(pack_path: str) -> str:
    name = Path(pack_path).name
    for suffix in (".pack.zs", ".pack"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return Path(name).stem


def _find_scene_param_in_stage_pack_files(pack_path: str, files: dict[str, bytes]) -> tuple[str | None, dict | None]:
    stem = _stage_pack_stem(pack_path)
    preferred_names = [
        f"Scene/{stem}.engine__scene__SceneParam.bgyml",
        f"Scene/{stem}.engine__scene_SceneParam.bgyml",
    ]
    candidates = []
    for preferred in preferred_names:
        if preferred in files:
            candidates.append(preferred)
    for name in sorted(files):
        low = name.casefold()
        if low.startswith("scene/") and (low.endswith(".engine__scene__sceneparam.bgyml") or low.endswith(".engine__scene_sceneparam.bgyml")) and name not in candidates:
            candidates.append(name)
    for name in candidates:
        try:
            data = byml.Byml(files[name]).parse()
        except Exception:
            continue
        if isinstance(data, dict):
            return name, data
    return None, None


def _scene_param_startup_bcett_candidate(pack_path: str, files: dict[str, bytes]) -> tuple[str | None, bytes | None]:
    """Resolve StartupMap -> Banc/<stage>.bcett.byml when SceneParam provides it.

    SceneParam components usually use Work/Banc/Scene/Foo.bcett.json, while the
    SARC entry is Banc/Foo.bcett.byml. Prefer that explicit name over simply
    taking the first .bcett.byml in the pack.
    """
    scene_name, scene_param = _find_scene_param_in_stage_pack_files(pack_path, files)
    components = scene_param.get("Components", {}) if isinstance(scene_param, dict) else {}
    ref = components.get("StartupMap") if isinstance(components, dict) else None
    if not ref:
        return None, None
    ref_name = _normalize_pack_ref_name(ref)
    stem = Path(ref_name).name
    for suffix in (".json", ".byml", ".bgyml", ".gyml"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    candidates = [
        f"Banc/{stem}.byml",
        f"Banc/{stem}.bcett.byml" if not stem.endswith(".bcett") else f"Banc/{stem}.byml",
        f"Banc/Scene/{stem}.byml",
    ]
    seen = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        payload = files.get(name)
        if payload is not None:
            return name, payload
    # Case-insensitive fallback.
    wanted = {c.casefold() for c in candidates}
    for name, payload in files.items():
        if name.casefold() in wanted:
            return name, payload
    return None, None

def _find_bcett_byml_in_stage_pack_files(pack_path: str, files: dict[str, bytes]) -> tuple[str, bytes]:
    """Return (internal_name, bytes) for the stage BCETT inside a parsed stage pack."""
    scene_bcett_name, scene_bcett_bytes = _scene_param_startup_bcett_candidate(pack_path, files)
    if scene_bcett_bytes is not None:
        return scene_bcett_name, scene_bcett_bytes
    archive = Path(pack_path)
    preferred = []
    fallback = []
    for name, payload in files.items():
        lower = name.casefold()
        if lower.startswith("banc/") and lower.endswith(".bcett.byml"):
            preferred.append((name, payload))
        elif lower.endswith(".bcett.byml"):
            fallback.append((name, payload))
    matches = preferred or fallback
    if not matches:
        examples = ", ".join(list(files.keys())[:8])
        raise FileNotFoundError(
            "Selected pack.zs was readable, but no Banc/*.bcett.byml file was found. "
            "Expected a stage pack from RomFS/Pack/Scene, such as Vss_Line03.pack.zs. "
            "Archive entries seen: %s" % (examples or "none")
        )
    if len(matches) > 1:
        # Deterministic and stage-like first; a future UI can expose a selector.
        matches.sort(key=lambda item: (0 if Path(item[0]).stem == archive.stem else 1, item[0]))
    return matches[0]


def _find_bcett_byml_in_stage_pack(pack_path: str) -> tuple[str, bytes]:
    files = _read_stage_pack_files(pack_path)
    return _find_bcett_byml_in_stage_pack_files(pack_path, files)

class ImportOperator(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Load a Splatoon 3 stage"""

    bl_idname = "import_scene.osi"
    bl_label = "Import BCETT.BYML Stage"
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".byml"
    filter_glob: bpy.props.StringProperty(default="*.byml;*.bcett.byml", options={"HIDDEN"})
    filepath: bpy.props.StringProperty(
        name="File Path",
        description="Filepath used for importing the stage BCETT.BYML file.",
        maxlen=1024,
        default="",
    )
    scenarioValue: bpy.props.IntProperty(name="Scenario", description="Scenario to import.", min=0, max=14)
    import_mode: bpy.props.EnumProperty(
        name="Import Mode",
        description="Choose material/texture import behavior.",
        items=(
            ("FULL", "Full", "Import materials and decode textures."),
            ("MATERIALS", "Materials Only", "Import materials using gray/no-texture fallback; skip texture decoding."),
            ("NONE", "No Materials/Textures", "Skip material and texture setup for fastest imports."),
        ),
        default="FULL",
    )
    skip_non_renderable_actors: bpy.props.BoolProperty(
        name="Skip Non-renderable Actors",
        description="Skip helper/control actors that normally do not have BFRES models, such as ChangePaintableArea, locators, route areas, and spawn/death markers. Gfx light actors are controlled by Import Non-Geometry Objects.",
        default=True,
    )
    import_geometry: bpy.props.BoolProperty(
        name="Import Geometry",
        description="Import BFRES mesh geometry from BCETT actors and pack scene-component geometry.",
        default=True,
    )
    import_non_geometry_objects: bpy.props.BoolProperty(
        name="Import Non-Geometry Objects",
        description="Create Blender-native objects for supported non-mesh BCETT actors: Gfx lights, SoundShape speakers/volumes, camera/photo locators, generic locators, effect locators, and gameplay area visualizers.",
        default=True,
    )
    import_graffiti: bpy.props.BoolProperty(
        name="Import Graffiti",
        description="Import pack graffiti decal planes from Graffiti/*.byml and SceneGraffitiPlacementData.",
        default=True,
    )
    import_background: bpy.props.BoolProperty(
        name="Import Background",
        description="Import Fld_[stage] ActorParam ModelInfo submodels into a Background collection.",
        default=False,
    )
    import_pack_extra_geometry: bpy.props.BoolProperty(
        name="Import Pack Extra Geometry",
        description="Import optional geometry discovered from pack scene-component passes, such as water candidates. Off keeps these extras read-only/metadata-only.",
        default=False,
    )
    report_verbosity: bpy.props.EnumProperty(
        name="Import Report",
        description="Choose how much detail to write to the import report file.",
        items=(
            ("FULL", "Full", "Write object-level import decisions, warnings, failures, and summaries; suppress per-texture BFRES/BNTX diagnostics."),
            ("DEBUG_TEXTURE", "Debug Texture", "Write Full report plus verbose per-texture BFRES/BNTX/image/cache diagnostics."),
            ("COMPACT", "Compact", "Suppress routine successful import/cache lines but keep warnings and special corrections."),
            ("MINIMAL", "Minimal", "Write summary plus warnings, failures, and explicit skips only."),
        ),
        default="COMPACT",
    )


    _remembered_option_names = (
        "scenarioValue",
        "import_mode",
        "skip_non_renderable_actors",
        "import_geometry",
        "import_non_geometry_objects",
        "import_graffiti",
        "import_background",
        "import_pack_extra_geometry",
        "report_verbosity",
    )

    def invoke(self, context, event):
        """Restore Blender's last-used import options before opening the file browser."""
        try:
            last_props = context.window_manager.operator_properties_last(self.bl_idname)
        except Exception:
            last_props = None
        if last_props:
            for name in self._remembered_option_names:
                if hasattr(last_props, name):
                    try:
                        setattr(self, name, getattr(last_props, name))
                    except Exception:
                        pass
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scenarioValue")
        layout.separator()
        layout.prop(self, "import_mode")
        layout.prop(self, "skip_non_renderable_actors")
        layout.prop(self, "import_geometry")
        layout.prop(self, "import_non_geometry_objects")
        layout.prop(self, "import_graffiti")
        layout.prop(self, "import_background")
        layout.prop(self, "import_pack_extra_geometry")
        layout.prop(self, "report_verbosity")

    @staticmethod
    def menu_func(self, context):
        self.layout.operator(ImportOperator.bl_idname, text="Splatoon 3 Stage (.byml)")

    def execute(self, context):
        importer = Importer(
            self,
            context,
            self.filepath,
            self.scenarioValue,
            self.import_mode,
            self.skip_non_renderable_actors,
            self.import_geometry,
            self.import_non_geometry_objects,
            self.import_graffiti,
            self.import_background,
            self.import_pack_extra_geometry,
            self.report_verbosity,
        )
        return importer.run()


class ImportPackOperator(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Load a Splatoon 3 stage pack"""

    bl_idname = "import_scene.osi_pack"
    bl_label = "Import Splatoon 3 Stage Pack"
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".zs"
    filter_glob: bpy.props.StringProperty(default="*.pack.zs", options={"HIDDEN"})
    filepath: bpy.props.StringProperty(
        name="File Path",
        description="Filepath used for importing a RomFS/Pack/Scene/*.pack.zs stage pack.",
        maxlen=1024,
        default="",
    )
    scenarioValue: bpy.props.IntProperty(name="Scenario", description="Scenario to import.", min=0, max=14)
    import_mode: bpy.props.EnumProperty(
        name="Import Mode",
        description="Choose material/texture import behavior.",
        items=(
            ("FULL", "Full", "Import materials and decode textures."),
            ("MATERIALS", "Materials Only", "Import materials using gray/no-texture fallback; skip texture decoding."),
            ("NONE", "No Materials/Textures", "Skip material and texture setup for fastest imports."),
        ),
        default="FULL",
    )
    skip_non_renderable_actors: bpy.props.BoolProperty(
        name="Skip Non-renderable Actors",
        description="Skip helper/control actors that normally do not have BFRES models. Gfx light actors are controlled by Import Non-Geometry Objects.",
        default=True,
    )
    import_geometry: bpy.props.BoolProperty(
        name="Import Geometry",
        description="Import BFRES mesh geometry from BCETT actors and pack scene-component geometry.",
        default=True,
    )
    import_non_geometry_objects: bpy.props.BoolProperty(
        name="Import Non-Geometry Objects",
        description="Create Blender-native objects for supported non-mesh BCETT actors.",
        default=True,
    )
    import_graffiti: bpy.props.BoolProperty(
        name="Import Graffiti",
        description="Import pack graffiti decal planes from Graffiti/*.byml and SceneGraffitiPlacementData.",
        default=True,
    )
    import_background: bpy.props.BoolProperty(
        name="Import Background",
        description="Import Fld_[stage] ActorParam ModelInfo submodels into a Background collection.",
        default=False,
    )
    import_pack_extra_geometry: bpy.props.BoolProperty(
        name="Import Pack Extra Geometry",
        description="Import optional geometry discovered from pack scene-component passes, such as water candidates. Off keeps these extras read-only/metadata-only.",
        default=False,
    )
    report_verbosity: bpy.props.EnumProperty(
        name="Import Report",
        description="Choose how much detail to write to the import report file.",
        items=(
            ("FULL", "Full", "Write object-level import decisions, warnings, failures, and summaries; suppress per-texture BFRES/BNTX diagnostics."),
            ("DEBUG_TEXTURE", "Debug Texture", "Write Full report plus verbose per-texture BFRES/BNTX/image/cache diagnostics."),
            ("COMPACT", "Compact", "Suppress routine successful import/cache lines but keep warnings and special corrections."),
            ("MINIMAL", "Minimal", "Write summary plus warnings, failures, and explicit skips only."),
        ),
        default="COMPACT",
    )

    _remembered_option_names = ImportOperator._remembered_option_names

    def invoke(self, context, event):
        try:
            last_props = context.window_manager.operator_properties_last(self.bl_idname)
        except Exception:
            last_props = None
        if last_props:
            for name in self._remembered_option_names:
                if hasattr(last_props, name):
                    try:
                        setattr(self, name, getattr(last_props, name))
                    except Exception:
                        pass
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scenarioValue")
        layout.separator()
        layout.prop(self, "import_mode")
        layout.prop(self, "skip_non_renderable_actors")
        layout.prop(self, "import_geometry")
        layout.prop(self, "import_non_geometry_objects")
        layout.prop(self, "import_graffiti")
        layout.prop(self, "import_background")
        layout.prop(self, "import_pack_extra_geometry")
        layout.prop(self, "report_verbosity")

    @staticmethod
    def menu_func(self, context):
        self.layout.operator(ImportPackOperator.bl_idname, text="Splatoon 3 Stage Pack (.pack.zs)")

    def execute(self, context):
        importer = Importer(
            self,
            context,
            self.filepath,
            self.scenarioValue,
            self.import_mode,
            self.skip_non_renderable_actors,
            self.import_geometry,
            self.import_non_geometry_objects,
            self.import_graffiti,
            self.import_background,
            self.import_pack_extra_geometry,
            self.report_verbosity,
            pack_mode=True,
        )
        return importer.run()


class Importer:
    def __init__(self, operator, context, filepath, scenarioValue, import_mode, skip_non_renderable_actors, import_geometry, import_non_geometry_objects, import_graffiti, import_background, import_pack_extra_geometry, report_verbosity, pack_mode=False):
        self.operator = operator
        self.context = context
        self.filepath = filepath
        self.scenarioValue = scenarioValue
        self.import_mode = import_mode
        self.skip_non_renderable_actors = skip_non_renderable_actors
        self.import_geometry = import_geometry
        self.import_non_geometry_objects = import_non_geometry_objects
        self.import_graffiti = import_graffiti
        self.import_background = import_background
        self.import_pack_extra_geometry = import_pack_extra_geometry
        self.report_verbosity = report_verbosity
        self.pack_mode = pack_mode

    def _stage_byml_from_archive(self, archive_path):
        output_dir = Path(sarc.extract_sarc_file(archive_path))
        expected = output_dir / (output_dir.name + ".byml")
        if expected.is_file():
            return str(expected)
        byml_files = sorted(output_dir.rglob("*.byml"))
        if not byml_files:
            raise FileNotFoundError("No .byml file was found after extracting %s" % archive_path)
        return str(byml_files[0])

    def run(self):
        try:
            path = Path(self.filepath)
            addon.stageFile = str(path)
            addon.stageFileBytes = None
            addon.scenario = self.scenarioValue
            addon.import_mode = self.import_mode
            addon.skip_non_renderable_actors = self.skip_non_renderable_actors
            addon.import_geometry = self.import_geometry
            addon.import_non_geometry_objects = self.import_non_geometry_objects
            addon.import_graffiti = self.import_graffiti
            addon.import_background = self.import_background
            addon.import_pack_extra_geometry = self.import_pack_extra_geometry
            try:
                addon._osi_non_geometry_impl.import_pack_extra_geometry = self.import_pack_extra_geometry
            except Exception:
                pass
            # Backward-compatible globals for older internal code paths.
            addon.skip_gfx_actors = self.skip_non_renderable_actors
            addon.skip_change_paintable_area = self.skip_non_renderable_actors
            addon.report_verbosity = self.report_verbosity

            if self.pack_mode:
                if path.is_dir():
                    raise RuntimeError(
                        "Selected path is a folder, not a stage pack. "
                        "Please select a Splatoon 3 stage .pack.zs file, for example Vss_Hiagari04.pack.zs, "
                        "from the RomFS/Pack/Scene folder rather than selecting a directory: %s" % path
                    )
                if not path.exists():
                    raise FileNotFoundError("The selected stage pack could not be found: %s" % path)
                pack_files = _read_stage_pack_files(str(path))
                internal_name, bcett_bytes = _find_bcett_byml_in_stage_pack_files(str(path), pack_files)
                addon.stageFile = "%s::%s" % (str(path), internal_name)
                addon.stageFileBytes = bcett_bytes
                addon.stagePackPath = str(path)
                addon.stagePackFiles = pack_files
                try:
                    prefs = addon._addon_preferences()
                    romfs_root = addon._romfs_root_path(prefs)
                except Exception:
                    romfs_root = None
                addon.stagePackResolver = addon.PackResourceResolver(pack_files, romfs_root, str(path))
            else:
                addon.stagePackPath = ""
                addon.stagePackFiles = {}
                addon.stagePackResolver = None
            if (not self.pack_mode) and path.suffix.lower() in {".szs", ".sarc"}:
                addon.stageFile = self._stage_byml_from_archive(str(path))

            addon.run_import()
            try:
                if getattr(addon, "last_import_report_path", ""):
                    self.operator.report({"INFO"}, "BYML import report: %s" % addon.last_import_report_path)
            except Exception:
                pass
            return {"FINISHED"}
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            tb = traceback.format_exc()
            print(tb)
            report_path = None
            body = "OSI import failed before completion.\n\nFile: %s\nError: %s\n\nTraceback:\n%s" % (self.filepath, message, tb)
            # Prefer Blender tempdir so failures are reported even when the source
            # RomFS/pack folder is not writable. Fall back to the file's folder.
            candidates = []
            try:
                import tempfile
                tmp = getattr(addon.bpy.app, "tempdir", "") or tempfile.gettempdir()
                if tmp:
                    candidates.append(Path(tmp))
            except Exception:
                pass
            try:
                candidates.append(Path(self.filepath).parent)
            except Exception:
                pass
            for folder in candidates:
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    safe = Path(self.filepath).name.replace("/", "_").replace(":", "_") or "stage"
                    candidate = folder / ("OSI_Import_Error_%s.txt" % safe)
                    candidate.write_text(body, encoding="utf-8", errors="replace")
                    report_path = candidate
                    break
                except Exception:
                    continue
            try:
                addon.last_import_report_path = str(report_path or "")
            except Exception:
                pass
            if report_path:
                self.operator.report({"ERROR"}, "%s; error report: %s" % (message, report_path))
            else:
                self.operator.report({"ERROR"}, message)
            return {"CANCELLED"}
