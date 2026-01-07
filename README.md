# beavr-network

A shared networking library for the BEAVR ecosystem, providing ZMQ-based messaging, serialization, and synchronization utilities.

## Overview

This package is designed to be used as a shared dependency across multiple BEAVR repositories (e.g., `beavr-sim` and `beavr-control`). By centralizing the networking logic, we ensure consistent communication protocols and message formats throughout the system.

## Setup for Development

If you are working on this library and want to see changes reflected immediately in your other projects, use an **editable install**.

### 1. Clone the repository

```bash
git clone https://github.com/ARCLab-MIT/beavr-network.git
```

### 2. Link to your project (using `uv`)

In your simulation or control repository, run:

```bash
uv add --editable ../path/to/beavr-network
```

This will update your local environment to point directly to your local `beavr-network` source code.

## Usage in Production

To use this library as a standard dependency in another project, add it to your `pyproject.toml`:

```toml
[project]
dependencies = [
    "beavr-network",
]

[tool.uv.sources]
beavr-network = { git = "https://github.com/ARCLab-MIT/beavr-network.git", branch = "main" }
```

## Features

- **LiveKit Streaming**: High-performance WebRTC video streaming via LiveKit SFU. See [LIVEKIT.md](docs/LIVEKIT.md) for setup and dev guide.
- **ZMQ Patterns**: Simplified wrappers for PUB/SUB and REQ/REP.
- **Serialization**: Support for Pickle (numpy-friendly) and FlatBuffers (zero-copy).
- **Handshaking**: Utilities for guaranteed delivery and connection synchronization.
- **Thread-safe Management**: Centralized `ZMQPublisherManager` for handling multiple endpoints across threads.

---
*Maintained by the ARCLab BEAVR Team.*
