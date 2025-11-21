import os
from dataclasses import dataclass

@dataclass
class APISettings:
    base_url: str = os.getenv("NC_API_BASE_URL", "https://nctvapi.n-compass.online")
    username: str = os.getenv("NC_API_USERNAME", "")
    password: str = os.getenv("NC_API_PASSWORD", "")

@dataclass
class SheetsSettings:
    credentials_file: str = os.getenv("SHEETS_CREDENTIALS_FILE", "client_secret.json")
    spreadsheet_id: str = os.getenv("SHEETS_SPREADSHEET_ID", "")

@dataclass
class SlackSettings:
    webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")

@dataclass
class SocketSettings:
    url: str = os.getenv("SOCKET_URL", "https://nctvsocket.n-compass.online")

@dataclass
class VersionSettings:
    expected_server_version: str = os.getenv("EXPECTED_SERVER_VERSION", "2.9.4")
    expected_ui_version: str = os.getenv("EXPECTED_UI_VERSION", "3.0.47")

@dataclass
class Settings:
    api: APISettings = APISettings()
    sheets: SheetsSettings = SheetsSettings()
    slack: SlackSettings = SlackSettings()
    socket: SocketSettings = SocketSettings()
    versions: VersionSettings = VersionSettings()

settings = Settings()
