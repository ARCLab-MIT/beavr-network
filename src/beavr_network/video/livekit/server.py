"""LiveKit-based streaming server.

Publishes camera frames to a LiveKit room for web browser consumption.
Uses the production-grade LiveKit SDK instead of manual aiortc WebRTC.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from beavr_configs.cameras import DEFAULT_PROFILE
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
        livekit_url: WebSocket URL of LiveKit server (used by publisher).
        external_livekit_url: Optional WebSocket URL advertised to clients via /info.
            If not set and livekit_url uses localhost/0.0.0.0, /info will auto-advertise
            a best-effort LAN-reachable URL (e.g. ws://<local_ipv4>:7880).
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
        track_name: LiveKit video track name exposed by /info for clients.
    """

    livekit_url: str = "ws://localhost:7880"
    external_livekit_url: str | None = None
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
    track_name: str = "camera"


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
            # If the publisher connects to a local LiveKit (e.g. ws://localhost:7880),
            # remote clients must NOT be told "localhost". Advertise a LAN-reachable URL.
            from urllib.parse import urlparse

            from beavr_configs.network import get_local_ipv4

            client_livekit_url = self._config.livekit_url
            if self._config.external_livekit_url:
                client_livekit_url = self._config.external_livekit_url
            else:
                try:
                    parsed = urlparse(self._config.livekit_url)
                    scheme = parsed.scheme or "ws"
                    host = parsed.hostname
                    port = parsed.port
                    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
                        ip = get_local_ipv4()
                        if port is not None:
                            client_livekit_url = f"{scheme}://{ip}:{port}"
                        else:
                            client_livekit_url = f"{scheme}://{ip}"
                except Exception:
                    # Fall back to configured URL if parsing fails.
                    client_livekit_url = self._config.livekit_url

            return web.json_response(
                {
                    "livekit_url": client_livekit_url,
                    "room": self._config.room_name,
                    "track_name": self._config.track_name,
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
        track = rtc.LocalVideoTrack.create_video_track(self._config.track_name, self._video_source)

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
            simulcast=True,  # Enable simulcast: client auto-picks best quality layer
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
        frames_published = 0
        no_frame_count = 0

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
                        frames_published += 1
                        no_frame_count = 0

                        # Log every 30 frames (once per second at 30fps)
                        if frames_published % 30 == 0:
                            logger.debug(f"Published {frames_published} frames to LiveKit")
                else:
                    no_frame_count += 1
                    # Warn if we haven't gotten frames for a while
                    if no_frame_count == 1000:  # ~1 second at 1ms sleep
                        logger.warning(f"No frames received from camera after {no_frame_count} attempts")

                # Small sleep to avoid busy-waiting (1ms = 1000 polls/sec max)
                # Physics at 480Hz produces 60 frames/sec, so this is plenty fast
                await asyncio.sleep(0.001)

            except Exception as e:
                logger.error(f"Error in stream loop: {e}")
                await asyncio.sleep(0.1)

        logger.info(f"Stream loop stopped (published {frames_published} frames total)")

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

        # Send black frame before disconnecting to signal "stream ended"
        if self._video_source is not None:
            try:
                import numpy as np

                black_frame = np.zeros((self._config.height, self._config.width, 3), dtype=np.uint8)
                lk_frame = rtc.VideoFrame(
                    self._config.width,
                    self._config.height,
                    rtc.VideoBufferType.RGB24,
                    black_frame.tobytes(),
                )
                self._video_source.capture_frame(lk_frame)
                # Small delay to ensure frame is sent
                await asyncio.sleep(0.1)
                logger.debug("Sent black frame before disconnect")
            except Exception as e:
                logger.debug(f"Could not send black frame: {e}")

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
        except Exception:
            logger.exception("LiveKitStreamServer failed")
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


@dataclass(frozen=True)
class LiveKitCameraStream:
    """One camera/track served by a multi-camera LiveKit publisher."""

    camera: CameraProtocol
    config: LiveKitConfig


@dataclass
class _PublishedStream:
    camera: CameraProtocol
    config: LiveKitConfig
    video_source: rtc.VideoSource


class LiveKitMultiStreamServer:
    """Publishes multiple camera tracks through one LiveKit room connection.

    The LiveKit Python SDK is process/global-FFI backed. Running multiple Room
    connections from multiple Python threads has been unreliable: token servers
    come up, but only one publisher connects. This class keeps one Room
    connection and publishes one named video track per camera while preserving
    one HTTP token/info endpoint per camera port.
    """

    def __init__(self, streams: Sequence[LiveKitCameraStream]):
        if not streams:
            raise ValueError("LiveKitMultiStreamServer requires at least one stream")

        self._streams = list(streams)
        self._base_config = self._streams[0].config

        self._room: rtc.Room | None = None
        self._shutdown = False
        self._stream_task: asyncio.Task | None = None
        self._published_streams: list[_PublishedStream] = []
        self._token_runners: list[Any] = []

    def _generate_token(self, identity: str, room_name: str) -> str:
        token = (
            api.AccessToken(self._base_config.api_key, self._base_config.api_secret)
            .with_identity(identity)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                )
            )
        )
        return token.to_jwt()

    def _client_livekit_url(self, config: LiveKitConfig) -> str:
        if config.external_livekit_url:
            return config.external_livekit_url

        from urllib.parse import urlparse

        client_livekit_url = config.livekit_url
        try:
            from beavr_configs.network import get_local_ipv4

            parsed = urlparse(config.livekit_url)
            scheme = parsed.scheme or "ws"
            host = parsed.hostname
            port = parsed.port
            if host in ("localhost", "127.0.0.1", "0.0.0.0"):
                ip = get_local_ipv4()
                if port is not None:
                    client_livekit_url = f"{scheme}://{ip}:{port}"
                else:
                    client_livekit_url = f"{scheme}://{ip}"
        except Exception:
            client_livekit_url = config.livekit_url

        return client_livekit_url

    async def _start_token_server(self, config: LiveKitConfig) -> None:
        from pathlib import Path

        from aiohttp import web

        app = web.Application()

        async def handle_token(request: web.Request) -> web.Response:
            token = self._generate_token(
                identity=f"viewer-{config.track_name}-{id(request)}",
                room_name=config.room_name,
            )
            return web.Response(text=token, content_type="text/plain")

        async def handle_info(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "livekit_url": self._client_livekit_url(config),
                    "room": config.room_name,
                    "track_name": config.track_name,
                    "resolution": [config.width, config.height],
                    "fps": config.fps,
                }
            )

        app.router.add_get("/token", handle_token)
        app.router.add_get("/info", handle_info)

        if config.doc_root:
            doc_path = Path(config.doc_root)
            index_path = doc_path / "index.html"

            if doc_path.exists():
                app.router.add_static("/static/", doc_path)

            if index_path.exists():

                async def handle_index(request: web.Request) -> web.FileResponse:
                    return web.FileResponse(index_path)

                app.router.add_get("/", handle_index)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, config.host, config.http_port)
        await site.start()
        self._token_runners.append(runner)

        logger.info(
            "Token server started at http://%s:%s for track '%s'",
            config.host,
            config.http_port,
            config.track_name,
        )

    async def _stop_token_servers(self) -> None:
        while self._token_runners:
            runner = self._token_runners.pop()
            await runner.cleanup()

    async def connect(self) -> None:
        token = self._generate_token(
            identity=self._base_config.identity,
            room_name=self._base_config.room_name,
        )

        self._room = rtc.Room()

        @self._room.on("connection_state_changed")
        def on_connection_state(state: rtc.ConnectionState) -> None:
            logger.info(f"LiveKit connection state: {state}")

        await self._room.connect(self._base_config.livekit_url, token)
        logger.info(f"Connected to LiveKit room: {self._base_config.room_name}")

        for stream in self._streams:
            config = stream.config
            video_source = rtc.VideoSource(config.width, config.height)
            track = rtc.LocalVideoTrack.create_video_track(config.track_name, video_source)
            video_encoding = rtc.VideoEncoding(
                max_bitrate=config.bitrate,
                max_framerate=config.fps,
            )
            options = rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_CAMERA,
                video_encoding=video_encoding,
                simulcast=True,
            )
            await self._room.local_participant.publish_track(track, options)
            self._published_streams.append(
                _PublishedStream(
                    camera=stream.camera,
                    config=config,
                    video_source=video_source,
                )
            )
            logger.info(
                "Published LiveKit track '%s' on room '%s' (%s Mbps)",
                config.track_name,
                config.room_name,
                config.bitrate // 1_000_000,
            )

    async def _stream_loop(self) -> None:
        logger.info(
            "Starting multi-camera stream loop (%d tracks, physics-driven)",
            len(self._published_streams),
        )

        last_frames: dict[str, Any] = {}
        frames_published: dict[str, int] = {stream.config.track_name: 0 for stream in self._published_streams}
        no_frame_counts: dict[str, int] = {stream.config.track_name: 0 for stream in self._published_streams}

        while not self._shutdown:
            for stream in self._published_streams:
                track_name = stream.config.track_name
                try:
                    frame = stream.camera.async_read(timeout_ms=0)
                    if frame is not None:
                        if frame is not last_frames.get(track_name):
                            lk_frame = rtc.VideoFrame(
                                stream.config.width,
                                stream.config.height,
                                rtc.VideoBufferType.RGB24,
                                frame.tobytes(),
                            )
                            stream.video_source.capture_frame(lk_frame)
                            last_frames[track_name] = frame
                            frames_published[track_name] += 1
                            no_frame_counts[track_name] = 0
                    else:
                        no_frame_counts[track_name] += 1
                        if no_frame_counts[track_name] == 1000:
                            logger.warning(
                                "No frames received for LiveKit track '%s' after %d attempts",
                                track_name,
                                no_frame_counts[track_name],
                            )
                except Exception as e:
                    logger.error("Error in stream loop for track '%s': %s", track_name, e)

            await asyncio.sleep(0.001)

        summary = ", ".join(f"{track}: {count}" for track, count in frames_published.items())
        logger.info("Multi-camera stream loop stopped (published frames: %s)", summary)

    async def start(self) -> None:
        for stream in self._streams:
            await self._start_token_server(stream.config)
        await self.connect()
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def stop(self) -> None:
        self._shutdown = True

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        for stream in self._published_streams:
            try:
                import numpy as np

                config = stream.config
                black_frame = np.zeros((config.height, config.width, 3), dtype=np.uint8)
                lk_frame = rtc.VideoFrame(
                    config.width,
                    config.height,
                    rtc.VideoBufferType.RGB24,
                    black_frame.tobytes(),
                )
                stream.video_source.capture_frame(lk_frame)
            except Exception as e:
                logger.debug("Could not send black frame for track '%s': %s", stream.config.track_name, e)

        if self._published_streams:
            await asyncio.sleep(0.1)
        self._published_streams.clear()

        if self._room:
            await self._room.disconnect()
            self._room = None

        await self._stop_token_servers()

        logger.info("LiveKitMultiStreamServer stopped")

    def stream(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self.start())
            while not self._shutdown:
                loop.run_until_complete(asyncio.sleep(0.1))
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception:
            logger.exception("LiveKitMultiStreamServer failed")
        finally:
            loop.run_until_complete(self.stop())
            loop.close()

    def request_shutdown(self) -> None:
        self._shutdown = True

    def cleanup(self) -> None:
        self._shutdown = True

    def get_client_count(self) -> int:
        if self._room:
            return len(self._room.remote_participants)
        return 0
