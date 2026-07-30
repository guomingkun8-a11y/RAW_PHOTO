import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[2]
ROOT_CONFIG_FILE = ROOT_DIR / "config.json"


class ConfigLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._created_root_config = False
        if not ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            cls._created_root_config = True

        from services import config as config_module

        cls.config_module = config_module

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_root_config and ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.unlink()

    def test_load_settings_ignores_directory_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            data_dir = base_dir / "data"
            config_dir = base_dir / "config.json"
            os_auth_key = "env-auth"

            config_dir.mkdir()

            module = self.config_module
            old_base_dir = module.BASE_DIR
            old_data_dir = module.DATA_DIR
            old_config_file = module.CONFIG_FILE
            old_env_auth_key = module.os.environ.get("GMKRAW_AUTH_KEY")
            try:
                module.BASE_DIR = base_dir
                module.DATA_DIR = data_dir
                module.CONFIG_FILE = config_dir
                module.os.environ["GMKRAW_AUTH_KEY"] = os_auth_key

                settings = module._load_settings()

                self.assertEqual(settings.auth_key, os_auth_key)
                self.assertEqual(settings.refresh_account_interval_minute, 5)
            finally:
                module.BASE_DIR = old_base_dir
                module.DATA_DIR = old_data_dir
                module.CONFIG_FILE = old_config_file
                if old_env_auth_key is None:
                    module.os.environ.pop("GMKRAW_AUTH_KEY", None)
                else:
                    module.os.environ["GMKRAW_AUTH_KEY"] = old_env_auth_key

    def test_qiniu_prefixes_are_separate_for_tasks_and_references(self) -> None:
        module = self.config_module
        with mock.patch.dict(
            module.os.environ,
            {
                "GMKRAW_QINIU_PREFIX": "gmkraw/reference",
                "GMKRAW_QINIU_TASK_PREFIX": "gmkraw/task-assets",
            },
            clear=False,
        ):
            storage = module._normalize_image_storage_settings({})
            reference = module._normalize_image_reference_upload_settings({})

        self.assertEqual(storage["qiniu_prefix"], "gmkraw/task-assets")
        self.assertEqual(reference["qiniu_prefix"], "gmkraw/reference")

    def test_reference_upload_normalizes_legacy_provider_config_to_qiniu_only(self) -> None:
        module = self.config_module
        legacy = {
            "enabled": True,
            "provider": "legacy-provider",
            "legacy_upload_url": "https://upload.example.test",
            "legacy_token": "legacy-token",
            "qiniu_access_key": "ak",
            "qiniu_secret_key": "sk",
            "qiniu_bucket": "bucket",
            "qiniu_domain": "https://cdn.example.test",
            "categories": "legacy",
            "compress": "1",
            "webp": "1",
        }

        with mock.patch.dict(module.os.environ, {}, clear=True):
            reference = module._normalize_image_reference_upload_settings(legacy)

        self.assertEqual(reference["provider"], "qiniu")
        self.assertEqual(reference["qiniu_access_key"], "ak")
        self.assertEqual(reference["qiniu_secret_key"], "sk")
        self.assertNotIn("legacy_token", reference)
        self.assertNotIn("legacy_upload_url", reference)
        self.assertNotIn("categories", reference)
        self.assertNotIn("compress", reference)
        self.assertNotIn("webp", reference)

    def test_openai_relay_accepts_environment_api_key_pool(self) -> None:
        module = self.config_module
        with mock.patch.dict(
            module.os.environ,
            {
                "GMKRAW_OPENAI_RELAY_API_KEY": "legacy-key",
                "GMKRAW_OPENAI_RELAY_API_KEYS": "pool-a\npool-b,pool-a",
                "GMKRAW_OPENAI_RELAY_POOL_DISTRIBUTED": "true",
            },
            clear=False,
        ):
            settings = module._normalize_openai_relay_settings({"base_url": "https://relay.example/v1"})

        self.assertEqual(settings["api_key"], "legacy-key")
        self.assertEqual(settings["api_keys"], ["pool-a", "pool-b"])
        self.assertTrue(settings["api_key_pool_distributed"])


if __name__ == "__main__":
    unittest.main()
