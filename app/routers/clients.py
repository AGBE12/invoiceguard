"""Routes CRUD pour les clients InvoiceGuard (protégées par authentification)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.client import Client
from app.models.user import User
from app.schemas.client import ClientCreate, ClientOut, ClientUpdate

router = APIRouter(prefix="/clients", tags=["clients"])


def _get_own_client(client_id: int, user_id: int, db: Session) -> Client:
    """Récupère un client appartenant à l'utilisateur, sinon lève une 404.

    La requête filtre simultanément sur l'id du client ET l'id de l'utilisateur :
    l'on garantit ainsi qu'un utilisateur ne peut accéder qu'à ses propres clients.
    """
    client = (
        db.query(Client)
        .filter(Client.id == client_id, Client.user_id == user_id)
        .first()
    )
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found",
        )
    return client


@router.get("", response_model=list[ClientOut], summary="Liste mes clients")
def list_clients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Client]:
    """Renvoie tous les clients de l'utilisateur connecté."""
    return (
        db.query(Client)
        .filter(Client.user_id == current_user.id)
        .order_by(Client.name)
        .all()
    )


@router.post(
    "",
    response_model=ClientOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un client",
)
def create_client(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Client:
    """Crée un client rattaché à l'utilisateur connecté."""
    new_client = Client(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        address=payload.address,
        user_id=current_user.id,
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    return new_client


@router.get("/{client_id}", response_model=ClientOut, summary="Détail d'un client")
def get_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Client:
    """Renvoie un client précis, s'il appartient bien à l'utilisateur."""
    return _get_own_client(client_id, current_user.id, db)


@router.put("/{client_id}", response_model=ClientOut, summary="Modifier un client")
def update_client(
    client_id: int,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Client:
    """Met à jour un client appartenant à l'utilisateur connecté."""
    client = _get_own_client(client_id, current_user.id, db)

    # N'applique que les champs réellement fournis dans la requête.
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(client, field, value)

    db.commit()
    db.refresh(client)
    return client


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Supprimer un client",
)
def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Supprime un client appartenant à l'utilisateur connecté."""
    client = _get_own_client(client_id, current_user.id, db)
    db.delete(client)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
