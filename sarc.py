"""Small, dependency-free Yaz0/SARC extractor for Blender add-on use."""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Iterable, Tuple


def decompress_yaz0(data: bytes) -> bytes:
    """Return decompressed Yaz0 data, or the input unchanged if it is not Yaz0."""
    if data[:4] != b"Yaz0":
        return data
    if len(data) < 16:
        raise ValueError("Invalid Yaz0 data: header is too short")

    dest_size = struct.unpack_from(">I", data, 4)[0]
    src_pos = 16
    out = bytearray()

    while len(out) < dest_size:
        if src_pos >= len(data):
            raise ValueError("Invalid Yaz0 data: unexpected end of stream")
        code = data[src_pos]
        src_pos += 1
        for bit in range(8):
            if code & (0x80 >> bit):
                if src_pos >= len(data):
                    raise ValueError("Invalid Yaz0 data: unexpected literal end")
                out.append(data[src_pos])
                src_pos += 1
            else:
                if src_pos + 1 >= len(data):
                    raise ValueError("Invalid Yaz0 data: unexpected backref end")
                byte1 = data[src_pos]
                byte2 = data[src_pos + 1]
                src_pos += 2
                distance = ((byte1 & 0x0F) << 8) | byte2
                copy_src = len(out) - (distance + 1)
                length = byte1 >> 4
                if length == 0:
                    if src_pos >= len(data):
                        raise ValueError("Invalid Yaz0 data: missing extended length")
                    length = data[src_pos] + 0x12
                    src_pos += 1
                else:
                    length += 2
                if copy_src < 0:
                    raise ValueError("Invalid Yaz0 data: invalid back-reference")
                for _ in range(length):
                    out.append(out[copy_src])
                    copy_src += 1
                    if len(out) >= dest_size:
                        break
            if len(out) >= dest_size:
                break
    return bytes(out)


def _endian_from_bom(bom: bytes) -> str:
    if bom == b"\xFE\xFF":
        return ">"
    if bom == b"\xFF\xFE":
        return "<"
    raise ValueError("Invalid SARC byte-order mark")


def iter_sarc_files(data: bytes) -> Iterable[Tuple[str, bytes]]:
    """Yield (relative_path, file_bytes) from SARC archive bytes."""
    data = decompress_yaz0(data)
    if data[:4] != b"SARC":
        raise ValueError("Input is not a SARC archive")
    if len(data) < 0x14:
        raise ValueError("Invalid SARC data: header is too short")

    endian = _endian_from_bom(data[6:8])
    file_size, data_offset = struct.unpack_from(endian + "II", data, 8)
    if file_size and file_size > len(data):
        file_size = len(data)

    sfat_offset = 0x14
    if data[sfat_offset:sfat_offset + 4] != b"SFAT":
        raise ValueError("Invalid SARC data: missing SFAT")
    _sfat_header_size, node_count, _hash_multiplier = struct.unpack_from(endian + "HHI", data, sfat_offset + 4)
    node_offset = sfat_offset + 0x0C
    sfnt_offset = node_offset + node_count * 0x10
    if data[sfnt_offset:sfnt_offset + 4] != b"SFNT":
        raise ValueError("Invalid SARC data: missing SFNT")
    string_base = sfnt_offset + 0x08

    for index in range(node_count):
        entry = node_offset + index * 0x10
        _name_hash, attributes, start, end = struct.unpack_from(endian + "IIII", data, entry)
        has_name = attributes & 0x01000000
        if has_name:
            name_offset = string_base + ((attributes & 0x00FFFFFF) * 4)
            name_end = data.find(b"\x00", name_offset)
            if name_end == -1:
                name_end = len(data)
            name = data[name_offset:name_end].decode("utf-8", "replace")
        else:
            name = "file_%04d.bin" % index
        safe_parts = [part for part in Path(name).parts if part not in ("", ".", "..")]
        safe_name = str(Path(*safe_parts)) if safe_parts else "file_%04d.bin" % index
        yield safe_name, data[data_offset + start:data_offset + end]


def extract_sarc_file(archive_path: str, output_dir: str | None = None) -> str:
    """Extract a .szs/.sarc archive. Returns the output directory."""
    archive = Path(archive_path)
    if output_dir is None:
        output = archive.with_suffix("")
    else:
        output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    content = archive.read_bytes()
    for relative_name, payload in iter_sarc_files(content):
        destination = output / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return str(output)
