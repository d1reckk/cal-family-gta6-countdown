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
# CAL BOT V16
# EDITOR DE NOTICIAS DE CAL FAMILY
# ============================================================

print("=" * 64)
print("CAL BOT V16")
print("EDITOR DE NOTICIAS DE CAL FAMILY")
print("=" * 64)


# ============================================================
# CONFIGURACIÓN
# ============================================================

WEBHOOK = os.environ.get(
    "NEWS_DRAFT_WEBHOOK",
    ""
).strip()

GEMINI_KEY = os.environ.get(
    "GEMINI_API_KEY",
    ""
).strip()

HISTORY_FILE = "seen_news.json"

ROLE_ID = "1504921814759903343"

MAX_HISTORY = 500

# Noticias de las últimas 72 horas
MAX_NEWS_AGE_HOURS = 72

# V16 reduce las llamadas innecesarias a Gemini
MAX_CANDIDATES = 8

# Solo se hacen unas pocas solicitudes por modelo
MAX_RETRIES_PER_MODEL = 1

# Modelos
GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite"
]


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
# VALIDAR SECRETS
# ============================================================

if not WEBHOOK:

    print(
        "ERROR: falta NEWS_DRAFT_WEBHOOK."
    )

    raise SystemExit(1)


if not GEMINI_KEY:

    print(
        "ERROR: falta GEMINI_API_KEY."
    )

    raise SystemExit(1)


# ============================================================
# HEADERS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9"
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

        text = text.replace(
            old,
            new
        )

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
            "oc"
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
# ID DEL ARTÍCULO
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

    except Exception as error:

        print(
            "Advertencia leyendo historial:",
            error
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
# FILTRO LOCAL V16
# ============================================================

def obvious_local_reject(title):

    normalized = normalize_text(
        title
    )

    # --------------------------------------------------------
    # RUMORES / LEAKS
    # --------------------------------------------------------

    hard_block = [

        "rumor",
        "rumour",
        "leak",
        "leaked",
        "leaker",
        "insider",
        "unconfirmed",
        "unverified",
        "allegedly",
        "supposedly",
        "might",
        "may reportedly",
        "reportedly",
        "could reportedly",
        "anonymous source"
    ]

    for pattern in hard_block:

        if pattern in normalized:

            return True


    # --------------------------------------------------------
    # REACCIONES
    # --------------------------------------------------------

    reaction_block = [

        "my thoughts",
        "what i think",
        "i think",
        "reaction to",
        "reacts to",
        "reaction",
        "wowed",
        "blown away",
        "driving me crazy",
        "holy shit",
        "impressions",
        "first impressions",
        "fan reaction",
        "fans react"
    ]

    for pattern in reaction_block:

        if pattern in normalized:

            return True


    # --------------------------------------------------------
    # OPINIONES / COLUMNAS
    # --------------------------------------------------------

    opinion_block = [

        "opinion:",
        "opinion -",
        "editorial:",
        "editorial -",
        "why i think",
        "my take on",
        "i was wrong about",
        "i love",
        "i hate"
    ]

    for pattern in opinion_block:

        if pattern in normalized:

            return True


    # --------------------------------------------------------
    # GUÍAS / COMPRAS
    # --------------------------------------------------------

    guide_block = [

        "best console",
        "best pc",
        "best monitor",
        "what console to buy",
        "which console should",
        "buy for gta",
        "guide",
        "tips and tricks",
        "how to",
        "walkthrough",
        "review"
    ]

    for pattern in guide_block:

        if pattern in normalized:

            return True


    # --------------------------------------------------------
    # RECOPILACIONES
    # --------------------------------------------------------

    compilation_block = [

        "everything leaked so far",
        "everything we know",
        "everything revealed",
        "all the actors",
        "all the details",
        "all known details",
        "complete list",
        "full list",
        "what we know so far"
    ]

    for pattern in compilation_block:

        if pattern in normalized:

            return True


    # --------------------------------------------------------
    # EXTENDED LOOK / TRAILER REACTION
    # --------------------------------------------------------

    extended_reaction = [

        "extended look made me",
        "extended look reaction",
        "extended look impressions",
        "extended look review",
        "extended look thoughts",
        "extended look wowed",
        "extended look holy"
    ]

    for pattern in extended_reaction:

        if pattern in normalized:

            return True


    return False


# ============================================================
# BUSCAR RSS
# ============================================================

print(
    "Buscando noticias nuevas..."
)

feed = feedparser.parse(
    RSS_URL
)


if not feed.entries:

    print(
        "No se encontraron noticias."
    )

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

            published_time = (
                datetime.fromtimestamp(
                    timestamp,
                    timezone.utc
                )
            )

        except Exception:

            published_time = None


    if published_time:

        age = (
            now - published_time
        )

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


    if news_id in history[
        "published"
    ]:

        continue


    # --------------------------------------------------------
    # TÍTULO REPETIDO
    # --------------------------------------------------------

    duplicate = False

    for old_title in history[
        "titles"
    ]:

        if similarity(
            title,
            old_title
        ) >= 0.84:

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

        "rss_content": rss_content

    })


    if len(candidates) >= MAX_CANDIDATES:

        break


