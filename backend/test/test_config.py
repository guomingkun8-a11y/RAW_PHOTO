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

        from services.platform import config as config_module

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

    def test_read_json_object_accepts_utf8_bom(self) -> None:
        module = self.config_module
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text('\ufeff{"auth-key": "bom-auth"}', encoding="utf-8")

            data = module._read_json_object(path, name="config.json")

        self.assertEqual(data["auth-key"], "bom-auth")

    def test_legacy_auth_key_admin_switch_defaults_off(self) -> None:
        module = self.config_module
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            with mock.patch.dict(module.os.environ, {}, clear=True):
                store = module.ConfigStore(path)

        self.assertFalse(store.legacy_auth_key_admin_enabled)

    def test_legacy_auth_key_admin_switch_reads_env(self) -> None:
        module = self.config_module
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            with mock.patch.dict(module.os.environ, {"GMKRAW_LEGACY_AUTH_KEY_ADMIN_ENABLED": "true"}, clear=True):
                store = module.ConfigStore(path)
                self.assertTrue(store.legacy_auth_key_admin_enabled)

    def test_oss_reference_prefix_is_separate_from_task_asset_root(self) -> None:
        module = self.config_module
        with mock.patch.dict(
            module.os.environ,
            {
                "GMKRAW_OSS_REFERENCE_PREFIX": "raw-photo/reference",
                "GMKRAW_MINIO_ROOT_PATH": "raw-photo/task-assets",
            },
            clear=True,
        ):
            storage = module._normalize_image_storage_settings({"enabled": True, "mode": "minio"})
            reference = module._normalize_image_reference_upload_settings({})
            video = module._normalize_video_upload_settings({}, reference)

        self.assertEqual(storage["minio_root_path"], "raw-photo/task-assets")
        self.assertEqual(reference["oss_prefix"], "raw-photo/reference")
        self.assertEqual(video["oss_prefix"], "raw-photo/video-inputs")

    def test_video_upload_reuses_reference_oss_credentials(self) -> None:
        module = self.config_module
        reference = {
            "enabled": True,
            "oss_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
            "oss_access_key": "ak",
            "oss_secret_key": "sk",
            "oss_bucket": "raw-photo",
            "oss_region": "oss-cn-hangzhou",
            "oss_secure": True,
            "public_base_url": "https://cdn.example.test",
        }
        with mock.patch.dict(module.os.environ, {"GMKRAW_OSS_VIDEO_PREFIX": "raw-photo/videos"}, clear=True):
            video = module._normalize_video_upload_settings({}, reference)

        self.assertTrue(video["enabled"])
        self.assertEqual(video["provider"], "oss")
        self.assertEqual(video["oss_endpoint"], reference["oss_endpoint"])
        self.assertEqual(video["oss_access_key"], "ak")
        self.assertEqual(video["oss_secret_key"], "sk")
        self.assertEqual(video["oss_bucket"], "raw-photo")
        self.assertEqual(video["oss_prefix"], "raw-photo/videos")

    def test_video_analysis_reuses_image_queue_redis_and_reads_env(self) -> None:
        module = self.config_module
        image_queue = {"redis_url": "redis://image-redis:6379/0"}
        with mock.patch.dict(
            module.os.environ,
            {
                "VIDEO_ANALYSIS_MAX_FRAMES": "16",
                "VIDEO_ANALYSIS_VISION_MODEL": "gpt-5.6-sol",
                "VIDEO_PARSE_QUEUE_NAME": "video-jobs",
                "VIDEO_PARSE_WORKER_CONCURRENCY": "6",
            },
            clear=True,
        ):
            settings = module._normalize_video_analysis_settings({}, image_queue)

        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["redis_url"], "redis://image-redis:6379/0")
        self.assertEqual(settings["max_frames"], 16)
        self.assertEqual(settings["vision_model"], "gpt-5.6-sol")
        self.assertEqual(settings["queue_name"], "video-jobs")
        self.assertEqual(settings["worker_concurrency"], 6)
        self.assertFalse(settings["auto_enqueue_on_upload"])

    def test_video_analysis_auto_enqueue_upload_switch_reads_env(self) -> None:
        module = self.config_module
        with mock.patch.dict(
            module.os.environ,
            {"VIDEO_ANALYSIS_AUTO_ENQUEUE_ON_UPLOAD": "true"},
            clear=True,
        ):
            settings = module._normalize_video_analysis_settings({}, {})

        self.assertTrue(settings["auto_enqueue_on_upload"])

    def test_video_generation_settings_read_env_and_mask_public_secrets(self) -> None:
        module = self.config_module
        with mock.patch.dict(
            module.os.environ,
            {
                "GMKRAW_AUTH_KEY": "test-auth",
                "VIDEO_GENERATION_ENABLED": "true",
                "VIDEO_GENERATION_BASE_URL": "https://api.example.test/v1",
                "VIDEO_GENERATION_API_KEYS": "key-one,key-two",
                "VIDEO_GENERATION_QUEUE_NAME": "video-generation-jobs",
                "VIDEO_GENERATION_WORKER_CONCURRENCY": "3",
            },
            clear=True,
        ), tempfile.TemporaryDirectory() as tmp_dir:
            store = module.ConfigStore(Path(tmp_dir) / "config.json")
            settings = store.get_video_generation_settings()
            public = store.get_public_video_generation_settings()

        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["base_url"], "https://api.example.test/v1")
        self.assertEqual(settings["api_keys"], ["key-one", "key-two"])
        self.assertEqual(settings["queue_name"], "video-generation-jobs")
        self.assertEqual(settings["worker_concurrency"], 3)
        self.assertEqual(public["api_keys"], [])
        self.assertEqual(public["api_key_count"], 2)
        self.assertTrue(public["has_api_key"])

    def test_video_generation_can_reuse_openai_relay_credentials(self) -> None:
        module = self.config_module
        with mock.patch.dict(
            module.os.environ,
            {"GMKRAW_AUTH_KEY": "test-auth"},
            clear=True,
        ), tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "auth-key": "test-auth",
                        "video_generation": {"enabled": True},
                        "openai_relay": {
                            "enabled": True,
                            "base_url": "https://relay.example.test/v1",
                            "api_key": "relay-key",
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = module.ConfigStore(path)
            settings = store.get_video_generation_settings()
            public = store.get_public_video_generation_settings()

        self.assertEqual(settings["base_url"], "https://relay.example.test/v1")
        self.assertEqual(settings["api_key"], "relay-key")
        self.assertEqual(settings["credential_source"], "openai_relay")
        self.assertTrue(public["has_api_key"])

    def test_reference_upload_normalizes_legacy_provider_config_to_oss_only(self) -> None:
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
            "qiniu_prefix": "legacy-reference",
            "categories": "legacy",
            "compress": "1",
            "webp": "1",
        }

        with mock.patch.dict(module.os.environ, {}, clear=True):
            reference = module._normalize_image_reference_upload_settings(legacy)

        self.assertEqual(reference["provider"], "oss")
        self.assertEqual(reference["oss_access_key"], "")
        self.assertEqual(reference["oss_secret_key"], "")
        self.assertEqual(reference["oss_bucket"], "")
        self.assertEqual(reference["oss_prefix"], "raw-photo/reference")
        self.assertNotIn("qiniu_access_key", reference)
        self.assertNotIn("qiniu_secret_key", reference)
        self.assertNotIn("qiniu_bucket", reference)
        self.assertNotIn("qiniu_domain", reference)
        self.assertNotIn("qiniu_prefix", reference)
        self.assertNotIn("legacy_token", reference)
        self.assertNotIn("legacy_upload_url", reference)
        self.assertNotIn("categories", reference)
        self.assertNotIn("compress", reference)
        self.assertNotIn("webp", reference)

    def test_image_storage_ignores_removed_qiniu_provider(self) -> None:
        module = self.config_module
        with mock.patch.dict(module.os.environ, {}, clear=True):
            storage = module._normalize_image_storage_settings({
                "enabled": True,
                "mode": "qiniu",
                "provider": "qiniu",
                "qiniu_access_key": "ak",
                "qiniu_secret_key": "sk",
                "qiniu_bucket": "bucket",
                "qiniu_domain": "https://cdn.example.test",
            })

        self.assertEqual(storage["mode"], "local")
        self.assertEqual(storage["provider"], "webdav")
        self.assertNotIn("qiniu_access_key", storage)
        self.assertNotIn("qiniu_secret_key", storage)

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
