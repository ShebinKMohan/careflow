"""The speech eval (M2-02): the normalisation both sides get, WER and CER, the report, the clip
manifest in step with its sources, and the scripts' rows. No audio or model is needed here."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from careflow_eval.speech import (
    CAVEATS,
    Clip,
    normalize,
    percentile,
    read_manifest,
    score,
    update_report,
    write_manifest,
)

REPO = Path(__file__).resolve().parents[2]
SPEECH = REPO / "eval" / "speech"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"speech_{name}", SPEECH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_clips = _script("make_clips")
run_wer = _script("run_wer")
run_turns = _script("run_turns")
run_latency = _script("run_latency")


def test_arabic_normalisation_strips_harakat_and_tatweel_and_folds_alef_and_ya() -> None:
    assert normalize("أَنا إلى آخِرِ الـمَدى؟ نعم، ولكن!", "ar") == "انا الي اخر المدي نعم ولكن"
    assert normalize("عِنْدي  أَلَمٌ   في صَدْري", "ar") == normalize("عندي الم في صدري", "ar")


def test_english_normalisation_lowercases_and_drops_punctuation_only() -> None:
    assert normalize("I have Chest-pain, since 7 AM!", "en") == "i have chest pain since 7 am"
    assert normalize("Seven out of ten.", "en") != normalize("7 out of 10.", "en")  # as specified


def test_score_is_corpus_wer_and_cer_of_the_normalised_text() -> None:
    assert score(["I have chest pain."], ["i have chest pain"], "en") == {"wer": 0.0, "cer": 0.0}
    result = score(["عندي ألم في صدري"], ["عدي الم في صدري"], "ar")
    assert result["wer"] == 0.25  # one word of four: ألم matches الم after folding
    assert 0 < result["cer"] < result["wer"]
    with pytest.raises(ValueError, match="as many"):
        score(["one"], [], "en")
    with pytest.raises(ValueError, match="empty"):
        score(["؟"], ["x"], "ar")


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 21)]
    assert (percentile(values, 0.5), percentile(values, 0.95)) == (10.0, 19.0)
    assert percentile([], 0.5) is None


def _clip(clip_id: str, **kwargs: Any) -> Clip:
    base = {
        "id": clip_id,
        "lang": "ar",
        "dialect": "gulf",
        "origin": "sentences",
        "text": "عندي ألم",
        "audio_path": f"eval/datasets/speech/clips/ar/{clip_id}.wav",
        "voice": "facebook/mms-tts-ara@x",
    }
    return Clip.model_validate(base | kwargs)


def test_the_manifest_round_trips_and_refuses_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    clips = [_clip("a"), _clip("b", lang="en", dialect="en", text="Chest pain.")]
    write_manifest(clips, path)
    assert read_manifest(path) == clips
    write_manifest([_clip("a"), _clip("a")], path)
    with pytest.raises(ValueError, match="duplicate"):
        read_manifest(path)


def test_the_report_keeps_other_sections_and_is_never_published(tmp_path: Path) -> None:
    path = tmp_path / "speech.json"
    update_report(path, "stt", {"rows": [1]})
    report = update_report(path, "latency", {"rows": [2]})
    assert report["stt"]["rows"] == [1] and report["latency"]["rows"] == [2]
    assert report["publish"] is False and report["caveats"] == list(CAVEATS)
    assert any("TTS-voiced" in caveat for caveat in CAVEATS)
    assert any("native speakers" in caveat for caveat in CAVEATS)
    assert "/Users/" not in json.dumps(report)  # the machine, never a path or a user name


# --- the dataset: the committed manifest is its sources, voiced ---


def test_the_sentences_are_15_gulf_15_levantine_and_15_english() -> None:
    rows = make_clips.sentence_rows()
    groups = [(row["lang"], row["dialect"]) for row in rows]
    assert groups.count(("ar", "gulf")) == groups.count(("ar", "levantine")) == 15
    assert groups.count(("en", "en")) == 15


def test_the_committed_manifest_is_the_sentences_and_every_scenario_turn() -> None:
    sources = make_clips.sentence_rows() + make_clips.scenario_rows()
    clips = read_manifest(make_clips.MANIFEST)
    assert [(c.id, c.lang, c.dialect, c.origin, c.text) for c in clips] == [
        (r["id"], r["lang"], r["dialect"], r["origin"], r["text"]) for r in sources
    ]
    for clip in clips:
        assert clip.audio_path == str(make_clips.clip_path(clip.lang, clip.text).relative_to(REPO))
        assert clip.voice.startswith("facebook/mms-tts-ara@" if clip.lang == "ar" else "kokoro")


def test_a_clip_is_named_and_seeded_by_its_text_alone() -> None:
    path = make_clips.clip_path("ar", "عندي ألم")
    assert path == make_clips.clip_path("ar", "عندي ألم") != make_clips.clip_path("en", "عندي ألم")
    assert make_clips.seed_for("ar", "عندي ألم") == make_clips.seed_for("ar", "عندي ألم")


# --- the scripts' rows ---


def test_wer_rows_per_dialect_and_set_and_not_run_rows_for_a_gated_engine() -> None:
    clips = [
        _clip("g1", dialect="gulf", text="عندي ألم في صدري"),
        _clip("l1", dialect="levantine", text="عندي وجع بصدري"),
        _clip("s1", dialect="gulf", origin="scenario:x#1", text="إي نعم"),
    ]
    heard = {"g1": "عدي الم في صدري", "l1": "عندي وجع بصدري", "s1": "اي نعم"}
    rows = run_wer.rows_for("mlx-whisper", "m@r", clips, heard)
    by_key = {(row["dialect"], row["set"]): row for row in rows}
    assert set(by_key) == {
        ("gulf", "sentences"),
        ("levantine", "sentences"),
        ("all", "sentences"),
        ("gulf", "scenarios"),
    }
    assert by_key[("gulf", "sentences")]["wer"] == 0.25
    assert by_key[("levantine", "sentences")]["clips_with_errors"] == 0
    gated = run_wer.not_run_rows("cohere-transcribe-arabic", "c@r", clips, "gated model")
    assert {row["not_run"] for row in gated} == {"gated model"}
    assert all(row["wer"] is None and row["cer"] is None for row in gated)


def test_digits_for_number_words_is_told_apart_from_a_real_error() -> None:
    assert run_wer.digits_only(
        "The pain is about seven out of ten.", "The pain is about 7 out of 10."
    )
    assert not run_wer.digits_only("Seven out of ten.", "Eleven out of ten.")
    assert not run_wer.digits_only("Chest pain.", "Chest pain.")  # no difference at all


def test_the_turn_eval_measures_the_repo_settings_for_each_language() -> None:
    english, arabic = run_turns.configured("en"), run_turns.configured("ar")
    assert english["smart_turn"] is False and english["endpoint_silence_ms"] == 400
    assert arabic["smart_turn"] is False and arabic["endpoint_silence_ms"] == 700
    assert run_turns.M1_14["smart_turn"] is True and run_turns.M1_14["smart_turn_threshold"] == 0.5


def test_the_latency_eval_plays_20_distinct_answers_per_language_mixing_dialects() -> None:
    clips = read_manifest(make_clips.MANIFEST)
    arabic, english = run_latency.pick(clips, "ar"), run_latency.pick(clips, "en")
    assert len(arabic) == len(english) == 20
    assert len({c.audio_path for c in arabic}) == 20
    assert [c.dialect for c in arabic[:4]] == ["gulf", "levantine", "gulf", "levantine"]
    assert all(c.lang == "en" for c in english)
