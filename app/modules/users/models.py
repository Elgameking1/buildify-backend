"""Identity tables: users, their refresh tokens, and the vendor profile.

One account carries exactly one role.  That keeps dashboard routing on the
React side unambiguous and keeps every authorisation check to a single
comparison.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import UserRole
from app.db.base import Base, PKMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.workers.models import WorkerProfile


class User(PKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=16), index=True, nullable=False
    )
    region: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    vendor_profile: Mapped["VendorProfile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    worker_profile: Mapped["WorkerProfile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.email} {self.role}>"


class RefreshToken(PKMixin, TimestampMixin, Base):
    """A single issued refresh token.

    Only the SHA-256 digest is stored, so a database leak yields no usable
    sessions.  Rows are kept after revocation to preserve an audit trail rather
    than being deleted.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class PasswordResetToken(PKMixin, TimestampMixin, Base):
    """A single password-reset grant.

    Same shape as RefreshToken and for the same reason: only the SHA-256
    digest is stored, so a database leak yields no usable reset links.

    `used_at` makes the token single-use. Without it a link sitting in an
    inbox - or in a mail provider's logs - stays a working key to the account
    for as long as it has not expired.
    """

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class VendorProfile(TimestampMixin, Base):
    """Business details for a VENDOR account.  Keyed by the user id itself."""

    __tablename__ = "vendor_profiles"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    business_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    logo_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="vendor_profile")
