#!/usr/bin/env python3
"""Convierte el crudo de la BDNS (raw/) en los agregados públicos (data/*.json).

Etapas (se pueden encadenar): load | convocatorias | aggregate
  python3 scripts/bdns_build.py load 2024 2025
  python3 scripts/bdns_build.py convocatorias
  python3 scripts/bdns_build.py aggregate 2024 2025

Decisiones editoriales (ver data/sources.json e informe):
- Sólo se agregan concesiones a ENTIDADES JURÍDICAS (NIF con letra A B C D E F G J N P Q R S U V W).
  Personas físicas (NIF enmascarado ***1234** o DNI/NIE), comunidades de propietarios (H) e
  identificadores no españoles se DESCARTAN antes de agregar; sólo se cuenta cuántas son y cuánto suman.
- La finalidad (política de gasto) no viene en la concesión: se toma de la convocatoria.
- Se cuentan y publican duplicados, importes nulos y negativos.
"""
from __future__ import annotations

import glob
import gzip
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, WORK, DATA = ROOT / "raw", ROOT / "work", ROOT / "data"
DB = WORK / "bdns.sqlite"
API = "https://www.infosubvenciones.es/bdnstrans/api"
UA = "quien-cobra/1.0 (observatorio ciudadano; github.com/pedri77/quien-cobra)"
LETRAS_JURIDICAS = set("ABCDEFGJNPQRSUVW")
GRUPO_NIF = {
    "A": "Sociedades anónimas", "B": "Sociedades limitadas", "C": "Sociedades colectivas",
    "D": "Sociedades comanditarias", "U": "Uniones temporales de empresas",
    "E": "Comunidades de bienes", "J": "Sociedades civiles", "V": "Otras entidades",
    "N": "Entidades extranjeras", "W": "Establecimientos permanentes de no residentes",
    "F": "Cooperativas", "G": "Asociaciones, fundaciones y otras sin ánimo de lucro",
    "P": "Ayuntamientos y otras corporaciones locales", "Q": "Organismos públicos, universidades y consorcios",
    "S": "Órganos de la Administración del Estado y de las CCAA", "R": "Entidades religiosas",
}
FAMILIA_NIF = {
    "A": "Empresas", "B": "Empresas", "C": "Empresas", "D": "Empresas", "U": "Empresas", "E": "Empresas",
    "J": "Empresas", "V": "Empresas", "N": "Entidades extranjeras", "W": "Entidades extranjeras",
    "F": "Cooperativas", "G": "Sin ánimo de lucro", "R": "Sin ánimo de lucro",
    "P": "Sector público", "Q": "Sector público", "S": "Sector público",
}
NIVEL1 = {"ESTADO": "Estado", "AUTONOMICA": "Comunidades autónomas", "LOCAL": "Entidades locales", "OTROS": "Otros"}


def clasificar(b: str | None) -> tuple[str, str | None, str | None]:
    """-> (motivo_o_'ok', nif, nombre)."""
    if not b:
        return "sin_beneficiario", None, None
    # La propia BDNS ya anonimiza a las personas físicas: en vez del NIF y el nombre publica
    # el literal «PF» (a veces con asteriscos). No llega ningún dato personal al fichero.
    if b.strip().upper() == "PF" or b.startswith("*"):
        return "persona_fisica", None, None
    ident, _, nombre = b.partition(" ")
    if len(ident) == 9 and ident[0] in LETRAS_JURIDICAS and ident[1:8].isdigit() and ident[8].isalnum():
        return "ok", ident, nombre.strip()
    if len(ident) == 9 and ident[0] == "H" and ident[1:8].isdigit():
        return "comunidad_propietarios_H", None, None
    if len(ident) == 9 and (ident[:8].isdigit() or (ident[0] in "XYZ" and ident[1:8].isdigit())):
        return "persona_fisica", None, None
    return "identificador_no_espanol", None, None


