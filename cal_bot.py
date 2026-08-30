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
# CAL BOT V15
# EDITOR DE NOTICIAS DE CAL FAMILY
# ============================================================

print("=" * 60)
print("CAL BOT V15")
print("EDITOR DE NOTICIAS DE CAL FAMILY")
print("=" * 60)


# ============================================================
# CONFIGURACIÓN
# ============================================================

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HISTORY_FILE = "seen_news.json"

ROLE_ID = "1504921814759903343"

MAX_HISTORY = 500

MAX_NEWS_AGE_HOURS = 72

# Cantidad máxima de noticias que Gemini podrá evaluar
# en una sola ejecución.
MAX_CANDIDATES = 12

RSS_URL = (
    "https://news.google.com/rss/"
    "?q=GTA+VI"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)

# Modelos conocidos por funcionar con la API usada.
GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite"
]


# ============================================================
# FILTROS LOCALES
# ============================================================

# Estas palabras/frases hacen que una noticia sea ignorada
# antes de gastar una llamada a Gemini.

LOCAL_BLOCKLIST = [

    # Rumores / especulación
    "rumor",
    "rumors",
    "rumour",
    "rumours",
    "speculation",
    "speculative",
    "supposedly",
    "allegedly",
    "leak",
    "leaked",
    "leaks",
    "leaker",

    # Hilos / recopilaciones de filtraciones
    "everything leaked",
    "leaks so far",
    "what might be next",
    "leaked so far",

    # Artículos genéricos
    "best gta 6",
    "everything we know",
    "what we know so far",
    "all we know",
    "things we know",

    # Opiniones / impacto cultural
    "opinion",
    "editorial",
    "work blackout",
    "cultural impact",
    "will cause",

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
# NORMALIZAR TEXTO
# ============================================================

def normalize_text(text):

    if not text:
        return ""

    text = text.lower()

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

        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                "",
                new_query,
                ""
            )
        )

    except Exception:

        return url


# ============================================================
# ID
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

        history = {}

else:

    history = {}


if isinstance(history, list):

    history = {
        "published": history,
        "titles": [],
        "content_hashes": [],
        "rejected": []
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
    []
)

history.setdefault(
    "rejected",
    []
)


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
# OBTENER FECHA
# ============================================================

def get_entry_datetime(entry):

    parsed = getattr(
        entry,
        "published_parsed",
        None
    )

    if not parsed:

        parsed = getattr(
            entry,
            "updated_parsed",
            None
        )

    if not parsed:

        return None

    try:

        timestamp = calendar.timegm(
            parsed
        )

        return datetime.fromtimestamp(
            timestamp,
            timezone.utc
        )

    except Exception:

        return None


# ============================================================
# FILTRO LOCAL
# ============================================================

def local_filter(title):

    normalized = normalize_text(
        title
    )

    for blocked in LOCAL_BLOCKLIST:

        if blocked in normalized:

            return True

    return False


# ============================================================
# OBTENER ARTÍCULO
# ============================================================

def fetch_article(url):

    article_text = ""

    final_url = url

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True
        )

        print(
            "Estado de página:",
            response.status_code
        )

        final_url = response.url

        if not response.ok:

            return "", final_url

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        for element in soup(
            [
                "script",
                "style",
                "noscript",
                "svg",
                "nav",
                "footer",
                "header",
                "form",
                "iframe"
            ]
        ):

            element.decompose()

        paragraphs = []

        for paragraph in soup.find_all("p"):

            text = paragraph.get_text(
                " ",
                strip=True
            )

            if len(text) >= 50:

                paragraphs.append(
                    text
                )

        article_text = "\n".join(
            paragraphs
        )

        article_text = article_text[
            :18000
        ]

    except Exception as error:

        print(
            "Error obteniendo artículo:",
            error
        )

    return (
        article_text,
        final_url
    )


# ============================================================
# CARGAR RSS
# ============================================================

print("Buscando noticias nuevas...")

feed = feedparser.parse(
    RSS_URL
)

if not feed.entries:

    print(
        "No se encontraron noticias."
    )

    raise SystemExit(0)


# ============================================================
# CONSTRUIR LISTA DE CANDIDATAS
# ============================================================

now = datetime.now(
    timezone.utc
)

