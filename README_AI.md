# Splatoon 3 Stage Importer (OSI)

**Documented add-on version: 0.8.96** · **Blender minimum: 3.6** (recent development and testing use Blender 4.2)

A community Blender add-on for importing Splatoon 3 stage layouts, BFRES geometry, materials and textures, gameplay helpers, and selected scene components from `*.bcett.byml` and `Scene/*.pack.zs` archives. It builds on earlier Super Mario Odyssey importer work and a privately vendored BFRES/BNTX importer. This is an experimental reconstruction tool, not a complete implementation of Nintendo's rendering or gameplay engine.

This README consolidates the previous `README.md`, `README_PORT.md`, `README_BFRES_PORT.md`, `README_SPLATOON3_ACTORINFO.md`, and `PROJECT_SUMMARY.md`. Historical version-specific claims have been replaced by a description of the current code and its known limitations.

## Installation and inputs

1. In Blender, install the add-on ZIP through **Preferences > Add-ons > Install** (wording varies by Blender version), then enable **Splatoon 3 Stage Importer**.
2. Configure the **Splatoon 3 RomFS Root** in the add-on preferences. This should be the directory containing folders such as `Model/`, `RSDB/`, `Scene/`, and `Pack/`.
3. Use **File > Import** to select a Splatoon 3 stage `Scene/Vss_*.pack.zs` or an extracted `.bcett.byml` file. The pack route is recommended when scene-component data is available.
4. Select the categories to import in the **import dialog**. In particular, **Import Pack Extra Geometry** is an *import-time* setting, not an add-on preference.

A matching, legitimately obtained local RomFS dump is required; the add-on does not download game assets. The configured model folder may contain compressed `.bfres.zs` files. The BFRES importer is bundled under `io_scene_osi._bfres_vendor` and deliberately is not registered as a separate Blender add-on, to avoid interfering with standalone BFRES/SZS/BYML add-ons. Private wheel packaging supports ASTC texture decoding on supported platforms; availability still depends on the bundled wheel and Blender Python environment.

### Import-time options

- **Import Geometry:** BCETT actors and their BFRES models.
- **Import Non-Geometry Objects:** supported cameras, locators, areas, lights, sounds, rails, and environment helpers.
- **Import Graffiti:** graffiti placement and fold reconstruction.
- **Import Background:** field background models referenced by field ModelInfo submodels.
- **Import Pack Extra Geometry:** additional scene/pack geometry candidates, independent of non-geometry helpers.
- **Skip Non-Renderable Actors:** filters selected actors without renderable output.
- **Import Report:** minimal, compact, full, or texture-debug detail, as exposed in the import dialog.

Import reports describe asset resolution, import decisions, warnings, and partial failures. A completed import is **not** proof that every optional resource or texture was imported: inspect the report for `WARNING`, `MISSING`, `UNRESOLVED`, `SKIPPED`, and phase-specific summaries.

## What is imported

### BCETT geometry and actor metadata

BCETT identifies the placed actors. The importer resolves actor names, ModelInfo/ActorInfo references, and BFRES assets using the local RomFS and RSDB data, and builds a stage hierarchy. Geometry objects retain a reduced set of useful custom properties (actor/label, hashes, instance IDs, team fields, and applicable live properties); other categories retain their more detailed metadata for now. SRTHash-based instancing and BFRES caching are supported.

### Background geometry

**Background is not simply another list of BCETT actor rows.** The field actor is identified through BCETT, while its background targets come from field `ModelInfo.SubModels`. The background importer inventories plausible BFRES **containers**, reads the FMDL names inside them, and maps each requested submodel to the container that actually contains it. Multiple FMDLs may share one BFRES. For example, `FldBG_Cross00.bfres.zs` contains both `FldBG_Cross00` and `Fld_Cross00SideWall`. This container-first discovery was introduced in 0.8.95. The resulting objects are organized under Background rather than being treated as independent BCETT actor instances.

### Materials and team colors

BFRES/BNTX materials and textures are translated into Blender node graphs with approximate game shader behavior. The **View3D > Spl3** controls include team-color selection and application. Team-aware materials, team masks, and specialized objects such as ink rails use the active team where supported; neutral components remain neutral where required. The procedural InkRail segment material connects **Spl InkRail Object Color** to **Principled BSDF > Base Color**. Some textures may still be unresolved because of name aliases or resource references, and Blender shading is not a pixel-exact reproduction of the game.

### Sponges

Supported sponge actors expose `spl_sponge_inflate` with live updates. The stabilized system uses the armature `root` bone for visual placement, prepares the body mesh's local pivot so **ordinary body-object scaling** expands from the constrained sponge origin, and moves fixed-size corner parts using pose bones with a small inset compensation. Small sponges currently use a maximum multiplier of **2.0x**; `SpongeTall` uses **1.2x**. These are implementation values, not verified universal in-game limits; stage-specific maxima remain an open research question. **Spl3** also provides **Deflate All** and **Inflate All** buttons, setting all sponge roots to 0 and 1 respectively.

### Rails

InkRailOnline rails are reconstructed from linked rail data, with nodes, segments, small centers, and nozzles. Other supported rail types include GachiYagura rail visuals and AILift-related paths. Nodes and segments are organized hierarchically; mirror handling is supported where applicable. The importer does not blindly render every raw BCETT `Rails[]` array as geometry.

### Graffiti

