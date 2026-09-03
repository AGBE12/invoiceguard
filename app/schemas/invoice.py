"""Schémas Pydantic pour les factures (InvoiceGuard)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class InvoiceStatus(str, Enum):
    """Statuts possibles d'une facture (aligné sur le modèle SQLAlchemy)."""

    draft = "draft"
    sent = "sent"
    paid = "paid"
    overdue = "overdue"


class InvoiceCreate(BaseModel):
    """Données requises pour créer une facture."""

    client_id: int
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    due_date: date | None = None
    status: InvoiceStatus = InvoiceStatus.draft
    # Optionnel : si absent, le numéro est généré automatiquement.
    invoice_number: str | None = Field(default=None, max_length=20)

    # Détails d'opération (nouveau template PDF ouest-africain).
    # Facultatifs pour préserver la compatibilité avec les anciens
    # appels où seul `amount` était fourni. `amount` reste le total.
    description: str | None = Field(default=None, max_length=500)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )


class InvoiceUpdate(BaseModel):
    """Champs modifiables lors de la mise à jour d'une facture (tous optionnels)."""

    client_id: int | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    due_date: date | None = None
    status: InvoiceStatus | None = None
    invoice_number: str | None = Field(default=None, max_length=20)

    # Détails d'opération modifiables.
    description: str | None = Field(default=None, max_length=500)
    quantity: int | None = Field(default=None, ge=1)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class InvoiceOut(BaseModel):
    """Représentation d'une facture exposée à l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    client_id: int
    amount: Decimal
    status: str
    due_date: date | None
    description: str | None = None
    quantity: int = 1
    unit_price: Decimal | None = None
    created_at: datetime
    updated_at: datetime

