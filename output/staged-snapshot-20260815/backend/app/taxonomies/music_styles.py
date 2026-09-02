from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class TaxonomyModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class TempoPrior(TaxonomyModel):
    minimum_bpm: float = Field(ge=20.0, le=300.0)
    maximum_bpm: float = Field(ge=20.0, le=300.0)
    note: str = Field(min_length=12, max_length=180)

    @model_validator(mode="after")
    def ordered_range(self) -> TempoPrior:
        if self.maximum_bpm <= self.minimum_bpm:
            raise ValueError("tempo prior maximum must exceed its minimum")
        if "weak" not in self.note.casefold():
            raise ValueError("tempo priors must explicitly remain weak evidence")
        return self


class StyleLabel(TaxonomyModel):
    id: str
    label: str
    prompt_safe_label: str
    description: str
    clap_descriptions: list[str] = Field(min_length=2, max_length=5)
    aliases: list[str] = Field(default_factory=list)
    compatibility_groups: list[str] = Field(default_factory=list)
    incompatibility_groups: list[str] = Field(default_factory=list)
    expected_rhythmic_traits: list[str] = Field(default_factory=list)
    tempo_prior: TempoPrior | None = None
    instrumentation_priors: list[str] = Field(default_factory=list)


class SubgenreLabel(StyleLabel):
    parent: str


class DescriptiveLabel(TaxonomyModel):
    id: str
    label: str
    description: str
    clap_descriptions: list[str] = Field(min_length=2, max_length=5)


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
        known_ids = set(all_ids)
        if any(
            left not in known_ids or right not in known_ids
            for left, right in self.compatible_blends
        ):
            raise ValueError("compatible blend ids must exist in the taxonomy")
        genre_descriptions = [
            description
            for item in [*self.broad_genres, *self.subgenres]
            for description in item.clap_descriptions
        ]
        tag_descriptions = [
            description
            for item in self.descriptive_tags
            for description in item.clap_descriptions
        ]
        descriptions = genre_descriptions + tag_descriptions
        if any(len(description.split()) < 6 for description in descriptions):
            raise ValueError("CLAP descriptions must be natural-language audio descriptions")
        return self


@lru_cache(maxsize=1)
def load_music_style_taxonomy() -> MusicStyleTaxonomy:
    path = Path(__file__).with_name("music_styles.v1.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return MusicStyleTaxonomy.model_validate(payload)
