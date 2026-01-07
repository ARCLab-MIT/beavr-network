"""LiveKit-based streaming server.

Publishes camera frames to a LiveKit room for web browser consumption.
Uses the production-grade LiveKit SDK instead of manual aiortc WebRTC.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from beavr_configs.network.defaults import DEFAULT_PROFILE
from livekit import api, rtc
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Camera Protocol (same interface as aiortc implementation)
# -----------------------------------------------------------------------------
class CameraProtocol(Protocol):
    """Protocol for camera objects that provide frames."""

    def async_read(self, timeout_ms: int = 200) -> NDArray[Any] | None:
        """Get the latest frame from the camera.

        Returns:
            Frame as numpy array (RGB24), or None if no frame available.
        """
        ...


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
@dataclass
class LiveKitConfig:
    """Configuration for LiveKitStreamServer.

    Attributes:
        livekit_url: WebSocket URL of LiveKit server.
        api_key: API key for token generation.
        api_secret: API secret for token generation.
        room_name: Name of the room to publish to.
        identity: Identity of this publisher (camera name).
        width: Video width in pixels.
        height: Video height in pixels.
        fps: Target frames per second.
        http_port: Port for the simple token server (for web clients).
        host: Host address for token server.
        doc_root: Path to serve static web client files.
    """

    livekit_url: str = "ws://localhost:7880"
    api_key: str = "devkey"
    api_secret: str = "secret"
    room_name: str = "sim-stream"
    identity: str = "sim-camera"
    width: int = DEFAULT_PROFILE.width
    height: int = DEFAULT_PROFILE.height
    fps: int = DEFAULT_PROFILE.fps
    bitrate: int = DEFAULT_PROFILE.bitrate
    http_port: int = 8080
    host: str = "0.0.0.0"
    doc_root: str | None = None


# -----------------------------------------------------------------------------
# LiveKit Stream Server
# -----------------------------------------------------------------------------
class LiveKitStreamServer:
    """Publishes camera frames to a LiveKit room.

    This replaces the manual aiortc WebRTCStreamServer with a production-grade
    LiveKit SDK implementation. Key benefits:
    - Built-in signaling (no custom HTTP/ZMQ signaling needed)
    - Multi-client support via SFU (single encoding, multiple subscribers)
    - Production reliability and reconnection handling
    - Proper bitrate adaptation

    Example:
        config = LiveKitConfig(room_name="robot-camera", fps=30)
        server = LiveKitStreamServer(camera, config)
        await server.start()  # Connects and starts streaming
        # ... later ...
        await server.stop()
    """

    def __init__(self, camera: CameraProtocol, config: LiveKitConfig):
        """Initialize the LiveKit stream server.

        Args:
            camera: Camera object that provides frames via async_read().
            config: LiveKit configuration.
        """
        self._camera = camera
        self._config = config

        self._room: rtc.Room | None = None
        self._video_source: rtc.VideoSource | None = None
        self._shutdown = False
        self._stream_task: asyncio.Task | None = None

        # Token server for web clients
        self._token_server_task: asyncio.Task | None = None
        self._token_app = None
        self._token_runner = None

    def _generate_token(self, identity: str, room_name: str) -> str:
        """Generate a JWT access token for LiveKit.

        Args:
            identity: User/client identity.
            room_name: Room to grant access to.

        Returns:
            JWT token string.
        """
        token = (
            api.AccessToken(self._config.api_key, self._config.api_secret)
            .with_identity(identity)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                )
            )
        )
        return token.to_jwt()

    async def _start_token_server(self) -> None:
        """Start HTTP server to provide tokens and serve web client."""
        from pathlib import Path

        from aiohttp import web

        app = web.Application()

        # Token endpoint for web clients
        async def handle_token(request: web.Request) -> web.Response:
            # Generate a viewer token
            token = self._generate_token(
                identity=f"viewer-{id(request)}",
                room_name=self._config.room_name,
            )
            return web.Response(text=token, content_type="text/plain")

        # Info endpoint
        async def handle_info(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "livekit_url": self._config.livekit_url,
                    "room": self._config.room_name,
                    "resolution": [self._config.width, self._config.height],
                    "fps": self._config.fps,
                }
            )

        app.router.add_get("/token", handle_token)
        app.router.add_get("/info", handle_info)

        # Serve static files if doc_root provided
        if self._config.doc_root:
            doc_path = Path(self._config.doc_root)
            index_path = doc_path / "index.html"

            if doc_path.exists():
                app.router.add_static("/static/", doc_path)

            if index_path.exists():

                async def handle_index(request: web.Request) -> web.FileResponse:
                    return web.FileResponse(index_path)

                app.router.add_get("/", handle_index)

        self._token_app = app
        self._token_runner = web.AppRunner(app)
        await self._token_runner.setup()

        site = web.TCPSite(self._token_runner, self._config.host, self._config.http_port)
        await site.start()

        logger.info(f"Token server started at http://{self._config.host}:{self._config.http_port}")

    async def _stop_token_server(self) -> None:
        """Stop the token server."""
        if self._token_runner:
            await self._token_runner.cleanup()
            self._token_runner = None
            self._token_app = None

    async def connect(self) -> None:
        """Connect to LiveKit server and publish video track."""
        # Generate publisher token
        token = self._generate_token(
            identity=self._config.identity,
            room_name=self._config.room_name,
        )

        # Connect to room
        self._room = rtc.Room()

        @self._room.on("connection_state_changed")
        def on_connection_state(state: rtc.ConnectionState) -> None:
            logger.info(f"LiveKit connection state: {state}")

        await self._room.connect(self._config.livekit_url, token)
        logger.info(f"Connected to LiveKit room: {self._config.room_name}")

        # Create video source and track
        self._video_source = rtc.VideoSource(self._config.width, self._config.height)
        track = rtc.LocalVideoTrack.create_video_track("camera", self._video_source)

        # ---------------------------------------------------------------------
        # HIGH QUALITY ENCODING
        # ---------------------------------------------------------------------
        # Define custom encoding options for max quality (e.g. 1440p 60fps)
        video_encoding = rtc.VideoEncoding(
            max_bitrate=self._config.bitrate,
            max_framerate=self._config.fps,
        )

        # Publish track with these options
        options = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            video_encoding=video_encoding,
            simulcast=False,  # Disable simulcast to save CPU/Bandwidth for high-res
        )
        await self._room.local_participant.publish_track(track, options)
        logger.info(
            f"Published high-quality video track to LiveKit ({self._config.bitrate // 1_000_000} Mbps)"
        )

    async def _stream_loop(self) -> None:
        """Main loop: poll for frames and publish to LiveKit.

        NOTE: Frame pacing is now controlled by the physics loop, which calls
        camera_provider.capture_frame() every N physics steps (deterministic).
        This loop just polls for new frames and publishes them immediately.
        """
        logger.info(f"Starting stream loop (physics-driven at {self._config.fps} fps target)")

        last_frame = None

        while not self._shutdown:
            try:
                # Poll for new frame (non-blocking - returns cached frame or None)
                frame = self._camera.async_read(timeout_ms=0)

                # Only publish when we get a NEW frame
                if frame is not None and self._video_source is not None:
                    # Check if this is actually a new frame (simple identity check)
                    if frame is not last_frame:
                        lk_frame = rtc.VideoFrame(
                            self._config.width,
                            self._config.height,
                            rtc.VideoBufferType.RGB24,
                            frame.tobytes(),
                        )
                        self._video_source.capture_frame(lk_frame)
                        last_frame = frame

                # Small sleep to avoid busy-waiting (1ms = 1000 polls/sec max)
                # Physics at 480Hz produces 60 frames/sec, so this is plenty fast
                await asyncio.sleep(0.001)

            except Exception as e:
                logger.error(f"Error in stream loop: {e}")
                await asyncio.sleep(0.1)

        logger.info("Stream loop stopped")

    async def start(self) -> None:
        """Start the server (connect, publish, and stream)."""
        # Start token server for web clients
        await self._start_token_server()

        # Connect to LiveKit and publish track
        await self.connect()

        # Start streaming in background
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def stop(self) -> None:
        """Stop streaming and disconnect."""
        self._shutdown = True

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        if self._room:
            await self._room.disconnect()
            self._room = None

        await self._stop_token_server()

        logger.info("LiveKitStreamServer stopped")

    def stream(self) -> None:
        """Blocking entry point for compatibility with threading pattern.

        This mirrors the WebRTCStreamServer.stream() interface for drop-in
        replacement in existing code that runs the server in a thread.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self.start())
            # Keep running until shutdown
            while not self._shutdown:
                loop.run_until_complete(asyncio.sleep(0.1))
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            loop.run_until_complete(self.stop())
            loop.close()

    def request_shutdown(self) -> None:
        """Signal the server to stop (thread-safe)."""
        self._shutdown = True

    def cleanup(self) -> None:
        """Clean up resources (for compatibility)."""
        self._shutdown = True

    def get_client_count(self) -> int:
        """Get number of connected clients (subscribers in the room)."""
        if self._room:
            return len(self._room.remote_participants)
        return 0
