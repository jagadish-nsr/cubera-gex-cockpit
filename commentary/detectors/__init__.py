"""commentary/detectors — Event Detectors Package."""

from __future__ import annotations

from commentary.detectors.base import BaseDetector
from commentary.detectors.gap import OpeningGapDetector
from commentary.detectors.walls import WallGexShiftDetector
from commentary.detectors.session_structure import SessionStructureDetector
from commentary.detectors.divergences import DivergenceDetector
from commentary.detectors.impulse import ImpulsiveMoveDetector
from commentary.detectors.volatility import VolatilityRegimeDetector
from commentary.detectors.expiry import ExpiryDynamicsDetector
from commentary.detectors.footprint import InstitutionalFootprintDetector
from commentary.detectors.news import NewsCatalystDetector
from commentary.detectors.pinned import PinnedPositionDetector

__all__ = [
    "BaseDetector",
    "OpeningGapDetector",
    "WallGexShiftDetector",
    "SessionStructureDetector",
    "DivergenceDetector",
    "ImpulsiveMoveDetector",
    "VolatilityRegimeDetector",
    "ExpiryDynamicsDetector",
    "InstitutionalFootprintDetector",
    "NewsCatalystDetector",
    "PinnedPositionDetector",
]
