import numpy as np
import pytest

from beavr_network.schemas.fbs.teleop.CartesianState import CartesianState
from beavr_network.schemas.fbs.teleop.Command import Command
from beavr_network.schemas.fbs.teleop.HandSide import HandSide
from beavr_network.schemas.fbs.teleop.InputFrame import InputFrame
from beavr_network.schemas.fbs.teleop.IsRelative import IsRelative
from beavr_network.schemas.fbs.teleop.JointState import JointState
from beavr_network.schemas.fbs.teleop.VRInput import VRInput
from beavr_network.schemas.message_builder import (
    CartesianStateBuilder,
    InputFrameBuilder,
    JointStateBuilder,
    VRInputMessageBuilder,
)


@pytest.fixture
def cartesian_builder():
    return CartesianStateBuilder()


@pytest.fixture
def joint_builder():
    return JointStateBuilder()


@pytest.fixture
def input_frame_builder():
    return InputFrameBuilder()


@pytest.fixture
def vr_input_builder():
    return VRInputMessageBuilder()


def test_cartesian_state_contract(cartesian_builder):
    """Verify CartesianState can round-trip through serialization."""
    pos = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    quat = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    hand_side = HandSide.right

    data = cartesian_builder.build_cartesian_state_quat(hand_side, pos, quat)

    # Deserialize
    state = CartesianState.GetRootAsCartesianState(data, 0)

    assert state.HandSide() == hand_side
    assert np.allclose(state.PositionMetersAsNumpy(), pos)
    assert np.allclose(state.OrientationQuatAsNumpy(), quat)
    assert state.Timestamp() > 0


def test_joint_state_contract(joint_builder):
    """Verify JointState can round-trip through serialization."""
    positions = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], dtype=np.float32)
    velocities = np.array([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07], dtype=np.float32)
    hand_side = HandSide.left

    data = joint_builder.build_joint_state(hand_side, positions, velocities)

    # Deserialize
    state = JointState.GetRootAsJointState(data, 0)

    assert state.HandSide() == hand_side
    assert np.allclose(state.JointPositionsRadAsNumpy(), positions)
    assert np.allclose(state.JointVelocitiesRadAsNumpy(), velocities)


def test_input_frame_contract(input_frame_builder):
    """Verify InputFrame (VR/Controller) can round-trip through serialization."""
    hand_side = HandSide.right
    coordinate_frame = np.eye(4, dtype=np.float32).ravel()
    keypoints = np.random.rand(21, 3).astype(np.float32).ravel()
    is_relative = IsRelative.relative
    command = Command.resume

    data = input_frame_builder.build_input_frame(hand_side, coordinate_frame, keypoints, is_relative, command)

    # Deserialize
    frame = InputFrame.GetRootAsInputFrame(data, 0)

    assert frame.HandSide() == hand_side
    assert np.allclose(frame.CoordinateFrameAsNumpy(), coordinate_frame)
    assert np.allclose(frame.KeypointsAsNumpy(), keypoints)
    assert frame.IsRelative() == is_relative
    assert frame.Command() == command


def test_vr_input_contract_includes_hand_orientation(vr_input_builder):
    """Verify VRInput can round-trip streamed hand root orientation."""
    keypoints = np.random.rand(26, 3).astype(np.float32)
    hand_orientation_quat = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)

    data = vr_input_builder.build_vr_input(
        keypoints,
        HandSide.right,
        IsRelative.relative,
        hand_orientation_quat=hand_orientation_quat,
    )

    vr_input = VRInput.GetRootAs(data, 0)

    assert vr_input.HandSide() == HandSide.right
    assert vr_input.IsRelative() == IsRelative.relative
    assert vr_input.Command() == Command.resume
    assert np.allclose(vr_input.KeypointsAsNumpy(), keypoints.ravel())
    assert np.allclose(vr_input.HandOrientationQuatAsNumpy(), hand_orientation_quat)
