"""Fit the `choice` temperature on validation (M3-01): `careflow-triage laya calibrate`.

As the notebook's `fit_one_temp`: LBFGS on log T minimises the NLL of softmax(logits / T) on the
validation rows, never on test. The rows are scored both as the nurse sees them (all vitals) and
as the kiosk does (no respiratory rate, blood pressure or consciousness), because the model serves
both. The run's `rl_agent_config.json` then gets `temperature = [T, 1.0, 1.0]`, without
`temperature_by_options`, so `laya.Agent` applies T to this one question. Laya clamps a
temperature to [0.5, 5.0] at load; the clamped value is the one that is used and reported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
from scipy.special import log_softmax

from careflow_triage.laya_ktas.dataset import encode, load_data
from careflow_triage.laya_ktas.train import TrainConfig, item_logits

TEMP_MIN: Final = 0.5  # laya.common.TEMP_MIN / TEMP_MAX: the load-time clamp
TEMP_MAX: Final = 5.0


def fit_one_temp(logits: np.ndarray, targets: np.ndarray) -> float:
    """The notebook's fit: LBFGS on log T of the (one-hot) cross-entropy."""
    import torch  # type: ignore[import-not-found]

    z = torch.tensor(logits, dtype=torch.float32)
    t = torch.nn.functional.one_hot(torch.tensor(targets), z.shape[1]).float()
    log_t = torch.zeros(1, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure() -> Any:
        optimizer.zero_grad()
        loss = -(t * torch.log_softmax(z / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(log_t.exp().item())


def nll(logits: np.ndarray, targets: np.ndarray, temperature: float) -> float:
    log_p = log_softmax(logits / temperature, axis=1)
    return float(-log_p[np.arange(len(targets)), targets].mean())


def load_run_model(run_dir: Path, device: Any) -> tuple[Any, Any, dict[str, Any]]:
    """The fine-tuned model of a run (fp32), its tokenizer and agent config."""
    from laya.common import build_model  # type: ignore[import-not-found]
    from safetensors.torch import load_file  # type: ignore[import-not-found]

    from careflow_triage.laya_ktas.dataset import agent_config, load_tokenizer

    tok = load_tokenizer(run_dir)
    cfg = agent_config(run_dir)
    model: Any = build_model(cfg, encoder_dir=str(run_dir / "encoder"), pretrained=False)
    model.load_state_dict(load_file(str(run_dir / "model.safetensors")), strict=True)
    model.float()
    model.encoder.config.reference_compile = False
    return model.to(device), tok, cfg


def calibrate(run_dir: Path, csv_path: Path, config_path: Path) -> dict[str, Any]:
    import torch  # type: ignore[import-not-found]

    config = TrainConfig.load(config_path)
    device = torch.device(config.device if torch.backends.mps.is_available() else "cpu")
    data = load_data(csv_path)
    model, tok, cfg = load_run_model(run_dir, device)
    val = data.part("val")
    items = [encode(tok, cfg, e.laya_input, e.level) for e in val]
    items += [encode(tok, cfg, e.laya_input.kiosk_partial(), e.level) for e in val]
    targets = np.array([e.level - 1 for e in val] * 2)
    logits = item_logits(model, items, device, tok.pad_token_id, config.eval_batch)
    raw = fit_one_temp(logits, targets)
    used = min(TEMP_MAX, max(TEMP_MIN, raw))
    result = {
        "fitted_on": "val (all vitals and kiosk-partial)",
        "n": len(items),
        "temperature_fitted": raw,
        "temperature_used": used,
        "clamped": used != raw,
        "val_nll_before": nll(logits, targets, 1.0),
        "val_nll_after": nll(logits, targets, used),
    }
    config_file = run_dir / "rl_agent_config.json"
    body = json.loads(config_file.read_text(encoding="utf-8"))
    body["temperature"] = [used, 1.0, 1.0]
    body.pop("temperature_by_options", None)
    body["model_name"] = "laya-ktas"
    body["calibration"] = result
    config_file.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    meta_file = run_dir / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["calibration"] = result
    meta_file.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return result


__all__ = ["TEMP_MAX", "TEMP_MIN", "calibrate", "fit_one_temp", "load_run_model", "nll"]
