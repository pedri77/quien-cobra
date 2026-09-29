# ¿Quién cobra las subvenciones públicas?

Observatorio de las **concesiones de subvenciones y ayudas públicas en España** a partir de la
Base de Datos Nacional de Subvenciones (BDNS), que gestiona la Intervención General de la
Administración del Estado (IGAE, Ministerio de Hacienda) y publica en
[infosubvenciones.es](https://www.infosubvenciones.es/). Quién concede, con qué finalidad, a qué tipo
de entidad y cuánto se concentra el dinero.

- **Web:** <https://pedri77.github.io/quien-cobra/>
- **Vídeo:** episodio `cci-09` de la serie [«Construido con IA»](https://iacedemy.com/free/yt/construido-con-ia/?utm_source=github&utm_medium=readme&utm_campaign=construido-con-ia&utm_content=quien-cobra) de IAcademy.
- **Plantilla para hacer el tuyo:** [observatorio-datos-plantilla](https://github.com/pedri77/observatorio-datos-plantilla).

## Léelo antes que las cifras

**Recibir una subvención no es indicio de nada.** Una subvención es dinero público concedido con un
procedimiento reglado (Ley 38/2003, General de Subvenciones) y publicado por obligación legal.
Aparecer en la BDNS significa exactamente eso: que se concedió y se registró. No dice nada sobre si
la ayuda estaba bien o mal dada, ni sobre quién la pidió, ni sobre cómo se gastó.

**Los primeros puestos del ranking no «ganan» ese dinero.** Universidades, ayuntamientos, diputaciones,
organismos públicos, fundaciones o entidades como el CSIC o Cruz Roja aparecen arriba porque
**gestionan dinero finalista**: lo reciben para ejecutar un programa (investigación, empleo, servicios
sociales, obras) y lo tienen que justificar céntimo a céntimo. No lo ingresan como beneficio. Un
ayuntamiento que recibe fondos para un plan de empleo no es «el que más cobra»; es el que más
reparte por encargo de otra administración.

**Un importe alto puede ser un préstamo o un aval.** La BDNS incluye subvenciones dinerarias, pero
también préstamos, garantías y ventajas fiscales. Por eso se publican dos cifras: el `importe` y la
`ayuda_equivalente` (lo que vale la ayuda como subvención pura). En un préstamo, la segunda es una
fracción pequeña de la primera.

**Comunidad autónoma concedente no es territorio del beneficiario.** El desglose por comunidades mide
quién concede, no dónde está quien recibe.

Aquí se dice «concentración», «atípico», «merece revisión». Nunca acusaciones. Si crees que un dato
está mal, abre un *issue*: la BDNS se corrige a posteriori y este observatorio también.

## Ninguna persona física

Regla editorial de la serie, no negociable: **no se publica ningún dato de personas**.

La BDNS ya anonimiza a los beneficiarios que son personas físicas: en lugar del NIF y el nombre
publica el literal `PF`. Aun así, el parser (`bdns_fetch.py` y `bdns_build.py`) sólo agrega concesiones
cuyo beneficiario tiene NIF de **entidad jurídica** (letras A, B, C, D, E, F, G, J, N, P, Q, R, S, U,
V, W según la Orden EHA/451/2008). Todo lo demás se descarta antes de agregar: personas físicas, DNI o
NIE que pudieran colarse sin enmascarar, comunidades de propietarios (letra H, porque detrás hay
vecinos), identificadores no españoles y registros sin beneficiario. No se guardan nombres, NIF,
seudónimos ni *hashes* de personas: sólo **cuántas concesiones quedan fuera y cuánto suman**, para que
se sepa qué parte de la foto falta.

Lo que queda fuera por esta decisión, leído de `data/resumen.json` (`excluidas`) en el momento de
escribir esto:

| Ejercicio | Concesiones a personas físicas excluidas | Importe excluido |
|---|---|---|
| 2024 | 14.801 (1,2 % de los registros leídos) | 1.319 M€ |
| 2025 | 14.498.849 (93,6 % de los registros leídos) | 11.555 M€ |

El salto entre 2024 y 2025 no es un error del parser; ver «Dos avisos sobre los ejercicios».

## Qué sale de los datos (2024, ejercicio cerrado)

Cifras de `data/resumen.json` y `data/concentracion.json`, sólo entidades jurídicas:

- **40.749 M€** concedidos en **1.189.396 concesiones** a **356.572 entidades** distintas.
- **Índice de Gini 0,91** sobre el importe acumulado por entidad.
- El **1 % de las entidades** (3.565) recibe el **58 %** del dinero; el 10 % recibe el 87 %.
- **Mediana por entidad: 7.700 €.** La media (114.281 €) no describe a nadie.

Los datos de 2025 están en los mismos ficheros, con las cautelas de abajo.

## Dos avisos sobre los ejercicios

**1. Los ejercicios recientes crecen porque el registro llega tarde.** Cada ejercicio se corta por
*fecha de concesión*, pero los órganos concedentes registran la concesión en la BDNS meses o años
después. Según `registrado_en_anio` en `data/resumen.json`, de las concesiones a entidades de 2024 el
**36 %** (por número y por importe) se dio de alta en 2025 o 2026. El ejercicio 2025 seguirá
engordando durante 2026 y 2027; comparar ejercicios con la misma antigüedad de registro es la única
comparación honesta, y aun así aproximada.

**2. 2024 y 2025 no son comparables en número de concesiones.** En 2025 la fuente empezó a publicar
de forma masiva ayudas a particulares (los 14,5 millones de registros `PF` de la tabla de arriba,
frente a 14.801 en 2024). Como este observatorio descarta a las personas físicas, ese cambio no
afecta a los importes agregados por entidad, pero sí a cualquier lectura del total de registros de la
BDNS y a la proporción entidad/persona. No se puede decir «las subvenciones se han multiplicado» a
partir de estas cifras.

Además, la propia API advierte de que la información «es de naturaleza dinámica» y «puede ser
sometida a correcciones, inserciones, modificaciones y eliminaciones» después de su extracción. Cada
fichero lleva la fecha `generado`.

## Reproducirlo

Todo usa la biblioteca estándar de Python (3.11+) y la API pública de la BDNS, sin clave.

```bash
# 1. Descarga las concesiones de un ejercicio a raw/concesiones/<año>/<tipo>/<día>.jsonl.gz
#    (trozos por tipo de administración y día; se cachean, se puede interrumpir y reanudar).
#    Tarda horas: la paginación de la API es por offset y se degrada.
python3 scripts/bdns_fetch.py --year 2024 --workers 4
python3 scripts/bdns_fetch.py --year 2025 --workers 4

# 2. Carga el crudo en work/bdns.sqlite, clasifica beneficiarios y cuenta lo excluido
python3 scripts/bdns_build.py load 2024 2025

# 3. Descarga el detalle de cada convocatoria (finalidad, MRR, tipos de beneficiario previstos)
python3 scripts/bdns_build.py convocatorias

# 4. Genera los agregados públicos en data/
python3 scripts/bdns_build.py aggregate 2024 2025

# 5. Copia data/*.json a site/data/ y sirve la web
python3 -m http.server -d site 8000   # http://localhost:8000
```

`raw/` y `work/` no se versionan (`.gitignore`): el crudo pesa gigabytes y se regenera desde la
fuente. Lo que se publica es sólo `data/`, y la web (`site/index.html`, HTML, CSS y JavaScript sin
dependencias) lee esos mismos JSON desde `site/data/`.

| Fichero | Contenido |
|---|---|
| `data/resumen.json` | Por ejercicio: concesiones, importe y ayuda equivalente a entidades; excluidas por motivo; registros leídos, duplicados, importes nulos y negativos; cuadre; reparto por instrumento; año de alta en la BDNS; concesiones por mes |
| `data/organos.json` | Quién concede: nivel de administración, ministerios, comunidades autónomas, 50 mayores entidades locales y 50 mayores órganos |
| `data/finalidades.json` | Política de gasto de la convocatoria (Industria y Energía, I+D+i, Empleo…) y parte financiada por el MRR / Next Generation EU |
| `data/beneficiarios_tipo.json` | Tipo de entidad deducido de la letra del NIF, por familias y por letra |
| `data/top_beneficiarios.json` | 100 mayores entidades por importe en el ejercicio, con contraste frente a la lista oficial de «grandes beneficiarios» de la BDNS y enlace de verificación al buscador público |
| `data/concentracion.json` | Gini, mediana, media y cuota del top 10/100/1000 y del 1 %, 10 % y 50 % de entidades |
| `data/sources.json` | Endpoints usados, parámetros, licencia y las decisiones editoriales del parser |

Cada indicador lleva su `meta` (fuente, descripción, periodo, unidad y URL del endpoint).

## Fuentes y licencia

- **Datos:** Base de Datos Nacional de Subvenciones – Sistema Nacional de Publicidad de Subvenciones y
  Ayudas Públicas (SNPSAP), Intervención General de la Administración del Estado, Ministerio de
  Hacienda. API pública `https://www.infosubvenciones.es/bdnstrans/api/` (concesiones, convocatorias,
  grandes beneficiarios). Reutilización autorizada para fines comerciales y no comerciales con las
  condiciones del [aviso legal del SNPSAP](https://www.infosubvenciones.es/bdnstrans/GE/es/avisolegal)
  y de la Ley 37/2007 sobre reutilización de la información del sector público.

  **Origen de los datos: Intervención General de la Administración del Estado.** Fecha de la última
  extracción: campo `generado` de cada fichero de `data/`. Los datos han sido agregados y las
  concesiones a personas físicas han sido disociadas por el autor de este repositorio, no por la
  IGAE. La IGAE no participa, patrocina ni apoya este observatorio.

- **Clasificación de entidades:** letra del NIF según la Orden EHA/451/2008.

Código bajo **MIT** (`LICENSE`). Los agregados de `data/` se redistribuyen con la atribución de arriba.
