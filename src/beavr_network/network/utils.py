from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import zmq

if TYPE_CHECKING:
    from .publisher import ZMQPublisherManager

logger = logging.getLogger(__name__)


# Exceptions
class SerializationError(Exception):
    """Exception raised for serialization/deserialization issues"""

    pass


# Global ZMQ context (one per process)
_GLOBAL_ZMQ_CONTEXT = zmq.Context()


def get_global_context() -> zmq.Context:
    """Get the global ZMQ context (shared across all sockets)"""
    global _GLOBAL_ZMQ_CONTEXT
    return _GLOBAL_ZMQ_CONTEXT


def set_global_context(context: zmq.Context) -> None:
    """Set a custom global ZMQ context (useful for testing).

    Args:
        context: The ZMQ context to use globally
    """
    global _GLOBAL_ZMQ_CONTEXT
    _GLOBAL_ZMQ_CONTEXT = context


def cleanup_zmq_resources() -> None:
    """Clean up all ZMQ resources gracefully.

    This function should be called before process termination to ensure
    proper cleanup of all ZMQ resources, including threads and sockets.
    """
    try:
        # Import locally to avoid circular imports at module import time
        from .handshake import HandshakeCoordinator
        from .publisher import ZMQPublisherManager

        # Stop the publisher manager and its monitor thread
        manager = ZMQPublisherManager.get_instance()
        if hasattr(manager, "close_all"):
            manager.close_all()

        # Clean up handshake coordinator
        HandshakeCoordinator.cleanup_all()

        # Then terminate the context
        context = get_global_context()
        if isinstance(context, zmq.Context):
            context.term()
    except Exception as e:
        logger.debug(f"Cleanup error (can be ignored in test environment): {e}")


def create_push_socket(host: str, port: int) -> zmq.Socket:
    """Create a PUSH socket with error handling."""
    socket = get_global_context().socket(zmq.PUSH)
    addr = f"tcp://{host}:{port}"
    socket.bind(addr)
    return socket


def create_pull_socket(host: str, port: int) -> zmq.Socket:
    """Create a PULL socket with error handling and connection verification."""
    socket = get_global_context().socket(zmq.PULL)
    socket.setsockopt(zmq.CONFLATE, 1)
    socket.setsockopt(zmq.RCVTIMEO, 100)  # 100ms timeout for receiving
    socket.setsockopt(zmq.LINGER, 0)  # Don't wait on close
    socket.setsockopt(zmq.RCVHWM, 1)  # Only keep latest message

    addr = f"tcp://{host}:{port}"
    # Try to bind to the address
    socket.bind(addr)

    # Test if the socket is actually bound
    bound_addrs = socket.getsockopt_string(zmq.LAST_ENDPOINT)
    if not bound_addrs:
        raise zmq.ZMQError("Socket binding verification failed")

    return socket


def create_response_socket(host: str, port: int) -> zmq.Socket:
    """Create a REP socket with error handling."""

    socket = get_global_context().socket(zmq.REP)
    addr = f"tcp://{host}:{port}"
    socket.bind(addr)
    return socket


def create_request_socket(host: str, port: int) -> zmq.Socket:
    """Create a REQ socket with error handling."""
    socket = get_global_context().socket(zmq.REQ)
    addr = f"tcp://{host}:{port}"
    socket.connect(addr)
    return socket


def setup_zmq_context() -> zmq.Context:
    """Get the global ZMQ context.

    Returns:
        zmq.Context: The global ZMQ context instance
    """
    return get_global_context()


def setup_publisher_manager(context: zmq.Context) -> ZMQPublisherManager:
    """Get the ZMQ publisher manager instance.

    Args:
        context: The ZMQ context

    Returns:
        ZMQPublisherManager: The singleton publisher manager instance
    """
    from .publisher import ZMQPublisherManager

    return ZMQPublisherManager.get_instance(context)


def cleanup_subscribers(subscribers_dict: dict[str, Any], component_name: str) -> None:
    """Clean up all subscribers in the provided dictionary.

    This function safely stops all subscribers in parallel for faster cleanup,
    logging any errors encountered.

    Args:
        subscribers_dict: Dictionary mapping topic names to subscriber objects
        component_name: Name of the component for logging purposes
    """
    # First, signal all subscribers to stop (non-blocking)
    subscribers_to_join = []
    for topic, subscriber in subscribers_dict.items():
        if subscriber:
            try:
                # Set running flag to False (non-blocking)
                subscriber._running = False
                # Close socket to wake up any blocking operations
                if subscriber._socket and subscriber._poller:
                    try:
                        subscriber._poller.unregister(subscriber._socket)
                        subscriber._socket.close()
                        subscriber._socket = None
                        subscriber._poller = None
                    except Exception as e:
                        logger.warning(f"Error closing socket for {topic} in {component_name}: {e}")
                subscribers_to_join.append((topic, subscriber))
            except Exception as e:
                logger.warning(f"Error stopping subscriber {topic} in {component_name}: {e}")

    # Then join all threads with a shorter timeout (subscribers poll every 100ms)
    # So 500ms should be more than enough for graceful shutdown
    for topic, subscriber in subscribers_to_join:
        try:
            subscriber.join(timeout=0.5)
            if subscriber.is_alive():
                logger.warning(
                    f"Subscriber thread for {topic} in {component_name} did not stop within timeout"
                )
        except Exception as e:
            logger.warning(f"Error joining subscriber thread {topic} in {component_name}: {e}")


def wait_for_server_ready(
    host: str,
    port: int,
    command: Any,
    timeout_sec: float = 30.0,
    check_interval_sec: float = 0.5,
    is_alive_callback: callable = None,
) -> bool:
    """Wait for a BEAVR server to be ready by checking manifest/ready endpoint.

    This is a shared utility for checking if a simulation server or similar
    ZMQ-based server is ready to accept connections.

    Args:
        host: Server host address (e.g., "127.0.0.1")
        port: Manifest/ready-check port
        timeout_sec: Maximum time to wait for server
        check_interval_sec: Time between ready checks
        is_alive_callback: Optional callback that returns False if we should abort waiting
                          (e.g., if the process exited)

    Returns:
        True if server is ready, False if timeout or abort
    """
    import time

    start_time = time.time()
    context = zmq.Context()

    try:
        socket = context.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, 1000)  # 1 second timeout per attempt
        socket.setsockopt(zmq.LINGER, 0)
        endpoint = f"tcp://{host}:{port}"
        socket.connect(endpoint)

        while time.time() - start_time < timeout_sec:
            # Check if we should abort
            if is_alive_callback is not None and not is_alive_callback():
                logger.error("Server process exited during startup")
                return False

            try:
                socket.send_string(command.PING)
                response = socket.recv_string()
                if response == "OK":
                    logger.info(f"✅ Server at {endpoint} is ready")
                    return True
            except zmq.Again:
                # Timeout, try again
                pass
            except zmq.ZMQError as e:
                logger.debug(f"ZMQ error checking server: {e}")
                # Recreate socket for retrying (ZMQ REQ/REP strict alternation)
                socket.close()
                socket = context.socket(zmq.REQ)
                socket.setsockopt(zmq.RCVTIMEO, 1000)
                socket.setsockopt(zmq.LINGER, 0)
                socket.connect(endpoint)

            time.sleep(check_interval_sec)

        logger.error(f"Timeout waiting for server at {endpoint}")
        return False

    finally:
        socket.close()
        context.term()
