# -*- coding: utf-8 -*-
"""
test_hooks.py — Matriz de bypass del hook guard_paths (F-05/SOL-007).
Cada caso que la auditoría evadió debe quedar BLOQUEADO (exit 2); las operaciones
legítimas deben PASAR (exit 0).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSYS = os.path.dirname(HERE)
HOOK = os.path.join(SUBSYS, "scripts", "hooks", "guard_paths.py")
TPL = os.path.join(SUBSYS, "excel", "template")


def _run(payload, raw=None):
    data = raw if raw is not None else json.dumps(payload)
    p = subprocess.run([sys.executable, HOOK], input=data.encode(),
                       capture_output=True)
    return p.returncode


def _write(path):
    return {"tool_name": "Write", "tool_input": {"file_path": path}}


def _bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def main():
    cases = []  # (nombre, exit_esperado, payload/raw)
    tpl_xlsx = os.path.join(TPL, "Mega_Sistema_Finanzas_Personales.xlsx")
    cases.append(("write plantilla directa", 2, _write(tpl_xlsx)))
    cases.append(("write plantilla MAYÚSCULAS", 2, _write(os.path.join(TPL, "MEGA.XLSX"))))
    cases.append(("write plantilla .tmp", 2, _write(tpl_xlsx + ".tmp")))
    cases.append(("write base sqlite", 2, _write(os.path.join(SUBSYS, "data", "finance.sqlite"))))
    cases.append(("bash sqlite3 a la base", 2, _bash("sqlite3 data/finance.sqlite 'DELETE FROM transactions'")))
    cases.append(("bash cp a plantilla", 2, _bash("cp /tmp/x.xlsx excel/template/Mega.xlsx")))
    cases.append(("bash redireccion a raw", 2, _bash("echo x > statements/raw/estado.pdf")))
    cases.append(("legit: write a staging", 0, _write(os.path.join(SUBSYS, "staging", "r", "x.json"))))
    cases.append(("legit: bash leer export", 0, _bash("cat data/exports/transacciones_excel.csv")))

    # symlink -> statements/raw (realpath debe resolverlo y bloquear)
    tmp = tempfile.mkdtemp()
    link = os.path.join(tmp, "linkraw")
    try:
        os.symlink(os.path.join(SUBSYS, "statements", "raw"), link)
        cases.append(("write via symlink a raw", 2, _write(os.path.join(link, "estado.pdf"))))
    except OSError:
        pass

    fails = 0
    for name, expected, payload in cases:
        rc = _run(payload)
        ok = rc == expected
        print(f"{'PASS' if ok else 'FAIL'} {name}: exit={rc} (esperado {expected})")
        if not ok:
            fails += 1

    # fail-closed: JSON inválido debe bloquear
    rc = _run(None, raw="{no es json")
    ok = rc == 2
    print(f"{'PASS' if ok else 'FAIL'} fail-closed JSON inválido: exit={rc} (esperado 2)")
    if not ok:
        fails += 1

    total = len(cases) + 1
    print(f"\n{total-fails}/{total} en verde")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
