# encoding:utf-8
"""
On-demand MCP tool retrieval.

Pure, stateless selection helpers used by the streaming executor to decide
which MCP tools to inject into a given LLM turn. Vector precompute + caching
live in ToolManager (the tool-lifecycle owner, a process-wide singleton);
only the context-aware selection lives here, because only the executor knows
the conversation context.

Invariants (per maintainer review of the feature proposal):
  * Built-in tools are never handled here — the caller injects them in full.
  * Any failure / missing input returns None so the caller falls back to
    full injection; tools must never be silently dropped.
  * Selection is union-accumulated across turns by the caller (only-grows),
    so a tool that already produced a tool_use in the message history can
    never disappear from the schema mid-run (which would make Claude/MiniMax
    raise a message-format error).
"""
import math
from typing import Dict, List, Optional, Sequence, Set

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# How many trailing messages to concatenate into the retrieval query. Tool
# needs drift across a multi-turn tool-call loop, so a single (initial) user
# query is not enough; a short recent window captures the drift without
# bloating the query with stale context.
DEFAULT_QUERY_MESSAGES = 5


def build_retrieval_query(messages: list, max_messages: int = DEFAULT_QUERY_MESSAGES) -> str:
    """Concatenate the text of the most recent messages into a retrieval query.

    Only ``text`` content blocks are kept; ``tool_use`` / ``tool_result`` blocks
    are skipped so the query stays short and focused on natural-language intent
    rather than large serialized tool payloads.

    Args:
        messages: Claude-style message list, each ``{"role", "content"}`` where
            content is either a string or a list of typed blocks.
        max_messages: Size of the trailing window to consider.

    Returns:
        A single string (possibly empty if no text is found).
    """
    if not messages:
        return ""

    parts: List[str] = []
    for message in messages[-max_messages:]:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            if content.strip():
                parts.append(content.strip())
            continue
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
    return "\n".join(parts)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def select_mcp_tools(
    query_vector: Optional[Sequence[float]],
    tool_vectors: Dict[str, Sequence[float]],
    top_k: int,
    already_selected: Optional[Set[str]] = None,
) -> Optional[Set[str]]:
    """Return the accumulated set of MCP tool names to inject this turn.

    Computes cosine similarity between ``query_vector`` and each candidate
    tool vector, keeps the ``top_k`` best, and unions them with
    ``already_selected`` so the injected set only ever grows within a run.

    Args:
        query_vector: Embedding of the current retrieval query, or None.
        tool_vectors: ``{mcp_tool_name: vector}`` for candidate MCP tools.
        top_k: Max number of tools to add from this turn's ranking.
        already_selected: Names accumulated in previous turns of this run.

    Returns:
        The union set of tool names to inject, or None to signal
        "fall back to full injection" (no query vector, empty/invalid index,
        or any unexpected error). This function never raises.
    """
    accumulated: Set[str] = set(already_selected) if already_selected else set()

    if not query_vector or not tool_vectors or top_k <= 0:
        return None

    try:
        expected_dim = len(query_vector)
        # Only rank candidates whose vector dimensionality matches the query.
        # A dimension mismatch means the index was built with a different
        # embedding model; ranking across dims is meaningless.
        candidates = {
            name: vec
            for name, vec in tool_vectors.items()
            if vec and len(vec) == expected_dim
        }
        if not candidates:
            return None

        ranked = _rank_by_similarity(query_vector, candidates)
        for name, _score in ranked[:top_k]:
            accumulated.add(name)
        return accumulated
    except Exception:
        # Selection must never break the agent — fall back to full injection.
        return None


def _rank_by_similarity(
    query_vector: Sequence[float],
    candidates: Dict[str, Sequence[float]],
) -> List[tuple]:
    """Return ``[(name, score), ...]`` sorted by descending cosine similarity.

    Uses numpy when available (vectorized, matching the memory-search path),
    with a pure-Python fallback so the feature works without numpy installed.
    """
    names = list(candidates.keys())

    if _HAS_NUMPY:
        matrix = np.array([candidates[n] for n in names], dtype=np.float32)  # (N, D)
        q_vec = np.array(query_vector, dtype=np.float32)                     # (D,)
        dots = matrix @ q_vec                                                # (N,)
        row_norms = np.linalg.norm(matrix, axis=1)                          # (N,)
        q_norm = float(np.linalg.norm(q_vec))
        denominators = row_norms * q_norm
        np.maximum(denominators, 1e-10, out=denominators)                   # avoid div-by-zero
        sims = dots / denominators
        order = np.argsort(sims)[::-1]
        return [(names[i], float(sims[i])) for i in order]

    scored = [(n, cosine_similarity(query_vector, candidates[n])) for n in names]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
