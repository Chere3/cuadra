# -*- coding: utf-8 -*-
"""config.py — Carga configuración y resuelve rutas del proyecto."""
from __future__ import annotations
import os
import functools
import yaml

def project_root() -> str:
    env = os.environ.get("FINANCE_ROOT")
    if env:
        return os.path.abspath(env)
    # raíz = carpeta que contiene 'config/' subiendo desde este archivo
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "config")) and os.path.isdir(os.path.join(d, "src")):
            return d
        d = os.path.dirname(d)
    # fallback: dos niveles arriba de src/finance
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def path(*parts) -> str:
    return os.path.join(project_root(), *parts)


@functools.lru_cache(maxsize=None)
def _load(name):
    # accounts.yml y merchant_rules.yml describen datos del usuario y están fuera de Git; en un
    # clone limpio se usa la versión *.example.yml para que CLI y tests funcionen sin configurar.
    p = path("config", name)
    if not os.path.exists(p):
        ejemplo = path("config", name[:-4] + ".example.yml")
        if os.path.exists(ejemplo):
            p = ejemplo
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def institutions():
    return _load("institutions.yml")["institutions"]

def accounts():
    return _load("accounts.yml")["accounts"]

def own_transfer_group():
    return _load("accounts.yml").get("own_transfer_group", [])

def titular():
    """Nombre del titular, si está configurado. Sirve como señal de transferencia entre cuentas
    propias (regla 26): muchos bancos rotulan el traspaso con el nombre del dueño en vez del banco
    de origen. Vacío = la señal simplemente no se usa."""
    return (_load("accounts.yml").get("titular") or "").strip()

def categories():
    return _load("categories.yml")

def merchant_rules():
    return _load("merchant_rules.yml")["rules"]

def policy():
    return _load("import_policy.yml")

def account_by_id(aid):
    for a in accounts():
        if a["id"] == aid:
            return a
    return None

def reset_cache():
    _load.cache_clear()
