# Runbook mensual

Objetivo: importar los estados del mes y cerrarlo, de forma auditable y reversible.

## 1. Preparación
- Los PDFs se descargan a `~/Downloads/` (tras las 21:00 una automatización los mueve a
  `~/Downloads/Documentos/`). Identifica banco y producto **por contenido**, no por nombre, y
  cópialos a una carpeta de trabajo (o a `statements/inbox/`).
- Verifica que cada cuenta activa (config/accounts.yml) tenga su estado, y que ese **periodo no
  esté ya en la DB** (un mismo estado re-descargado cambia de SHA y se volvería a incorporar).

## 2. Vista previa (el comportamiento por defecto)
```
./scripts/finance ingest statements/inbox --period 2026-06
```
**`ingest` no escribe nada mientras no le pases `--commit`.** El dry-run es el modo por
defecto, no una bandera que haya que pedir. `--dry-run` existe y se acepta, pero es
redundante: el interruptor real es `--commit`.

Lee la vista previa: `staging/<run_id>/preview.md`. Revisa por cuenta:
- Estado (READY_TO_COMMIT / REVIEW_REQUIRED) y motivo.
- Conciliación (OK/WARNING/BLOCKED) y diferencia.
- Movimientos dudosos y categorías "Por revisar".

## 3. Correcciones (opcional, solo categoría/etiqueta)
Crea `correcciones.json` (schema `schemas/correction.schema.json`) y:
```
./scripts/finance correct <run_id> correcciones.json
```
Las fechas/importes NO se corrigen aquí: si son dudosos, corrige el documento origen o
marca la excepción.

## 4. Incorporar
No hay auto-commit. Incorporar es siempre un acto explícito, en cualquiera de las dos formas:
```
./scripts/finance ingest statements/inbox --period 2026-06 --commit
```
o, si hubo warnings/review y prefieres revisar antes de decidir:
```
./scripts/finance commit <run_id>
```

## 5. Subir a tu dashboard (opcional)
Desde la DB, nunca desde el PDF. `finance refresh-excel` deja un CSV en `data/exports/`;
lo que sigue es un ejemplo con un dashboard que expone `create_transaction`/`create_balance`.
1. `create_transaction` por movimiento con `category_id` (pocos) o CSV `Date,Description,Amount`
   en DD/MM/YYYY con BOM + importador (cientos). Signo canónico: compra en tarjeta = negativo.
2. `create_balance` con el saldo de corte a la fecha de cierre (tarjetas/préstamos en negativo).
3. `search_transactions` por cuenta y periodo: mismo conteo y misma suma que la DB.
4. Recategoriza contrapartes si aplica y aplica/crea reglas de automatización.

## 5-bis. (Secundario) Meter los movimientos al libro de Excel
```
./scripts/finance refresh-excel        # -> data/exports/transacciones_excel.csv
cd ../Herramientas
python3 construir_libro.py --datos ../ingesta_estados_finanzas/data/exports/transacciones_excel.csv --semilla ../semilla.json
```
El libro no lee el CSV solo (no hay Power Query). `semilla.json` vive en la raíz del proyecto;
las dos banderas van en una sola pasada; `--datos` implica `--recalc` y valida hash/totales
contra `_contract.json`.

## 6. Auditar y archivar
```
./scripts/finance audit-month 2026-06
```
Mueve los originales de `inbox/` a `statements/raw/` (no se borran).

## 7. Deshacer (si algo salió mal)
```
./scripts/finance rollback <import_id>     # deshace por completo esa importación
```
Diagnóstico: `./scripts/finance doctor` y `./scripts/finance status`.

## Cierre del mes
Solo se marca cerrado si: se recibieron los estados esperados (o excepción documentada),
todas las conciliaciones requeridas están OK, los "Por revisar" no afectan importes/fechas,
y tu dashboard (si usas uno) cuadra con la DB (conteo, suma y saldo de corte por cuenta).
