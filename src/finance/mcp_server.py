# -*- coding: utf-8 -*-
"""
mcp_server.py — Servidor MCP local sobre stdio (JSON-RPC 2.0), sin dependencias externas.

Expone los tools de `mcp_tools` (que invocan la capa `services`, regla 60). Implementa el
núcleo del protocolo MCP: `initialize`, `tools/list`, `tools/call` y la notificación
`notifications/initialized`. Un mensaje por línea (JSON delimitado por saltos de línea).

Uso (registrar en el cliente MCP):
    { "command": "python3", "args": ["-m", "finance.mcp_server"], "env": {"FINANCE_ROOT": "..."} }
"""
from __future__ import annotations
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finance import mcp_tools as T  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "finanzas-ingesta", "version": "1.0.0"}


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(req):
    """Procesa un mensaje JSON-RPC. Devuelve el dict de respuesta o None (notificaciones)."""
    method = req.get("method")
    id_ = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return _result(id_, {"protocolVersion": PROTOCOL_VERSION,
                             "capabilities": {"tools": {}},
                             "serverInfo": SERVER_INFO})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _result(id_, {"tools": T.list_tools()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            out = T.call_tool(name, args)
            return _result(id_, {"content": [{"type": "text",
                                              "text": json.dumps(out, ensure_ascii=False, default=str)}]})
        except T.MCPError as e:
            # error de uso (validación/confirmación): resultado con isError, no fallo de protocolo
            return _result(id_, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        except Exception as e:  # noqa: BLE001
            return _result(id_, {"content": [{"type": "text", "text": f"error interno: {e}"}],
                                "isError": True})
    if id_ is not None:
        return _error(id_, -32601, f"método no soportado: {method}")
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_error(None, -32700, "JSON inválido")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
