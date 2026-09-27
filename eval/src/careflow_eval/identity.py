"""Conservative, shared writer/reviewer model identity checks with no provider dependencies."""

from __future__ import annotations

import re

_FAMILIES = ("qwen", "claude", "gpt", "gemini", "llama", "mistral", "gemma", "deepseek")
_UNKNOWN = ("unknown", "unspecified", "default", "unidentified", "placeholder")


def model_family(identity: str) -> str:
    """Resolve a known model family, ignoring transport/tool and model-repository prefixes.

    ``agent:local/openai/gpt-6`` and ``GPT-6`` are both GPT. Only the final model
    component establishes the family, so a tool/provider name cannot masquerade as a
    model identity. Unknown aliases fail closed. This classifies an identified model;
    the calling review workflow must still verify its actual runtime receipt.
    """
    name = identity.strip().casefold()
    if not name or any(token in name for token in _UNKNOWN):
        raise ValueError(f"The model identity is not established: {identity}")
    model = name.rsplit("/", maxsplit=1)[-1]
    for family in _FAMILIES:
        if re.match(rf"^{family}(?:\d|[-_.:]|$)", model):
            return family
    raise ValueError(f"Cannot establish model-family independence for {identity}")


def require_distinct_models(writer: str, reviewer: str) -> None:
    """Require known, different model families for authoring and automated review."""
    if model_family(writer) == model_family(reviewer):
        raise ValueError("The review model must be from a different family than the writer")
