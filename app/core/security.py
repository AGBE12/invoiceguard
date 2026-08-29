"""Outils de sécurité et d'authentification pour InvoiceGuard.

Hachage des mots de passe (passlib + bcrypt) et gestion des jetons JWT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# --- Hachage des mots de passe ---
# CryptContext centralise la configuration de l'algorithme de hachage.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hache un mot de passe en clair et renvoie son empreinte sécurisée."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie qu'un mot de passe en clair correspond bien à son empreinte."""
    return pwd_context.verify(plain_password, hashed_password)


# --- Gestion des jetons JWT ---
# Algorithme de signature utilisé pour les jetons d'accès.
ALGORITHM = "HS256"


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """Crée un jeton d'accès JWT signé contenant le sujet (ex: id utilisateur).

    Args:
        subject: Identifiant porté par le jeton (généralement le user_id).
        expires_delta: Durée de validité. Si omise, la durée par défaut
            est lue depuis settings.access_token_expire_minutes.

    Returns:
        Le jeton JWT encodé.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    expire_at = datetime.now(timezone.utc) + expires_delta
    payload = {"sub": str(subject), "exp": expire_at}

    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Décode et valide un jeton JWT.

    Args:
        token: Le jeton JWT à examiner.

    Returns:
        Le payload (dictionnaire des revendications) si le jeton est valide,
        sinon None en cas d'échec de signature ou d'expiration.
    """
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
        )
    except JWTError:
        # Jetons invalides, expirés ou mal signés.
        return None
