import os
import re
import json
import time
import hashlib
import calendar

from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup


# ============================================================
# CAL BOT V18
# EDITOR DE NOTICIAS DE CAL FAMILY
#
# OBJETIVO:
# Encontrar noticias REALES de GTA VI y preparar borradores
# para revisión humana en Discord.
#
# FILOSOFÍA V18:
#
#   NOVEDAD + RELEVANCIA + FUENTE RAZONABLE
#   = PUBLICAR
#
# No exige confirmación directa de Rockstar para todo.
# Tampoco publica rumores, leaks, opiniones o especulación
# sin fundamento.
# ============================================================

print("=" * 68)
print("CAL BOT V18")
print("EDITOR DE NOTICIAS DE CAL FAMILY")
print("=" * 68)


# ============================================================
# CONFIGURACIÓN
# ============================================================

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HISTORY_FILE = "seen_news.json"

ROLE_ID = "1504921814759903343"

MAX_HISTORY = 500

# Noticias de hasta 72 horas.
MAX_NEWS_AGE_HOURS = 72

# Más candidatas = más posibilidades de encontrar una buena noticia.
MAX_CANDIDATES = 25

# Evita artículos casi idénticos entre sí durante la misma ejecución.
INTERNAL_TITLE_SIMILARITY = 0.90

# Evita repetir noticias del historial.
HISTORY_TITLE_SIMILARITY = 0.86


# ============================================================
# RSS
# ============================================================

RSS_URL = (
    "https://news.google.com/rss/"
    "?q=GTA+VI"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)


# ============================================================
# MODELOS GEMINI
#
# Gemini 3.7 es la primera opción.
# Si está ocupado / limitado, bajamos automáticamente.
# ============================================================

GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite"
]


# ============================================================
# VALIDAR SECRETS
# ============================================================

if not WEBHOOK:

    print("ERROR: falta NEWS_DRAFT_WEBHOOK.")
    raise SystemExit(1)


if not GEMINI_KEY:

    print("ERROR: falta GEMINI_API_KEY.")
    raise SystemExit(1)


# ============================================================
# HEADERS
# ============================================================

HEADERS = {

    "User-Agent":
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"

}


# ============================================================
# NORMALIZAR TEXTO
# ============================================================

def normalize_text(text):

    if not text:

        return ""

    text = str(text).lower()

    replacements = {

        "grand theft auto vi": "gta vi",
        "grand theft auto 6": "gta vi",
        "grand theft auto": "gta",
        "gta 6": "gta vi"

    }

    for old, new in replacements.items():

        text = text.replace(old, new)


    text = re.sub(
        r"https?://\S+",
        "",
        text
    )


    text = re.sub(
        r"[^a-z0-9áéíóúüñ ]+",
        " ",
        text
    )


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    return text.strip()


# ============================================================
# SIMILITUD
# ============================================================

def similarity(a, b):

    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:

        return 0

    return SequenceMatcher(
        None,
        a,
        b
    ).ratio()


# ============================================================
# URL CANÓNICA
# ============================================================

def canonical_url(url):

    if not url:

        return ""

    try:

        parsed = urlparse(url)

        query = parse_qs(
            parsed.query,
            keep_blank_values=True
        )


        ignored = {

            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "oc",
            "gclid",
            "fbclid"

        }


        clean_query = {

            key: value

            for key, value in query.items()

            if key not in ignored

        }


        new_query = urlencode(
            clean_query,
            doseq=True
        )


        return urlunparse((

            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            new_query,
            ""

        ))


    except Exception:

        return url


# ============================================================
# ARTICLE ID
# ============================================================

def article_id(title, url):

    value = (

        normalize_text(title)
        + "|"
        + canonical_url(url)

    )


    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


# ============================================================
# LIMPIAR HTML
# ============================================================

def clean_html(text):

    if not text:

        return ""

    soup = BeautifulSoup(
        text,
        "html.parser"
    )


    return soup.get_text(
        " ",
        strip=True
    )


# ============================================================
# HISTORIAL
# ============================================================

