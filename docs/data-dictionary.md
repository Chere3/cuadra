# Diccionario de datos (canónico)

Dinero en **centavos enteros** (`*_minor`), con signo (negativo=salida). Fechas ISO 8601.

- **institutions**(institution_id, name, country, created_at)
- **accounts**(account_id, institution_id, name, type[debito|credito|ahorro|credito_personal|inversion|efectivo], currency, mask[últimos 4], external_ref, active, sign_convention[normal|inverted], created_at)
- **statements**(statement_id, account_id, file_sha256, original_name, currency, period_start, period_end, opening_balance_minor, closing_balance_minor, declared_totals_json, extraction_method, extractor_version, ingested_at, status, supersedes_statement_id, superseded_by_statement_id) — UNIQUE(account_id,file_sha256)
- **imports**(import_id, run_id, account_id, statement_id, period, started_at, finished_at, state, dry_run, committed, rolled_back, summary_json)
- **transactions**(transaction_id[estable, NO depende de categoría], account_id, statement_id, import_id, date_op, date_post, date_value, description_raw, description_norm, merchant_norm, amount_minor[con signo], currency, sign, type, balance_after_minor, category, category_source, review_status[ok|por_revisar], locator, bank_ref, fingerprint, fx_json, dup_of, created_at, updated_at)
- **transaction_sources**(id, transaction_id, statement_id, locator, raw_text[original], page, table_idx, row_idx)
- **transfer_links**(id, from_transaction_id, to_transaction_id, kind[transfer|card_payment], confidence, created_at)
- **categories**(category_id, name, kind, active)
- **categorization_rules**(rule_id, scope, matcher_json, category, priority, active, valid_from, valid_to, source, created_at)
- **corrections**(correction_id, transaction_id, field, old_value, new_value, reason, created_at)
- **reconciliation_checks**(check_id, statement_id, import_id, opening_minor, credits_minor, debits_minor, computed_closing_minor, declared_closing_minor, diff_minor, tolerance_minor, n_transactions, declared_count, coverage_ok, result[OK|WARNING|BLOCKED|REVIEW_REQUIRED], details_json, created_at)
- **month_close**(account_id, month, expected, received, period_covered, missing_days, overlap_days, imported_at, reconciliation, status[open|closed|exception], exception_reason)
- **schema_migrations**(version, applied_at, checksum)
- **audit_events**(event_id, ts, actor, action, entity, entity_id, run_id, details_json)

## Contrato de exportación (Excel)
`data/exports/transacciones_excel.csv` v1.0.0 — columnas:
`transaction_id, fecha, cuenta, institucion, descripcion, categoria, tipo, importe, moneda,
ingreso_gasto, es_transferencia, estado_revision, bank_ref, fingerprint`.