def open_db() -> sqlite3.Connection:
    WORK.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF;
    CREATE TABLE IF NOT EXISTS concesion(id INTEGER PRIMARY KEY, anio INT, fecha TEXT, tipo_admin TEXT,
      nivel1 TEXT, nivel2 TEXT, nivel3 TEXT, num_conv TEXT, instrumento TEXT, importe REAL, ayuda_eq REAL,
      nif TEXT, nombre TEXT, fecha_alta TEXT);
    CREATE TABLE IF NOT EXISTS excluida(anio INT, tipo_admin TEXT, nivel1 TEXT, nivel2 TEXT, num_conv TEXT,
      instrumento TEXT, motivo TEXT, n INT, importe REAL, PRIMARY KEY(anio,tipo_admin,nivel1,nivel2,num_conv,instrumento,motivo));
    CREATE TABLE IF NOT EXISTS carga(anio INT PRIMARY KEY, leidas INT, duplicadas INT, importe_nulo INT,
      importe_negativo INT, importe_negativo_suma REAL, ficheros INT, cargado TEXT);
    CREATE TABLE IF NOT EXISTS convocatoria(num TEXT PRIMARY KEY, finalidad TEXT, tipo TEXT, mrr INT,
      presupuesto REAL, tipos_beneficiarios TEXT, sectores TEXT, regiones TEXT, http INT);
    CREATE INDEX IF NOT EXISTS ix_c_anio ON concesion(anio);
    CREATE INDEX IF NOT EXISTS ix_c_nif ON concesion(anio, nif);
    CREATE INDEX IF NOT EXISTS ix_c_conv ON concesion(num_conv);
    """)
    return c


# ---------------------------------------------------------------- load
def load(years: list[int]) -> None:
    db = open_db()
    for y in years:
        files = sorted(glob.glob(str(RAW / "concesiones" / str(y) / "*" / "*.jsonl.gz")))
        if not files:
            raise SystemExit(f"no hay crudo para {y}; ejecuta bdns_fetch.py --year {y}")
        db.execute("DELETE FROM concesion WHERE anio=?", (y,))
        db.execute("DELETE FROM excluida WHERE anio=?", (y,))
        leidas = dup = nulo = neg = 0
        neg_sum = 0.0
        excl: dict[tuple, list] = defaultdict(lambda: [0, 0.0])
        rows: list[tuple] = []
        t0 = time.time()
        for i, fn in enumerate(files):
            tipo = Path(fn).parent.name
            with gzip.open(fn, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    leidas += 1
                    imp = r.get("importe")
                    if imp is None:
                        nulo += 1
                        imp = 0.0
                    elif imp < 0:
                        neg += 1
                        neg_sum += imp
                    motivo, nif, nombre = clasificar(r.get("beneficiario"))
                    if motivo != "ok":
                        k = (y, tipo, r["nivel1"], r["nivel2"], r["numeroConvocatoria"], (r["instrumento"] or "").strip(), motivo)
                        e = excl[k]
                        e[0] += 1
                        e[1] += imp
                        continue
                    rows.append((r["id"], y, r["fechaConcesion"], tipo, r["nivel1"], r["nivel2"], r["nivel3"],
                                 r["numeroConvocatoria"], (r["instrumento"] or "").strip(), imp, r.get("ayudaEquivalente"),
                                 nif, nombre, r.get("fechaAlta")))
            if len(rows) > 200_000 or i == len(files) - 1:
                before = db.execute("SELECT count(*) FROM concesion WHERE anio=?", (y,)).fetchone()[0]
                db.executemany("INSERT OR IGNORE INTO concesion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                after = db.execute("SELECT count(*) FROM concesion WHERE anio=?", (y,)).fetchone()[0]
                dup += len(rows) - (after - before)
                rows = []
                db.commit()
                print(f"  {y} {i + 1}/{len(files)} ficheros, {leidas} leídas, {after} entidades, {time.time() - t0:.0f}s")
        db.executemany("INSERT INTO excluida VALUES (?,?,?,?,?,?,?,?,?)",
                       [(*k, v[0], round(v[1], 2)) for k, v in excl.items()])
        db.execute("INSERT OR REPLACE INTO carga VALUES (?,?,?,?,?,?,?,?)",
                   (y, leidas, dup, nulo, neg, round(neg_sum, 2), len(files), datetime.now(timezone.utc).isoformat()))
        db.commit()
        print(f"OK load {y}: leídas={leidas} dup={dup} nulo={nulo} neg={neg}")


# ---------------------------------------------------------------- convocatorias
def fetch_conv(num: str) -> dict:
    dest = RAW / "convocatorias" / f"{num}.json"
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{API}/convocatorias?" + urllib.parse.urlencode({"vpd": "GE", "numConv": num})
    for i in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read())
                d["_http"] = 200
                break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                d = {"_http": 404}
                break
            time.sleep(3 * 2**i)
        except Exception:  # noqa: BLE001
            time.sleep(3 * 2**i)
    else:
        d = {"_http": 0}
    d.pop("documentos", None)
    d.pop("anuncios", None)
    dest.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def convocatorias(workers: int = 6) -> None:
    db = open_db()
    nums = [r[0] for r in db.execute(
        "SELECT DISTINCT num_conv FROM concesion WHERE num_conv NOT IN (SELECT num FROM convocatoria) "
        "UNION SELECT DISTINCT num_conv FROM excluida WHERE num_conv NOT IN (SELECT num FROM convocatoria)")]
    print(f"convocatorias pendientes: {len(nums)}")
    t0 = time.time()
    batch: list[tuple] = []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_conv, n): n for n in nums}
        for k, fu in enumerate(as_completed(futs), 1):
            n, d = futs[fu], fu.result()
            batch.append((n, d.get("descripcionFinalidad"), d.get("tipoConvocatoria"), int(bool(d.get("mrr"))),
                          d.get("presupuestoTotal"), json.dumps([t["descripcion"] for t in d.get("tiposBeneficiarios") or []], ensure_ascii=False),
                          json.dumps([s.get("codigo") for s in d.get("sectores") or []]),
                          json.dumps([s.get("descripcion") for s in d.get("regiones") or []], ensure_ascii=False), d.get("_http", 0)))
            if len(batch) >= 500 or k == len(nums):
                db.executemany("INSERT OR REPLACE INTO convocatoria VALUES (?,?,?,?,?,?,?,?,?)", batch)
                db.commit()
                batch = []
                print(f"  {k}/{len(nums)} {time.time() - t0:.0f}s")
    print("OK convocatorias")


# ---------------------------------------------------------------- aggregate
def meta(desc: str, period: str, unit: str, url: str = f"{API}/concesiones/busqueda", **extra) -> dict:
    return {"source": "BDNS – Sistema Nacional de Publicidad de Subvenciones y Ayudas Públicas (IGAE, Ministerio de Hacienda)",
            "description": desc, "period": period, "url": url, "unit": unit, **extra}


def pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 2) if b else None


def aggregate(years: list[int]) -> None:
    db = open_db()
    DATA.mkdir(exist_ok=True)
    q = lambda sql, *p: db.execute(sql, p).fetchall()  # noqa: E731
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    out_resumen, out_organos, out_fin, out_tipo, out_top, out_conc = {}, {}, {}, {}, {}, {}
    for y in years:
        per = str(y)
        n, imp, aeq = q("SELECT count(*), sum(importe), sum(coalesce(ayuda_eq,0)) FROM concesion WHERE anio=?", y)[0]
        carga = q("SELECT leidas, duplicadas, importe_nulo, importe_negativo, importe_negativo_suma, ficheros FROM carga WHERE anio=?", y)[0]
        excl = {m: {"n": a, "importe": round(b, 2)} for m, a, b in
                q("SELECT motivo, sum(n), sum(importe) FROM excluida WHERE anio=? GROUP BY motivo", y)}
        n_excl = sum(v["n"] for v in excl.values())
        imp_excl = sum(v["importe"] for v in excl.values())
        instr = [{"instrumento": i, "n": a, "importe": round(b, 2)} for i, a, b in
                 q("SELECT instrumento, count(*), sum(importe) FROM concesion WHERE anio=? GROUP BY 1 ORDER BY 3 DESC", y)]
        # Cobertura temporal: cuándo se registró (fechaAlta) lo concedido en el ejercicio
        alta = q("SELECT substr(fecha_alta,1,4), count(*), sum(importe) FROM concesion WHERE anio=? GROUP BY 1 ORDER BY 1", y)
        meses = [{"mes": m, "n": a, "importe": round(b, 2)} for m, a, b in
                 q("SELECT substr(fecha,1,7), count(*), sum(importe) FROM concesion WHERE anio=? GROUP BY 1 ORDER BY 1", y)]
        benef = q("SELECT count(DISTINCT nif) FROM concesion WHERE anio=?", y)[0][0]
        out_resumen[per] = {
            "entidades": {"concesiones": n, "importe": round(imp, 2), "ayuda_equivalente": round(aeq, 2),
                          "beneficiarios_distintos": benef, "convocatorias_distintas": q("SELECT count(DISTINCT num_conv) FROM concesion WHERE anio=?", y)[0][0]},
            "excluidas": {"total": {"n": n_excl, "importe": round(imp_excl, 2)}, "por_motivo": excl},
            "registros_leidos": carga[0], "duplicados_ignorados": carga[1], "importe_nulo": carga[2],
            "importe_negativo": {"n": carga[3], "suma": carga[4]},
            "cuadre": {"leidos_igual_entidades_mas_excluidas_mas_dup": carga[0] == n + n_excl + carga[1]},
            "por_instrumento": instr,
            "registrado_en_anio": [{"anio_alta": a or "desconocido", "n": b, "importe": round(c, 2)} for a, b, c in alta],
            "por_mes_concesion": meses,
        }
        # Órganos
        def organo(sql, *p):
            return [{"organo": o, "n": a, "importe": round(b, 2), "pct_importe": pct(b, imp)} for o, a, b in q(sql, *p)]
        out_organos[per] = {
            "nivel1": [{"nivel1": NIVEL1.get(k, k), "n": a, "importe": round(b, 2), "pct_importe": pct(b, imp)} for k, a, b in
                       q("SELECT nivel1, count(*), sum(importe) FROM concesion WHERE anio=? GROUP BY 1 ORDER BY 3 DESC", y)],
            "ministerios": organo("SELECT nivel2, count(*), sum(importe) FROM concesion WHERE anio=? AND nivel1='ESTADO' GROUP BY 1 ORDER BY 3 DESC", y),
            "comunidades": organo("SELECT nivel2, count(*), sum(importe) FROM concesion WHERE anio=? AND nivel1='AUTONOMICA' GROUP BY 1 ORDER BY 3 DESC", y),
            "entidades_locales_top50": organo("SELECT nivel3, count(*), sum(importe) FROM concesion WHERE anio=? AND nivel1='LOCAL' GROUP BY 1 ORDER BY 3 DESC LIMIT 50", y),
            "organos_top50": [{"nivel1": NIVEL1.get(k1, k1), "nivel2": k2, "organo": k3, "n": a, "importe": round(b, 2), "pct_importe": pct(b, imp)} for k1, k2, k3, a, b in
                              q("SELECT nivel1, nivel2, nivel3, count(*), sum(importe) FROM concesion WHERE anio=? GROUP BY 1,2,3 ORDER BY 5 DESC LIMIT 50", y)],
        }
        # Finalidad (de la convocatoria)
        fin = q("SELECT coalesce(v.finalidad, CASE WHEN v.num IS NULL THEN 'CONVOCATORIA NO RECUPERADA' ELSE 'INFORMACIÓN NO DISPONIBLE' END), "
                "count(*), sum(c.importe), count(DISTINCT c.num_conv) FROM concesion c LEFT JOIN convocatoria v ON v.num=c.num_conv "
                "WHERE c.anio=? GROUP BY 1 ORDER BY 3 DESC", y)
        out_fin[per] = [{"finalidad": f, "n": a, "importe": round(b, 2), "pct_importe": pct(b, imp), "convocatorias": c} for f, a, b, c in fin]
        mrr = q("SELECT count(*), sum(c.importe) FROM concesion c JOIN convocatoria v ON v.num=c.num_conv WHERE c.anio=? AND v.mrr=1", y)[0]
        out_fin[per + "_mrr"] = {"n": mrr[0], "importe": round(mrr[1] or 0, 2), "pct_importe": pct(mrr[1] or 0, imp)}
        # Tipo de beneficiario por letra de NIF
        letras = q("SELECT substr(nif,1,1), count(*), sum(importe), count(DISTINCT nif) FROM concesion WHERE anio=? GROUP BY 1 ORDER BY 3 DESC", y)
        fam: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
        for l, a, b, c in letras:
            f = fam[FAMILIA_NIF[l]]
            f[0] += a; f[1] += b; f[2] += c  # noqa: E702
        out_tipo[per] = {
            "familias": [{"familia": k, "n": v[0], "importe": round(v[1], 2), "pct_importe": pct(v[1], imp), "beneficiarios": v[2]}
                         for k, v in sorted(fam.items(), key=lambda kv: -kv[1][1])],
            "letras": [{"letra": l, "descripcion": GRUPO_NIF[l], "n": a, "importe": round(b, 2), "pct_importe": pct(b, imp), "beneficiarios": c} for l, a, b, c in letras],
        }
        # Top beneficiarios (sólo entidades jurídicas) + contraste con la lista oficial de grandes beneficiarios
        grandes = {}
        gp = RAW / "grandes" / f"{y}.jsonl.gz"
        if gp.exists():
            with gzip.open(gp, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    m, nif, _ = clasificar(r["beneficiario"])
                    if m == "ok":
                        grandes[nif] = r["ayudaETotal"]
        top = q("SELECT nif, count(*), sum(importe), sum(coalesce(ayuda_eq,0)), count(DISTINCT num_conv), count(DISTINCT nivel3), count(DISTINCT nombre) "
                "FROM concesion WHERE anio=? GROUP BY nif ORDER BY 3 DESC LIMIT 100", y)
        out_top[per] = []
        for nif, a, b, c, d, e, nn in top:
            # Nombre más frecuente para ese NIF (un mismo NIF aparece con distintos nombres: CSIC, Cruz Roja, ministerios…)
            nom = q("SELECT nombre FROM concesion WHERE anio=? AND nif=? GROUP BY nombre ORDER BY count(*) DESC, sum(importe) DESC LIMIT 1", y, nif)[0][0]
            out_top[per].append({"nif": nif, "nombre": nom, "nombres_distintos": nn, "tipo": GRUPO_NIF[nif[0]], "concesiones": a,
                                 "importe": round(b, 2), "ayuda_equivalente": round(c, 2), "pct_total": pct(b, imp),
                                 "convocatorias": d, "organos": e, "bdns_grandes_beneficiarios_ayuda_eq": grandes.get(nif),
                                 "url_verificacion": f"https://www.infosubvenciones.es/bdnstrans/GE/es/concesiones?beneficiario={nif}"})
        # Concentración
        sums = [r[0] for r in q("SELECT sum(importe) FROM concesion WHERE anio=? GROUP BY nif ORDER BY 1 DESC", y)]
        nb = len(sums)
        share = {}
        for k, name in ((10, "top_10"), (100, "top_100"), (1000, "top_1000")):
            share[name] = pct(sum(sums[:k]), imp)
        for p, name in ((0.01, "top_1pct"), (0.10, "top_10pct"), (0.50, "top_50pct")):
            k = max(1, int(nb * p))
            share[name] = {"beneficiarios": k, "pct_importe": pct(sum(sums[:k]), imp)}
        # Gini sobre importes acumulados por beneficiario (positivos)
        pos = sorted(s for s in sums if s > 0)
        npos = len(pos)
        cum_sum = 0.0
        gini_num = 0.0
        for i, s in enumerate(pos, 1):
            cum_sum += s
            gini_num += i * s
        gini = round((2 * gini_num) / (npos * cum_sum) - (npos + 1) / npos, 4) if npos else None
        mediana = pos[npos // 2] if npos else None
        out_conc[per] = {"beneficiarios": nb, "importe_total": round(imp, 2), "gini": gini, "mediana_por_beneficiario": round(mediana, 2) if mediana else None,
                         "media_por_beneficiario": round(imp / nb, 2) if nb else None, **share}

    common = {"generado": gen, "ejercicios": [str(y) for y in years], "ambito": "Concesiones a entidades jurídicas (NIF de entidad). Personas físicas excluidas a propósito."}
    write("resumen.json", {**common, "datos": out_resumen, "meta": {
        "entidades.concesiones": meta("Número de concesiones a entidades jurídicas registradas en la BDNS, por fecha de concesión", "ejercicio", "concesiones"),
        "entidades.importe": meta("Suma del campo importe de esas concesiones", "ejercicio", "EUR"),
        "entidades.ayuda_equivalente": meta("Suma del campo ayudaEquivalente (para préstamos/garantías, equivalente de subvención)", "ejercicio", "EUR"),
        "excluidas": meta("Concesiones descartadas antes de agregar: personas físicas (NIF enmascarado o DNI/NIE), comunidades de propietarios (H), identificadores no españoles", "ejercicio", "concesiones y EUR"),
        "registrado_en_anio": meta("Año de alta en la BDNS (fechaAlta) de las concesiones del ejercicio: mide el retraso de publicación", "ejercicio", "concesiones"),
        "por_instrumento": meta("Subvención dineraria, ventaja fiscal, préstamo, garantía…", "ejercicio", "concesiones y EUR"),
    }})
    write("organos.json", {**common, "datos": out_organos, "meta": {
        "nivel1": meta("Reparto por nivel de administración concedente (nivel1 de la BDNS)", "ejercicio", "EUR y %"),
        "ministerios": meta("Departamento ministerial (nivel2 cuando nivel1=ESTADO; incluye organismos adscritos)", "ejercicio", "EUR y %"),
        "comunidades": meta("Comunidad autónoma concedente (nivel2 cuando nivel1=AUTONOMICA). No es el territorio del beneficiario", "ejercicio", "EUR y %"),
        "entidades_locales_top50": meta("Entidad local concedente (nivel3 cuando nivel1=LOCAL)", "ejercicio", "EUR y %"),
        "organos_top50": meta("Órgano concedente concreto (nivel3)", "ejercicio", "EUR y %"),
    }})
    write("finalidades.json", {**common, "datos": out_fin, "meta": {
        "finalidad": meta("Política de gasto declarada en la convocatoria (descripcionFinalidad, /api/convocatorias?numConv=). Se asigna a cada concesión por su convocatoria", "ejercicio", "EUR y %", url=f"{API}/convocatorias"),
        "_mrr": meta("Concesiones cuya convocatoria está marcada como financiada por el Mecanismo de Recuperación y Resiliencia (Next Generation EU)", "ejercicio", "EUR y %", url=f"{API}/convocatorias"),
    }})
    write("beneficiarios_tipo.json", {**common, "datos": out_tipo, "meta": {
        "familias": meta("Tipo de entidad deducido de la letra inicial del NIF (Orden EHA/451/2008). La BDNS no publica el tipo real del beneficiario en la concesión", "ejercicio", "EUR, % y beneficiarios distintos"),
        "letras": meta("Desglose por letra de NIF", "ejercicio", "EUR, % y beneficiarios distintos"),
    }})
    write("top_beneficiarios.json", {**common, "datos": out_top, "meta": {
        "importe": meta("Suma de importes concedidos en el ejercicio a cada NIF de entidad jurídica (100 mayores). Sólo entidades: nunca personas físicas", "ejercicio", "EUR"),
        "bdns_grandes_beneficiarios_ayuda_eq": meta("Contraste: ayuda equivalente total que la BDNS publica para ese NIF en su lista oficial de grandes beneficiarios (>100.000 €/año)", "ejercicio", "EUR", url=f"{API}/grandesbeneficiarios/busqueda"),
        "url_verificacion": meta("Buscador público de concesiones de la BDNS filtrado por NIF", "ejercicio", "URL", url="https://www.infosubvenciones.es/bdnstrans/GE/es/concesiones"),
    }})
    write("concentracion.json", {**common, "datos": out_conc, "meta": {
        "top_1pct": meta("Porcentaje del importe total que acumula el 1 % de beneficiarios (entidades jurídicas) con más importe", "ejercicio", "%"),
        "gini": meta("Índice de Gini de los importes acumulados por beneficiario (0 = reparto igual, 1 = todo a uno). Sólo importes positivos", "ejercicio", "índice 0-1"),
        "top_10": meta("Porcentaje del importe total que acumulan los 10 mayores beneficiarios", "ejercicio", "%"),
    }})
    write("sources.json", {
        "generado": gen,
        "fuentes": [
            {"id": "bdns_concesiones", "nombre": "BDNS – API de concesiones", "url": f"{API}/concesiones/busqueda",
             "parametros": "vpd=GE, fechaDesde/fechaHasta (dd/mm/aaaa, sobre fecha de concesión), tipoAdministracion (C/A/L/O), page, pageSize=10000",
             "licencia": "Datos abiertos con las condiciones de reutilización del SNPSAP (campo 'advertencia' de la API)", "periodo": [str(y) for y in years]},
            {"id": "bdns_convocatorias", "nombre": "BDNS – detalle de convocatoria (finalidad, MRR, tipos de beneficiario previstos)", "url": f"{API}/convocatorias?vpd=GE&numConv="},
            {"id": "bdns_grandes", "nombre": "BDNS – grandes beneficiarios (>100.000 € de ayuda equivalente en el ejercicio)", "url": f"{API}/grandesbeneficiarios/busqueda?vpd=GE&anios="},
            {"id": "bdns_portal", "nombre": "Portal público", "url": "https://www.infosubvenciones.es/bdnstrans/GE/es/concesiones"},
        ],
        "decisiones": [
            "Sólo se agregan concesiones cuyo beneficiario tiene NIF de entidad jurídica (letras A B C D E F G J N P Q R S U V W).",
            "Las concesiones a personas físicas se descartan en el parser: no se guardan nombres, NIF, seudónimos ni hashes. Sólo se cuenta cuántas son y cuánto suman.",
            "Ante la duda (letra H, identificadores no españoles, campo vacío) se descarta y se cuenta aparte.",
            "El importe es el campo 'importe' de la concesión (EUR). 'ayudaEquivalente' se publica aparte.",
            "La BDNS no marca anulaciones ni reintegros en la API de concesiones; los importes negativos se cuentan y se mantienen.",
            "Los ejercicios se cortan por fecha de concesión; el registro puede llegar meses o años después (ver registrado_en_anio).",
        ],
    })
    print("OK aggregate")


def write(name: str, obj: dict) -> None:
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  data/{name} {(DATA / name).stat().st_size // 1024} KB")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    stage, years = sys.argv[1], [int(a) for a in sys.argv[2:]]
    if stage == "load":
        load(years)
    elif stage == "convocatorias":
        convocatorias()
    elif stage == "aggregate":
        aggregate(years)
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
