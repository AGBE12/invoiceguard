"""Service d'envoi d'e-mails pour InvoiceGuard.

Fournit une fonction asynchrone ``send_invoice_email`` qui envoie une facture
par e-mail au client, avec le PDF en pièce jointe le cas échéant.

Le blocage réseau (connexion SMTP + envoi) est délégué à un thread séparé
via ``asyncio.to_thread`` afin de ne pas bloquer la boucle événementielle
de FastAPI. La configuration SMTP est lue depuis ``app.config.settings``
(variables d'environnement ``SMTP_SERVER``, ``SMTP_PORT``, ``SMTP_USERNAME``,
``SMTP_PASSWORD``, ``EMAIL_FROM``).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.headerregistry import Address
from email.message import EmailMessage

from app.config import settings
from app.services.pdf_generator import format_fcfa

logger = logging.getLogger(__name__)


class EmailSenderError(Exception):
    """Levée lorsqu'un e-mail ne peut pas être envoyé."""


def _render_html(invoice_number: str, amount_fcfa: str) -> str:
    """Compose un e-mail HTML professionnel et courtois en français."""
    return f"""\
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, Helvetica, sans-serif; color: #2c3e50; margin: 0; padding: 0; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 24px; }}
        h1 {{ color: #2980b9; font-size: 22px; }}
        .box {{
            background: #f4f6f8;
            border-left: 4px solid #2980b9;
            padding: 14px 16px;
            margin: 18px 0;
            font-size: 15px;
        }}
        .amount {{ font-size: 20px; font-weight: bold; color: #2980b9; }}
        .footer {{ color: #7f8c8d; font-size: 12px; margin-top: 28px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Votre facture {invoice_number}</h1>
        <p>Bonjour,</p>
        <p>Nous vous remercions pour votre confiance.</p>
        <div class="box">
            <p>Vous trouverez ci-joint votre facture :</p>
            <p><strong>Numéro :</strong> {invoice_number}</p>
            <p><strong>Montant à régler :</strong> <span class="amount">{amount_fcfa}</span></p>
        </div>
        <p>
            Si vous avez la moindre question, n'hésitez pas à nous contacter.
            Merci pour votre paiement.
        </p>
        <div class="footer">
            <p>Cordialement,<br>L'équipe InvoiceGuard</p>
        </div>
    </div>
</body>
</html>
"""


def _send_blocking(
    client_email: str,
    invoice_number: str,
    amount_fcfa: str,
    pdf_content: bytes | None,
) -> None:
    """Envoie réellement l'e-mail (fonction synchrone, exécutée dans un thread).

    Établit une connexion SMTP, authentifie puis transmet le message avec,
    éventuellement, le PDF joint. Lève ``EmailSenderError`` en cas d'échec.
    """
    server_addr = settings.smtp_server.strip()
    if not server_addr:
        raise EmailSenderError(
            "SMTP non configuré : définissez SMTP_SERVER dans l'environnement."
        )

    port = settings.smtp_port
    from_addr = settings.email_from.strip() or settings.smtp_username.strip()

    # --- Construction du message MIME ---
    msg = EmailMessage()
    msg["Subject"] = f"Votre facture {invoice_number}"
    msg["From"] = from_addr
    msg["To"] = client_email

    # Corps HTML.
    msg.set_content(
        f"Bonjour,\n\nVeuillez trouver ci-joint votre facture {invoice_number} "
        f"d'un montant de {amount_fcfa}.\n\nCordialement,\nL'équipe InvoiceGuard."
    )
    msg.add_alternative(_render_html(invoice_number, amount_fcfa), subtype="html")

    # --- Pièce jointe PDF (si fournie) ---
    if pdf_content is not None:
        # Nom de fichier sûr (ASCII).
        safe_number = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in invoice_number
        ) or str(invoice_number)
        filename = f"facture_{safe_number}.pdf"

        msg.add_attachment(
            pdf_content,
            maintype="application",
            subtype="pdf",
            filename=filename,
        )

    # --- Envoi via SMTP ---
    try:
        with smtplib.SMTP(server_addr, port, timeout=30) as server:
            server.ehlo()
            # TLS si le port indique qu'il est utilisé (587 standard, 465 SMTPS).
            if port in (587, 25):
                server.starttls()
                server.ehlo()
            if settings.smtp_username.strip() or settings.smtp_password:
                server.login(
                    settings.smtp_username.strip(),
                    settings.smtp_password,
                )
            server.send_message(msg)
        logger.info(
            "E-mail envoyé à %s pour la facture %s (%s).",
            client_email,
            invoice_number,
            amount_fcfa,
        )
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("Échec de l'envoi de l'e-mail à %s.", client_email)
        raise EmailSenderError(f"Échec de l'envoi de l'e-mail : {exc}") from exc


async def send_invoice_email(
    client_email: str,
    invoice_number: str,
    amount: float,
    pdf_content: bytes | None = None,
) -> None:
    """Envoie une facture par e-mail au client (en tâche de fond asynchrone).

    Args:
        client_email: adresse du destinataire.
        invoice_number: numéro de la facture (affiché dans l'objet et le corps).
        amount: montant de la facture (formaté en FCFA dans le corps).
        pdf_content: octets du PDF à joindre, ou None pour un e-mail simple.

    Raises:
        EmailSenderError: si l'envoi échoue (après rejet des exceptions SMTP).
    """
    amount_fcfa = format_fcfa(amount)

    # Délègue l'opération bloquante à un thread séparé pour ne pas geler
    # la boucle d'événements de FastAPI.
    await asyncio.to_thread(
        _send_blocking,
        client_email,
        invoice_number,
        amount_fcfa,
        pdf_content,
    )
