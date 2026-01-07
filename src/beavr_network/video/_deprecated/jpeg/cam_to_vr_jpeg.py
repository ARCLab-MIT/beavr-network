"""Camera to VR ZMQ JPEG Streaming.

DEPRECATED: This module is no longer actively maintained.
Use `beavr_network.video.webrtc` for active video streaming.

Simple high-level API for streaming camera feeds to VR devices via ZMQ with JPEG compression.

Usage:
    # One-liner for quick streaming
    stream_camera_to_vr()

    # With custom settings
    stream_camera_to_vr(
        camera="realsense",
        resolution=(640, 480),
        zmq_port=10005,
    )

    # Using the class for more control
    streamer = CameraToVRStreamer(camera="opencv:0", zmq_port=10005)
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
from beavr_teleop.teleop.common.network.publisher import ZMQCompressedImageTransmitter

from beavr_network.video.utils import get_shutdown_event

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
@dataclass
class StreamConfig:
    """Streaming configuration with sensible defaults."""

    camera_type: Literal["realsense", "opencv"] = "realsense"
    camera_id: str | int | None = None  # Serial number or device index
    resolution: tuple[int, int] = (1280, 720)
    fps: int = 30
    zmq_port: int = 10005
    jpeg_quality: int = 85  # JPEG quality (1-100, higher = better quality)


# -----------------------------------------------------------------------------
# Main Streaming Class
# -----------------------------------------------------------------------------
class CameraToVRStreamer:
    """High-level camera to VR streaming via ZMQ with JPEG compression.

    Streams camera frames as compressed JPEG images via ZMQ PUB socket.
    Compatible with Unity NetMQ subscribers.

    Example:
        streamer = CameraToVRStreamer(camera="realsense", zmq_port=10005)
        streamer.start()  # Non-blocking
        # ... do other things ...
        streamer.stop()

        # Or blocking:
        streamer.run()  # Blocks until Ctrl+C
    """

    def __init__(
        self,
        camera: str = "realsense",
        resolution: tuple[int, int] = (1280, 720),
        fps: int = 30,
        zmq_port: int = 10005,
        host: str = "0.0.0.0",
        jpeg_quality: int = 85,
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
            zmq_port: ZMQ port for streaming JPEG frames
            host: Host address to bind to (default: 0.0.0.0 for all interfaces)
            jpeg_quality: JPEG compression quality (1-100, higher = better quality)
        """
        self._camera_type, self._camera_id = self._parse_camera(camera)
        self._resolution = resolution
        self._fps = fps
        self._zmq_port = zmq_port
        self._host = host
        self._jpeg_quality = jpeg_quality
        self._camera = None
        self._publisher: ZMQCompressedImageTransmitter | None = None
        self._stream_thread: threading.Thread | None = None
        self._shutdown_event = get_shutdown_event()
        self._running = False

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

    def _stream_loop(self) -> None:
        """Main streaming loop - runs in background thread."""
        frame_time = 1.0 / self._fps
        logger.info(f"Starting stream loop at {self._fps} fps")

        while self._running and not self._shutdown_event.is_set():
            try:
                start_time = time.time()

                # Get frame from camera
                frame = self._camera.read()

                if frame is not None:
                    # Send compressed JPEG frame
                    self._publisher.send_image(frame)
                else:
                    logger.warning("Camera returned None frame")

                # Frame rate limiting
                elapsed = time.time() - start_time
                sleep_time = max(0, frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in stream loop: {e}")
                if self._running:
                    time.sleep(0.1)  # Brief pause before retry

        logger.info("Stream loop exited")

    def start(self) -> None:
        """Start streaming (non-blocking).

        Starts camera capture and ZMQ JPEG streaming in background thread.
        """
        if self._running:
            logger.warning("Streamer already running")
            return

        logger.info(
            f"Starting camera stream: {self._camera_type} @ "
            f"{self._resolution[0]}x{self._resolution[1]} {self._fps}fps"
        )

        # Create and start camera
        self._camera = self._create_camera()
        self._camera.connect()

        # Give camera time to initialize
        time.sleep(0.3)

        # Create ZMQ publisher
        self._publisher = ZMQCompressedImageTransmitter(
            host=self._host,
            port=self._zmq_port,
            jpeg_quality=self._jpeg_quality,
        )

        # Start streaming thread
        self._running = True
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            name="ZMQJPEGStreamer",
            daemon=True,
        )
        self._stream_thread.start()

        logger.info(f"Streaming started - ZMQ publisher: tcp://{self._host}:{self._zmq_port}")
        logger.info(f"Unity clients should connect to: tcp://<server_ip>:{self._zmq_port}")

    def stop(self) -> None:
        """Stop streaming and cleanup resources."""
        logger.info("Stopping camera stream...")

        # Signal shutdown
        self._running = False
        self._shutdown_event.set()

        # Wait for stream thread to finish
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=2.0)
            if self._stream_thread.is_alive():
                logger.warning("Stream thread did not stop gracefully")
            self._stream_thread = None

        # Cleanup publisher
        if self._publisher is not None:
            try:
                self._publisher.stop()
            except Exception as e:
                logger.error(f"Error stopping publisher: {e}")
            finally:
                self._publisher = None

        # Cleanup camera
        if self._camera is not None:
            try:
                self._camera.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting camera: {e}")
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
        return self._running and self._camera is not None and self._publisher is not None


