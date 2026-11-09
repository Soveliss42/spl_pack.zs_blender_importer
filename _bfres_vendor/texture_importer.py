from __future__ import annotations

import logging
import hashlib
from typing import TYPE_CHECKING

import bpy
import numpy as np

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .bntx.bntx import BNTX


_OSI_IMAGE_CACHE = {}


def _osi_image_is_valid(image):
    try:
        return image is not None and getattr(image, "name", None) in bpy.data.images and not bool(getattr(image, "is_missing", False))
    except ReferenceError:
        return False
    except Exception:
        return False


def _osi_texture_identity(tex):
    """Stable key for reusing packed Blender Image datablocks across actors.

    The key deliberately ignores operator.name_prefix so repeated imports of the
    same BNTX texture from different actor instances share one Image datablock.
    Materials may still be unique per actor/rail, but their image nodes can point
    to the same packed image.
    """
    try:
        raw = bytes(getattr(tex, "pixels", b"") or b"")
    except Exception:
        raw = repr(getattr(tex, "pixels", b"")).encode("utf-8", "replace")
    try:
        pix_hash = hashlib.sha1(raw).hexdigest()
    except Exception:
        pix_hash = "nohash"
    try:
        ch = tuple(getattr(tex, "channel_types", b""))
    except Exception:
        ch = ()
    return (
        str(getattr(tex, "name", "")),
        int(getattr(tex, "width", 0) or 0),
        int(getattr(tex, "height", 0) or 0),
        getattr(getattr(tex, "format_", None), "__name__", ""),
        getattr(getattr(tex, "fmt_dtype", None), "name", ""),
        ch,
        pix_hash,
    )


