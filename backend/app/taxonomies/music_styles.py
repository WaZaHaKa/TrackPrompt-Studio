from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class TaxonomyModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class StyleLabel(TaxonomyModel):
    id: str
    label: str
    prompt_safe_label: str
    description: str
    aliases: list[str] = Field(default_factory=list)


class SubgenreLabel(StyleLabel):
    parent: str


class DescriptiveLabel(TaxonomyModel):
    id: str
    label: str
    description: str


class MusicStyleTaxonomy(TaxonomyModel):
    taxonomy_version: str
    excluded_artist_names: list[str] = Field(default_factory=list)
    broad_genres: list[StyleLabel]
    subgenres: list[SubgenreLabel]
    descriptive_tags: list[DescriptiveLabel]
    compatible_blends: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> MusicStyleTaxonomy:
        all_ids = [item.id for item in self.broad_genres] + [item.id for item in self.subgenres]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("music-style taxonomy ids must be unique")
        broad_ids = {item.id for item in self.broad_genres}
        if any(item.parent not in broad_ids for item in self.subgenres):
            raise ValueError("every subgenre must have a broad parent")
        if self.excluded_artist_names:
            raise ValueError("artist names are not permitted in the music-style taxonomy")
        return self


@lru_cache(maxsize=1)
def load_music_style_taxonomy() -> MusicStyleTaxonomy:
    path = Path(__file__).with_name("music_styles.v1.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return MusicStyleTaxonomy.model_validate(payload)