if os.path.exists(HISTORY_FILE):

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            history = json.load(file)


    except Exception:

        print(
            "Aviso: historial corrupto. "
            "Se iniciará uno nuevo."
        )

        history = {}


else:

    history = {}


if isinstance(history, list):

    history = {

        "published": history,
        "titles": [],
        "content_hashes": []

    }


history.setdefault(
    "published",
    []
)

history.setdefault(
    "titles",
    []
)

history.setdefault(
    "content_hashes",
    [])


# ============================================================
# FILTRO LOCAL
#
# IMPORTANTE:
#
# Este filtro SOLO elimina basura evidente.
#
# NO intenta decidir si una noticia es verdadera.
# Eso lo hará Gemini.
# ============================================================

def obvious_local_reject(title):

    normalized = normalize_text(title)


    blocked_patterns = [

        # ----------------------------------------------------
        # LEAKS / CYBERLEAK
        # ----------------------------------------------------

        "everything leaked so far",
        "everything leaked",
        "leaked so far",
        "cyberleek update",
        "cyberleak update",
        "cyberleek",
        "cyberleak",
        "leaks so far",

        # ----------------------------------------------------
        # OPINIÓN PURA
        # ----------------------------------------------------

        "my thoughts",
        "what i think",
        "i think",
        "my opinion",
        "reaction",
        "reacts",
        "wowed",
        "holy shit",
        "blown away",
        "not blown away",
        "driving me crazy",
        "impressions",

        # ----------------------------------------------------
        # WISH / DESEO
        # ----------------------------------------------------

        "things we want to see",
        "what we want to see",
        "what i want to see",
        "we want to see",

        # ----------------------------------------------------
        # GUÍAS / COMPRA
        # ----------------------------------------------------

        "best console",
        "best pc",
        "best gaming pc",
        "buy for gta",
        "guide",
        "tips and tricks",
        "what to buy",

        # ----------------------------------------------------
        # RECOPILACIONES OBVIAS
        # ----------------------------------------------------

        "everything we know",
        "everything revealed",
        "all the details",
        "all the actors we",
        "20 things you may have missed",
        "things you may have missed",

        # ----------------------------------------------------
        # PREDICCIONES / CLICKBAIT
        # ----------------------------------------------------

        "what might be next",
        "could be",
        "might be",
        "probably",
        "likely to",
        "expected to",

    ]


    for pattern in blocked_patterns:

        if pattern in normalized:

            return True


    return False


# ============================================================
# EXTRAER FECHA RSS
# ============================================================

def get_entry_datetime(entry):

    published_time = None


    if getattr(
        entry,
        "published_parsed",
        None
    ):

        try:

            timestamp = calendar.timegm(
                entry.published_parsed
            )


            published_time = datetime.fromtimestamp(
                timestamp,
                timezone.utc
            )


        except Exception:

            published_time = None


    if not published_time:

        if getattr(
            entry,
            "updated_parsed",
            None
        ):

            try:

                timestamp = calendar.timegm(
                    entry.updated_parsed
                )


                published_time = datetime.fromtimestamp(
                    timestamp,
                    timezone.utc
                )


            except Exception:

                published_time = None


    return published_time


# ============================================================
# BUSCAR RSS
# ============================================================

print("Buscando noticias nuevas...")

feed = feedparser.parse(
    RSS_URL
)


if not feed.entries:

    print("No se encontraron noticias.")
    raise SystemExit(0)


# ============================================================
# RECOPILAR CANDIDATAS
# ============================================================

candidates = []

now = datetime.now(
    timezone.utc
)


