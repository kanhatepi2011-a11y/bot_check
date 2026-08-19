import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tiktok_checker import (
    cleanup_stale_temp_directories,
    remove_managed_temp_directory,
)


class TempStorageTests(unittest.TestCase):
    def test_cleanup_removes_only_bot_owned_directories(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            managed = root / "telegram_tiktok_download_old"
            unrelated = root / "keep-me"
            managed.mkdir()
            unrelated.mkdir()
            (managed / "video.mp4").write_bytes(b"video")
            (unrelated / "important.txt").write_text("keep", encoding="utf-8")

            with patch.dict(os.environ, {"BOT_TEMP_DIR": str(root)}):
                removed = cleanup_stale_temp_directories(max_age_seconds=0)

            self.assertEqual(removed, [managed])
            self.assertFalse(managed.exists())
            self.assertTrue(unrelated.exists())

    def test_direct_removal_rejects_unmanaged_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            unrelated = root / "uploads"
            unrelated.mkdir()

            with patch.dict(os.environ, {"BOT_TEMP_DIR": str(root)}):
                deleted = remove_managed_temp_directory(unrelated)

            self.assertFalse(deleted)
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
