"""Schémas Pydantic pour les clients (InvoiceGuard)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ClientCreate(BaseModel):
    """Données requises pour créer un client."""

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=255)


class ClientUpdate(BaseModel):
    """Champs modifiables lors de la mise à jour d'un client (tous optionnels)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=255)


class ClientOut(BaseModel):
    """Représentation d'un client exposée à l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr | None
    phone: str | None
    address: str | None
    created_at: datetime
    updated_at: datetime