# -----------------------------------------------------------------------------
# Simple Function API
# -----------------------------------------------------------------------------
def stream_camera_to_vr(
    camera: str = "realsense",
    resolution: tuple[int, int] = (1280, 720),
    fps: int = 30,
    zmq_port: int = 10005,
    host: str = "0.0.0.0",
    jpeg_quality: int = 85,
) -> None:
    """Stream camera to VR/Unity clients via ZMQ with JPEG compression (blocking).

    DEPRECATED: This module is no longer actively maintained.
    Use `beavr_network.video.webrtc` for active video streaming.

    This is the simplest way to start streaming. It blocks until Ctrl+C.

        Args:
            camera: Camera to use. Options:
                - "realsense" - First available RealSense
                - "realsense:SERIAL" - Specific RealSense by serial
                - "opencv" or "opencv:0" - OpenCV camera
                - "opencv:/dev/video0" - OpenCV camera by path
            resolution: (width, height) tuple
            fps: Target framerate
            zmq_port: ZMQ port for streaming JPEG frames
            host: Host address to bind to (default: 0.0.0.0 for all interfaces)
            jpeg_quality: JPEG compression quality (1-100, higher = better quality)

        Example:
            # Stream first RealSense camera
            stream_camera_to_vr()

            # Stream with custom settings
            stream_camera_to_vr(
                camera="opencv:0",
                resolution=(1280, 720),
                zmq_port=10005,
                jpeg_quality=85,
            )

            # Stream with lower quality for bandwidth
            stream_camera_to_vr(
                camera="realsense",
                jpeg_quality=30,
            )
    """
    streamer = CameraToVRStreamer(
        camera=camera,
        resolution=resolution,
        fps=fps,
        zmq_port=zmq_port,
        host=host,
        jpeg_quality=jpeg_quality,
    )
    streamer.run()


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------
def main() -> None:
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Stream camera to VR/Unity via ZMQ with JPEG compression")
    parser.add_argument(
        "--camera",
        "-c",
        default="realsense",
        help="Camera: 'realsense', 'opencv', 'opencv:0', 'realsense:SERIAL'",
    )
    parser.add_argument("--resolution", "-r", default="1280x720", help="Resolution WxH (default: 640x480)")
    parser.add_argument("--fps", "-f", type=int, default=30, help="Target FPS (default: 30)")
    parser.add_argument(
        "--zmq-port", "-p", type=int, default=10005, help="ZMQ port for streaming (default: 10005)"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind to (default: 0.0.0.0)")
    parser.add_argument("--quality", "-q", type=int, default=85, help="JPEG quality 1-100 (default: 85)")

    args = parser.parse_args()

    # Parse resolution
    width, height = map(int, args.resolution.split("x"))

    # Validate quality
    if not 1 <= args.quality <= 100:
        parser.error("Quality must be between 1 and 100")

    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    print("\n📹 Camera to VR Streamer (ZMQ JPEG)")
    print(f"   Camera: {args.camera}")
    print(f"   Resolution: {width}x{height} @ {args.fps}fps")
    print(f"   JPEG Quality: {args.quality}")
    print(f"   ZMQ Endpoint: tcp://{args.host}:{args.zmq_port}")
    print(f"\n   Unity should connect to: tcp://<server_ip>:{args.zmq_port}")
    print("\n   Press Ctrl+C to stop\n")

    stream_camera_to_vr(
        camera=args.camera,
        resolution=(width, height),
        fps=args.fps,
        zmq_port=args.zmq_port,
        host=args.host,
        jpeg_quality=args.quality,
    )


if __name__ == "__main__":
    main()
