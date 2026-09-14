# Contrato del libro Excel

## Plantilla (inmutable)
- Archivo: `excel/template/Mega_Sistema_Finanzas_Personales.xlsx`
- **SHA-256:** `377d5f64e391f45ed7f536323342101eebe7712dff6c406282e388e3f8f74f68`
- Componentes (verificado): 22 hojas · 4 tablas · 89 rangos con nombre · 6 gráficos ·
  51 validaciones · 33 reglas de formato condicional · **sin macros, sin Power Query
  previa, sin pivots, sin conexiones, sin enlaces externos**.
- Tabla de movimientos: **`tMovimientos`** en la hoja `MOVIMIENTOS`.
  - Columnas de ENTRADA: Fecha, Descripción, Cuenta, Tipo, Categoría, Subcategoría,
    Importe, Ingreso/Gasto, Recurrente, Método de pago, Etiqueta, Conciliado, Notas.
  - Columnas CALCULADAS (fórmulas a preservar): ID, Mes, Año, Periodo, ImporteSigno.
- Dependencias: todo el dashboard/presupuestos/proyección leen `tMovimientos` por rangos
  con nombre de columna completa (`Mov_Periodo`, `Mov_Flujo`, `Mov_Importe`, `Mov_Cuenta`,
  `Mov_Cat`, `Mov_Signo`, …) vía `SUMIFS`. **No** cambiar esas columnas.
- Hoja `MONTECARLO`: ~44,000 fórmulas **volátiles** → verificar por estructura, no valores.

## Interfaz canónica → Excel (contrato de exportación)
- Archivo: `data/exports/transacciones_excel.csv` (regenerado en cada commit/rollback).
- Versión de esquema: **1.1.0** (`data/exports/_contract.json`). Desde 1.1.0 (SOL-008) el
  contrato incluye `totals{n_rows,ingresos_minor,gastos_minor}` y `csv_sha256`: el export
  verifica la capa DB→CSV releyendo el archivo escrito, y el puente verifica hash y totales
  del MISMO contrato antes de inyectar (CSV→XLSX). Las columnas no cambian respecto a 1.0.0.
- Columnas (orden EXACTO, no cambiar sin subir versión):
  `transaction_id, fecha, cuenta, institucion, descripcion, categoria, tipo, importe,
   moneda, ingreso_gasto, es_transferencia, estado_revision, bank_ref, fingerprint`
- `importe` es magnitud positiva; `ingreso_gasto` ∈ {Ingreso, Gasto}; `es_transferencia`
  ∈ {0,1} (para excluir transferencias del gasto/ingreso en Excel).

## Puente PROBADO: builder-inyección (ruta verificable, F-15/SOL-001)
La ruta **canónica y verificada** de conexión NO es escribir dentro del `.xlsx` existente,
sino **regenerar el libro inyectando el CSV** durante la construcción:

```
finance ingest statements/inbox --commit     # o dry-run + finance commit <run_id>
finance refresh-excel                         # regenera data/exports/transacciones_excel.csv
cd Herramientas && python3 construir_libro.py --datos ../ingesta_estados_finanzas/data/exports/transacciones_excel.csv --recalc
```

`construir_libro.py --datos <csv>` mapea el contrato a las columnas de ENTRADA de
`MOVIMIENTOS` (las calculadas se extienden solas), añade un **marcador de frescura**
(`Mov_Frescura`) con origen/versión/conteo/fecha, y `--recalc` deja **valores cacheados**.
La trazabilidad fixture→SQLite→CSV→xlsx→KPI se verifica en `Herramientas/test_puente.py`.
Esta ruta es 100% automatizable/verificable con LibreOffice (no requiere pasos manuales en
Excel) y es la que demuestra el objetivo end-to-end.

## Conexión por Power Query (opcional, refresco en vivo)
Si además prefieres refrescar sin reconstruir, el sistema **NUNCA escribe dentro del
`.xlsx`**; configuración de una sola vez en Excel:
1. Datos → Obtener datos → Desde texto/CSV → seleccionar
   `data/exports/transacciones_excel.csv`.
2. Cargar como **tabla** en una hoja nueva (p. ej. `mov_import`) o reemplazar el origen
   de `MOVIMIENTOS` según prefieras.
3. Si alimentas `tMovimientos`: mapea las columnas del CSV a las de ENTRADA; las
   CALCULADAS se extienden solas.
4. Uso mensual: **Datos → Actualizar todo** tras `finance refresh-excel`.

> Alternativa (si algún día se exige escribir el `.xlsx`): adaptador probado que escribe
> **solo el cuerpo** de `tMovimientos` sobre una **copia versionada**, preservando
> fórmulas/tablas/gráficos/validaciones y verificando antes de promover (regla 30).

## Pruebas antes de aceptar una actualización del libro (corte real, en la Mac)
Abrir con motor real → recalcular → escanear errores de fórmula → comparar antes/después
(hojas, tablas, rangos, fórmulas, conexiones, gráficos, validaciones, formato condicional)
→ revisar visualmente. Guardar sin error **no** es prueba suficiente.
