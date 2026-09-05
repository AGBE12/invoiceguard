"""Routes Stripe Checkout pour InvoiceGuard.

- ``POST /api/invoices/{invoice_id}/checkout`` : crée une session de paiement
  Stripe Checkout pour une facture de l'utilisateur connecté.
- ``POST /api/webhooks/stripe`` : reçoit les événements Stripe (webhook
  signé) et marque la facture comme payée lors de ``checkout.session.completed``.
"""

from __future__ import annotations

import asyncio
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import SessionLocal, get_db
from app.models.client import Client
from app.models.invoice import Invoice, InvoiceStatus
from app.models.user import User
from app.routers.invoices import _get_own_invoice
from app.services import stripe_service
from app.services.email_sender import EmailSenderError, send_invoice_email
from app.services.pdf_generator import PDFGenerationError, generate_invoice_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stripe"])


@router.post(
    "/invoices/{invoice_id}/checkout",
    summary="Créer une session Stripe Checkout pour une facture",
    responses={
        200: {"description": "URL de la session Checkout"},
        404: {"description": "Facture introuvable ou non autorisée"},
        500: {"description": "Échec de la création de la session Stripe"},
    },
)
async def create_invoice_checkout(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Génère un lien de paiement Stripe Checkout pour la facture.

    - Vérifie que la facture appartient à l'utilisateur connecté (404 sinon).
    - Crée une session Stripe ``mode="payment"`` en FCFA (XOF).
    - Renvoie ``{"checkout_url": session.url}`` au frontend.
    """
    # Sécurité : seul le propriétaire peut initier le paiement de SA facture.
    invoice = _get_own_invoice(invoice_id, current_user.id, db)

    # Facultatif mais propre : on évite de proposer de payer une facture déjà payée.
    if invoice.status == InvoiceStatus.PAID.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette facture est déjà payée.",
        )

    try:
        result = stripe_service.create_checkout_session(invoice)
    except stripe.error.StripeError as exc:
        logger.exception("Échec création session Stripe (facture %s).", invoice_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de créer la session de paiement : {exc.user_message or exc}",
        ) from exc

    return result


@router.post(
    "/invoices/{invoice_id}/send-email",
    summary="Envoyer une facture par e-mail au client",
    responses={
        200: {"description": "E-mail envoyé avec succès"},
        404: {"description": "Facture introuvable ou non autorisée"},
        400: {"description": "Le client n'a pas d'adresse e-mail"},
        500: {"description": "Échec de l'envoi de l'e-mail"},
    },
)
async def send_invoice_email_route(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Génère le PDF de la facture puis l'envoie par e-mail au client.

    - Vérifie que la facture appartient à l'utilisateur connecté (404 sinon).
    - Récupère l'adresse e-mail du client associé (400 si absente).
    - Génère le PDF (dans un thread, pour ne pas bloquer FastAPI).
    - Envoie l'e-mail avec la pièce jointe via ``send_invoice_email``.
    """
    invoice = _get_own_invoice(invoice_id, current_user.id, db)

    # Récupère le client associé (garanti par la vérification de propriété à
    # la création) pour obtenir son adresse e-mail.
    client = (
        db.query(Client)
        .filter(Client.id == invoice.client_id, Client.user_id == current_user.id)
        .first()
    )
    client_email = (client.email if client else None) or ""
    client_email = client_email.strip()

    if not client_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le client de cette facture n'a pas d'adresse e-mail renseignée.",
        )

    # --- Préparation des données pour le PDF ---
    # Lignes d'opération (multi-lignes) normalisées pour le rendu PDF.
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
            "email": client_email,
            "phone": client.phone if client else None,
            "address": client.address if client else None,
        },
        "freelance": {
            "full_name": current_user.full_name,
            "company_name": current_user.company_name,
            "email": current_user.email,
            "phone": None,
            "address": None,
        },
    }

    # --- Génération du PDF (opération CPU lourde -> thread séparé) ---
    try:
        pdf_bytes = await asyncio.to_thread(generate_invoice_pdf, invoice_data)
    except PDFGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    # --- Envoi de l'e-mail (SMTP -> thread séparé via asyncio.to_thread) ---
    try:
        await send_invoice_email(
            client_email=client_email,
            invoice_number=invoice.invoice_number,
            amount=float(invoice.amount),
            pdf_content=pdf_bytes,
        )
    except EmailSenderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return {
        "status": "ok",
        "message": f"Facture {invoice.invoice_number} envoyée par e-mail à {client_email}.",
    }


@router.post(
    "/webhooks/stripe",
    summary="Webhook Stripe (paiement de facture)",
    include_in_schema=False,
)
async def stripe_webhook(request: Request) -> dict:
    """Reçoit un événement Stripe et met à jour la facture en base.

    - Lit le corps brut de la requête (``request.body()``).
    - Vérifie la signature avec ``Stripe-Signature``.
    - Sur ``checkout.session.completed``, marque la facture citée dans les
      ``metadata`` comme payée (``paid``).
    """
    payload = await request.body()
    signature_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe_service.verify_webhook_signature(payload, signature_header)
    except ValueError as exc:
        # Secret webhook manquant.
        logger.error("Webhook Stripe : %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except stripe.error.SignatureVerificationError as exc:
        # Signature invalide : on rejette (400) pour ne pas accuser réception.
        logger.warning("Signature webhook Stripe invalide : %s", exc)
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    # Répond "200" implicitement en retournant un dict pour accuser réception.
    event_type = event.get("type")

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        invoice_id = (session.get("metadata") or {}).get("invoice_id")

        if invoice_id is not None:
            # Traite la mise à jour dans une session dédiée.
            _mark_invoice_paid_with_new_session(int(invoice_id))
        else:
            logger.warning(
                "Événement checkout.session.completed sans invoice_id dans metadata."
            )

    return {"received": True, "type": event_type}


def _mark_invoice_paid_with_new_session(invoice_id: int) -> None:
    """Marque la facture comme payée, dans une session DB dédiée."""
    db: Session = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            logger.error("Webhook Stripe : facture id=%s introuvable.", invoice_id)
            return
        if invoice.status == InvoiceStatus.PAID.value:
            logger.info("Facture id=%s déjà payée, rien à faire.", invoice_id)
            return
        invoice.status = InvoiceStatus.PAID.value
        db.commit()
        logger.info("Facture id=%s marquée comme payée via Stripe.", invoice_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Erreur, échec mise à jour facture id=%s.", invoice_id)
    finally:
        db.close()
