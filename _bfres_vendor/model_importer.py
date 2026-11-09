import logging

import bpy

from .material_importer import import_material, report_material_input_detail, report_material_output_detail
from .mesh_importer import import_mesh
from .skeleton_importer import import_fskl

log = logging.getLogger(__name__)


class ModelImporter:
    def __init__(self, parent):
        self.operator = parent.operator
        self.texture_map = parent.texture_map

    def convert_fmdl(self, fmdl, collection):
        self.fskl_ob = import_fskl(fmdl, fmdl.skeleton, collection, self.operator.copy_bone_transforms)

        # Import all materials for this model.  Keep material/texture failures
        # local to each material so a bad BNTX decode or shader node assignment
        # cannot abort the whole FMDL.
        material_map = {}
        fallback_mat = None
        if getattr(self.operator, "report_texture_details", False) and hasattr(self.operator, "report"):
            try:
                self.operator.report({"INFO"}, "FMDL_TEXTURE_DIAG owner=%r fmdl=%r materials=%d shapes=%d texture_map_keys=%s" % (getattr(self.operator, "texture_diag_owner", "UNKNOWN"), getattr(fmdl, "name", "<unnamed>"), len(getattr(fmdl, "materials", {}) or {}), len(getattr(fmdl, "shapes", {}) or {}), [str(k) for k in list((self.texture_map or {}).keys())[:50]]))
            except Exception:
                pass
        import_materials = bool(getattr(self.operator, "import_materials", True))
        for i, fmat in enumerate(fmdl.materials.values()):
            log.info("Importing material %3d / %3d...", i + 1, len(fmdl.materials))
            try:
                report_material_input_detail(self.operator, getattr(fmdl, "name", "<unnamed>"), fmat, self.texture_map)
                if not import_materials:
                    mat = None
                elif getattr(self.operator, "import_tex_mode", "EMBEDDED") == "OFF":
                    if fallback_mat is None:
                        fallback_mat = bpy.data.materials.get("OSI_Fallback_NoTexture")
                        if fallback_mat is None:
                            fallback_mat = bpy.data.materials.new(name="OSI_Fallback_NoTexture")
                            fallback_mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
                    mat = fallback_mat
                else:
                    mat = import_material(fmat, self.texture_map, self.operator.name_prefix, self.operator)
                report_material_output_detail(self.operator, getattr(fmdl, "name", "<unnamed>"), fmat, mat)
            except Exception as exc:
                msg = "Skipped material '%s' in FMDL '%s' because material/texture import failed: %s" % (
                    getattr(fmat, "name", "<unnamed>"), getattr(fmdl, "name", "<unnamed>"), exc
                )
                log.warning(msg, exc_info=True)
                if hasattr(self.operator, "report"):
                    tex_names = [str(getattr(ref, "name", "")) for ref in getattr(fmat, "texture_refs", []) or []]
                    self.operator.report({"WARNING"}, "MATERIAL_FAIL_DETAIL owner=%r fmdl=%r material=%r textures=%s error=%r" % (getattr(self.operator, "texture_diag_owner", "UNKNOWN"), getattr(fmdl, "name", "<unnamed>"), getattr(fmat, "name", "<unnamed>"), tex_names, exc))
                    self.operator.report({"WARNING"}, msg)
                if fallback_mat is None:
                    fallback_mat = bpy.data.materials.get("OSI_Fallback_NoTexture")
                    if fallback_mat is None:
                        fallback_mat = bpy.data.materials.new(name="OSI_Fallback_NoTexture")
                        fallback_mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
                mat = fallback_mat
            material_map[i] = mat

        # Go through the polygons in this model and create mesh objects representing them.
        for i, fshp in enumerate(fmdl.shapes.values()):
            log.info("Importing shape %3d / %3d '%s'...", i + 1, len(fmdl.shapes), fshp.name)
            try:
                mesh_object = import_mesh(fmdl, fshp, self.operator.lod_index, self.operator.custom_normals)
                mat = material_map.get(fshp.material_idx)
                if mat is not None:
                    mesh_object.data.materials.append(mat)

                # Parent to the empty FMDL object and link it to the scene.
                collection.objects.link(mesh_object)
                mesh_object.parent = self.fskl_ob

                # Add armature modifier
                modifier = mesh_object.modifiers.new(name=fshp.name, type="ARMATURE")
                modifier.object = self.fskl_ob
                modifier.use_bone_envelopes = False
                modifier.use_vertex_groups = True
            except Exception as exc:
                msg = "Skipped shape '%s' in FMDL '%s' because mesh import failed: %s" % (
                    getattr(fshp, "name", "<unnamed>"), getattr(fmdl, "name", "<unnamed>"), exc
                )
                log.warning(msg, exc_info=True)
                if hasattr(self.operator, "report"):
                    self.operator.report({"WARNING"}, msg)
                continue
