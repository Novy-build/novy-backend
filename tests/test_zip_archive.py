"""ZIP archive behavior for React exports."""

from __future__ import annotations

import io
import zipfile

import pytest

from novy.domain.generation import GeneratedArchive, GeneratedFile
from novy.domain.generation import GeneratedFile as File
from novy.infrastructure.react_source.zip_archive import ZipArchiveWriter, files_from


def make_archive() -> GeneratedArchive:
    return GeneratedArchive(
        filename="notes-react-app.zip",
        files=(
            File("src/z.ts", "export const z = 1;\n"),
            File("src/a.ts", "export const a = 2;\n"),
            File("README.md", "readme\n"),
        ),
    )


def test_archive_is_a_valid_zip_with_sorted_entries() -> None:
    data = ZipArchiveWriter().write(make_archive())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["README.md", "src/a.ts", "src/z.ts"]
        assert archive.read("src/a.ts") == b"export const a = 2;\n"
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_archive_bytes_are_deterministic() -> None:
    writer = ZipArchiveWriter()
    assert writer.write(make_archive()) == writer.write(make_archive())


def test_files_from_uses_writer_order() -> None:
    assert [item.path for item in files_from(make_archive())] == [
        "README.md",
        "src/a.ts",
        "src/z.ts",
    ]


def test_traversal_paths_are_rejected() -> None:
    with pytest.raises(ValueError):
        GeneratedFile("../escape.ts", "bad")


def test_oversized_source_is_rejected() -> None:
    archive = GeneratedArchive(
        filename="notes-react-app.zip",
        files=(GeneratedFile("src/big.ts", "x" * (5 * 1024 * 1024)),),
    )
    with pytest.raises(ValueError):
        ZipArchiveWriter().write(archive)
