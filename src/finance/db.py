# -*- coding: utf-8 -*-
"""
db.py — Fuente canónica en SQLite. Modelo normalizado, dinero en centavos enteros.

- foreign_keys ON, journal_mode WAL.
- Migraciones versionadas en schema_migrations (idempotentes).
- Toda escritura de commit ocurre dentro de UNA transacción atómica (services.py).
"""
from __future__ import annotations
import os
import shutil
import sqlite3
import hashlib

SCHEMA_VERSION = 3

# --- DDL v1 (migración inicial) ---
MIGRATION_1 = r"""
CREATE TABLE institutions (
  institution_id TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  country        TEXT DEFAULT 'MX',
  created_at     TEXT NOT NULL
);

CREATE TABLE accounts (
  account_id     TEXT PRIMARY KEY,
  institution_id TEXT NOT NULL REFERENCES institutions(institution_id),
  name           TEXT NOT NULL,
  type           TEXT NOT NULL,          -- debito, credito, ahorro, credito_personal, inversion, efectivo
  currency       TEXT NOT NULL,          -- ISO 4217
  mask           TEXT,                   -- SOLO últimos 4 (regla 50)
  external_ref   TEXT,
  active         INTEGER NOT NULL DEFAULT 1,
  sign_convention TEXT NOT NULL DEFAULT 'normal', -- normal | inverted (bancos con débito/crédito opuestos)
  created_at     TEXT NOT NULL
);

CREATE TABLE statements (
  statement_id        TEXT PRIMARY KEY,
  account_id          TEXT NOT NULL REFERENCES accounts(account_id),
  file_sha256         TEXT NOT NULL,
  original_name       TEXT NOT NULL,
  currency            TEXT NOT NULL CHECK(currency GLOB '[A-Z][A-Z][A-Z]'),   -- ISO 4217 (SOL-003)
  period_start        TEXT,
  period_end          TEXT,
  opening_balance_minor INTEGER,
  closing_balance_minor INTEGER,
  declared_totals_json  TEXT,            -- {"credits_minor":..,"debits_minor":..,"n":..}
  extraction_method   TEXT,             -- pdf_text | ocr | csv | xlsx | image
  extractor_version   TEXT,
  ingested_at         TEXT NOT NULL,
  status              TEXT NOT NULL,     -- estado de máquina de importación
  supersedes_statement_id  TEXT REFERENCES statements(statement_id),
  superseded_by_statement_id TEXT REFERENCES statements(statement_id),
  UNIQUE(account_id, file_sha256)
);

CREATE TABLE imports (
  import_id   TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL,
  account_id  TEXT REFERENCES accounts(account_id),
  statement_id TEXT REFERENCES statements(statement_id),
  period      TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  state       TEXT NOT NULL,
  dry_run     INTEGER NOT NULL DEFAULT 1,
  committed   INTEGER NOT NULL DEFAULT 0,
  rolled_back INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT
);

CREATE TABLE transactions (
  transaction_id   TEXT PRIMARY KEY,
  account_id       TEXT NOT NULL REFERENCES accounts(account_id),
  statement_id     TEXT NOT NULL REFERENCES statements(statement_id),
  import_id        TEXT NOT NULL REFERENCES imports(import_id),
  date_op          TEXT NOT NULL CHECK(date_op GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- ISO 8601 (SOL-003)
  date_post        TEXT,                 -- fecha de procesamiento/cargo
  date_value       TEXT,                 -- fecha valor
  description_raw  TEXT NOT NULL,
  description_norm TEXT,
  merchant_norm    TEXT,
  amount_minor     INTEGER NOT NULL,     -- CON SIGNO: negativo=salida, positivo=entrada
  currency         TEXT NOT NULL CHECK(currency GLOB '[A-Z][A-Z][A-Z]'),   -- ISO 4217 (SOL-003)
  sign             INTEGER NOT NULL CHECK(sign IN (-1, 1)),                 -- SOL-003
  type             TEXT,                 -- gasto, ingreso, pago_deuda, transferencia, comision, interes, ...
  balance_after_minor INTEGER,
  category         TEXT,
  category_source  TEXT,                 -- rule | history | ai | manual | none
  review_status    TEXT NOT NULL DEFAULT 'ok', -- ok | por_revisar
  locator          TEXT,                 -- pagina/tabla/fila/bloque de origen
  bank_ref         TEXT,                 -- folio/autorización de la institución, si existe
  fingerprint      TEXT NOT NULL,
  fx_json          TEXT,                 -- {"orig_minor","orig_ccy","rate","rate_source","rate_date","conv_minor"}
  dup_of           TEXT REFERENCES transactions(transaction_id),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX idx_txn_account ON transactions(account_id);
CREATE INDEX idx_txn_statement ON transactions(statement_id);
CREATE INDEX idx_txn_fp ON transactions(fingerprint);
CREATE INDEX idx_txn_date ON transactions(date_op);

CREATE TABLE transaction_sources (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
  statement_id   TEXT NOT NULL REFERENCES statements(statement_id),
  locator        TEXT,
  raw_text       TEXT,                   -- texto ORIGINAL exacto de origen
  page           INTEGER,
  table_idx      INTEGER,
  row_idx        INTEGER
);

CREATE TABLE transfer_links (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  from_transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
  to_transaction_id   TEXT NOT NULL REFERENCES transactions(transaction_id),
  kind                TEXT NOT NULL,     -- transfer | card_payment
  confidence          REAL NOT NULL,
  created_at          TEXT NOT NULL,
  UNIQUE(from_transaction_id, to_transaction_id)
);

CREATE TABLE categories (
  category_id TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  kind        TEXT,                       -- gasto | ingreso | transferencia | otro
  active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE categorization_rules (
  rule_id     TEXT PRIMARY KEY,
  scope       TEXT NOT NULL,             -- merchant | account | currency | description | global
  matcher_json TEXT NOT NULL,           -- {"field":"merchant_norm","op":"equals|contains|regex","value":..}
  category    TEXT NOT NULL,
  priority    INTEGER NOT NULL DEFAULT 100,
  active      INTEGER NOT NULL DEFAULT 1,
  valid_from  TEXT,
  valid_to    TEXT,
  source      TEXT,                       -- seed | user_confirmed
  created_at  TEXT NOT NULL
);

CREATE TABLE corrections (
  correction_id TEXT PRIMARY KEY,
  transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
  field         TEXT NOT NULL,
  old_value     TEXT,
  new_value     TEXT,
  reason        TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE reconciliation_checks (
  check_id       TEXT PRIMARY KEY,
  statement_id   TEXT NOT NULL REFERENCES statements(statement_id),
  import_id      TEXT REFERENCES imports(import_id),
  opening_minor  INTEGER,
  credits_minor  INTEGER,
  debits_minor   INTEGER,
  computed_closing_minor INTEGER,
  declared_closing_minor INTEGER,
  diff_minor     INTEGER,
  tolerance_minor INTEGER,
  n_transactions INTEGER,
  declared_count INTEGER,
  coverage_ok    INTEGER,
  result         TEXT NOT NULL,          -- OK | WARNING | BLOCKED | REVIEW_REQUIRED
  details_json   TEXT,
  created_at     TEXT NOT NULL
);

CREATE TABLE month_close (
  account_id  TEXT NOT NULL REFERENCES accounts(account_id),
  month       TEXT NOT NULL,             -- YYYY-MM
  expected    INTEGER NOT NULL DEFAULT 1,
  received    INTEGER NOT NULL DEFAULT 0,
  period_covered TEXT,
  missing_days INTEGER,
  overlap_days INTEGER,
  imported_at TEXT,
  reconciliation TEXT,
  status      TEXT NOT NULL DEFAULT 'open',  -- open | closed | exception
  exception_reason TEXT,
  PRIMARY KEY (account_id, month)
);

CREATE TABLE audit_events (
  event_id  TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  actor     TEXT NOT NULL,               -- cli | mcp | migration
  action    TEXT NOT NULL,
  entity    TEXT,
  entity_id TEXT,
  run_id    TEXT,
  details_json TEXT
);

CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL,
  checksum   TEXT NOT NULL
);
"""