for entry in feed.entries:

    title = entry.get(
        "title",
        ""
    ).strip()


    google_url = entry.get(
        "link",
        ""
    ).strip()


    if not title or not google_url:

        continue


    # --------------------------------------------------------
    # FILTRO LOCAL
    # --------------------------------------------------------

    if obvious_local_reject(title):

        print(
            "Ignorada por filtro local:",
            title
        )

        continue


    # --------------------------------------------------------
    # FECHA
    # --------------------------------------------------------

    published_time = get_entry_datetime(
        entry
    )


    if published_time:

        age = now - published_time


        if age > timedelta(
            hours=MAX_NEWS_AGE_HOURS
        ):

            continue


    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    clean_url = canonical_url(
        google_url
    )


    news_id = article_id(
        title,
        clean_url
    )


    # --------------------------------------------------------
    # HISTORIAL POR ID
    # --------------------------------------------------------

    if news_id in history["published"]:

        print(
            "Ignorada: ya publicada."
        )

        continue


    # --------------------------------------------------------
    # HISTORIAL POR TÍTULO
    # --------------------------------------------------------

    duplicate = False


    for old_title in history["titles"]:

        if similarity(
            title,
            old_title
        ) >= HISTORY_TITLE_SIMILARITY:

            duplicate = True
            break


    if duplicate:

        print(
            "Ignorada: título demasiado parecido."
        )

        continue


    # --------------------------------------------------------
    # RSS CONTENT
    # --------------------------------------------------------

    summary = clean_html(
        entry.get(
            "summary",
            ""
        )
    )


    description = clean_html(
        entry.get(
            "description",
            ""
        )
    )


    rss_content = (

        summary
        + "\n"
        + description

    ).strip()


    candidates.append({

        "id": news_id,

        "title": title,

        "google_url": google_url,

        "canonical_url": clean_url,

        "rss_content": rss_content,

        "published_time": (
            published_time.isoformat()
            if published_time
            else ""
        )

    })


    if len(candidates) >= MAX_CANDIDATES:

        break


# ============================================================
# ELIMINAR DUPLICADOS ENTRE CANDIDATAS
# ============================================================

unique_candidates = []


for candidate in candidates:

    duplicate = False


    for existing in unique_candidates:

        if similarity(
            candidate["title"],
            existing["title"]
        ) >= INTERNAL_TITLE_SIMILARITY:

            duplicate = True
            break


    if not duplicate:

        unique_candidates.append(
            candidate
        )


candidates = unique_candidates


# ============================================================
# SIN CANDIDATAS
# ============================================================

if not candidates:

    print("=" * 68)
    print("NO HAY NOTICIAS NUEVAS.")
    print("=" * 68)

    raise SystemExit(0)


print("=" * 68)
print(
    f"CANDIDATAS ENCONTRADAS: "
    f"{len(candidates)}"
)
print("=" * 68)


# ============================================================
# GEMINI
# ============================================================

def ask_gemini(prompt):

    for model in GEMINI_MODELS:

        print(
            "Intentando:",
            model
        )


        endpoint = (

            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{model}:generateContent"
            f"?key={GEMINI_KEY}"

        )


        payload = {

            "contents": [

                {

                    "parts": [

                        {

                            "text": prompt

                        }

                    ]

                }

            ],

            "generationConfig": {

                "maxOutputTokens": 1800,

                "responseMimeType":
                    "application/json"

            }

        }


        for attempt in range(3):

            try:

                response = requests.post(

                    endpoint,

                    headers={

                        "Content-Type":
                            "application/json"

                    },

                    json=payload,

                    timeout=90

                )


                print(
                    "Gemini HTTP:",
                    response.status_code
                )


                if response.status_code == 200:

                    return response.json()


                # ------------------------------------------------
                # RATE LIMIT / SERVICIO TEMPORAL
                # ------------------------------------------------

                if response.status_code in (

                    429,
                    500,
                    502,
                    503,
                    504

                ):

                    wait = 4 * (
                        attempt + 1
                    )


                    print(
                        f"Modelo temporalmente "
                        f"no disponible. "
                        f"Reintentando en {wait}s..."
                    )


                    time.sleep(
                        wait
                    )

                    continue


                print(
                    response.text[:1200]
                )

                break


            except Exception as error:

                print(
                    "Error Gemini:",
                    error
                )


                if attempt < 2:

                    wait = 4 * (
                        attempt + 1
                    )


                    time.sleep(
                        wait
                    )


    return None


