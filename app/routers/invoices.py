"""Routes CRUD pour les factures InvoiceGuard (protégées par authentification)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.client import Client
from app.models.invoice import Invoice, InvoiceItem, InvoiceStatus
from app.models.user import User
from app.schemas.invoice import InvoiceCreate, InvoiceOut, InvoiceUpdate
from app.services.pdf_generator import PDFGenerationError, generate_invoice_pdf

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _generate_invoice_number(db: Session) -> str:
    """Génère un numéro de facture unique au format INV-YYYYMMDD-XXX.

    Le compteur XXX repart de 001 pour chaque nouveau jour.
    """
    today = date.today()
    prefix = f"INV-{today:%Y%m%d}-"

    # Compte les factures déjà créées aujourd'hui pour déterminer le prochain numéro.
    count = db.query(func.count(Invoice.id)).filter(
        Invoice.invoice_number.like(f"{prefix}%")
    ).scalar()

    return f"{prefix}{count + 1:03d}"


def _get_own_invoice(invoice_id: int, user_id: int, db: Session) -> Invoice:
    """Récupère une facture (et ses lignes) appartenant à l'utilisateur."""
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, Invoice.user_id == user_id)
    )
    invoice = db.execute(stmt).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )
    return invoice


def _validate_client_ownership(client_id: int, user_id: int, db: Session) -> None:
    """Vérifie que le client_id appartient à l'utilisateur, sinon lève une 400."""
    owns_client = (
        db.query(Client.id)
        .filter(Client.id == client_id, Client.user_id == user_id)
        .first()
    )
    if owns_client is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected client does not belong to you",
        )


def _compute_total(items) -> Decimal:
    """Calcule le montant total de la facture (somme des lignes)."""
    total = Decimal("0")
    for it in items:
        total += Decimal(it.quantity) * Decimal(it.unit_price)
    return total


@router.get("", response_model=list[InvoiceOut], summary="Liste mes factures")
def list_invoices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Invoice]:
    """Renvoie toutes les factures (avec leurs lignes) de l'utilisateur."""
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(Invoice.user_id == current_user.id)
        .order_by(Invoice.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post(
    "",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une facture multi-lignes",
)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Invoice:
    """Crée une facture à partir de ses lignes d'opération."""
    _validate_client_ownership(payload.client_id, current_user.id, db)

    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Au moins une ligne d'opération (item) est requise.",
        )

    invoice_number = payload.invoice_number or _generate_invoice_number(db)
    total = _compute_total(payload.items)

    new_invoice = Invoice(
        invoice_number=invoice_number,
        client_id=payload.client_id,
        user_id=current_user.id,
        amount=total,
        status=payload.status.value,
        due_date=payload.due_date,
    )
    # Renseigne les lignes d'opération (persistées en cascade par la relation).
    new_invoice.items = [
        InvoiceItem(
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
        )
        for item in payload.items
    ]

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)
    return new_invoice


@router.get("/{invoice_id}", response_model=InvoiceOut, summary="Détail d'une facture")
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Invoice:
    """Renvoie une facture précise (avec ses lignes), si elle appartient à l'utilisateur."""
    return _get_own_invoice(invoice_id, current_user.id, db)


@router.get(
    "/{invoice_id}/download",
    summary="Télécharger une facture en PDF",
    responses={
        200: {
            "description": "PDF de la facture",
            "content": {"application/pdf": {}},
        },
        404: {"description": "Facture introuvable ou non autorisée"},
        500: {"description": "Échec de la génération du PDF"},
    },
)
def download_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Génère et renvoie le PDF d'une facture appartenant à l'utilisateur."""
    invoice = _get_own_invoice(invoice_id, current_user.id, db)

    client = (
        db.query(Client)
        .filter(Client.id == invoice.client_id, Client.user_id == current_user.id)
        .first()
    )

    # Lignes d'opération normalisées pour le rendu PDF (WeasyPrint ou reportlab).
    items = [
        {
            "description": it.description,
            "quantity": it.quantity,
            "unit_price": it.unit_price,
        }
        for it in invoice.items
    ]

    invoice_data: dict = {
        "invoice_number": invoice.invoice_number,
        "amount": invoice.amount,
        "items": items,
        "created_at": invoice.created_at,
        "due_date": invoice.due_date,
        "status": invoice.status,
        "client": {
            "name": client.name if client else None,
            "email": client.email if client else None,
            "phone": client.phone if client else None,
            "address": client.address if client else None,
        },
        "freelance": {
            "full_name": current_user.full_name,
            "company_name": current_user.company_name,
            "email": current_user.email,
            # L'utilisateur ne porte pas de phone/address en base ; on les laisse
            # vides, le bloc signature en affichera seulement ce qui est connu.
            "phone": None,
            "address": None,
        },
    }

    try:
        pdf_bytes = generate_invoice_pdf(invoice_data)
    except PDFGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    safe_number = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in invoice.invoice_number
    )
    filename = f"facture_{safe_number}.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{invoice_id}", response_model=InvoiceOut, summary="Modifier une facture")
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Invoice:
    """Met à jour une facture appartenant à l'utilisateur connecté."""
    invoice = _get_own_invoice(invoice_id, current_user.id, db)

    update_data = payload.model_dump(exclude_unset=True)

    # Si un nouveau client_id est fourni, vérifie qu'il appartient à l'utilisateur.
    if "client_id" in update_data:
        _validate_client_ownership(update_data["client_id"], current_user.id, db)
        invoice.client_id = update_data["client_id"]

    if "status" in update_data and update_data["status"] is not None:
        invoice.status = update_data["status"].value

    if "due_date" in update_data:
        invoice.due_date = update_data["due_date"]

    if "invoice_number" in update_data:
        invoice.invoice_number = update_data["invoice_number"]

    # Remplacement complet des lignes si une nouvelle liste est fournie.
    if "items" in update_data and update_data["items"] is not None:
        invoice.items.clear()
        invoice.items = [
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in update_data["items"]
        ]
        invoice.amount = _compute_total(update_data["items"])

    db.commit()
    db.refresh(invoice)
    return invoice


@router.put("/{invoice_id}/pay", response_model=InvoiceOut, summary="Marquer une facture comme payée")
def pay_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Invoice:
    """Marque une facture comme payée, si elle appartient à l'utilisateur connecté."""
    invoice = _get_own_invoice(invoice_id, current_user.id, db)
    invoice.status = InvoiceStatus.PAID.value
    db.commit()
    db.refresh(invoice)
    return invoice


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Supprimer une facture",
)
def delete_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Supprime une facture appartenant à l'utilisateur connecté."""
    invoice = _get_own_invoice(invoice_id, current_user.id, db)
    db.delete(invoice)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
