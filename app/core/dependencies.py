"""Dépendances partagées : authentification et récupération de l'utilisateur courant."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User

# Point d'entrée attendu par l'écran de connexion OAuth2.
# Le préfixe est relatif : la route complète sera /auth/login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# Codes/messages d'erreur réutilisés pour l'authentification.
_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Décode le jeton JWT et renvoie l'utilisateur authentifié.

    Leve une HTTPException 401 si :
      * le jeton est invalide ou expiré (decode_access_token renvoie None) ;
      * le champ 'sub' est absent ou ne correspond pas à un entier ;
      * aucun utilisateur actif ne correspond à cet identifiant.

    Args:
        token: Jeton d'accès extrait de l'en-tête Authorization.
        db: Session SQLAlchemy fournie par la dépendance get_db.

    Returns:
        L'instance User de l'utilisateur authentifié.
    """
    payload = decode_access_token(token)
    if payload is None:
        raise _CREDENTIALS_ERROR

    # 'sub' contient le user_id (encodé en chaîne dans le jeton).
    subject = payload.get("sub")
    if subject is None:
        raise _CREDENTIALS_ERROR

    try:
        user_id = int(subject)
    except ValueError:
        raise _CREDENTIALS_ERROR

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR

    return user
