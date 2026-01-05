"""Integration tests for protocol compatibility across beavr-sim and beavr-teleop.

These tests verify that the shared protocol is used consistently and correctly.
"""

import pytest
from beavr_network import DefaultPorts, ProtocolConfig, TopicBuilder, TopicPrefix
from beavr_network.schemas.fbs.teleop.HandSide import HandSide


class TestTopicNaming:
    """Test topic builder produces protocol-compliant names."""

    def test_action_topic_without_side_suffix(self):
        """Test action topic when side is already in robot name."""
        topic = TopicBuilder.build_action_topic("arm_xarm7_right", HandSide.right)
        assert topic == "action_arm_xarm7_right"
        assert TopicBuilder.validate_topic(topic)

    def test_action_topic_with_side_suffix(self):
        """Test action topic when side needs to be added."""
        topic = TopicBuilder.build_action_topic("arm_xarm7", HandSide.right)
        assert topic == "action_arm_xarm7_right"
        assert TopicBuilder.validate_topic(topic)

    def test_observation_topic_without_side_suffix(self):
        """Test observation topic when side is already in robot name."""
        topic = TopicBuilder.build_observation_topic("leap_left", HandSide.left)
        assert topic == "observation_leap_left"
        assert TopicBuilder.validate_topic(topic)

    def test_observation_topic_with_side_suffix(self):
        """Test observation topic when side needs to be added."""
        topic = TopicBuilder.build_observation_topic("leap", HandSide.left)
        assert topic == "observation_leap_left"
        assert TopicBuilder.validate_topic(topic)

    def test_topic_validation_rejects_invalid(self):
        """Test that invalid topics are rejected."""
        assert not TopicBuilder.validate_topic("invalid_topic")
        assert not TopicBuilder.validate_topic("action")
        assert not TopicBuilder.validate_topic("action_")

    def test_topic_validation_accepts_valid(self):
        """Test that valid topics are accepted."""
        assert TopicBuilder.validate_topic("action_robot")
        assert TopicBuilder.validate_topic("observation_robot_right")
        assert TopicBuilder.validate_topic("action_arm_xarm7_left")


class TestPortConstants:
    """Test that default ports are consistent."""

    def test_manifest_port(self):
        """Test manifest port is 5554."""
        assert DefaultPorts.MANIFEST == 5554

    def test_observation_port(self):
        """Test observation port is 5555."""
        assert DefaultPorts.OBSERVATION == 5555

    def test_action_port(self):
        """Test action port is 5556."""
        assert DefaultPorts.ACTION == 5556

    def test_ports_are_unique(self):
        """Test all ports are  unique."""
        ports = [DefaultPorts.MANIFEST, DefaultPorts.OBSERVATION, DefaultPorts.ACTION]
        assert len(ports) == len(set(ports))


class TestProtocolConfig:
    """Test protocol configuration constants."""

    def test_version(self):
        """Test protocol version is defined."""
        assert ProtocolConfig.VERSION
        assert isinstance(ProtocolConfig.VERSION, str)

    def test_topic_separator(self):
        """Test topic separator is underscore."""
        assert ProtocolConfig.TOPIC_SEPARATOR == "_"

    def test_validation_pattern(self):
        """Test validation pattern is defined."""
        import re

        pattern = ProtocolConfig.VALID_TOPIC_PATTERN
        assert pattern

        # Test pattern works
        assert re.match(pattern, "action_robot")
        assert re.match(pattern, "observation_robot_right")
        assert not re.match(pattern, "invalid")


class TestTopicPrefixes:
    """Test topic prefix enum."""

    def test_action_prefix(self):
        """Test action prefix."""
        assert TopicPrefix.ACTION == "action_"

    def test_observation_prefix(self):
        """Test observation prefix."""
        assert TopicPrefix.OBSERVATION == "observation_"


class TestCrossRepoConsistency:
    """Test that topic generation is deterministic and consistent."""

    @pytest.mark.parametrize(
        "robot,side,expected_action,expected_obs",
        [
            ("arm_xarm7", HandSide.right, "action_arm_xarm7_right", "observation_arm_xarm7_right"),
            ("arm_xarm7_left", HandSide.left, "action_arm_xarm7_left", "observation_arm_xarm7_left"),
            ("leap", HandSide.right, "action_leap_right", "observation_leap_right"),
            ("hand_leap", HandSide.left, "action_hand_leap_left", "observation_hand_leap_left"),
        ],
    )
    def test_topic_generation_consistency(self, robot, side, expected_action, expected_obs):
        """Test that topic generation is consistent for common robot types."""
        action = TopicBuilder.build_action_topic(robot, side)
        observation = TopicBuilder.build_observation_topic(robot, side)

        assert action == expected_action
        assert observation == expected_obs
        assert TopicBuilder.validate_topic(action)
        assert TopicBuilder.validate_topic(observation)
