"""Message bus module for decoupled channel-agent communication."""

from pathmind.partners.bus.events import InboundMessage, OutboundMessage
from pathmind.partners.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
