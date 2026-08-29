"""Configuration de la base de données pour InvoiceGuard."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# --- Base de données ---
# En production : définit la variable d'environnement DATABASE_URL (par ex.
# une base PostgreSQL fournie par Render/Railway).
# En développement : sinon on retombe sur SQLite local, dans app/.
_SQLITE_MARKERS = ("sqlite:///./", "sqlite:///", "sqlite://")
if settings.database_url.startswith(_SQLITE_MARKERS):
    # Chemin SQLite absolu, indépendant du répertoire de lancement.
    SQLALCHEMY_DATABASE_URL = (
        f"sqlite:///{Path(__file__).resolve().parent / 'invoiceguard.db'}"
    )
else:
    SQLALCHEMY_DATABASE_URL = settings.database_url

# SQLite : check_same_thread=False autorise l'usage depuis le thread FastAPI.
# PostgreSQL / autres : aucun paramètre spécial requis (ni besoin de variable).
connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith(
    "sqlite"
) else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)



# --- Classe de base moderne (SQLAlchemy 2.0) ---
class Base(DeclarativeBase):
    """Classe de base déclarative pour tous les modèles SQLAlchemy 2.0."""


# --- Fabrique de sessions ---
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# --- Dépendance FastAPI pour ouvrir/fermer la session à chaque requête ---
def get_db():
    """Yield une session de base de données puis la ferme proprement."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