# --- Migración v2 (SOL-R2-002): reconstruye statements y transactions con los CHECK de
# dominio, de modo que una BD ya en v1 (creada sin constraints) también los adquiera.
# Idempotente: en una BD nueva (v1 ya con CHECK) sólo re-crea las tablas vacías.
MIGRATION_2 = r"""
CREATE TABLE statements_v2 (
  statement_id        TEXT PRIMARY KEY,
  account_id          TEXT NOT NULL REFERENCES accounts(account_id),
  file_sha256         TEXT NOT NULL,
  original_name       TEXT NOT NULL,
  currency            TEXT NOT NULL CHECK(currency GLOB '[A-Z][A-Z][A-Z]'),
  period_start        TEXT,
  period_end          TEXT,
  opening_balance_minor INTEGER,
  closing_balance_minor INTEGER,
  declared_totals_json  TEXT,
  extraction_method   TEXT,
  extractor_version   TEXT,
  ingested_at         TEXT NOT NULL,
  status              TEXT NOT NULL,
  supersedes_statement_id  TEXT REFERENCES statements(statement_id),
  superseded_by_statement_id TEXT REFERENCES statements(statement_id),
  UNIQUE(account_id, file_sha256)
);
INSERT INTO statements_v2 SELECT statement_id,account_id,file_sha256,original_name,currency,
  period_start,period_end,opening_balance_minor,closing_balance_minor,declared_totals_json,
  extraction_method,extractor_version,ingested_at,status,supersedes_statement_id,
  superseded_by_statement_id FROM statements;
DROP TABLE statements;
ALTER TABLE statements_v2 RENAME TO statements;

CREATE TABLE transactions_v2 (
  transaction_id   TEXT PRIMARY KEY,
  account_id       TEXT NOT NULL REFERENCES accounts(account_id),
  statement_id     TEXT NOT NULL REFERENCES statements(statement_id),
  import_id        TEXT NOT NULL REFERENCES imports(import_id),
  date_op          TEXT NOT NULL CHECK(date_op GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  date_post        TEXT,
  date_value       TEXT,
  description_raw  TEXT NOT NULL,
  description_norm TEXT,
  merchant_norm    TEXT,
  amount_minor     INTEGER NOT NULL,
  currency         TEXT NOT NULL CHECK(currency GLOB '[A-Z][A-Z][A-Z]'),
  sign             INTEGER NOT NULL CHECK(sign IN (-1, 1)),
  type             TEXT,
  balance_after_minor INTEGER,
  category         TEXT,
  category_source  TEXT,
  review_status    TEXT NOT NULL DEFAULT 'ok',
  locator          TEXT,
  bank_ref         TEXT,
  fingerprint      TEXT NOT NULL,
  fx_json          TEXT,
  dup_of           TEXT REFERENCES transactions(transaction_id),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
INSERT INTO transactions_v2 SELECT transaction_id,account_id,statement_id,import_id,date_op,
  date_post,date_value,description_raw,description_norm,merchant_norm,amount_minor,currency,sign,
  type,balance_after_minor,category,category_source,review_status,locator,bank_ref,fingerprint,
  fx_json,dup_of,created_at,updated_at FROM transactions;
DROP TABLE transactions;
ALTER TABLE transactions_v2 RENAME TO transactions;
CREATE INDEX idx_txn_account ON transactions(account_id);
CREATE INDEX idx_txn_statement ON transactions(statement_id);
CREATE INDEX idx_txn_fp ON transactions(fingerprint);
CREATE INDEX idx_txn_date ON transactions(date_op);
"""

