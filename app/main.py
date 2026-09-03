"""Point d'entrée principal de l'API InvoiceGuard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.migrations import run_migrations

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cycle de vie de l'application (lifespan)
# ---------------------------------------------------------------------------
# Au démarrage du serveur on :
#   1. Crée les tables manquantes (Base.metadata.create_all) — utile pour une
#      base vierge (locale ou déployée la première fois).
#   2. Exécute la migration « glissante » qui ajoute les colonnes nouvelles
#      (description / quantity / unit_price) à la table invoices existante.
# L'import d'un modèle SQLAlchemy est nécessaire pour enregistrer les tables
# dans Base.metadata avant l'appel à create_all().
from app import models  # noqa: F401  (imports invoice, client, user)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Initialisation du schéma avant de servir les requêtes ---
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Tables de base créées / vérifiées.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Création des tables en échec (%s).", exc)

    # --- Migration des colonnes ajoutées sur la table invoices ---
    run_migrations(engine)

    yield
    # (aucun nettoyage particulier nécessaire côté moteur à l'arrêt)


# --- Initialisation de l'application FastAPI ---
app = FastAPI(
    title="InvoiceGuard API",
    description="API de gestion de facturation pour indépendants.",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Configuration CORS (accès depuis un frontend au navigateur) ---
# Origines autorisées lues depuis les paramètres (settings.allowed_origins),
# une liste séparée par des virgules. Pour autoriser toutes les origines
# (utile en dev rapide, DANGEREUX en production), mettre "*".
allowed_origins = [
    origin.strip()
    for origin in settings.allowed_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
def read_root() -> dict:
    """Route de test : message de bienvenue."""
    return {"message": "Welcome to InvoiceGuard API"}


# --- Routers ---
from app.routers import auth, clients, invoices, stripe  # noqa: E402

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(invoices.router)
app.include_router(stripe.router)

# --- Initialisation Stripe ---
# Configure la clé secrète au démarrage. Si la clé est manquante, on log
# un avertissement sans bloquer le démarrage (les endpoints Stripe
# renverront alors une erreur explicite).
try:
    from app.services import stripe_service

    stripe_service.configure_stripe()
except RuntimeError as exc:
    logging.getLogger(__name__).warning("Stripe non configuré : %s", exc)