# ============================================================
# PARSEAR GEMINI
# ============================================================

def parse_gemini(result):

    if not result:

        return None


    try:

        candidates_data = result.get(
            "candidates",
            []
        )


        if not candidates_data:

            return None


        parts = (

            candidates_data[0]
            .get(
                "content",
                {}
            )
            .get(
                "parts",
                []
            )

        )


        if not parts:

            return None


        text = (

            parts[0]
            .get(
                "text",
                ""
            )
            .strip()

        )


        if not text:

            return None


        # --------------------------------------------------------
        # LIMPIAR MARKDOWN JSON
        # --------------------------------------------------------

        text = re.sub(

            r"^```json\s*",

            "",

            text,

            flags=re.IGNORECASE

        )


        text = re.sub(

            r"\s*```$",

            "",

            text

        )


        return json.loads(
            text
        )


    except Exception as error:

        print(
            "Error interpretando Gemini:",
            error
        )

        return None


# ============================================================
# PROMPT V18
# ============================================================

EDITORIAL_PROMPT = """

Eres el editor principal de noticias de CAL FAMILY,
una comunidad dedicada a Grand Theft Auto VI.

Tu trabajo es decidir si un artículo contiene una
NOVEDAD suficientemente útil para convertirse en un
borrador de noticia para Discord.

IMPORTANTE:

No queremos un bot demasiado estricto.

Tampoco queremos un bot que publique basura.

Tu objetivo es encontrar el PUNTO MEDIO.

============================================================
REGLA PRINCIPAL
============================================================

PUBLICAR cuando exista:

1. INFORMACIÓN NUEVA o una actualización relevante.
2. RELACIÓN REAL con GTA VI.
3. UNA FUENTE RAZONABLE O IDENTIFICABLE.
4. Información suficientemente concreta para redactar
   una noticia sin inventar.

NO es obligatorio que Rockstar Games haya confirmado
directamente cada información.

============================================================
FUENTES ACEPTABLES
============================================================

Pueden ser válidas:

- Rockstar Games.
- Take-Two.
- Desarrolladores identificados.
- Actores identificados.
- Directores o exdirectores identificados.
- Productores identificados.
- Empresas involucradas en el juego.
- Documentos públicos.
- Organismos gubernamentales.
- Entrevistas.
- Medios periodísticos reconocidos.
- Periodistas identificables.
- Fuentes secundarias fiables.

Una fuente secundaria NO debe ser descartada simplemente
por no ser Rockstar.

============================================================
EJEMPLO MUY IMPORTANTE
============================================================

Titular:

"GTA 6 Takes Around 80 Hours To Complete, Says Developer"

NO descartes automáticamente esto.

Si el artículo atribuye la información a un desarrollador
identificable y explica razonablemente de dónde sale la
afirmación, puede ser PUBLICAR.

No necesitas una confirmación adicional de Rockstar.

============================================================
CUANDO PUBLICAR
============================================================

PUBLICA noticias como:

- Un desarrollador revela un detalle.
- Un actor habla sobre su participación.
- Una empresa involucrada revela información.
- Un documento público revela algo relacionado con GTA VI.
- Un organismo gubernamental publica algo relacionado
  directamente con GTA VI.
- Rockstar anuncia algo.
- Take-Two anuncia algo.
- Un medio fiable reporta una información concreta
  atribuida a una fuente identificable.
- Una entrevista contiene una declaración nueva.
- Aparece información empresarial relevante.
- Hay una actualización real sobre lanzamiento,
  distribución, plataformas, desarrollo o producción.
- Una noticia secundaria aporta un dato concreto nuevo.
- Un evento relacionado directamente con GTA VI produce
  información nueva.

============================================================
NO PUBLICAR
============================================================

DESCARTA cuando sea principalmente:

- Opinión personal.
- Reacción.
- Review.
- Análisis sin información nueva.
- Teoría.
- Predicción.
- Rumor sin fuente.
- "Podría".
- "Probablemente".
- "Se espera que" sin fuente concreta.
- Clickbait.
- Guía.
- Comparación de consolas.
- Recomendación de compra.
- Recopilación de información ya conocida.
- Resumen del Extended Look sin novedad.
- "Todo lo que sabemos".
- Lista de detalles ya mostrados.
- Artículo basado únicamente en leaks.
- Cyberleaks.
- Deseos de jugadores.
- "Lo que queremos ver".
- Opiniones sobre gráficos.
- Opiniones sobre rendimiento sin fuente.
- Especulación sobre FPS sin información concreta.
- Artículos cuyo único objetivo sea comentar el tráiler.

============================================================
REGLA SOBRE ARTÍCULOS DE ANÁLISIS
============================================================

Un artículo NO debe descartarse simplemente porque
contenga análisis.

Pregunta:

¿Dentro del artículo existe un DATO NUEVO?

Si existe:

PUBLICAR.

Si todo el artículo es interpretación/opinión:

DESCARTAR.

============================================================
REGLA SOBRE INFORMACIÓN SECUNDARIA
============================================================

Una fuente secundaria puede publicar una noticia válida.

NO hagas esto:

"TechPowerUp no es Rockstar -> DESCARTAR."

Eso es demasiado estricto.

Haz esto:

"¿TechPowerUp está citando a un desarrollador,
una entrevista, un documento o una fuente identificable?"

Si sí:

puede PUBLICARSE.

Si no existe respaldo:

DESCARTAR.

============================================================
REGLA SOBRE RUMORES
============================================================

Si el artículo dice:

"Según un insider..."
"Podría..."
"Se rumorea..."
"Se espera..."
"Al parecer..."

eso NO significa automáticamente DESCARTAR.

Investiga el contexto del propio artículo.

Si existe una fuente identificable y evidencia suficiente,
puede ser noticia.

Si solamente es especulación:

DESCARTAR.

IMPORTANTE:

Nunca presentes un rumor como hecho confirmado.

Si se publica una información no oficial pero razonablemente
respaldada, redacta claramente:

"Según..."
"El medio señala..."
"La información procede de..."
"De acuerdo con..."

============================================================
REGLA SOBRE LEAKS
============================================================

Los leaks de GTA VI no son contenido editorial válido
para CAL FAMILY.

Si el artículo depende principalmente de material filtrado,
Cyberleek o contenido robado:

DESCARTAR.

============================================================
REGLA SOBRE REPETICIONES
============================================================

Si el artículo simplemente vuelve a hablar de una noticia
que ya está en el historial:

DESCARTAR.

Pero si aporta un dato NUEVO sobre un tema ya conocido:

PUBLICAR.

Ejemplo:

Noticia anterior:
"Rockstar muestra el Extended Look."

Nueva noticia:
"Un desarrollador explica una mecánica mostrada
en el Extended Look."

Esto puede PUBLICARSE.

============================================================
HISTORIAL
============================================================

{HISTORY}

============================================================
ARTÍCULO
============================================================

TÍTULO:

{TITLE}

URL:

{URL}

CONTENIDO:

{CONTENT}

============================================================
PROCESO DE DECISIÓN
============================================================

Antes de decidir:

1. ¿Cuál es la afirmación principal?
2. ¿Cuál es el dato nuevo?
3. ¿Quién lo afirma?
4. ¿Qué tipo de fuente es?
5. ¿El artículo aporta contexto suficiente?
6. ¿Es información o simplemente opinión?
7. ¿Ya publicamos esencialmente lo mismo?
8. ¿Podemos escribir una noticia sin inventar?

NO seas excesivamente estricto.

Si la información parece una noticia razonable,
con una fuente razonable y un dato concreto:

PUBLICAR.

Si es claramente opinión, rumor sin respaldo,
especulación, leak o repetición:

DESCARTAR.

============================================================
FORMATO DE RESPUESTA
============================================================

Devuelve SOLAMENTE JSON válido.

PUBLICAR:

{
  "decision": "PUBLICAR",
  "reason": "Motivo breve",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Texto final"
}

DESCARTAR:

{
  "decision": "DESCARTAR",
  "reason": "Motivo breve",
  "category": "Noticias",
  "title": "",
  "content": ""
}

============================================================
SI PUBLICAS
============================================================

Escribe en español.

Entre 450 y 1000 caracteres.

El texto debe sonar como una noticia real de una comunidad
de GTA VI.

NO inventes.

NO agregues información que no esté respaldada por el
artículo.

NO inventes declaraciones.

NO presentes rumores como hechos.

NO incluyas el enlace dentro del contenido.

Puedes utilizar:

**El dato clave**

**Por qué importa**

⚠️ **Contexto**

Usa estas secciones solo cuando realmente ayuden.

============================================================
IMPORTANTE
============================================================

No conviertas la noticia en una opinión.

No digas:

"Esto demuestra que el juego será increíble."

No digas:

"Los fans estarán emocionados."

No hagas hype artificial.

Informa.

El objetivo es que CAL FAMILY tenga noticias frecuentes,
útiles y creíbles.
"""


