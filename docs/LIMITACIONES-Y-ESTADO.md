# Estado honesto y limitaciones

> **Actualización 2026-09-02.** El libro de Excel es una vía secundaria; la base SQLite es la
> fuente de verdad. Sin `tesseract` instalado no hay OCR de respaldo. Rondas 20–26 abajo.

## 🔒 Rondas 20–26 — VERIFICADO (operación real ago–sep 2026, suite 133 OK)

Todas salieron de estados reales; cada una con test que falla al revertir el arreglo.

| Ronda | Test | Qué cubre |
|---|---|---|
| 20 | `test_r20_bbva_detalle.py` | BBVA débito: `date_post`, saldo, detalle bajo el movimiento, año por periodo |
| 21 | `test_r21_bbva_tdc.py` | BBVA Tarjeta Crea: plantilla propia y saldos del resumen |
| 22 | `test_r22_uala_monto_previo.py` | Ualá: importe SOLO en la línea anterior a fecha+descripción; cierre = "Pago para no generar intereses" |
| 23 | `test_r23_nu_fecha_partida_desc.py` | Nu débito: movimiento en 3 renglones (día-mes / importe / año + resto) |
| 24 | `test_r24_nu_legacy_signo_explicito.py` | Nu TDC diseño 2025: abonos con `- $`, saldos a favor `- $X`, rótulo "Saldo total del periodo", desglose "0% 1/3" |
| 25 | `test_r25_vexi_pago_tardio.py` | Vexi: "Comisión por pago tardío" / "IVA pago tardío" son CARGO aunque digan "pago" |
| 26 | `test_r26_hsbc_ebcdic.py` | HSBC: texto EBCDIC en fuentes Type 3 → `(cid:NN)`/StandardEncoding → byte → cp037. Método `pdf_ebcdic_template:hsbc` admitido en `schemas/statement.schema.json`. Sustituye al OCR |

Lo que sigue **sin** resolver: DiDi (préstamos y tarjeta) no tiene plantilla; un PDF sin texto
nativo legible no se puede leer (sin tesseract); `vexi_tdc` arrastra 2 registros de estado
duplicados sin movimientos.


**Fecha:** 31 de julio de 2026. Este documento dice, sin adornos, qué está **construido y
verificado**, qué está **parcial** y qué **falta**. Actualizado tras remediar los hallazgos
de dos auditorías externas (`audits/fable5`, `audits/sol`) y tras las rondas 13–19.

## 🔒 Rondas 13–19 — VERIFICADO (cobertura multi-emisor)

No salen de una auditoría: salen de **meter estados reales de emisores que el sistema no había
visto** (HSBC, Ualá, Vexi) y los meses de 2026 de los que ya estaban. El patrón que las une es el
peligroso: *el parser daba por buena una lectura parcial y seguía sin levantar error*. El único
síntoma era una conciliación que no cuadraba — o, peor, que **sí** cuadraba con datos incompletos.

Verificado el 2026-07-31 sobre el árbol actual: **67/67 checks pasan.**

| Ronda | Test | Casos | Qué cubre |
|---|---|---:|---|
| 13 | `tests/test_r13_hsbc.py` | 11 | plantilla HSBC + respaldo OCR (PDF con fuentes Type 3) |
| 14 | `tests/test_r14_transferencias.py` | 17 | señales de transferencia entre cuentas propias (regla 26) |
| 15 | `tests/test_r15_periodo.py` | 7 | detección del periodo en sus distintos rotulados |
| 16 | `tests/test_r16_identificacion.py` | 3 | a qué cuenta pertenece un estado |
| 17 | `tests/test_r17_tolerancia_cuenta.py` | 5 | tolerancia de conciliación por cuenta (regla 25) |
| 18 | `tests/test_r18_declarados_y_tokens.py` | 10 | totales declarados por plantilla + señal de contraparte |
| 19 | `tests/test_r19_multicuenta.py` | 14 | lecturas parciales en HSBC, Nu y Ualá |

- **R13 — HSBC.** Día suelto en vez de fecha completa: mes y año salen del periodo, y sin periodo
  **no se inventa** la fecha (regla 00) — el renglón se conserva. PDF con fuentes Type 3 devolvían
  glifos sin mapa Unicode: había "texto" pero ni fechas ni importes, y el extractor devolvía **cero
  movimientos en silencio**; ahora se detecta y se cae a OCR. El signo sale del delta del saldo
  corriente y se cruza-valida contra el importe declarado — un dígito mal leído por OCR rompe la
  igualdad y queda `importe_dudoso`, que bloquea el commit.
- **R14 — transferencias.** Vincular excluye las dos patas del P&L, así que importe igual + signo
  opuesto + ventana **no basta**. Señales nuevas (`titular`, `pago_tarjeta`) y, sobre todo, los
  casos en que no deben dispararse: el titular se exige **completo como secuencia**, porque hay
  transferencias de familiares con el mismo apellido y vincularlas las borraría del P&L. Los tokens
  dejan de derivarse de `match_hints` (producían "por", "saldo").
- **R15 — periodo.** Asigna el estado a un mes, da el `year_hint` de las fechas DD/MMM y permite
  fechar el día suelto de HSBC. Sin periodo el estado entra correcto pero **fuera de la cobertura
  del mes**. Se cubren Ualá (mes + número de días) y Vexi (encabezado); si los días no cuadran con
  el mes, no se deduce.
- **R16 — identificación de cuenta.** La conciliación **no** atrapa este error: los saldos salen del
  mismo documento y cuadran igual. Con `sign_convention` distinta, todos los movimientos entran con
  el signo invertido. El desempate mira ahora el **cuerpo** del documento, no solo las líneas de
  movimiento; si aun así hay ambigüedad, **bloquea** en vez de elegir el primero.
- **R17 — tolerancia por cuenta.** La global sigue en 0. La excepción es por cuenta, exige
  justificación documentada (regla 25) y **no se derrama**: otra cuenta con el mismo residuo sigue
  bloqueando, y un residuo mayor bloquea también en la cuenta exceptuada.
