"""Modèles SQLAlchemy des factures InvoiceGuard (multi-lignes).

Une facture (``Invoice``) possède plusieurs lignes d'opération (``items``),
stockées dans la table normale ``invoice_items`` via une relation One-to-Many.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base  # adapté au chemin de votre config SQLAlchemy


class InvoiceStatus(str, Enum):
    """Statuts possibles d'une facture."""

    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"


class Invoice(Base):
    """Modèle de la table `invoices`."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    invoice_number: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False
    )

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Montant TOTAL de la facture = somme des lignes (items).
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    status: Mapped[InvoiceStatus] = mapped_column(
        String(20), default=InvoiceStatus.DRAFT, nullable=False
    )

    due_date: Mapped[date] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Lignes d'opération (One-to-Many, supprimées en cascade avec la facture).
    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InvoiceItem.id",
    )

    # Relations (à adapter selon vos modèles déjà définis)
    client = relationship("Client", back_populates="invoices")
    user = relationship("User", back_populates="invoices")

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} invoice_number={self.invoice_number!r} "
            f"client_id={self.client_id} status={self.status.value}>"
        )


class InvoiceItem(Base):
    """Modèle de la table `invoice_items` (une ligne d'opération)."""

    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    invoice: Mapped["Invoice"] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return (
            f"<InvoiceItem id={self.id} qty={self.quantity} "
            f"unit={self.unit_price} desc={self.description!r}>"
        )
