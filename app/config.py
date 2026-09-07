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

    # --- Backend (base des URLs de notification) ---
    # URL publique de base de l'API. Utilisée pour construire les URLs de
    # notification (ex : webhook CinetPay) envoyées aux fournisseurs de
    # paiement afin qu'ils nous rappellent au moment du virement.
    backend_url: str = "http://localhost:8000"

    # --- CinetPay (Mobile Money, UEMOA — devise XOF) ---
    # Identifiants de la place de marché CinetPay. Obtenus depuis le tableau
    # de bord CinetPay (https://cinetpay.com) dans « Mes identifiants ».
    cinetpay_api_key: str = ""
    cinetpay_site_id: str = ""

    # Mode d'environnement CinetPay : "TEST" (bac à sable) ou "PRODUCTION".
    # À définir en fonction de l'environnement d'exécution.
    cinetpay_mode: str = "TEST"

    # URL d'initiation du paiement CinetPay (API Checkout v2).
    cinetpay_checkout_url: str = "https://api-checkout.cinetpay.com/v2/payment"

    # URL de redirection du client APRÈS un paiement réussi/annulé CinetPay.
    cinetpay_success_url: str = "http://localhost:8000/?payment=success"
    cinetpay_cancel_url: str = "http://localhost:8000/?payment=cancel"

    # --- Email (envoi de factures par SMTP) ---
    # Serveur SMTP sortant et identifiants associés.
    smtp_server: str = ""
    smtp_port: int = 587
    # Identifiants SMTP (souvent identiques à l'adresse expéditrice).
    smtp_username: str = ""
    smtp_password: str = ""
    # Adresse d'expédition affichée dans les e-mails envoyés.
    email_from: str = ""


@lru_cache
def get_settings() -> Settings:
    """Retourne une instance unique (mise en cache) des paramètres."""
    return Settings()


# Instance partagée utilisée dans toute l'application
settings = get_settings()