- **R18 — controles que se saltaban en silencio.** BBVA no extraía los totales impresos, así que
  `declared_count`/`coverage_ok` llegaban en `None` y el control cruzado de la regla 25 se saltaba
  **sin avisar**. Y ninguna cuenta declaraba `counterparty_tokens`, de modo que Nu, Ualá y HSBC
  nunca podían cumplir la señal de la regla 26. Riesgo inverso fijado como regresión: un token de
  dos letras casa con `COMPRA NU SKIN` y el candado de grado único no lo cubre.
- **R19 — cuatro lecturas parciales.** Nu parte la fecha en dos renglones (4 movimientos perdidos
  por estado); congelar/descongelar de una Cajita no es movimiento y descuadraba; HSBC separa
  miles con espacio (`$ X XXX.XX`) y se perdía el millar sin avisar; y su tabla antepone el número
  de autorización en compras con tarjeta, que `[A-Z]{2,7}` tiraba. Además `month_close` pasa a
  indexarse por el **cierre** del periodo, no por su inicio.

## 🔒 Ronda 12-E — VERIFICADO (remediación de la auditoría adversarial de la ronda 12)

Dos auditores independientes (Codex GPT-5.6-Sol y Claude Opus, en paralelo y con alcances
distintos) revisaron la ronda 12. Convergieron en varios puntos, y **el defecto más grave estaba
en el arreglo de la propia ronda 12**. Todo cerrado con test que falla al revertir el arreglo.

- **R12-E-001 (CRITICAL) — `_page_text` destruía texto legítimo.** La des-superposición decidía por
  NÚMERO DE CAMBIOS DE FUENTE y conservaba la capa de la PRIMERA palabra. Ambas cosas fallaban: una
  tabla que alterna fuentes (fecha en negrita, descripción regular, importe en negrita, saldo
  regular) superaba el umbral y perdía descripción **y saldo corrido** sin que la conciliación lo
  notara —los importes sobrevivían—; y si la marca de agua empezaba más a la izquierda que el
  movimiento, se conservaba la marca y **se borraba el movimiento entero**. Ahora la superposición
  se detecta por **solapamiento geométrico** de las cajas (imposible en texto normal), se conserva
  la **fuente dominante de la página** y se avisa de cada palabra descartada.
- **R12-E-002 (CRITICAL) — el puente borraba cargos repetidos legítimos.** `inyectar_datos`
  deduplicaba por `fingerprint`, que dos cargos genuinos idénticos comparten por diseño (regla 26:
  "un cargo genuino repetido MUST NOT fusionarse"). Y el descarte ocurría **después** de validar el
  contrato, así que hash y totales cuadraban y la pérdida era invisible: la base y el CSV
  conservaban los dos, el libro recibía uno. La clave pasa a ser `transaction_id`.
- **R12-E-003 (HIGH) — dos signos invertidos se compensaban.** La identidad de saldo solo controla
  el NETO, así que una devolución y un reverso con el signo cruzado y el mismo importe conciliaban
  en `OK`. Los conceptos cuyo sentido no se deduce del rótulo (devolución, reverso, bonificación,
  nota de crédito) se marcan **dudosos y bloquean**; y toda línea excluida por rótulo deja warning.
- **R12-E-004 (HIGH) — transferencias descartadas entre runs.** El commit exigía que AMBOS extremos
  vinieran en ese mismo commit. Importar cuenta por cuenta —el flujo normal— deja cada lado en un
  run distinto: ningún vínculo se persistía y el libro contaba el mismo dinero como gasto real en
  una cuenta y como ingreso real en la otra. Ahora el extremo ya committeado se lee de
  `transactions`, con la misma re-validación server-side.
- **R12-E-005 (HIGH) — colisión de PK en `imports`.** `import_id` salía de (run, cuenta): dos
  estados de la misma cuenta en un run abortaban el commit ENTERO con un `IntegrityError` crudo.
  Ahora incluye el `statement_id`.
- **R12-E-006 (HIGH) — `validate_statement()` nunca se invocaba** y un periodo invertido
  (inicio > fin) llegaba a `READY_TO_COMMIT`. Se enchufa en la revalidación del commit, el esquema
  admite las plantillas por banco (`pdf_template:*`) y el periodo invertido bloquea.
- **MEDIUM/LOW:** `SUMMARY_EXTRAS` ya no aplica a estados de crédito ni duplica un concepto que el
  cuerpo ya lista · el regex del saldo inicial deja de ser greedy · el fallback legacy se restringe
  a Nu y avisa para otros emisores · el desempate de año solo reescribe dentro de ±45 días · las
  reglas de comercio usan límites de palabra · la validación del contrato suma en `Decimal`.
- **Calidad de las pruebas:** el mutation testing del auditor encontró que **3 de los 5 tests de la
  ronda 12 pasaban con el código bajo prueba mutilado**. `R12-19` contaba eventos de rollback en vez
  del de la corrección; `R12-11` se apoyaba en el JSON-Schema en lugar de la guarda que decía
  probar; `R12-05` afirmaba una garantía **que el código no implementaba**. Los tres reforzados, y
  el tercero además implementado.

Verificado: suite 27 → **33 tests**, rojo→verde en cada arreglo, cero regresiones sobre los estados
archivados, `test_puente` con recálculo real. Enero se **rehízo** con el código corregido: el
`csv_sha256` del export resultó **idéntico** al anterior, lo que confirma que los datos ya
committeados no estaban afectados.

## 🔒 Ronda 12 — VERIFICADO (primera operación real, enero 2026)

No vino de una auditoría: apareció al importar estados reales por primera vez. **Tres de los
cuatro defectos son del sistema, no de un banco concreto**, y ninguno se habría visto con los
fixtures sintéticos. Cada uno cerrado con test que falla al revertir el arreglo.

**Defectos generales (afectan a cualquier banco):**
- **R12-C-001 (ALTA) — movimientos invisibles por texto superpuesto.** Varios emisores imprimen el
  nombre del titular como marca de agua sobre la tabla. `extract_text()` aplana ambas capas
  entrelazándolas carácter a carácter, y la línea deja de casar con cualquier patrón: **el
  movimiento se pierde en silencio**. Ahora `pdf_ext._page_text` reconstruye por posición y, cuando
  las fuentes aparecen INTERCALADAS (≥3 cambios al recorrer la línea), conserva solo la capa con la
  que arranca. Si la página no trae superposición se devuelve `extract_text()` intacto, para no
  alterar a los demás.
