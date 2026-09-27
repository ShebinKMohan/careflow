"""`careflow-triage laya quantize`: the int8 encoder the kiosk runs (M3-02, ADR-043).

The export (`packages/laya-ts/scripts/export_onnx.py`) writes the split fp32 ONNX to `onnx-fp32/`:
`encoder.onnx` (last_hidden_state from input_ids and attention_mask) and `head.onnx` (logits and
act_logits), after its torch-vs-ONNX 1e-4 check.

The encoder's weights go to int8, weight-only: every MatMul becomes ONNX Runtime's `MatMulNBits`
(8 bits, symmetric, blocks of 32), so activations stay fp32. The embedding table is stored as fp16
with a Cast after its lookup. Dynamic int8 (`quantize_dynamic`, int8 weights and per-tensor uint8
activations) was the plan; on the golden states it agreed with the fp32 model on 62% of argmaxes,
because ModernBERT's activations have outlier channels, and an int8 embedding table cost 4 points
more (ADR-043). The head stays fp32. Every file is written as one file (no `.onnx.data`), with the
tokenizer, the agent config and `question.json` (the question the model was trained on), to
`onnx-int8/`, as the kiosk and the Python ONNX model load them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Final

import numpy as np

from careflow_triage.laya_ktas.serialize import KTAS_QUESTION, STATE_VERSION

COPIED: Final = ("tokenizer.json", "rl_agent_config.json")
BITS: Final = 8
BLOCK_SIZE: Final = 32
MIN_EMBEDDING_VALUES: Final = 1_000_000  # only the vocabulary table, never a small Gather


def embeddings_to_fp16(model: Any) -> int:
    """Store each large fp32 embedding table as fp16, cast back to fp32 after the lookup; the
    number of tables changed."""
    from onnx import TensorProto, helper, numpy_helper  # type: ignore[import-not-found]

    graph = model.graph
    tables = {init.name: init for init in graph.initializer}
    changed, nodes = 0, []
    for node in graph.node:
        nodes.append(node)
        table = tables.get(node.input[0]) if node.op_type == "Gather" else None
        if table is None:
            continue
        values = numpy_helper.to_array(table)
        if values.dtype != np.float32 or values.size < MIN_EMBEDDING_VALUES:
            continue
        table.CopyFrom(numpy_helper.from_array(values.astype(np.float16), table.name))
        looked_up = node.output[0]
        node.output[0] = f"{looked_up}_fp16"
        nodes.append(
            helper.make_node(
                "Cast",
                [node.output[0]],
                [looked_up],
                to=TensorProto.FLOAT,
                name=f"{node.name}_to_fp32",
            )
        )
        changed += 1
    del graph.node[:]
    graph.node.extend(nodes)
    return changed


def quantize(run_dir: Path) -> Path:
    import onnx  # type: ignore[import-not-found]
    from onnxruntime.quantization.matmul_nbits_quantizer import (  # type: ignore[import-not-found]
        DefaultWeightOnlyQuantConfig,
        MatMulNBitsQuantizer,
    )

    source, target = run_dir / "onnx-fp32", run_dir / "onnx-int8"
    if not (source / "encoder.onnx").exists():
        raise FileNotFoundError(
            f"{source}/encoder.onnx is missing: run packages/laya-ts/scripts/export_onnx.py first"
        )
    target.mkdir(parents=True, exist_ok=True)
    encoder = onnx.load(str(source / "encoder.onnx"))  # with its external data
    config = DefaultWeightOnlyQuantConfig(
        block_size=BLOCK_SIZE, is_symmetric=True, bits=BITS, op_types_to_quantize=("MatMul",)
    )
    quantizer = MatMulNBitsQuantizer(
        encoder,
        bits=BITS,
        block_size=BLOCK_SIZE,
        is_symmetric=True,
        algo_config=config,
        op_types_to_quantize=("MatMul",),
    )
    quantizer.process()
    quantized: Any = quantizer.model.model  # type: ignore[attr-defined]  # ONNXModel wrapper
    if embeddings_to_fp16(quantized) != 1:
        raise ValueError("expected exactly one embedding table in the encoder")
    onnx.save_model(quantized, str(target / "encoder.onnx"), save_as_external_data=False)
    head = onnx.load(str(source / "head.onnx"))
    onnx.save_model(head, str(target / "head.onnx"), save_as_external_data=False)
    for name in COPIED:
        if (source / name).exists():
            shutil.copy2(source / name, target / name)
    # The tokenizer folder too, for the Python ONNX model (`load_tokenizer`); the kiosk reads the
    # root tokenizer.json.
    shutil.copytree(run_dir / "tokenizer", target / "tokenizer", dirs_exist_ok=True)
    # The question the model was trained on travels with it: the kiosk asks exactly this.
    body = {"input_contract": STATE_VERSION, "questions": KTAS_QUESTION}
    (target / "question.json").write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    return target


__all__ = ["BITS", "BLOCK_SIZE", "embeddings_to_fp16", "quantize"]
