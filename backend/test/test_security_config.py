import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.config import ConfigStore
from services.security_config import find_embedded_secret_paths


class SecurityConfigTests(unittest.TestCase):
    def _write_config(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "auth-key": "legacy-auth",
                    "ai_review": {"api_key": "legacy-review"},
                    "backup": {
                        "access_key_id": "legacy-access",
                        "secret_access_key": "legacy-secret",
                        "passphrase": "legacy-passphrase",
                    },
                    "image_reference_upload": {
                        "qiniu_access_key": "legacy-qiniu-access",
                        "qiniu_secret_key": "legacy-qiniu-secret",
                    },
                    "image_storage": {
                        "enabled": True,
                        "mode": "webdav",
                        "webdav_url": "https://user:password@example.test/storage",
                        "webdav_password": "legacy-webdav",
                    },
                    "openai_relay": {
                        "enabled": True,
                        "api_key": "legacy-relay",
                        "api_keys": ["pool-a", "pool-b"],
                    },
                    "proxy_runtime": {
                        "proxy_url": "http://user:password@example.test:8080",
                        "clearance": {
                            "cf_cookies": "legacy-cookie",
                            "cf_clearance": "legacy-clearance",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_public_config_redacts_all_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            self._write_config(path)
            with patch.dict(os.environ, {"CHATGPT2API_AUTH_KEY": "env-auth"}, clear=True):
                store = ConfigStore(path)
                public = store.get()

            self.assertNotIn("auth-key", public)
            self.assertEqual(public["ai_review"]["api_key"], "")
            self.assertTrue(public["ai_review"]["has_api_key"])
            self.assertEqual(public["backup"]["access_key_id"], "")
            self.assertEqual(public["backup"]["secret_access_key"], "")
            self.assertEqual(public["backup"]["passphrase"], "")
            self.assertTrue(public["backup"]["has_secret_access_key"])
            self.assertEqual(public["image_reference_upload"]["qiniu_access_key"], "")
            self.assertEqual(public["image_reference_upload"]["qiniu_secret_key"], "")
            self.assertEqual(public["image_storage"]["webdav_password"], "")
            self.assertNotIn("password", public["image_storage"]["webdav_url"])
            self.assertEqual(public["openai_relay"]["api_key"], "")
            self.assertEqual(public["openai_relay"]["api_keys"], [])
            self.assertTrue(public["openai_relay"]["has_api_key"])
            self.assertEqual(public["openai_relay"]["api_key_count"], 3)
            self.assertEqual(public["proxy_runtime"]["clearance"]["cf_cookies"], "")
            self.assertEqual(public["proxy_runtime"]["clearance"]["cf_clearance"], "")
            self.assertNotIn("password", public["proxy_runtime"]["proxy_url"])

    def test_environment_secrets_are_not_persisted_by_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text(
                json.dumps({"auth-key": "", "openai_relay": {}, "image_task_queue": {}}),
                encoding="utf-8",
            )
            environment = {
                "CHATGPT2API_AUTH_KEY": "env-auth",
                "CHATGPT2API_OPENAI_RELAY_API_KEY": "env-relay",
                "IMAGE_TASK_DATABASE_URL": "mysql+pymysql://user:password@db/test",
            }
            with patch.dict(os.environ, environment, clear=False):
                store = ConfigStore(path)
                self.assertEqual(store.get_openai_relay_settings()["api_key"], "env-relay")
                store.update({"image_retention_days": 45})

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["auth-key"], "")
            self.assertEqual(persisted["openai_relay"]["api_key"], "")
            self.assertEqual(persisted["image_task_queue"]["database_url"], "")

    def test_strict_mode_rejects_embedded_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            self._write_config(path)
            environment = {
                "CHATGPT2API_AUTH_KEY": "env-auth",
                "CHATGPT2API_STRICT_SECRET_SOURCES": "true",
            }
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(ValueError, "embedded secrets are not allowed"):
                    ConfigStore(path)

    def test_security_audit_reports_paths_without_values(self) -> None:
        findings = find_embedded_secret_paths(
            {
                "openai_relay": {"api_key": "do-not-print", "api_keys": ["do-not-print-either"]},
                "image_task_queue": {"redis_url": "redis://user:password@redis:6379/0"},
            }
        )
        output = " ".join(findings)
        self.assertIn("openai_relay.api_key", output)
        self.assertIn("openai_relay.api_keys", output)
        self.assertIn("image_task_queue.redis_url", output)
        self.assertNotIn("do-not-print", output)
        self.assertNotIn("password@", output)


if __name__ == "__main__":
    unittest.main()
