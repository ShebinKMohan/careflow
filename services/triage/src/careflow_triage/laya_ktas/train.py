"""Fine-tune Laya on KTAS, on MPS only (M3-01): `careflow-triage laya train --config ...`.

As the official notebook (NandhaKishorM/laya @ 4066d5d5) does it: `build_model` from the
checkpoint's config and `encoder/`, the checkpoint's weights, gradient checkpointing on the encoder
and the head, AdamW with one rate for the encoder and one for the head, a cosine schedule and
gradient clipping; the loss is the notebook's soft cross-entropy term only (its RL term is not
used). fp32 on MPS: no autocast. The best epoch by validation macro-F1 is kept in the run
directory (a loadable Laya checkpoint); `resume.pt` lets a stopped run go on after its last
epoch and is deleted at the end. Early stop after `patience` epochs without improvement.
"""

from __future__ import annotations

import json
import math
import platform
import random
import time
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Final

import numpy as np
import yaml

from careflow_triage.laya_ktas.dataset import (
    KtasData,
    agent_config,
    encode,
    encode_examples,
    epoch_items,
    load_data,
    load_tokenizer,
)
from careflow_triage.metrics import macro_f1
from careflow_triage.paths import ARTIFACTS_DIR

LAYA_DIR: Final = ARTIFACTS_DIR / "laya"
BASE_FILES: Final = ["rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*"]
MODEL_ID: Final = "laya-ktas"


@dataclass(frozen=True)
class TrainConfig:
    base_model: str
    revision: str
    device: str
    seed: int
    lr_encoder: float
    lr_head: float
    weight_decay: float
    eta_min: float
    grad_clip: float
    micro_batch: int
    grad_accum: int
    max_epochs: int
    patience: int
    kiosk_p: float
    eval_batch: int
    precision: str = "fp32"

    @classmethod
    def load(cls, path: Path) -> TrainConfig:
        body: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(**body)


def base_model_dir(config: TrainConfig) -> Path:
    """The pinned base checkpoint (downloaded once into the Hugging Face cache)."""
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(config.base_model, revision=config.revision, allow_patterns=BASE_FILES)
    )


def run_id(data: KtasData, seed: int) -> str:
    return f"{'fixture-' if data.fixture else ''}{MODEL_ID}-{data.csv_sha256[:8]}-s{seed}"


def collate(items: list[dict[str, Any]], pad_id: int) -> dict[str, Any]:
    import torch  # type: ignore[import-not-found]

    n, length = len(items), max(len(it["ids"]) for it in items)
    kmax = max(len(it["markers"]) for it in items)
    ids = torch.full((n, length), pad_id, dtype=torch.long)
    att = torch.zeros((n, length), dtype=torch.long)
    mpos = torch.zeros((n, kmax), dtype=torch.long)
    mmask = torch.zeros((n, kmax), dtype=torch.bool)
    target = torch.zeros((n, kmax), dtype=torch.float32)
    for i, it in enumerate(items):
        ids[i, : len(it["ids"])] = torch.tensor(it["ids"])
        att[i, : len(it["ids"])] = 1
        k = len(it["markers"])
        mpos[i, :k] = torch.tensor(it["markers"])
        mmask[i, :k] = True
        target[i, : len(it["target"])] = torch.tensor(it["target"], dtype=torch.float32)
    return {
        "input_ids": ids,
        "attention_mask": att,
        "marker_pos": mpos,
        "marker_mask": mmask,
        "qtype": torch.tensor([it["qtype"] for it in items]),
        "target": target,
    }


def forward(model: Any, batch: dict[str, Any], device: Any) -> tuple[Any, Any]:
    logits, act = model(
        batch["input_ids"].to(device),
        batch["attention_mask"].to(device),
        batch["marker_pos"].to(device),
        batch["marker_mask"].to(device),
        batch["qtype"].to(device),
    )
    return logits.float(), act


def soft_cross_entropy(logits: Any, target: Any, mask: Any) -> Any:
    import torch  # type: ignore[import-not-found]

    log_p = torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)
    return -(target * log_p).sum(-1).mean()


def item_logits(
    model: Any, items: list[dict[str, Any]], device: Any, pad_id: int, batch: int
) -> np.ndarray:
    """The raw (temperature 1) logits of each item's 5 options, in order."""
    import torch  # type: ignore[import-not-found]

    model.eval()
    out: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(items), batch):
            chunk = items[start : start + batch]
            logits, _ = forward(model, collate(chunk, pad_id), device)
            out.append(logits[:, :5].cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 5))


def load_base(config: TrainConfig) -> tuple[Any, Any, dict[str, Any], Path]:
    """The base Laya model (fp32), its tokenizer and config."""
    from laya.common import build_model  # type: ignore[import-not-found]
    from safetensors.torch import load_file  # type: ignore[import-not-found]

    base = base_model_dir(config)
    tok = load_tokenizer(base)
    cfg = agent_config(base)
    model: Any = build_model(cfg, encoder_dir=str(base / "encoder"))
    model.load_state_dict(load_file(str(base / "model.safetensors")), strict=True)
    model.float()
    model.encoder.config.reference_compile = False  # no torch.compile on MPS
    return model, tok, cfg, base


def save_checkpoint(run_dir: Path, model: Any, tok: Any, cfg: dict[str, Any]) -> None:
    """A loadable Laya checkpoint: fp32 weights, encoder config, tokenizer, agent config."""
    from safetensors.torch import save_file  # type: ignore[import-not-found]

    run_dir.mkdir(parents=True, exist_ok=True)
    weights = {k: v.detach().float().contiguous().cpu() for k, v in model.state_dict().items()}
    save_file(weights, str(run_dir / "model.safetensors"))
    model.encoder.config.save_pretrained(str(run_dir / "encoder"))
    tok.save_pretrained(str(run_dir / "tokenizer"))
    body = {**cfg, "model_name": MODEL_ID, "fine_tuned": True}
    body.pop("training", None)
    (run_dir / "rl_agent_config.json").write_text(json.dumps(body, indent=2) + "\n", "utf-8")


