"""Routeur Billing — liens de paiement en ligne (Stripe + Mobile Money).

Endpoints (préfixe ``/billing``) :

1. ``POST /billing/invoices/{invoice_id}/generate-payment-links``
   - Réservé à l'utilisateur propriétaire de la facture (authentification).
   - Génère **les deux** moyens de paiement d'Afrique de l'Ouest (FCFA / XOF) :
       * Carte bancaire  -> Stripe Checkout (``create_stripe_checkout``,
         devise ``xof`` transmise **sans** multiplication par 100).
       * Mobile Money   -> CinetPay (``mobile_money_service``, devise ``XOF``).
   - Persiste chacun des deux liens en base (``stripe_payment_link`` et
     ``mobile_money_payment_link``) puis les renvoie.

2. ``POST /billing/cinetpay-webhook``
   - Webhook CinetPay : reçoit la notification brute, vérifie que le paiement
     est réellement réussi (``cpm_result == "00"``). Si c'est le cas, en extrait
     le ``cpm_trans_id`` (= notre numéro de facture / ID de transaction),
     retrouve la facture correspondante et bascule son statut à ``paid``.
     Sinon, un échec est loggé et le statut reste inchangé.
   - Non authentifié (appelé par les serveurs CinetPay).
"""

from __future__ import annotations

import asyncio
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import SessionLocal, get_db
from app.models.invoice import Invoice, InvoiceStatus
from app.models.user import User
from app.routers.invoices import _get_own_invoice
from app.services import mobile_money_service, stripe_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

# Devise unique UEMOA (FCFA), en majuscules côté CinetPay.
CURRENCY_XOF = "XOF"


async def _generate_stripe_link(invoice: Invoice) -> str:
    """Génère le lien Stripe Checkout d'une facture (devise ``xof``).

    Délègue à ``stripe_service.create_checkout_session`` qui gère déjà le
    FCFA **sans** multiplication par 100 (devise sans décimale). L'appel à la
    fois bloquant (I/O réseau) est déporté dans un thread pour ne pas geler la
    boucle événementielle.
    """
    try:
        result = await asyncio.to_thread(
            stripe_service.create_checkout_session, invoice
        )
    except stripe.error.StripeError as exc:  # noqa: BLE001
        logger.exception("Échec Stripe (facture %s).", invoice.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de créer le lien Stripe : {exc.user_message or exc}",
        ) from exc
    return result["checkout_url"]


async def _generate_mobile_money_link(invoice: Invoice) -> str:
    """Génère le lien de paiement Mobile Money (CinetPay, devise XOF).

    Délègue à ``mobile_money_service.create_mobile_money_link`` qui prépare le
    payload (montant entier, ``XOF``, ID de transaction = numéro de facture,
    URL de notification) puis renvoie la ``payment_url`` CinetPay. L'appel
    réseau est déporté dans un thread.
    """
    try:
        result = await asyncio.to_thread(
            mobile_money_service.create_mobile_money_link, invoice
        )
    except mobile_money_service.CinetPayError as exc:  # noqa: BLE001
        logger.exception("Échec CinetPay (facture %s).", invoice.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de créer le lien Mobile Money : {exc}",
        ) from exc
    return result["payment_url"]


