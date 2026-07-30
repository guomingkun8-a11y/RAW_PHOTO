from __future__ import annotations

import unittest

from services.captcha_service import CaptchaService


class CaptchaServiceTests(unittest.TestCase):
    def test_captcha_is_verified_once(self) -> None:
        service = CaptchaService()
        item = service.create()
        captcha_id = item["captcha_id"]
        answer = service._entries[captcha_id].answer

        self.assertTrue(item["image_data_url"].startswith("data:image/png;base64,"))
        self.assertTrue(service.verify(captcha_id, answer.upper()))
        self.assertFalse(service.verify(captcha_id, answer.upper()))


if __name__ == "__main__":
    unittest.main()