- **R12-D-001 (ALTA) — importación con correcciones IRREVERSIBLE.** `rollback` limpiaba
  `transfer_links`, `transaction_sources` y las supersesiones antes de borrar los movimientos, pero
  **no `corrections`**, que también referencia `transactions`: la FK reventaba y la importación no
  se podía deshacer. Contradecía la invariante 00 (toda importación reversible). Es el mismo
  descuido que la ronda 4 cerró para `dup_of`, con otra tabla. La auditoría no se pierde: cada
  corrección dejó su `audit_events` al aplicarse.
- **R12-C-003 (MEDIA) — año equivocado en cargos con fecha de corte.** Las tarjetas cargan IVA y
  ajustes con la fecha de corte, un día DESPUÉS del último día del periodo. Al no caber en el rango,
  `_resolve_year_in_period` se quedaba con la fecha original: un `DD MMM` con periodo que cierra
  días antes aterrizaba en el año anterior, a doce meses del mes contable correcto. Ahora, si
  ninguna variación cae dentro, se elige la MÁS CERCANA al periodo (empate: la original).

**Plantillas por banco:**
- **R12-C-002 — tarjeta Nu, diseño anterior.** Nu convive con dos formatos. El anterior usa otros
  rótulos (`Saldo inicial/final del periodo`), periodo con guion en vez de `al`, y movimientos con
  fecha SIN año e importe SIN signo. Además repite las compras a meses en un desglose con tasa y
  parcialidad (`NN.NN% 3/3`) que duplicaría cada cargo, y lista el aumento de línea de crédito con
  importe aunque no genere deuda. El signo no se adivina: se deriva del rotulado (solo los pagos al
  emisor abonan) y **la identidad `deuda_ant + cargos − abonos = deuda_corte` lo verifica al
  centavo** — un solo signo mal y el estado se bloquea.
- **Nu débito — conceptos declarados solo en el resumen.** Nu abona los rendimientos de la cuenta
  (`Dinero generado este mes`) sin listarlos como movimiento: la suma se quedaba corta por ese
  importe y la conciliación bloqueaba para siempre. `bank_templates.SUMMARY_EXTRAS` los lee del
  documento (no los calcula) y los fecha al cierre del periodo; **sin periodo no se incorporan y se
  avisa**, en vez de inventarles una fecha y dar un cuadre falso. También se soporta el periodo con
  la fecha inicial abreviada.

**Contrato y modelo de datos:**
- **R12-B-001 — apartar dinero no es gasto.** Sin un tipo propio, una aportación (Cajita de Nu,
  traspaso a la casa de bolsa) caía en `gasto`: el libro la contaba como gasto real del mes y encima
  reemplazaba su categoría por "Otros gastos". Se añaden `aportacion_ahorro` y
  `aportacion_inversion` a `VALID_TYPES` y al esquema, y `puente_datos.TIPO_MAP` los traduce a los
  tipos que el libro ya excluye del gasto vía `GASTO_NO_AHORRO` y cuenta en Ahorro-Deuda del
  50/30/20. No se infieren del texto: los fija el usuario con `finance correct`.
- **Decimales del CSV-contrato.** `money.from_minor` dividía sin fijar la escala, así que el
  formato de la columna `importe` salía variable hacia el CSV que consume el Excel. El valor nunca
  fue incorrecto (el round-trip a centavos ya cerraba), el formato sí. Ahora cuantiza a la escala
  de la moneda —exacto, no redondea ningún importe— y las monedas sin decimales siguen sin
  recibirlos.
- **Identificación de cuentas del mismo emisor.** Las dos cuentas Nu compartían `match_hints`
  (ambas con el token `Nu`) y el desempate por frecuencia elegía mal: el estado de DÉBITO se
  identificaba como la TARJETA, se le aplicaba la plantilla de crédito y los saldos salían con signo
  invertido. Se calibraron hints exclusivos de cada documento, verificados contra los 7 estados
  disponibles (la configuración previa acertaba 5 de 7).

Verificado: suite de 27 → **32 tests** (`test_r12_nu_debito`, `test_r12_export_decimales`,
`test_r12_tipos_aportacion`, `test_r12_nu_tdc_legacy`, `test_r12_rollback_correcciones`), rojo→verde
comprobado revirtiendo cada arreglo, los 6 estados archivados extraen exactamente igual que antes,
`test_puente` con recálculo real de LibreOffice, y verificación end-to-end de que una aportación
sale del gasto y suma al 50/30/20 en el libro recalculado.

**Registro histórico de la ejecución de ronda 12:** enero 2026 importado y committeado — 46
movimientos (25 de Nu débito, 21 de Nu crédito), ambos estados conciliando al centavo, `doctor` en
verde. Es el conteo de *aquella* ejecución; el **estado operativo actual no fue verificado en esta
revisión**.

## 🔒 Ronda 11 — VERIFICADO (cierre de los pendientes del retest sol 2026-07-22, ronda 9)
Los 4 hallazgos que mantenían el CONDITIONALLY ACCEPTABLE, cerrados con TDD
(`tests/test_r11_pendientes.py`, 12 checks) y verificados contra los 5 estados REALES:
- **SOL-009 (HIGH, cerrado):** crédito revolvente por banco. Los saldos del periodo de una TDC no
  van por línea sino en el RESUMEN del emisor; `bank_templates.credit_summary` los extrae con
  plantilla CALIBRADA contra un estado real de cada banco (Nu: 'Adeudo del periodo anterior' →
  'Saldo cargos regulares'; Ualá: → 'Saldo deudor total'; Vexi: 'Saldo revolvente anterior' →
  'Saldo revolvente al corte', admite saldo a favor). La identidad deuda_anterior + cargos − abonos
  = deuda_al_corte concilia al centavo en los 3 PDFs reales de crédito (diff=0, READY_TO_COMMIT);
  los 2 de débito siguen OK. Fail-safe intacto: identidad rota (fila perdida) BLOQUEA; rótulo
  desconocido → REVIEW (nunca se inventa). Además: `_PERIODO` acepta fechas con espacios (Nu) y el
  regex genérico de saldos exige un dígito (capturaba una coma sola como saldo).
