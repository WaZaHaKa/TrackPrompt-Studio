from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MissionControlConfig:
    repository_root: Path
    state_root: Path
    profile_root: Path
    calibration_root: Path
    default_output_root: Path
    allow_fake_renderer: bool = False
    native_dialog_enabled: bool = True
    event_retention: int = 50_000

    @classmethod
    def from_repository(
        cls,
        repository_root: Path,
        *,
        state_root: Path | None = None,
        allow_fake_renderer: bool | None = None,
        native_dialog_enabled: bool = True,
    ) -> MissionControlConfig:
        root = repository_root.resolve()
        fake_enabled = (
            os.getenv("TRACKPROMPT_MC_ALLOW_FAKE_RENDERER", "").strip().lower()
            in {"1", "true", "yes", "on"}
            if allow_fake_renderer is None
            else allow_fake_renderer
        )
        profile_root = Path(os.getenv("TRACKPROMPT_MC_PROFILE_ROOT", str(root / "render-profiles")))
        calibration_root = Path(
            os.getenv(
                "TRACKPROMPT_MC_CALIBRATION_ROOT",
                str(root / "test-output" / "render-calibration"),
            )
        )
        output_root = Path(os.getenv("TRACKPROMPT_MC_OUTPUT_ROOT", str(root / "final-output")))
        configured_state_root = Path(
            os.getenv(
                "TRACKPROMPT_MC_STATE_ROOT",
                str(state_root or root / ".trackprompt-data" / "mission-control"),
            )
        )
        return cls(
            repository_root=root,
            state_root=configured_state_root.resolve(),
            profile_root=profile_root.resolve(),
            calibration_root=calibration_root.resolve(),
            default_output_root=output_root.resolve(),
            allow_fake_renderer=fake_enabled,
            native_dialog_enabled=native_dialog_enabled,
        )

    def ensure_directories(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.state_root / "mission-control.sqlite3"

    @property
    def calibration_plan_root(self) -> Path:
        return self.state_root / "calibration-plans"
