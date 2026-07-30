from __future__ import annotations

import unittest

from services.image_size import normalize_image_size


class ImageSizeTests(unittest.TestCase):
    def test_business_sizes_use_model_safe_canvases(self):
        self.assertEqual(normalize_image_size("750x3000"), "768x2304")
        self.assertEqual(normalize_image_size("800x800"), "816x816")
        self.assertEqual(normalize_image_size("750x6000"), "1024x3072")

    def test_rounds_to_model_constraints(self):
        self.assertEqual(normalize_image_size("1024x1365"), "1024x1360")
        self.assertEqual(normalize_image_size("512x512"), "816x816")
        self.assertEqual(normalize_image_size("4096x4096"), "2880x2880")

    def test_keeps_empty_and_auto(self):
        self.assertIsNone(normalize_image_size(None))
        self.assertEqual(normalize_image_size("auto"), "auto")


if __name__ == "__main__":
    unittest.main()