# ============================================================
# SIN CANDIDATAS
# ============================================================

if not candidates:

    print("=" * 64)

    print(
        "NO HAY NOTICIAS NUEVAS."
    )

    print("=" * 64)

    raise SystemExit(0)


print("=" * 64)

print(
    f"CANDIDATAS ENCONTRADAS: "
    f"{len(candidates)}"
)

print("=" * 64)


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

                "temperature": 0.1,

                "maxOutputTokens": 2200,

                "responseMimeType":
                    "application/json"

            }

        }


        for attempt in range(
            MAX_RETRIES_PER_MODEL
        ):

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
                # RATE LIMIT / SERVER ERROR
                # ------------------------------------------------

                if response.status_code in (
                    429,
                    500,
                    502,
                    503,
                    504
                ):

                    print(
                        "Modelo temporalmente no disponible."
                    )

                    # No hacemos 3 esperas largas.
                    # Pasamos directamente al siguiente modelo.
                    break


                # ------------------------------------------------
                # OTRO ERROR
                # ------------------------------------------------

                print(
                    response.text[:1500]
                )

                break


            except requests.RequestException as error:

                print(
                    "Error Gemini:",
                    error
                )

                break


    return None


# ============================================================
# EXTRAER JSON
# ============================================================

def parse_gemini(result):

    if not result:

        return None


    try:

        candidates_response = result.get(
            "candidates",
            []
        )


        if not candidates_response:

            return None


        content = (
            candidates_response[0]
            .get(
                "content",
                {}
            )
        )


        parts = content.get(
            "parts",
            []
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
        # QUITAR MARKDOWN
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


        # --------------------------------------------------------
        # ENCONTRAR JSON
        # --------------------------------------------------------

        start = text.find(
            "{"
        )

        end = text.rfind(
            "}"
        )


        if start == -1 or end == -1:

            return None


        text = text[
            start:end + 1
        ]


        data = json.loads(
            text
        )


        if not isinstance(
            data,
            dict
        ):

            return None


        return data


    except Exception as error:

        print(
            "Error interpretando Gemini:",
            error
        )

        return None


# ============================================================
# HISTORIAL PARA EL PROMPT
# ============================================================

previous_titles = history[
    "titles"
][-100:]


previous_titles_text = "\n".join(
    f"- {title}"
    for title in previous_titles
)


# ============================================================
# EVALUAR CANDIDATAS
# ============================================================

selected = None


for index, candidate in enumerate(
    candidates,
    start=1
):

    print("=" * 64)

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

    print("=" * 64)


    # --------------------------------------------------------
    # DESCARGAR ARTÍCULO
    # --------------------------------------------------------

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


        final_source_url = (
            response.url
        )


        if response.ok:

            soup = BeautifulSoup(

                response.text,

                "html.parser"

            )


            # ------------------------------------------------
            # ELIMINAR BASURA HTML
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


            for paragraph in soup.find_all(
                "p"
            ):

                text = paragraph.get_text(
                    " ",
                    strip=True
                )


                if len(text) >= 45:

                    paragraphs.append(
                        text
                    )


            article_text = "\n".join(
                paragraphs
            )


            article_text = (
                article_text[:24000]
            )


    except Exception as error:

        print(
            "Error obteniendo artículo:",
            error
        )


    # --------------------------------------------------------
    # ELEGIR CONTENIDO
    # --------------------------------------------------------

    if len(article_text) >= 300:

        source_content = (
            article_text
        )

    elif len(
        candidate["rss_content"]
    ) >= 100:

        source_content = (
            candidate["rss_content"]
        )

    else:

        print(
            "Descartada: contenido insuficiente."
        )

        continue


    # ========================================================
    # PROMPT V16
    # ========================================================

    prompt = f"""
Eres el editor principal de CAL FAMILY,
una comunidad dedicada a Grand Theft Auto VI.

Tu trabajo es decidir si este artículo merece convertirse
en una noticia para la comunidad.

Debes ser ESTRICTO con rumores, especulación, opiniones,
reacciones y contenido repetido.

Pero NO debes exigir que cada noticia venga directamente
de Rockstar Games.

============================================================
REGLA CENTRAL
============================================================

PUBLICAR solamente cuando exista:

NOVEDAD REAL
+
DATO CONCRETO
+
FUENTE IDENTIFICABLE
+
CONTEXTO SUFICIENTE
+
RELEVANCIA PARA GTA VI

Si falta alguno de estos elementos importantes,
DESCARTAR.

============================================================
FUENTES ACEPTABLES
============================================================

Puede ser información procedente de:

- Rockstar Games
- Take-Two Interactive
- Un desarrollador identificado
- Un empleado identificado
- Un actor identificado
- Una empresa relacionada directamente
- Una autoridad gubernamental
- Un documento público
- Una entrevista
- Una presentación
- Un comunicado
- Un medio periodístico fiable
- Una fuente secundaria que cite claramente
  una fuente identificable

Una fuente secundaria NO debe ser descartada
automáticamente.

Lo importante es saber:

¿De dónde sale realmente la información?

============================================================
DESCARTAR
============================================================

DESCARTA:

- Rumores.
- Leaks.
- Supuestos leaks.
- Insider anónimo.
- Predicciones.
- Teorías.
- Especulación.
- Opiniones.
- Reacciones.
- Reviews.
- Impresiones.
- Guías.
- Recomendaciones de compra.
- Comparaciones de consolas.
- Artículos de "todo lo que sabemos".
- Recopilaciones.
- Clickbait.
- Análisis sin dato nuevo.
- Artículos que simplemente repiten información conocida.
- Artículos que solamente reaccionan al Extended Look.
- Artículos que solamente resumen el Extended Look.
- Artículos que solamente describen escenas ya mostradas.
- Artículos que convierten una posibilidad en una afirmación.
- Información sin fuente identificable.

============================================================
EXTENDED LOOK
============================================================

IMPORTANTE:

NO publiques un artículo simplemente porque habla
del GTA VI Extended Look.

Ejemplos que deben DESCARTARSE:

"Todo lo que vimos en el Extended Look."

"Mi reacción al Extended Look."

"Los fans están sorprendidos por el Extended Look."

"El Extended Look demuestra que GTA VI será increíble."

"Todos los actores que aparecen en el Extended Look."

Sin embargo:

Si después del Extended Look un medio publica una
información CONCRETA obtenida de una entrevista,
declaración, documento o fuente identificable,
puede PUBLICARSE.

La existencia del Extended Look NO es suficiente.

============================================================
ANÁLISIS
============================================================

No descartes automáticamente un artículo porque contiene
análisis.

Pregunta:

¿Dentro del artículo existe un dato NUEVO y VERIFICABLE?

Si sí:

PUBLICAR.

Si solamente existe análisis:

DESCARTAR.

============================================================
FUENTES SECUNDARIAS
============================================================

Ejemplo:

"TechPowerUp dice que GTA VI durará 80 horas."

No publiques automáticamente.

Busca dentro del artículo:

¿Identifica al desarrollador?

¿Existe una entrevista?

¿Existe una declaración concreta?

¿Existe contexto?

Si sí:

puede ser PUBLICAR.

Si solamente repite una afirmación sin respaldo:

DESCARTAR.

============================================================
REPETICIONES
============================================================

Compara con el historial.

Si la noticia básicamente comunica lo mismo que
una noticia anterior:

DESCARTAR.

Aunque el título sea diferente.

============================================================
HISTORIAL
============================================================

{previous_titles_text}

============================================================
ARTÍCULO ACTUAL
============================================================

TÍTULO:

{candidate["title"]}

URL:

{final_source_url}

CONTENIDO:

{source_content}

============================================================
PROCESO DE DECISIÓN
============================================================

Antes de responder:

1. Identifica el dato principal.
2. Identifica quién proporciona ese dato.
3. Determina si la fuente es identificable.
4. Determina si el dato puede verificarse.
5. Determina si es realmente nuevo.
6. Comprueba si ya aparece en el historial.
7. Comprueba si es opinión o análisis.
8. Comprueba si es rumor o especulación.
9. Comprueba si solamente habla del Extended Look.
10. Decide.

============================================================
PUBLICAR
============================================================

Solo devuelve PUBLICAR si puedes explicar claramente
qué dato nuevo contiene el artículo.

El campo "reason" debe explicar brevemente:

- cuál es la novedad
- quién la respalda

============================================================
FORMATO
============================================================

Devuelve ÚNICAMENTE JSON válido.

NO markdown.

Si DESCARTAR:

{{
  "decision": "DESCARTAR",
  "reason": "Motivo breve y específico",
  "category": "Noticias",
  "title": "",
  "content": ""
}}

Si PUBLICAR:

{{
  "decision": "PUBLICAR",
  "reason": "Dato nuevo y fuente que lo respalda",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Noticia final en español"
}}

============================================================
CONTENIDO DE LA NOTICIA
============================================================

Si PUBLICAS:

- Español natural.
- Profesional.
- 500-1100 caracteres.
- No inventar datos.
- No inventar citas.
- No exagerar.
- No presentar rumores como hechos.
- No incluir la URL dentro del contenido.
- No utilizar frases como "según rumores".
- Si es una fuente secundaria, dejar claro de dónde
  procede la información.

Usa esta estructura cuando sea apropiado:

[Introducción]

**El dato clave**

[Información concreta]

**Por qué importa**

[Contexto]

⚠️ **Contexto**

[Origen de la información]

============================================================
REGLA FINAL
============================================================

PUBLICAR:

NOVEDAD + DATO CONCRETO + FUENTE IDENTIFICABLE
+ INFORMACIÓN VERIFICABLE.

DESCARTAR:

RUMOR + ESPECULACIÓN + OPINIÓN + REACCIÓN
+ REPETICIÓN + INFORMACIÓN SIN RESPALDO.
"""


    print(
        "CAL BOT ESTÁ EVALUANDO..."
    )


    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    gemini_raw = ask_gemini(
        prompt
    )


    result = parse_gemini(
        gemini_raw
    )


    if not result:

        print(
            "No se pudo interpretar la respuesta de Gemini."
        )

        continue


    # --------------------------------------------------------
    # DECISIÓN
    # --------------------------------------------------------

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


    # Seguridad:
    # cualquier respuesta que no sea PUBLICAR
    # se considera DESCARTAR.

    if decision != "PUBLICAR":

        print("=" * 64)

        print(
            "DESCARTADA POR CAL BOT"
        )

        print("=" * 64)

        print(
            reason or
            "No cumple los criterios editoriales."
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


    if not ai_title:

        print(
            "Descartada: Gemini no generó título."
        )

        continue


    if not ai_content:

        print(
            "Descartada: Gemini no generó contenido."
        )

        continue


    # ========================================================
    # SEGURIDAD EXTRA
    # ========================================================

    # Si Gemini intenta introducir URLs dentro
    # del contenido, las eliminamos.

    ai_content = re.sub(
        r"https?://\S+",
        "",
        ai_content
    ).strip()


    # --------------------------------------------------------
    # BLOQUEAR RESPUESTAS SOSPECHOSAS
    # --------------------------------------------------------

    suspicious_phrases = [

        "según rumores",

        "podría ser",

        "podría tener",

        "se espera que",

        "se cree que",

        "se especula que",

        "supuestamente",

        "al parecer",

        "podría indicar",

        "los fans creen",

        "creemos que"

    ]


    normalized_content = normalize_text(
        ai_content
    )


    suspicious_count = 0


    for phrase in suspicious_phrases:

        if normalize_text(
            phrase
        ) in normalized_content:

            suspicious_count += 1


    if suspicious_count >= 2:

        print(
            "Descartada: contenido generado "
            "contiene demasiadas afirmaciones especulativas."
        )

        continue


    # ========================================================
    # DUPLICADO POR TÍTULO GENERADO
    # ========================================================

    repeated = False


    for old_title in history[
        "titles"
    ]:

        if similarity(
            ai_title,
            old_title
        ) >= 0.86:

            repeated = True

            break


    if repeated:

        print(
            "Descartada: título demasiado parecido "
            "a una publicación anterior."
        )

        continue


    # ========================================================
    # HASH DEL CONTENIDO FUENTE
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
            "Descartada: contenido idéntico "
            "a una noticia anterior."
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


    print("=" * 64)

    print(
        "NOTICIA SELECCIONADA POR CAL BOT."
    )

    print(
        "Motivo:",
        reason
    )

    print("=" * 64)

    break


# ============================================================
# NINGUNA NOTICIA
# ============================================================

if selected is None:

    print("=" * 64)

    print(
        "NINGUNA NOTICIA CUMPLIÓ LOS CRITERIOS."
    )

    print("=" * 64)

    raise SystemExit(0)


# ============================================================
# DATOS FINALES
# ============================================================

ai_title = selected[
    "title"
]

ai_content = selected[
    "content"
]

category = selected[
    "category"
]

final_source_url = selected[
    "source_url"
]


# ============================================================
# LIMPIEZA
# ============================================================

remove_phrases = [

    "🔗 **Fuente:**",

    "🔗 **Fuente original:**",

    "**Fuente:**",

    "**Fuente original:**"

]


for phrase in remove_phrases:

    ai_content = ai_content.replace(
        phrase,
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

    f"Este contenido fue preparado por Cal Bot "
    f"como borrador editorial. "
    f"Revisa la información antes de publicarlo "
    f"en #noticias.\n\n"

    f"-# Cal Bot V16 · {date_text}"

)


# ============================================================
# DISCORD: LÍMITE
# ============================================================

DISCORD_LIMIT = 1950


if len(final_message) > DISCORD_LIMIT:

    print(
        "Mensaje demasiado largo. Recortando contenido..."
    )


    prefix = (

        f"<@&{ROLE_ID}>\n\n"

        f"🧭 **{category}**\n\n"

        f"# {ai_title}\n\n"

    )


    suffix = (

        f"\n\n🔗 **Fuente original:** "
        f"{final_source_url}\n\n"

        f"⚠️ **REVISIÓN REQUERIDA**\n"

        f"Este contenido fue preparado por Cal Bot "
        f"como borrador editorial. "
        f"Revisa la información antes de publicarlo "
        f"en #noticias.\n\n"

        f"-# Cal Bot V16 · {date_text}"

    )


    available = (
        DISCORD_LIMIT
        - len(prefix)
        - len(suffix)
    )


    if available < 300:

        print(
            "Descartada: no es posible ajustar "
            "el mensaje al límite de Discord."
        )

        raise SystemExit(0)


    ai_content = (
        ai_content[:available]
        .rsplit(
            " ",
            1
        )[0]
        .rstrip()
    )


    final_message = (
        prefix
        + ai_content
        + suffix
    )


# ============================================================
# SEGURIDAD FINAL
# ============================================================

if len(final_message) > DISCORD_LIMIT:

    print(
        "ERROR: mensaje sigue superando "
        "el límite de Discord."
    )

    raise SystemExit(1)


# ============================================================
# ENVIAR DISCORD
# ============================================================

print("=" * 64)

print(
    "NOTICIA APROBADA."
)

print(
    "ENVIANDO A DISCORD..."
)

print("=" * 64)


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


except requests.RequestException as error:

    print("=" * 64)

    print(
        "ERROR DE CONEXIÓN CON DISCORD"
    )

    print(
        error
    )

    print(
        "La noticia NO se guardará."
    )

    print("=" * 64)

    raise SystemExit(1)


# ============================================================
# RESULTADO DISCORD
# ============================================================

print(
    "Discord HTTP:",
    discord_response.status_code
)


if not discord_response.ok:

    print("=" * 64)

    print(
        "ERROR DE DISCORD"
    )

    print(
        discord_response.text[:2000]
    )

    print(
        "La noticia NO se guardará."
    )

    print("=" * 64)

    raise SystemExit(1)


# ============================================================
# DISCORD CONFIRMÓ
# ============================================================

print("=" * 64)

print(
    "DISCORD CONFIRMÓ EL MENSAJE."
)

print("=" * 64)


# ============================================================
# GUARDAR HISTORIAL
# ============================================================

history[
    "published"
].append(
    selected["news_id"]
)


history[
    "titles"
].append(
    ai_title
)


history[
    "content_hashes"
].append(
    selected["content_hash"]
)


# ------------------------------------------------------------
# LIMITAR HISTORIAL
# ------------------------------------------------------------

history[
    "published"
] = history[
    "published"
][-MAX_HISTORY:]


history[
    "titles"
] = history[
    "titles"
][-MAX_HISTORY:]


history[
    "content_hashes"
] = history[
    "content_hashes"
][-MAX_HISTORY:]


# ============================================================
# GUARDAR
# ============================================================

try:

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


except Exception as error:

    print("=" * 64)

    print(
        "ADVERTENCIA: Discord recibió la noticia,"
        " pero no se pudo guardar el historial."
    )

    print(
        error
    )

    print("=" * 64)

    raise SystemExit(1)


# ============================================================
# FINAL
# ============================================================

print("=" * 64)

print(
    "NOTICIA PUBLICADA Y GUARDADA EN HISTORIAL."
)

print(
    "CAL BOT V16 FINALIZADO."
)

print("=" * 64)
