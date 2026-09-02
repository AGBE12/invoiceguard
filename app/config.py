"""Configuration de l'application InvoiceGuard via variables d'environnement."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Racine du projet (dossier parent de app/)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Paramètres de configuration chargés depuis un fichier .env et la variable d'environnement."""

    model_config = SettingsConfigDict(
        # Fichier .env situé à la racine du projet
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        # Une valeur d'environnement écrase celle du .env
        extra="ignore",
    )

    # --- Identité du projet ---
    project_name: str = "InvoiceGuard"

    # --- Base de données ---
    database_url: str = "sqlite:///./invoiceguard.db"

    # --- Sécurité / authentification ---
    # À remplacer par une clé robuste en production (générer avec secrets.token_urlsafe)
    secret_key: str = "change-me-in-production"

    # Durée de validité du token JWT (en minutes), utile pour l'authentification
    access_token_expire_minutes: int = 60 * 24  # 24 heures

    # --- CORS (accès d'un frontend au navigateur) ---
    # Liste d'origines autorisées, séparées par des virgules.
    # En développement local, on autorise les serveurs des outils frontend
    # courants ; en production, cette liste doit être restreinte.
    # Met "*" pour autoriser toutes les origines (peu sûr en production).
    allowed_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5500,"
        "http://localhost:5500"
    )

    # --- Stripe (paiement en ligne) ---
    # Clé secrète Stripe : "sk_test_..." en dev, "sk_live_..." en production.
    # À remplacer par la vraie valeur dans les variables d'environnement.
    stripe_secret_key: str = ""

    # Secret de signature du webhook Stripe (clé "whsec_...").
    stripe_webhook_secret: str = ""

    # Devise par défaut. Le FCFA (XOF) est une devise SANS DÉCIMAL sur Stripe :
    # le montant est transmis tel quel (sans multiplication par 100).
    stripe_currency: str = "xof"

    # URLs de retour après le paiement Stripe Checkout.
    checkout_success_url: str = "http://localhost:8000/?success=true"
    checkout_cancel_url: str = "http://localhost:8000/?cancel=true"


@lru_cache
def get_settings() -> Settings:
    """Retourne une instance unique (mise en cache) des paramètres."""
    return Settings()


# Instance partagée utilisée dans toute l'application
settings = get_settings()

