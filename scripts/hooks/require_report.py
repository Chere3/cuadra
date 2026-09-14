#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
require_report.py — Hook Stop: recuerda que una importación NO está terminada sin
reporte + conciliación + estado final registrado. No destructivo (recordatorio).
Detecta importaciones committeadas sin reconciliation_checks (inconsistencia) y avisa.
"""
import os
import sys
import sqlite3

ROOT = os.environ.get("FINANCE_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "finance.sqlite")


def main():
    if not os.path.exists(DB):
        return 0
    try:
        con = sqlite3.connect(DB)
        n = con.execute(
            "SELECT COUNT(*) FROM imports i WHERE i.committed=1 AND i.rolled_back=0 "
            "AND NOT EXISTS (SELECT 1 FROM reconciliation_checks r WHERE r.import_id=i.import_id)"
        ).fetchone()[0]
        con.close()
    except Exception:
        return 0
    if n:
        sys.stderr.write(
            f"RECORDATORIO (70-pruebas/40-commit): {n} importación(es) committeada(s) sin "
            f"reconciliation_checks. Revisa con 'finance status' y 'finance doctor'.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
