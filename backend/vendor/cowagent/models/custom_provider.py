# encoding:utf-8

"""
Centralized resolver for custom (OpenAI-compatible) provider credentials.

CowAgent historically supported only a *single* custom provider via the flat
config keys ``custom_api_key`` / ``custom_api_base``. This module adds support
for *multiple* custom providers (see issue #2838) while remaining 100%
backward compatible.

Config model
------------
- ``custom_providers``: list of dicts, each describing one custom provider::

      {
          "id": "3f2a9c1b",               # server-generated short uuid (primary key)
          "name": "my-provider",           # user-facing display label (not a key)
          "api_key": "sk-...",             # required
          "api_base": "https://...",       # required, must be OpenAI-compatible
          "model": "model-name"           # optional default model
      }

Routing
-------
- ``bot_type: "custom"`` (legacy): reads the flat ``custom_api_key`` / ``custom_api_base``.
- ``bot_type: "custom:<id>"`` (multi-provider): looks up the provider by id in
  ``custom_providers``.  There is a single source of truth — no separate
  ``custom_active_provider`` field.

Backward-compatibility contract
-------------------------------
When ``bot_type`` is exactly ``"custom"`` (no colon suffix), behaviour is
unchanged: we return ``custom_api_key`` / ``custom_api_base`` values.
"""

import uuid
from config import conf
from common.log import logger


def generate_provider_id() -> str:
    """Generate a short random id for a new custom provider."""
    return uuid.uuid4().hex[:8]


def get_custom_providers():
    """Return the list of configured custom providers (always a list)."""
    providers = conf().get("custom_providers")
    if not isinstance(providers, list):
        return []
    # Keep only well-formed entries with an id.
    return [p for p in providers if isinstance(p, dict) and p.get("id")]


def _find_provider_by_id(providers, provider_id):
    """Look up a provider by its id, or None if not found."""
    if not providers or not provider_id:
        return None
    for p in providers:
        if p.get("id") == provider_id:
            return p
    return None


def parse_custom_bot_type(bot_type):
    """Parse bot_type to extract custom provider id.

    Returns:
        (is_custom, provider_id) where:
        - is_custom: True if bot_type starts with "custom"
        - provider_id: the id suffix (e.g. "3f2a9c1b") or empty string for legacy mode
    """
    if not bot_type or not isinstance(bot_type, str):
        return False, ""
    if bot_type == "custom":
        return True, ""
    if bot_type.startswith("custom:"):
        return True, bot_type[7:]  # len("custom:") == 7
    return False, ""


def resolve_custom_credentials():
    """Resolve the effective (api_key, api_base, model) for custom mode.

    Resolution order:
      1. If ``bot_type`` is ``"custom:<id>"``, look up that id in
         ``custom_providers``.
      2. If ``bot_type`` is exactly ``"custom"`` (legacy), return the flat
         ``custom_api_key`` / ``custom_api_base``.

    :return: tuple ``(api_key, api_base, model)``. ``api_base`` and ``model``
             may be ``None`` / empty when not configured.
    """
    bot_type = conf().get("bot_type", "")
    is_custom, provider_id = parse_custom_bot_type(bot_type)

    if not is_custom:
        # Not custom at all — should not happen but be defensive.
        return (
            conf().get("open_ai_api_key", ""),
            conf().get("open_ai_api_base") or None,
            None,
        )

    if provider_id:
        # Multi-provider mode: look up by id.
        providers = get_custom_providers()
        provider = _find_provider_by_id(providers, provider_id)
        if provider is not None:
            return (
                provider.get("api_key", ""),
                provider.get("api_base") or None,
                provider.get("model") or None,
            )
        logger.warning(
            "[CUSTOM] provider id '%s' not found in custom_providers, "
            "falling back to legacy fields", provider_id
        )

    # Legacy single-provider fallback — unchanged behavior.
    return (
        conf().get("custom_api_key", ""),
        conf().get("custom_api_base") or None,
        None,
    )
