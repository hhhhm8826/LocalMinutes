"""사용자 영역 경로와 루프백 실행 설정."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def user_path(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, str(Path.home() / fallback))) / "local-meeting-minutes"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINUTES_", extra="ignore")
    data_dir: Path = Field(default_factory=lambda: user_path("XDG_DATA_HOME", ".local/share"))
    config_dir: Path = Field(default_factory=lambda: user_path("XDG_CONFIG_HOME", ".config"))
    cache_dir: Path = Field(default_factory=lambda: user_path("XDG_CACHE_HOME", ".cache"))
    origin: str = "http://127.0.0.1:8765"
    dev_origin: str | None = None
    web_dir: Path | None = None
    max_upload_bytes: int = Field(default=2_000_000_000, ge=1, le=2_000_000_000)
    max_duration_seconds: int = Field(default=7200, ge=1, le=7200)
    threads: int = Field(default=4, ge=1, le=16)
    worker_enabled: bool = True
    codex_cli: Path = Field(default_factory=lambda: user_path('XDG_DATA_HOME', '.local/share') /
                           'toolchain/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')
    codex_home: Path = Field(default_factory=lambda: user_path('XDG_DATA_HOME', '.local/share') / 'codex-home')
    codex_user_home: Path = Field(default_factory=lambda: user_path('XDG_DATA_HOME', '.local/share') / 'runtime-user-home')
    codex_model: str = 'gpt-6-astra'
    codex_timeout_seconds: int = Field(default=180, ge=10, le=1800)
    codex_input_bytes: int = Field(default=150000, ge=1000, le=1000000)
    codex_max_calls: int = Field(default=10, ge=1, le=30)

    @field_validator("data_dir", "config_dir", "cache_dir", "web_dir", "codex_cli", "codex_home", "codex_user_home")
    @classmethod
    def absolute_path(cls, value):
        if value is None:
            return value
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError("절대 경로가 필요합니다")
        return value.resolve()

    @field_validator("origin", "dev_origin")
    @classmethod
    def loopback_origin(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("경로 없는 HTTP 루프백 origin만 지원합니다")
        _ = parsed.port
        return value

    def prepare(self):
        for folder in (self.data_dir, self.config_dir, self.cache_dir,
                       self.data_dir / "media", self.data_dir / "artifacts", self.data_dir / "logs"):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder.chmod(0o700)

    @property
    def database_path(self):
        return self.data_dir / "minutes.sqlite3"

    @property
    def owner_key_path(self):
        return self.config_dir / "owner-key"
