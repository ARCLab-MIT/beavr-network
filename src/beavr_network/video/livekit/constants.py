"""Constants for LiveKit streaming.

Defines the 'Golden Values' for high-quality simulation streaming to ensure
consistency across beavr-sim, beavr-network, and beavr-configs.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StreamProfile:
    """Standard quality profile for simulation streaming."""

    width: int
    height: int
    fps: int
    bitrate: int


# -----------------------------------------------------------------------------
# HIGH QUALITY (1440p 60fps) - The Gold Standard
# -----------------------------------------------------------------------------
# Physics (480Hz) / Stream (60fps) = exactly 8 steps per frame
ULTRA_QUALITY = StreamProfile(
    width=2560,
    height=1440,
    fps=60,
    bitrate=8_000_000,  # 8 Mbps
)

# -----------------------------------------------------------------------------
# BALANCED (720p 30fps) - For lower bandwidth
# -----------------------------------------------------------------------------
# Physics (240Hz) / Stream (30fps) = exactly 8 steps per frame
BALANCED_QUALITY = StreamProfile(
    width=1280,
    height=720,
    fps=30,
    bitrate=3_000_000,  # 3 Mbps
)

RECORDING_QUALITY = StreamProfile(
    width=640,
    height=480,
    fps=30,
    bitrate=3_000_000,  # 3 Mbps
)

# Current default for the system
DEFAULT_PROFILE = RECORDING_QUALITY