candidates = []


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
    # FILTRO DE FECHA
    # --------------------------------------------------------

    published_time = get_entry_datetime(
        entry
    )

    if published_time:

        age = (
            now
            - published_time
        )

        if age > timedelta(
            hours=MAX_NEWS_AGE_HOURS
        ):

            print(
                "Ignorada por antigüedad:",
                title
            )

            continue


    # --------------------------------------------------------
    # FILTRO LOCAL
    # --------------------------------------------------------

    if local_filter(title):

        print(
            "Ignorada por filtro local:",
            title
        )

        continue


    # --------------------------------------------------------
    # URL / ID
    # --------------------------------------------------------

    clean_url = canonical_url(
        google_url
    )

    news_id = article_id(
        title,
        clean_url
    )


    # --------------------------------------------------------
    # YA PUBLICADA
    # --------------------------------------------------------

    if news_id in history["published"]:

        print(
            "Ignorada: URL ya publicada."
        )

        continue


    # --------------------------------------------------------
    # YA RECHAZADA
    # --------------------------------------------------------

    if news_id in history["rejected"]:

        print(
            "Ignorada: candidata ya rechazada."
        )

        continue


    # --------------------------------------------------------
    # TÍTULO DUPLICADO
    # --------------------------------------------------------

    duplicate = False

    for old_title in history["titles"]:

        if similarity(
            title,
            old_title
        ) >= 0.86:

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


    candidates.append(
        {
            "id": news_id,
            "title": title,
            "google_url": google_url,
            "canonical_url": clean_url,
            "rss_content": rss_content
        }
    )


    if len(candidates) >= MAX_CANDIDATES:

        break


# ============================================================
# NO HAY CANDIDATAS
# ============================================================

if not candidates:

    print("=" * 60)
    print("NO HAY NOTICIAS CANDIDATAS.")
    print("=" * 60)

    raise SystemExit(0)


print("=" * 60)
print(
    f"CANDIDATAS ENCONTRADAS: {len(candidates)}"
)
print("=" * 60)


# ============================================================
# PROCESAR CANDIDATAS
# ============================================================

