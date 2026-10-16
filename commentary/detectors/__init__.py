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
from commentary.detectors.secondary_walls import SecondaryWallDetector
from commentary.detectors.pcr_flow import PcrFlowDetector
from commentary.detectors.vwap_dynamics import VwapDynamicsDetector
from commentary.detectors.sector_breadth import SectorBreadthDetector
from commentary.detectors.straddle_decay import StraddleDecayDetector

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
    "SecondaryWallDetector",
    "PcrFlowDetector",
    "VwapDynamicsDetector",
    "SectorBreadthDetector",
    "StraddleDecayDetector",
]
