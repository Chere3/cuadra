## Qué cambia para quien usa cuadra

<!-- Una o dos frases. Si es un banco nuevo: banco, producto y formato. -->

## El detalle que muerde

<!-- Lo que te sorprendió del formato o del bug. Termina en la tabla del README. -->

## Checklist

- [ ] Ningún dato real: nombres, cifras y referencias son inventados.
- [ ] Hay un test `tests/test_rNN_*.py` que falla si se revierte el cambio.
- [ ] `PYTHONPATH=src python3 -m pytest tests -q --deselect tests/test_ocr.py --deselect tests/test_ci_suites.py` pasa.
- [ ] Dinero en centavos enteros, sin `float` en rutas contables.
