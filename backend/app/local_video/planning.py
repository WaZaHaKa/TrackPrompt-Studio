from __future__ import annotations

from typing import Any

from .models import LocalVideoTimelineScene
from .package import LocalVideoProjectPackage
from .prompting import CompiledLocalPrompt


def build_story_plan(
    package: LocalVideoProjectPackage,
    timeline: tuple[LocalVideoTimelineScene, ...],
) -> dict[str, Any]:
    raw_chapters = package.chapter_map.get("chapters")
    if not isinstance(raw_chapters, list):
        raw_chapters = package.chapter_map.get("acts")
    chapters: list[dict[str, Any]] = []
    if isinstance(raw_chapters, list):
        for index, chapter in enumerate(raw_chapters, start=1):
            if not isinstance(chapter, dict):
                continue
            chapter_id = str(chapter.get("chapterId") or chapter.get("id") or f"chapter-{index:02d}")
            shot_ids_raw = chapter.get("shotIds")
            shot_ids = [str(item) for item in shot_ids_raw] if isinstance(shot_ids_raw, list) else []
            matching = [scene for scene in timeline if scene.shot_id in shot_ids]
            chapters.append(
                {
                    "chapterId": chapter_id,
                    "title": str(chapter.get("title") or chapter.get("name") or chapter_id),
                    "narrativeIntent": str(
                        chapter.get("narrativeIntent") or chapter.get("description") or ""
                    ),
                    "shotIds": shot_ids,
                    "startSeconds": matching[0].start_seconds if matching else None,
                    "endSeconds": matching[-1].end_seconds if matching else None,
                }
            )
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-local-video-story-plan",
        "projectId": package.project_id,
        "title": package.title,
        "durationSeconds": timeline[-1].end_seconds,
        "clockSource": "decoded-audio-pts",
        "chapters": chapters,
        "orderedShotIds": [scene.shot_id for scene in timeline],
        "story": {
            "logline": str(package.creative_bible.get("logline") or ""),
            "narrativeThemes": list(package.creative_bible.get("narrativeTheme") or []),
        },
    }


def build_shot_plan(
    package: LocalVideoProjectPackage,
    timeline: tuple[LocalVideoTimelineScene, ...],
    prompts: tuple[CompiledLocalPrompt, ...],
) -> dict[str, Any]:
    by_id = {prompt.shot_id: prompt for prompt in prompts}
    package_shots = {str(shot["shotId"]): shot for shot in package.shots}
    shots: list[dict[str, Any]] = []
    for scene in timeline:
        prompt = by_id[scene.shot_id]
        source = package_shots[scene.shot_id]
        shots.append(
            {
                "shotId": scene.shot_id,
                "order": scene.order,
                "chapterId": str(source.get("chapterId") or ""),
                "startSeconds": scene.start_seconds,
                "endSeconds": scene.end_seconds,
                "durationSeconds": scene.duration_seconds,
                "boundarySource": scene.boundary_source,
                "seed": prompt.seed,
                "promptDigest": prompt.prompt_digest,
                "continuityGroupIds": list(source.get("continuityGroupIds") or []),
                "transitionOutId": source.get("transitionOutId"),
                "heroVariantAllowed": scene.shot_id in package.optional_alternate_shots,
            }
        )
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-local-video-shot-plan",
        "projectId": package.project_id,
        "fps": 24,
        "width": 1920,
        "height": 1080,
        "durationSeconds": timeline[-1].end_seconds,
        "shots": shots,
    }
