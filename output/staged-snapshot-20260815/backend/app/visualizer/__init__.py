"""Private visual-feature extraction and public Blender cue compilation."""

from .compiler import VisualCueCompilationError, compile_visual_cues
from .features import extract_full_mix_features, extract_stem_features
from .presets import (
    AbstractResolvedVisualizerConfig,
    AbstractVisualizerConfigRequest,
    ResolvedVisualizerConfig,
    SpaceJourneyResolvedVisualizerConfig,
    SpaceJourneyVisualizerConfigRequest,
    VisualizerConfigRequest,
    resolve_visualizer_config,
    validate_visualizer_config_request,
)
from .schemas import CuePreferences, TrackPromptVisualCueSheet, VisualFeatureArtifact

__all__ = [
    "CuePreferences",
    "TrackPromptVisualCueSheet",
    "AbstractResolvedVisualizerConfig",
    "AbstractVisualizerConfigRequest",
    "ResolvedVisualizerConfig",
    "SpaceJourneyResolvedVisualizerConfig",
    "SpaceJourneyVisualizerConfigRequest",
    "VisualizerConfigRequest",
    "VisualCueCompilationError",
    "VisualFeatureArtifact",
    "compile_visual_cues",
    "extract_full_mix_features",
    "extract_stem_features",
    "resolve_visualizer_config",
    "validate_visualizer_config_request",
]
