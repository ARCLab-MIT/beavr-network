"""Camera to VR WebRTC Streaming.

Simple high-level API for streaming camera feeds to VR devices and browsers.

Usage:
    # One-liner for quick streaming
    stream_camera_to_vr()

    # With custom settings
    stream_camera_to_vr(
        camera="realsense",
        resolution=(1280, 720),
        http_port=8080,
    )

    # Using the class for more control
    streamer = CameraToVRStreamer(camera="opencv:0", http_port=8080)
    streamer.start()
    # ... do other things ...
    streamer.stop()
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Literal

from beavr_teleop.teleop.cameras import (
    ColorMode,
    OpenCVCamera,
    OpenCVCameraConfig,
    RealSenseCamera,
    RealSenseCameraConfig,
)
from beavr_teleop.teleop.components.component import get_shutdown_event
from beavr_teleop.teleop.webrtc.server import WebRTCStreamServer, WebRTCStreamServerConfig

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
@dataclass
class StreamConfig:
    """Streaming configuration with sensible defaults."""

    camera_type: Literal["realsense", "opencv"] = "realsense"
    camera_id: str | int | None = None  # Serial number or device index
    resolution: tuple[int, int] = (640, 480)
    fps: int = 30
    http_port: int = 8080
    zmq_port: int = 10005
    codec: Literal["H264", "VP8"] = "H264"


# -----------------------------------------------------------------------------
# Main Streaming Class
# -----------------------------------------------------------------------------
class CameraToVRStreamer:
    """High-level camera to VR/browser streaming.

    Composes CameraProvider and WebRTCStreamServer for simple streaming setup.

    Example:
        streamer = CameraToVRStreamer(camera="realsense", http_port=8080)
        streamer.start()  # Non-blocking
        # ... do other things ...
        streamer.stop()

        # Or blocking:
        streamer.run()  # Blocks until Ctrl+C
    """

    def __init__(
        self,
        camera: str = "realsense",
        resolution: tuple[int, int] = (640, 480),
        fps: int = 30,
        http_port: int = 8080,
        zmq_port: int = 10005,
        host: str = "localhost",
        codec: Literal["H264", "VP8"] = "H264",
    ):
        """Initialize the streamer.

        Args:
            camera: Camera specification. Options:
                - "realsense" - First available RealSense camera
                - "realsense:SERIAL" - RealSense with specific serial
                - "opencv" or "opencv:0" - OpenCV camera at index 0
                - "opencv:/dev/video0" - OpenCV camera at path
            resolution: (width, height) tuple
            fps: Target framerate
            http_port: HTTP signaling port for browser clients
            zmq_port: ZMQ signaling port for VR/Unity clients
            codec: Video codec to use ('H264' or 'VP8')
        """
        self._camera_type, self._camera_id = self._parse_camera(camera)
        self._resolution = resolution
        self._fps = fps
        self._http_port = http_port
        self._zmq_port = zmq_port
        self._host = host
        self._codec = codec
        self._camera = None
        self._server: WebRTCStreamServer | None = None
        self._server_thread: threading.Thread | None = None
        self._shutdown_event = get_shutdown_event()

    @staticmethod
    def _parse_camera(camera: str) -> tuple[str, str | int | None]:
        """Parse camera string like 'realsense:SERIAL' or 'opencv:0'."""
        if ":" in camera:
            cam_type, cam_id = camera.split(":", 1)
            # Try to convert to int for opencv device index
            if cam_type == "opencv":
                try:
                    return cam_type, int(cam_id)
                except ValueError:
                    return cam_type, cam_id  # Path like /dev/video0
            return cam_type, cam_id
        return camera, None

    def _create_camera(self):
        """Instantiate the selected camera."""
        if self._camera_type == "realsense":
            config = RealSenseCameraConfig(
                serial_number_or_name=self._camera_id if isinstance(self._camera_id, str) else None,
                width=self._resolution[0],
                height=self._resolution[1],
                fps=self._fps,
                color_mode=ColorMode.RGB,
            )
            return RealSenseCamera(config)

        config = OpenCVCameraConfig(
            index_or_path=self._camera_id if self._camera_id is not None else 0,
            width=self._resolution[0],
            height=self._resolution[1],
            fps=self._fps,
            color_mode=ColorMode.RGB,
        )
        return OpenCVCamera(config)

    def _create_server_config(self) -> WebRTCStreamServerConfig:
        """Create WebRTCStreamServer configuration."""
        return WebRTCStreamServerConfig(
            signaling_port=self._http_port,
            zmq_port=self._zmq_port,
            framerate=self._fps,
            resolution=self._resolution,
            signaling_type="zmq",
            codec=self._codec,
        )

    def start(self) -> None:
        """Start streaming (non-blocking).

        Starts camera capture and WebRTC server in background threads.
        """
        if self._camera is not None:
            logger.warning("Streamer already running")
            return

        logger.info(
            f"Starting camera stream: {self._camera_type} @ "
            f"{self._resolution[0]}x{self._resolution[1]} {self._fps}fps"
        )

        # Create and start camera
        self._camera = self._create_camera()
        self._camera.connect()

        # Give camera time to start publishing frames
        time.sleep(0.3)

        # Create and start WebRTC server
        self._server = WebRTCStreamServer(self._camera, self._create_server_config())
        self._server_thread = threading.Thread(
            target=self._server.stream,
            name="WebRTCStreamServer",
            daemon=True,
        )
        self._server_thread.start()

        logger.info(f"Streaming started - ZMQ signaling: tcp://{self._host}:{self._zmq_port}")

    def stop(self) -> None:
        """Stop streaming and cleanup resources."""
        logger.info("Stopping camera stream...")

        self._shutdown_event.set()

        if self._server is not None:
            # Ask the WebRTC server to exit its loop; cleanup happens in its thread.
            self._server.request_shutdown()

        if self._server_thread is not None:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None

        if self._server is not None:
            # Ensure resources are released even if the loop already stopped.
            self._server.cleanup()
            self._server = None

        if self._camera is not None:
            try:
                self._camera.disconnect()
            finally:
                self._camera = None

        logger.info("Camera stream stopped")

    def run(self) -> None:
        """Start streaming and block until shutdown (Ctrl+C)."""
        self.start()

        try:
            while not self._shutdown_event.is_set():
                if self._shutdown_event.wait(timeout=1.0):
                    break
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            self.stop()

    @property
    def is_running(self) -> bool:
        """Check if streamer is currently running."""
        return self._camera is not None and self._server is not None

    def get_client_count(self) -> int:
        """Get number of connected WebRTC clients."""
        if self._server is not None:
            return self._server.get_client_count()
        return 0


# -----------------------------------------------------------------------------
# Simple Function API
# -----------------------------------------------------------------------------
def stream_camera_to_vr(
    camera: str = "realsense",
    resolution: tuple[int, int] = (1280, 720),
    fps: int = 30,
    http_port: int = 8080,
    zmq_port: int = 10005,
    host: str = "localhost",
    codec: Literal["H264", "VP8"] = "H264",
) -> None:
    """Stream camera to VR/browser clients (blocking).

    This is the simplest way to start streaming. It blocks until Ctrl+C.

    Args:
        camera: Camera to use. Options:
            - "realsense" - First available RealSense
            - "realsense:SERIAL" - Specific RealSense by serial
            - "opencv" or "opencv:0" - OpenCV camera
            - "opencv:/dev/video0" - OpenCV camera by path
        resolution: (width, height) tuple
        fps: Target framerate
        http_port: HTTP port (unused when signaling_type='zmq')
        zmq_port: ZMQ port for VR/Unity signaling
        codec: Video codec to use ('H264' or 'VP8')

    Example:
        # Stream first RealSense camera
        stream_camera_to_vr()

        # Stream with custom settings
        stream_camera_to_vr(
            camera="opencv:0",
            resolution=(1920, 1080),
            http_port=8080,
        )

        # Stream with VP8 codec for Unity compatibility
        stream_camera_to_vr(
            camera="realsense",
            codec="VP8",
        )
    """
    streamer = CameraToVRStreamer(
        camera=camera,
        resolution=resolution,
        fps=fps,
        http_port=http_port,
        zmq_port=zmq_port,
        host=host,
        codec=codec,
    )
    streamer.run()


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------
def main() -> None:
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Stream camera to VR/browser via WebRTC")
    parser.add_argument(
        "--camera",
        "-c",
        default="realsense",
        help="Camera: 'realsense', 'opencv', 'opencv:0', 'realsense:SERIAL'",
    )
    parser.add_argument("--resolution", "-r", default="640x480", help="Resolution WxH (default: 640x480)")
    parser.add_argument("--fps", "-f", type=int, default=30, help="Target FPS (default: 30)")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP signaling port (default: 8080)")
    parser.add_argument("--zmq-port", type=int, default=10005, help="ZMQ signaling port (default: 10005)")

    parser.add_argument("--host", default="10.7.133.21", help="Host address (default: 10.7.133.21)")
    parser.add_argument(
        "--codec", choices=["H264", "VP8"], default="VP8", help="Video codec to use (default: VP8)"
    )

    args = parser.parse_args()

    # Parse resolution
    width, height = map(int, args.resolution.split("x"))

    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    print("\n📹 Camera to VR Streamer")
    print(f"   Camera: {args.camera}")
    print(f"   Resolution: {width}x{height} @ {args.fps}fps")
    print(f"   Codec: {args.codec}")
    print(f"   HTTP: http://{args.host}:{args.http_port}")
    print(f"   ZMQ: tcp://{args.host}:{args.zmq_port}")
    print("\n   Press Ctrl+C to stop\n")

    stream_camera_to_vr(
        camera=args.camera,
        resolution=(width, height),
        fps=args.fps,
        http_port=args.http_port,
        zmq_port=args.zmq_port,
        host=args.host,
        codec=args.codec,
    )


if __name__ == "__main__":
    main()
