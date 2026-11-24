"""Deterministic in-memory ZIP assembly for generated source archives."""

from __future__ import annotations

import io
import zipfile

from novy.domain.generation import GeneratedArchive, GeneratedFile, safe_relative_path

MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ZipArchiveWriter:
    """Write a generated archive with stable order and timestamps."""

    def write(self, archive: GeneratedArchive) -> bytes:
        buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for source_file in sorted(archive.files, key=lambda item: item.path):
                safe_relative_path(source_file.path)
                content = source_file.content.encode("utf-8")
                total += len(content)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Generated source exceeds the size limit")
                info = zipfile.ZipInfo(source_file.path, date_time=_FIXED_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                output.writestr(info, content)
        return buffer.getvalue()


def files_from(archive: GeneratedArchive) -> tuple[GeneratedFile, ...]:
    """Return archive files in the order used by the writer."""
    return tuple(sorted(archive.files, key=lambda item: item.path))
