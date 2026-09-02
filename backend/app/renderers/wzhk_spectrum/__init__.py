"""WZHK Spectrum renderer contracts, design, preflight, and workspace materialization."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .preflight import SpectrumInspection
    from .service import WzhkSpectrumRenderer

__all__ = ["SpectrumInspection", "WzhkSpectrumRenderer"]


def __getattr__(name: str) -> object:
    if name == "SpectrumInspection":
        from .preflight import SpectrumInspection

        return SpectrumInspection
    if name == "WzhkSpectrumRenderer":
        from .service import WzhkSpectrumRenderer

        return WzhkSpectrumRenderer
    raise AttributeError(name)
