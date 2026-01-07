"""Simple WebRTC Stream Server for direct camera access.

This module provides a simplified WebRTC streaming server that streams
directly from a camera without shared memory or complex signaling.
"""

import asyncio
import contextlib
import fractions
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import aioice
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp
from av import VideoFrame
from numpy.typing import NDArray

from ..utils import get_shutdown_event
from .signaling import (
    HTTPSignalingServer,
    SignalingMessage,
    SignalingServer,
    ZMQSignalingServer,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Camera Protocol
# -----------------------------------------------------------------------------
class CameraProtocol(Protocol):
    """Protocol for camera objects that provide frames."""

    def async_read(self, timeout_ms: int = 200) -> NDArray[Any] | None:
        """Get the latest frame from the camera.

        Returns:
            Frame as numpy array, or None if no frame available.
        """
        ...


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
@dataclass
class WebRTCStreamServerConfig:
    """Configuration for WebRTCStreamServer.

    Attributes:
        host: Host address for signaling server.
        signaling_port: Port for HTTP signaling (browser clients) or fallback
            when ZMQ port is not provided.
        zmq_port: Port for ZMQ signaling (VR/Unity clients).
        framerate: Target framerate for WebRTC streams.
        resolution: Optional (width, height) for display purposes.
        doc_root: Optional path to serve static web client assets.
        signaling_type: Signaling transport ('http' or 'zmq').
        codec: Video codec to prefer ('H264' or 'VP8').
    """

    host: str = "0.0.0.0"
    signaling_port: int = 8080
    zmq_port: int | None = None
    framerate: int = 30
    resolution: tuple[int, int] | None = None
    doc_root: str | None = None
    signaling_type: Literal["http", "zmq"] = "http"
    codec: Literal["H264", "VP8"] = "H264"


# -----------------------------------------------------------------------------
# WebRTC Client Connection
# -----------------------------------------------------------------------------
@dataclass
class ClientConnection:
    """Represents a connected WebRTC client.

    Attributes:
        peer_connection: RTCPeerConnection for this client.
        video_track: Video track being sent to this client.
        connected_at: Timestamp when client connected.
    """

    peer_connection: RTCPeerConnection
    video_track: "CameraVideoTrack"
    connected_at: float


# -----------------------------------------------------------------------------
# Video Track that reads from camera
# -----------------------------------------------------------------------------
class CameraVideoTrack(MediaStreamTrack):
    """Video track that streams frames directly from a camera.

    Implements proper frame pacing to ensure frames are sent at the correct
    rate without flooding the network or blocking the event loop.
    """

    kind = "video"

    def __init__(self, camera: CameraProtocol, framerate: int = 30):
        """Initialize the video track.

        Args:
            camera: Camera object that provides frames.
            framerate: Target framerate for the video stream.
        """
        super().__init__()
        self._camera = camera
        self._framerate = framerate

        # Timing for frame pacing (similar to aiortc's PlayerStreamTrack)
        self._start_time: float | None = None  # When streaming started
        self._timestamp = 0  # Frame counter for PTS calculation
        self._frame_time = 1.0 / framerate  # Time per frame in seconds

    async def recv(self) -> VideoFrame:
        """Receive the next video frame for WebRTC transmission.

        This method implements proper frame pacing:
        - Prevents network flooding by rate-limiting to the target framerate
        - Yields control to the event loop via asyncio.sleep(), allowing aiortc
          to process ICE/DTLS messages and keep-alives
        - Ensures smooth streaming without stalls

        Frame pacing pattern is based on aiortc's PlayerStreamTrack implementation.
        """
        # Calculate expected time for this frame
        expected_time = self._timestamp * self._frame_time

        # Frame pacing: wait until it's time to send this frame
        if self._start_time is None:
            # First frame: record start time (adjusted for current timestamp)
            self._start_time = time.time() - expected_time
        else:
            # Calculate how long to wait before sending this frame
            target_time = self._start_time + expected_time
            wait_time = target_time - time.time()

            # CRITICAL: asyncio.sleep() yields control to event loop
            # This allows aiortc to process ICE/DTLS and prevents flooding
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        # Get frame from camera
        frame = self._camera.async_read(timeout_ms=200)

        if frame is None:
            # Return black frame if no frame available
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # Create VideoFrame (expects RGB)
        video_frame = VideoFrame.from_ndarray(frame, format="rgb24")

        # Set presentation timestamp and time base
        # PTS is frame number, time_base is 1/framerate
        video_frame.pts = self._timestamp
        video_frame.time_base = fractions.Fraction(1, self._framerate)

        # Increment timestamp for next frame
        self._timestamp += 1

        return video_frame


# -----------------------------------------------------------------------------
# WebRTC Stream Server
# -----------------------------------------------------------------------------
class WebRTCStreamServer:
    """Simple WebRTC streaming server that streams directly from a camera."""

    def __init__(self, camera: CameraProtocol, config: WebRTCStreamServerConfig):
        """Initialize the WebRTC stream server.

        Args:
            camera: Camera object to stream from.
            config: Configuration for the server.
        """
        self._camera = camera
        self._config = config

        # Guard against aioice sending STUN on a closed transport. We patch once
        # per process to drop retries after transport teardown instead of
        # crashing with AttributeError when sendto is invoked on None.
        self._patch_aioice_send_stun()

        # Signaling server
        self._signaling_server: SignalingServer | None = None

        # Client connection (single client for simplicity)
        self._client: ClientConnection | None = None

        # Async event loop for WebRTC
        self._loop: asyncio.AbstractEventLoop | None = None

        # Shutdown event
        self._shutdown_event = get_shutdown_event()

        # Prevent double cleanup when stop is called from multiple places
        self._cleanup_started = False

    @staticmethod
    def _patch_aioice_send_stun() -> None:
        """Monkey-patch aioice to ignore STUN retries after transport closes."""
        try:
            proto_cls = aioice.ice.StunProtocol
        except Exception:
            return

        if getattr(proto_cls, "_beavr_safe_send_stun", False):
            return

        original_send_stun = proto_cls.send_stun

        def safe_send_stun(self, message, addr):
            transport = getattr(self, "transport", None)
            if transport is None:
                logger.debug("Dropped STUN send: transport already closed")
                return
            try:
                return original_send_stun(self, message, addr)
            except AttributeError as exc:
                # Occurs when transport._sock is None after closure; suppress.
                if "sendto" in str(exc):
                    logger.debug("Ignored STUN send after transport shutdown")
                    return
                raise

        proto_cls.send_stun = safe_send_stun  # type: ignore[assignment]
        proto_cls._beavr_safe_send_stun = True  # type: ignore[attr-defined]

    # -------------------------------------------------------------------------
    # Signaling
    # -------------------------------------------------------------------------
    def _start_signaling_server(self) -> bool:
        """Start the configured signaling server (HTTP or ZMQ)."""

        if self._config.signaling_type == "zmq":
            port = self._config.zmq_port or self._config.signaling_port
            self._signaling_server = ZMQSignalingServer(port=port)

            if not self._signaling_server.start():
                logger.error("Failed to start ZMQ signaling server")
                self._signaling_server = None

                return False

            logger.info(f"ZMQ signaling server started on port {port}")

            return True

        self._signaling_server = HTTPSignalingServer(
            port=self._config.signaling_port,
            host=self._config.host,
            info={
                "resolution": self._config.resolution,
                "fps": self._config.framerate,
            },
            doc_root=self._config.doc_root,
        )

        self._signaling_server.set_offer_handler(self._handle_offer_sync)  # type: ignore[attr-defined]

        if not self._signaling_server.start():
            logger.error("Failed to start HTTP signaling server")
            self._signaling_server = None

            return False

        logger.info(f"HTTP signaling server started on port {self._config.signaling_port}")

        return True

    def _stop_signaling_server(self) -> None:
        """Stop the signaling server."""
        if self._signaling_server is not None:
            self._signaling_server.stop()
            self._signaling_server = None

    def _handle_offer_sync(self, message: SignalingMessage) -> SignalingMessage | None:
        """Handle WebRTC offer synchronously (wrapper for async handler).

        Args:
            message: Offer message.

        Returns:
            Answer message.
        """
        if self._loop is None:
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._handle_offer_async(message),
            self._loop,
        )
        try:
            return future.result(timeout=10.0)
        except Exception as e:
            logger.error(f"Error handling offer: {e}")
            return None

    async def _handle_ice_async(self, message: SignalingMessage) -> None:
        """Handle ICE candidate messages."""
        candidate_sdp = message.payload.get("candidate")
        if self._client is None or not candidate_sdp:
            return

        ice = candidate_from_sdp(candidate_sdp)
        ice.sdpMid = message.payload.get("sdpMid")
        ice.sdpMLineIndex = message.payload.get("sdpMLineIndex")

        try:
            await self._client.peer_connection.addIceCandidate(ice)
        except Exception as e:
            logger.warning(f"Error adding ICE candidate: {e}")

    async def _handle_offer_async(self, message: SignalingMessage) -> SignalingMessage | None:
        """Handle WebRTC offer asynchronously.

        Args:
            message: Offer message.

        Returns:
            Answer message.
        """
        client_id = message.client_id

        # Close existing connection if any (Unity reconnection after stall detection)
        if self._client is not None:
            old_client_state = self._client.peer_connection.connectionState
            logger.info(
                f"Closing existing client connection (state: {old_client_state}) "
                f"to accept new offer from {client_id}"
            )
            await self._close_client_connection()
            # Small delay to ensure cleanup completes
            await asyncio.sleep(0.1)

        try:
            # Create peer connection
            pc = RTCPeerConnection()
            server_candidates: list[dict[str, Any]] = []

            # Create video track
            video_track = CameraVideoTrack(
                camera=self._camera,
                framerate=self._config.framerate,
            )

            # Add transceiver with codec preferences
            transceiver = pc.addTransceiver(video_track, direction="sendonly")

            # Set codec preferences based on configuration
            # CRITICAL: Unity Software Encoder requires VP8 exclusively
            if self._config.codec == "VP8":
                caps = RTCRtpSender.getCapabilities("video")

                # Filter for VP8 ONLY (no H264 fallback to prevent Unity crashes)
                vp8_codecs = [c for c in caps.codecs if c.name.lower() == "vp8"]

                if not vp8_codecs:
                    logger.error("VP8 codec not available on this system!")
                    raise RuntimeError("VP8 codec required but not available")

                # Apply preferences: VP8 ONLY
                transceiver.setCodecPreferences(vp8_codecs)
                logger.info(f"Enforcing VP8 codec exclusively ({len(vp8_codecs)} variants available)")
            else:
                # For H264, allow both H264 and VP8 as fallback
                caps = RTCRtpSender.getCapabilities("video")
                h264_codecs = [c for c in caps.codecs if c.name.lower() == "h264"]
                vp8_codecs = [c for c in caps.codecs if c.name.lower() == "vp8"]

                if h264_codecs:
                    transceiver.setCodecPreferences(h264_codecs + vp8_codecs)
                    logger.info(f"Preferring H264 codec ({len(h264_codecs)} variants available)")

            # Set up event handlers
            @pc.on("connectionstatechange")
            async def on_connectionstatechange():
                state = pc.connectionState
                logger.info(f"Client {client_id} connection state changed: {state}")

                # Handle connection failures (Unity stall detection or network issues)
                if state == "failed":
                    logger.warning(
                        f"Client {client_id} connection FAILED - Unity may have detected stall. "
                        "Ready for reconnection."
                    )
                    await self._close_client_connection()
                elif state == "closed":
                    logger.info(f"Client {client_id} connection CLOSED gracefully")
                    await self._close_client_connection()
                elif state == "connected":
                    logger.info(f"Client {client_id} successfully CONNECTED - streaming active")
                elif state == "connecting":
                    logger.debug(f"Client {client_id} is CONNECTING...")

            @pc.on("iceconnectionstatechange")
            async def on_iceconnectionstatechange():
                state = pc.iceConnectionState
                if state in ("failed", "disconnected", "closed"):
                    logger.warning(f"Client {client_id} ICE connection state: {state}")
                elif state == "connected" or state == "completed":
                    logger.info(f"Client {client_id} ICE connection: {state}")
                else:
                    logger.debug(f"Client {client_id} ICE state: {state}")

            @pc.on("icecandidate")
            async def on_icecandidate(candidate: Any):
                if candidate:
                    server_candidates.append(
                        {
                            "candidate": candidate.to_sdp(),
                            "sdpMid": candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex,
                        }
                    )

            # Set remote description (offer)
            offer_sdp = message.payload.get("sdp", "")
            offer = RTCSessionDescription(sdp=offer_sdp, type="offer")
            await pc.setRemoteDescription(offer)

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Store client connection
            self._client = ClientConnection(
                peer_connection=pc,
                video_track=video_track,
                connected_at=time.time(),
            )

            logger.info(f"Client {client_id} connected")

            return SignalingMessage(
                msg_type="answer",
                client_id=client_id,
                payload={
                    "type": "answer",
                    "sdp": pc.localDescription.sdp,
                    "candidates": server_candidates,
                },
            )

        except Exception as e:
            logger.error(f"Error handling offer from {client_id}: {e}")
            return SignalingMessage(
                msg_type="error",
                client_id=client_id,
                payload={"error": str(e)},
            )

    async def _close_client_connection(self) -> None:
        """Close the client connection and clean up resources.

        This is called when:
        - Unity client detects stall and disconnects
        - Connection state changes to 'failed' or 'closed'
        - New client connects (replaces existing connection)
        """
        if self._client is None:
            return

        client = self._client
        self._client = None

        try:
            # Stop the video track to prevent sending to dead connection
            if client.video_track:
                client.video_track.stop()
                logger.debug("Stopped video track")

            # Close the peer connection
            connection_state = client.peer_connection.connectionState
            if connection_state not in ("closed", "failed"):
                await client.peer_connection.close()
                logger.info(f"Client disconnected (was in {connection_state} state)")
            else:
                logger.info(f"Client connection already {connection_state}")

        except Exception as e:
            logger.warning(f"Error closing client connection: {e}")

    # -------------------------------------------------------------------------
    # WebRTC Event Loop
    # -------------------------------------------------------------------------
    async def _webrtc_main_loop(self) -> None:
        """Main WebRTC loop - process signaling messages.

        This loop handles:
        - WebRTC offer/answer exchange (initial connection)
        - ICE candidate exchange (connection establishment)
        - Unity reconnections after stall detection

        Uses non-blocking ZMQ/HTTP signaling to avoid blocking video stream.
        """
        logger.info("WebRTC main loop started")
        consecutive_errors = 0
        max_consecutive_errors = 10

        while not self._shutdown_event.is_set():
            try:
                # Process signaling messages (non-blocking)
                if self._signaling_server is not None:
                    message = self._signaling_server.receive_message()
                    if message is not None:
                        logger.debug(f"Processing {message.msg_type} message from {message.client_id}")

                        response: SignalingMessage | None = None
                        if message.msg_type == "offer":
                            response = await self._handle_offer_async(message)
                        elif message.msg_type == "ice":
                            await self._handle_ice_async(message)
                        else:
                            logger.warning(f"Unknown message type: {message.msg_type}")

                        # Send response (required for ZMQ REQ/REP pattern)
                        if response is not None:
                            if not self._signaling_server.send_message(response):
                                logger.error("Failed to send signaling response")

                # Reset error counter on successful iteration
                consecutive_errors = 0

                # Small sleep to prevent busy-waiting (10ms = 100 checks/sec)
                await asyncio.sleep(0.01)

            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    f"Error in WebRTC main loop ({consecutive_errors}/{max_consecutive_errors}): {e}",
                    exc_info=True,
                )

                # If too many consecutive errors, something is seriously wrong
                if consecutive_errors >= max_consecutive_errors:
                    logger.critical("Too many consecutive errors in WebRTC loop - shutting down")
                    self._shutdown_event.set()
                    break

                await asyncio.sleep(0.1)

        logger.info("WebRTC main loop stopped")

    # -------------------------------------------------------------------------
    # Server Lifecycle
    # -------------------------------------------------------------------------
    def stream(self) -> None:
        """Main streaming loop."""
        try:
            # Create and run WebRTC event loop
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # Start signaling server
            if not self._start_signaling_server():
                logger.error("Failed to start signaling server")
                return

            logger.info(
                "WebRTCStreamServer started using %s signaling on port %s",
                self._config.signaling_type,
                self._config.zmq_port
                if self._config.signaling_type == "zmq"
                else self._config.signaling_port,
            )

            # Run the main WebRTC loop
            self._loop.run_until_complete(self._webrtc_main_loop())

        except KeyboardInterrupt:
            logger.info("WebRTCStreamServer received shutdown signal")
        finally:
            self.cleanup()

    def request_shutdown(self) -> None:
        """Signal the server to stop without closing the loop immediately.

        This is used by callers on other threads (e.g., the app wrapper) to
        request a graceful exit. The actual cleanup is done inside the loop
        thread when `stream()` unwinds.
        """
        self._shutdown_event.set()
        if self._loop is not None and self._loop.is_running():
            # Wake the loop so it can observe the shutdown flag promptly.
            self._loop.call_soon_threadsafe(lambda: None)

    def cleanup(self) -> None:
        """Clean up all resources."""
        if self._cleanup_started:
            return

        if self._loop is not None and self._loop.is_running():
            # Cleanup is only safe once the loop is finished; let the loop exit
            # naturally after the shutdown flag is observed.
            logger.warning("Cleanup called while event loop is running; deferring")
            return

        self._cleanup_started = True
        logger.info("WebRTCStreamServer cleanup starting...")

        self._shutdown_event.set()

        # Close client connection
        if self._loop is not None and self._client is not None:
            try:
                if self._loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._close_client_connection(),
                        self._loop,
                    )
                    future.result(timeout=1.0)
                else:
                    self._loop.run_until_complete(self._close_client_connection())
            except Exception as e:
                logger.warning(f"Error closing client connection: {e}")

        # Stop signaling server
        self._stop_signaling_server()

        # Close event loop
        if self._loop is not None:
            # Cancel any pending tasks to avoid "Task was destroyed" warnings
            pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                with contextlib.suppress(Exception):
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())

            self._loop.close()
            self._loop = None

        logger.info("WebRTCStreamServer cleanup complete")

    def get_client_count(self) -> int:
        """Get the number of connected clients.

        Returns:
            Number of active client connections (0 or 1).
        """
        return 1 if self._client is not None else 0

    def get_client_info(self) -> list[dict[str, Any]]:
        """Get information about the connected client.

        Returns:
            List with client info dictionary (empty if no client).
        """
        if self._client is None:
            return []

        return [
            {
                "connected_at": self._client.connected_at,
                "connection_state": self._client.peer_connection.connectionState,
            }
        ]