- **SOL-014 (HIGH, cerrado):** inmutabilidad y recuperación. `commit`/`rollback` crean ANTES un
  backup VERIFICADO de la base (backup API de SQLite + integrity_check + SHA-256 releído +
  manifiesto en `data/backups/`; si no verifica, la operación aborta). El original se ARCHIVA en
  `statements/raw/<sha256>.<ext>` (0444) verificando que no cambió desde el ingest; `doctor()`
  re-verifica hash de TODOS los archivados y del último backup en cualquier momento (protección
  integral fuera de ventanas de commit).
- **SOL-008 (MEDIUM, cerrado):** contrato **1.1.0** (aditivo): `totals{n_rows,ingresos_minor,
  gastos_minor}` + `csv_sha256`. El export reconcilia DB→CSV releyendo el archivo escrito (si no
  cuadra, falla y no publica); el puente (`Herramientas/puente_datos.py`) verifica hash y totales
  del MISMO contrato antes de inyectar al libro (CSV→XLSX). Una sola fuente de verdad por capa.
- **SOL-R2-005 (MEDIUM, cerrado):** enforcement técnico del canal no confiable. Las warnings de
  extracción (que embeben texto crudo del documento) se neutralizan EN EL ORIGEN
  (`neutralize_untrusted`: C0/ANSI fuera, una línea, backticks sustituidos); y `call_tool` (MCP)
  pasa TODO resultado por `sanitize_out` recursivo en el chokepoint de despacho — ningún tool
  presente o futuro puede devolver bytes de control ni romper el encapsulado (no es opt-in).
  El texto contable NO se redacta ni interpreta (eso sigue siendo consumer policy).
- **CI (debilidad señalada, cerrada):** `python3 -m unittest discover -s tests` ahora descubre y
  corre la suite completa (`tests/test_ci_suites.py` envuelve cada script con contrato exit 0);
  antes salía 5 con 0 tests (falsa cobertura). Verificado: 14 tests OK y exit ≠ 0 con suite rota.
- **R9-OBS-01 (LOW, cerrado):** `finance resolver-cuarentena <txn_id> --reason` (+ tool MCP
  `finance_resolve_quarantine` con confirm, paridad SOL-004): resuelve una cuarentena a nivel
  movimiento ya committeada sin rollback completo; auditada, bloquea en mes cerrado y re-exporta.

## 🔒 Ronda 10 — VERIFICADO (auditoría del ciclo + crédito)
Dos auditores (ingesta + Excel); 8 hallazgos, todos cerrados con TDD:
- **R10-A-001 (MEDIA-ALTA):** reingerir el MISMO PDF ya committeado lo ponía en cuarentena → doble
  conteo + `close_month` bloqueado permanente. Ahora la rama skipped verifica `_statement_committed`
  ANTES de cuarentenar (un duplicado es no-op idempotente).
- **R10-A-002 (MEDIA):** una cuenta `inverted` invertía importes pero no opening/closing → la TDC
  nunca conciliaba. Ahora también invierte opening/closing (la deuda pasa a saldo canónico negativo);
  una TDC con saldos de deuda limpios CONCILIA (verificado). Los reales aún no (su terminología de
  saldo de deuda no se extrae; revolvente pendiente).
- **R10-A-004 (BAJA-MED):** la inversión solo aplica a fuentes NATIVAS (plantilla PDF), no a un CSV
  ya canónico (evita la doble inversión).
- **R10-A-003 / R10-B-001 (MEDIA):** el filtro de INGRESO era asimétrico (solo excluía Transferencia)
  → un pago de TDC/retiro marcado Ingreso inflaba ingreso y tasa de ahorro. Nuevo `INGRESO_REAL`
  (espejo de `GASTO_NO_AHORRO` + Retiro de inversión + Ajuste) en DASHBOARD/PRESUP_MENSUAL/PRESUP_ANUAL/INGRESOS.
- **R10-A-005 (BAJA):** el hook `guard_paths` no detectaba `install`/`rsync`; añadidos a WRITE_TOKENS.
- **R10-B-002 (BAJA):** PATRIMONIO `Var. $` daba `#VALUE!` con un hueco entre fotos; la guarda ahora
  chequea la fila previa.
- **R10-A-006 (INFO):** sin backup antes de commit/rollback — pendiente (recuperación por reingesta
  del PDF original, que se preserva). Fail-safe; documentado.

Los auditores confirmaron RESISTEN: transferencias multi-cuenta con TDC, idempotencia de IDs, rollback
de movimientos en cuarentena, y el modelo de signo del libro (recalc real: ingreso/gasto/tasa exactos).

Verificado: `tests/test_r10_integridad.py` (3), `validar.py` 0 errores + recalc de R10-B-001/002;
regresión intacta (26/26, r4–r9, hooks/ocr/mcp/e2e).

## 🔒 Ronda 9c — VERIFICADO (auditoría adversarial de las plantillas)
Un auditor atacó el código de calibración; 3 hallazgos, todos cerrados con TDD:
- **R9-A-001/004 (ALTA) — cruce de año:** un estado dic→ene con fechas DD/MMM asignaba el año del
  periodo → '28/DIC' salía 2026 en vez de 2025. Se resuelve el año por el periodo (±1 año para caer
  en `[period_start, period_end]`) + flag `fecha_fuera_de_periodo` si aun así queda fuera.
- **R9-A-002 (MEDIA) — movimiento fantasma:** `_parse_saldo` usaba fecha NO anclada, así que una línea
  de resumen ('Rendimiento acumulado del 01/06/2026 …') con 2 importes se ingería como movimiento.
  Ahora la fecha se ancla al INICIO del renglón (como signed/cargo_abono).