# --- DDL v3: registro de CUARENTENA (SOL-R2-006) ---
# Estados/movimientos que NO entraron al libro (bloqueados: dudoso, mes cerrado, revisión). El
# subconteo debe ser VISIBLE: export_excel propaga su conteo/monto al contrato y al dashboard, y
# close_month se bloquea mientras exista cuarentena para el mes.
MIGRATION_3 = r"""
CREATE TABLE quarantine (
  statement_id TEXT NOT NULL,
  account_id   TEXT,
  month        TEXT,                         -- YYYY-MM del date_op de los movimientos (R8-A-003)
  reason       TEXT,
  n_txns       INTEGER NOT NULL DEFAULT 0,
  amount_minor INTEGER NOT NULL DEFAULT 0,   -- suma de |importe| retenido en ese mes
  run_id       TEXT,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (statement_id, month)          -- un estado puede abarcar varios meses contables
);
CREATE INDEX idx_quarantine_month ON quarantine(account_id, month);
"""

MIGRATIONS = {1: MIGRATION_1, 2: MIGRATION_2, 3: MIGRATION_3}


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")   # F-11/SOL-012: esperar el lock, no fallar de inmediato
    ensure_triggers(con)
    return con


def ensure_triggers(con) -> None:
    """Backstop de integridad a nivel de MOTOR (SOL-R2-002/SOL-003): rechaza en el propio INSERT/
    UPDATE cualquier date_op/date_post que no sea una fecha de calendario REAL. Cubre TODO camino de
    escritura —incluido el acceso directo a la BD que evade la validación Python—, no solo el
    pipeline sancionado. El CHECK GLOB solo verifica el FORMATO (`2026-99-99` lo pasa); y `date()`
    de SQLite NORMALIZA el desbordamiento de día (`2026-02-30`->`2026-03-02`) en vez de rechazarlo,
    por eso se exige igualdad EXACTA `date = date(date)` (además de no-NULL). Los triggers son objetos
    de esquema persistentes e idempotentes (`IF NOT EXISTS`); se crean si ya existe `transactions`."""
    has = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transactions'").fetchone()
    if not has:
        return
    for col in ("date_op", "date_post"):
        for ev in ("INSERT", "UPDATE"):
            con.execute(
                f"CREATE TRIGGER IF NOT EXISTS trg_txn_{col}_{ev.lower()} "
                f"BEFORE {ev} ON transactions FOR EACH ROW "
                f"WHEN NEW.{col} IS NOT NULL "
                f"AND (date(NEW.{col}) IS NULL OR NEW.{col} <> date(NEW.{col})) "
                f"BEGIN SELECT RAISE(ABORT, '{col} no es una fecha de calendario válida'); END")


