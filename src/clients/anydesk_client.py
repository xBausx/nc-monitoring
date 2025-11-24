import logging
import os
import subprocess
import time
from typing import Optional

import pyautogui
import pygetwindow as gw
import pytesseract
from PIL import Image

import os

# Configure pytesseract to use TESSERACT_CMD env var if provided.
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    

logger = logging.getLogger(__name__)


class AnyDeskClient:
    """
    Wrapper around the AnyDesk Windows client.

    This class is intended to run on a Windows agent where:
      - The AnyDesk executable is installed and available on PATH as `anydesk`.
      - A graphical desktop session is active (no headless mode).
      - Tesseract is installed and configured for pytesseract.

    Typical usage:
        client = AnyDeskClient()
        status = client.check_session(anydesk_id="123456789", password="secret")
        # status is one of: "Online", "Offline", "Wrong Password", "Errored"
    """

    def __init__(self) -> None:
        # Optionally override tesseract command via env
        tesseract_cmd = os.getenv("TESSERACT_CMD")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def check_session(
        self,
        anydesk_id: str,
        password: str,
        *,
        wait_for_window: int = 10,
        wait_after_window: int = 5,
    ) -> str:
        """
        Open AnyDesk to the given ID, attempt to log in with password,
        capture the screen and classify the connection status.

        Returns:
            One of:
              - "Online"
              - "Offline"
              - "Wrong Password"
              - "Errored"
        """

        if not anydesk_id or not password:
            logger.error("AnyDesk ID or password missing.")
            return "Errored"

        try:
            self._launch_anydesk(anydesk_id, password)
            if not self._wait_for_anydesk_window(timeout=wait_for_window):
                logger.error("AnyDesk window not found after %s seconds.", wait_for_window)
                return "Errored"

            # Give the UI a bit of time to settle
            time.sleep(wait_after_window)
            screenshot = self._capture_screenshot()
            if screenshot is None:
                return "Errored"

            status = self._classify_status_from_image(screenshot)
            logger.info(
                "AnyDesk status for ID %s determined as: %s",
                anydesk_id,
                status,
            )
            return status
        except Exception as exc:
            logger.error("Unexpected error in AnyDeskClient.check_session: %s", exc, exc_info=True)
            return "Errored"
        finally:
            self.close_anydesk()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _launch_anydesk(self, anydesk_id: str, password: str) -> None:
        """
        Launch AnyDesk via CLI using the provided ID and password.

        NOTE:
            This uses `echo <password> | anydesk ...` which will briefly expose
            the password in the shell command. This mirrors the behavior of the
            legacy script but may not be desirable in all environments.
        """
        cmd = (
            f"echo {password} | "
            f"anydesk {anydesk_id} --with-password --fullscreen"
        )
        logger.info("Launching AnyDesk for ID %s", anydesk_id)
        subprocess.Popen(cmd, shell=True)

    def _wait_for_anydesk_window(self, timeout: int = 10) -> bool:
        """Wait up to `timeout` seconds until an AnyDesk window appears."""
        start = time.time()
        while time.time() - start < timeout:
            if self._anydesk_window_present():
                return True
            time.sleep(1)
        return False

    @staticmethod
    def _anydesk_window_present() -> bool:
        try:
            titles = gw.getAllTitles()
        except Exception as exc:
            logger.error("Error while enumerating windows: %s", exc)
            return False

        return any("AnyDesk" in title for title in titles)

    @staticmethod
    def _capture_screenshot() -> Optional[Image.Image]:
        try:
            screenshot = pyautogui.screenshot()
            return screenshot
        except Exception as exc:
            logger.error("Failed to capture screenshot: %s", exc)
            return None

    @staticmethod
    def _classify_status_from_image(image: Image.Image) -> str:
        """Run OCR on the given image and classify the AnyDesk status."""
        try:
            text = pytesseract.image_to_string(image)
        except Exception as exc:
            logger.error("Failed to run OCR on screenshot: %s", exc)
            return "Errored"

        logger.debug("AnyDesk OCR text: %s", text)

        if "Client Offline" in text:
            return "Offline"
        if "Authorization" in text:
            return "Wrong Password"

        # Default assumption if no known error text is found
        return "Online"

    @staticmethod
    def close_anydesk() -> None:
        """Close AnyDesk using taskkill on Windows."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "AnyDesk.exe"],
                capture_output=True,
                text=True,
            )
            logger.info("Closed AnyDesk.")
        except Exception as exc:
            logger.error("Failed to close AnyDesk: %s", exc)
