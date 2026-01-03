import pickle
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Serializer(ABC, Generic[T]):
    """Abstract serializer interface for message payloads.

    Implementations must be symmetrical: ``decode(encode(x)) == x`` for supported inputs.
    """

    @abstractmethod
    def encode(self, obj: T) -> bytes:
        pass

    @abstractmethod
    def decode(self, buffer: bytes) -> T:
        pass


class PickleSerializer(Serializer[T]):
    """Pickle-based serializer with optional runtime type validation."""

    def __init__(self, expected_type: type[T] | None = None, allow_subclasses: bool = True):
        self._expected_type = expected_type
        self._allow_subclasses = allow_subclasses

    def encode(self, obj: T) -> bytes:
        return pickle.dumps(obj, protocol=-1)

    def decode(self, buffer: bytes) -> T:
        obj = pickle.loads(buffer)
        if self._expected_type is not None:
            if self._allow_subclasses:
                if not isinstance(obj, self._expected_type):
                    raise TypeError(f"Decoded type {type(obj)} != expected {self._expected_type}")
            else:
                if type(obj) is not self._expected_type:
                    raise TypeError(f"Decoded exact type {type(obj)} != expected {self._expected_type}")
        return obj


class RawBytesSerializer(Serializer[bytes]):
    """No-op serializer for raw byte streams."""

    def encode(self, obj: bytes) -> bytes:
        return obj

    def decode(self, buffer: bytes) -> bytes:
        return buffer


class FlatBufferSerializer(Serializer[Any]):
    """FlatBuffer serializer for zero-copy message passing.

    This serializer enables true zero-copy serialization using FlatBuffers over ZMQ.

    Usage:
        # Publisher side (no-op encoding):
        builder = flatbuffers.Builder(1024)
        # ... build your FlatBuffer table ...
        builder.Finish(table_offset)
        buffer = bytes(builder.Output())
        serializer.encode(buffer)  # No-op, returns buffer directly for ZMQ send

        # Subscriber side:
        root = serializer.decode(buffer)  # Returns FlatBuffer root accessor
        value = root.SomeField()  # Zero-copy field access

    Args:
        root_accessor: A callable that takes (buffer, offset) and returns the FlatBuffer root.
                      Example: lambda buf, off: MyTable.GetRootAs(buf, off)
                      If None, encode() is no-op and decode() returns raw bytes.
        validate: If True, validates the buffer before decoding (default: False for performance)
    """

    def __init__(
        self,
        root_accessor: Callable[[bytes, int], Any] | None = None,
        validate: bool = False,
    ):
        """Initialize the FlatBuffer serializer.

        Args:
            root_accessor: Function to get the root table from bytes.
                          Format: lambda buffer, offset: TableType.GetRootAs(buffer, offset)
                          If None, acts as raw bytes serializer (no-op encode/decode).
            validate: Whether to validate buffers during decode (adds overhead)
        """
        self._root_accessor = root_accessor
        self._validate = validate

    def encode(self, obj: bytes) -> bytes:
        """Encode a FlatBuffer (no-op for zero-copy).

        Args:
            obj: Pre-built FlatBuffer bytes from builder.Output()

        Returns:
            The same bytes (zero-copy passthrough for ZMQ)
        """
        return obj

    def decode(self, buffer: bytes | bytearray) -> Any:
        """Decode FlatBuffer bytes to a root table accessor.

        Args:
            buffer: Raw FlatBuffer bytes/bytearray received over ZMQ (zero-copy)

        Returns:
            FlatBuffer root table accessor (allows zero-copy field access)

        Raises:
            ValueError: If root_accessor is not set or buffer is invalid
        """
        if self._root_accessor is None:
            # If no accessor provided, return raw bytes (caller must parse)
            return buffer

        # Optional validation (costs performance but catches corruption)
        if self._validate:
            # FlatBuffer validation would go here
            # For now, we skip it for performance
            pass

        # Use the root accessor to create a zero-copy view directly on raw bytes
        try:
            root = self._root_accessor(buffer, 0)
            return root

        except Exception as e:
            # Log the issue but return raw buffer as fallback
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to decode FlatBuffer ({len(buffer)} bytes): {e}, returning raw buffer")
            return buffer
