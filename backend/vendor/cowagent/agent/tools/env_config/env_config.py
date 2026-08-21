"""
Environment Configuration Tool - manage API keys and runtime skill config.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger
from common.utils import expand_path
from config import conf


API_KEY_REGISTRY = {
    "OPENAI_API_KEY": "OpenAI API key (GPT / embedding)",
    "OPENAI_API_BASE": "OpenAI API base URL",
    "GEMINI_API_KEY": "Google Gemini API key",
    "GEMINI_API_BASE": "Google Gemini API base URL",
    "CLAUDE_API_KEY": "Claude API key",
    "CLAUDE_API_BASE": "Claude API base URL",
    "LINKAI_API_KEY": "LinkAI API key",
    "LINKAI_API_BASE": "LinkAI API base URL",
    "ARK_API_KEY": "Volcengine Ark API key",
    "ARK_API_BASE": "Volcengine Ark API base URL",
    "DASHSCOPE_API_KEY": "DashScope API key",
    "DASHSCOPE_API_BASE": "DashScope API base URL",
    "MINIMAX_API_KEY": "MiniMax API key",
    "MINIMAX_API_BASE": "MiniMax API base URL",
    "BOCHA_API_KEY": "Bocha AI search API key",
    "SKILL_IMAGE_GENERATION_PROVIDER": "Current provider for image-generation",
    "SKILL_IMAGE_GENERATION_MODEL": "Current model for image-generation",
    "SKILL_IMAGE_GENERATION_PROXY": "Current proxy for image-generation",
    "CUSTOM_PROVIDERS": "Custom OpenAI-compatible providers from config.json",
}


class EnvConfig(BaseTool):
    """Tool for managing environment variables (API keys, etc.)."""

    name: str = "env_config"
    description: str = (
        "Manage API keys and runtime skill config securely. "
        "Use this tool when the user wants to configure API keys, "
        "view configured keys, or inspect image-generation runtime config. "
        "Actions: 'set' (add/update key), 'get' (view specific key), "
        "'list' (show configured keys), 'delete' (remove key). "
        "Values are masked for security. Changes take effect immediately via hot reload."
    )

    params: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action to perform: 'set', 'get', 'list', 'delete'",
                "enum": ["set", "get", "list", "delete"],
            },
            "key": {
                "type": "string",
                "description": (
                    "Environment variable key name. Common keys:\n"
                    "- OPENAI_API_KEY: OpenAI API (GPT models)\n"
                    "- OPENAI_API_BASE: OpenAI API base URL\n"
                    "- CLAUDE_API_KEY: Anthropic Claude API\n"
                    "- GEMINI_API_KEY: Google Gemini API\n"
                    "- LINKAI_API_KEY: LinkAI platform\n"
                    "- BOCHA_API_KEY: Bocha AI search\n"
                    "- SKILL_IMAGE_GENERATION_PROVIDER: active image skill provider\n"
                    "- SKILL_IMAGE_GENERATION_MODEL: active image skill model\n"
                    "Use exact key names (case-sensitive, uppercase with underscores)"
                ),
            },
            "value": {
                "type": "string",
                "description": "Value to set for the environment variable (for 'set' action)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.env_dir = expand_path("~/.cow")
        self.env_path = os.path.join(self.env_dir, ".env")
        self.agent_bridge = self.config.get("agent_bridge")

    def _ensure_env_file(self):
        os.makedirs(self.env_dir, exist_ok=True)
        if not os.path.exists(self.env_path):
            Path(self.env_path).touch()
            logger.info(f"[EnvConfig] Created .env file at {self.env_path}")

    def _mask_value(self, value: str) -> str:
        if not value or len(value) <= 10:
            return "***"
        return f"{value[:6]}***{value[-4:]}"

    def _display_value(self, key: str, value: Any) -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""
        upper = key.upper()
        if upper.endswith("_API_KEY") or "SECRET" in upper or "TOKEN" in upper:
            return self._mask_value(text)
        return text

    def _runtime_config(self) -> Dict[str, Any]:
        active = conf()
        return active if isinstance(active, dict) else {}

    @staticmethod
    def _get_nested(data: Dict[str, Any], path: tuple[str, ...]) -> Any:
        current = data
        for part in path:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
        return current

    def _read_env_file(self) -> Dict[str, str]:
        env_vars: Dict[str, str] = {}
        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r"^([^=]+)=(.*)$", line)
                    if match:
                        key, value = match.groups()
                        env_vars[key.strip()] = value.strip()
        return env_vars

    def _write_env_file(self, env_vars: Dict[str, str]):
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("# Environment variables for agent skills\n")
            f.write("# Auto-managed by env_config tool\n\n")
            for key, value in sorted(env_vars.items()):
                f.write(f"{key}={value}\n")

    def _reload_env(self):
        env_vars = self._read_env_file()
        for key, value in env_vars.items():
            os.environ[key] = value
        logger.debug(f"[EnvConfig] Reloaded {len(env_vars)} environment variables")

    def _refresh_skills(self):
        if self.agent_bridge:
            try:
                self._reload_env()
                refreshed = self.agent_bridge.refresh_all_skills()
                logger.info(f"[EnvConfig] Refreshed skills in {refreshed} agent instance(s)")
                return True
            except Exception as e:
                logger.warning(f"[EnvConfig] Failed to refresh skills: {e}")
                return False
        return False

    def _find_provider(self, providers, provider_id: str):
        for provider in providers:
            if isinstance(provider, dict) and str(provider.get("id") or "") == provider_id:
                return provider
        return None

    def _selected_custom_provider(self) -> tuple[Optional[dict], Optional[str]]:
        """Resolve the custom provider used by the current runtime."""
        active = self._runtime_config()
        providers = active.get("custom_providers")
        providers = providers if isinstance(providers, list) else []

        # Prefer the image-generation skill provider because this is the path
        # that usually drives the "missing image API" confusion.
        skills = active.get("skills")
        skill_cfg = skills.get("image-generation") if isinstance(skills, dict) else None
        provider_id = ""
        if isinstance(skill_cfg, dict):
            provider_id = str(skill_cfg.get("provider") or "").strip()
            if provider_id.startswith("custom:"):
                provider = self._find_provider(providers, provider_id.split(":", 1)[1])
                if provider:
                    return provider, "config.json -> skills.image-generation.provider"
            if provider_id == "custom":
                legacy_key = str(active.get("custom_api_key") or "").strip()
                legacy_base = str(active.get("custom_api_base") or "").strip()
                if legacy_key and legacy_base:
                    return {
                        "id": "custom",
                        "name": "custom",
                        "api_key": legacy_key,
                        "api_base": legacy_base,
                        "model": "",
                    }, "config.json -> skills.image-generation.provider"

        bot_type = str(active.get("bot_type") or "").strip()
        if bot_type.startswith("custom:"):
            provider = self._find_provider(providers, bot_type.split(":", 1)[1])
            if provider:
                return provider, "config.json -> bot_type"
        if bot_type == "custom":
            legacy_key = str(active.get("custom_api_key") or "").strip()
            legacy_base = str(active.get("custom_api_base") or "").strip()
            if legacy_key and legacy_base:
                return {
                    "id": "custom",
                    "name": "custom",
                    "api_key": legacy_key,
                    "api_base": legacy_base,
                    "model": "",
                }, "config.json -> bot_type"

        if len(providers) == 1:
            provider = providers[0]
            if (
                isinstance(provider, dict)
                and str(provider.get("api_key") or "").strip()
                and str(provider.get("api_base") or "").strip()
            ):
                return provider, "config.json -> custom_providers[0]"

        return None, None

    def _custom_provider_summary(self) -> list[dict]:
        active = self._runtime_config()
        providers = active.get("custom_providers")
        providers = providers if isinstance(providers, list) else []

        skills = active.get("skills")
        skill_cfg = skills.get("image-generation") if isinstance(skills, dict) else {}
        selected = ""
        if isinstance(skill_cfg, dict):
            provider_id = str(skill_cfg.get("provider") or "").strip()
            if provider_id.startswith("custom:"):
                selected = provider_id.split(":", 1)[1]
            elif provider_id == "custom":
                selected = "custom"

        summary = []
        for provider in providers:
            if not isinstance(provider, dict) or not provider.get("id"):
                continue
            pid = str(provider.get("id"))
            summary.append(
                {
                    "id": pid,
                    "name": provider.get("name") or pid,
                    "api_key": self._display_value("OPENAI_API_KEY", provider.get("api_key") or ""),
                    "api_base": provider.get("api_base") or "",
                    "model": provider.get("model") or "",
                    "selected_for_image_generation": pid == selected,
                }
            )
        return summary

    def _runtime_entries(self) -> Dict[str, Dict[str, Any]]:
        active = self._runtime_config()
        entries: Dict[str, Dict[str, Any]] = {}

        for key, path in (
            ("OPENAI_API_KEY", ("open_ai_api_key",)),
            ("OPENAI_API_BASE", ("open_ai_api_base",)),
            ("GEMINI_API_KEY", ("gemini_api_key",)),
            ("GEMINI_API_BASE", ("gemini_api_base",)),
            ("CLAUDE_API_KEY", ("claude_api_key",)),
            ("CLAUDE_API_BASE", ("claude_api_base",)),
            ("LINKAI_API_KEY", ("linkai_api_key",)),
            ("LINKAI_API_BASE", ("linkai_api_base",)),
            ("ARK_API_KEY", ("ark_api_key",)),
            ("ARK_API_BASE", ("ark_api_base",)),
            ("DASHSCOPE_API_KEY", ("dashscope_api_key",)),
            ("DASHSCOPE_API_BASE", ("dashscope_api_base",)),
            ("MINIMAX_API_KEY", ("minimax_api_key",)),
            ("MINIMAX_API_BASE", ("minimax_api_base",)),
            ("SKILL_IMAGE_GENERATION_PROVIDER", ("skills", "image-generation", "provider")),
            ("SKILL_IMAGE_GENERATION_MODEL", ("skills", "image-generation", "model")),
            ("SKILL_IMAGE_GENERATION_PROXY", ("skills", "image-generation", "proxy")),
        ):
            value = self._get_nested(active, path)
            if value in (None, ""):
                continue
            entries[key] = {
                "value": self._display_value(key, value),
                "description": API_KEY_REGISTRY.get(key, "未知用途的环境变量"),
                "source": "config.json",
            }

        provider, provider_source = self._selected_custom_provider()
        if provider:
            api_key = str(provider.get("api_key") or "").strip()
            api_base = str(provider.get("api_base") or "").strip()
            provider_name = str(provider.get("name") or provider.get("id") or "custom")
            entries.setdefault(
                "OPENAI_API_KEY",
                {
                    "value": self._display_value("OPENAI_API_KEY", api_key),
                    "description": API_KEY_REGISTRY["OPENAI_API_KEY"],
                    "source": provider_source or "config.json",
                },
            )
            entries.setdefault(
                "OPENAI_API_BASE",
                {
                    "value": self._display_value("OPENAI_API_BASE", api_base),
                    "description": API_KEY_REGISTRY["OPENAI_API_BASE"],
                    "source": provider_source or "config.json",
                },
            )
            entries.setdefault(
                "CUSTOM_PROVIDERS",
                {
                    "value": (
                        f"{provider_name} (id={provider.get('id')}, "
                        f"api_base={api_base}, model={provider.get('model') or ''})"
                    ),
                    "description": API_KEY_REGISTRY["CUSTOM_PROVIDERS"],
                    "source": "config.json -> custom_providers",
                },
            )

        return entries

    def _effective_entries(self) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}

        for key, value in self._read_env_file().items():
            entries[key] = {
                "value": self._display_value(key, value),
                "description": API_KEY_REGISTRY.get(key, "未知用途的环境变量"),
                "source": ".env",
            }

        for key in sorted(API_KEY_REGISTRY):
            if key in entries:
                continue
            value = os.getenv(key)
            if not value:
                continue
            entries[key] = {
                "value": self._display_value(key, value),
                "description": API_KEY_REGISTRY.get(key, "未知用途的环境变量"),
                "source": "process env",
            }

        runtime_entries = self._runtime_entries()
        for key, info in runtime_entries.items():
            entries.setdefault(key, info)

        return entries

    def _resolve_effective_value(self, key: str) -> tuple[Optional[str], Optional[str]]:
        env_vars = self._read_env_file()
        if key in env_vars and env_vars[key]:
            return env_vars[key], ".env"

        value = os.getenv(key)
        if value:
            return value, "process env"

        runtime_entries = self._runtime_entries()
        info = runtime_entries.get(key)
        if info and info.get("value"):
            runtime_value = info["value"]
            if key.upper().endswith("_API_KEY") or "SECRET" in key.upper() or "TOKEN" in key.upper():
                raw_value = ""
                provider, _ = self._selected_custom_provider()
                if key in {"OPENAI_API_KEY"} and provider:
                    raw_value = str(provider.get("api_key") or "").strip()
                elif key in {"OPENAI_API_BASE"} and provider:
                    raw_value = str(provider.get("api_base") or "").strip()
                if raw_value:
                    return raw_value, info.get("source")
            return str(runtime_value), info.get("source")

        return None, None

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        self._ensure_env_file()

        action = args.get("action")
        key = args.get("key")
        value = args.get("value")

        try:
            if action == "set":
                if not key or not value:
                    return ToolResult.fail("Error: 'key' and 'value' are required for 'set' action.")

                env_vars = self._read_env_file()
                env_vars[key] = value
                self._write_env_file(env_vars)
                os.environ[key] = value
                logger.info(f"[EnvConfig] Set {key}={self._mask_value(value)}")

                refreshed = self._refresh_skills()
                result = {
                    "message": f"Successfully set {key}",
                    "key": key,
                    "value": self._display_value(key, value),
                }
                result["note"] = (
                    "Skills refreshed automatically - changes are now active"
                    if refreshed
                    else "Skills not refreshed - restart agent to load new skills"
                )
                return ToolResult.success(result)

            if action == "get":
                if not key:
                    return ToolResult.fail("Error: 'key' is required for 'get' action.")

                value, source = self._resolve_effective_value(key)
                description = API_KEY_REGISTRY.get(key, "未知用途的环境变量")
                if value is not None:
                    logger.info(f"[EnvConfig] Got {key}={self._mask_value(value)}")
                    return ToolResult.success(
                        {
                            "key": key,
                            "value": self._display_value(key, value),
                            "description": description,
                            "source": source or "runtime",
                            "exists": True,
                            "note": f"Value is masked for security. In bash, use ${key} directly.",
                        }
                    )
                return ToolResult.success(
                    {
                        "key": key,
                        "description": description,
                        "exists": False,
                        "message": f"Environment variable '{key}' is not set",
                    }
                )

            if action == "list":
                effective = self._effective_entries()
                logger.info(f"[EnvConfig] Listed {len(effective)} environment variables")
                result = {
                    "message": f"Found {len(effective)} environment variable(s)",
                    "variables": effective,
                    "runtime": {
                        "custom_providers": self._custom_provider_summary(),
                        "image_generation": self._get_nested(
                            self._runtime_config(), ("skills", "image-generation")
                        )
                        or {},
                    },
                }
                if effective:
                    result["note"] = (
                        "Some values come from config.json runtime settings, including "
                        "custom_providers and skills.image-generation."
                    )
                return ToolResult.success(result)

            if action == "delete":
                if not key:
                    return ToolResult.fail("Error: 'key' is required for 'delete' action.")

                env_vars = self._read_env_file()
                if key not in env_vars:
                    return ToolResult.success(
                        {
                            "message": f"Environment variable '{key}' was not set",
                            "key": key,
                        }
                    )

                del env_vars[key]
                self._write_env_file(env_vars)
                if key in os.environ:
                    del os.environ[key]
                logger.info(f"[EnvConfig] Deleted {key}")

                refreshed = self._refresh_skills()
                result = {
                    "message": f"Successfully deleted {key}",
                    "key": key,
                }
                result["note"] = (
                    "Skills refreshed automatically - changes are now active"
                    if refreshed
                    else "Skills not refreshed - restart agent to apply changes"
                )
                return ToolResult.success(result)

            return ToolResult.fail(f"Error: Unknown action '{action}'. Use 'set', 'get', 'list', or 'delete'.")

        except Exception as e:
            logger.error(f"[EnvConfig] Error: {e}", exc_info=True)
            return ToolResult.fail(f"EnvConfig tool error: {str(e)}")
