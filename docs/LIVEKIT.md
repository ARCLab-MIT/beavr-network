# LiveKit Streaming in BEAVR

BEAVR uses **LiveKit** for production-grade, low-latency video streaming from simulation environments to web browsers. It replaces the previous manual WebRTC implementation with a robust SFU (Selective Forwarding Unit) architecture.

## 🚀 Quick Start (Local Development)

### 1. Start the LiveKit Server

We use Docker to run the LiveKit SFU. A compose file is provided in the project root.

```bash
# Run from the project root
docker compose -f docker-compose.livekit.yml up -d
```

The server will be reachable at `ws://localhost:7880`.

- **API Key**: `devkey`
- **Secret**: `secret`

### 2. Setup Your Environment

Ensure your Python environment includes the LiveKit SDK. If you are using `uv`:

```bash
cd repos/beavr-network
uv sync
```

### 3. Run the Simulation

Launch `beavr-sim` with streaming enabled:

```bash
uv run python -m beavr_sim.main --enable_streaming
```

### 4. View the Stream

Open your browser and navigate to:
[http://localhost:8080](http://localhost:8080)

If the active scene defines multiple cameras and `--stream_camera` is not set,
`beavr-sim` starts one LiveKit track per scene camera on incrementing HTTP ports:
`8080`, `8081`, and so on. The HTTP endpoints expose per-camera tokens and
track metadata; the publisher uses one LiveKit room connection for all sim
tracks. For example, `scene_hello_world` exposes its overhead camera at `8080`
and its wrist-test camera at `8081`.

To override the default camera list or ports:

```bash
uv run python -m beavr_sim.main \
  --enable_streaming \
  --stream_cameras='[overhead,wrist]' \
  --stream_ports='[8080,8081]'
```

---

## 🛠️ Developer Guide

### Dependencies

The following Python packages must be installed (handled automatically by `beavr-network` dependencies):

- `livekit`: The Real-time SDK for publishing/subscribing to tracks.
- `livekit-api`: Used for server-side JWT token generation.

### Single Source of Truth

To ensure no "async drift" or timing conflicts between the simulation physics and the video stream, all streaming constants are centralized in:
`src/beavr_network/video/livekit/constants.py`

**The Gold Standard (Ultra Quality):**

- **Resolution**: 2560x1440 (1440p)
- **Framerate**: 60 FPS
- **Bitrate**: 8 Mbps (Optimal for quality vs. decoder load)
- **Physics Sync**: The physics loop must run at a multiple of the FPS (e.g., 480 Hz) to ensure a deterministic 8:1 step-to-frame ratio.

### Architecture Overview

1. **LiveKit Server (Docker)**: Acts as the SFU. It receives one high-quality track per simulation camera and distributes each track to any number of connected web clients.
2. **LiveKitStreamServer / LiveKitMultiStreamServer (Python)**:
   - Connects to the room using a JWT token.
   - Creates a `VideoSource` and named `LocalVideoTrack` for each camera.
   - Uses **simulcast** so clients can adapt to available bandwidth.
   - Manages internal `aiohttp` servers to provide tokens and track metadata to web clients.
3. **Web Client (JS)**: Located in `clients/web/`, it uses the LiveKit JS SDK to automatically connect and attach the requested video track to an HTML5 video element.

## 🛑 Troubleshooting

- **Docker Permission Denied**: If you get a socket error, ensure your user is in the `docker` group: `sudo usermod -aG docker $USER`.
- **Micro-stutter**: Ensure your simulation physics rate is exactly `480 Hz`. If the physics rate fluctuates, the deterministic rendering will look jerky.
- **Browser Lag**: If the browser is struggling, verify that `chrome://webrtc-internals` shows a stable bitrate of ~8 Mbps. If your hardware cannot decode 1440p60 fast enough, try lowering the resolution in `constants.py`.
