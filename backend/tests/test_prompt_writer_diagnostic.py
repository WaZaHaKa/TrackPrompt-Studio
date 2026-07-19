from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.diagnostics.prompt_writer import _batch_validation, _candidate_schema_errors
from app.prompting.local_writer import OllamaPromptWriterAdapter
from app.schemas import GenerationIntent, PromptEngineMode, PromptEvidence, PromptGenerationParameters

from .helpers import settings_for


def _candidate(prompt: str) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "shortTitle": "Direction",
        "factsUsed": ["acceptedGenreCandidates"],
        "creativeDirectionsUsed": ["arrangement contrast"],
    }


def test_prompt_writer_diagnostic_reports_repairable_originality_error() -> None:
    errors = _candidate_schema_errors(
        _candidate("Build an original melody over a steady electronic pulse.")
    )

    assert errors == ["originality requirement is missing"]


def test_prompt_writer_diagnostic_accepts_valid_diverse_batch() -> None:
    candidates = [
        _candidate(
            "Build an original melody and arrangement around a driving pulse with evolving percussion."
        ),
        _candidate(
            "Shape a spacious introduction, then reveal an original arrangement and melody through bright synth layers."
        ),
        _candidate(
            "Develop tactile bass movement beneath an original melody while the arrangement grows into a restrained climax."
        ),
    ]

    schema_valid, diverse, errors = _batch_validation(candidates, 3)

    assert schema_valid is True
    assert diverse is True
    assert errors == []


def test_ollama_repair_contract_requires_literal_originality_terms_for_every_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = OllamaPromptWriterAdapter(settings_for(tmp_path))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(adapter, "capability", lambda: SimpleNamespace(available=True))

    def request(path: str, payload: dict[str, Any] | None, timeout: int) -> dict[str, Any]:
        captured.update({"path": path, "payload": payload, "timeout": timeout})
        return {
            "message": {
                "content": json.dumps(
                    {
                        "candidates": [
                            _candidate(
                                "Shape a new direction with an original melody and arrangement."
                            )
                        ]
                    }
                )
            }
        }

    monkeypatch.setattr(adapter, "_request", request)
    evidence = PromptEvidence(
        desired_transformations=["increase timbral surprise"],
        generation_intent=GenerationIntent.PRESERVE,
        creative_freedom=0.9,
        originality_requirement="Create an original melody, arrangement, and any lyrics.",
        maximum_characters=600,
        output_language="English",
    )
    candidates = adapter.generate_candidates(
        evidence,
        PromptEngineMode.EXPERIMENTAL,
        12345,
        1,
        PromptGenerationParameters(sampling=True, temperature=0.88),
        repair_errors=["originality requirement is missing"],
        forbidden_genre_labels=["ambient", "techno"],
        allowed_fact_paths=["rhythm.bpm"],
        required_fact_paths=["rhythm.bpm"],
        required_fact_literals={"rhythm.bpm": "128 BPM"},
    )

    assert len(candidates) == 1
    payload = captured["payload"]
    assert isinstance(payload, dict)
    system_text = payload["messages"][0]["content"]
    user_payload = json.loads(payload["messages"][1]["content"])
    prompt_schema = payload["format"]["properties"]["candidates"]["items"]["properties"]["prompt"]
    assert 'literal English words "original", "melody", and "arrangement"' in system_text
    assert user_payload["candidateContract"]["requiredLiteralWordsInEveryPrompt"] == [
        "original",
        "melody",
        "arrangement",
    ]
    assert user_payload["candidateContract"]["allowedGenreLabels"] == []
    assert user_payload["candidateContract"]["forbiddenGenreLabels"] == ["ambient", "techno"]
    assert user_payload["candidateContract"]["allowedFactPaths"] == ["rhythm.bpm"]
    assert user_payload["candidateContract"]["requiredFactPaths"] == ["rhythm.bpm"]
    assert user_payload["candidateContract"]["requiredFactEvidence"] == [
        {"path": "rhythm.bpm", "literal": "128 BPM"}
    ]
    assert user_payload["candidateContract"]["requiredCreativeDirections"] == [
        "increase timbral surprise"
    ]
    assert payload["format"]["properties"]["candidates"]["items"]["properties"]["factsUsed"]["minItems"] == 1
    assert payload["format"]["properties"]["candidates"]["items"]["properties"]["factsUsed"]["items"]["enum"] == [
        "rhythm.bpm"
    ]
    direction_schema = payload["format"]["properties"]["candidates"]["items"]["properties"][
        "creativeDirectionsUsed"
    ]
    assert direction_schema["minItems"] == 1
    assert direction_schema["maxItems"] == 1
    assert direction_schema["items"]["enum"] == ["increase timbral surprise"]
    assert "copying" in user_payload["candidateContract"]["forbiddenReferenceTerms"]
    assert user_payload["candidateContract"]["candidateBlueprints"] == [
        {
            "candidateNumber": 1,
            "requiredOpeningSentence": "Drive the rhythm into a direct entrance.",
            "arrangementDirection": "Break the form into asymmetric accumulations and sudden voids.",
            "productionDirection": "Push spectral extremes against a deliberately unstable center.",
        }
    ]
    assert "when that list is empty, name no genre or style" in system_text
    assert "Every repaired prompt must contain" in user_payload["repairInstruction"]
    assert prompt_schema["maxLength"] == 600
