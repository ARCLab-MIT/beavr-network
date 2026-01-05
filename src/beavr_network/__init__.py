"""beavr_network - ZMQ transport and FlatBuffer schemas for Beavr.

This package provides:
- ZMQ publishers, subscribers, and handshake utilities
- FlatBuffer schemas for teleop communication

Note: RobotId, SimManifest, TopicBuilder, enum_mappings, and protocol constants
have moved to beavr_configs to achieve a clean dependency graph.
"""

from __future__ import annotations

from .network.handshake import (
    HandshakeClient,
    HandshakeCoordinator,
    HandshakeServer,
    publish_with_guaranteed_delivery,
)
from .network.publisher import BasePublisher, PublisherThread, ZMQPublisherManager
from .network.subscriber import BaseSubscriber, MultiplexedZMQSubscriber
from .network.utils import (
    SerializationError,
    cleanup_zmq_resources,
    get_global_context,
    set_global_context,
)

__version__ = "0.1.0"

__all__ = [
    # ZMQ Transport
    "BasePublisher",
    "BaseSubscriber",
    "HandshakeClient",
    "HandshakeCoordinator",
    "HandshakeServer",
    "MultiplexedZMQSubscriber",
    "PublisherThread",
    "SerializationError",
    "ZMQPublisherManager",
    "cleanup_zmq_resources",
    "get_global_context",
    "publish_with_guaranteed_delivery",
    "set_global_context",
]
