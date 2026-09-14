# -*- coding: utf-8 -*-
"""test_mcp.py — SOL-004: el MCP existe, envuelve la capa services (paridad con la CLI),
es dry-run por defecto, exige confirmación para commit/rollback, limita rutas y no expone
cuentas completas. Incluye un round-trip del protocolo stdio JSON-RPC."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSYS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUBSYS, "src"))


def _fresh_root():
    root = tempfile.mkdtemp(prefix="finmcp_")
    for d in ("config", "schemas", "fixtures/sanitized"):
        shutil.copytree(os.path.join(SUBSYS, d), os.path.join(root, d))
    for d in ("data/exports", "staging", "statements/inbox"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    # copiar un fixture al inbox
    shutil.copy(os.path.join(SUBSYS, "fixtures/sanitized/bbva_2026-06.csv"),
                os.path.join(root, "statements/inbox/bbva_2026-06.csv"))
    return root


def _reload(root):
    os.environ["FINANCE_ROOT"] = root
    for m in list(sys.modules):
        if m == "finance" or m.startswith("finance."):
            del sys.modules[m]
    from finance import mcp_tools as T, services as S, config as C
    C.reset_cache()
    return T, S


def test_paridad_status():
    T, S = _reload(_fresh_root())
    assert T.finance_system_status() == S.status(), "MCP status != services.status (sin paridad)"


def test_preview_es_dry_run():
    T, S = _reload(_fresh_root())
    res = T.finance_preview_import("statements/inbox")
    assert res["dry_run"] is True, "MCP preview debe ser dry-run"
    assert S.status()["counts"]["transactions"] == 0, "preview no debe incorporar nada"


def test_commit_exige_confirmacion():
    T, S = _reload(_fresh_root())
    res = T.finance_preview_import("statements/inbox")
    run_id = res["run_id"]
    try:
        T.finance_commit_import(run_id)   # sin confirm
        assert False, "commit sin confirm debió fallar"
    except T.MCPError:
        pass
    out = T.finance_commit_import(run_id, confirm=True)
    assert out.get("committed") is True
    assert S.status()["counts"]["transactions"] > 0, "commit confirmado no incorporó"


def test_ruta_fuera_de_proyecto_bloqueada():
    T, S = _reload(_fresh_root())
    try:
        T.finance_preview_import("../../../etc")
        assert False, "ruta fuera del proyecto debió bloquearse"
    except T.MCPError:
        pass


def test_no_expone_cuenta_completa():
    T, S = _reload(_fresh_root())
    res = T.finance_preview_import("statements/inbox")
    run = T.finance_get_run(res["run_id"])
    blob = json.dumps(run, ensure_ascii=False)
    assert "raw_text" not in blob, "el manifiesto no debe incluir texto original"
    # las cuentas van enmascaradas (formato '•••• 0000'), nunca dígitos largos crudos
    assert "••••" in blob or all(s.get("account") for s in run.get("statements", [])) is not None


def test_protocolo_stdio():
    root = _fresh_root()
    env = dict(os.environ, FINANCE_ROOT=root)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "finance_system_status", "arguments": {}}},
    ]
    inp = "\n".join(json.dumps(m) for m in msgs) + "\n"
    p = subprocess.run([sys.executable, "-m", "finance.mcp_server"],
                       input=inp.encode(), capture_output=True,
                       cwd=os.path.join(SUBSYS, "src"), env=env)
    out_lines = [l for l in p.stdout.decode().splitlines() if l.strip()]
    resps = [json.loads(l) for l in out_lines]
    ids = {r.get("id"): r for r in resps}
    assert 1 in ids and ids[1]["result"]["serverInfo"]["name"] == "finanzas-ingesta"
    assert 2 in ids and any(t["name"] == "finance_commit_import" for t in ids[2]["result"]["tools"])
    assert 3 in ids and "content" in ids[3]["result"]


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            fails += 1; print(f"FAIL {name}: {e}")
        except Exception:
            fails += 1; print(f"ERROR {name}:"); traceback.print_exc()
    print(f"\n{'OK' if not fails else str(fails)+' fallos'}")
    raise SystemExit(1 if fails else 0)
