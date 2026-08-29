"""Schémas Pydantic pour les utilisateurs et l'authentification (InvoiceGuard)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """Données requises pour l'inscription d'un nouvel utilisateur."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=120)
    company_name: str | None = Field(default=None, max_length=120)


class UserOut(BaseModel):
    """Représentation d'un utilisateur exposée à l'API (jamais le mot de passe)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str | None
    company_name: str | None
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    """Jeton d'accès renvoyé lors de l'authentification."""

    access_token: str
    token_type: str = "bearer"