- **R9-A-003 (BAJA) — Nu TDC no auto-identificable:** el hint 'Tarjeta de crédito Nu' (con acento) no
  casaba contra 'credito' sin acento, y el 'Nu' compartido empataba con nu_debito. Ahora `_account_hits`
  pliega acentos, pondera por especificidad (hints de más palabras pesan más) y deduplica hints
  plegados. Nu TDC y Nu débito se distinguen (verificado con los estados reales).

El auditor confirmó que la heurística de signo BBVA y la inversión `sign_convention` **resisten**
(fallan seguro; un signo mal firmado rompe el delta o la conciliación y bloquea).

Verificado: `tests/test_r9_bancos_reales.py` (6 casos); regresión intacta.

## 🔒 Ronda 9b — VERIFICADO (condición de ACCEPTED del retest sol 2026-07-21)
El retest de sol (ronda 8, CONDITIONALLY ACCEPTABLE "a un paso de ACCEPTED") dejó UNA condición:
- **R8-A-001 (sol) — RESUELTO:** `close_month` bloqueaba la cuarentena a nivel *statement* (tabla
  `quarantine`) pero NO a nivel *movimiento* (`review_status='cuarentena'`, supersession ambigua),
  pudiendo sellar un mes con un sub-conteo. Ahora `close_month` también trata como pendiente la
  cuenta/mes con `COUNT(transactions WHERE review_status='cuarentena')>0` por `date_op` (simetría).
  Verificado con `tests/test_r8_integridad.py` (nuevo caso); regresión intacta.
Heredados fail-safe (no bloquean): SOL-009 (extracción PDF por banco — ya construida para débito,
crédito en revisión) y SOL-014 (lock/hash de inmutabilidad fuera de ventanas de commit).

## 🔒 Ronda 9 — CALIBRACIÓN CON ESTADOS REALES (F5-R3-02, en curso)
Se calibraron las plantillas contra estados de cuenta REALES enmascarados (no versionados). Cada
banco se verifica end-to-end: extracción → normalización → CONCILIACIÓN (opening + Σ == closing).

Resultado con los 5 estados reales (todos: cuenta identificada, 100% de fechas parseadas):
| Estado | Cuenta | Mov. | Conciliación | Estado |
|---|---|---:|---|---|
| BBVA débito | bbva_debito | 14 | **OK** | READY_TO_COMMIT |
| Nu débito | nu_debito | 15 | **OK** | READY_TO_COMMIT |
| Nu crédito | nu_tdc | 6 | (revolvente) | REVIEW_REQUIRED |
| Ualá crédito | uala_tdc | 10 | (revolvente) | REVIEW_REQUIRED |
| Vexi | vexi_tdc | 1 | (revolvente) | REVIEW_REQUIRED |

**Calibrados y conciliando (débito):**
- **BBVA débito** — layout `OPER LIQ DESC REF CARGOS ABONOS OPERACION LIQUIDACION`: importe = 1er
  número, saldos al final; SIN signo por línea → se deriva del DELTA del saldo corriente, con
  resolución de tramos sin saldo (mismo día) y heurística RECIBIDO/DEPÓSITO; la conciliación es el
  candado. Estado real: 14 movimientos, cargos/abonos exactos, **concilia diff 0**.
- **Nu débito** — importe con SIGNO explícito (+$/−$), fecha `DD MMM YYYY`; saldo de cierre con la
  terminología de Nu ('Saldo al generar este estado de cuenta'). Estado real: 15 movimientos,
  **concilia diff 0**.

**Mejoras transversales:** `detect_bank` e `identify_account` pasaron a **frecuencia** (el banco
emisor domina; un estado que menciona otro banco en una transferencia ya no se atribuye mal);
extracción de periodo del PDF (year_hint para fechas DD/MMM); cierre de Nu.

**Crédito (Nu/Ualá/Vexi):** los movimientos se extraen bien (fecha, importe, descripción limpia) y
con signo CANÓNICO — la cuenta es `sign_convention='inverted'`, así que una COMPRA con TDC se guarda
como GASTO (−) y un PAGO como (+). Quedan en REVIEW_REQUIRED (fail-safe): la CONCILIACIÓN del crédito
revolvente (adeudo anterior + cargos − pagos, con conversiones de línea de crédito, intereses, IVA y
planes de pago) NO se auto-cuadra todavía — es un modelado por banco que conviene calibrar con más de
un estado por banco. Nunca corrompe: bloquea y muestra los movimientos para revisión/aprobación.

Verificado: `tests/test_r9_bancos_reales.py` (golden BBVA débito + Nu débito conciliando; crédito con
signo invertido); regresión intacta (auditoría 26/26, r4–r8, hooks/ocr/mcp/e2e).

## 🔒 Remediación ronda 8b — VERIFICADO (retest sol 20260720-1524, 88/100)
Cierre de los dos puntos que mantenían `SOL-R2-006` parcial en el último retest de sol:
- **La supersession AMBIGUA ahora alimenta el contrato/dashboard.** Un movimiento ambiguo se pone en
  `review_status='cuarentena'` y se EXCLUYE del export (evita el sobre-conteo), pero antes ese
  sub-conteo era invisible. Ahora `export_excel` reporta esos movimientos excluidos
  (`review_txns`/`review_amount_minor`) en el `_contract.json`; el puente los escribe
  (`Mov_RevisarN/Monto`) y el DASHBOARD los muestra junto a la cuarentena ("NO incluidos").
- **Test de limpieza reforzado (no trivial).** El anterior pasaba sin limpiar ni committear
  (`before=1, after=1, committed=0`). Ahora usa un WARNING de cobertura → REVIEW_REQUIRED aprobable:
  bloquea (cuarentena, 0 committeadas) → approve → commit → cuarentena a 0 y 1 committeada.

Verificado con `tests/test_r7_cuarentena.py` (3/3, limpieza real) y `tests/test_r8_integridad.py`
(8/8, incl. review_txns del contrato); regresión intacta; DASHBOARD recalculado muestra
"⚠ Ingesta (NO incluidos): N en cuarentena · M ambiguos por revisar".