def import_textures(bntx: BNTX, operator):
    """Import textures from BNTX."""
    images = {}
    for i, tex in enumerate(bntx.nx.textures):
        log.info(
            "Importing texture %3d/%3d '%s' (%s, %s)...",
            i + 1,
            len(bntx.nx.textures),
            tex.name,
            tex.format_.__name__,
            tex.fmt_dtype.name,
        )
        if getattr(operator, "report_texture_details", False) and hasattr(operator, "report"):
            try:
                operator.report({"INFO"}, "BNTX_TEXTURE_DETAIL owner=%r index=%d total=%d name=%r size=%dx%d format=%s dtype=%s channel_types=%r swizzle=%r mip_count=%r tile_mode=%r" % (
                    getattr(operator, "texture_diag_owner", "UNKNOWN"), i, len(bntx.nx.textures), str(tex.name),
                    int(getattr(tex, "width", 0)), int(getattr(tex, "height", 0)),
                    getattr(getattr(tex, "format_", None), "__name__", "<unknown>"),
                    getattr(getattr(tex, "fmt_dtype", None), "name", "<unknown>"),
                    tuple(getattr(tex, "channel_types", b"")),
                    getattr(tex, "swizzle", None), getattr(tex, "mip_count", None), getattr(tex, "tile_mode", None)
                ))
            except Exception:
                pass
        float_buffer = False
        isdata = not bool(tex.fmt_dtype.name == "SRGB")
        alpha = bool(b"\x05" in tex.channel_types)

        if tex.fmt_dtype.name in {"UHALF", "SINGLE"}:
            isdata = False
            float_buffer = True

        image_name = operator.name_prefix + str(tex.name)
        cache_key = _osi_texture_identity(tex)
        cached_image = _OSI_IMAGE_CACHE.get(cache_key)
        if _osi_image_is_valid(cached_image):
            if hasattr(operator, "report"):
                try:
                    operator.report({"INFO"}, "IMAGE_REUSE owner=%r requested=%r reused=%r texture=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), image_name, getattr(cached_image, "name", ""), str(tex.name)))
                except Exception:
                    pass
            images[tex.name] = cached_image
            images[str(tex.name)] = cached_image
            continue
        image = bpy.data.images.new(
            name=image_name,
            width=tex.width,
            height=tex.height,
            float_buffer=float_buffer,
            alpha=alpha,
            is_data=isdata,
        )

        decode_error = getattr(tex, "_osi_texture_decode_error", None)
        if decode_error:
            message = "Texture '%s' (%s) decode failed; using gray fallback material/texture: %s" % (
                getattr(tex, "name", "<unnamed>"),
                getattr(getattr(tex, "format_", None), "__name__", "<unknown>"),
                decode_error,
            )
            log.warning(message)
            if hasattr(operator, "report"):
                operator.report({"WARNING"}, message)
                if getattr(operator, "report_texture_details", False):
                    operator.report({"WARNING"}, "BNTX_TEXTURE_DECODE_FAIL_DETAIL owner=%r name=%r size=%dx%d format=%s dtype=%s error=%r" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), str(getattr(tex, "name", "<unnamed>")), int(getattr(tex, "width", 0)), int(getattr(tex, "height", 0)), getattr(getattr(tex, "format_", None), "__name__", "<unknown>"), getattr(getattr(tex, "fmt_dtype", None), "name", "<unknown>"), decode_error))
            pixels = np.empty((tex.height, tex.width, 4), dtype=np.float32)
            pixels[..., 0] = 0.8
            pixels[..., 1] = 0.8
            pixels[..., 2] = 0.8
            pixels[..., 3] = 1.0
        else:
            # Issues arise when textures are not multiples of 4, pretty rare.
            if len(tex.pixels) > tex.width * tex.height * 4:
                pixels = tex.pixels[: tex.width * tex.height * 4]
                pixels = tex.format_.decodepixels(pixels)
            else:
                pixels = tex.format_.decodepixels(tex.pixels)
            pixels = pixels.reshape((tex.height, tex.width, 4))

        if (
            operator.component_selector
            and tex.format_.__name__ != "BC1"  # don't make alpha channel if it's not needed
            and not (tex.format_.__name__ == "BC5" and tex.channel_types[2] == 0)  # normal maps
        ):
            temppix = pixels.copy()
            for ch in range(4):
                if tex.channel_types[ch] == ch + 2:
                    continue
                match tex.channel_types[ch]:
                    case 0:
                        pixels[..., ch] = 0
                    case 1:
                        pixels[..., ch] = 1
                    case 2:
                        pixels[..., ch] = temppix[..., 0]
                    case 3:
                        pixels[..., ch] = temppix[..., 1]
                    case 4:
                        pixels[..., ch] = temppix[..., 2]
                    case 5:
                        pixels[..., ch] = temppix[..., 3]

        # Add some file data if it's needed:
        if tex.fmt_dtype.name in {"UHALF", "SINGLE"}:
            image.file_format = "OPEN_EXR"
            image.colorspace_settings.name = "sRGB"
        else:
            image.file_format = "PNG"

        # flip image from dx to gl
        pixels = np.flipud(pixels)

        if getattr(operator, "report_texture_details", False) and hasattr(operator, "report"):
            try:
                operator.report({"INFO"}, "BNTX_TEXTURE_PIXELS_DETAIL owner=%r name=%r pixels_shape=%r min=%0.6f max=%0.6f alpha=%s is_data=%s float_buffer=%s" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), image_name, tuple(pixels.shape), float(np.nanmin(pixels)), float(np.nanmax(pixels)), alpha, isdata, float_buffer))
            except Exception:
                pass
        image.pixels = np.ravel(pixels)

        image.use_fake_user = operator.add_fake_user

        image.update()
        image.pack()
        try:
            if hasattr(operator, "report"):
                operator.report({"INFO"}, "IMAGE_CREATE owner=%r name=%r size=%dx%d format=%s dtype=%s is_data=%s" % (getattr(operator, "texture_diag_owner", "UNKNOWN"), image_name, tex.width, tex.height, getattr(tex.format_, "__name__", "<unknown>"), tex.fmt_dtype.name, isdata))
        except Exception:
            pass
        try:
            _OSI_IMAGE_CACHE[cache_key] = image
        except Exception:
            pass
        images[tex.name] = image
        images[str(tex.name)] = image
    return images
