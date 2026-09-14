# -*- coding: utf-8 -*-
"""
test_ci_suites.py — Wrapper unittest DESCUBRIBLE (retest sol 2026-07-22).

`python3 -m unittest discover -s tests` descubría 0 tests y salía con código 5 (NO TESTS RAN):
las suites históricas son scripts ejecutables con contrato "exit 0 = verde", no TestCases. Una CI
que dependiera solo de discovery daba una falsa sensación de cobertura. Este módulo expone CADA
suite como un caso unittest que la ejecuta en subproceso con el mismo intérprete; así discovery
corre la suite completa real y falla si cualquier script falla. Los scripts siguen siendo
ejecutables directamente (ese contrato no cambia).
"""
import os
import subprocess
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS_DIR)
_SELF = os.path.basename(__file__)

SUITES = sorted(n for n in os.listdir(_TESTS_DIR)
                if n.startswith("test_") and n.endswith(".py") and n != _SELF)


class TestSuites(unittest.TestCase):
    maxDiff = None


def _make_case(script):
    def run(self):
        env = dict(os.environ)
        env.setdefault("FINANCE_ROOT", _ROOT)
        p = subprocess.run([sys.executable, os.path.join(_TESTS_DIR, script)],
                           capture_output=True, text=True, env=env, timeout=1800)
        self.assertEqual(
            p.returncode, 0,
            f"{script} falló (exit {p.returncode})\n--- stdout ---\n{p.stdout[-4000:]}"
            f"\n--- stderr ---\n{p.stderr[-4000:]}")
    return run


for _n in SUITES:
    setattr(TestSuites, "test_" + _n[:-3], _make_case(_n))


if __name__ == "__main__":
    unittest.main(verbosity=2)
