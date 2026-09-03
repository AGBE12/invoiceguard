"""Migrations d'initialisation légères pour InvoiceGuard (adaptées au MVP).

Le projet ne dispose volontairement pas d'Alembic : pour un MVP, on s'appuie
sur ``Base.metadata.create_all`` (qui crée les tables absentes) complété par
une petite migration « glissante » qui ajoute les colonnes nouvelles aux
tables déjà existantes.

La table ``invoices`` a récemment gagné les colonnes ``description``,
``quantity`` et ``unit_price``. ``create_all`` ne modifiant jamais une table
déjà présente, on injecte manuellement ces colonnes via ``ALTER TABLE``.

Compatibilité :
    - SQLite (développement local) : ne supporte pas ``ADD COLUMN IF NOT EXISTS``
      ni les contraintes ajoutées après coup. On se base sur l'inspection SQLAlchemy
      pour n'altérer que les colonnes réellement manquantes.
    - PostgreSQL (Render, production) : supporte ``ADD COLUMN IF NOT EXISTS``,
      on peut s'y fier, mais on garde la même logique d'inspection uniforme.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Colonnes à garantir sur la table `invoices`, avec une proposition d'ajout
# par dialecte (liste ordonnée : chaque élément est une clause d'ALTER COLUMN).
_INVOICE_COLUMNS = {
    "description": "VARCHAR(500)",
    "quantity": "INTEGER NOT NULL DEFAULT 1",
    "unit_price": "NUMERIC(12,2)",
}


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
    columns = {col["name"] for col in inspector.get_columns(table)}
    return columns


def run_migrations(engine: Engine) -> None:
    """Ajoute, de façon sûre et idempotente, les colonnes manquantes.

    Détecte le dialecte (SQLite vs PostgreSQL) et ajuste la syntaxe si
    nécessaire. N'altère que les colonnes réellement absentes, afin que la
    fonction puisse être réexécutée sans risque à chaque démarrage.

    Args:
        engine: moteur SQLAlchemy déjà configuré (``app.database.engine``).
    """
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
        "Migration : ajout de colonnes manquantes sur '%s' -> %s",
        table,
        ", ".join(name for name, _ in missing),
    )

    with engine.begin() as conn:  # transaction automatiquement commit/rollback
        for column_name, column_ddl in missing:
            # --- Construit la commande ALTER TABLE selon le dialecte ---
            if dialect == "sqlite":
                # SQLite ne supporte pas `ADD COLUMN IF NOT EXISTS` : on a déjà
                # filtré les colonnes existantes via l'inspection, donc simple ADD.
                stmt = f'ALTER TABLE {table} ADD COLUMN {column_name} {column_ddl}'
            else:
                # PostgreSQL (et la plupart des SGBD) acceptent IF NOT EXISTS,
                # garde-fou supplémentaire contre les doubles exécutions.
                stmt = (
                    f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS '
                    f'{column_name} {column_ddl}'
                )

            try:
                conn.execute(text(stmt))
                logger.info("Migration : colonne '%s.%s' ajoutée.", table, column_name)
            except Exception as exc:  # noqa: BLE001 - on ne bloque pas tout le set
                logger.error(
                    "Migration : échec sur '%s.%s' (%s). Détail : %s",
                    table,
                    column_name,
                    dialect,
                    exc,
                )
                # On ne relève pas d'erreur bloquante pour que le serveur puisse
                # quand même démarrer : une colonne manquante sera signalée plus
                # tard si un endpoint en dépend réellement.
