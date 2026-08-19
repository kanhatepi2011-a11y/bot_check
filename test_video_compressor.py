import unittest

from video_compressor import calculate_target_bitrates, fit_1080p_dimensions


class VideoCompressorTests(unittest.TestCase):
    def test_portrait_4k_becomes_1080p_without_changing_aspect(self) -> None:
        self.assertEqual(fit_1080p_dimensions(2160, 3840), (1080, 1920))

    def test_landscape_4k_becomes_1080p_without_changing_aspect(self) -> None:
        self.assertEqual(fit_1080p_dimensions(3840, 2160), (1920, 1080))

    def test_720p_is_not_upscaled(self) -> None:
        self.assertEqual(fit_1080p_dimensions(720, 1280), (720, 1280))

    def test_bitrate_budget_reserves_audio(self) -> None:
        video_kbps, audio_kbps = calculate_target_bitrates(
            duration_seconds=30,
            target_size_mb=47,
            has_audio=True,
        )
        self.assertGreater(video_kbps, 10_000)
        self.assertEqual(audio_kbps, 128)


if __name__ == "__main__":
    unittest.main()
