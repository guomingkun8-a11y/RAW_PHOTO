from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from dotenv import dotenv_values


ROOT_DIR = Path(__file__).resolve().parents[2]

SECRET_MAPPINGS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("auth-key",), "GMKRAW_AUTH_KEY"),
    (("ai_review", "api_key"), "GMKRAW_AI_REVIEW_API_KEY"),
    (("backup", "access_key_id"), "GMKRAW_BACKUP_ACCESS_KEY_ID"),
    (("backup", "secret_access_key"), "GMKRAW_BACKUP_SECRET_ACCESS_KEY"),
    (("backup", "passphrase"), "GMKRAW_BACKUP_PASSPHRASE"),
    (("image_reference_upload", "qiniu_access_key"), "GMKRAW_QINIU_ACCESS_KEY"),
    (("image_reference_upload", "qiniu_secret_key"), "GMKRAW_QINIU_SECRET_KEY"),
    (("image_storage", "webdav_password"), "GMKRAW_WEBDAV_PASSWORD"),
    (("image_storage", "minio_access_key"), "GMKRAW_MINIO_ACCESS_KEY"),
    (("image_storage", "minio_secret_key"), "GMKRAW_MINIO_SECRET_KEY"),
    (("openai_relay", "api_key"), "GMKRAW_OPENAI_RELAY_API_KEY"),
    (("openai_relay", "api_keys"), "GMKRAW_OPENAI_RELAY_API_KEYS"),
    (("proxy_runtime", "clearance", "cf_cookies"), "GMKRAW_CF_COOKIES"),
    (("proxy_runtime", "clearance", "cf_clearance"), "GMKRAW_CF_CLEARANCE"),
)

URL_MAPPINGS: tuple[tuple[tuple[str, ...], str, object], ...] = (
    (("proxy",), "GMKRAW_PROXY_URL", ""),
    (("proxy_runtime", "proxy_url"), "GMKRAW_PROXY_URL", ""),
    (("proxy_runtime", "resource_proxy_url"), "GMKRAW_RESOURCE_PROXY_URL", ""),
    (("image_storage", "webdav_url"), "GMKRAW_WEBDAV_URL", ""),
    (("image_task_queue", "redis_url"), "IMAGE_TASK_REDIS_URL", "redis://127.0.0.1:6379/0"),
    (("image_task_queue", "database_url"), "IMAGE_TASK_DATABASE_URL", ""),
)


def _lookup(data: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = data
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _set(data: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current = data
    for part in path[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            return
        current = nested
    current[path[-1]] = value


def _url_has_password(value: object) -> bool:
    text = str(value or "").strip()
    if not text or "://" not in text:
        return False
    try:
        return bool(urlsplit(text).password)
    except ValueError:
        return False


def _quote_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _stringify_secret_value(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Move legacy JSON secrets into a local dotenv file.")
    parser.add_argument("--config", type=Path, default=ROOT_DIR / "config.json")
    parser.add_argument("--env-file", type=Path, default=ROOT_DIR / ".env.local")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("configuration root must be a JSON object")

    existing = {key: str(value or "") for key, value in dotenv_values(args.env_file).items()}
    migrated: dict[str, str] = {}

    for path, env_name in SECRET_MAPPINGS:
        value = _stringify_secret_value(_lookup(data, path))
        if not value:
            continue
        migrated[env_name] = existing.get(env_name) or value
        _set(data, path, [] if path[-1] == "api_keys" else "")

    for path, env_name, replacement in URL_MAPPINGS:
        value = str(_lookup(data, path) or "").strip()
        if not _url_has_password(value):
            continue
        migrated[env_name] = existing.get(env_name) or value
        _set(data, path, replacement)

    if not migrated:
        print("secret migration skipped: no embedded secrets found")
        return

    print(f"secret migration prepared for {len(migrated)} environment variable(s):")
    for env_name in sorted(migrated):
        print(f"- {env_name}")

    if args.dry_run:
        return

    merged = dict(existing)
    merged.update(migrated)
    env_text = "# Local secrets. Do not commit this file.\n" + "\n".join(
        f"{key}={_quote_env(value)}" for key, value in sorted(merged.items()) if value
    ) + "\n"
    _atomic_write(args.env_file, env_text)
    _atomic_write(args.config, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"migrated secrets to {args.env_file.name} and sanitized {args.config.name}")


if __name__ == "__main__":
    main()
