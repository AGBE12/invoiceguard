"""Service d'intégration Stripe Checkout pour InvoiceGuard.

Encapsule la création de sessions de paiement et la vérification des
webhooks Stripe, en gérant correctement la devise FCFA (XOF) qui est une
**devise sans décimale** : le montant est transmis tel quel à Stripe,
sans être multiplié par 100.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

import stripe

from app.config import settings

logger = logging.getLogger(__name__)

# Devises Stripe sans décimale : le montant envoyé dans ``unit_amount``
# est exprimé dans l'unité monétaire réelle (pas en centimes).
_ZERO_DECIMAL_CURRENCIES = {"xof", "jpy", "krw", "vnd", "clp", "idr"}

# La devise de notre application (FCFA / XOF).
CURRENCY = settings.stripe_currency.lower()


def configure_stripe() -> None:
    """Configure le SDK Stripe avec la clé secrète chargée depuis l'environnement.

    À appeler au démarrage de l'application. Lève une erreur explicite si la
    clé Stripe est absente (aucun paiement ne pourra fonctionner sans elle).
    """
    api_key = settings.stripe_secret_key.strip()
    if not api_key:
        raise RuntimeError(
            "STRIPE_SECRET_KEY est manquante. Définissez-la dans les variables "
            "d'environnement (dans .env en local, ou dans le dashboard de "
            "déploiement)."
        )
    stripe.api_key = api_key
    logger.info("Stripe initialisé.")

    # Vérifie simplement qu'une clé a été utilisable (test en prod et dev).
    if api_key.startswith("sk_live"):
        logger.warning("Stripe utilise une clé LIVE — soyez vigilant lors des tests.")
    else:
        logger.info("Stripe en mode test (sk_test).")


def _unit_amount_for_amount(amount) -> int:
    """Convertit un montant (Decimal/float/str) en entier pour Stripe.

    Pour le XOF (et les devises zero-decimal), le montant est utilisé tel quel,
    arrondi à l'entier le plus proche — **sans** multiplication par 100.

    Pour les devises à 2 décimales (ex. EUR, USD), il faudrait multiplier
    par 100. Ici nous traitons le FCFA (XOF) par défaut.
    """
    value = float(amount)
    if CURRENCY in _ZERO_DECIMAL_CURRENCIES:
        # FCFA : pas de décimales, montant entier tel quel.
        # ROUND_HALF_UP : demi-arrondi vers le haut (prévisible en finance).
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    # Autres devises (général) : conversion en centimes.
    return int(round(value * 100))


def create_checkout_session(invoice) -> dict:
    """Crée une session Stripe Checkout pour payer une facture.

    Args:
        invoice: objet facture contenant au moins ``id``, ``invoice_number``
            et ``amount``.

    Returns:
        Dictionnaire ``{"checkout_url": session.url}`` menant au formulaire
        de paiement Stripe hébergé.

    Raises:
        stripe.error.StripeError: si la création de la session échoue.
    """
    amount = _unit_amount_for_amount(invoice.amount)
    invoice_label = f"Facture {invoice.invoice_number}"

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": CURRENCY,
                    "product_data": {"name": invoice_label},
                    # FCFA (XOF) : montant entier, SANS x100.
                    "unit_amount": amount,
                },
                "quantity": 1,
            }
        ],
        metadata={"invoice_id": invoice.id},
        success_url=settings.checkout_success_url,
        cancel_url=settings.checkout_cancel_url,
    )

    if not session.url:
        raise stripe.error.StripeError(
            "Stripe n'a pas renvoyé d'URL de session Checkout."
        )

    logger.info(
        "Session Checkout créée pour la facture %s (montant %d %s).",
        invoice.invoice_number,
        amount,
        CURRENCY.upper(),
    )
    return {"checkout_url": session.url}


def verify_webhook_signature(payload: bytes, signature_header: str) -> dict:
    """Vérifie et décode un événement Stripe reçu sur le webhook.

    Args:
        payload: corps brut (bytes) de la requête HTTP.
        signature_header: valeur de l'en-tête ``Stripe-Signature``.

    Returns:
        L'objet ``event`` Stripe.

    Raises:
        ValueError: si ``STRIPE_WEBHOOK_SECRET`` est manquante.
        stripe.error.SignatureVerificationError: si la signature est invalide
            (tampering / mauvaise clé).
    """
    webhook_secret = settings.stripe_webhook_secret.strip()
    if not webhook_secret:
        raise ValueError(
            "STRIPE_WEBHOOK_SECRET est manquante. Définissez-la dans les "
            "variables d'environnement."
        )

    return stripe.Webhook.construct_event(
        payload,
        signature_header,
        webhook_secret,
    )