# ============================================================
# EVALUAR CANDIDATAS
# ============================================================

selected = None


for index, candidate in enumerate(
    candidates,
    start=1
):

    print("=" * 68)

    print(
        f"EVALUANDO CANDIDATA "
        f"{index}/{len(candidates)}"
    )

    print(
        candidate["title"]
    )

    print(
        candidate["google_url"]
    )

    print("=" * 68)


    # ========================================================
    # OBTENER ARTÍCULO
    # ========================================================

    article_text = ""

    final_source_url = (
        candidate["google_url"]
    )


    try:

        response = requests.get(

            candidate["google_url"],

            headers=HEADERS,

            timeout=25,

            allow_redirects=True

        )


        print(
            "Estado de página:",
            response.status_code
        )


        final_source_url = response.url


        if response.ok:

            soup = BeautifulSoup(

                response.text,

                "html.parser"

            )


            # ------------------------------------------------
            # ELIMINAR ELEMENTOS NO INFORMATIVOS
            # ------------------------------------------------

            for element in soup([

                "script",
                "style",
                "noscript",
                "svg",
                "nav",
                "footer",
                "header",
                "form",
                "aside"

            ]):

                element.decompose()


            paragraphs = []


            for paragraph in soup.find_all("p"):

                text = paragraph.get_text(

                    " ",

                    strip=True

                )


                # ------------------------------------------------
                # Evitar basura muy pequeña
                # ------------------------------------------------

                if len(text) >= 40:

                    paragraphs.append(
                        text
                    )


            article_text = "\n".join(
                paragraphs
            )


            # ------------------------------------------------
            # Evitar contexto infinito
            # ------------------------------------------------

            article_text = article_text[
                :24000
            ]


    except Exception as error:

        print(
            "Error obteniendo artículo:",
            error
        )


    # ========================================================
    # SELECCIONAR CONTENIDO
    # ========================================================

    if len(article_text) >= 250:

        source_content = article_text

    elif len(candidate["rss_content"]) >= 80:

        source_content = candidate[
            "rss_content"
        ]

    else:

        print(
            "Descartada: contenido insuficiente."
        )

        continue


    # ========================================================
    # HISTORIAL
    # ========================================================

    previous_titles = history[
        "titles"
    ][-100:]


    if previous_titles:

        previous_titles_text = "\n".join(

            f"- {title}"

            for title in previous_titles

        )

    else:

        previous_titles_text = (
            "No existen publicaciones anteriores."
        )


    # ========================================================
    # CREAR PROMPT
    # ========================================================

    prompt = EDITORIAL_PROMPT.format(

        HISTORY=previous_titles_text,

        TITLE=candidate["title"],

        URL=final_source_url,

        CONTENT=source_content

    )


    print(
        "CAL BOT ESTÁ EVALUANDO..."
    )


    # ========================================================
    # GEMINI
    # ========================================================

    gemini_raw = ask_gemini(
        prompt
    )


    result = parse_gemini(
        gemini_raw
    )


    if not result:

        print(
            "No se pudo interpretar "
            "la respuesta."
        )

        continue


    # ========================================================
    # DECISIÓN
    # ========================================================

    decision = str(

        result.get(
            "decision",
            "DESCARTAR"
        )

    ).upper().strip()


    reason = str(

        result.get(
            "reason",
            ""
        )

    ).strip()


    if decision != "PUBLICAR":

        print("=" * 68)
        print("DESCARTADA POR CAL BOT")
        print("=" * 68)

        print(
            reason
        )

        continue


    # ========================================================
    # DATOS GENERADOS
    # ========================================================

    ai_title = str(

        result.get(
            "title",
            ""
        )

    ).strip()


    ai_content = str(

        result.get(
            "content",
            ""
        )

    ).strip()


    category = str(

        result.get(
            "category",
            "Noticias"
        )

    ).strip()


    if not ai_title or not ai_content:

        print(
            "Descartada: contenido "
            "generado inválido."
        )

        continue


    # ========================================================
    # SEGURIDAD CONTRA REPETICIONES
    # ========================================================

    repeated = False


    for old_title in history["titles"]:

        if similarity(
            ai_title,
            old_title
        ) >= HISTORY_TITLE_SIMILARITY:

            repeated = True

            break


    if repeated:

        print(
            "Descartada: título demasiado "
            "parecido a una publicación anterior."
        )

        continue


    # ========================================================
    # HASH DEL CONTENIDO
    # ========================================================

    content_hash = hashlib.sha256(

        normalize_text(
            source_content
        ).encode(
            "utf-8"
        )

    ).hexdigest()


    if content_hash in history[
        "content_hashes"
    ]:

        print(
            "Descartada: contenido idéntico."
        )

        continue


    # ========================================================
    # SELECCIONADA
    # ========================================================

    selected = {

        "news_id":
            candidate["id"],

        "title":
            ai_title,

        "content":
            ai_content,

        "category":
            category,

        "source_url":
            final_source_url,

        "content_hash":
            content_hash,

        "reason":
            reason

    }


    print("=" * 68)
    print("NOTICIA SELECCIONADA.")
    print("=" * 68)

    break