## 🔒 Remediación ronda 8 — VERIFICADO (endurece el código nuevo de ronda 7)
- **Conciliación no tautológica en plantillas de saldo (R8-A-001, HIGH):** el importe del movimiento
  es ahora el DECLARADO en la línea; el saldo corriente solo aporta el signo y CRUZA-VALIDA la
  magnitud (delta ≠ declarado ⇒ `importe_dudoso`). Antes el importe ERA el delta, lo que hacía que
  Σimportes telescopiara y la conciliación diera diff=0 SIEMPRE (ocultando saldos corruptos o filas
  perdidas). Ahora un saldo corrupto se marca dudoso y una fila perdida hace que la conciliación no
  cuadre y bloquee.
- **Identificación de cuenta robusta (R8-A-002, HIGH):** `identify_account` casa por LÍMITE DE
  PALABRA (no subcadena: `Nu` ya no casa en `anual`), prioriza la CABECERA sobre las descripciones,
  y si más de una cuenta casa es AMBIGUO ⇒ no identifica (bloquea) en vez de 'primero gana'.
- **Cuarentena por mes contable real (R8-A-003, HIGH):** se archiva por el mes de `date_op` de cada
  movimiento (clave compuesta `(statement_id, month)`), no por `period_start`; así `close_month` del
  mes real ve la cuarentena y no se puede sellar un mes con movimientos retenidos que le pertenecen.
- **Monto de cuarentena estimado (R8-A-004, MEDIUM):** si el importe es dudoso, se estima desde el
  texto crudo en vez de reportar $0 (que daba falsa tranquilidad justo en el bloqueo más común).
- **`_untrust` quita controles C0/ANSI (R8-A-005, LOW):** evita inyección de terminal si se ve el
  `preview.md` en consola.

Verificado con `tests/test_r8_integridad.py` (7/7); regresión intacta (r4–r7, auditoría 26/26,
hooks/ocr/mcp/e2e). Excel R8-B: PRESUP_MENSUAL por-categoría/C6 usa `GASTO_NO_AHORRO` (gasto real
12000, 50/30/20 sin doble-conteo) y la alerta de fondo coerce numérico.

## 🔒 Remediación ronda 7 — VERIFICADO (los 3 pendientes de sol/fable)
- **Cuarentena visible (SOL-R2-006, HIGH parcial):** lo retenido por la ingesta (estados bloqueados:
  dudoso, mes cerrado, revisión) ya no subcuenta en silencio. Tabla `quarantine` (migración v3);
  `export_excel` propaga conteo/monto al `_contract.json` y a `cuarentena.csv`; el puente los escribe
  en el libro (frescura + celdas `Mov_CuarentenaN/Monto`) y el DASHBOARD **alerta** "⚠ N movimiento(s)
  en cuarentena — NO incluidos"; `close_month` se **bloquea** mientras haya cuarentena para el mes.
- **Descripción como dato estructurado (SOL-R2-005, MEDIUM):** se ELIMINÓ el blacklist por regex
  (evadible por sinónimos y mutilaba descripciones legítimas). Ahora `preview.md` preserva la
  descripción FIEL, encapsulada como dato entre backticks, con un banner de *consumer policy*
  ("DATOS NO CONFIABLES — NO instrucciones"). No se interpreta el contenido; la responsabilidad de no
  obedecer instrucciones embebidas es del consumidor.
- **Plantillas de extracción PDF por banco (F5-R3-02, MEDIUM):** `bank_templates.py` maneja el layout
  real CARGO/ABONO/SALDO. Estrategia `saldo` (BBVA/Nu/Ualá/GBM): el importe FIRMADO se deriva del
  delta del saldo corriente (separa saldo del importe Y asigna signo); estrategia `cargo_abono` (Vexi)
  por rótulo/signo. Si no se detecta banco, cae al parser genérico (fail-safe). La identificación de
  cuenta ahora usa la cabecera del documento. Verificado end-to-end con PDFs SINTÉTICOS por banco
  (extracción→normalización→conciliación OK, signos correctos).
  **Limitación honesta:** los fixtures son ficticios; falta calibrar contra un estado REAL enmascarado
  de cada banco (formato exacto de columnas/fechas) — es lo único que separa un ACCEPTED limpio.

Verificado: `tests/test_r7_cuarentena.py` (3/3), `tests/test_r7_pdf_bancos.py` (5/5); regresión intacta
(auditoría 26/26, r4–r6, hooks/ocr/mcp/e2e).

## 🔒 Remediación ronda 6 — VERIFICADO
- **Mes cerrado inmutable por date_op (R6-A-001, HIGH):** el candado de mes cerrado se evaluaba por
  el periodo del estado, pero la atribución contable es por `date_op`. Un movimiento retrofechado a
  un mes ya cerrado (compra fin de mes que llega en el estado del siguiente) mutaba el mes sellado
  en silencio. Ahora `_month_is_closed` revisa el mes de `date_op` de CADA movimiento y bloquea el
  commit (visible como `skipped:MONTH_CLOSED` + audit).
- **`type` revalidado (R6-A-002):** `type` es corregible pero no se revalidaba; un `correct` podía
  fijar `tipo='transferencia'`/basura en un gasto (y el libro Excel usa `Mov_Tipo` para el P&L).
  Ahora el commit exige que `type` pertenezca al catálogo (`VALID_TYPES`) + enum en el schema.
- **xlsx multi-hoja (R6-A-003):** el extractor procesaba solo la primera hoja de datos (`break`) y
  descartaba el resto sin aviso. Ahora procesa TODAS y avisa si combina >1 hoja.
- **Aprendizaje de correcciones (R6-A-004):** corregir la categoría fija `category_source='manual'`
  (antes quedaba el source viejo → no entraba al histórico y tergiversaba la procedencia).
- **Homóglifos (R6-A-005):** `normalize_merchant` aplica NFKC + plegado de confundibles
  cirílico/griego → una regla `casino` ya no se evade con `CаSINO` (а cirílica).

Verificado con `tests/test_r6_integridad.py` (5/5); regresión intacta (r4/r5, auditoría 26/26,
hooks/ocr/mcp/e2e). Nota: `query_sql` NO existe en esta ingesta (es del MCP finanzas-diego).

