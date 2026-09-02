"""Service de génération de PDF de factures pour InvoiceGuard.

Convertit un modèle Jinja2 (``app/templates/invoice_template.html``)
en PDF, en utilisant :

1. **WeasyPrint** (rendu fidèle du HTML + CSS) si disponible ;
2. **reportlab** en secours (fallback plus léger, compatible macOS
   Catalina) si WeasyPrint (ou ses dépendances système) fait défaut.

Le résultat est renvoyé sous forme d'un flux d'octets (``bytes``),
prêt à être renvoyé par FastAPI (StreamingResponse / Response).
"""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

# Chemin absolu du dossier contenant les templates HTML.
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_FILE = "invoice_template.html"

logger = logging.getLogger(__name__)


class PDFGenerationError(Exception):
    """Levée lorsqu'une erreur explicite survient lors de la génération du PDF."""


def format_fcfa(amount) -> str:
    """Formate un montant en FCFA, sans décimales superflues.

    Ex. 50000 -> "50 000 FCFA"  (espace comme séparateur de milliers).

    Args:
        amount: nombre (Decimal / float / str) représentant un montant.

    Returns:
        Chaîne au format "<milliers espacés> FCFA".
    """
    value = _to_float(amount)
    integer = int(round(value))
    formatted = f"{integer:,}".replace(",", " ")
    return f"{formatted} FCFA"


def _build_env() -> Environment:
    """Retourne un environnement Jinja2 configuré sur le dossier des templates.

    Enregistre le filtre ``fcfa`` afin de formater les montants directement
    dans le modèle HTML (ex: ``{{ invoice.amount | fcfa }}``).
    """
    if not TEMPLATES_DIR.is_dir():
        raise PDFGenerationError(
            f"Le dossier des templates '{TEMPLATES_DIR}' est introuvable."
        )
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Filtre maison : formate un montant en "X XXXX FCFA".
    env.filters["fcfa"] = format_fcfa
    return env


def render_invoice_html(invoice_data: dict) -> str:
    """Injecte les données de la facture dans le modèle HTML.

    Args:
        invoice_data: dictionnaire structuré (``invoice``, ``client``,
            infos freelance) — voir ``_build_context``.

    Returns:
        Le HTML final sous forme de chaîne.

    Raises:
        PDFGenerationError: si le template est introuvable ou si le
            rendu Jinja2 échoue.
    """
    context = _build_context(invoice_data)

    try:
        env = _build_env()
        template = env.get_template(TEMPLATE_FILE)
    except TemplateNotFound as exc:
        raise PDFGenerationError(
            f"Le template '{TEMPLATE_FILE}' est introuvable "
            f"dans '{TEMPLATES_DIR}'. Vérifiez le fichier."
        ) from exc

    try:
        rendered = template.render(**context)
    except Exception as exc:  # noqa: BLE001 - erreurs Jinja variées
        logger.exception("Échec du rendu HTML de la facture.")
        raise PDFGenerationError(
            f"Une erreur est survenue lors du rendu HTML de la facture : {exc}"
        ) from exc

    return rendered


def _build_context(invoice_data: dict) -> dict:
    """Normalise les données brutes en un contexte prêt pour le template.

    Attend notamment (clés tolérantes) :
        - ``invoice_number`` / ``number``
        - ``amount``
        - ``created_at`` / ``issue_date``
        - ``due_date``
        - ``status``
        - ``client`` (objet ou dict : ``name``, ``email``, ...)
        - ``freelance`` / ``user`` (objet ou dict) + champs ``full_name``,
          ``company_name``, ``email``, ``phone``, ``address``
    """
    invoice = dict(invoice_data)

    # --- Numéro de facture ---
    number = invoice.get("invoice_number") or invoice.get("number") or ""
    invoice["number"] = number

    # --- Dates ---
    invoice.setdefault(
        "issue_date",
        invoice.get("created_at") or invoice.get("issue_date") or "",
    )
    invoice.setdefault("due_date", invoice.get("due_date") or "")

    # --- Montant ---
    amount = invoice.get("amount", 0)
    invoice["amount"] = _to_float(amount)

    # --- Statut ---
    status = invoice.get("status", "")
    if hasattr(status, "value"):  # Enum (InvoiceStatus)
        status = status.value
    invoice["status"] = status or "draft"

    # --- Client ---
    client = invoice.get("client") or {}
    client_dict = _extract_entity(client)
    invoice["client"] = client_dict

    # --- Infos freelance ---
    freelance = invoice.get("freelance") or invoice.get("user") or {}
    freelance_dict = _extract_entity(freelance)

    # Alias utilisés par le template pour le bloc "De".
    invoice["freelance_name"] = (
        freelance_dict.get("full_name")
        or freelance_dict.get("name")
        or "Mon Entreprise"
    )
    invoice["freelance_company"] = freelance_dict.get("company_name")
    invoice["freelance_email"] = freelance_dict.get("email", "")
    invoice["freelance_phone"] = freelance_dict.get("phone")
    invoice["freelance_address"] = freelance_dict.get("address")

    # Pour le bloc brand (nom affiché en haut à gauche).
    invoice.setdefault("company_name", invoice["freelance_company"])

    return {"invoice": invoice}


