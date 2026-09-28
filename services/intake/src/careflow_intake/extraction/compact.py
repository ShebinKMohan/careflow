"""Smaller local-model prompt representation; canonical extraction validation is unchanged."""

from __future__ import annotations

from functools import cache
from typing import Any, Final

from careflow_contracts.generated.extraction_output_schema import ExtractionOutput
from careflow_intake.extraction.prompt import extractable_keys

VERSION_SUFFIX: Final = "_compact1"
OUTPUT_RULE: Final = (
    "Compact output: for each fact emit only key, value, turn_id and quote. "
    "For a denied fact also emit negated:true, as required above. "
    "Otherwise omit negated; its default is false. Never emit unit, char_start, char_end, "
    "null optional fields, or negated:false. Code computes source offsets. "
    "Keep all facts supported by the conversation; do not shorten or translate source quotes."
)


def _without_annotations(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_annotations(item)
            for key, item in value.items()
            if key not in {"description", "title"}
        }
    if isinstance(value, list):
        return [_without_annotations(item) for item in value]
    return value


@cache
def presentation_schema() -> dict[str, Any]:
    """Retain types, lengths, identifier patterns and canonical field names in the hint."""
    schema = ExtractionOutput.model_json_schema()
    definitions = schema["$defs"]
    properties = definitions["ExtractedFact"]["properties"]
    # Neither patient units nor model-supplied offsets are consumed by _entry().
    for name, definition in (
        ("unit", "Unit"),
        ("char_start", "CharStart"),
        ("char_end", "CharEnd"),
    ):
        properties.pop(name)
        definitions.pop(definition)
    properties["negated"] = {"type": "boolean", "const": True}
    definitions["FactKey"]["enum"] = [key.value for key in extractable_keys()]
    return _without_annotations(schema)
