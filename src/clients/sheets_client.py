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

    def find_row_by_value(self, ws, value, col: int = 1) -> Optional[int]:
        """
        Return the row index of the first cell in column `col` that matches `value`.

        Works with both old and new gspread versions. If nothing is found or any
        lookup error happens, returns None.
        """
        try:
            cell = ws.find(str(value), in_column=col)
            return cell.row if cell is not None else None
        except Exception:
            # In gspread 6.x there is no CellNotFound anymore; any lookup
            # failure just bubbles as a generic exception. We treat that
            # as "not found".
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
    
    def set_column_widths(
        self,
        ws,
        start_col: int,
        end_col: int,
        pixel_size: int,
    ) -> None:
        """
        Set the width (in pixels) for a range of columns in the given worksheet.

        Columns are 1-based (A=1, B=2, ...), and end_col is inclusive.
        This uses the low-level Google Sheets API via gspread's client.
        """
        # gspread Worksheet exposes the underlying sheetId as .id
        try:
            sheet_id = ws.id
        except AttributeError:
            sheet_id = ws._properties.get("sheetId")

        if sheet_id is None:
            logger.warning(
                "Cannot set column widths for worksheet '%s': no sheetId.",
                getattr(ws, "title", "<unknown>"),
            )
            return

        # Google Sheets API uses 0-based indices, endIndex is exclusive
        start_index = start_col - 1
        end_index = end_col

        body = {
            "requests": [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        },
                        "properties": {"pixelSize": pixel_size},
                        "fields": "pixelSize",
                    }
                }
            ]
        }

        try:
            logger.info(
                "Setting column widths for '%s' (cols %s-%s -> %spx)",
                ws.title,
                start_col,
                end_col,
                pixel_size,
            )
            client = self.spreadsheet.client
            client.request(
                "post",
                f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet.id}:batchUpdate",
                json=body,
            )
        except Exception as exc:
            logger.warning(
                "Failed to set column widths for '%s': %s",
                ws.title,
                exc,
            )

    def set_horizontal_alignment(
        self,
        ws,
        start_col: int,
        end_col: int,
        horizontal: str = "LEFT",
    ) -> None:
        """
        Set horizontal alignment for a range of columns in the given worksheet.

        Columns are 1-based (A=1, B=2, ...), end_col is inclusive.
        Applies to all rows in those columns.
        """
        try:
            sheet_id = ws.id
        except AttributeError:
            sheet_id = ws._properties.get("sheetId")

        if sheet_id is None:
            logger.warning(
                "Cannot set alignment for worksheet '%s': no sheetId.",
                getattr(ws, "title", "<unknown>"),
            )
            return

        # 0-based indices for the API; endIndex is exclusive
        start_col_index = start_col - 1
        end_col_index = end_col

        # Apply to all rows (0 .. row_count). row_count is usually large enough;
        # worst case, this just covers more rows than currently used.
        start_row_index = 0

        body = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row_index,
                            "startColumnIndex": start_col_index,
                            "endColumnIndex": end_col_index,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "horizontalAlignment": horizontal.upper(),
                            }
                        },
                        "fields": "userEnteredFormat.horizontalAlignment",
                    }
                }
            ]
        }

        try:
            logger.info(
                "Setting horizontal alignment=%s for '%s' (cols %s-%s)",
                horizontal,
                ws.title,
                start_col,
                end_col,
            )
            client = self.spreadsheet.client
            client.request(
                "post",
                f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet.id}:batchUpdate",
                json=body,
            )
        except Exception as exc:
            logger.warning(
                "Failed to set alignment for '%s': %s",
                ws.title,
                exc,
            )
