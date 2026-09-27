"""Tests for scripts/i18n/backtranslate.py: the flag rule, the re-check of changed strings and the
offline `--check` gate (a scripted provider stands in for the model; no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from careflow_providers import ChatResult, Message

SCRIPT = Path(__file__).resolve().parents[1] / "i18n" / "backtranslate.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backtranslate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["backtranslate"] = module
    spec.loader.exec_module(module)
    return module


bt = load()


class ScriptedProvider:
    """Answers back-translation batches from `backs` (Arabic → English) and judgement batches
    from `different` (English originals the model calls different); records every call."""

    task = "translation_check"
    model_name = "scripted-model"

    def __init__(
        self, backs: dict[str, str], different: frozenset[str] = frozenset(), *, drop: int = 0
    ) -> None:
        self.backs = backs
        self.different = different
        self.drop = drop  # the first `drop` batches of more than one item answer only item 0
        self.calls: list[str] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        items = [json.loads(line) for line in messages[-1]["content"].splitlines()[1:]]
        if "arabic" in items[0]:
            self.calls.append("back")
            answer = [{"id": item["id"], "english": self.backs[item["arabic"]]} for item in items]
        else:
            self.calls.append("judge")
            answer = [
                {
                    "id": item["id"],
                    "same_meaning": item["original"] not in self.different,
                    "issue": "differs" if item["original"] in self.different else None,
                }
                for item in items
            ]
        if self.drop and len(answer) > 1:
            self.drop -= 1
            answer = answer[:1]
        return ChatResult(text=json.dumps({"items": answer}), model=self.model_name)

    def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        raise NotImplementedError


EN = {"safety": {"told": "The nurse has been told."}, "home": {"start": "Start", "tap": "Tap here"}}
AR = {"safety": {"told": "تم إبلاغ الممرضة."}, "home": {"start": "ابدأ", "tap": "اضغط هنا"}}
BACKS = {"تم إبلاغ الممرضة.": "The nurse has been told.", "ابدأ": "Start", "اضغط هنا": "Tap here"}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo where the kiosk's messages are EN and AR; the other sources are empty."""
    monkeypatch.setattr(bt, "REPO", tmp_path)
    for app in bt.APPS:
        english, arabic = (EN, AR) if app == "kiosk" else ({}, {})
        bt.messages(app, "en").parent.mkdir(parents=True)
        bt.messages(app, "en").write_text(json.dumps(english), encoding="utf-8")
        bt.messages(app, "ar").write_text(json.dumps(arabic, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bt, "REPORT_JSON", tmp_path / "eval" / "reports" / "report.json")
    monkeypatch.setattr(bt, "UNRESOLVED", tmp_path / "docs" / "i18n" / "unresolved.md")
    return tmp_path


async def full_report(provider: ScriptedProvider, previous: dict[str, Any] | None = None) -> Any:
    kept, todo = bt.pending_rows(previous, recheck_all=False)
    fresh = await bt.check_strings(provider, todo, model=provider.model_name)
    return bt.build_report([*kept, *fresh], provider_info={"task": "translation_check"})


def write(report: dict[str, Any]) -> None:
    bt.REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    bt.REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


async def test_a_matching_back_translation_passes_and_safety_keys_come_first(repo: Path) -> None:
    report = await full_report(ScriptedProvider(BACKS))
    assert [row["key"] for row in report["items"]] == ["safety.told", "home.start", "home.tap"]
    assert [row["flagged"] for row in report["items"]] == [False, False, False]
    assert report["items"][0]["chrf"] == 100.0
    assert report["apps"]["kiosk"]["strings"] == 3
    write(report)
    assert bt.check() == 0


async def test_the_model_saying_different_or_a_low_chrf_flags_a_key(repo: Path) -> None:
    backs = {**BACKS, "تم إبلاغ الممرضة.": "Nursing staff were informed.", "ابدأ": "Stop"}
    report = await full_report(ScriptedProvider(backs, different=frozenset({"Start"})))
    rows = {row["key"]: row for row in report["items"]}
    # Same meaning by the model's judgement, but chrF below the floor: still flagged.
    assert rows["safety.told"]["same_meaning"] is True
    assert rows["safety.told"]["chrf"] < bt.CHRF_FLOOR
    assert rows["safety.told"]["flagged"] is True
    assert rows["home.start"]["same_meaning"] is False
    assert rows["home.start"]["issue"] == "differs"
    assert rows["home.start"]["flagged"] is True
    assert rows["home.tap"]["flagged"] is False


async def test_check_fails_on_a_flagged_safety_key_until_it_is_listed_as_unresolved(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backs = {**BACKS, "تم إبلاغ الممرضة.": "Nursing staff were informed.", "ابدأ": "Stop"}
    write(await full_report(ScriptedProvider(backs, different=frozenset({"Start"}))))
    assert bt.check() == 1  # the flagged non-safety key alone would not fail it
    assert "kiosk:safety.told is flagged" in capsys.readouterr().err

    bt.UNRESOLVED.parent.mkdir(parents=True)
    bt.UNRESOLVED.write_text("- `kiosk:safety.told`: five rewrites still flagged\n")
    assert bt.check() == 1  # listed, but the Arabic does not show the English yet
    assert "must show the English beside it" in capsys.readouterr().err

    arabic = repo / "apps" / "kiosk" / "messages" / "ar.json"
    shown = {**AR, "safety": {"told": "تم إبلاغ الممرضة. (The nurse has been told.)"}}
    arabic.write_text(json.dumps(shown, ensure_ascii=False), encoding="utf-8")
    write(
        await full_report(
            ScriptedProvider(
                {**backs, **{shown["safety"]["told"]: "Staff told."}},
                different=frozenset({"Start"}),
            )
        )
    )
    assert bt.check() == 0

    bt.UNRESOLVED.write_text("- `kiosk:safety.told`\n- `kiosk:safety.gone`\n")
    assert bt.check() == 1
    assert "kiosk:safety.gone is listed in docs/i18n/unresolved.md but is not a key" in (
        capsys.readouterr().err
    )


async def test_check_fails_when_either_messages_file_changed(repo: Path) -> None:
    write(await full_report(ScriptedProvider(BACKS)))
    for locale in ("ar", "en"):
        path = repo / "apps" / "kiosk" / "messages" / f"{locale}.json"
        original = path.read_text(encoding="utf-8")
        path.write_text(original.replace("}}", ', "extra": "x"}}', 1), encoding="utf-8")
        assert bt.check() == 1
        path.write_text(original, encoding="utf-8")
    assert bt.check() == 0


async def test_only_changed_and_new_strings_are_checked_again(repo: Path) -> None:
    first = await full_report(ScriptedProvider(BACKS))
    arabic = repo / "apps" / "kiosk" / "messages" / "ar.json"
    english = repo / "apps" / "kiosk" / "messages" / "en.json"
    arabic.write_text(
        json.dumps({**AR, "home": {"start": "ابدأ الآن", "tap": "اضغط هنا"}, "new": {"k": "جديد"}}),
        encoding="utf-8",
    )
    english.write_text(json.dumps({**EN, "new": {"k": "New"}}), encoding="utf-8")
    provider = ScriptedProvider({**BACKS, "ابدأ الآن": "Start now", "جديد": "New"})

    kept, todo = bt.pending_rows(first, recheck_all=False)
    assert sorted(row["key"] for row in kept) == ["home.tap", "safety.told"]
    assert sorted(key for _, key, _, _ in todo) == ["home.start", "new.k"]
    second = bt.build_report(
        [*kept, *await bt.check_strings(provider, todo, model="scripted-model")],
        provider_info={"task": "translation_check"},
    )
    assert provider.calls == ["back", "judge"]  # one batch each, for the two strings
    assert {row["key"]: row["back_translation"] for row in second["items"]}["home.start"] == (
        "Start now"
    )
    _, everything = bt.pending_rows(second, recheck_all=True)
    assert len(everything) == 4


async def test_a_batch_answered_with_missing_items_is_asked_again_in_halves(repo: Path) -> None:
    provider = ScriptedProvider(BACKS, drop=1)
    report = await full_report(provider)
    assert [row["back_translation"] for row in report["items"]] == [
        "The nurse has been told.",
        "Start",
        "Tap here",
    ]
    # 3 strings: the batch of 3 lost two answers, so it was asked as 1 + 2; then one judgement.
    assert provider.calls == ["back", "back", "back", "judge"]


async def test_every_finished_batch_is_checkpointed_and_an_interrupted_run_resumes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bt, "BATCH", 2)
    seen: list[int] = []
    _, todo = bt.pending_rows(None, recheck_all=True)
    rows = await bt.check_strings(
        ScriptedProvider(BACKS), todo, model="m", on_rows=lambda done: seen.append(len(done))
    )
    assert seen == [2, 3]
    # A resumed row is reused even by a full re-check; only the others are asked again.
    kept, again = bt.pending_rows(None, recheck_all=True, resumed=rows[:2])
    assert [row["key"] for row in kept] == [row["key"] for row in rows[:2]]
    assert [key for _, key, _, _ in again] == [rows[2]["key"]]


async def test_a_key_missing_in_one_language_stops_the_run(repo: Path) -> None:
    arabic = repo / "apps" / "kiosk" / "messages" / "ar.json"
    arabic.write_text(json.dumps({"safety": AR["safety"]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="en/ar keys differ"):
        bt.pending_rows(None, recheck_all=False)


def test_the_markdown_says_what_the_check_is_and_lists_flagged_keys(repo: Path) -> None:
    row = {
        "app": "kiosk",
        "key": "safety.told",
        "safety": True,
        "english": "The nurse has been told.",
        "arabic": "…",
        "back_translation": "Staff | informed",
        "same_meaning": True,
        "issue": None,
        "chrf": 30.2,
        "flagged": True,
        "checked_at": "2026-09-27T00:00:00+00:00",
        "model": "scripted-model",
    }
    report = bt.build_report([row], provider_info={"task": "translation_check", "served": []})
    text = bt.markdown(report, {"kiosk:safety.told"})
    assert "not a validation" in text
    assert "no accuracy figure is claimed" in text
    assert "Flagged: 1 (1 `safety.`; 1 of them listed" in text
    assert "Unresolved after five rewrites, shown with their English original" in text
    assert "Staff \\| informed" in text  # table cells are escaped
    assert "%" not in text


@respx.mock
def test_served_models_names_snapshots_without_local_paths() -> None:
    respx.get("http://127.0.0.1:8081/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "/Users/x/.cache/hf/hub/models--org--Model-4bit/snapshots/abc123"},
                    {"id": "org/Other"},
                ]
            },
        )
    )

    class Local:
        base_url = "http://127.0.0.1:8081/v1"

    served = bt.served_models(Local())  # type: ignore[arg-type]
    assert served == ["org/Model-4bit@abc123"]  # the loaded snapshot only
