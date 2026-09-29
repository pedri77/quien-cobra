# Checklist antes de publicar «Quién cobra»

Lista de comprobación adaptada a la BDNS. Cada punto es algo que hubo que vigilar de verdad en este
dataset. Los contadores que se citan viven en `data/resumen.json`; si alguno no cuadra, no se publica
(mejor los JSON de ayer que unos rotos).

## Personas físicas: cero datos

- [ ] `clasificar()` en `scripts/bdns_build.py` sigue devolviendo `persona_fisica` para el literal `PF`,
      para cualquier beneficiario que empiece por `*` y para DNI/NIE (8 dígitos o X/Y/Z + 7 dígitos).
- [ ] La letra H (comunidades de propietarios) sigue excluida: detrás hay vecinos.
- [ ] Ningún fichero de `data/` ni `site/data/` contiene un DNI, NIE, nombre de persona, seudónimo ni
      *hash*. Comprobación rápida: `grep -E '"nif": "[0-9XYZ]' data/*.json` debe devolver vacío.
- [ ] `excluidas.por_motivo.persona_fisica` existe para cada ejercicio, con `n` e `importe`, y la web
      dice cuánto queda fuera.
- [ ] `raw/` y `work/` siguen en `.gitignore` y no hay ningún crudo en el `git status`.

## Cuadre de la carga

- [ ] `cuadre.leidos_igual_entidades_mas_excluidas_mas_dup` es `true` en todos los ejercicios:
      `registros_leidos = entidades.concesiones + excluidas.total.n + duplicados_ignorados`.
- [ ] `registros_leidos` de cada ejercicio coincide con la suma de `n` del `fetch.log` de
      `raw/concesiones/<año>/` (ningún trozo día×tipo se quedó a medias; si la descarga se cortó,
      se relanza `bdns_fetch.py`, que sólo pide lo que falta).
- [ ] Los cuatro tipos de administración (C, A, L, O) tienen ficheros para los 365/366 días.
- [ ] `duplicados_ignorados`: la API puede devolver el mismo `id` de concesión más de una vez
      (concesiones modificadas o repetidas entre páginas por la ordenación inestable del *offset*). Se
      dedupe por `id` con `INSERT OR IGNORE` y se publica cuántas se ignoraron. Un valor alto de golpe
      es señal de paginación rota, no de dato nuevo.
- [ ] `importe_nulo` e `importe_negativo`: la API no marca anulaciones ni reintegros; los negativos se
      cuentan, se suman aparte y **se mantienen** en los agregados. Si aparecen, decirlo en la web. Los
      nulos se tratan como 0 y se cuentan.
- [ ] El Gini y la mediana se calculan sólo sobre importes acumulados positivos por entidad
      (`concentracion.meta.gini`); un ejercicio con muchos negativos exige revisar esa decisión.

## Beneficiarios

- [ ] Un mismo NIF aparece con varios nombres (CSIC, Cruz Roja, ministerios, universidades…). Se agrupa
      **siempre por NIF**, se publica el nombre más frecuente y `nombres_distintos` en
      `top_beneficiarios.json`. Nunca agrupar por nombre.
- [ ] Los identificadores no españoles (`identificador_no_espanol`) quedan fuera y contados: no se
      puede saber si son empresa o persona.
- [ ] El tipo de entidad sale de la **letra del NIF**, no de un campo de la BDNS; la web lo dice así.
- [ ] Contraste del top 100 con la lista oficial de «grandes beneficiarios» de la BDNS
      (`bdns_grandes_beneficiarios_ayuda_eq`): las cifras no tienen por qué coincidir (una es importe,
      la otra ayuda equivalente), pero una diferencia de orden de magnitud merece revisión.
- [ ] Cada fila del top lleva `url_verificacion` al buscador público, y el enlace funciona.

## Convocatorias y finalidad

- [ ] La finalidad no viene en la concesión: se toma de la convocatoria. Tras `bdns_build.py
      convocatorias`, la fila `CONVOCATORIA NO RECUPERADA` de `finalidades.json` debe ser residual.
      Si pesa, la descarga de convocatorias no ha terminado: no publicar el desglose por finalidad.
- [ ] `INFORMACIÓN NO DISPONIBLE` (convocatoria recuperada pero sin finalidad) se muestra como tal,
      no se reparte entre las demás.
- [ ] Las convocatorias marcadas `mrr` alimentan la cifra Next Generation EU; el porcentaje se calcula
      sobre el importe a entidades del ejercicio, no sobre el total de la BDNS.

## Ejercicios y comparabilidad

- [ ] Cada ejercicio se corta por **fecha de concesión**; `registrado_en_anio` muestra cuánto se dio de
      alta después. La web avisa de que los ejercicios recientes siguen creciendo.
- [ ] 2024 y 2025 **no se comparan en número de concesiones**: en 2025 la fuente empezó a publicar
      masivamente ayudas a particulares. Ningún titular del tipo «se han multiplicado».
- [ ] El desglose por comunidad autónoma es del órgano **concedente**, no del territorio del
      beneficiario, y así se rotula.
- [ ] `importe` y `ayuda_equivalente` van siempre como dos cifras distintas; los préstamos y avales no
      se presentan como subvención dineraria.
- [ ] Todos los ficheros de `data/` tienen el mismo `generado` y los mismos `ejercicios`.

## Responsabilidad

- [ ] Lenguaje neutral: «concentración», «atípico», «merece revisión». Recibir una subvención no es
      indicio de nada y el README lo dice antes que las cifras.
- [ ] El texto explica que universidades, ayuntamientos y organismos públicos gestionan dinero
      finalista, no lo ingresan como beneficio.
- [ ] Atribución exigida por el aviso legal del SNPSAP visible en README y web: «Origen de los datos:
      Intervención General de la Administración del Estado», fecha de la extracción, mención de que la
      disociación de personas físicas la hace el autor, y ninguna insinuación de que la IGAE patrocina
      o apoya el proyecto.
- [ ] Hay una vía para pedir rectificaciones (*issues* del repositorio) y se responde.

## Web

- [ ] `site/data/*.json` son copia idéntica de `data/*.json` (mismo `generado`).
- [ ] Revisión visual en escritorio y móvil (`prompts/03-revision-visual.md`); sin errores de consola
      ni desbordamiento horizontal; legible en modo oscuro.
- [ ] Los importes se muestran en millones con separador de miles español y las cifras de los
      titulares coinciden con los JSON.
