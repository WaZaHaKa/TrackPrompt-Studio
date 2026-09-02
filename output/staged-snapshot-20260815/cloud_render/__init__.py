"""Provider-neutral distributed rendering primitives for TrackPrompt Studio.

The package is deliberately safe by default: importing it never contacts a
provider, starts Blender, uploads data, or provisions infrastructure.
"""

from .models import (
    BenchmarkResult,
    BudgetLimits,
    ChunkLease,
    ChunkState,
    FrameArtifact,
    GpuOffer,
    IdentityBundle,
    WorkerKind,
)

__all__ = [
    "BenchmarkResult",
    "BudgetLimits",
    "ChunkLease",
    "ChunkState",
    "FrameArtifact",
    "GpuOffer",
    "IdentityBundle",
    "WorkerKind",
]
