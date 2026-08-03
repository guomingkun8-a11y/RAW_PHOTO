from __future__ import annotations

import unittest
from unittest import mock

import api.support as support


class FakeConfig:
    def __init__(self, *, enabled: bool):
        self.auth_key = "legacy-secret"
        self.legacy_auth_key_admin_enabled = enabled


class LegacyAuthKeyAdminTests(unittest.TestCase):
    def test_legacy_admin_identity_is_disabled_by_default(self) -> None:
        with mock.patch.object(support, "config", FakeConfig(enabled=False)):
            self.assertIsNone(support._legacy_admin_identity("legacy-secret"))

    def test_legacy_admin_identity_requires_explicit_switch(self) -> None:
        with mock.patch.object(support, "config", FakeConfig(enabled=True)):
            identity = support._legacy_admin_identity("legacy-secret")

        self.assertIsNotNone(identity)
        self.assertEqual(identity["role"], "admin")
        self.assertEqual(identity["username"], "legacy-admin")


if __name__ == "__main__":
    unittest.main()
