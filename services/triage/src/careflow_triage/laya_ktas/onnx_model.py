"""`LayaOnnxUrgencyModel`: the shipped ONNX files behind the M1-05 `UrgencyModel` port (M3-02).

It runs exactly what the kiosk runs (`onnx-int8/`, or `onnx-fp32/`) with ONNX Runtime on the CPU:
the sequence is built with `laya.common.build_sequence`, the encoder maps `input_ids` and
`attention_mask` to `last_hidden_state`, and the head maps `hidden_states`, `marker_pos`,
`marker_mask`, `qtype` and `attention_mask` to `logits` and `act_logits` (the export script's
names). The config temperature (clamped to [0.5, 5], as `laya.Agent` does) is applied before the
softmax over the five level markers. `OnnxAgent.predict_batch` answers like `laya.Agent`'s, so the
M3-01 leave-one-fact-out reasons run unchanged; M5-02 gates new versions on this model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
from scipy.special import softmax

from careflow_triage.laya_ktas.dataset import internal_question, load_tokenizer
from careflow_triage.laya_ktas.model import LayaUrgencyModel
from careflow_triage.laya_ktas.serialize import LEVEL_KEYS

TEMP_MIN: Final = 0.5
TEMP_MAX: Final = 5.0
CHOICE: Final = 0


class OnnxAgent:
    """The split encoder and head, answering `KTAS_QUESTION` like `laya.Agent.predict_batch`."""

    def __init__(self, bundle: Path, *, threads: int = 4) -> None:
        import onnxruntime as ort  # type: ignore[import-not-found]

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        providers = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(
            str(bundle / "encoder.onnx"), options, providers=providers
        )
        self.head = ort.InferenceSession(str(bundle / "head.onnx"), options, providers=providers)
        self.cfg: dict[str, Any] = json.loads((bundle / "rl_agent_config.json").read_text("utf-8"))
        self.tok = load_tokenizer(bundle)
        self.temperature = min(TEMP_MAX, max(TEMP_MIN, float(self.cfg["temperature"][0])))

    def logits(self, states: list[dict[str, str]]) -> np.ndarray:
        """Raw (temperature 1) logits of the five level options, one row per state."""
        from laya.common import build_sequence  # type: ignore[import-not-found]

        question = internal_question()
        encoded = [
            build_sequence(self.tok, state, question, self.cfg["max_len"], self.cfg["head_max_len"])
            for state in states
        ]
        n, length = len(encoded), max(len(ids) for ids, _ in encoded)
        ids = np.full((n, length), self.tok.pad_token_id, dtype=np.int64)
        att = np.zeros((n, length), dtype=np.int64)
        pos = np.zeros((n, len(LEVEL_KEYS)), dtype=np.int64)
        mask = np.zeros((n, len(LEVEL_KEYS)), dtype=bool)
        for i, (row, markers) in enumerate(encoded):
            ids[i, : len(row)] = row
            att[i, : len(row)] = 1
            pos[i, : len(markers)] = markers
            mask[i, : len(markers)] = True
        (hidden,) = self.encoder.run(None, {"input_ids": ids, "attention_mask": att})
        logits, _ = self.head.run(
            None,
            {
                "hidden_states": np.asarray(hidden, dtype=np.float32),
                "marker_pos": pos,
                "marker_mask": mask,
                "qtype": np.full((n, 1), CHOICE, dtype=np.int64),
                "attention_mask": att,
            },
        )
        return np.asarray(logits, dtype=np.float64)[:, : len(LEVEL_KEYS)]

    def predict_batch(
        self, states: list[dict[str, str]], questions: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if list(questions) != ["urgency"]:
            raise ValueError("the ONNX model answers the KTAS urgency question only")
        probs = softmax(self.logits(states) / self.temperature, axis=1)
        results = []
        for row in probs:
            best = int(row.argmax())
            results.append(
                {
                    "answers": {
                        "urgency": {
                            "choice": LEVEL_KEYS[best],
                            "probabilities": {
                                key: float(p) for key, p in zip(LEVEL_KEYS, row, strict=True)
                            },
                        }
                    }
                }
            )
        return results


@dataclass
class LayaOnnxUrgencyModel(LayaUrgencyModel):
    """The `UrgencyModel` port over the shipped ONNX files (int8 encoder, fp32 head)."""

    @classmethod
    def load(cls, path: Path, *, device: str | None = None) -> LayaOnnxUrgencyModel:
        run_dir = path.parent
        meta_file = run_dir / "meta.json"
        run = (
            json.loads(meta_file.read_text("utf-8"))["run_id"]
            if meta_file.exists()
            else run_dir.name
        )
        return cls(agent=OnnxAgent(path), model_version=f"{run}-{path.name}")


__all__ = ["LayaOnnxUrgencyModel", "OnnxAgent"]