Graffiti uses placement data and referenced images, with FoldInfo-based reconstruction of folded panels. The solver splits decal polygons at seams and uses centerpoint/panel relationships to determine fold direction. Unusual fold layouts and texture-space corner cases may still need investigation.

### Non-geometry, cameras, and environment

Supported gameplay areas, locators, lights, cameras, sounds, and volumes become Blender-native helpers. Pack scene import is driven by `Scene/Vss_[stage].engine__scene__SceneParam.bgyml` when present. Its `StartupMap` identifies the BCETT reference; `SceneGfxFieldEnv` resolves environment sets (such as day/night), with collections named from the environment set rather than raw rendering filenames. Referenced MainLight, Sky/SkySphere, and environment parameters are imported when resolvable. `SceneBake` and `SceneSoundEnv` are recognized but not fully reconstructed as game-equivalent systems. Sky model resolution can still fail for some actor-pack references.

## Scene organization and controls

The add-on groups imported content into stage-specific collections, including **Geometry**, **Background**, **Graffiti**, and **Non-Geometry** categories (cameras, areas, locators, rails, environment, lights, sounds). Exact nesting varies with imported data. The **View3D > Spl3** panel provides layer visibility controls, team-color tools, and sponge controls. Rendering environment collections are separated from normal gameplay layers where available.

## Code architecture (0.8.96)

The current module split is intentional: shared helpers should have one owner and be imported explicitly. Blender add-on reload order matters, so foundational modules are reloaded before importer modules. Avoid reintroducing wildcard `addon.py` namespace snapshots or wrappers that dynamically resolve their own names.

| Module | Responsibility |
| --- | --- |
| `__init__.py`, `importing.py` | Add-on registration, import operators and options |
| `addon.py` | Main stage import orchestration and remaining shared state |
| `actor_import.py`, `bfres_import_bridge.py` | Actor geometry, BFRES loading, cache/import integration |
| `non_geometry.py` | Native cameras, lights, areas, locators, and other helpers |
| `background.py` | Field background discovery and import |
| `graffiti.py`, `rails.py`, `team_colors.py` | Specialized visuals and live material/sponge controls |
| `layer_visibility.py`, `collection_utils.py` | Collection hierarchy and layer UI |
| `actor_helpers.py`, `value_utils.py`, `property_utils.py`, `transform.py` | Shared actor/value/property/coordinate utilities |
| `rsdb_loader.py`, `rsdb_helpers.py` | RSDB loading, lookups and annotations |
| `pack_context.py`, `pack_resources.py`, `romfs_resolver.py` | Stage pack and RomFS resource resolution |
| `report.py`, `report_utils.py`, `session.py`, `lifecycle.py` | Reporting, import state, cache/reload lifecycle |
| `byml.py`, `sarc.py` | File-format utilities |

The project originally ported Blender 3.6+ APIs (modern registration, import menus, preferences, properties, and OBJ import support) and replaced a Windows-only extraction dependency with Python archive handling. BFRES import is now the primary geometry path, not the historical OBJ workflow.

### Coordinate conventions

Nintendo stage transforms and Blender axes differ. Coordinate conversion should be centralized in `transform.py`; armature pose-space offsets also require consistent conversion. Sponge investigations identified an important pose-space relationship for imported rigs: Blender `(x, y, z)` coordinates may correspond to pose-space `(x, z, -y)` for the relevant armature setup. Do not assume raw BYML vectors can be assigned directly to pose bones.

## Known limitations and troubleshooting

- **Missing geometry:** check the report's exact BFRES container and FMDL target. A model may live inside a differently named BFRES, particularly for backgrounds.
- **Missing materials/textures:** inspect `MATERIAL_TEXTURE_MISSING` and alias resolution; missing texture warnings do not always mean the whole model failed.
- **Camera/area import `NameError`:** older builds (0.8.88–0.8.95) had split-module helper and reload-order regressions. 0.8.96 audited `actor_import.py` and `non_geometry.py` dependencies, but should still be regression-tested on multiple stages.
- **Scene sky/environment incomplete:** some actor-pack and ModelInfo paths remain unresolved, and Blender lighting does not reproduce game baked-lighting systems.
- **Sponges undersized on some stages:** maximum inflation may depend on stage/actor data; current multipliers are not data-driven.
- **Unusual graffiti/rail actors:** not every game-specific behavior is reconstructed.

For testing after a patch, restart Blender to avoid stale in-memory modules, import a fresh stage, and compare the import report and scene hierarchy against the previous known-good version. Particularly useful regression checks are: Cross00 background (including `Fld_Cross00SideWall`), Section01 cameras, non-geometry area import, team-color switching, sponge movement, and InkRail segment shader links.

## Development priorities

1. Replace the remaining synchronized `addon.py` dependencies with explicit, focused pack/model/report services.
2. Add automated tests for helper ownership, reload order, symbol resolution, and background container/FMDL inventory.
3. Improve BFRES texture alias resolution and environment/sky completeness.
4. Investigate data-driven sponge inflation limits across stages.

## Credits and redistribution

Original work and contributions include **Soveliss42**, **Philippus229** (Super Mario Odyssey importer), **Ranidspace** (BFRES importer), community reverse engineering and testing, and ChatGPT-assisted development. This package includes vendored third-party code; review applicable upstream licenses and attribution obligations before public redistribution. This README does not replace those licenses.