def train(
    config_path: Path,
    csv_path: Path,
    *,
    out_dir: Path = LAYA_DIR,
    max_epochs: int | None = None,
    log: Any = print,
) -> Path:
    import torch  # type: ignore[import-not-found]

    config = TrainConfig.load(config_path)
    epochs = max_epochs if max_epochs is not None else config.max_epochs
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    if config.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available: Laya is fine-tuned on Apple silicon only")
    device = torch.device(config.device)
    started = time.perf_counter()

    data = load_data(csv_path)
    run_dir = out_dir / run_id(data, config.seed)
    model, tok, cfg, base = load_base(config)
    train_rows, val_rows = data.part("train"), data.part("val")
    encoded = encode_examples(tok, cfg, train_rows)
    val_items = [encode(tok, cfg, e.laya_input, e.level) for e in val_rows]
    y_val = np.array([e.level for e in val_rows])
    log(f"laya: {len(train_rows)} train rows, {len(val_rows)} val rows, run {run_dir.name}")

    model.encoder.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    model.head_checkpointing = True
    model.to(device)
    encoder_params = [p for n, p in model.named_parameters() if n.startswith("encoder.")]
    head_params = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    optimizer = torch.optim.AdamW(
        [
            {"params": encoder_params, "lr": config.lr_encoder},
            {"params": head_params, "lr": config.lr_head},
        ],
        weight_decay=config.weight_decay,
    )
    per_epoch = math.ceil(len(encoded) / (config.micro_batch * config.grad_accum))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=max(1, per_epoch * epochs), eta_min=config.eta_min
    )

    resume_path = run_dir / "resume.pt"
    start_epoch, best_f1, stale, history = 0, -1.0, 0, []
    if resume_path.exists():
        state = torch.load(resume_path, map_location="cpu", weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start_epoch, best_f1, stale = state["epoch"], state["best_f1"], state["stale"]
        history = state["history"]
        log(f"laya: resuming after epoch {start_epoch} (best val macro-F1 {best_f1:.3f})")

    pad_id = tok.pad_token_id
    for epoch in range(start_epoch, epochs):
        model.train()
        items = epoch_items(encoded, epoch=epoch, seed=config.seed, kiosk_p=config.kiosk_p)
        epoch_start, total, batches = time.perf_counter(), 0.0, 0
        optimizer.zero_grad(set_to_none=True)
        for step, start in enumerate(range(0, len(items), config.micro_batch), start=1):
            batch = collate(items[start : start + config.micro_batch], pad_id)
            logits, act = forward(model, batch, device)
            mask = batch["marker_mask"].to(device)
            loss = soft_cross_entropy(logits, batch["target"].to(device), mask)
            (loss / config.grad_accum + 0.0 * act.sum()).backward()
            total, batches = total + float(loss.item()), batches + 1
            if step % config.grad_accum == 0 or start + config.micro_batch >= len(items):
                torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
        val_logits = item_logits(model, val_items, device, pad_id, config.eval_batch)
        predicted = val_logits.argmax(axis=1) + 1
        f1 = macro_f1(y_val, predicted)
        record = {
            "epoch": epoch + 1,
            "train_loss": total / max(1, batches),
            "val_macro_f1": f1,
            "val_accuracy": float((predicted == y_val).mean()),
            "seconds": round(time.perf_counter() - epoch_start, 1),
        }
        history.append(record)
        improved = f1 > best_f1
        if improved:
            best_f1, stale = f1, 0
            save_checkpoint(run_dir, model, tok, cfg)
        else:
            stale += 1
        log(
            f"laya: epoch {epoch + 1}/{epochs} loss {record['train_loss']:.4f} val macro-F1 "
            f"{f1:.3f}{' (best)' if improved else ''} in {record['seconds']} s"
        )
        torch.save(
            {
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "epoch": epoch + 1,
                "best_f1": best_f1,
                "stale": stale,
                "history": history,
            },
            resume_path,
        )
        if stale >= config.patience:
            log(f"laya: early stop, no val macro-F1 gain for {stale} epochs")
            break
    resume_path.unlink(missing_ok=True)
    best = max(history, key=lambda r: r["val_macro_f1"])
    meta = {
        "run_id": run_dir.name,
        "model_id": MODEL_ID,
        "state_version": "laya-state/v1",
        "fixture": data.fixture,
        "csv_sha256": data.csv_sha256,
        "split_file": "ktas_v1.json",
        "split_counts": {name: len(idx) for name, idx in data.splits.items()},
        "base_model": f"{config.base_model}@{config.revision}",
        "config": config.__dict__,
        "history": history,
        "best_epoch": best["epoch"],
        "best_val_macro_f1": best["val_macro_f1"],
        "train_seconds": round(time.perf_counter() - started, 1),
        "libraries": {name: version(name) for name in ("laya", "torch", "transformers")},
        "python": platform.python_version(),
        "base_checkpoint_dir": str(base),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return run_dir


__all__ = [
    "LAYA_DIR",
    "MODEL_ID",
    "TrainConfig",
    "base_model_dir",
    "collate",
    "forward",
    "item_logits",
    "load_base",
    "run_id",
    "save_checkpoint",
    "soft_cross_entropy",
    "train",
]
