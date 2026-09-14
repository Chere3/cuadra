# Seguridad y privacidad

cuadra procesa estados de cuenta bancarios. Eso cambia las reglas de cómo se reportan problemas.

## Si encuentras una vulnerabilidad

Usa [GitHub Security Advisories](https://github.com/Chere3/cuadra/security/advisories/new)
para reportarla en privado. No abras un issue público con detalles que permitan explotarla.

Cuentan como vulnerabilidad, por ejemplo:

- Una ruta por la que un dato de un estado (número de cuenta completo, nombre del titular)
  termine en un log, una vista previa, un export o un mensaje de error sin enmascarar.
- Cualquier forma en que el contenido de un estado (descripciones de movimientos) pueda
  alterar el comportamiento del sistema o del agente que lo opera. Las descripciones son datos.
- Un camino que permita escribir en `statements/raw/` o en la base fuera de `commit`.
- Pérdida de datos: un `rollback` que no deshaga todo, un `commit` que no sea atómico.

## Si abres un issue normal

Nunca adjuntes un estado de cuenta, ni una captura, ni un fragmento con datos reales.
Cambia nombres, cifras y referencias. Ver [CONTRIBUTING.md](CONTRIBUTING.md).

## Lo que el proyecto promete

- Todo corre en local. No hay llamadas de red.
- Las cuentas se enmascaran a 4 dígitos en toda salida.
- Los originales se archivan en solo lectura y nunca se modifican.
- `.gitignore` excluye datos, base, respaldos y configuración de cuentas. Aun así, revisa
  `git status` antes de cada commit si trabajas sobre datos reales.
