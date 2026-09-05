"""Migrations d'initialisation légères pour InvoiceGuard (adaptées au MVP).

Le projet ne dispose volontairement pas d'Alembic : pour un MVP, on s'appuie
sur ``Base.metadata.create_all`` (qui crée les tables absentes) complété par
une petite migration « glissante » qui :

1. Ajoute les colonnes nouvelles aux tables déjà existantes. Par exemple la
   table ``invoices`` a gagné ``description``, ``quantity``, ``unit_price``
   dans une première itération.
2. Crée la table ``invoice_items`` (lignes d'opération) si elle n'existe pas.
   ``create_all`` ne crée que les tables absentes ; néanmoins on la force ici
   pour garantir aussi sa présence sur les bases dont la création remonte à
   avant ce modèle multi-lignes.

Compatibilité :
    - SQLite (développement local) : ne supporte pas ``ADD COLUMN IF NOT EXISTS``
      ni les contraintes ajoutées après coup. On se base sur l'inspection SQLAlchemy
      pour n'altérer que les colonnes réellement manquantes.
    - PostgreSQL (Render, production) : supporte ``ADD COLUMN IF NOT EXISTS``.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Colonnes à garantir sur la table `invoices` (déjà gérées par une ancienne passe).
# Conservées pour idempotence : elles ne seront jamais ajoutées si déjà présentes.
_INVOICE_COLUMNS = {
    "description": "VARCHAR(500)",
    "quantity": "INTEGER NOT NULL DEFAULT 1",
    "unit_price": "NUMERIC(12,2)",
}

# DDL de création de la table des lignes d'opération, défini par dialecte dans
# ``_ensure_invoice_items`` (SQLite AUTOINCREMENT vs PostgreSQL SERIAL).


def _dialect_name(engine: Engine) -> str:
    """Retourne le nom du dialecte (ex: 'sqlite', 'postgresql')."""
    name = getattr(engine.dialect, "name", "") or ""
    return str(name).lower()


def _table_exists(engine: Engine, table: str) -> bool:
    """Renvoie True si la table existe déjà en base."""
    return inspect(engine).has_table(table)


def _existing_columns(engine: Engine, table: str) -> set[str]:
    """Retourne l'ensemble des noms de colonnes déjà présents sur la table."""
    inspector = inspect(engine)
    return {col["name"] for col in inspector.get_columns(table)}


def _ensure_invoice_items(engine: Engine) -> None:
    """Crée la table ``invoice_items`` si absente (idempotent).

    En pratique, ``Base.metadata.create_all`` (exécuté systématiquement en amont,
    au démarrage) crée déjà cette table si elle n'existe pas. Ce garde-fou
    n'intervient donc que par précaution, au cas où cette création ne serait pas
    passée par là. On adapte le DDL au dialecte (SQLite vs PostgreSQL).
    """
    if _table_exists(engine, "invoice_items"):
        logger.info("Migration : la table 'invoice_items' existe déjà.")
        return

    dialect = _dialect_name(engine)
    if dialect == "postgresql":
        ddl = """
CREATE TABLE IF NOT EXISTS invoice_items (
    id          SERIAL PRIMARY KEY,
    invoice_id  INTEGER      NOT NULL,
    description VARCHAR(500) NOT NULL,
    quantity    INTEGER      NOT NULL DEFAULT 1,
    unit_price  NUMERIC(12,2) NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
)
"""
    else:  # sqlite / autres
        ddl = """
CREATE TABLE IF NOT EXISTS invoice_items (
    id          INTEGER      PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER      NOT NULL,
    description VARCHAR(500) NOT NULL,
    quantity    INTEGER      NOT NULL DEFAULT 1,
    unit_price  NUMERIC(12,2) NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
)
"""
    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info("Migration : table 'invoice_items' créée (dialecte %s).", dialect)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Migration : création de 'invoice_items' en échec (%s). Détail : %s",
            dialect,
            exc,
        )


def _sync_invoice_columns(engine: Engine) -> None:
    """Ajoute les colonnes éventuellement manquantes sur ``invoices``."""
    table = "invoices"
    if not _table_exists(engine, table):
        logger.info("Migration : aucune table '%s' à compléter (absente).", table)
        return

    existing = _existing_columns(engine, table)
    dialect = _dialect_name(engine)
    missing = [
        (name, ddl) for name, ddl in _INVOICE_COLUMNS.items() if name not in existing
    ]

    if not missing:
        logger.info("Migration : la table '%s' est déjà à jour.", table)
        return

    logger.info(
        "Migration : colonnes manquantes sur '%s' -> %s",
        table,
        ", ".join(name for name, _ in missing),
    )

    with engine.begin() as conn:
        for column_name, column_ddl in missing:
            if dialect == "sqlite":
                stmt = f'ALTER TABLE {table} ADD COLUMN {column_name} {column_ddl}'
            else:
                stmt = (
                    f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS '
                    f'{column_name} {column_ddl}'
                )
            try:
                conn.execute(text(stmt))
                logger.info("Migration : colonne '%s.%s' ajoutée.", table, column_name)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Migration : échec sur '%s.%s' (%s). Détail : %s",
                    table,
                    column_name,
                    dialect,
                    exc,
                )


def run_migrations(engine: Engine) -> None:
    """Exécute toutes les migrations idempotentes au démarrage.

    Args:
        engine: moteur SQLAlchemy déjà configuré (``app.database.engine``).
    """
    # 1. Garantit la présence de la table des lignes d'opération.
    _ensure_invoice_items(engine)
    # 2. Rattrape les colonnes éventuellement absentes sur `invoices`.
    _sync_invoice_columns(engine)
