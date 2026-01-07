"""Camera to Web Browser Streaming.

Simple WebRTC streaming directly to web browsers. This is a lightweight
alternative to cam_to_vr.py that uses direct camera access (no shared memory)
for simpler single-process streaming.

Usage:
    # One-liner
    stream_camera_to_web()

    # With settings
    stream_camera_to_web(camera="opencv:0", port=8080)

    # CLI
    python cam_to_web.py --port 8080 --camera realsense
"""

import logging
from pathlib import Path

from beavr_teleop.teleop.cameras import (
    ColorMode,
    OpenCVCamera,
    OpenCVCameraConfig,
    RealSenseCamera,
    RealSenseCameraConfig,
)
from beavr_teleop.teleop.webrtc.server import WebRTCStreamServer, WebRTCStreamServerConfig

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Simple Function API
# -----------------------------------------------------------------------------
def stream_camera_to_web(
    camera: str = "realsense",
    resolution: tuple[int, int] = (1280, 720),
    fps: int = 30,
    port: int = 8080,
) -> None:
    """Stream camera to web browser (blocking).

    Opens a web server that serves a video stream viewable in any browser.
    Press Ctrl+C to stop.

    Args:
        camera: Camera to use:
            - "realsense" - First RealSense camera
            - "realsense:SERIAL" - RealSense by serial number
            - "opencv" or "opencv:0" - OpenCV camera
        resolution: (width, height) tuple
        fps: Target framerate
        port: HTTP port for web server

    Example:
        stream_camera_to_web(camera="realsense", port=8080)
    """
    # Parse camera string
    cam_type = camera.split(":")[0]
    cam_id = camera.split(":")[1] if ":" in camera else None

    # Create camera
    if cam_type == "realsense":
        logger.info(f"Creating RealSense camera with ID: {cam_id}")
        config = RealSenseCameraConfig(
            serial_number_or_name=cam_id,
            width=resolution[0],
            height=resolution[1],
            fps=fps,
            color_mode=ColorMode.RGB,
        )
        cam = RealSenseCamera(config)

    else:
        logger.info(f"Creating OpenCV camera with ID: {cam_id}")
        idx = int(cam_id) if cam_id and cam_id.isdigit() else (cam_id or 0)
        config = OpenCVCameraConfig(
            index_or_path=idx,
            width=resolution[0],
            height=resolution[1],
            fps=fps,
            color_mode=ColorMode.RGB,
        )
        cam = OpenCVCamera(config)

    # Connect camera
    cam.connect()
    logger.info(f"Camera connected: {cam}")

    # Create WebRTC stream server
    doc_root = Path(__file__).resolve().parent.parent / "clients" / "web"
    config = WebRTCStreamServerConfig(
        signaling_port=port,
        framerate=fps,
        resolution=resolution,
        doc_root=str(doc_root),
    )
    server = WebRTCStreamServer(camera=cam, config=config)

    # Run server (blocking)
    try:
        server.stream()
    finally:
        cam.disconnect()
        logger.info("Camera disconnected")


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------
def main() -> None:
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Stream camera to web browser")
    parser.add_argument(
        "--camera",
        "-c",
        default="realsense",
        help="Camera: 'realsense', 'opencv', 'opencv:0', 'realsense:SERIAL'",
    )
    parser.add_argument("--resolution", "-r", default="1280x720", help="Resolution WxH (default: 1280x720)")
    parser.add_argument("--fps", "-f", type=int, default=30, help="Target FPS (default: 30)")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Web server port (default: 8080)")
    parser.add_argument("--list-cameras", action="store_true", help="List available cameras and exit")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logging.getLogger("aiortc").setLevel(logging.WARNING)

    # List cameras option
    if args.list_cameras:
        print("\n📹 Available Cameras:\n")

        print("RealSense:")
        for cam in RealSenseCamera.find_cameras():
            print(f"  - {cam['name']} (serial: {cam['id']})")

        print("\nOpenCV:")
        for cam in OpenCVCamera.find_cameras()[:5]:  # Limit to first 5
            print(f"  - {cam['name']}")

        return

    # Parse resolution
    width, height = map(int, args.resolution.split("x"))

    print("\n📹 Camera to Web Streamer")
    print(f"   Camera: {args.camera}")
    print(f"   Resolution: {width}x{height} @ {args.fps}fps")
    print(f"   URL: http://localhost:{args.port}")
    print("\n   Press Ctrl+C to stop\n")

    stream_camera_to_web(
        camera=args.camera,
        resolution=(width, height),
        fps=args.fps,
        port=args.port,
    )


if __name__ == "__main__":
    main()
