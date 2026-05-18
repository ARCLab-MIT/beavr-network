"""Shared runtime helpers for LiveKit camera streaming."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .server import LiveKitCameraStream, LiveKitMultiStreamServer, LiveKitStreamServer


class LiveKitServer(Protocol):
    """Common lifecycle surface for single- and multi-track LiveKit servers."""

    def stream(self) -> None: ...

    def request_shutdown(self) -> None: ...

    def cleanup(self) -> None: ...

    def get_client_count(self) -> int: ...


@dataclass(frozen=True)
class LiveKitServerRuntime:
    """A concrete LiveKit server and the thread that runs it."""

    server: LiveKitServer
    thread: threading.Thread
    streams: tuple[LiveKitCameraStream, ...]


def create_livekit_server_runtime(
    streams: Sequence[LiveKitCameraStream],
    *,
    single_thread_name: str | None = None,
    multi_thread_name: str = "LiveKitMultiStreamServer",
) -> LiveKitServerRuntime:
    """Create the correct LiveKit server runtime for one or more camera streams.

    LiveKit's Python SDK is process/global-FFI backed, so multi-camera publishing
    should use one room connection with named tracks instead of independent
    publisher threads. Keeping that choice here prevents sim and hardware from
    drifting into subtly different streaming topologies.
    """
    stream_tuple = tuple(streams)
    if not stream_tuple:
        raise ValueError("At least one LiveKit camera stream is required")

    if len(stream_tuple) == 1:
        stream = stream_tuple[0]
        server: LiveKitServer = LiveKitStreamServer(stream.camera, stream.config)
        thread_name = single_thread_name or f"LiveKitStreamServer-{stream.config.track_name}"
    else:
        server = LiveKitMultiStreamServer(stream_tuple)
        thread_name = multi_thread_name

    thread = threading.Thread(
        target=server.stream,
        name=thread_name,
        daemon=True,
    )
    return LiveKitServerRuntime(server=server, thread=thread, streams=stream_tuple)
