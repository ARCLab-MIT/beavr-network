"""Simulation camera streaming to VR via ZMQ JPEG.

DEPRECATED: This module is no longer actively maintained.
Use `beavr_network.video.webrtc` for active video streaming.

Streams rendered camera views from Mujoco simulation to VR headset using the same
infrastructure as physical camera streaming.
"""

import logging
import time

import mujoco

from beavr_network.network.publisher import ZMQCompressedImageTransmitter

try:
    from beavr_teleop.teleop.components import Component
    from beavr_teleop.teleop.components.simulation.mujoco_env import MujocoEnv
except ImportError:
    # This legacy module requires beavr_teleop which may not be present in all environments
    Component = object  # type: ignore
    MujocoEnv = object  # type: ignore
    pass

logger = logging.getLogger(__name__)


class FrequencyTimer:
    def __init__(self, frequency_rate):
        self.time_available = 1e9 / frequency_rate

    def start_loop(self):
        self.start_time = time.time_ns()

    def end_loop(self):
        wait_time = self.time_available + self.start_time

        while time.time_ns() < wait_time:
            continue

    def __enter__(self):
        """Context manager entry - start the timing loop."""
        self.start_loop()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - end the timing loop."""
        self.end_loop()


class SimCameraStreamer(Component):
    """Streams Mujoco simulation camera view to VR via ZMQ with JPEG compression.

    This component runs in its own process and continuously renders the simulation
    camera view, compresses it to JPEG, and streams it to the VR headset on the
    standard camera port (10005).

    Example:
        streamer = SimCameraStreamer(
            env=mujoco_env,
            camera_id=0,
            resolution=(1280, 720),
            fps=60,
            port=10005,
            host="0.0.0.0",
        )
        streamer.stream()  # Blocking
    """

    def __init__(
        self,
        env: MujocoEnv,
        camera_id: int | str = -1,  # -1 = free camera (default view)
        resolution: tuple[int, int] = (1280, 720),
        fps: int = 60,
        port: int = 10005,
        host: str = "0.0.0.0",
        jpeg_quality: int = 100,
    ):
        """Initialize the simulation camera streamer.

        Args:
            env: MujocoEnv instance to render from
            camera_id: Camera ID (int), name (str), or -1 for free camera (default)
            resolution: (width, height) tuple
            fps: Target framerate
            port: ZMQ port for streaming (default: 10005 for VR)
            host: Host address to bind to
            jpeg_quality: JPEG compression quality (1-100)
        """
        self.notify_component_start("sim_camera_streamer")

        self._env = env
        self._camera_id = camera_id
        self._width, self._height = resolution
        self._fps = fps
        self._port = port
        self._host = host
        self._jpeg_quality = jpeg_quality

        # Create ZMQ publisher for compressed images
        self._publisher = ZMQCompressedImageTransmitter(
            host=self._host,
            port=self._port,
            jpeg_quality=self._jpeg_quality,
        )

        # Frequency timer for frame rate control
        self._timer = FrequencyTimer(self._fps)

        # Create a dedicated renderer for this thread to avoid EGL context sharing issues
        # EGL contexts cannot be shared across threads, so each thread needs its own renderer
        self._renderer = None  # Will be initialized in step() on first call

        logger.info(
            f"📹 Sim camera streaming initialized: camera={self._camera_id}, "
            f"{self._width}x{self._height} @ {self._fps}fps, "
            f"port={self._port}, quality={self._jpeg_quality}"
        )

        # Log available cameras
        try:
            cameras = self._env.get_camera_list()
            logger.info(f"Available cameras in simulation: {cameras}")
        except Exception as e:
            logger.warning(f"Could not list cameras: {e}")

    def step(self) -> None:
        """Execute one step of the streaming loop.

        Renders current camera view from simulation and streams via ZMQ.
        """
        self._timer.start_loop()

        try:
            # Initialize renderer on first call (lazy init in this thread to get correct EGL context)
            if self._renderer is None:
                self._renderer = mujoco.Renderer(self._env.model, height=self._height, width=self._width)
                self._renderer.enable_headlight = True

                # Enable rendering flags for better visual quality (match passive viewer)
                # These flags enable shadows and reflections for more realistic lighting
                self._renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 1
                self._renderer.scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 1

                logger.info(f"Initialized renderer in streaming thread (PID: {__import__('os').getpid()})")

            # Update renderer with current simulation state
            self._renderer.update_scene(self._env.data, camera=self._camera_id)

            # Manual lighting enhancement: Add a persistent brighter headlight
            # We modify the scene before rendering.
            # MJV_LIGHT_HEADLIGHT is usually at index 0 or 1 if enabled.
            # We can also add a new light to the scene.lights array.

            # Simple approach: Find the headlight and boost it.
            # If headlight is not found, add a directional light at camera position.

            # Access the abstract scene
            scene = self._renderer.scene

            # Boost existing lights or add a new one
            # Light 0 and 1 are typically the headlights tracking the camera if enable_headlight is True
            # We will manually overwrite the first light to be a strong headlight
            if scene.nlight > 0:
                # Boost the first light (usually the headlight)
                # diffuse color [R, G, B]
                scene.lights[0].diffuse[:] = [1.5, 1.5, 1.5]  # Boost to 150% white
                scene.lights[0].specular[:] = [1.0, 1.0, 1.0]  # Specular highlights

                # Ensure it tracks camera if it's the headlight
                # (The renderer update_scene usually handles position, but we just want to boost intensity)

            # Render frame
            frame = self._renderer.render()

            if frame is not None:
                # Send compressed JPEG frame
                self._publisher.send_image(frame)
            else:
                logger.warning("Renderer returned None frame")

        except Exception as e:
            logger.error(f"Error rendering/streaming frame: {e}")
            time.sleep(0.1)  # Brief pause before retry

        self._timer.end_loop()

    def cleanup(self) -> None:
        """Clean up resources."""
        # Clean up renderer
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception as e:
                logger.error(f"Error closing renderer: {e}")
            finally:
                self._renderer = None

        # Clean up publisher
        if self._publisher is not None:
            try:
                self._publisher.stop()
            except Exception as e:
                logger.error(f"Error stopping publisher: {e}")
            finally:
                self._publisher = None

        logger.info("Sim camera streaming stopped")
