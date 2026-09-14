#!/usr/bin/env bash
# proteger_fs.sh — Protección de FONDO a nivel de sistema de archivos (F-05/SOL-007).
# Deja la plantilla Excel y los estados originales de solo-lectura, de modo que ni un
# bypass de los hooks pueda sobrescribirlos sin un chmod deliberado y auditable.
# Idempotente. Correr una vez tras clonar/extraer, con FINANCE_ROOT en la raíz.
set -euo pipefail
ROOT="${FINANCE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

# Plantilla inmutable (solo lectura para todos).
if [ -d "$ROOT/excel/template" ]; then
  chmod -R a-w "$ROOT/excel/template" 2>/dev/null || true
fi
# Estados originales inmutables.
if [ -d "$ROOT/statements/raw" ]; then
  find "$ROOT/statements/raw" -type f -exec chmod a-w {} + 2>/dev/null || true
fi
echo "Protección FS aplicada: excel/template y statements/raw en solo-lectura."
echo "Nota: la base data/*.sqlite se escribe SOLO por la capa services (finance ...)."
