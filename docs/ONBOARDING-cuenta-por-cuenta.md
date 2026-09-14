# Onboarding cuenta por cuenta (arranque lento)

La idea: metes **una cuenta, un mes**, ves cómo se comporta, y solo cuando te convence
pasas a la siguiente. Todo es idempotente (reimportar no duplica) y reversible
(`finance rollback`). Por defecto el sistema está en **modo revisión**: nada entra a la
base sin tu `finance commit`.

## 0. Instalar una vez
```bash
git clone https://github.com/Chere3/cuadra.git && cd cuadra
python3 -m pip install -r requirements.txt        # pyyaml, openpyxl, pdfplumber, pytesseract, Pillow
# OCR (solo si usarás PDF escaneado/imágenes):  brew install tesseract tesseract-lang
chmod +x scripts/finance
FINANCE_ROOT=$(pwd) python3 tests/test_e2e.py       # debe decir "E2E COMPLETO SIN FALLOS"
```

## 1. Elige la primera cuenta (recomendación)
Empieza por una cuenta de **débito** (BBVA nómina o Nu débito). Razón: concilia limpio
(`saldo inicial + entradas − salidas = saldo final`). Deja las **tarjetas de crédito**
(Vexi/Nu/Ualá) para después: tienen MSI, intereses, pago mínimo y a veces convención de
signo invertida — mejor probarlas cuando ya confíes en el flujo.

## 2. Configura ESA cuenta (una vez por cuenta)
Edita `config/accounts.yml` en el bloque de tu cuenta:
- `mask`: los **últimos 4 dígitos** reales (solo 4, por privacidad).
- `match_hints`: palabras que aparezcan en ese estado y no en otros (p. ej. `["BBVA",
  "1234", "nómina"]`), para que el sistema identifique la cuenta sola.
- `type` y `currency` correctos.

## 3. Consigue el estado del mes (esto define qué tan bien concilia)
El sistema **no inventa** saldos. Para conciliar por saldo necesita saldo inicial y final:
- **Ideal:** el **estado de cuenta oficial** del mes (PDF), que trae saldo inicial/final
  y totales. → conciliación completa (OK/BLOCKED).
- **CSV de movimientos** (export del banco, sin saldos): también importa, pero la
  conciliación queda **REVIEW_REQUIRED** (no puede cuadrar por saldo). Para que cuadre,
  antepón unas líneas de metadatos al CSV:
  ```
  # account_id: bbva_debito
  # currency: MXN
  # period_start: 2026-06-01
  # period_end: 2026-06-30
  # opening_balance: 10000.00
  # closing_balance: 31600.00
  fecha,descripcion,monto,ref
  ...
  ```
  (Encabezados flexibles: fecha/monto/descripcion/saldo/ref; monto negativo = salida.)

Coloca el archivo en `statements/inbox/`.

## 4. Corre en seco y REVISA (nada se guarda todavía)
```bash
FINANCE_ROOT=$(pwd) ./scripts/finance ingest statements/inbox --period 2026-06 --dry-run
```
Abre `staging/<run_id>/preview.md` y revisa:
- **# de movimientos** extraídos vs los que esperas.
- **Conciliación**: `OK` (cuadra), `WARNING` (cuadra el saldo pero difiere el conteo/totales),
  `REVIEW_REQUIRED` (faltan saldos), `BLOCKED` (la diferencia supera 1 centavo).
- **Categorías**: las que quedaron "Por revisar".
- **Dudosos**: fechas/importes que no pudo leer (bloquean el commit hasta resolverlos).
- `exceptions.csv`: el detalle de cada excepción con su localizador.

## 5. Ajusta categorías (opcional) e incorpora
Si quieres corregir categorías (nunca fechas/importes), crea un `correcciones.json`
(`schemas/correction.schema.json`) y:
```bash
./scripts/finance correct <run_id> correcciones.json
./scripts/finance commit  <run_id>       # incorpora atómicamente + regenera el CSV para Excel
```

## 6. Refresca Excel y audita
```bash
./scripts/finance refresh-excel                 # actualiza data/exports/transacciones_excel.csv
./scripts/finance audit-month 2026-06           # cobertura y conciliación del mes
```
En Excel: **Datos → Actualizar todo** (una vez conectado Power Query al CSV; ver
`docs/workbook-contract.md`).

## 7. Siguiente cuenta / siguiente mes
Repite 2–6 con la siguiente cuenta. La transferencia entre dos cuentas propias se
vincula sola **cuando ya importaste ambos lados** (mismo importe, signo opuesto, ±3 días).

## Cuando ya confíes: activa el auto-commit
En `config/import_policy.yml` pon `auto_commit_if_ok: true`. Entonces, si un mes concilia
OK y sin dudosos, se incorpora solo al correr sin `--dry-run`.

## Si un banco no extrae bien en PDF
El parser de PDF es genérico. Si un banco (Nu, Vexi, etc.) no sale limpio, mándame **un
estado de ejemplo (o su texto)** y te armo una **plantilla específica de ese banco** en
`extractors/` — es la forma correcta de subir la precisión por banco.

## Deshacer
Cualquier importación: `./scripts/finance rollback <import_id>` (lo ves en `finance status`
o en el audit). Deja la base como antes.
