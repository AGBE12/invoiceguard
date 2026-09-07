"""Service d'intégration CinetPay (Mobile Money, devise XOF) pour InvoiceGuard.

Encapsule la création de liens de paiement Mobile Money via l'API
CinetPay (zone UEMOA / devise FCFA).

Le flux utilisé est une **initialisation de checkout** : on prépare un payload
avec la devise ``XOF``, le montant et un identifiant de transaction unique —
le numéro de facture — puis on appelle l'endpoint CinetPay qui renvoie une
``payment_url`` vers laquelle on redirige le client pour qu'il procède au
paiement par Mobile Money.

Le client HTTP utilisé est ``httpx`` (asynchrone-compatible mais le service
emploie un client synchrone simple, exécuté hors de la boucle événementielle
lors de l'appel depuis un endpoint FastAPI).
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Devise attendue par CinetPay (ISO 4217 en majuscules).
CURRENCY_XOF = "XOF"

# Durée (s) d'attente maximale pour la réponse de l'API CinetPay.
_REQUEST_TIMEOUT = 20.0


class CinetPayError(Exception):
    """Erreur générique lors de l'appel à l'API CinetPay."""


class CinetPayConfigError(CinetPayError):
    """Erreur de configuration (clé API / site_id manquants).

    Lève au lieu d'invoquer l'API pour donner un message d'erreur clair sur
    l'absence de configuration plutôt qu'un échec réseau opaque.
    """


def _xof_integer(amount) -> int:
    """Convertit un montant (Decimal/float/str) en entier FCFA (XOF).

    Le XOF est une devise **sans décimales** : on arrondit à l'entier le plus
    proche, sans aucune multiplication (contrairement à EUR/USD multipliés
    par 100 côté certains fournisseurs).
    """
    return int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _assert_configured() -> None:
    """Vérifie que les identifiants CinetPay sont présents dans la config."""
    if not settings.cinetpay_api_key.strip():
        raise CinetPayConfigError(
            "CINETPAY_API_KEY est manquante. Définissez-la dans les variables "
            "d'environnement (fichier .env ou dashboard d'hébergement)."
        )
    if not str(settings.cinetpay_site_id).strip():
        raise CinetPayConfigError(
            "CINETPAY_SITE_ID est manquant. Définissez-le dans les variables "
            "d'environnement."
        )


def _build_payload(invoice) -> dict[str, Any]:
    """Prépare le payload d'initiation de paiement CinetPay (devise XOF).

    Args:
        invoice: objet facture exposant ``numéro`` (``invoice_number``) et
            ``montant`` (``amount``).

    Returns:
        Dictionnaire prêt à être envoyé en JSON à l'endpoint CinetPay.
    """
    amount = _xof_integer(invoice.amount)

    payload: dict[str, Any] = {
        # Identifiants de la place de marché CinetPay.
        "apikey": settings.cinetpay_api_key.strip(),
        "site_id": str(settings.cinetpay_site_id).strip(),
        # Environnement : "TEST" en développement, "PRODUCTION" en prod.
        "mode": settings.cinetpay_mode.strip() or "TEST",
        # Montant entier en FCFA (sans décimales).
        "amount": amount,
        # Devise demandée : XOF (FCFA).
        "currency": CURRENCY_XOF,
        # Identifiant de transaction : numéro de facture (unique).
        # Sera renvoyé dans la notification (``cpm_trans_id``) pour retrouver
        # la facture au déclenchement du webhook.
        "transaction_id": invoice.invoice_number,
        "description": f"Règlement de la facture {invoice.invoice_number}",
        # URL à laquelle CinetPay notifie la fin de la transaction.
        "notify_url": f"{settings.backend_url}/billing/cinetpay-webhook",
        # URLs de retour du client une fois le paiement terminé / annulé.
        "return_url": settings.cinetpay_success_url,
        "cancel_url": settings.cinetpay_cancel_url,
    }
    return payload


def create_mobile_money_link(invoice) -> dict[str, Any]:
    """Crée un lien de paiement Mobile Money via CinetPay (devise XOF).

    - Vérifie que les identifiants CinetPay sont configurés.
    - Prépare le payload (montant entier, devise ``XOF``, ID de transaction =
      numéro de facture, URL de notification vers le webhook CinetPay).
    - Envoie la requête ``httpx`` à ``CINETPAY_CHECKOUT_URL``.
    - Extrait et renvoie l'URL de paiement chez CinetPay (``payment_url``).

    Args:
        invoice: objet facture (``invoice_number`` et ``amount``).

    Returns:
        Dictionnaire ``{"payment_url": <str>}``.

    Raises:
        CinetPayConfigError: si la clé API / le site_id ne sont pas définis.
        CinetPayError: si l'appel réseau échoue ou si CinetPay renvoie un code
            d'erreur métier.
    """
    _assert_configured()

    payload = _build_payload(invoice)

    logger.info(
        "Initiation CinetPay — facture %s, montant %d %s.",
        invoice.invoice_number,
        payload["amount"],
        CURRENCY_XOF,
    )

    try:
        # Client HTTP simple (synchrone). Il sera idéalement exécuté via
        # ``asyncio.to_thread`` s'il est appelé depuis une coroutine FastAPI.
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            response = client.post(
                settings.cinetpay_checkout_url,
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.exception(
            "Échec réseau CinetPay (facture %s).", invoice.invoice_number
        )
        raise CinetPayError(
            f"Impossible de joindre CinetPay : {exc}"
        ) from exc

    if response.status_code >= 400:
        logger.error(
            "CinetPay a répondu HTTP %s (facture %s) : %s",
            response.status_code,
            invoice.invoice_number,
            response.text[:500],
        )
        raise CinetPayError(
            f"CinetPay a répondu avec le statut HTTP {response.status_code}."
        )

    try:
        data = response.json()
    except ValueError as exc:  # noqa: BLE001
        logger.exception("Réponse CinetPay non-JSON.")
        raise CinetPayError("Réponse CinetPay illisible (non-JSON).") from exc

    # CinetPay signale son succès via un code métier ; nous acceptons par
    # défaut tout corps contenant une ``payment_url`` exploitable.
    payment_url = (
        (data.get("data") or {}).get("payment_url")
        if isinstance(data.get("data"), dict)
        else None
    )
    if not payment_url:
        logger.error(
            "CinetPay n'a pas renvoyé de payment_url (facture %s) : %s",
            invoice.invoice_number,
            str(data)[:500],
        )
        raise CinetPayError(
            "CinetPay n'a pas renvoyé d'URL de paiement (payment_url)."
        )

    logger.info(
        "Lien Mobile Money généré pour la facture %s.", invoice.invoice_number
    )
    return {"payment_url": payment_url}
