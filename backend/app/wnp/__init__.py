"""Opt-in local observation hooks. WNP never owns execution or submits work."""
from .lifecycle import emit_analysis, emit_lifecycle, emit_local_run, emit_video_job, observe_worker
from .projection import ProjectionError, ProjectionResult, WNPProjection, load_wnp_kit
from .render import emit_encode_status, emit_render_job

__all__ = ["ProjectionError", "ProjectionResult", "WNPProjection", "emit_analysis",
           "emit_lifecycle", "emit_local_run", "emit_video_job", "load_wnp_kit", "observe_worker",
           "emit_encode_status", "emit_render_job"]
