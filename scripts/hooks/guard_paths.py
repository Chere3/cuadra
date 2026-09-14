#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guard_paths.py — Hook PreToolUse: BLOQUEA escrituras a rutas protegidas
(plantilla Excel inmutable, estados originales, base SQLite directa).

Endurecido (F-05/SOL-007):
- Cubre Write | Edit | MultiEdit | NotebookEdit **y Bash** (redirecciones, cp/mv/tee/dd,
  sqlite3, python open('w'), sed -i, rm, truncate…).
- Rutas canónicas (realpath, resuelve symlinks) y comparación **case-insensitive**
  (macOS): `Mega.XLSX`, `Mega.xlsx.tmp`, symlink → statements/raw quedan cubiertos.
- **Fail-closed**: si el input no se puede leer/parsear, se BLOQUEA (exit 2).

RESIDUAL CONOCIDO (F5-R2-06, LOW): una ruta ofuscada construida en runtime
(p. ej. `P=$(echo <b64>|base64 -d); echo x>$P`) evade la detección por literal. Los hooks
son DEFENSA EN PROFUNDIDAD contra escrituras accidentales de Claude, no un sandbox: la
protección de fondo real es (1) el FS de solo-lectura de plantilla/estados
(`proteger_fs.sh`) y (2) que la única vía a la BD es la capa `services`. La ofuscación
deliberada queda fuera del modelo de amenazas de este hook.

Salida con código 2 = bloquear.
"""
import sys
import os
import re
import json

# Fragmentos de ruta protegidos (comparados sobre la ruta canónica en minúsculas).
PROTECTED_SUBSTR = ["/excel/template/", "/statements/raw/"]
PROTECTED_SUFFIX = [".sqlite", ".sqlite-wal", ".sqlite-shm"]

# Indicadores de ESCRITURA en un comando Bash.
WRITE_TOKENS = re.compile(
    r"(>>?|\btee\b|\bcp\b|\bmv\b|\bdd\b|\btruncate\b|\brm\b|\bln\b|\bsqlite3\b|"
    # R10-A-005: comandos de copia/escritura no ofuscados (install/rsync/cpio/ditto) que un agente
    # podría emitir por accidente y clobberear la plantilla o un estado original.
    r"\binstall\b|\brsync\b|\bcpio\b|\bditto\b|"
    r"\bsed\b\s+-i|open\s*\([^)]*['\"][wax]|\binsert\b|\bupdate\b|\bdelete\b|\bdrop\b|\balter\b)",
    re.I)
PROTECTED_IN_CMD = re.compile(r"(excel/template/|statements/raw/|\.sqlite\b)", re.I)


def _canon(p):
    try:
        return os.path.realpath(os.path.abspath(p)).replace("\\", "/").lower()
    except Exception:
        return (p or "").replace("\\", "/").lower()


def _is_protected_path(p):
    c = _canon(p)
    if any(sub in c for sub in PROTECTED_SUBSTR):
        return True
    if any(c.endswith(suf) for suf in PROTECTED_SUFFIX):
        return True
    return False


def _block(msg):
    sys.stderr.write(
        "BLOQUEADO por 30-integridad-excel / 00-invariantes / 50-privacidad: " + msg +
        "\nUsa los comandos oficiales (finance ...) o trabaja sobre una copia versionada.\n")
    return 2


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        # fail-closed: sin input legible no se puede garantizar seguridad
        return _block("no se pudo leer/parsear el input del tool (fail-closed).")

    ti = data.get("tool_input", data)

    # Herramientas de escritura de archivo: revisar la ruta canónica.
    for key in ("file_path", "path", "notebook_path"):
        p = ti.get(key)
        if p and _is_protected_path(p):
            return _block(f"escritura directa a ruta protegida '{p}'.")

    # Bash: revisar el comando por referencias a rutas protegidas con intención de escritura.
    cmd = ti.get("command") or ""
    if cmd and PROTECTED_IN_CMD.search(cmd) and WRITE_TOKENS.search(cmd):
        return _block("comando Bash que escribiría en una ruta protegida.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
