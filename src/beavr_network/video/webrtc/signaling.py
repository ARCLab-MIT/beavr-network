"""Signaling servers for WebRTC connection establishment.

This module provides signaling mechanisms for WebRTC offer/answer exchange:
- ZMQSignalingServer: For VR/Unity clients using ZMQ
- HTTPSignalingServer: For browser clients using HTTP/WebSocket

Both implement a common SignalingServer interface for easy interoperability.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import zmq

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Signaling Message Types
# -----------------------------------------------------------------------------
@dataclass
class SignalingMessage:
    """A WebRTC signaling message.

    Attributes:
        msg_type: Message type ("offer", "answer", "ice", "connect", "disconnect").
        client_id: Unique identifier for the client.
        payload: Message payload (SDP or ICE candidate data).
    """

    msg_type: str
    client_id: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "type": self.msg_type,
                "client_id": self.client_id,
                "payload": self.payload,
            }
        )

    @classmethod
    def from_json(cls, json_str: str) -> "SignalingMessage":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(
            msg_type=data.get("type", ""),
            client_id=data.get("client_id", ""),
            payload=data.get("payload", {}),
        )


# -----------------------------------------------------------------------------
# Base Signaling Server
# -----------------------------------------------------------------------------
class SignalingServer(ABC):
    """Abstract base class for signaling servers."""

    @abstractmethod
    def start(self) -> bool:
        """Start the signaling server.

        Returns:
            True if started successfully.
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the signaling server."""
        pass

    @abstractmethod
    def receive_message(self) -> SignalingMessage | None:
        """Receive a signaling message (non-blocking).

        Returns:
            SignalingMessage if available, None otherwise.
        """
        pass

    @abstractmethod
    def send_message(self, message: SignalingMessage) -> bool:
        """Send a signaling message to a client.

        Args:
            message: The signaling message to send.

        Returns:
            True if sent successfully.
        """
        pass


# -----------------------------------------------------------------------------
# ZMQ Signaling Server (for VR/Unity clients)
# -----------------------------------------------------------------------------
class ZMQSignalingServer(SignalingServer):
    """ZMQ-based signaling server for VR/Unity clients.

    Uses REQ/REP pattern for simple request-response signaling.
    Each client sends an offer and receives an answer.
    """

    def __init__(self, port: int):
        """Initialize the ZMQ signaling server.

        Args:
            port: Port to bind the signaling socket.
        """
        self._port = port
        self._context: zmq.Context | None = None
        self._socket: zmq.Socket | None = None
        self._is_running = False

        # Pending response (for REQ/REP pattern)
        self._pending_client_id: str | None = None

    def start(self) -> bool:
        """Start the ZMQ signaling server."""
        try:
            self._context = zmq.Context()
            self._socket = self._context.socket(zmq.REP)
            self._socket.bind(f"tcp://*:{self._port}")
            self._socket.setsockopt(zmq.RCVTIMEO, 100)  # 100ms timeout
            self._is_running = True
            logger.info(f"ZMQ signaling server started on port {self._port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start ZMQ signaling server: {e}")
            return False

    def stop(self) -> None:
        """Stop the ZMQ signaling server."""
        self._is_running = False

        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        if self._context is not None:
            try:
                self._context.term()
            except Exception:
                pass
            self._context = None

        logger.info("ZMQ signaling server stopped")

    def receive_message(self) -> SignalingMessage | None:
        """Receive a signaling message from a client (non-blocking).

        Uses zmq.NOBLOCK to ensure this never blocks the video stream.
        """
        if not self._is_running or self._socket is None:
            return None

        try:
            # Non-blocking receive - returns immediately if no message
            raw_message = self._socket.recv_string(zmq.NOBLOCK)
            data = json.loads(raw_message)

            # Extract or generate client ID
            client_id = data.get("client_id", f"zmq_client_{id(data)}")
            self._pending_client_id = client_id

            msg_type = data.get("type", "")

            logger.debug(f"Received ZMQ message type={msg_type} from client={client_id}")

            return SignalingMessage(
                msg_type=msg_type,
                client_id=client_id,
                payload=data,
            )
        except zmq.Again:
            # No message available - this is normal and expected
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in ZMQ message: {e}")
            # Try to send error response if we have pending client
            if self._socket is not None:
                try:
                    self._socket.send_string(json.dumps({"error": "Invalid JSON"}))
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.warning(f"Error receiving ZMQ signaling message: {e}")
            return None

    def send_message(self, message: SignalingMessage) -> bool:
        """Send a response to the pending client (REQ/REP pattern).

        This must be called after receiving a message to complete the REQ/REP cycle.
        Uses non-blocking send to avoid hanging the video stream.
        """
        if not self._is_running or self._socket is None:
            logger.warning("Cannot send message: ZMQ server not running")
            return False

        if self._pending_client_id is None:
            logger.warning("Cannot send message: no pending client (REQ/REP out of sync)")
            return False

        try:
            # In REQ/REP pattern, we must send a reply
            response = {
                "type": message.msg_type,
                "client_id": message.client_id,
                **message.payload,
            }

            # Send with NOBLOCK to prevent hanging
            self._socket.send_string(json.dumps(response), zmq.NOBLOCK)
            logger.debug(f"Sent ZMQ response type={message.msg_type} to client={message.client_id}")
            self._pending_client_id = None
            return True

        except zmq.Again:
            logger.error("ZMQ send buffer full - client may be unresponsive")
            self._pending_client_id = None
            return False
        except Exception as e:
            logger.error(f"Failed to send ZMQ signaling message: {e}")
            self._pending_client_id = None
            return False


# -----------------------------------------------------------------------------
# HTTP/WebSocket Signaling Server (for browser clients)
# -----------------------------------------------------------------------------
class HTTPSignalingServer(SignalingServer):
    """HTTP/WebSocket signaling server for browser clients.

    Provides:
    - POST /offer: Client sends SDP offer, receives answer
    - WebSocket /ws: Real-time signaling for ICE candidates

    Uses aiohttp for async HTTP handling.
    """

    def __init__(
        self,
        port: int,
        host: str = "0.0.0.0",
        doc_root: str | None = None,
        info: dict[str, Any] | None = None,
    ):
        """Initialize the HTTP signaling server.

        Args:
            port: Port to bind the HTTP server.
            host: Host address to bind (default: all interfaces).
            doc_root: Optional filesystem path to serve index.html and /static assets.
        """
        self._port = port
        self._host = host
        self._doc_root = doc_root
        self._info = info or {}
        self._is_running = False

        # Message queues
        self._incoming_messages: asyncio.Queue[SignalingMessage] = asyncio.Queue()
        self._outgoing_messages: dict[str, asyncio.Queue[SignalingMessage]] = {}

        # Async event loop
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server_task: asyncio.Task | None = None
        self._app = None
        self._runner = None

        # Callback for handling offers
        self._offer_handler: Callable[[SignalingMessage], SignalingMessage | None] | None = None

    def set_offer_handler(self, handler: Callable[[SignalingMessage], SignalingMessage | None]) -> None:
        """Set the handler for processing offers.

        Args:
            handler: Callable that takes an offer message and returns an answer message.
        """
        self._offer_handler = handler

    async def _handle_offer(self, request):  # type: ignore[no-untyped-def]
        """Handle POST /offer endpoint."""
        from aiohttp import web

        try:
            data = await request.json()
            client_id = data.get("client_id", f"http_client_{id(data)}")

            # Create incoming message
            incoming = SignalingMessage(
                msg_type="offer",
                client_id=client_id,
                payload=data,
            )

            # Put in queue for processing
            await self._incoming_messages.put(incoming)

            # Wait for response (with timeout)
            # Reset outgoing queue for this client_id to avoid stale answers
            self._outgoing_messages[client_id] = asyncio.Queue()

            try:
                response = await asyncio.wait_for(
                    self._outgoing_messages[client_id].get(),
                    timeout=10.0,
                )
                return web.json_response(response.payload)
            except asyncio.TimeoutError:
                return web.json_response({"error": "Timeout waiting for answer"}, status=504)

        except Exception as e:
            logger.error(f"Error handling offer: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_ice(self, request):  # type: ignore[no-untyped-def]
        """Handle POST /ice endpoint for ICE candidates."""
        from aiohttp import web

        try:
            data = await request.json()
            client_id = data.get("client_id", "")

            incoming = SignalingMessage(
                msg_type="ice",
                client_id=client_id,
                payload=data,
            )
            await self._incoming_messages.put(incoming)

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Error handling ICE candidate: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _run_server(self) -> None:
        """Run the HTTP server."""
        from pathlib import Path

        from aiohttp import web

        self._app = web.Application()

        # Optional static/index serving
        if self._doc_root:
            doc_path = Path(self._doc_root)
            index_path = doc_path / "index.html"

            if doc_path.exists():
                self._app.router.add_static("/static/", doc_path)

            if index_path.exists():

                async def _handle_index(request):  # type: ignore[no-untyped-def]
                    return web.FileResponse(index_path)

                self._app.router.add_get("/", _handle_index)
            else:
                self._app.router.add_get("/", self._handle_index)
        else:
            self._app.router.add_get("/", self._handle_index)

        # Info endpoint for client metadata (e.g., resolution/FPS)
        async def _handle_info(request):  # type: ignore[no-untyped-def]
            return web.json_response(self._info)

        self._app.router.add_get("/info", _handle_info)

        self._app.router.add_post("/offer", self._handle_offer)
        self._app.router.add_post("/ice", self._handle_ice)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        logger.info(f"HTTP signaling server started on http://{self._host}:{self._port}")

        # Keep running until stopped
        while self._is_running:
            await asyncio.sleep(0.1)

    def start(self) -> bool:
        """Start the HTTP signaling server."""
        try:
            self._is_running = True
            self._loop = asyncio.new_event_loop()

            # Start server in background
            import threading

            def run_loop():
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._run_server())

            self._server_thread = threading.Thread(target=run_loop, daemon=True)
            self._server_thread.start()

            return True
        except Exception as e:
            logger.error(f"Failed to start HTTP signaling server: {e}")
            return False

    def stop(self) -> None:
        """Stop the HTTP signaling server."""
        self._is_running = False

        if self._runner is not None and self._loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)
                future.result(timeout=2.0)
            except Exception:
                pass

        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

        logger.info("HTTP signaling server stopped")

    def receive_message(self) -> SignalingMessage | None:
        """Receive a signaling message (non-blocking)."""
        if self._loop is None:
            return None

        try:
            return self._incoming_messages.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def send_message(self, message: SignalingMessage) -> bool:
        """Send a signaling message to a client."""
        if self._loop is None:
            return False

        try:
            client_id = message.client_id
            if client_id not in self._outgoing_messages:
                self._outgoing_messages[client_id] = asyncio.Queue()

            # Thread-safe put
            asyncio.run_coroutine_threadsafe(
                self._outgoing_messages[client_id].put(message),
                self._loop,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send HTTP signaling message: {e}")
            return False
