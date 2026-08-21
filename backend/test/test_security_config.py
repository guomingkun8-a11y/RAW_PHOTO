import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.platform.config import ConfigStore
from services.platform.security_config import find_embedded_secret_paths
from services.platform.runtime_requirements import validate_enterprise_runtime


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
                        "oss_access_key": "legacy-oss-access",
                        "oss_secret_key": "legacy-oss-secret",
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
            with patch.dict(os.environ, {"GMKRAW_AUTH_KEY": "env-auth"}, clear=True):
                store = ConfigStore(path)
                public = store.get()

            self.assertNotIn("auth-key", public)
            self.assertEqual(public["ai_review"]["api_key"], "")
            self.assertTrue(public["ai_review"]["has_api_key"])
            self.assertEqual(public["backup"]["access_key_id"], "")
            self.assertEqual(public["backup"]["secret_access_key"], "")
            self.assertEqual(public["backup"]["passphrase"], "")
            self.assertTrue(public["backup"]["has_secret_access_key"])
            self.assertEqual(public["image_reference_upload"]["oss_access_key"], "")
            self.assertEqual(public["image_reference_upload"]["oss_secret_key"], "")
            self.assertTrue(public["image_reference_upload"]["has_oss_access_key"])
            self.assertTrue(public["image_reference_upload"]["has_oss_secret_key"])
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
                "GMKRAW_AUTH_KEY": "env-auth",
                "GMKRAW_OPENAI_RELAY_API_KEY": "env-relay",
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
                "GMKRAW_AUTH_KEY": "env-auth",
                "GMKRAW_STRICT_SECRET_SOURCES": "true",
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

    def test_enterprise_runtime_rejects_sqlite(self) -> None:
        environment = {
            "GMKRAW_ENTERPRISE_MODE": "true",
            "GMKRAW_REQUIRE_VECTOR_SEARCH": "false",
            "GMKRAW_DATABASE_URL": "sqlite:///enterprise.db",
            "STORAGE_BACKEND": "sqlite",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must not use SQLite"):
                validate_enterprise_runtime()

    def test_enterprise_runtime_accepts_mysql_vector_layout(self) -> None:
        environment = {
            "GMKRAW_ENTERPRISE_MODE": "true",
            "GMKRAW_REQUIRE_VECTOR_SEARCH": "true",
            "GMKRAW_DATABASE_URL": "mysql+pymysql://user:secret@mysql/raw_photo",
            "STORAGE_BACKEND": "mysql",
            "IMAGE_TASK_QUEUE_ENABLED": "true",
            "IMAGE_TASK_REDIS_URL": "redis://redis:6379/0",
            "AGENT_QUEUE_ENABLED": "true",
            "AGENT_REDIS_URL": "redis://redis:6379/0",
            "QDRANT_URL": "http://qdrant:6333",
            "QDRANT_API_KEY": "qdrant-test-key",
            "GMKRAW_EMBEDDING_BASE_URL": "http://embedding:8080/v1",
            "GMKRAW_EMBEDDING_MODEL": "text-embedding-3-small",
            "WEB_SECURITY_SSRF_PROTECTION": "true",
            "GMKRAW_IMAGE_REFERENCE_UPLOAD_ENABLED": "true",
            "GMKRAW_OSS_ENDPOINT": "https://oss.example.test",
            "GMKRAW_OSS_ACCESS_KEY_ID": "access",
            "GMKRAW_OSS_ACCESS_KEY_SECRET": "secret",
            "GMKRAW_OSS_BUCKET": "raw-photo",
            "GMKRAW_IMAGE_STORAGE_ENABLED": "true",
            "GMKRAW_IMAGE_STORAGE_MODE": "both",
            "GMKRAW_IMAGE_STORAGE_PROVIDER": "minio",
            "GMKRAW_MINIO_ENDPOINT": "https://s3.example.test",
            "GMKRAW_MINIO_ACCESS_KEY": "access",
            "GMKRAW_MINIO_SECRET_KEY": "secret",
            "GMKRAW_MINIO_BUCKET": "raw-photo",
        }
        with patch.dict(os.environ, environment, clear=True):
            result = validate_enterprise_runtime()

        self.assertTrue(result["enterpriseMode"])
        self.assertTrue(result["sqliteRejected"])

    def test_enterprise_runtime_requires_vector_auth_and_ssrf_protection(self) -> None:
        environment = {
            "GMKRAW_ENTERPRISE_MODE": "true",
            "GMKRAW_REQUIRE_VECTOR_SEARCH": "true",
            "GMKRAW_DATABASE_URL": "mysql+pymysql://user:secret@mysql/raw_photo",
            "STORAGE_BACKEND": "mysql",
            "IMAGE_TASK_QUEUE_ENABLED": "true",
            "IMAGE_TASK_REDIS_URL": "redis://redis:6379/0",
            "AGENT_QUEUE_ENABLED": "true",
            "AGENT_REDIS_URL": "redis://redis:6379/0",
            "QDRANT_URL": "http://qdrant:6333",
            "GMKRAW_EMBEDDING_BASE_URL": "http://embedding:8080/v1",
            "GMKRAW_EMBEDDING_MODEL": "text-embedding-3-small",
            "GMKRAW_IMAGE_REFERENCE_UPLOAD_ENABLED": "true",
            "GMKRAW_OSS_ENDPOINT": "https://oss.example.test",
            "GMKRAW_OSS_ACCESS_KEY_ID": "access",
            "GMKRAW_OSS_ACCESS_KEY_SECRET": "secret",
            "GMKRAW_OSS_BUCKET": "raw-photo",
            "GMKRAW_IMAGE_STORAGE_ENABLED": "true",
            "GMKRAW_IMAGE_STORAGE_MODE": "both",
            "GMKRAW_IMAGE_STORAGE_PROVIDER": "minio",
            "GMKRAW_MINIO_ENDPOINT": "https://s3.example.test",
            "GMKRAW_MINIO_ACCESS_KEY": "access",
            "GMKRAW_MINIO_SECRET_KEY": "secret",
            "GMKRAW_MINIO_BUCKET": "raw-photo",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "QDRANT_API_KEY.*WEB_SECURITY_SSRF_PROTECTION"):
                validate_enterprise_runtime()


if __name__ == "__main__":
    unittest.main()