# ============================================================
# NINGUNA NOTICIA
# ============================================================

if selected is None:

    print("=" * 68)
    print(
        "NINGUNA NOTICIA CUMPLIÓ "
        "LOS CRITERIOS."
    )
    print("=" * 68)

    raise SystemExit(0)


# ============================================================
# LIMPIAR CONTENIDO
# ============================================================

ai_title = selected["title"]

ai_content = selected["content"]

category = selected["category"]

final_source_url = selected[
    "source_url"
]


# ------------------------------------------------------------
# Eliminar fuentes duplicadas que Gemini pudiera añadir.
# ------------------------------------------------------------

source_patterns = [

    "🔗 **Fuente:**",
    "🔗 **Fuente original:**",
    "**Fuente:**",
    "**Fuente original:**"

]


for pattern in source_patterns:

    ai_content = ai_content.replace(
        pattern,
        ""
    )


ai_content = ai_content.strip()


# ============================================================
# FECHA
# ============================================================

date_text = datetime.now(
    timezone.utc
).strftime(
    "%d/%m/%Y"
)


# ============================================================
# MENSAJE DISCORD
# ============================================================

final_message = (

    f"<@&{ROLE_ID}>\n\n"

    f"🧭 **{category}**\n\n"

    f"# {ai_title}\n\n"

    f"{ai_content}\n\n"

    f"🔗 **Fuente original:** "
    f"{final_source_url}\n\n"

    f"⚠️ **REVISIÓN REQUERIDA**\n"

    f"Este contenido fue preparado "
    f"por Cal Bot como borrador editorial. "

    f"Revisa la información antes de "
    f"publicarlo en #noticias.\n\n"

    f"-# Cal Bot V18 · {date_text}"

)


