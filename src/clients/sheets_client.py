import os
import logging
from typing import Any, List, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# Default scope: full access to spreadsheets
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    """
    Thin wrapper around gspread.

    - Authenticates using a service account JSON file.
    - Opens a single target spreadsheet (by ID from env var).
    - Provides helpers for creating worksheets, setting headers,
      finding rows, and upserting rows by a key value.
    """

    def __init__(
        self,
        credentials_file: Optional[str] = None,
        spreadsheet_id: Optional[str] = None,
    ) -> None:
        self.credentials_file = credentials_file or os.getenv(
            "SHEETS_CREDENTIALS_FILE",
            "client_secret.json",
        )
        self.spreadsheet_id = spreadsheet_id or os.getenv("SHEETS_SPREADSHEET_ID", "")

        if not self.spreadsheet_id:
            logger.warning(
                "SheetsClient initialized without SHEETS_SPREADSHEET_ID. "
                "You must set this env var for most operations to work."
            )

        logger.info(
            "Initializing SheetsClient with credentials_file=%s, spreadsheet_id=%s",
            self.credentials_file,
            self.spreadsheet_id,
        )

        creds = Credentials.from_service_account_file(
            self.credentials_file,
            scopes=SCOPES,
        )
        gc = gspread.authorize(creds)
        self.spreadsheet = gc.open_by_key(self.spreadsheet_id)

    # ------------------------------------------------------------------ #
    # Worksheet helpers
    # ------------------------------------------------------------------ #

    def get_or_create_worksheet(
        self,
        title: str,
        rows: int = 1000,
        cols: int = 20,
    ):
        """
        Return an existing worksheet with the given title, or create it.

        rows/cols are only used when creating a new worksheet.
        """
        try:
            ws = self.spreadsheet.worksheet(title)
            return ws
        except gspread.WorksheetNotFound:
            logger.info("Worksheet '%s' not found. Creating...", title)
            return self.spreadsheet.add_worksheet(
                title=title,
                rows=str(rows),
                cols=str(cols),
            )

    def ensure_headers(self, ws, headers: List[str]) -> None:
        """
        Ensure the first row of the worksheet is exactly `headers`.
        Overwrites row 1 if needed.
        """
        try:
            existing = ws.row_values(1)
        except Exception as exc:
            logger.warning("Failed to read header row from '%s': %s", ws.title, exc)
            existing = []

        if existing == headers:
            return

        logger.info("Updating headers for worksheet '%s'", ws.title)
        ws.update("1:1", [headers])

    # ------------------------------------------------------------------ #
    # Row helpers
    # ------------------------------------------------------------------ #

    def find_row_by_value(
        self,
        ws,
        value: Any,
        col: int = 1,
    ) -> Optional[int]:
        """
        Find the row index where the given value appears in the given column.

        Returns:
            Row index (1-based) if found, otherwise None.
        """
        try:
            cell = ws.find(str(value), in_column=col)
            return cell.row
        except gspread.exceptions.CellNotFound:
            return None

    def upsert_row(
        self,
        ws,
        key_value: Any,
        values: List[Any],
        key_col: int = 1,
    ) -> None:
        """
        Insert or update a row identified by `key_value` in column `key_col`.

        - If a row with `key_value` exists in `key_col`, we update that row.
        - Otherwise, we append a new row at the bottom.
        """
        row_index = self.find_row_by_value(ws, key_value, col=key_col)

        if row_index is not None:
            range_str = f"{row_index}:{row_index}"
            logger.debug(
                "Updating row %s in worksheet '%s' (key=%s)",
                row_index,
                ws.title,
                key_value,
            )
            ws.update(range_str, [values])
        else:
            logger.debug(
                "Appending new row in worksheet '%s' (key=%s)",
                ws.title,
                key_value,
            )
            ws.append_row(values, value_input_option="USER_ENTERED")
