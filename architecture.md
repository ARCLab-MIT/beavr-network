# beavr-network Architecture

## Video Streaming Flow

```mermaid
flowchart LR
    subgraph sim [beavr-sim]
        Camera[SimCameraProvider]
        LKServer[LiveKitStreamServer]
    end

    subgraph docker [Docker]
        SFU[LiveKit SFU :7880]
    end

    subgraph clients [Clients]
        Web[Web Browser]
        Desktop[beavr-desktop]
        VR[Quest VR]
    end

    Camera --> LKServer
    LKServer -->|publish| SFU
    SFU -->|subscribe| Web
    SFU -->|subscribe| Desktop
    SFU -->|subscribe| VR
```

## Key Components

| Component | Port | Description |
|-----------|------|-------------|
| LiveKit SFU | 7880 | WebRTC signaling server (Docker) |
| LiveKitStreamServer | 8080 | HTTP `/info` endpoint + video publisher |
| SimCameraProvider | - | MuJoCo camera frame capture |

## Connection Flow

1. Client fetches `GET /info` from LiveKitStreamServer (port 8080)
2. Response contains: `livekit_url`, `room`, `resolution`, `fps`
3. Client connects to LiveKit SFU and joins the room
4. Client subscribes to video track published by `sim-camera`

## Complete Streaming Architecture Flow

```mermaid
flowchart TB
    subgraph Source[Video Source]
        SimCam[SimCameraProvider<br/>MuJoCo Frame Capture]
        HWCam[OpenCVCamera<br/>Hardware Camera]
    end

    subgraph StreamServer[LiveKitStreamServer :8080]
        FrameLoop[Frame Loop<br/>30-60 FPS]
        Publisher[LiveKit Publisher<br/>Video Track]
        TokenServer[Token Server<br/>HTTP /info /token]
    end

    subgraph Docker[Docker Container]
        LiveKitSFU[LiveKit SFU :7880<br/>WebRTC Signaling]
        WebRTC[WebRTC Media<br/>UDP :7882]
    end

    subgraph Clients[Remote Clients]
        Web[Web Browser<br/>localhost:8080]
        Mobile[Mobile Device<br/>10.29.x.x:8080]
        VR[VR Headset<br/>18.29.x.x:8080]
    end

    subgraph Network[Network Layer]
        WS[WebSocket<br/>Signaling]
        UDP[UDP Media<br/>RTP/RTCP]
        TURN[TURN Server<br/>If Needed]
    end

    SimCam -->|Frames| FrameLoop
    HWCam -->|Frames| FrameLoop
    FrameLoop -->|Publish| Publisher
    Publisher -->|WebRTC| LiveKitSFU
    TokenServer -->|GET /info| Web
    TokenServer -->|GET /info| Mobile
    TokenServer -->|GET /info| VR
    LiveKitSFU -->|WS| WS
    LiveKitSFU -->|UDP| UDP
    WS --> Web
    WS --> Mobile
    WS --> VR
    UDP --> Web
    UDP -->|Blocked| Mobile
    UDP -->|Blocked| VR
    UDP -.->|Relay| TURN
    TURN -.->|Relay| Mobile
    TURN -.->|Relay| VR

    style Source fill:#e1f5ff
    style StreamServer fill:#fff4e1
    style Docker fill:#ffe1f5
    style Clients fill:#e1ffe1
    style Network fill:#f5e1ff
    style TURN fill:#ffcccc,stroke-dasharray: 5 5
```

**Network Considerations:**
- **WebSocket (TCP)**: Always works for signaling
- **UDP Media**: Requires same subnet or TURN server for cross-subnet connectivity
- **TURN Server**: Needed when devices are on different network segments (e.g., MIT WiFi subnets)

