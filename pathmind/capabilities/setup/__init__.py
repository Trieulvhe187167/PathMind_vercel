"""Setup loop capability — PathMind inspecting and changing its own configuration."""

from pathmind.capabilities.setup.capability import SetupCapability
from pathmind.capabilities.setup.tools import SETUP_TOOL_NAMES, SETUP_TOOL_TYPES

__all__ = ["SETUP_TOOL_NAMES", "SETUP_TOOL_TYPES", "SetupCapability"]