for candidate_index, candidate in enumerate(
    candidates,
    start=1
):

    title = candidate["title"]

    google_url = candidate["google_url"]

    rss_content = candidate["rss_content"]

    news_id = candidate["id"]


    print("=" * 60)

    print(
        f"EVALUANDO CANDIDATA "
        f"{candidate_index}/{len(candidates)}"
    )

    print(title)

    print(google_url)

    print("=" * 60)


    # ========================================================
    # OBTENER ARTÍCULO
    # ========================================================

    article_text, final_source_url = fetch_article(
        google_url
    )


    if len(article_text) >= 300:

        source_content = article_text

    elif len(rss_content) >= 100:

        source_content = rss_content

    else:

        print(
            "DESCARTADA: no hay suficiente información."
        )

        history["rejected"].append(
            news_id
        )

        continue


    # ========================================================
    # HASH
    # ========================================================

    content_hash = hashlib.sha256(
        normalize_text(
            source_content
        ).encode("utf-8")
    ).hexdigest()


    if content_hash in history["content_hashes"]:

        print(
            "DESCARTADA: contenido idéntico."
        )

        history["rejected"].append(
            news_id
        )

        continue


    # ========================================================
    # HISTORIAL PARA GEMINI
    # ========================================================

    previous_titles = history[
        "titles"
    ][-60:]


    previous_titles_text = "\n".join(
        f"- {old_title}"
        for old_title in previous_titles
    )


    # ========================================================
    # PROMPT
    # ========================================================

    prompt = f"""
Eres CAL BOT V15, editor de noticias de CAL FAMILY.

CAL FAMILY es una comunidad dedicada a Grand Theft Auto VI.

Tu trabajo es encontrar únicamente información que
merezca convertirse en una noticia para la comunidad.

PRIORIDAD:

CALIDAD > CANTIDAD.

============================================================
REGLA PRINCIPAL
============================================================

PUBLICA solamente si existe información:

1. NUEVA
2. CONCRETA
3. RELEVANTE
4. VERIFICABLE

Si no cumple esas condiciones:

DESCARTAR.

============================================================
NO PUBLICAR
============================================================

DESCARTA:

- rumores reciclados
- filtraciones
- leaks
- especulación
- artículos de opinión
- artículos editoriales
- análisis sin datos nuevos
- reacciones a trailers
- reacciones al Extended Look
- recopilaciones
- "todo lo que sabemos"
- artículos que simplemente repiten información anterior
- comparaciones generales
- artículos sobre popularidad
- artículos sobre impacto cultural
- artículos sobre expectativas
- artículos que no aporten información concreta
- información que no pueda verificarse

IMPORTANTE:

Un artículo nuevo NO significa necesariamente una noticia nueva.

Si 10 medios hablan del mismo acontecimiento,
eso sigue siendo UN SOLO acontecimiento.

Solo publica otro artículo si contiene un dato nuevo
que realmente no estaba disponible anteriormente.

============================================================
FUENTES SECUNDARIAS
============================================================

Una fuente secundaria puede publicarse si aporta
información nueva y concreta.

Pero si la fuente secundaria solamente repite:

"un rumor dice..."

"un insider afirma..."

"se cree que..."

"podría..."

"aparentemente..."

sin información verificable:

DESCARTAR.

============================================================
FUENTES OFICIALES
============================================================

No inventes declaraciones de:

Rockstar Games
Take-Two Interactive

No conviertas declaraciones de terceros en declaraciones
oficiales de Rockstar.

============================================================
HISTORIAL
============================================================

{previous_titles_text}

============================================================
ARTÍCULO ACTUAL
============================================================

TÍTULO:

{title}

FUENTE:

{final_source_url}

CONTENIDO:

{source_content}

============================================================
CLASIFICACIÓN
============================================================

Identifica correctamente si es:

1. HECHO OFICIAL
2. DECLARACIÓN DE UNA FUENTE
3. INFORMACIÓN DE UN MEDIO
4. OPINIÓN
5. ANÁLISIS
6. RUMOR
7. ESPECULACIÓN
8. FILTRACIÓN

Nunca presentes 4, 5, 6, 7 u 8 como hechos.

============================================================
EJEMPLO IMPORTANTE
============================================================

Si el título dice:

"GTA 6 Takes Around 80 Hours To Complete"

pero el contenido solamente repite una supuesta declaración
sin una fuente verificable:

DESCARTAR.

No debes aceptar automáticamente una afirmación solamente
porque aparece en el título.

============================================================
DECISIÓN
============================================================

Devuelve:

PUBLICAR

o

DESCARTAR

Solo PUBLICAR cuando realmente exista una noticia nueva.

============================================================
FORMATO JSON
============================================================

Devuelve EXCLUSIVAMENTE JSON válido.

PUBLICAR:

{{
  "decision": "PUBLICAR",
  "reason": "motivo breve",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Texto final"
}}

DESCARTAR:

{{
  "decision": "DESCARTAR",
  "reason": "motivo breve",
  "category": "Noticias",
  "title": "",
  "content": ""
}}

============================================================
SI PUBLICAS
============================================================

Idioma: español.

Extensión:

500-1100 caracteres aproximadamente.

Estilo:

- profesional
- natural
- atractivo
- informativo
- directo

NO inventes información.

NO incluyas el enlace.

NO menciones inteligencia artificial.

Estructura:

[Introducción]

**El dato clave**

[Información concreta]

**Por qué importa**

[Importancia real]

⚠️ **Contexto**

[Indica claramente si la información es oficial,
una declaración de una fuente o información secundaria.]

============================================================
REGLA FINAL
============================================================

Ante la duda:

DESCARTAR.

Si es realmente una noticia nueva:

PUBLICAR.
"""


    # ========================================================
    # GEMINI
    # ========================================================

    print("=" * 60)
    print("CAL BOT ESTÁ EVALUANDO...")
    print("=" * 60)


    gemini_result = None


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

                "maxOutputTokens": 1800,

                "responseMimeType":
                    "application/json"
            }
        }


        model_success = False


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

                    gemini_result = (
                        response.json()
                    )

                    model_success = True

                    break


                if response.status_code in (
                    429,
                    500,
                    502,
                    503,
                    504
                ):

                    wait_time = 5 * (
                        attempt + 1
                    )

                    print(
                        f"Reintentando en "
                        f"{wait_time} segundos..."
                    )

                    time.sleep(
                        wait_time
                    )

                    continue


                print(
                    response.text[:2000]
                )

                break


            except Exception as error:

                print(
                    "Error de conexión:",
                    error
                )

                if attempt < 2:

                    time.sleep(
                        5 * (attempt + 1)
                    )


        if model_success:

            break


    # ========================================================
    # GEMINI FALLÓ
    # ========================================================

    if gemini_result is None:

        print("=" * 60)
        print(
            "GEMINI NO RESPONDIÓ PARA ESTA CANDIDATA."
        )

        print(
            "Se probará la siguiente noticia."
        )

        print("=" * 60)

        continue


    # ========================================================
    # EXTRAER JSON
    # ========================================================

    try:

        candidates_response = gemini_result.get(
            "candidates",
            []
        )


        if not candidates_response:

            raise ValueError(
                "Gemini no devolvió candidates."
            )


        parts = (
            candidates_response[0]
            .get("content", {})
            .get("parts", [])
        )


        if not parts:

            raise ValueError(
                "Gemini no devolvió parts."
            )


        ai_text = (
            parts[0]
            .get("text", "")
            .strip()
        )


        if not ai_text:

            raise ValueError(
                "Gemini devolvió texto vacío."
            )


        ai_text = re.sub(
            r"^```json\s*",
            "",
            ai_text,
            flags=re.IGNORECASE
        )


        ai_text = re.sub(
            r"\s*```$",
            "",
            ai_text
        )


        result = json.loads(
            ai_text
        )


    except Exception as error:

        print("=" * 60)
        print(
            "ERROR INTERPRETANDO GEMINI:"
        )

        print(error)

        print("=" * 60)

        print(
            json.dumps(
                gemini_result,
                indent=2,
                ensure_ascii=False
            )[:5000]
        )

        print(
            "Se probará la siguiente candidata."
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


    # ========================================================
    # DESCARTADA
    # ========================================================

    if decision != "PUBLICAR":

        print("=" * 60)
        print("DESCARTADA POR CAL BOT")
        print("=" * 60)

        print(reason)

        # Guardamos la candidata como rechazada
        # para no volver a analizarla inmediatamente.

        history["rejected"].append(
            news_id
        )

        history["rejected"] = (
            history["rejected"][-MAX_HISTORY:]
        )

        continue


    # ========================================================
    # DATOS
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


    if category not in (
        "Noticias",
        "Análisis"
    ):

        category = "Noticias"


    # ========================================================
    # VALIDACIÓN
    # ========================================================

    if not ai_title or not ai_content:

        print(
            "DESCARTADA: Gemini no generó contenido válido."
        )

        history["rejected"].append(
            news_id
        )

        continue


    # ========================================================
    # TÍTULO REPETIDO
    # ========================================================

    repeated = False


    for old_title in history["titles"]:

        if similarity(
            ai_title,
            old_title
        ) >= 0.88:

            repeated = True
            break


    if repeated:

        print(
            "DESCARTADA: título demasiado parecido "
            "a una publicación anterior."
        )

        history["rejected"].append(
            news_id
        )

        continue


    # ========================================================
    # LIMPIEZA
    # ========================================================

    ai_content = ai_content.replace(
        "🔗 **Fuente:**",
        ""
    )

    ai_content = ai_content.replace(
        "🔗 **Fuente original:**",
        ""
    )

    ai_content = ai_content.strip()


    # ========================================================
    # FECHA
    # ========================================================

    date_text = datetime.now(
        timezone.utc
    ).strftime(
        "%d/%m/%Y"
    )


    # ========================================================
    # MENSAJE DISCORD
    # ========================================================

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
        f"-# Cal Bot V15 · {date_text}"
    )


    # ========================================================
    # LÍMITE DISCORD
    # ========================================================

    if len(final_message) > 1950:

        fixed_length = (
            len(final_message)
            - len(ai_content)
        )

        allowed = (
            1950
            - fixed_length
            - 10
        )


        if allowed < 300:

            print(
                "DESCARTADA: mensaje demasiado largo."
            )

            history["rejected"].append(
                news_id
            )

            continue


        ai_content = (
            ai_content[:allowed]
            .rstrip()
        )


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
            f"-# Cal Bot V15 · {date_text}"
        )


    # ========================================================
    # ENVIAR DISCORD
    # ========================================================

    print("=" * 60)
    print("ENVIANDO NOTICIA A DISCORD...")
    print("=" * 60)


    discord_payload = {

        "content": final_message,

        "username": "Cal Bot",

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

        print("=" * 60)
        print("ERROR DE CONEXIÓN CON DISCORD")
        print(error)
        print("=" * 60)

        # NO guardar como publicada.

        raise SystemExit(1)


    print(
        "Discord HTTP:",
        discord_response.status_code
    )


    # Discord webhook normalmente responde 204.

    if not discord_response.ok:

        print("=" * 60)
        print("ERROR DE DISCORD")
        print(discord_response.status_code)
        print(discord_response.text)
        print("=" * 60)

        # NO guardar como publicada.

        raise SystemExit(1)


    # ========================================================
    # DISCORD CONFIRMÓ
    # ========================================================

    print("=" * 60)
    print("DISCORD CONFIRMÓ EL MENSAJE.")
    print("=" * 60)


    # ========================================================
    # GUARDAR HISTORIAL
    # ========================================================

    history["published"].append(
        news_id
    )

    history["titles"].append(
        ai_title
    )

    history["content_hashes"].append(
        content_hash
    )


    history["published"] = (
        history["published"][-MAX_HISTORY:]
    )

    history["titles"] = (
        history["titles"][-MAX_HISTORY:]
    )

    history["content_hashes"] = (
        history["content_hashes"][-MAX_HISTORY:]
    )

    history["rejected"] = (
        history["rejected"][-MAX_HISTORY:]
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


    # ========================================================
    # FINAL
    # ========================================================

    print("=" * 60)
    print("NOTICIA PUBLICADA Y GUARDADA.")
    print("=" * 60)

    print(
        "Título:",
        ai_title
    )

    print(
        "Fuente:",
        final_source_url
    )

    # IMPORTANTE:
    # Terminamos después de publicar UNA noticia.
    raise SystemExit(0)


# ============================================================
# NINGUNA CANDIDATA FUE PUBLICADA
# ============================================================

print("=" * 60)
print("NINGUNA NOTICIA CUMPLIÓ LOS CRITERIOS.")
print("=" * 60)

raise SystemExit(0)
