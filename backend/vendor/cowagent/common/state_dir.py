"""Single point of truth for where an Agent's state lives on disk.

Two rules keep later work cheap, and both matter:

1. This is the only module that reads ``agent_workspace`` from config.
2. This is the only module that knows the directory layout. Callers ask for
   ``memory_dir()``; they never write ``os.path.join(root, "memory")``. Without
   this second rule, inserting the per-user layer means editing every call site
   a second time.

Which paths follow the end user and which follow the Agent is decided here.
Until tenancy lands ``user_id`` is always None and every path resolves exactly
where it does today, so this module is a no-op for single-Agent installs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from common.runtime_identity import RuntimeIdentity, current_identity


class StateDirError(RuntimeError):
    """Raised when an identity names an Agent that does not exist."""


def _resolve(identity: Optional[RuntimeIdentity]) -> RuntimeIdentity:
    return identity if identity is not None else current_identity()


def state_root(identity: Optional[RuntimeIdentity] = None) -> Path:
    """Workspace root of the Agent this work belongs to.

    An absent ``agent_id`` resolves to the default Agent: startup tasks such as
    skill sync and scheduler boot legitimately run before routing. An
    ``agent_id`` that does not resolve is a bug and raises rather than quietly
    falling back, which would leak one Agent's files into another's workspace.
    """
    from agent.registry import get_agent_registry

    agent_id = _resolve(identity).agent_id
    registry = get_agent_registry()
    if not agent_id:
        return Path(registry.get(require_enabled=False).workspace)
    try:
        profile = registry.get(agent_id, require_enabled=False)
    except KeyError:
        raise StateDirError(f"unknown agent id: {agent_id!r}") from None
    return Path(profile.workspace)


def user_root(identity: Optional[RuntimeIdentity] = None) -> Path:
    """Root of the data owned by one end user of this Agent.

    Collapses onto the workspace root while ``user_id`` is unset, which is what
    makes the tenancy migration a change to this function rather than to every
    caller.
    """
    ident = _resolve(identity)
    root = state_root(ident)
    if ident.user_id:
        return root / "users" / ident.user_id
    return root


def state_path(*parts: str, identity: Optional[RuntimeIdentity] = None) -> Path:
    """Escape hatch for paths with no named helper. Prefer adding a helper."""
    return state_root(identity).joinpath(*parts)


def _ensure(path: Path, ensure: bool) -> Path:
    if ensure:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _agent_base(identity, base) -> Path:
    return Path(base) if base is not None else state_root(identity)


def _user_base(identity, base) -> Path:
    return Path(base) if base is not None else user_root(identity)


# ``base`` lets value objects that already carry a resolved root (MemoryConfig,
# KnowledgeService) reuse the layout without re-resolving an identity they do
# not have. It exists so the layout stays defined exactly once.


# --- Agent-scoped: shared by every user this Agent serves --------------------


def skills_dir(identity=None, ensure: bool = False, base=None) -> Path:
    return _ensure(_agent_base(identity, base) / "skills", ensure)


def knowledge_dir(identity=None, ensure: bool = False, base=None) -> Path:
    return _ensure(_agent_base(identity, base) / "knowledge", ensure)


def websites_dir(identity=None, ensure: bool = False, base=None) -> Path:
    return _ensure(_agent_base(identity, base) / "websites", ensure)


def subagents_dir(identity=None, ensure: bool = False, base=None) -> Path:
    """Sub agent templates. An Agent asset, like skills: sub agents have no
    identity of their own, they are a way this Agent gets work done."""
    return _ensure(_agent_base(identity, base) / "subagents", ensure)


def mcp_config_file(identity=None, base=None) -> Path:
    return _agent_base(identity, base) / "mcp.json"


def env_file(identity=None, base=None) -> Path:
    return _agent_base(identity, base) / ".env"


def scheduler_file(identity=None, base=None) -> Path:
    return _agent_base(identity, base) / "scheduler" / "tasks.json"


def tmp_dir(identity=None, ensure: bool = True, base=None) -> Path:
    """Transient downloads and synthesized media.

    Agent-scoped for now. It holds inbound attachments, so it arguably belongs
    to the user; revisit when tenancy lands rather than guessing now.
    """
    return _ensure(_agent_base(identity, base) / "tmp", ensure)


# --- User-scoped: isolated per end user once tenancy lands -------------------


def memory_dir(identity=None, ensure: bool = False, base=None) -> Path:
    return _ensure(_user_base(identity, base) / "memory", ensure)


def memory_index_db(identity=None, ensure: bool = True, base=None) -> Path:
    index_dir = _ensure(memory_dir(identity, base=base) / "long-term", ensure)
    return index_dir / "index.db"


def memory_file(identity=None, base=None) -> Path:
    return _user_base(identity, base) / "MEMORY.md"


def output_dir(identity=None, ensure: bool = False, base=None) -> Path:
    return _ensure(_user_base(identity, base) / "output", ensure)


def runs_dir(identity=None, ensure: bool = False, base=None) -> Path:
    """Task execution records. User-scoped because a trace holds full tool
    output: one user's runs must not be readable by another."""
    return _ensure(_user_base(identity, base) / "runs", ensure)


# --- Compatibility -----------------------------------------------------------


def state_root_str(identity: Optional[RuntimeIdentity] = None) -> str:
    """``state_root`` for the many call sites that still pass str paths around."""
    return str(state_root(identity))


def real_state_root(identity: Optional[RuntimeIdentity] = None) -> str:
    """Symlink-resolved root, for containment checks that compare prefixes."""
    return os.path.realpath(str(state_root(identity)))