# ============================================================
# LÍMITE DISCORD
# ============================================================

DISCORD_LIMIT = 1950


if len(final_message) > DISCORD_LIMIT:

    print(
        "Mensaje demasiado largo. "
        "Recortando contenido..."
    )


    fixed_message = (

        f"<@&{ROLE_ID}>\n\n"

        f"🧭 **{category}**\n\n"

        f"# {ai_title}\n\n"

        f"\n\n"

        f"🔗 **Fuente original:** "
        f"{final_source_url}\n\n"

        f"⚠️ **REVISIÓN REQUERIDA**\n"

        f"Este contenido fue preparado "
        f"por Cal Bot como borrador editorial. "
        f"Revisa la información antes de "
        f"publicarlo en #noticias.\n\n"

        f"-# Cal Bot V18 · {date_text}"

    )


    allowed = (

        DISCORD_LIMIT
        - len(fixed_message)

        - 10

    )


    if allowed < 250:

        print(
            "Descartada: no se puede "
            "ajustar al límite de Discord."
        )

        raise SystemExit(0)


    ai_content = (

        ai_content[:allowed]
        .rstrip()

    )


    # Evitar terminar una palabra a la mitad.

    if " " in ai_content:

        ai_content = ai_content.rsplit(
            " ",
            1
        )[0]


    final_message = (

        f"<@&{ROLE_ID}>\n\n"

        f"🧭 **{category}**\n\n"

        f"# {ai_title}\n\n"

        f"{ai_content}\n\n"

        f"🔗 **Fuente original:** "
        f"{final_source_url}\n\n"

        f"⚠️ **REVISIÓN REQUERIDA**\n"

        f"Este contenido fue preparado "
        f"por Cal Bot como borrador editorial. "
        f"Revisa la información antes de "
        f"publicarlo en #noticias.\n\n"

        f"-# Cal Bot V18 · {date_text}"

    )