def _extract_entity(entity) -> dict:
    """Convertit un objet (SQLAlchemy) ou un dict en dictionnaire plat."""
    if hasattr(entity, "__dict__"):
        data = {
            key: val
            for key, val in entity.__dict__.items()
            if not key.startswith("_")
        }
    elif isinstance(entity, dict):
        data = dict(entity)
    else:
        data = {}

    # Normalise les valeurs manquantes et les Enum.
    for key in ("email", "phone", "address", "company_name", "full_name", "name"):
        val = data.get(key)
        if hasattr(val, "value"):
            val = val.value
        data[key] = val if val is not None else ""

    return data


def _to_float(value) -> float:
    """Convertit un montant (Decimal / float / str) en float, sans jamais échouer."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Compilation du PDF
# ---------------------------------------------------------------------------

def generate_invoice_pdf(invoice_data: dict) -> bytes:
    """Génère le PDF d'une facture et le retourne sous forme d'octets.

    Utilise **WeasyPrint** en priorité, puis **reportlab** en secours.

    Args:
        invoice_data: dictionnaire des données de la facture (voir
            ``_build_context`` pour les clés acceptées).

    Returns:
        ``bytes`` contenant le PDF généré.

    Raises:
        PDFGenerationError: si le template est introuvable ou si la
            compilation du PDF échoue (avec un message explicite).
    """
    context = _build_context(invoice_data)
    html = render_invoice_html(invoice_data)

    # Étape 1 : essais de WeasyPrint (résultat mis en cache : l'import
    # peut être très lent quand les bibliothèques système font défaut).
    if _weasyprint_available():
        try:
            from weasyprint import HTML  # import différé, coûteux

            return HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()
        except Exception as exc:  # noqa: BLE001
            logger.debug("WeasyPrint a échoué à la compilation (%s).", exc)

    # Étape 2 : secours reportlab (compatible macOS Catalina).
    try:
        return _generate_pdf_reportlab(context)
    except Exception as exc:  # noqa: BLE001
        raise PDFGenerationError(
            "La compilation du PDF a échoué, ni WeasyPrint ni reportlab "
            f"n'ont pu générer le document. Détail : {exc}"
        ) from exc


_WEASYPRINT_OK: bool | None = None


def _weasyprint_available() -> bool:
    """Retourne True si WeasyPrint est utilisable, en mettant le résultat en cache.

    L'import de WeasyPrint tente de charger plusieurs bibliothèques système
    (libgobject, pango, ...) et peut prendre plusieurs dizaines de secondes
    (voire échouer) quand elles sont absentes. On ne fait donc ce test
    qu'une seule fois par processus.
    """
    global _WEASYPRINT_OK
    if _WEASYPRINT_OK is None:
        try:
            import weasyprint  # noqa: F401
            _WEASYPRINT_OK = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("WeasyPrint indisponible (%s).", exc)
            _WEASYPRINT_OK = False
    return _WEASYPRINT_OK


def _generate_pdf_reportlab(context: dict) -> bytes:
    """Fallback reportlab : reconstruit une facture structurée en mémoire.

    Le HTML/CSS ne peut pas être rendu fidèlement par reportlab ; on
    reconstruit donc une mise en page simple et lisible directement à
    partir du contexte normalisé (produit par ``_build_context``).

    Args:
        context: contexte normalisé ``{"invoice": {...}}``.

    Returns:
        ``bytes`` contenant le PDF généré.
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT

    invoice = context.get("invoice", {})
    client = invoice.get("client", {})
    amount = invoice.get("amount", 0.0)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", fontName="Helvetica-Bold", fontSize=20, leading=24,
        spaceAfter=6, textColor=colors.HexColor("#2980b9"),
    )
    brand_style = ParagraphStyle(
        "Brand", parent=styles["Heading1"], fontSize=22, leading=26,
        textColor=colors.HexColor("#2980b9"), spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", fontName="Helvetica", fontSize=9, leading=12,
        textColor=colors.HexColor("#7f8c8d"),
    )
    body_style = ParagraphStyle(
        "Body", fontName="Helvetica", fontSize=10, leading=14,
    )
    strong_style = ParagraphStyle(
        "Strong", parent=body_style, fontName="Helvetica-Bold",
    )
    section_style = ParagraphStyle(
        "Section", fontName="Helvetica-Bold", fontSize=11, leading=14,
        spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#34495e"),
    )

    story = []

    # ---- En-tête : marque + titre facture ----
    header_left = [
        Paragraph(
            "<b>%s</b>" % (invoice.get("company_name") or invoice.get("freelance_name") or "Facture"),
            brand_style,
        ),
    ]
    email = invoice.get("freelance_email")
    if email:
        header_left.append(Paragraph(_esc(email), subtitle_style))
    phone = invoice.get("freelance_phone")
    if phone:
        header_left.append(Paragraph(_esc(phone), subtitle_style))

    header_right = [
        Paragraph("FACTURE", title_style),
        Paragraph(f"<b>{_esc(invoice.get('number') or '')}</b>", body_style),
        Paragraph(f"Émission : {_esc(invoice.get('issue_date') or '—')}", subtitle_style),
    ]
    if invoice.get("due_date"):
        header_right.append(Paragraph(f"Échéance : {_esc(invoice['due_date'])}", subtitle_style))

    header = Table(
        [[header_left, header_right]],
        colWidths=[90 * mm, 85 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 14))

    # ---- Blocs « Facturé à » / « De » ----
    client_block = [
        Paragraph("Facturé à", section_style),
        Paragraph(f"<b>{_esc(client.get('name') or '—')}</b>", body_style),
    ]
    for key in ("email", "phone", "address"):
        val = client.get(key)
        if val:
            client_block.append(Paragraph(_esc(val), body_style))

    freelance_block = [
        Paragraph("De", section_style),
        Paragraph(f"<b>{_esc(invoice.get('freelance_name') or '—')}</b>", body_style),
    ]
    if invoice.get("freelance_company"):
        freelance_block.append(Paragraph(_esc(invoice["freelance_company"]), body_style))
    if invoice.get("freelance_email"):
        freelance_block.append(Paragraph(_esc(invoice["freelance_email"]), body_style))

    parties = Table([[client_block, freelance_block]], colWidths=[90 * mm, 85 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(parties)

    # ---- Table des montants (en FCFA) ----
    amount_fcfa = format_fcfa(amount)
    data = [
        ["Désignation", "Prix HT"],
        ["Prestation facturée", amount_fcfa],
        ["Total HT", amount_fcfa],
    ]
    amounts_table = Table(data, colWidths=[130 * mm, 45 * mm])
    amounts_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f1f1")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -2), 0.4, colors.HexColor("#e0e0e0")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#e0e0e0")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 14),
    ]))
    story.append(Spacer(1, 14))
    story.append(amounts_table)

    # ---- Métadonnées de bas de page ----
    meta = Paragraph(
        "Statut : <b>%s</b><br/>N° de facture : <b>%s</b>" % (
            _esc(invoice.get("status") or "draft"),
            _esc(invoice.get("number") or ""),
        ),
        subtitle_style,
    )
    story.append(Spacer(1, 20))
    story.append(meta)

    doc.build(story)
    return buffer.getvalue()


def _esc(text: str) -> str:
    """Échappe le texte pour un rendu sûr dans reportlab (legacy XML)."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