## 🔒 Remediación ronda 5 — VERIFICADO
- **Concurrencia (R5-A-001, HIGH):** `commit`/`rollback` usan `BEGIN IMMEDIATE` (antes `BEGIN`
  diferido leía y luego escribía → SQLite devolvía `SQLITE_BUSY` sin aplicar el `busy_timeout`).
  Verificado: 0 fallos por lock en commits concurrentes (antes ~7/8).
- **Inyección vía descripción/`raw_text` (R5-A-002):** `preview.md` neutraliza el texto libre no
  confiable (colapsa saltos, lo encierra en `código`) para que un consumidor LLM no lo lea como
  instrucción. El export canónico preserva la descripción literal (protegido aparte con `_san`).
- **Supersession ambigua visible (R5-A-003):** cuando no se puede emparejar 1:1 una corrección con
  su versión previa, además del audit event el movimiento nuevo se marca
  `review_status='por_revisar'` (el posible sobre-conteo deja de ser silencioso).
- **Anti-DoS (R5-A-004):** límite de filas por documento (`limits.max_rows_per_statement`, bloquea
  y falla seguro) y cota al bucket O(n²) de detección de transferencias (`transfer_bucket_cap`).
- **Cota de magnitud (R5-A-005):** un importe > 10^15 centavos se marca `importe_dudoso` (bloquea)
  en vez de reventar el INSERT con `OverflowError`.

Verificado con `tests/test_r5_integridad.py` (5/5) + prueba empírica de concurrencia; regresión
intacta (r4 8/8, auditoría 23/23, hooks/ocr/mcp/e2e). Los fixes de ronda 4 (HMAC, revalidación de
links, rollback restore, month_close) siguen firmes.

## 🔒 Remediación ronda 4 — VERIFICADO
- **Sello no evadible por re-sello (SOL-R4-001/SOL-R5-001):** `approve`/`correct`/
  `apply_corrections` verifican el **sello previo** antes de re-sellar (`_read_sealed_full`), y el
  sello es ahora **HMAC con clave** (`data/.seal_key`, fuera de staging), no un SHA256 público.
  Los `transfer_links` se **re-validan server-side** en commit (signo/importe/moneda) para que un
  link forjado no oculte movimientos del P&L.
- **Supersession por identidad de movimiento (SOL-R4-002/F5-R3-01):** `bank_ref` por sí solo ya
  no basta (dos movimientos legítimos con la misma ref no se fusionan); se empareja por
  (cuenta, `date_op`, `description_norm`) con refs compatibles y **unicidad 1:1** — cubre
  correcciones sin folio y nunca pierde ni duplica en silencio (ambigüedad → conserva ambos +
  `supersede_omitido_ambiguo`).
- **Rollback reversible (SOL-R5-002):** al deshacer una corrección se **restaura** el movimiento
  superseded (`dup_of=NULL`) antes de borrar (evita la violación de FK que lo volvía
  irreversible); auditado como `unsupersede_txn`. Además el rollback revierte `month_close`
  (SOL-R5-004).
- **Sin transferencias falsas por prefijo (SOL-R5-003):** `_ref_related` exige igualdad de folio
  (o prefijo solo si ≥6 caracteres); refs cortas ya no vinculan movimientos no relacionados.

Verificado con `tests/test_r4_integridad.py` (8/8) y los oráculos de los auditores; regresión
intacta (23/23 + hooks/ocr/mcp/e2e).

## 🔒 Remediación ronda 3 (retest-2) — VERIFICADO
- **Supersession a nivel de MOVIMIENTO (F5-R2-08/SOL-R3-002):** un movimiento corregido
  (mismo account+`bank_ref`, importe distinto, periodo solapado) reemplaza a su versión previa;
  los movimientos **disjuntos** de un estado del mismo periodo se **conservan** (sin pérdida
  silenciosa). Reemisión idéntica y corrección −250→−350 siguen sin duplicar.
- **Sello del payload completo (SOL-R3-001):** el sello cubre `statements` + `transfer_links` +
  `corrections`; forjar cualquiera rompe el sello y el commit aborta.
- **Config revalidada en commit (SOL-R3-003):** desactivar la cuenta (o cambiar su moneda) entre
  preview y commit hace fallar el commit (cierra la ventana TOCTOU).
- Limitación conocida: un movimiento corregido **sin `bank_ref`** no se empareja con su versión
  previa → se conserva como movimiento aparte (conservador, regla 26). El adversarial del auditor
  (`r3_adversarial.py`) pasa en los 4 casos.

## 🔒 Remediación ronda 2 (retest) — VERIFICADO
- **Staging no confiable (CRITICAL SOL-R2-001):** el `_full.json` se **sella**; el commit
  re-valida esquema, re-deriva IDs, re-concilia y verifica moneda por txn. Cualquier mutación
  post-preview (signo/importe/fecha/moneda/cuenta/omisión/duplicado/saldo/categoría fuera de
  `correct`) **bloquea**. Correcciones legítimas vía `correct`/MCP re-sellan y pasan.
- **Corrección que cambia importe (F-01 completo):** un estado del mismo account con periodo
  solapado **reemplaza** al anterior (SUPERSEDED, excluido del export); no duplica.
- **Migración v1→v2 (SOL-R2-002):** una BD existente en v1 adquiere los CHECK vía migración v2
  (reconstrucción de tablas con FK verificadas).
- **Transferencias misma moneda (SOL-R2-003):** no se emparejan MXN↔USD.
- **Cuenta inactiva (SOL-R2-004):** bloquea el commit.
- **Excel:** el DASHBOARD/presupuestos **excluyen transferencias** del Ingreso/Gasto
  (F5-R2-07); la tabla `tMovimientos` se **redimensiona** a los datos (401+ filas dentro,
  SOL-R2-005); el flujo `--datos` **cachea valores** (F-22) y fija el **periodo** al mes de los
  datos (F5-R2-02); el generador **respalda** el entregable antes de sobrescribir (F5-R2-05);
  fechas del CSV se parsean **estrictamente** (F5-R2-06/SOL-R2-006) y `validar.py` verifica un
  **contrato de fórmulas**. Oráculo de deudas **independiente** (fórmula cerrada, F5-R2-04).

