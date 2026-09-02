from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .schemas import (
    RendererAvailabilityState,
    RendererDescriptor,
    RendererRegistryResponse,
    SpectrumWorkspaceJob,
    SpectrumWorkspacePrepareRequest,
)
from .wzhk_spectrum.preflight import SpectrumInspection, SpectrumPaths
from .wzhk_spectrum.service import WzhkSpectrumRenderer


class RendererRegistry:
    def __init__(self, wzhk_spectrum: WzhkSpectrumRenderer) -> None:
        self.wzhk_spectrum = wzhk_spectrum

    @staticmethod
    def _blender_descriptor() -> RendererDescriptor:
        return RendererDescriptor(
            renderer_id="blender",
            display_name="Blender Visualizer",
            description=(
                "Existing deterministic cue-sheet and visualizer-configuration export path. Blender execution remains operator-controlled."
            ),
            platform="cross-platform",
            capabilities=["visual-cue-export", "configuration-export"],
            availability=RendererAvailabilityState.READY,
            available=True,
            preparation_available=True,
            warnings=[],
            requirements=[],
        )

    def list(self) -> RendererRegistryResponse:
        return RendererRegistryResponse(
            renderers=[self._blender_descriptor(), self.wzhk_spectrum.descriptor()]
        )

    def get(self, renderer_id: str) -> RendererDescriptor:
        if renderer_id == "blender":
            return self._blender_descriptor()
        if renderer_id == self.wzhk_spectrum.renderer_id:
            return self.wzhk_spectrum.descriptor()
        raise KeyError(renderer_id)

    def prepare_wzhk_spectrum(
        self,
        request: SpectrumWorkspacePrepareRequest,
    ) -> SpectrumWorkspaceJob:
        return self.wzhk_spectrum.prepare(request)

    def get_wzhk_spectrum_job(self, job_id: str) -> SpectrumWorkspaceJob:
        return self.wzhk_spectrum.get_job(job_id)

    def preflight_wzhk_spectrum_capture(self, job_id: str) -> SpectrumWorkspaceJob:
        return self.wzhk_spectrum.capture_preflight(job_id)

    def start_wzhk_spectrum_production(self, job_id: str) -> SpectrumWorkspaceJob:
        return self.wzhk_spectrum.start_production(job_id)

    def cancel_wzhk_spectrum_production(
        self,
        job_id: str,
        reason: str,
    ) -> SpectrumWorkspaceJob:
        return self.wzhk_spectrum.cancel_production(job_id, reason)


def create_renderer_registry(
    settings: Settings,
    *,
    repository_root: Path | None = None,
    spectrum_inspection: SpectrumInspection | None = None,
) -> RendererRegistry:
    resolved_repository_root = (
        repository_root or Path(__file__).resolve().parents[3]
    ).resolve()
    paths = SpectrumPaths(
        repository_root=resolved_repository_root,
        data_root=settings.data_dir.resolve(),
    )
    return RendererRegistry(
        WzhkSpectrumRenderer(settings, paths, spectrum_inspection)
    )
