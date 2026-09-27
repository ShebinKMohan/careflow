"""Identity checks are shared by review execution and reviewed-dataset admission."""

import pytest

from careflow_eval.identity import model_family, require_distinct_models


@pytest.mark.parametrize(
    "identity,family",
    [
        ("GPT-6", "gpt"),
        ("agent:local/openai/gpt-6", "gpt"),
        ("mlx-community/Qwen3.5-9B-MLX-4bit", "qwen"),
        ("agent:local/meta-llama/Llama-3.3-70B", "llama"),
        ("anthropic/claude-sonnet-4-5", "claude"),
    ],
)
def test_family_uses_model_component(identity: str, family: str) -> None:
    assert model_family(identity) == family


@pytest.mark.parametrize(
    "identity",
    [
        "",
        "unknown",
        "agent:local/default",
        "agent:gpt/unknown",
        "gpt-provider/some-alias",
        "qwen-default",
        "agent:local/mystery-model",
        "not-gpt-6",
    ],
)
def test_unknown_or_misleading_identities_reject(identity: str) -> None:
    with pytest.raises(ValueError):
        model_family(identity)


def test_independence_requires_two_known_different_families() -> None:
    require_distinct_models("GPT-6", "mlx-community/Qwen3.5-9B-MLX-4bit")
    with pytest.raises(ValueError, match="different family"):
        require_distinct_models("GPT-6", "agent:local/openai/gpt-5.6")
    with pytest.raises(ValueError):
        require_distinct_models("unknown", "qwen3")
    with pytest.raises(ValueError):
        require_distinct_models("gpt6", "unknown")
