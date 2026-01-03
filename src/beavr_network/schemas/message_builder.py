"""Centralized FlatBuffer message builders for teleoperation components.

This module provides reusable builder classes for all FlatBuffer message types
used in the teleoperation system, following a consistent pattern for performance
and maintainability.
"""

import time

import flatbuffers
import numpy as np

from beavr_network.schemas.fbs.teleop.CartesianState import CartesianStateT
from beavr_network.schemas.fbs.teleop.Command import Command
from beavr_network.schemas.fbs.teleop.CommandMessage import (
    CommandMessageAddCommand,
    CommandMessageEnd,
    CommandMessageStart,
)
from beavr_network.schemas.fbs.teleop.HandSide import HandSide
from beavr_network.schemas.fbs.teleop.InputFrame import InputFrameT
from beavr_network.schemas.fbs.teleop.IsRelative import IsRelative
from beavr_network.schemas.fbs.teleop.JointState import JointStateT
from beavr_network.schemas.fbs.teleop.Resolution import Resolution
from beavr_network.schemas.fbs.teleop.VRInput import VRInputT


def build_command_message(command: Command) -> bytes:
    """Build a CommandMessage FlatBuffer message."""
    command_builder = flatbuffers.Builder(256)
    CommandMessageStart(command_builder)
    CommandMessageAddCommand(command_builder, command)
    command_message = CommandMessageEnd(command_builder)
    command_builder.Finish(command_message)

    return command_builder.Output()


class VRInputMessageBuilder:
    """Utility class for building VR-related FlatBuffer messages with reusable builders."""

    def __init__(self):
        """Initialize with reusable builders for performance."""
        self._vr_input_builder = flatbuffers.Builder(1024)
        self._command_builder = flatbuffers.Builder(256)

    def build_vr_input(self, keypoints: np.ndarray, hand_side: HandSide, is_relative: IsRelative) -> bytes:
        """Build a VRInput FlatBuffer message."""
        vr_input_t = VRInputT()
        vr_input_t.keypoints = keypoints
        vr_input_t.handSide = hand_side
        vr_input_t.isRelative = is_relative
        vr_input_t.command = Command.resume  # Default
        vr_input_t.resolution = Resolution.High  # Default

        self._vr_input_builder.Clear()
        vr_input = vr_input_t.Pack(self._vr_input_builder)
        self._vr_input_builder.Finish(vr_input)

        return self._vr_input_builder.Output()

    def build_command_message(self, command: Command) -> bytes:
        """Build a CommandMessage FlatBuffer message."""
        return build_command_message(command)


class InputFrameBuilder:
    """Utility class for building InputFrame FlatBuffer messages with a reusable builder."""

    def __init__(self):
        self._input_frame_builder = flatbuffers.Builder(1024)

    def build_input_frame(
        self,
        hand_side: HandSide,
        coordinate_frame: np.ndarray,
        keypoints: np.ndarray,
        is_relative: IsRelative,
        command: Command,
    ) -> bytes:
        """Build InputFrame FlatBuffer message."""

        input_frame_t = InputFrameT()
        input_frame_t.handSide = hand_side
        # Ensure we send float32 to match FlatBuffers schema [float]
        # Failing to do this when input is float64 results in garbage values on deserialization
        input_frame_t.coordinateFrame = coordinate_frame.astype(np.float32)
        input_frame_t.keypoints = keypoints.astype(np.float32)
        input_frame_t.isRelative = is_relative
        input_frame_t.command = command

        self._input_frame_builder.Clear()
        input_frame = input_frame_t.Pack(self._input_frame_builder)
        self._input_frame_builder.Finish(input_frame)

        return self._input_frame_builder.Output()


class CartesianStateBuilder:
    """Utility class for building CartesianState FlatBuffer messages with a reusable builder."""

    def __init__(self):
        self._cartesian_state_builder = flatbuffers.Builder(1024)  # Increased size for larger messages

    def build_cartesian_state_quat(
        self, hand_side: HandSide, position: np.ndarray, orientation_quat: np.ndarray
    ) -> bytes:
        """
        Build a CartesianState object.

        Args:
            hand_side: Hand side enum
            position: 3-element position vector
            orientation_quat: 4-element quaternion

        Returns:
            CartesianState object
        """

        cartesian_state_t = CartesianStateT()
        cartesian_state_t.handSide = hand_side
        cartesian_state_t.positionMeters = position
        cartesian_state_t.orientationQuat = orientation_quat
        cartesian_state_t.timestamp = time.time() * 1000  # Add millisecond timestamp for latency tracking

        self._cartesian_state_builder.Clear()
        cartesian_state = cartesian_state_t.Pack(self._cartesian_state_builder)
        self._cartesian_state_builder.Finish(cartesian_state)

        return self._cartesian_state_builder.Output()

    def build_cartesian_state_homo(self, homo_matrix: np.ndarray, hand_side: HandSide) -> bytes:
        """
        Build CartesianState FlatBuffer message with homogeneous matrix.

        Args:
            homo_matrix: 16-element flattened homogeneous transformation matrix
            hand_side: Hand side string ('left' or 'right')

        Returns:
            CartesianState object
        """

        cartesian_state_t = CartesianStateT()
        cartesian_state_t.handSide = hand_side
        cartesian_state_t.homoMatrix = homo_matrix
        cartesian_state_t.timestamp = time.time() * 1000  # Add millisecond timestamp for latency tracking

        self._cartesian_state_builder.Clear()
        cartesian_state = cartesian_state_t.Pack(self._cartesian_state_builder)
        self._cartesian_state_builder.Finish(cartesian_state)

        return self._cartesian_state_builder.Output()


class JointStateBuilder:
    """Utility class for building JointState FlatBuffer messages with a reusable builder."""

    def __init__(self):
        self._joint_builder = flatbuffers.Builder(512)

    def build_joint_state(self, hand_side: HandSide, joint_positions_rad: np.ndarray) -> bytes:
        """
        Build JointState FlatBuffer message with joint positions.

        Args:
            hand_side: Hand side enum
            joint_positions_rad: List of joint positions in radians

        Returns:
            Serialized FlatBuffer bytes
        """

        joint_state_t = JointStateT()
        joint_state_t.handSide = hand_side
        # Ensure we send float32 to match FlatBuffers schema [float]
        # Failing to do this when input is float64 results in garbage values on deserialization
        joint_state_t.jointPositionsRad = joint_positions_rad.astype(np.float32)

        self._joint_builder.Clear()
        joint_state = joint_state_t.Pack(self._joint_builder)
        self._joint_builder.Finish(joint_state)

        return self._joint_builder.Output()


class CommandMessageBuilder:
    """Utility class for building CommandMessage FlatBuffer messages with a reusable builder."""

    def __init__(self):
        self._command_builder = flatbuffers.Builder(256)

    def build_command_message(self, command: Command) -> bytes:
        """Build a CommandMessage FlatBuffer message."""
        return build_command_message(command)
