"""Service de génération de PDF de factures pour InvoiceGuard.

Convertit un modèle Jinja2 (``app/templates/invoice_template.html``) en PDF,
en utilisant :

1. **WeasyPrint** (rendu fidèle du HTML + CSS terracotta) si disponible ;
2. **reportlab** en secours (fallback plus léger, compatible macOS Catalina)
   si WeasyPrint (ou ses dépendances système) fait défaut.

Le résultat est renvoyé sous forme d'un flux d'octets (``bytes``), prêt à
être renvoyé par FastAPI (StreamingResponse / Response).

Design : thème terracotta / orange chaud (#b75b0a), immense bloc
« RÉSUMÉ / SUMMARY » avec le total en très gros, tableau listant toutes les
lignes d'opération, et bloc de signature de l'émetteur en bas.
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


# Couleurs du thème terracotta (partagées avec le fallback reportlab).
TERRA = "#b75b0a"
TERRA_DARK = "#8f4707"
TERRA_SOFT = "#fff7eb"


def format_fcfa(amount) -> str:
    """Formate un montant en FCFA, sans décimales superflues.

    Ex. 50000 -> "50 000 FCFA"  (espace comme séparateur de milliers).
    """
    value = _to_float(amount)
    integer = int(round(value))
    formatted = f"{integer:,}".replace(",", " ")
    return f"{formatted} FCFA"


def _build_env() -> Environment:
    """Retourne un environnement Jinja2 configuré sur le dossier des templates."""
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
    env.filters["fcfa"] = format_fcfa
    return env


def render_invoice_html(invoice_data: dict) -> str:
    """Injecte les données de la facture dans le modèle HTML."""
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
        return template.render(**context)
    except Exception as exc:  # noqa: BLE001 - erreurs Jinja variées
        logger.exception("Échec du rendu HTML de la facture.")
        raise PDFGenerationError(
            f"Une erreur est survenue lors du rendu HTML de la facture : {exc}"
        ) from exc


def _build_context(invoice_data: dict) -> dict:
    """Normalise les données brutes en un contexte prêt pour le template.

    La donnée attendue contient notamment :
        - ``invoice_number`` / ``number``
        - ``amount``  (total général de la facture)
        - ``items``   (liste de lignes : description / quantity / unit_price)
        - ``created_at`` / ``issue_date`` / ``due_date`` / ``status``
        - ``client`` (objet ou dict : name, email, phone, address)
        - ``freelance`` / ``user`` (+ full_name, company_name, email, address)
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

    # --- Montant total ---
    amount = invoice.get("amount", 0)
    invoice["amount"] = _to_float(amount)

    # --- Lignes d'opération (multi-lignes) ---
    # Chaque ligne est normalisée : description, quantité, prix unitaire et
    # montant HT (quantité x prix unitaire) calculé.
    raw_items = invoice.get("items") or []
    items: list[dict] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            item = dict(raw)
        else:  # objet SQLAlchemy
            item = {
                key: val
                for key, val in raw.__dict__.items()
                if not key.startswith("_")
            }
        desc = item.get("description")
        item["description"] = desc if desc is not None else "Prestation facturée"
        qty = int(item.get("quantity") or 1)
        item["quantity"] = qty if qty >= 1 else 1
        unit_price = item.get("unit_price")
        unit_price_f = _to_float(unit_price) if unit_price is not None else 0.0
        item["unit_price"] = unit_price_f
        item["amount"] = _to_float(item["quantity"]) * unit_price_f
        items.append(item)

    # À défaut (anciennes factures sans lignes), on génère une ligne de secours.
    if not items:
        items = [
            {
                "description": "Prestation facturée",
                "quantity": 1,
                "unit_price": invoice["amount"],
                "amount": invoice["amount"],
            }
        ]
    invoice["line_items"] = items

    # --- Statut ---
    status = invoice.get("status", "")
    if hasattr(status, "value"):  # Enum (InvoiceStatus)
        status = status.value
    invoice["status"] = status or "draft"

    # --- Client ---
    client = invoice.get("client") or {}
    invoice["client"] = _extract_entity(client)

    # --- Infos freelance / émetteur (bloc signature) ---
    freelance = invoice.get("freelance") or invoice.get("user") or {}
    freelance_dict = _extract_entity(freelance)
    invoice["freelance_name"] = (
        freelance_dict.get("full_name")
        or freelance_dict.get("name")
        or "Mon Entreprise"
    )
    invoice["freelance_company"] = freelance_dict.get("company_name")
    invoice["freelance_email"] = freelance_dict.get("email", "")
    invoice["freelance_address"] = freelance_dict.get("address")

    # Nom de marque affiché en haut à gauche.
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
    """
    context = _build_context(invoice_data)
    html = render_invoice_html(invoice_data)

    if _weasyprint_available():
        try:
            from weasyprint import HTML  # import différé, coûteux
            return HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()
        except Exception as exc:  # noqa: BLE001
            logger.debug("WeasyPrint a échoué à la compilation (%s).", exc)

    try:
        return _generate_pdf_reportlab(context)
    except Exception as exc:  # noqa: BLE001
        raise PDFGenerationError(
            "La compilation du PDF a échoué, ni WeasyPrint ni reportlab "
            f"n'ont pu générer le document. Détail : {exc}"
        ) from exc


_WEASYPRINT_OK: bool | None = None


def _weasyprint_available() -> bool:
    """Retourne True si WeasyPrint est utilisable (résultat mis en cache)."""
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
    """Fallback reportlab : facture terracotta structurée.

    Reproduit, sans CSS, la mise en page demandée côté métier :
    émetteur -> résumé en très gros -> tableau des lignes -> bloc signature.
    """
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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

    invoice = context.get("invoice", {})
    client = invoice.get("client", {})
    amount = invoice["amount"]
    items = invoice.get("line_items", [])

    terra = colors.HexColor(TERRA)
    terra_dark = colors.HexColor(TERRA_DARK)
    terra_soft = colors.HexColor(TERRA_SOFT)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Facture %s" % invoice.get("number", ""),
    )

    base = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        "Brand", parent=base["Heading1"], fontSize=24, leading=28,
        textColor=terra, spaceAfter=0,
    )
    title_style = ParagraphStyle(
        "Title", fontName="Helvetica-Bold", fontSize=20, leading=24,
        alignment=TA_RIGHT, textColor=colors.white, backColor=terra,
        borderPadding=(6, 14, 6, 14), spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", fontName="Helvetica", fontSize=10, leading=14,
        alignment=TA_RIGHT, textColor=colors.HexColor("#7f8c8d"),
    )
    body_style = ParagraphStyle(
        "Body", fontName="Helvetica", fontSize=10, leading=14,
    )
    party_title = ParagraphStyle(
        "PartyTitle", fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=terra, spaceAfter=6,
    )
    summary_label = ParagraphStyle(
        "SumLabel", fontName="Helvetica-Bold", fontSize=15, leading=18,
        alignment=TA_CENTER, textColor=terra,
    )
    summary_amount = ParagraphStyle(
        "SumAmount", fontName="Helvetica-Bold", fontSize=34, leading=40,
        alignment=TA_CENTER, textColor=colors.HexColor("#2c3e50"),
    )
    summary_due = ParagraphStyle(
        "SumDue", fontName="Helvetica", fontSize=11, leading=15,
        alignment=TA_CENTER, textColor=colors.HexColor("#8f4707"),
    )
    sig_style = ParagraphStyle(
        "Sig", parent=body_style, spaceBefore=4,
    )

    story = []

    # ---- En-tête : brand + titre ----
    brand_name = invoice.get("company_name") or invoice.get("freelance_name")         or "Entreprise"
    header = Table(
        [[
            [Paragraph("<b>%s</b>" % _esc(brand_name), brand_style)],
            [Paragraph("FACTURE", title_style)],
        ]],
        colWidths=[100 * mm, 75 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 2, terra),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(header)

    # Numéro + dates (aligné à droite)
    meta_lines = [f"<b>{_esc(invoice.get('number') or '')}</b>",
                  "Émission : %s" % _esc(invoice.get("issue_date") or "—")]
    if invoice.get("due_date"):
        meta_lines.append("Échéance : %s" % _esc(invoice["due_date"]))
    for ln in meta_lines:
        story.append(Paragraph(ln, subtitle_style))
    story.append(Spacer(1, 10))

    # ---- Facturé à ----
    client_lines = [Paragraph("FACTURÉ À", party_title),
                    Paragraph("<b>%s</b>" % _esc(client.get("name") or "—"), body_style)]
    for key in ("email", "phone", "address"):
        val = client.get(key)
        if val:
            client_lines.append(Paragraph(_esc(val), body_style))
    client_box = Table([[
        client_lines,
        # case vide de droite pour garder le look épuré (pas de bloc "De" ici)
        "",
    ]], colWidths=[120 * mm, 55 * mm])
    client_box.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (0, 0), 0.6, terra),
        ("LEFTPADDING", (0, 0), (0, 0), 10),
        ("BACKGROUND", (0, 0), (0, 0), terra_soft),
    ]))
    story.append(client_box)
    story.append(Spacer(1, 14))

    # ---- IMMENSE RÉSUMÉ / TOTAL ----
    summary_cell = [
        Paragraph("RÉSUMÉ / SUMMARY", summary_label),
        Paragraph(format_fcfa(amount), summary_amount),
        Paragraph(
            "Date d'échéance : <b>%s</b>" % _esc(invoice.get("due_date") or "paiement dû à réception"),
            summary_due,
        ),
    ]
    summary_box = Table([[summary_cell]], colWidths=[175 * mm])
    summary_box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 2.5, terra),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffe2bd")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 16),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
    ]))
    story.append(summary_box)
    story.append(Spacer(1, 18))

    # ---- Tableau des lignes ----
    header_row = ["Désignation", "Qté", "Prix unitaire", "Montant HT"]
    data = [header_row]
    for it in items:
        data.append([
            _esc(it["description"]),
            str(it["quantity"]),
            format_fcfa(it["unit_price"]),
            format_fcfa(it["amount"]),
        ])
    data.append(["TOTAL À PAYER", "", "", format_fcfa(amount)])

    n_rows = len(data)
    items_table = Table(data, colWidths=[93 * mm, 14 * mm, 34 * mm, 34 * mm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), terra_soft),
        ("TEXTCOLOR", (0, 0), (-1, 0), terra_dark),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fffaf2")]),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#efddc6")),
        ("LINEBELOW", (0, n_rows - 1), (-1, n_rows - 1), 2, terra),
        ("SPAN", (0, n_rows - 1), (2, n_rows - 1)),
        ("FONTNAME", (0, n_rows - 1), (-1, n_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, n_rows - 1), (-1, n_rows - 1), 12),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 34))

    # ---- Bloc signature ----
    sig_lines = [
        Paragraph("L'ÉMETTEUR (SIGNATURE &amp; CACHET)", party_title),
        Paragraph(
            "Je certifie que les prestations ci-dessus ont été réalisées et sont dues.",
            ParagraphStyle("sigNote", parent=sig_style, textColor=colors.HexColor("#7f8c8d")),
        ),
    ]
    if invoice.get("freelance_name"):
        sig_lines.append(Paragraph(
            "<b>%s</b>" % _esc(invoice["freelance_name"]), sig_style))
    if invoice.get("freelance_company"):
        sig_lines.append(Paragraph(_esc(invoice["freelance_company"]), sig_style))
    if invoice.get("freelance_email"):
        sig_lines.append(Paragraph(_esc(invoice["freelance_email"]), sig_style))

    signature = Table([[sig_lines]], colWidths=[175 * mm])
    signature.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#efddc6")),
        ("LINEABOVE", (0, 0), (-1, 0), 3, terra),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(signature)

    # Espace blanc pour la signature manuscrite.
    story.append(Spacer(1, 26))
    story.append(Paragraph(
        "Signature &amp; cachet de l'émetteur",
        ParagraphStyle("sigSpace", parent=subtitle_style,
                       alignment=TA_RIGHT,
                       borderColor=terra, borderWidth=0.4, borderPadding=(4, 0, 0, 0)),
    ))

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
