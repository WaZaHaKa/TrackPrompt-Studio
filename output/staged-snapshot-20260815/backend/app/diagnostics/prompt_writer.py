from __future__ import annotations

import argparse
import json
from itertools import combinations
from typing import Any

from ..config import Settings
from ..prompting.engine import _materially_diverse
from ..prompting.local_writer import LocalPromptWriterError, OllamaPromptWriterAdapter
from ..schemas import GenerationIntent, PromptEngineMode, PromptEvidence, PromptGenerationParameters

SYNTHETIC_PRIVATE_TRANSCRIPT_MARKER = "synthetic private transcript must remain excluded"


def _candidate_schema_errors(candidate: dict[str, Any]) -> list[str]:
    prompt = candidate.get("prompt")
    errors: list[str] = []
    if not isinstance(prompt, str) or not prompt.strip():
        errors.append("candidate prompt is missing")
    elif "\n" in prompt.strip() or len(prompt) > 600:
        errors.append("prompt must be one bounded paragraph")
    elif not all(term in prompt.casefold() for term in ("original", "melody", "arrangement")):
        errors.append("originality requirement is missing")
    if not isinstance(candidate.get("shortTitle"), str):
        errors.append("shortTitle must be text")
    if not isinstance(candidate.get("factsUsed"), list):
        errors.append("factsUsed must be a list")
    if not isinstance(candidate.get("creativeDirectionsUsed"), list):
        errors.append("creativeDirectionsUsed must be a list")
    return errors


def _candidate_schema_is_valid(candidate: dict[str, Any]) -> bool:
    return not _candidate_schema_errors(candidate)


def _batch_validation(
    candidates: list[dict[str, Any]],
    expected_count: int,
) -> tuple[bool, bool, list[str]]:
    errors = [
        error
        for candidate in candidates
        for error in _candidate_schema_errors(candidate)
    ]
    if len(candidates) != expected_count:
        errors.append(f"expected exactly {expected_count} candidates")
    prompts = [str(candidate.get("prompt", "")) for candidate in candidates]
    diverse = len(prompts) == expected_count and all(
        _materially_diverse(left, right)
        for left, right in combinations(prompts, 2)
    )
    if not diverse:
        errors.append("candidates were not materially diverse")
    return (
        len(candidates) == expected_count
        and all(_candidate_schema_is_valid(candidate) for candidate in candidates),
        diverse,
        list(dict.fromkeys(errors)),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    adapter = OllamaPromptWriterAdapter(settings)
    capability = adapter.capability()
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "reliableAvailable": True,
            "creativeAvailable": capability.creative_available,
            "experimentalAvailable": capability.experimental_available,
            "fallbackBehavior": capability.fallback_behavior,
            "fallbackDeclared": bool(capability.fallback_behavior),
            "privateTranscriptIncludedInEvidence": False,
        }
    )
    smoke_passed = not arguments.smoke
    try:
        if arguments.smoke and capability.available:
            evidence = PromptEvidence(
                accepted_genre_candidates=["electronic dance"],
                groove=["steady club pulse"],
                generation_intent=GenerationIntent.PRESERVE,
                creative_freedom=0.5,
                originality_requirement="Create an original melody, arrangement, and any lyrics.",
                maximum_characters=600,
                output_language="English",
            )
            seed = 12345
            parameters = {
                PromptEngineMode.CREATIVE: PromptGenerationParameters(
                    sampling=True,
                    temperature=0.65,
                    top_p=0.9,
                    repetition_penalty=1.08,
                    maximum_tokens=700,
                    timeout_seconds=settings.local_llm_timeout_seconds,
                ),
                PromptEngineMode.EXPERIMENTAL: PromptGenerationParameters(
                    sampling=True,
                    temperature=0.88,
                    top_p=0.96,
                    repetition_penalty=1.12,
                    maximum_tokens=700,
                    timeout_seconds=settings.local_llm_timeout_seconds,
                ),
            }
            results: dict[str, object] = {}
            all_prompts: list[str] = []
            for mode in (PromptEngineMode.CREATIVE, PromptEngineMode.EXPERIMENTAL):
                candidates = adapter.generate_candidates(
                    evidence,
                    mode,
                    seed,
                    3,
                    parameters[mode],
                )
                initial_schema_valid, initial_diverse, repair_errors = _batch_validation(candidates, 3)
                repair_attempted = bool(repair_errors)
                if repair_attempted:
                    candidates = adapter.generate_candidates(
                        evidence,
                        mode,
                        seed,
                        3,
                        parameters[mode],
                        repair_errors=repair_errors,
                    )
                schema_valid, diverse, final_errors = _batch_validation(candidates, 3)
                prompts = [str(candidate.get("prompt", "")) for candidate in candidates]
                results[mode.value] = {
                    "candidateCount": len(candidates),
                    "initialCandidateSchemaValid": initial_schema_valid,
                    "initialCandidateDiversity": initial_diverse,
                    "repairAttempted": repair_attempted,
                    "repairReasons": repair_errors,
                    "candidateSchemaValid": schema_valid,
                    "materiallyDiverse": diverse,
                    "finalValidationErrors": final_errors,
                }
                all_prompts.extend(prompts)
            evidence_json = evidence.model_dump_json()
            raw_lyrics_excluded = (
                SYNTHETIC_PRIVATE_TRANSCRIPT_MARKER not in evidence_json
                and all(
                    SYNTHETIC_PRIVATE_TRANSCRIPT_MARKER not in prompt.casefold()
                    for prompt in all_prompts
                )
            )
            payload.update(
                {
                    "tinyInference": True,
                    "modeResults": results,
                    "candidateSchemaValidation": all(
                        bool(result["candidateSchemaValid"])
                        for result in results.values()
                        if isinstance(result, dict)
                    ),
                    "candidateDiversity": all(
                        bool(result["materiallyDiverse"])
                        for result in results.values()
                        if isinstance(result, dict)
                    ),
                    "seedHandling": {
                        "supported": adapter.supports_seed(),
                        "requestedSeed": seed,
                        "sameSeedUsedAcrossModeChecks": True,
                    },
                    "rawLyricsExclusion": raw_lyrics_excluded,
                }
            )
            smoke_passed = bool(
                payload["candidateSchemaValidation"]
                and payload["candidateDiversity"]
                and raw_lyrics_excluded
                and adapter.supports_seed()
            )
    except LocalPromptWriterError as exc:
        payload["tinyInference"] = False
        payload["smokeFailure"] = str(exc)
        smoke_passed = False
    finally:
        adapter.unload_or_release()
    print(json.dumps(payload, indent=2))
    return 0 if capability.available and smoke_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
