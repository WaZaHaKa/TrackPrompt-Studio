from __future__ import annotations

import argparse
import json

from ..config import Settings
from ..prompting.local_writer import OllamaPromptWriterAdapter
from ..schemas import GenerationIntent, PromptEngineMode, PromptEvidence, PromptGenerationParameters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    adapter = OllamaPromptWriterAdapter(settings)
    capability = adapter.capability()
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    if arguments.smoke and capability.available:
        evidence = PromptEvidence(
            accepted_genre_candidates=["electronic"],
            generation_intent=GenerationIntent.PRESERVE,
            creative_freedom=0.5,
            originality_requirement="Create an original melody, arrangement, and any lyrics.",
            maximum_characters=600,
            output_language="English",
        )
        candidates = adapter.generate_candidates(
            evidence,
            PromptEngineMode.CREATIVE,
            12345,
            1,
            PromptGenerationParameters(sampling=True, temperature=0.65, top_p=0.9, repetition_penalty=1.08, maximum_tokens=300, timeout_seconds=settings.local_llm_timeout_seconds),
        )
        payload["tinyInference"] = bool(candidates and isinstance(candidates[0].get("prompt"), str))
    adapter.unload_or_release()
    print(json.dumps(payload, indent=2))
    return 0 if capability.available and (not arguments.smoke or payload.get("tinyInference") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