## ✅ Construido y VERIFICADO (con evidencia ejecutable)
- **Fuente canónica SQLite** (dinero en centavos enteros) con **CHECKs de dominio**
  (moneda ISO, signo ±1, fecha ISO) y **migraciones transaccionales con backup** (una
  migración fallida no deja esquema parcial). *(SOL-003, SOL-011)*
- **Parsing monetario** robusto sin float; la ambigüedad miles/decimal (`5.000`) se marca
  **dudosa** en vez de asumirse. *(F-14)*
- **Sin doble conteo**: un estado reemitido/solapado NO duplica movimientos; el commit omite
  los `dup_of` y el export filtra `dup_of IS NULL`; supersession explícita entre estados.
  *(F-01, SOL-006)*
- **Integridad de moneda**: se bloquea mezclar moneda de cuenta/estado; conversión exige
  objeto FX explícito; moneda fuera de ISO 4217 soportada se rechaza. *(SOL-010)*
- **Transferencias con señal**: no se vinculan por solo importe; requieren `bank_ref` cruzado
  o token de contraparte; sin señal se marcan para revisión. *(F-06)*
- **Validación JSON Schema real** como paso VALIDATED (jsonschema obligatorio). *(F-03)*
- **Aprobación auditada** REVIEW_REQUIRED→READY_TO_COMMIT (`finance approve`, no aprueba
  bloqueos contables duros); el commit avisa de statements omitidos. *(F-04)*
- **Correcciones auditadas** (tabla `corrections` + `audit_events`, campos contables
  inmutables; campos no permitidos se rechazan explícito). *(SOL-005)*
- **Estado de exportación explícito** (EXPORTED / EXPORT_FAILED) y **export atómico**
  (temp+fsync+rename); concurrencia con `busy_timeout`. *(F-12/SOL-008, F-11/SOL-012)*
- **Cierre de mes persistente** (`finance cerrar-mes`). *(F-10)*
- **Máquina de estados activa** (`statemachine.check` en cada transición) y **política
  consumida** (`import_policy.block_on` se evalúa; sin claves muertas). *(F-08, F-09)*
- **Neutralización de inyección CSV** (`= + - @`, tab/CR) en export y artefactos. *(F-07/SOL-002)*
- **Hooks endurecidos y ACTIVOS**: cubren Bash, rutas canónicas case-insensitive,
  fail-closed; protección de fondo a nivel FS (`scripts/hooks/proteger_fs.sh`). *(F-05/SOL-007)*
- **OCR robusto**: orientación/deskew + confianza por línea; importes de baja confianza se
  bloquean (no se fabrican). *(SOL-009)*
- **MCP local** (`finance.mcp_server`, stdio JSON-RPC) sobre la capa `services` con paridad
  CLI, dry-run por defecto y confirmación para commit/rollback. *(SOL-004)*
- **Dependencias** con piso parcheado (Pillow ≥ 12.3), jsonschema obligatoria y límites
  anti-DoS de imagen. *(SOL-014)*
- **Puente ingesta → Excel FUNCIONAL y VERIFICADO**: `construir_libro.py --datos <csv>`
  inyecta el CSV canónico en MOVIMIENTOS; el DASHBOARD refleja los totales importados.
  Traza end-to-end fixture→SQLite→CSV→xlsx→KPI probada en `Herramientas/test_puente.py`.
  *(F-15/SOL-001)*
- **CLI** completa sobre la capa única; **E2E** (`tests/test_e2e.py`) y regresiones de
  auditoría (`tests/test_regresion_auditoria.py`, `test_hooks.py`, `test_ocr.py`,
  `test_mcp.py`) pasan.

## 🟡 Parcial / heurístico (funciona, con matices honestos)
- **PDF de texto y OCR por banco**: los extractores existen y el OCR ya es fail-safe
  (bloquea dudoso), pero el parser de líneas es **genérico**; para producción por banco
  conviene una **plantilla específica**. CSV/XLSX son deterministas (ruta probada del E2E).
- **Bloques de captura del libro** (DEUDAS 8, METAS 12, INVERSIONES 20, GASTOS_FIJOS 25,
  CALENDARIO 30): capacidad **fija**; MOVIMIENTOS sí se expande (rangos de columna completa
  + el puente extiende fórmulas). Si excedes un bloque fijo, amplía el rango antes de cargar.
  *(F-21: capacidad documentada; no hay truncación silenciosa en MOVIMIENTOS)*
- **Categorización IA**: interfaz lista pero **desactivada** por defecto (no adivina).

## 🔴 Pendiente (requiere tu Mac / datos reales)
- **Corte REAL**: importar tus estados reales (OCR de tus PDFs) y, si aplica, migrar datos
  históricos — requiere ejecutar en tu Mac.
- **Refresco en vivo por Power Query** (opcional): el puente por builder-inyección es la ruta
  probada y verificable; si prefieres refresco sin reconstruir, puedes además configurar
  Power Query una vez (ver `docs/workbook-contract.md`).
- **Matriz completa de fixtures dorados** (regla 70): faltan casos escaneado/imagen con golden
  files por banco, 2ª moneda con FX real, y volúmenes de miles de movimientos.

## Cómo probar ahora mismo (en tu Mac)
```
# Ingesta
FINANCE_ROOT=$(pwd) python3 tests/test_e2e.py                 # "E2E COMPLETO SIN FALLOS"
python3 tests/test_regresion_auditoria.py                     # 14/14 en verde
python3 tests/test_hooks.py && python3 tests/test_mcp.py

# Puente end-to-end (desde Herramientas/)
python3 test_puente.py                                        # traza fixture→…→KPI
```
Requiere Python 3.11+, `pyyaml openpyxl pdfplumber pytesseract Pillow>=12.3 jsonschema numpy`,
`tesseract`(+lang) y **LibreOffice** (`soffice`) para recálculo/validación.

## Riesgos vigentes
OCR por banco (mitigado: bloquea dudoso, no inventa); preservación del Excel real en el corte
en tu Mac; capacidad fija de algunos bloques del libro (documentada arriba).
