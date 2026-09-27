"""SQLite 연결 및 버전 마이그레이션. API에서 ML을 가져오지 않는다."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event


def make_engine(path: Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
    return engine


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
