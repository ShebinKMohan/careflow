"""Fetch the voice service's model files into services/voice/models/ (git-ignored).

Sources are the official releases the agent's README names, pinned (GitHub release tags,
Hugging Face revisions). Rules (docs/AGENT_RUNBOOK.md):

- Hugging Face files: `model_info` first; a gated or missing model is reported `not_run`.
- A download over 1 GB needs at least 15 GB free.
- sha256: the first download records each file's hash in services/voice/models.sha256
  (committed); every later run verifies against it and deletes a file that does not match.

Usage: services/voice/scripts/download_models.sh [--without-whisper] [--verify]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

VOICE_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = VOICE_ROOT / "models"
MANIFEST = VOICE_ROOT / "models.sha256"
GB = 1024**3
MIN_FREE_FOR_LARGE = 15 * GB


@dataclass(frozen=True)
class ModelFile:
    path: str  # relative to MODELS_DIR
    url: str
    size: int  # bytes, as published
    hf_repo: str | None = None
    hf_revision: str | None = None
    hf_file: str | None = None
    whisper: bool = False


_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_SMART_TURN_REV = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
_WHISPER_REV = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
_MMS_REPO = "facebook/mms-tts-ara"  # Arabic TTS (M2-02); CC-BY-NC-4.0, see ADR-033
_MMS_REV = "afba26a4b470f9ae64aeeea91b99f080dee410de"


def _hf(repo: str, revision: str, file: str, path: str, size: int, *, whisper: bool = False):
    return ModelFile(
        path=path,
        url=f"https://huggingface.co/{repo}/resolve/{revision}/{file}",
        size=size,
        hf_repo=repo,
        hf_revision=revision,
        hf_file=file,
        whisper=whisper,
    )


FILES: tuple[ModelFile, ...] = (
    ModelFile("silero_vad.onnx", f"{_SHERPA}/silero_vad.onnx", 643_854),
    _hf("pipecat-ai/smart-turn-v3", _SMART_TURN_REV, "smart-turn-v3.2-cpu.onnx",
        "smart-turn-v3.2-cpu.onnx", 8_679_182),
    ModelFile("kokoro-v1.0.onnx", f"{_KOKORO}/kokoro-v1.0.onnx", 325_532_387),
    ModelFile("voices-v1.0.bin", f"{_KOKORO}/voices-v1.0.bin", 28_214_398),
    _hf("mlx-community/whisper-large-v3-turbo", _WHISPER_REV, "config.json",
        "whisper-large-v3-turbo/config.json", 268, whisper=True),
    _hf("mlx-community/whisper-large-v3-turbo", _WHISPER_REV, "weights.safetensors",
        "whisper-large-v3-turbo/weights.safetensors", 1_613_977_612, whisper=True),
    _hf(_MMS_REPO, _MMS_REV, "config.json", "mms-tts-ara/config.json", 1_641),
    _hf(_MMS_REPO, _MMS_REV, "model.safetensors", "mms-tts-ara/model.safetensors", 145_228_280),
    _hf(_MMS_REPO, _MMS_REV, "special_tokens_map.json", "mms-tts-ara/special_tokens_map.json", 48),
    _hf(_MMS_REPO, _MMS_REV, "tokenizer_config.json", "mms-tts-ara/tokenizer_config.json", 288),
    _hf(_MMS_REPO, _MMS_REV, "vocab.json", "mms-tts-ara/vocab.json", 460),
)  # fmt: skip


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest() -> dict[str, str]:
    if not MANIFEST.is_file():
        return {}
    entries: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            entries[name.strip()] = digest
    return entries


def write_manifest(entries: dict[str, str]) -> None:
    lines = ["# sha256 of each model file, recorded on first download (download_models.py)."]
    lines += [f"{entries[name]}  {name}" for name in sorted(entries)]
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def hf_check(item: ModelFile) -> str | None:
    """None when the file may be downloaded, else the not_run reason."""
    from huggingface_hub import model_info

    assert item.hf_repo is not None and item.hf_file is not None  # noqa: S101 - HF entries only
    try:
        info = model_info(item.hf_repo, revision=item.hf_revision, files_metadata=True)
    except Exception as exc:  # noqa: BLE001 - any lookup failure means "do not download"
        return f"model_info failed for {item.hf_repo}: {type(exc).__name__}"
    if info.gated:
        return f"{item.hf_repo} is gated"
    names = {sibling.rfilename for sibling in info.siblings or []}
    if item.hf_file not in names:
        return f"{item.hf_file} is not in {item.hf_repo}@{item.hf_revision}"
    return None


def download(item: ModelFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    with httpx.stream("GET", item.url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    if partial.stat().st_size != item.size:
        partial.unlink()
        raise RuntimeError(f"{item.path}: expected {item.size} bytes from {item.url}")
    partial.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the voice service model files.")
    parser.add_argument("--without-whisper", action="store_true", help="skip the 1.6 GB STT model")
    parser.add_argument("--verify", action="store_true", help="only verify files already present")
    args = parser.parse_args(argv)

    wanted = [item for item in FILES if not (args.without_whisper and item.whisper)]
    manifest = read_manifest()
    missing = [item for item in wanted if not (MODELS_DIR / item.path).is_file()]
    if not args.verify:
        large = [item for item in missing if item.size > GB]
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(MODELS_DIR).free
        if large and free < MIN_FREE_FOR_LARGE:
            print(f"refusing: {free / GB:.1f} GB free, 15 GB needed before a download over 1 GB")
            return 1

    failures = 0
    for item in wanted:
        target = MODELS_DIR / item.path
        if not target.is_file():
            if args.verify:
                print(f"missing   {item.path}")
                failures += 1
                continue
            if item.hf_repo is not None and (reason := hf_check(item)) is not None:
                print(f"not_run   {item.path}: {reason}")
                continue
            print(f"download  {item.path} ({item.size / 1e6:.1f} MB) from {item.url}")
            download(item, target)
        digest = sha256_of(target)
        recorded = manifest.get(item.path)
        if recorded is None:
            manifest[item.path] = digest
            print(f"recorded  {item.path} {digest}")
        elif recorded != digest:
            target.unlink()
            print(f"MISMATCH  {item.path}: {digest} != recorded {recorded}; file deleted")
            failures += 1
        else:
            print(f"verified  {item.path}")
    write_manifest(manifest)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