@router.post(
    "/invoices/{invoice_id}/generate-payment-links",
    summary="Générer les liens de paiement (Stripe + Mobile Money)",
    responses={
        200: {"description": "Liens de paiement générés / régénérés et persistés"},
        400: {"description": "Facture déjà payée"},
        404: {"description": "Facture introuvable ou non autorisée"},
        502: {"description": "Échec de création d'un des liens de paiement"},
    },
)
async def generate_payment_links(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Génère, enregistre puis renvoie le Stripe ET le lien Mobile Money.

    - Vérifie que la facture appartient à l'utilisateur connecté (404 sinon).
    - N'ouvre pas un paiement sur une facture déjà réglée (400 sinon).
    - Crée la session Stripe Checkout en ``xof`` (sans x100) et le lien
      CinetPay en ``XOF``, puis persiste les deux sur la facture.
    """
    invoice = _get_own_invoice(invoice_id, current_user.id, db)

    if invoice.status == InvoiceStatus.PAID.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette facture est déjà payée.",
        )

    # ---- 1. Stripe (carte bancaire) : devise "xof", sans x100 ----
    stripe_url = await _generate_stripe_link(invoice)
    invoice.stripe_payment_link = stripe_url

    # ---- 2. CinetPay (Mobile Money) : devise "XOF" ----
    mobile_money_url = await _generate_mobile_money_link(invoice)
    invoice.mobile_money_payment_link = mobile_money_url

    # Persiste les deux liens en base puis renvoie-les.
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    return {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "currency": CURRENCY_XOF,
        "amount": invoice.amount,
        "stripe_payment_link": invoice.stripe_payment_link,
        "mobile_money_payment_link": invoice.mobile_money_payment_link,
    }


@router.post(
    "/cinetpay-webhook",
    summary="Webhook CinetPay (notification de paiement Mobile Money)",
    include_in_schema=False,
)
async def cinetpay_webhook(request: Request) -> dict:
    """Reçoit la notification CinetPay et marque la facture comme payée.

    CinetPay transmet au webhook une notification brute contenant notamment un
    code de résultat (``cpm_result``). Le paiement Mobile Money n'est considéré
    comme **réussi** que si ``cpm_result`` vaut exactement ``"00"`` (code de
    succès CinetPay). Dans ce cas seulement on bascule la facture à ``paid``.

    Sécurité du flux :
    1. on tente de lire le corps (form, sinon JSON, sinon texte brut) ;
    2. on vérifie que ``cpm_result`` est strictement égal à ``"00"`` ;
       sinon on loggue l'échec et on **ne change pas** le statut de la facture ;
    3. on extrait ``cpm_trans_id`` (l'identifiant de transaction — égal au
       numéro de facture passé à l'initiation) puis on retrouve la facture ;
    4. on bascule son statut à ``paid``.

    Returns:
        ``{"received": True, "cpm_trans_id": ...}`` pour accuser réception.
    """
    notification = await _read_cinetpay_body(request)

    # Code de résultat CinetPay : seul "00" signifie un paiement réussi.
    # Normalisé en chaîne pour comparer de façon robuste (form -> str/JSON -> int).
    result_code = str(notification.get("cpm_result") or "").strip()
    if result_code != "00":
        logger.warning(
            "Notification CinetPay avec résultat non réussi "
            "(cpm_result=%r). Statut inchangé.",
            result_code,
        )
        # On accuse réception mais sans marquer la facture comme payée.
        return {
            "received": True,
            "cpm_result": result_code,
            "paid": False,
        }

    # Identifiant de transaction, généralement sous la forme form/JSON.
    trans_id = (
        notification.get("cpm_trans_id")
        or notification.get("transaction_id")
        or ""
    )
    if not trans_id:
        logger.warning("Notification CinetPay sans cpm_trans_id reçue.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Champ 'cpm_trans_id' manquant dans la notification.",
        )

    # Met à jour la facture dans une session DB dédiée (le webhook est appelé
    # par CinetPay sans passer par nos dépendances d'authentification).
    db: Session = SessionLocal()
    try:
        # Le cpm_trans_id est notre numéro de facture (voir l'initiation).
        invoice = (
            db.query(Invoice)
            .filter(Invoice.invoice_number == trans_id)
            .first()
        )
        if invoice is None:
            logger.error(
                "Webhook CinetPay : aucune facture %s trouvée.", trans_id
            )
            return {
                "received": True,
                "cpm_trans_id": trans_id,
                "paid": False,
            }

        if invoice.status != InvoiceStatus.PAID.value:
            invoice.status = InvoiceStatus.PAID.value
            db.commit()
            logger.info("Facture %s marquée payée via CinetPay.", trans_id)
        else:
            logger.info("Facture %s déjà payée, rien à faire.", trans_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Échec mise à jour facture %s via CinetPay.", trans_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors du traitement de la notification.",
        )
    finally:
        db.close()

    return {
        "received": True,
        "cpm_trans_id": trans_id,
        "paid": True,
    }


async def _read_cinetpay_body(request: Request) -> dict:
    """Lit une notification CinetPay et la renvoie sous forme de dict.

    CinetPay utilise historiquement un POST *form-urlencoded*, mais certaines
    configurations émettent du JSON. On gère les deux cas sans échouer.
    """
    # Tentative de lecture du corps en tant que formulaire.
    try:
        form = await request.form()
        if form:
            return dict(form)
    except Exception:  # noqa: BLE001
        logger.debug("Corps CinetPay non interprété comme un formulaire.")

    # Sinon tentative JSON.
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        logger.debug("Corps CinetPay non interprété comme du JSON.")

    return {}
