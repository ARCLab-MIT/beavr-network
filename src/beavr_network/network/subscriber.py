from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

import cv2
import zmq

from .serialization import FlatBufferSerializer, Serializer
from .utils import get_global_context

T = TypeVar("T")

logger = logging.getLogger(__name__)


class BaseSubscriber(threading.Thread, ABC, Generic[T]):
    """Base class for all subscribers with graceful shutdown support."""

    def __init__(
        self,
        host: str,
        port: int,
        topic: str = "",
        socket_type: int = zmq.SUB,
        context: zmq.Context | None = None,
        serializer: Serializer[T] | None = None,
        bind: bool = False,
    ):
        """Initialize subscriber.

        Args:
            host: The host address to connect/bind to
            port: The port number to connect/bind to
            topic: The topic to subscribe to (empty for all topics)
            socket_type: The ZMQ socket type (default: SUB)
            context: Optional custom ZMQ context (default: global context)
            serializer: Optional serializer for payloads; defaults to FlatBufferSerializer[T]
            bind: Whether to bind instead of connect (default: False)
        """
        super().__init__(daemon=True)
        self._host = host
        self._port = port
        self._topic = topic
        self._socket: zmq.Socket | None = None
        self._running = True
        self._poller: zmq.Poller | None = None
        self._context = context or get_global_context()
        # Store socket configuration for creation in worker thread
        self._socket_type = socket_type
        self._bind = bind
        # Decoder/encoder for payloads; default to FlatBufferSerializer with expected_type
        self._serializer: Serializer[T] = serializer or FlatBufferSerializer(
            root_accessor=None
        )

    def _init_socket(self, socket_type: int) -> None:
        """Initialize the socket in the worker thread."""
        try:
            self._socket = self._context.socket(socket_type)

            # Configure socket for real-time control
            self._socket.setsockopt(zmq.RCVHWM, 5)  # Keep last 5 messages
            self._socket.setsockopt(zmq.LINGER, 0)  # Don't wait on close
            self._socket.setsockopt(zmq.RCVTIMEO, 50)  # 50ms timeout on receive

            # Enable message conflation for non-SUB sockets
            if socket_type != zmq.SUB:
                self._socket.setsockopt(zmq.CONFLATE, 1)

            # For SUB sockets, we handle conflation in the application layer
            # to avoid issues with topic filtering

            addr = f"tcp://{self._host}:{self._port}"
            if self._bind:
                self._socket.bind(addr)
            else:
                self._socket.connect(addr)

            if socket_type == zmq.SUB:
                self._socket.setsockopt_string(zmq.SUBSCRIBE, self._topic)

            # Create poller and register socket
            self._poller = zmq.Poller()
            self._poller.register(self._socket, zmq.POLLIN)

        except zmq.ZMQError as e:
            logger.error(f"Failed to initialize socket: {e}")
            raise

    @property
    def topic(self) -> str:
        """Get the topic this subscriber is subscribed to."""
        return self._topic

    def stop(self) -> None:
        """Stop the subscriber thread gracefully."""
        self._running = False
        if self._socket and self._poller:
            try:
                self._poller.unregister(self._socket)
                self._socket.close()
                self._socket = None
                self._poller = None
            except Exception as e:
                logger.warning(f"Error closing socket: {e}")
        # Reduced timeout since poll loop checks every 100ms
        # 500ms should be sufficient for graceful shutdown
        self.join(timeout=0.5)
        if self.is_alive():
            logger.warning("Subscriber thread did not stop gracefully")

    @abstractmethod
    def cache(self, topic: str, message: T) -> None:
        """Cache received message.

        Args:
            topic: The topic the message was received on
            message: The received message data
        """
        pass

    def run(self) -> None:
        """Main subscriber loop with socket creation in worker thread."""
        try:
            # Create socket in the worker thread
            self._init_socket(self._socket_type)

            # Initialize poll timeout logging timestamp once
            last_poll_log = 0.0

            while self._running:
                try:
                    if self._socket is None or self._poller is None:
                        logger.error(
                            "Socket or poller is None, breaking subscriber loop"
                        )
                        break

                    # Poll with timeout to check _running periodically
                    # Note: poll() returns list of (socket, event) tuples; no dict() conversion needed
                    events = self._poller.poll(100)  # 100ms timeout

                    if events:  # If any events occurred
                        try:
                            topic_bytes, payload = self._socket.recv_multipart(
                                zmq.NOBLOCK
                            )
                            topic = topic_bytes.decode("utf-8")
                            try:
                                data_typed = self._serializer.decode(payload)
                                self.cache(topic, data_typed)
                            except Exception as e:
                                logger.error(f"Failed to cache message: {e}")
                        except zmq.Again:
                            continue
                        except Exception as e:
                            if self._running:  # Only log if we're not shutting down
                                logger.error(f"Error receiving message: {e}")
                            break
                    else:
                        # Log poll timeout occasionally (every 5 seconds)
                        current_time = time.time()
                        if current_time - last_poll_log > 5.0:
                            last_poll_log = current_time

                except Exception as e:
                    if self._running:
                        logger.error(f"Error in subscriber loop: {e}")
                    break
        finally:
            # Ensure socket is closed when thread exits
            if self._socket:
                try:
                    self._socket.close()
                except Exception as e:
                    logger.warning(f"Error closing socket in cleanup: {e}")
                self._socket = None


