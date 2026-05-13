"""SQLAlchemy 2.0 ORM models for the dashboard."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all dashboard ORM models."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin','viewer')", name="ck_users_role"),
        UniqueConstraint("username", name="uq_users_username"),
    )

    def is_admin(self) -> bool:
        return self.role == "admin" and not self.disabled


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    mp3_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    notes_md_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    items_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="generate")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    log_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed')",
            name="ck_jobs_status",
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user = relationship("User", lazy="joined")
