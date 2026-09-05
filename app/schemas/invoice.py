"""Schémas Pydantic pour les factures (InvoiceGuard).

Une facture porte désormais plusieurs lignes d'opération (``items``),
chacune définie par une désignation, une quantité et un prix unitaire.
Le montant global (``amount``) stocké sur la facture est la somme des lignes.
"""

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


class InvoiceItemCreate(BaseModel):
    """Ligne d'opération envoyée lors de la création d'une facture."""

    description: str = Field(..., min_length=1, max_length=500)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class InvoiceItemOut(BaseModel):
    """Représentation d'une ligne d'opération exposée à l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    quantity: int
    unit_price: Decimal


class InvoiceCreate(BaseModel):
    """Données requises pour créer une facture multi-lignes."""

    client_id: int
    # Surchargé côté serveur par la somme des items ; accepté pour compatibilité.
    amount: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    due_date: date | None = None
    status: InvoiceStatus = InvoiceStatus.draft
    # Optionnel : si absent, le numéro est généré automatiquement.
    invoice_number: str | None = Field(default=None, max_length=20)
    items: list[InvoiceItemCreate] = Field(default_factory=list, min_length=1)


class InvoiceUpdate(BaseModel):
    """Champs modifiables lors de la mise à jour (tous optionnels)."""

    client_id: int | None = None
    due_date: date | None = None
    status: InvoiceStatus | None = None
    invoice_number: str | None = Field(default=None, max_length=20)
    items: list[InvoiceItemCreate] | None = None


class InvoiceOut(BaseModel):
    """Représentation d'une facture exposée à l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    client_id: int
    amount: Decimal
    status: str
    due_date: date | None
    items: list[InvoiceItemOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