class ZMQCompressedImageReceiver(BaseSubscriber):
    """Subscriber for compressed images using multipart messaging."""

    def __init__(self, host: str, port: int):
        """Initialize compressed image receiver.

        Args:
            host: The host address to connect to
            port: The port number to connect to
        """
        from .serialization import RawBytesSerializer

        super().__init__(host, port, "image", zmq.SUB, serializer=RawBytesSerializer())
        self._last_image: np.ndarray | None = None
        self.start()  # Start the subscriber thread

    def cache(self, topic: str, data: bytes) -> None:
        """Cache received image data.

        Args:
            topic: The topic the image was received on
            data: Raw bytes of compressed image data
        """
        encoded_data = np.frombuffer(data, np.uint8)
        self._last_image = cv2.imdecode(encoded_data, 1)

    def receive(self) -> np.ndarray | None:
        """Get the latest received image.

        Returns:
            The latest image array if available, None otherwise
        """
        return self._last_image


class ZMQSubscriber(BaseSubscriber[T]):
    """Subscriber for data using multipart messaging."""

    def __init__(
        self,
        host: str,
        port: int,
        topic: str,
        context: zmq.Context | None = None,
        serializer: Serializer[T] | None = None,
        bind: bool = False,
    ):
        super().__init__(
            host,
            port,
            topic,
            zmq.SUB,
            context,
            serializer=serializer,
            bind=bind,
        )
        self._last_data: T | None = None
        self.start()

    def cache(self, topic: str, data: T) -> None:
        """Cache received data.

        Args:
            topic: The topic the data was received on
            data: The received data
        """
        self._last_data = data

    def receive(self) -> T | None:
        """Get the latest data.

        Returns:
            The latest data if available, None otherwise
        """
        return self._last_data


class ZMQButtonFeedbackSubscriber(BaseSubscriber[T], Generic[T]):
    """Subscriber for button/feedback using multipart messaging."""

    def __init__(
        self,
        host: str,
        port: int,
        context: zmq.Context | None = None,
        serializer: Serializer[T] | None = None,
        bind: bool = False,
    ):
        super().__init__(
            host,
            port,
            "",
            zmq.SUB,
            context,
            serializer=serializer,
            bind=bind,
        )  # Empty topic to receive all messages
        self._last_data: T | None = None
        self.start()

    def cache(self, topic: str, data: T) -> None:
        """Cache received button feedback data.

        Args:
            topic: The topic the button feedback was received on
            data: The received button feedback data
        """
        self._last_data = data

    def receive(self) -> T | None:
        """Get the latest button feedback data.

        Returns:
            The latest button feedback data if available, None otherwise
        """
        return self._last_data
