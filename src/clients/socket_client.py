import logging
import os
from typing import Any, Dict, Optional

import socketio

logger = logging.getLogger(__name__)


class SocketClient:
    """
    Thin wrapper around the N-Compass socket server.

    Provides convenience methods to emit common events like:
      - D_player_restart
      - D_restart_anydesk
    """

    def __init__(self, url: Optional[str] = None) -> None:
        # Default URL can be overridden via env var or argument
        self.url = url or os.getenv(
            "SOCKET_URL",
            "https://nctvsocket.n-compass.online",
        )

    # ------------------------------------------------------------------ #
    # Internal helper
    # ------------------------------------------------------------------ #

    def _emit(self, event: str, data: Dict[str, Any]) -> bool:
        """
        Connects, emits a single event, and disconnects.

        Returns:
            True on success, False on failure.
        """
        logger.info("Socket emit: event=%s data=%s", event, data)

        sio = socketio.Client()

        try:
            sio.connect(self.url, transports=["websocket"])
            sio.emit(event, data)
            sio.disconnect()
            logger.info("Socket emit succeeded for event=%s", event)
            return True
        except Exception as exc:
            logger.error(
                "Socket emit failed for event=%s url=%s error=%s",
                event,
                self.url,
                exc,
            )
            try:
                sio.disconnect()
            except Exception:
                pass
            return False

    # ------------------------------------------------------------------ #
    # Public convenience methods
    # ------------------------------------------------------------------ #

    def restart_player(self, license_id: str) -> bool:
        """
        Emit a D_player_restart event for the given license_id.
        """
        if not license_id:
            logger.error("restart_player called without license_id")
            return False

        return self._emit("D_player_restart", {"license_id": license_id})

    def restart_anydesk(self, license_id: str) -> bool:
        """
        Emit a D_restart_anydesk event for the given license_id.
        """
        if not license_id:
            logger.error("restart_anydesk called without license_id")
            return False

        return self._emit("D_restart_anydesk", {"license_id": license_id})
