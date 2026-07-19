"""Private visual-feature extraction and public Blender cue compilation."""

from .compiler import VisualCueCompilationError, compile_visual_cues
from .features import extract_full_mix_features, extract_stem_features
from .schemas import CuePreferences, TrackPromptVisualCueSheet, VisualFeatureArtifact

__all__ = [
    "CuePreferences",
    "TrackPromptVisualCueSheet",
    "VisualCueCompilationError",
    "VisualFeatureArtifact",
    "compile_visual_cues",
    "extract_full_mix_features",
    "extract_stem_features",
]
