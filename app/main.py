"""Point d'entrée principal de l'API InvoiceGuard."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine

# Import des modèles afin qu'ils soient enregistrés auprès de Base.metadata
# avant l'appel à Base.metadata.create_all().
from app import models  # noqa: F401  (imports invoice, client, user)

# --- Création automatique des tables SQLite au démarrage ---
# Crée les tables définies par les modèles (users, clients, invoices)
# dans le fichier local invoiceguard.db si elles n'existent pas encore.
Base.metadata.create_all(bind=engine)

# --- Initialisation de l'application FastAPI ---
app = FastAPI(
    title="InvoiceGuard API",
    description="API de gestion de facturation pour indépendants.",
    version="0.1.0",
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
from app.routers import auth, clients, invoices  # noqa: E402

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(invoices.router)
