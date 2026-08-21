"""Deterministic inbound routing for agent workspaces."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional

from common.log import logger

from agent.registry import AgentRegistry


class AgentBindingError(ValueError):
    """Raised when a binding selector is malformed or ambiguous."""


class AgentUnavailableError(RuntimeError):
    """Raised when a binding resolves to a missing or disabled agent."""


@dataclass(frozen=True)
class AgentBinding:
    agent_id: str
    channel_type: str
    conversation_id: Optional[str] = None

    @property
    def selector(self):
        return self.channel_type, self.conversation_id


class AgentRouter:
    """Resolve explicit selections and channel bindings to enabled agents."""

    def __init__(self, registry: AgentRegistry, bindings: Iterable[AgentBinding] = ()):
        self.registry = registry
        self._exact = {}
        self._channel_defaults = {}
        for binding in bindings:
            target = (
                self._exact if binding.conversation_id is not None
                else self._channel_defaults
            )
            key = binding.selector if binding.conversation_id is not None else binding.channel_type
            if key in target:
                raise AgentBindingError(f"duplicate agent binding selector: {key!r}")
            target[key] = binding.agent_id

    @classmethod
    def from_config(cls, settings: Mapping, registry: AgentRegistry) -> "AgentRouter":
        raw_bindings = settings.get("agent_bindings") or []
        if not isinstance(raw_bindings, list):
            raise AgentBindingError("agent_bindings must be a list")
        bindings: List[AgentBinding] = []
        for index, raw in enumerate(raw_bindings):
            if not isinstance(raw, Mapping):
                raise AgentBindingError(f"agent_bindings[{index}] must be an object")
            agent_id = raw.get("agent_id")
            channel_type = raw.get("channel_type")
            conversation_id = raw.get("conversation_id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise AgentBindingError(
                    f"agent_bindings[{index}].agent_id must be a non-empty string"
                )
            if not isinstance(channel_type, str) or not channel_type.strip():
                raise AgentBindingError(
                    f"agent_bindings[{index}].channel_type must be a non-empty string"
                )
            if conversation_id is not None and (
                not isinstance(conversation_id, str) or not conversation_id.strip()
            ):
                raise AgentBindingError(
                    f"agent_bindings[{index}].conversation_id must be a non-empty string"
                )
            bindings.append(
                AgentBinding(
                    agent_id=agent_id.strip(),
                    channel_type=channel_type.strip().lower(),
                    conversation_id=(
                        conversation_id.strip() if conversation_id is not None else None
                    ),
                )
            )
        return cls(registry, bindings)

    def _require_enabled(self, agent_id: Optional[str], source: str) -> str:
        """A binding that names an unavailable agent is a configuration error.

        Serving it with the default agent instead would answer the user with a
        different persona, memory and workspace than the one they are bound to,
        and nothing in the conversation would reveal the substitution.
        """
        try:
            return self.registry.get(agent_id).id
        except Exception as exc:
            raise AgentUnavailableError(
                f"{source} selected agent {agent_id!r}, which is missing or disabled"
            ) from exc

    def resolve(
        self,
        channel_type: str = "",
        conversation_ids: Iterable[str] = (),
        explicit_agent_id: str = None,
    ) -> str:
        if explicit_agent_id:
            return self._require_enabled(explicit_agent_id, "explicit route")

        channel_type = (channel_type or "").strip().lower()
        for conversation_id in conversation_ids:
            if not conversation_id:
                continue
            target = self._exact.get((channel_type, str(conversation_id)))
            if target:
                return self._require_enabled(target, "conversation binding")

        target = self._channel_defaults.get(channel_type)
        if target:
            return self._require_enabled(target, "channel binding")
        return self.registry.default_agent_id

    def resolve_context(self, context) -> str:
        if context is None:
            return self.registry.default_agent_id
        agent_id = self.resolve(
            channel_type=context.get("channel_type", ""),
            conversation_ids=(
                context.get("session_id", ""),
                context.get("receiver", ""),
            ),
            explicit_agent_id=context.get("agent_id"),
        )
        context["agent_id"] = agent_id
        return agent_id


_router_instance: Optional[AgentRouter] = None
_router_signature: Optional[tuple] = None
_router_pinned: bool = False
_router_lock = threading.Lock()


def get_agent_router(registry: AgentRegistry = None) -> AgentRouter:
    """Router for the current configuration.

    Rebuilt when agent_bindings change rather than cached on first access:
    the console edits bindings at runtime, and a router built before
    load_config() saw no bindings at all.
    """

    global _router_instance, _router_signature
    from config import conf
    from agent.registry import get_agent_registry

    settings = conf()
    active_registry = registry or get_agent_registry()
    signature = (repr(settings.get("agent_bindings") or []), id(active_registry))
    with _router_lock:
        if _router_pinned and _router_instance is not None:
            return _router_instance
        if _router_instance is None or _router_signature != signature:
            _router_instance = AgentRouter.from_config(settings, active_registry)
            _router_signature = signature
        return _router_instance


def set_agent_router(router: Optional[AgentRouter]) -> None:
    """Pin a router, or pass None to go back to following configuration."""

    global _router_instance, _router_signature, _router_pinned
    with _router_lock:
        _router_instance = router
        _router_signature = None
        _router_pinned = router is not None
