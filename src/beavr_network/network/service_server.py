"""Reusable ZMQ REQ/REP service server base class.

Provides a threaded ZMQ REP socket that handles request/response patterns.
Subclasses implement handle_request() to process incoming messages.
"""

import json
import logging
import threading
from abc import ABC, abstractmethod

import zmq

logger = logging.getLogger(__name__)


class ZMQServiceServer(ABC):
    """Abstract base for ZMQ REQ/REP service servers.

    Runs in a background thread, handling requests and sending JSON responses.
    Subclasses implement handle_request() to define service behavior.

    Usage:
        class MyService(ZMQServiceServer):
            def handle_request(self, request: str, payload: str | None) -> dict:
                if request == "PING":
                    return {"status": "ok"}
                return {"error": f"Unknown request: {request}"}

        server = MyService(host="0.0.0.0", port=5557)
        server.start()
        # ... later ...
        server.stop()
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5557,
        context: zmq.Context | None = None,
        name: str | None = None,
    ):
        """Initialize the service server.

        Args:
            host: Host address to bind to
            port: Port number to bind to
            context: Optional ZMQ context (creates own if not provided)
            name: Optional server name for logging (defaults to class name)
        """
        self._host = host
        self._port = port
        self._context = context or zmq.Context()
        self._own_context = context is None
        self._name = name or self.__class__.__name__
        self._socket: zmq.Socket | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    def start(self) -> None:
        """Start the service server in a background thread."""
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        logger.info(f"📡 {self._name} listening on port {self._port}")

    def _run(self) -> None:
        """Main server loop."""
        self._socket = self._context.socket(zmq.REP)
        self._socket.setsockopt(zmq.RCVTIMEO, 100)  # 100ms timeout for clean shutdown
        self._socket.bind(f"tcp://{self._host}:{self._port}")

        while not self._shutdown.is_set():
            try:
                raw_request = self._socket.recv_string()

                # Parse request: "REQUEST_TYPE" or "REQUEST_TYPE <json_payload>"
                parts = raw_request.split(" ", 1)
                request = parts[0]
                payload = parts[1] if len(parts) > 1 else None

                try:
                    response = self.handle_request(request, payload)
                except Exception as e:
                    logger.exception(f"❌ {self._name} error handling '{request}'")
                    response = {"error": str(e)}

                self._socket.send_string(json.dumps(response))

            except zmq.Again:
                # Timeout - check shutdown flag
                continue

            except zmq.ZMQError as e:
                if not self._shutdown.is_set():
                    logger.error(f"❌ {self._name} ZMQ error: {e}")
                break

        self._socket.close()
        self._socket = None

    def stop(self) -> None:
        """Stop the service server."""
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._own_context:
            self._context.term()
        logger.info(f"🔌 {self._name} stopped")

    @property
    def port(self) -> int:
        """Get the port this server is bound to."""
        return self._port

    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._thread is not None and self._thread.is_alive()

    @abstractmethod
    def handle_request(self, request: str, payload: str | None) -> dict:
        """Handle an incoming request.

        Args:
            request: The request type (e.g., "GET_PARAMS", "PING")
            payload: Optional JSON payload string (parse with json.loads)

        Returns:
            Response dict to be serialized as JSON
        """
        ...
