from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tiktok_checker import (
    TIKTOK_FORMAT_SELECTOR,
    _extract_method_from_ffprobe,
    _extract_mp4_artist_atom,
)


def _box(atom_type: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + atom_type + payload


def _metadata_file(atom_type: bytes, text: str) -> bytes:
    data = _box(b"data", b"\x00\x00\x00\x01" + b"\x00\x00\x00\x00" + text.encode("utf-8"))
    item = _box(atom_type, data)
    ilst = _box(b"ilst", item)
    meta = _box(b"meta", b"\x00\x00\x00\x00" + ilst)
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"moov", _box(b"udta", meta))


class MethodExtractionTests(unittest.TestCase):
    def test_prefers_original_single_file_mp4_before_merge(self) -> None:
        self.assertTrue(TIKTOK_FORMAT_SELECTOR.startswith("b[ext=mp4]/b/"))

    def test_reads_patcher_text_from_ffprobe_comment(self) -> None:
        data = {
            "format": {
                "tags": {
                    "comment": "Patched by Compressbase.com",
                    "encoder": "Lavf61.7.100",
                }
            },
            "streams": [],
        }
        self.assertEqual(
            _extract_method_from_ffprobe(data),
            "Patched by Compressbase.com",
        )

    def test_ignores_normal_description_without_method_marker(self) -> None:
        data = {"format": {"tags": {"description": "My holiday video"}}}
        self.assertEqual(_extract_method_from_ffprobe(data), "")

    def test_reads_comment_atom_when_ffprobe_does_not_expose_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.mp4"
            path.write_bytes(_metadata_file(b"\xa9cmt", "Patched by Compressbase.com"))
            self.assertEqual(
                _extract_mp4_artist_atom(path),
                "Patched by Compressbase.com",
            )

    def test_reads_artist_atom_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.mp4"
            path.write_bytes(_metadata_file(b"\xa9ART", "TheziessMethod.site"))
            self.assertEqual(_extract_mp4_artist_atom(path), "TheziessMethod.site")


if __name__ == "__main__":
    unittest.main()
