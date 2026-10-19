from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    sujeto_tipo: Mapped[str]
    sujeto_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(unique=True)
    familia_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    sujeto_tipo: Mapped[str]
    sujeto_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(unique=True)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    usado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
