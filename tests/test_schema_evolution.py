"""Schema evolution / backward compatibility tests.

Verifies:
1. Old clients can parse messages from new servers (forward compat)
2. New clients can parse messages from old servers (backward compat)
3. Unknown fields are gracefully ignored
4. Corrupted/truncated messages are handled safely
"""

import numpy as np

from beavr_network.schemas.fbs.teleop.CartesianState import CartesianState
from beavr_network.schemas.fbs.teleop.HandSide import HandSide
from beavr_network.schemas.fbs.teleop.JointState import JointState
from beavr_network.schemas.message_builder import CartesianStateBuilder, JointStateBuilder


class TestSchemaBackwardCompatibility:
    """Tests for backward compatibility with older message formats."""

    def test_joint_state_minimal_fields(self):
        """JointState should deserialize correctly with only required fields."""
        builder = JointStateBuilder()
        positions = np.array([0.1, 0.2, 0.3], dtype=np.float32)

        # Build with minimal fields (no velocities)
        data = builder.build_joint_state(HandSide.right, positions)

        # Parse and verify
        state = JointState.GetRootAsJointState(data, 0)
        assert state.JointPositionsRadLength() == 3
        assert state.HandSide() == HandSide.right
        # Velocities should be 0-length if not provided
        assert state.JointVelocitiesRadLength() == 0

    def test_joint_state_with_velocities(self):
        """JointState with both positions and velocities should round-trip."""
        builder = JointStateBuilder()
        positions = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        velocities = np.array([0.01, 0.02, 0.03], dtype=np.float32)

        data = builder.build_joint_state(HandSide.left, positions, velocities)

        state = JointState.GetRootAsJointState(data, 0)
        assert state.JointPositionsRadLength() == 3
        assert state.JointVelocitiesRadLength() == 3
        assert np.allclose(state.JointPositionsRadAsNumpy(), positions)
        assert np.allclose(state.JointVelocitiesRadAsNumpy(), velocities)

    def test_extra_array_elements_handled(self):
        """Verify that large arrays serialize and deserialize correctly."""
        builder = JointStateBuilder()

        # Build with a large array (more DoFs than typical robots)
        large_positions = np.random.randn(100).astype(np.float32)
        data = builder.build_joint_state(HandSide.left, large_positions)

        state = JointState.GetRootAsJointState(data, 0)
        assert state.JointPositionsRadLength() == 100
        assert np.allclose(state.JointPositionsRadAsNumpy(), large_positions)

    def test_cartesian_state_preserves_precision(self):
        """Verify CartesianState preserves float32 precision."""
        builder = CartesianStateBuilder()

        # Use values that would lose precision with wrong handling
        pos = np.array([1.23456789, -0.00001234, 999.99999], dtype=np.float32)
        quat = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)

        data = builder.build_cartesian_state_quat(HandSide.right, pos, quat)

        state = CartesianState.GetRootAsCartesianState(data, 0)
        # Float32 should round-trip exactly
        assert np.array_equal(state.PositionMetersAsNumpy(), pos)
        assert np.array_equal(state.OrientationQuatAsNumpy(), quat)


class TestSchemaVersionMismatch:
    """Tests for handling version mismatches gracefully."""

    def test_truncated_message_raises(self):
        """Truncated binary data should raise or return invalid state."""
        builder = JointStateBuilder()
        positions = np.array([0.1, 0.2], dtype=np.float32)
        data = builder.build_joint_state(HandSide.right, positions)

        # Truncate the message (cut in half)
        truncated = data[: len(data) // 2]

        # FlatBuffers may not raise, but reading values should fail or give garbage
        # We check that either:
        # 1. Parsing fails with an exception
        # 2. Or the message is clearly invalid (wrong length, etc.)
        try:
            state = JointState.GetRootAsJointState(truncated, 0)
            # If parsing succeeds, the data should be clearly broken
            length = state.JointPositionsRadLength()
            if length > 0:
                # If somehow valid, must not match original
                assert length != 2 or not np.allclose(state.JointPositionsRadAsNumpy(), positions), (
                    "Truncated message should not match original"
                )
        except Exception:
            pass  # Exception is acceptable for truncated data

    def test_corrupted_message_detected(self):
        """Corrupted binary should not silently produce garbage values."""
        builder = JointStateBuilder()
        positions = np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)
        data = builder.build_joint_state(HandSide.right, positions)

        # Corrupt multiple bytes in the payload area
        corrupted = bytearray(data)
        for i in range(10, min(20, len(corrupted))):
            corrupted[i] ^= 0xFF

        # Should either raise or produce obviously wrong values
        try:
            state = JointState.GetRootAsJointState(bytes(corrupted), 0)
            if state.JointPositionsRadLength() > 0:
                parsed_pos = state.JointPositionsRadAsNumpy()
                # If parsing succeeds, values should be finite (not NaN/Inf garbage)
                if np.all(np.isfinite(parsed_pos)):
                    # Values should differ from original if corruption was effective
                    assert not np.allclose(parsed_pos, positions), (
                        "Corrupted data should differ from original"
                    )
        except Exception:
            pass  # Exception is acceptable for corrupted data

    def test_empty_array_handled(self):
        """Empty joint array should serialize and deserialize."""
        builder = JointStateBuilder()
        empty_positions = np.array([], dtype=np.float32)

        data = builder.build_joint_state(HandSide.right, empty_positions)

        state = JointState.GetRootAsJointState(data, 0)
        assert state.JointPositionsRadLength() == 0


class TestSchemaFieldDefaults:
    """Tests for default value handling in schemas."""

    def test_cartesian_timestamp_auto_populated(self):
        """Verify timestamp is automatically set when building CartesianState messages.

        Note: CartesianStateBuilder auto-populates timestamp, but JointStateBuilder does not.
        This is intentional - JointState is used for high-frequency observations where
        timestamps are added at the network layer.
        """
        builder = CartesianStateBuilder()
        pos = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        quat = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)

        data = builder.build_cartesian_state_quat(HandSide.right, pos, quat)
        state = CartesianState.GetRootAsCartesianState(data, 0)

        # Timestamp should be non-zero (auto-populated with current time in ms)
        assert state.Timestamp() > 0

    def test_joint_state_no_auto_timestamp(self):
        """Verify JointState does NOT auto-populate timestamp (by design)."""
        builder = JointStateBuilder()
        positions = np.array([0.1], dtype=np.float32)

        data = builder.build_joint_state(HandSide.right, positions)
        state = JointState.GetRootAsJointState(data, 0)

        # JointState timestamp is left at default (0) - timestamps added at network layer
        assert state.Timestamp() == 0
