import threading

_shutdown_event = threading.Event()


def get_shutdown_event() -> threading.Event:
    """Get the global shutdown event for this process."""
    return _shutdown_event
