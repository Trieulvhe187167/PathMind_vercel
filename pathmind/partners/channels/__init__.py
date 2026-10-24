"""Chat channels module with plugin architecture."""

from pathmind.partners.channels.base import BaseChannel
from pathmind.partners.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
