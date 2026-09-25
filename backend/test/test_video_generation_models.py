from __future__ import annotations

import unittest

from services.video.video_generation_models import (
    VIDEO_GENERATION_MODEL_SPECS,
    normalize_video_generation_options,
    public_video_generation_models,
)


class VideoGenerationModelSpecTests(unittest.TestCase):
    def test_hailuo_h3_defaults_to_768p(self):
        options = normalize_video_generation_options("hailuo-h3", duration_secs=5)

        self.assertEqual(options["aspect_ratio"], "16:9")
        self.assertEqual(options["duration_secs"], 5)
        self.assertEqual(options["params"], {
            "aspect_ratio": "16:9",
            "duration": "5",
            "resolution": "768P",
        })

    def test_seedance_25_adds_web_search_false(self):
        options = normalize_video_generation_options(
            "doubao-seedance-2-5-260628",
            aspect_ratio="9:16",
            duration_secs=8,
            resolution="720p",
        )

        self.assertEqual(options["params"], {
            "aspect_ratio": "9:16",
            "duration": "8",
            "resolution": "720p",
            "web_search": False,
        })

    def test_kling_v3_uses_std_mode(self):
        options = normalize_video_generation_options("kling-v3-video", duration_secs=5)

        self.assertEqual(options["quality"], "std")
        self.assertEqual(options["params"], {
            "aspect_ratio": "16:9",
            "duration": "5",
            "mode": "std",
        })

    def test_hailuo_reference_generation_matches_platform_spec(self):
        options = normalize_video_generation_options(
            "hailuo-h3-cankaosheng",
            aspect_ratio="adaptive",
            duration_secs=15,
            resolution="4K",
        )

        self.assertEqual(options["modes"], ("image_to_video",))
        self.assertEqual((options["min_images"], options["max_images"]), (1, 9))
        self.assertEqual(options["params"], {
            "aspect_ratio": "adaptive",
            "duration": "15",
            "resolution": "4K",
        })

    def test_hailuo_h3_max_first_last_frame_matches_platform_spec(self):
        options = normalize_video_generation_options(
            "hailuo-h3-max-shouweizhen",
            aspect_ratio="16:9",
            duration_secs=15,
            resolution="768P",
        )

        self.assertEqual(options["modes"], ("image_to_video",))
        self.assertEqual((options["min_images"], options["max_images"]), (1, 2))
        self.assertEqual(options["image_input_kind"], "first_last_frame")
        self.assertEqual(options["aspect_ratio"], "adaptive")
        self.assertEqual(options["params"], {
            "duration": "15",
            "resolution": "768P",
        })

    def test_gk_video_35_requires_exactly_one_image(self):
        options = normalize_video_generation_options(
            "gk-video-3.5",
            aspect_ratio="2:3",
            duration_secs=1,
            resolution="720p",
        )

        self.assertEqual((options["min_images"], options["max_images"]), (1, 1))
        self.assertEqual(options["params"]["aspect_ratio"], "2:3")
        self.assertEqual(options["params"]["duration"], "1")

    def test_seedance_reference_generation_supports_auto_duration(self):
        options = normalize_video_generation_options(
            "doubao-seedance-2-5-cankaosheng",
            aspect_ratio="adaptive",
            duration_secs="auto",
            resolution="480p",
        )

        self.assertEqual(options["duration_secs"], "auto")
        self.assertEqual((options["min_images"], options["max_images"]), (1, 30))
        self.assertEqual(options["params"], {
            "aspect_ratio": "adaptive",
            "duration": "auto",
            "resolution": "480p",
            "web_search": False,
        })

    def test_public_model_specs_include_image_limits(self):
        public_specs = {item["id"]: item for item in public_video_generation_models()}

        self.assertEqual(public_specs["hailuo-h3-cankaosheng"]["max_images"], 9)
        self.assertEqual(public_specs["hailuo-h3-max-shouweizhen"]["max_images"], 2)
        self.assertEqual(public_specs["hailuo-h3-max-shouweizhen"]["image_input_kind"], "first_last_frame")
        self.assertEqual(public_specs["gk-video-3.5"]["max_images"], 1)
        self.assertEqual(public_specs["doubao-seedance-2-5-cankaosheng"]["max_images"], 30)
        self.assertIn("auto", public_specs["doubao-seedance-2-5-cankaosheng"]["durations"])
        self.assertEqual(VIDEO_GENERATION_MODEL_SPECS["doubao-seedance-2-5-cankaosheng"]["default_duration"], "auto")

    def test_invalid_model_duration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "supports duration"):
            normalize_video_generation_options("kling-v3-video", duration_secs=30)


if __name__ == "__main__":
    unittest.main()
