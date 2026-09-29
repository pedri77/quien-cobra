#!/usr/bin/env python3
"""Descarga las concesiones de la BDNS (Base de Datos Nacional de Subvenciones) por ejercicio.

API pública, sin clave: https://www.infosubvenciones.es/bdnstrans/api/concesiones/busqueda
Parámetros usados (ver informe): vpd=GE, fechaDesde/fechaHasta (dd/mm/aaaa, sobre fechaConcesion),
tipoAdministracion (C=Estado, A=autonómica, L=local, O=otros), page, pageSize (10000 funciona).
La paginación es por offset y se degrada con el offset (≈21 s en offset 1.000.000), por eso se
descarga en trozos pequeños: (tipoAdministracion, día). Cada trozo se cachea en
raw/concesiones/<año>/<tipo>/<fecha>.jsonl.gz y no se vuelve a pedir si ya existe.

PROTECCIÓN DE DATOS: los beneficiarios que son personas físicas (NIF enmascarado "***1234**" o
cualquier NIF que no sea de entidad jurídica) se escriben en el crudo SIN nombre ni NIF
(beneficiario="PF"), sólo con importe y órgano, para poder contar lo que se excluye.
El crudo no se versiona (raw/ está en .gitignore).

Uso: python3 scripts/bdns_fetch.py --year 2025 [--workers 4]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
API = "https://www.infosubvenciones.es/bdnstrans/api/concesiones/busqueda"
UA = "quien-cobra/1.0 (observatorio ciudadano; github.com/pedri77/quien-cobra)"
PAGE = 10000
TIPOS = ("C", "A", "L", "O")
# Letras de NIF de entidades jurídicas (Orden EHA/451/2008). H (comunidades de propietarios) queda
# fuera a propósito: ante la duda, se descarta. Todo lo demás se trata como persona física.
LETRAS_JURIDICAS = set("ABCDEFGJNPQRSUVW")


def es_juridica(beneficiario: str | None) -> bool:
    if not beneficiario or len(beneficiario) < 9:
        return False
    nif = beneficiario[:9]
    return nif[0] in LETRAS_JURIDICAS and nif[1:8].isdigit() and nif[8].isalnum() and beneficiario[9:10] == " "


def anonimizar(rec: dict) -> dict:
    """Persona física: se conserva importe, órgano y convocatoria; se elimina nombre/NIF/idPersona."""
    if es_juridica(rec.get("beneficiario")):
        return rec
    out = dict(rec)
    out["beneficiario"] = "PF"
    out["idPersona"] = None
    return out


def get(params: dict, tries: int = 6) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            wait = min(60, 5 * 2**i)
            print(f"  reintento {i + 1} {params} -> {e!r}; espero {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"fallo definitivo {params}")


def fetch_chunk(tipo: str, d: date) -> dict:
    dest = RAW / "concesiones" / str(d.year) / tipo / f"{d.isoformat()}.jsonl.gz"
    if dest.exists():
        return {"tipo": tipo, "dia": d.isoformat(), "cached": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n, pages, total = 0, 0, None
    tmp = dest.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        page = 0
        while True:
            fecha = d.strftime("%d/%m/%Y")
            r = get({"vpd": "GE", "page": page, "pageSize": PAGE, "fechaDesde": fecha,
                     "fechaHasta": fecha, "tipoAdministracion": tipo})
            total = r["totalElements"]
            for rec in r["content"]:
                f.write(json.dumps(anonimizar(rec), ensure_ascii=False) + "\n")
            n += r["numberOfElements"]
            pages += 1
            if r["last"] or r["numberOfElements"] == 0:
                break
            page += 1
    if total is not None and n != total:
        print(f"  AVISO {tipo} {d}: totalElements={total} pero recibidos {n}", file=sys.stderr)
    tmp.rename(dest)
    return {"tipo": tipo, "dia": d.isoformat(), "n": n, "total": total, "pages": pages,
            "segundos": round(time.time() - t0, 1)}


def fetch_grandes(year: int) -> Path:
    """Lista oficial de «grandes beneficiarios» (ayuda equivalente > 100.000 € en el ejercicio).
    Endpoint: /api/grandesbeneficiarios/busqueda?anios=AAAA. Se usa como contraste de los rankings."""
    dest = RAW / "grandes" / f"{year}.jsonl.gz"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = "https://www.infosubvenciones.es/bdnstrans/api/grandesbeneficiarios/busqueda"
    n, page = 0, 0
    with gzip.open(dest.with_suffix(".tmp"), "wt", encoding="utf-8") as f:
        while True:
            q = urllib.parse.urlencode({"vpd": "GE", "page": page, "pageSize": PAGE, "anios": year,
                                        "order": "ayudaETotal", "direccion": "desc"})
            req = urllib.request.Request(url + "?" + q, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            for rec in d["content"]:
                f.write(json.dumps(anonimizar(rec), ensure_ascii=False) + "\n")
            n += d["numberOfElements"]
            if d["last"]:
                break
            page += 1
    dest.with_suffix(".tmp").rename(dest)
    print(f"grandes beneficiarios {year}: {n} filas (totalElements={d['totalElements']})")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tipos", default=",".join(TIPOS))
    ap.add_argument("--solo-grandes", action="store_true")
    a = ap.parse_args()
    fetch_grandes(a.year)
    if a.solo_grandes:
        return 0
    d0, d1 = date(a.year, 1, 1), date(a.year, 12, 31)
    dias = [d0 + timedelta(i) for i in range((d1 - d0).days + 1)]
    jobs = [(t, d) for t in a.tipos.split(",") for d in dias]
    log = RAW / "concesiones" / str(a.year) / "fetch.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(a.workers) as ex, open(log, "a", encoding="utf-8") as lf:
        futs = [ex.submit(fetch_chunk, t, d) for t, d in jobs]
        for fu in as_completed(futs):
            r = fu.result()
            done += 1
            if not r.get("cached"):
                lf.write(json.dumps(r) + "\n")
                lf.flush()
                print(f"[{done}/{len(jobs)}] {r['tipo']} {r['dia']} n={r['n']} pags={r['pages']} {r['segundos']}s")
    print(f"OK {a.year}: {len(jobs)} trozos en {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
