# Contribuir a cuadra

Gracias por llegar hasta aquí. Lo que más ayuda, en este orden:

1. **Una plantilla para un banco que no está.** Es el 80 % del valor del proyecto.
2. **Un estado que rompe una plantilla existente.** Con fragmento ficticio, ver abajo.
3. Mejoras a la documentación en español o inglés.

## Regla cero: nunca datos reales

Ni un estado completo, ni recortado, ni "solo la línea que falla". Antes de pegar cualquier
fragmento en un issue, test o PR:

- Cambia el **nombre del titular** por uno inventado.
- Cambia **todas las cifras**. Que sigan cuadrando (saldo inicial + movimientos = saldo final)
  es parte del trabajo: usa números redondos.
- Cambia **referencias, folios, números de cuenta y CLABE**. Los últimos 4 dígitos de una
  cuenta son `0000` en todos los fixtures.
- Quita **direcciones, RFC, CURP** y cualquier dato que identifique a una persona.

Si el PR contiene algo que parezca real, se cierra sin revisar. No es personal.

## Agregar un banco

1. Lee `src/finance/extractors/bank_templates.py`. `BANKS` mapea marcadores de texto a una
   estrategia. Las estrategias existentes:
   - `signed`: cada línea trae el importe con signo.
   - `cargo_abono`: columnas separadas para cargo y abono.
   - `saldo`: el signo se deduce del saldo corriente.
   - Función propia (`_parse_bbva`, `_parse_hsbc`): cuando el diseño no cabe en las anteriores.
2. Si el PDF trae saldos y totales declarados, enséñale al parser a leerlos (`declared_totals`,
   `credit_summary`). Son lo que hace posible la conciliación.
3. Crea `tests/test_rNN_<banco>.py` (NN = el siguiente número libre). Copia la estructura de
   `tests/test_r21_bbva_tdc.py`: texto ficticio en una constante, parseo, asserts sobre fechas,
   importes en centavos y signo. El test debe **fallar** si quitas tu plantilla.
4. Añade el banco a `config/institutions.yml` y, si aplica, una cuenta de ejemplo a
   `config/accounts.example.yml`.
5. Corre la suite:
   ```bash
   PYTHONPATH=src python3 -m pytest tests -q --deselect tests/test_ocr.py --deselect tests/test_ci_suites.py
   ```
6. Documenta en el PR el "detalle que muerde" de ese banco: lo que te sorprendió del formato.
   Esa frase termina en la tabla del README.

## Estilo

- Python 3.11+, sin dependencias nuevas si se puede evitar. Si hace falta una, con rango de
  versión acotado en `requirements.txt` y una línea explicando por qué.
- Dinero siempre en **centavos enteros** (`money.py`). Un `float` en una ruta de dinero es un
  bug, aunque el test pase.
- Comentarios en español, como el resto del código. Explican **por qué**, no qué.
- La lógica contable vive en `services.py`. CLI y MCP solo la llaman.

## Pull requests

- Un PR por banco o por bug. Los PR que tocan tres cosas tardan tres veces más en revisarse.
- El título dice qué cambia para el usuario, no qué archivo tocaste.
- Si cambias una plantilla existente, di qué estado la rompió (banco, producto, mes, sin datos).
