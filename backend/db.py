"""SQLite + sqlmodel setup and ORM models."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional
from uuid import uuid4

from sqlmodel import Field, Session, SQLModel, create_engine

from config import DB_PATH, ensure_directories


ensure_directories()

DATABASE_URL: str = f"sqlite:///{DB_PATH}"
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


class Video(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str
    filename: str
    duration_sec: Optional[float] = None
    language: Optional[str] = None
    status: str = Field(default="uploaded", index=True)
    progress_message: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class Clip(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    video_id: str = Field(foreign_key="video.id", index=True)
    sequence: int
    start_sec: float
    end_sec: float
    hook_text: str
    filename: str
    duration_sec: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a Session."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone context manager for use in background tasks."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
