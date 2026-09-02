from __future__ import annotations

from ...config import Settings
from ..schemas import (
    RendererDescriptor,
    SpectrumWorkspaceJob,
    SpectrumWorkspacePrepareRequest,
)
from .capture import SpectrumProductionManager
from .preflight import (
    SpectrumInspection,
    SpectrumPaths,
    inspect_wzhk_spectrum,
)
from .workspace import load_workspace_job, prepare_workspace


class WzhkSpectrumRenderer:
    renderer_id = "wzhk-spectrum"

    def __init__(
        self,
        settings: Settings,
        paths: SpectrumPaths,
        inspection: SpectrumInspection | None = None,
    ) -> None:
        self._settings = settings
        self.paths = paths
        self._inspection = inspection
        self.production = SpectrumProductionManager(
            settings,
            paths,
            inspection,
        )

    def descriptor(self) -> RendererDescriptor:
        return inspect_wzhk_spectrum(
            self._settings,
            self.paths,
            self._inspection,
        ).descriptor

    def prepare(
        self,
        request: SpectrumWorkspacePrepareRequest,
    ) -> SpectrumWorkspaceJob:
        outcome = inspect_wzhk_spectrum(
            self._settings,
            self.paths,
            self._inspection,
        )
        return prepare_workspace(self.paths, outcome, request)

    def get_job(self, job_id: str) -> SpectrumWorkspaceJob:
        return load_workspace_job(self.paths, job_id)

    def capture_preflight(self, job_id: str) -> SpectrumWorkspaceJob:
        return self.production.preflight(job_id)

    def start_production(self, job_id: str) -> SpectrumWorkspaceJob:
        return self.production.start(job_id)

    def cancel_production(self, job_id: str, reason: str) -> SpectrumWorkspaceJob:
        return self.production.cancel(job_id, reason)