def current_version(con) -> int:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not row:
        return 0
    r = con.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return r["v"] or 0


def _split_statements(sql: str):
    """Divide un script DDL en sentencias individuales (sin ';' dentro de literales aquí)."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def migrate(con, now_iso: str, db_file: str | None = None) -> int:
    """Aplica migraciones pendientes de forma ATÓMICA e idempotente (SOL-011).

    Cada migración corre dentro de UNA transacción explícita; si cualquier sentencia
    falla, se hace ROLLBACK completo y NO se deja esquema parcial ni se registra la
    versión. Antes de aplicar sobre una base ya existente se respalda el archivo.
    """
    v = current_version(con)
    for target in sorted(MIGRATIONS):
        if target > v:
            if db_file and v > 0 and os.path.exists(db_file):
                _backup(db_file, v)
            sql = MIGRATIONS[target]
            # Las migraciones que reconstruyen tablas (DROP/RENAME de padres con FK) requieren
            # FK OFF durante el rebuild; se verifica la integridad referencial al final.
            con.execute("PRAGMA foreign_keys=OFF")
            try:
                con.execute("BEGIN")
                for stmt in _split_statements(sql):
                    con.execute(stmt)
                con.execute(
                    "INSERT INTO schema_migrations(version, applied_at, checksum) VALUES (?,?,?)",
                    (target, now_iso, hashlib.sha256(sql.encode()).hexdigest()[:16]),
                )
                bad = con.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise RuntimeError(f"foreign_key_check falló tras migración {target}: {bad[:3]}")
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                con.execute("PRAGMA foreign_keys=ON")
                raise
            con.execute("PRAGMA foreign_keys=ON")
    return current_version(con)


def _backup(db_file: str, from_version: int) -> str:
    bdir = os.path.join(os.path.dirname(db_file), "backups")
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, f"{os.path.basename(db_file)}.pre_v{from_version + 1}")
    shutil.copy2(db_file, dst)
    return dst
