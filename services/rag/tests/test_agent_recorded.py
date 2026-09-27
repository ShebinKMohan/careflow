"""The ask-the-clinic agent with the real `conversation` model's replies (M2-09, SHE-306),
recorded once from the local Qwen3.5-9B and replayed in CI (`fixtures/agent.cassette.json`).

Practical questions in English and Arabic, with fixed retrieval (the gold section and two
others), so every prompt, and so every recording key, is the same on each run. The checks: the
real model's answers are cited, their citations are sources, and the output gate lets them
through (it does not block real practical answers). Record again with a model serving:

    CAREFLOW_AGENT_RECORD=1 scripts/with_llm.sh \
        uv run pytest services/rag/tests/test_agent_recorded.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

import pytest

from careflow_contracts import RetrievedChunk
from careflow_providers import build_provider, load_config
from careflow_providers.adapters.replay import ReplayProvider
from careflow_rag.agent import AskAgent, AskConfig, AskPorts, MedicalFlag, QueueSnapshot
from careflow_rag.config import load_rag_config
from careflow_rag.handbook import load
from careflow_rag.ingest import DEFAULT_HANDBOOK

REPO: Final = Path(__file__).resolve().parents[3]
CASSETTE: Final = Path(__file__).parent / "fixtures" / "agent.cassette.json"
RECORDING: Final = os.environ.get("CAREFLOW_AGENT_RECORD") == "1"
CHUNKS: Final = {(c.section_id, c.lang): c for c in load(DEFAULT_HANDBOOK)}
ARABIC: Final = re.compile("[؀-ۿ]")
SESSION: Final = "5f0c7a3e-9d6b-4c1e-8a2f-1b3c5d7e9f02"
# (language, question, gold section, two other retrieved sections)
PRACTICAL: Final = [
    ("en", "When does the pharmacy open?", "pharmacy.hours", ("facilities.cafe", "hours.opening")),
    (
        "en",
        "Where can I park my car?",
        "parking.car_park",
        ("parking.drop_off", "directions.lifts"),
    ),
    (
        "en",
        "Which insurance companies do you work with?",
        "insurance.accepted",
        ("insurance.no_insurance", "insurance.documents"),
    ),
    (
        "en",
        "Is there free Wi-Fi here?",
        "facilities.wifi",
        ("facilities.cafe", "facilities.toilets"),
    ),
    (
        "en",
        "Where is the prayer room?",
        "facilities.prayer_room",
        ("facilities.cafe", "directions.ground_floor"),
    ),
    (
        "en",
        "Until what time can I visit my husband?",
        "visitors.hours",
        ("visitors.rules", "hours.opening"),
    ),
    ("ar", "متى تفتح الصيدلية؟", "pharmacy.hours", ("facilities.cafe", "hours.opening")),
    ("ar", "أين يمكنني ركن سيارتي؟", "parking.car_park", ("parking.drop_off", "directions.lifts")),
    (
        "ar",
        "ما هي شركات التأمين التي تتعاملون معها؟",
        "insurance.accepted",
        ("insurance.no_insurance", "insurance.documents"),
    ),
    (
        "ar",
        "هل يوجد واي فاي مجاني هنا؟",
        "facilities.wifi",
        ("facilities.cafe", "facilities.toilets"),
    ),
    (
        "ar",
        "أين غرفة الصلاة؟",
        "facilities.prayer_room",
        ("facilities.cafe", "directions.ground_floor"),
    ),
    ("ar", "حتى أي ساعة يمكنني زيارة زوجي؟", "visitors.hours", ("visitors.rules", "hours.opening")),
]


def retrieved(section_id: str, lang: str, cosine: float) -> RetrievedChunk:
    source = CHUNKS[(section_id, lang)]
    return RetrievedChunk(
        section_id=section_id,
        chunk_no=0,
        lang=lang,  # type: ignore[arg-type]
        title=source.title,
        body=source.body,
        dense_cosine=cosine,
        bm25_rank=None,
        rrf_score=0.0,
    )


def model() -> ReplayProvider:
    config = load_config(REPO / "config" / "providers.yaml")
    inner = build_provider("conversation", config) if RECORDING else None
    return ReplayProvider(
        inner,
        task="conversation",
        model=inner.model_name if inner is not None else "qwen3.5-9b-4bit",
        cassette=CASSETTE,
        mode="record" if RECORDING else "replay",
    )


@pytest.fixture(scope="module")
def agent() -> AskAgent:
    if not RECORDING and not CASSETTE.is_file():
        pytest.skip("no recording: record with CAREFLOW_AGENT_RECORD=1 and a model serving")
    return AskAgent(model, AskConfig(min_score=load_rag_config().ask.min_score, llm_timeout_s=120))


@pytest.mark.parametrize(
    ("lang", "question", "gold", "others"), PRACTICAL, ids=[f"{p[0]}-{p[2]}" for p in PRACTICAL]
)
async def test_agent_real_answers_are_cited_and_pass_the_gate(
    agent: AskAgent, lang: str, question: str, gold: str, others: tuple[str, str]
) -> None:
    chunks = [retrieved(gold, lang, 0.62), *(retrieved(s, lang, 0.41) for s in others)]
    flags: list[MedicalFlag] = []

    async def fetch(_question: str, _lang: str) -> list[RetrievedChunk]:
        return chunks

    async def queue(_session: str) -> QueueSnapshot:
        return QueueSnapshot(state="waiting", position=3, wait_estimate_minutes=15)

    async def flag(item: MedicalFlag) -> str:
        flags.append(item)
        return "flg_recorded"

    ports = AskPorts(retrieve=fetch, queue_status=queue, flag=flag)
    result = await agent.ask(question, lang, session_id=SESSION, ports=ports)  # type: ignore[arg-type]
    assert not result.refused_medical and flags == [], (result.category, result.answer)
    assert not result.dont_know, result.answer
    cited = [c["doc_id"] for c in result.citations]
    assert cited and set(cited) <= {gold, *others}
    assert gold in cited
    assert bool(ARABIC.search(result.answer)) is (lang == "ar")  # in the session's language
