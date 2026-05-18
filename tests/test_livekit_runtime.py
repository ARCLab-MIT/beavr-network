import pytest

from beavr_network.video.livekit import (
    LiveKitCameraStream,
    LiveKitConfig,
    LiveKitMultiStreamServer,
    LiveKitStreamServer,
    create_livekit_server_runtime,
)
from beavr_network.video.livekit.server import _viewer_identity


class _FakeCamera:
    def async_read(self, timeout_ms: int = 200):
        return None


def test_livekit_runtime_uses_single_track_server_for_one_stream():
    stream = LiveKitCameraStream(
        _FakeCamera(),
        LiveKitConfig(track_name="camera", http_port=8080),
    )

    runtime = create_livekit_server_runtime([stream])

    assert isinstance(runtime.server, LiveKitStreamServer)
    assert runtime.thread.name == "LiveKitStreamServer-camera"
    assert runtime.streams == (stream,)


def test_livekit_runtime_uses_multi_track_server_for_multiple_streams():
    streams = [
        LiveKitCameraStream(_FakeCamera(), LiveKitConfig(track_name="camera-one", http_port=8080)),
        LiveKitCameraStream(_FakeCamera(), LiveKitConfig(track_name="camera-two", http_port=8081)),
    ]

    runtime = create_livekit_server_runtime(streams, multi_thread_name="SharedLiveKit")

    assert isinstance(runtime.server, LiveKitMultiStreamServer)
    assert runtime.thread.name == "SharedLiveKit"
    assert runtime.streams == tuple(streams)


def test_livekit_runtime_requires_at_least_one_stream():
    with pytest.raises(ValueError, match="At least one LiveKit camera stream is required"):
        create_livekit_server_runtime([])


def test_viewer_identity_is_unique_for_reloaded_clients():
    identities = {_viewer_identity("camera-overhead") for _ in range(100)}

    assert len(identities) == 100
    assert all(identity.startswith("viewer-camera-overhead-") for identity in identities)