# ============================================================
# DISCORD
# ============================================================

print("=" * 68)
print("NOTICIA APROBADA.")
print("ENVIANDO A DISCORD...")
print("=" * 68)


discord_payload = {

    "content":
        final_message,

    "username":
        "Cal Bot",

    "allowed_mentions": {

        "roles": [
            ROLE_ID
        ]

    }

}


try:

    discord_response = requests.post(

        WEBHOOK,

        json=discord_payload,

        timeout=30

    )


except Exception as error:

    print("=" * 68)
    print("ERROR DE CONEXIÓN CON DISCORD")
    print(error)
    print("La noticia NO se guardará.")
    print("=" * 68)

    raise SystemExit(1)


print(
    "Discord HTTP:",
    discord_response.status_code
)


if not discord_response.ok:

    print("=" * 68)
    print("ERROR DE DISCORD")
    print(
        discord_response.text[:2000]
    )

    print(
        "La noticia NO se guardará."
    )

    print("=" * 68)

    raise SystemExit(1)


# ============================================================
# DISCORD CONFIRMÓ
# ============================================================

print("=" * 68)
print("DISCORD CONFIRMÓ EL MENSAJE.")
print("=" * 68)


# ============================================================
# GUARDAR HISTORIAL
# ============================================================

history["published"].append(
    selected["news_id"]
)


history["titles"].append(
    ai_title
)


history["content_hashes"].append(
    selected["content_hash"]
)


history["published"] = (

    history["published"]
    [-MAX_HISTORY:]

)


history["titles"] = (

    history["titles"]
    [-MAX_HISTORY:]

)


history["content_hashes"] = (

    history["content_hashes"]
    [-MAX_HISTORY:]

)


with open(

    HISTORY_FILE,

    "w",

    encoding="utf-8"

) as file:

    json.dump(

        history,

        file,

        indent=2,

        ensure_ascii=False

    )


# ============================================================
# FINAL
# ============================================================

print("=" * 68)
print(
    "NOTICIA PUBLICADA Y GUARDADA "
    "EN HISTORIAL."
)

print(
    "CAL BOT V18 FINALIZADO."
)

print("=" * 68)
