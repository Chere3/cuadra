# -*- coding: utf-8 -*-
"""
test_r12_corte_real.py — Hallazgos del CORTE REAL (primeros estados reales importados).

R12-A-001 (HIGH) — `_basic_type` declaraba 'transferencia' por la sola palabra SPEI/transfer en
la descripción. En México la nómina llega por SPEI: el ingreso principal se tipificaba como
transferencia y el libro (que excluye Mov_Tipo="Transferencia" del P&L) lo borraba del ingreso
del mes en SILENCIO. Una transferencia es un HECHO VINCULADO entre dos cuentas propias
(`transfer_links` → columna `es_transferencia`, regla 26), no una heurística de texto.

R12-A-002 (HIGH) — `_parse_signed` (Nu/Ualá) leía cada movimiento de UNA sola línea. Cuando el
PDF pone la descripción en la línea ANTERIOR al importe (compras en divisa, transferencias
recibidas), `description` quedaba VACÍA: se pierde el dato (regla 23 exige conservarla) y con él
la señal de contraparte que permite vincular la transferencia (regla 26).

Fixtures SINTÉTICOS (regla 70): reproducen el layout, no contienen datos reales.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSYS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUBSYS, "src"))

from finance.services import _basic_type
from finance.extractors.bank_templates import _parse_signed


class TestTipoNoEsHeuristicaDeTexto(unittest.TestCase):
    """R12-A-001: el tipo no puede excluir dinero del P&L por una palabra en la descripción."""

    def test_spei_recibido_es_ingreso(self):
        # nómina depositada por SPEI desde el banco del empleador (NO es cuenta propia)
        self.assertEqual(_basic_type(735700, "SPEI RECIBIDO BANCO EMPLEADOR"), "ingreso")

    def test_spei_enviado_es_gasto(self):
        self.assertEqual(_basic_type(-429700, "SPEI ENVIADO TERCERO"), "gasto")

    def test_transferencia_en_texto_no_decide_el_tipo(self):
        self.assertEqual(_basic_type(185000, "TRANSFERENCIA RECIBIDA"), "ingreso")
        self.assertEqual(_basic_type(-39000, "TRASPASO A TERCERO"), "gasto")

    def test_pago_de_tarjeta_sigue_siendo_pago_deuda(self):
        # no se toca: reducir deuda no es gasto del periodo
        self.assertEqual(_basic_type(-11000, "Pago a tu tarjeta de credito"), "pago_deuda")

    def test_comision_e_interes_intactos(self):
        self.assertEqual(_basic_type(-3438, "Disposicion de saldo - intereses"), "interes")
        self.assertEqual(_basic_type(-5000, "Comision por manejo"), "comision")


class TestDescripcionMultilinea(unittest.TestCase):
    """R12-A-002: la descripción en la línea previa debe conservarse, no perderse."""

    NU = (
        "FECHA DEL 01 AL 30 JUN 2026 (30 DÍAS) MONTO EN PESOS MEXICANOS\n"
        "20 JUN 2026 COMERCIO EJEMPLO Compra -$1,750.43\n"
        "NOMBRE APELLIDO Transferencia desde\n"
        "20 JUN 2026 +$6,000.00\n"
        "Otro Banco\n"
    )

    UALA = (
        "Fecha de la operación Fecha de cargo Descripción del movimiento Monto\n"
        "01 Mayo 2026 02 Mayo 2026 COMERCIO CONOCIDO MEX + $79.94\n"
        "SERVICIO EJEMPLO* TRIAL CIUDAD ON\n"
        "24 Mayo 2026 25 Mayo 2026 + $1,740.79\n"
        "100.00USD (1USD= 17.41MXN)\n"
    )

    def test_nu_recupera_descripcion_de_linea_previa(self):
        rows, _, _ = _parse_signed(self.NU, page=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["amount"], "6000.00")
        self.assertIn("Transferencia desde", rows[1]["description"])
        self.assertTrue(rows[1]["description"].strip(), "la descripción no puede quedar vacía")

    def test_uala_recupera_descripcion_de_linea_previa(self):
        rows, _, _ = _parse_signed(self.UALA, page=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["amount"], "1740.79")
        self.assertIn("SERVICIO EJEMPLO", rows[1]["description"])

    def test_no_altera_las_descripciones_que_ya_venian_en_la_linea(self):
        rows, _, _ = _parse_signed(self.NU, page=1)
        self.assertEqual(rows[0]["description"], "COMERCIO EJEMPLO Compra")
        rows, _, _ = _parse_signed(self.UALA, page=1)
        self.assertEqual(rows[0]["description"], "COMERCIO CONOCIDO MEX")

    def test_no_toma_el_encabezado_como_descripcion(self):
        # si la línea previa es el encabezado de la tabla, NO se usa (no se inventa descripción)
        txt = ("Fecha de la operación Fecha de cargo Descripción del movimiento Monto\n"
               "01 Mayo 2026 02 Mayo 2026 + $226.68\n")
        rows, _, _ = _parse_signed(txt, page=1)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("Descripción del movimiento", rows[0]["description"])

    def test_no_toma_otro_movimiento_como_descripcion(self):
        # la línea previa es otro movimiento (empieza con fecha) => no se roba su texto
        txt = ("20 JUN 2026 PRIMER MOVIMIENTO -$100.00\n"
               "20 JUN 2026 +$200.00\n")
        rows, _, _ = _parse_signed(txt, page=1)
        self.assertEqual(len(rows), 2)
        self.assertNotIn("PRIMER MOVIMIENTO", rows[1]["description"])

    def test_importes_y_signos_intactos(self):
        rows, _, _ = _parse_signed(self.NU, page=1)
        self.assertEqual([r["amount"] for r in rows], ["-1750.43", "6000.00"])


class TestLasPruebasNoTocanDatosReales(unittest.TestCase):
    """R12-A-003 (CRITICAL): `test_e2e.py` tomaba FINANCE_ROOT del entorno y su `clean()` borra
    `data/finance.sqlite*`. Correr la suite destruía la base canónica del usuario. Guardia: se le
    apunta a un root con una base CENTINELA y se verifica que no la toca."""

    def test_el_e2e_no_borra_la_base_del_root_que_le_pasen(self):
        root = tempfile.mkdtemp(prefix="fin_centinela_")
        try:
            os.makedirs(os.path.join(root, "data"), exist_ok=True)
            centinela = os.path.join(root, "data", "finance.sqlite")
            with open(centinela, "wb") as f:
                f.write(b"NO BORRAR: base canonica del usuario")
            env = dict(os.environ, FINANCE_ROOT=root)
            p = subprocess.run([sys.executable, os.path.join(HERE, "test_e2e.py")],
                               capture_output=True, text=True, env=env, timeout=600)
            self.assertEqual(p.returncode, 0, f"el E2E falló:\n{p.stdout[-2000:]}")
            self.assertTrue(os.path.exists(centinela),
                            "el E2E BORRÓ la base del FINANCE_ROOT recibido")
            with open(centinela, "rb") as f:
                self.assertEqual(f.read(), b"NO BORRAR: base canonica del usuario",
                                 "el E2E SOBRESCRIBIÓ la base del FINANCE_ROOT recibido")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
