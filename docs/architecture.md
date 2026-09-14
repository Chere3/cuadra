# Arquitectura técnica

## Principio
Excel es capa de presentación; **SQLite es la fuente de verdad**. Los movimientos se
guardan primero por **transacción atómica** en `data/finance.sqlite`; después se genera
la **exportación** que Excel consume por Power Query. Nada depende de texto en la
conversación de Claude.

## Capas
| Capa | Ubicación | Rol |
|---|---|---|
| A. Originales | `statements/raw/` | Documentos inmutables (SHA-256) |
| B. Staging | `staging/<run_id>/` | Ejecución efímera y auditable |
| C. Canónico | `data/finance.sqlite` | Verdad (centavos, atómico) |
| D. Export | `data/exports/transacciones_excel.csv` | Contrato para Excel |
| E. Excel | `excel/template` → `generated`/`backups` | Presentación |
| F. Registro | `audit_events`, `imports`, `reconciliation_checks`, `reports/` | Trazabilidad |

## Módulos (`src/finance/`)
- `money.py` — centavos enteros; parser de importes; sin float binario.
- `ids.py` — `transaction_id` estable (independiente de categoría), `fingerprint`, hashes.
- `db.py` — esquema (14 entidades) + migraciones versionadas.
- `config.py` — carga de `config/*.yml`.
- `extractors/` — CSV, XLSX, PDF texto, OCR/imagen (contrato común) + `textparse` heurístico.
- `normalize.py` — fechas ISO, importes, comercio; dudosos marcados (nunca inventados).
- `categorize.py` — reglas → histórico → IA(off) → "Por revisar".
- `reconcile.py` — conciliación determinista (OK/WARNING/BLOCKED/REVIEW_REQUIRED).
- `dedup.py` — duplicados (huella+ref+solape) y transferencias entre cuentas propias.
- `statemachine.py` — estados de importación (sin saltos).
- `services.py` — **capa de servicios única** (ingest/commit/rollback/export/status/doctor/audit_month).
- `cli.py` — CLI que delega en services (misma capa que el MCP).

## Modelo canónico (14 entidades)
institutions, accounts, statements, imports, transactions, transaction_sources,
transfer_links, categories, categorization_rules, corrections, reconciliation_checks,
month_close, schema_migrations, audit_events.

## Máquina de estados
`DISCOVERED→HASHED→EXTRACTED→NORMALIZED→VALIDATED→(REVIEW_REQUIRED|READY_TO_COMMIT)→
COMMITTED→EXPORTED→EXCEL_VERIFIED` (+ FAILED / ROLLED_BACK). Cada run: `staging/<run_id>/`
con manifest, extraction, normalized_transactions, reconciliation, exceptions, preview, log.

## Idempotencia y reversibilidad
- Idempotencia: `statement_id` = hash(cuenta+SHA-256 del archivo); `transaction_id`
  estable; commit con `INSERT OR IGNORE` → reimportar no duplica.
- Reversibilidad: `finance rollback <import_id>` elimina transacciones/fuentes/vínculos/
  checks de esa importación (atómico) y regenera el export.
