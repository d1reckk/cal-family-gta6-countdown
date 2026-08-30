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
# CAL BOT V13
# EDITOR DE NOTICIAS DE CAL FAMILY
# ============================================================

print("=" * 60)
print("CAL BOT V13")
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

RSS_URL = (
    "https://news.google.com/rss/"
    "?q=GTA+VI"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)

# Modelos actuales / respaldo
GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
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
        "gta 6": "gta vi",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^a-z0-9áéíóúüñ ]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# SIMILITUD
# ============================================================

def similarity(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    return SequenceMatcher(None, a, b).ratio()


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

        return urlunparse((
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            urlencode(clean_query, doseq=True),
            ""
        ))

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
# HTML
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
        "content_hashes": []
    }


history.setdefault("published", [])
history.setdefault("titles", [])
history.setdefault("content_hashes", [])


# ============================================================
# BUSCAR RSS
# ============================================================

print("Buscando noticias nuevas...")

feed = feedparser.parse(RSS_URL)

if not feed.entries:

    print("No se encontraron noticias.")
    raise SystemExit(0)


# ============================================================
# FILTRO LOCAL
# Evita gastar Gemini en basura obvia
# ============================================================

BLOCKED_PATTERNS = [

    r"\b80\s*hours?\b",
    r"\b80\s*horas?\b",

    r"\brumou?r\b",
    r"\brumores?\b",
    r"\brumored\b",

    r"\bleak\b",
    r"\bleaked\b",
    r"\bfiltraci[oó]n\b",
    r"\bfiltrado\b",
    r"\bfiltrada\b",

    r"\bspeculation\b",
    r"\bspeculaci[oó]n\b",
    r"\bspeculative\b",

    r"\bmy\s+thoughts\b",
    r"\bopinion\b",
    r"\bopini[oó]n\b",

    r"\bwhy\s+i\b",
    r"\bwhat\s+i\s+think\b",
    r"\bwhat\s+we\s+think\b",

    r"\bwork\s+blackout\b",

    r"\breaction\b",
    r"\breacci[oó]n\b",

]


def obvious_bad_candidate(title):

    normalized = normalize_text(title)

    for pattern in BLOCKED_PATTERNS:

        if re.search(
            pattern,
            normalized,
            re.IGNORECASE
        ):

            return True

    return False


# ============================================================
# CANDIDATO
# ============================================================

candidate = None

now = datetime.now(timezone.utc)


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
    # FILTRO RÁPIDO
    # --------------------------------------------------------

    if obvious_bad_candidate(title):

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

            published_time = datetime.fromtimestamp(
                timestamp,
                timezone.utc
            )

        except Exception:

            published_time = None


    if published_time:

        age = now - published_time

        if age > timedelta(
            hours=MAX_NEWS_AGE_HOURS
        ):

            print(
                "Ignorada: noticia demasiado antigua."
            )

            continue


    # --------------------------------------------------------
    # ID
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
            "Ignorada: URL ya procesada."
        )

        continue


    # --------------------------------------------------------
    # TÍTULO REPETIDO
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
    # RSS
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


    candidate = {
        "id": news_id,
        "title": title,
        "google_url": google_url,
        "canonical_url": clean_url,
        "rss_content": rss_content
    }

    break


# ============================================================
# SIN CANDIDATO
# ============================================================

if candidate is None:

    print("=" * 60)
    print("NO HAY NOTICIAS NUEVAS PUBLICABLES.")
    print("=" * 60)

    raise SystemExit(0)


title = candidate["title"]
google_url = candidate["google_url"]
rss_content = candidate["rss_content"]
news_id = candidate["id"]


print("=" * 60)
print("CANDIDATO ENCONTRADO")
print(title)
print(google_url)
print("=" * 60)


# ============================================================
# EXTRAER ARTÍCULO
# ============================================================

article_text = ""

final_source_url = google_url


headers = {
    "User-Agent":
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124 Safari/537.36"
}


try:

    response = requests.get(
        google_url,
        headers=headers,
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

        for element in soup([
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "form"
        ]):

            element.decompose()


        paragraphs = []

        for paragraph in soup.find_all("p"):

            text = paragraph.get_text(
                " ",
                strip=True
            )

            if len(text) >= 50:

                paragraphs.append(text)


        article_text = "\n".join(
            paragraphs
        )

        article_text = article_text[:18000]


except Exception as error:

    print(
        "No se pudo extraer el artículo:"
    )

    print(error)


# ============================================================
# CONTENIDO
# ============================================================

if len(article_text) >= 300:

    source_content = article_text

elif len(rss_content) >= 100:

    source_content = rss_content

else:

    print("=" * 60)
    print("DESCARTADA: no hay suficiente información.")
    print("=" * 60)

    raise SystemExit(0)


# ============================================================
# HASH
# ============================================================

content_hash = hashlib.sha256(
    normalize_text(
        source_content
    ).encode("utf-8")
).hexdigest()


if content_hash in history["content_hashes"]:

    print(
        "DESCARTADA: contenido idéntico."
    )

    raise SystemExit(0)


# ============================================================
# HISTORIAL
# ============================================================

previous_titles = history["titles"][-60:]

previous_titles_text = "\n".join(
    f"- {old_title}"
    for old_title in previous_titles
)


# ============================================================
# PROMPT
# ============================================================

prompt = f"""
Eres CAL BOT V13, editor de noticias de CAL FAMILY.

Tu trabajo es encontrar SOLO noticias que aporten
información nueva, concreta y verificable sobre GTA VI.

CALIDAD > CANTIDAD.

============================================================
DESCARTAR
============================================================

DESCARTA inmediatamente si es:

- rumor
- filtración no verificada
- especulación
- opinión
- reacción
- análisis sin información nueva
- artículo que solamente comenta un tráiler existente
- artículo que solamente comenta el Extended Look
- artículo que repite una noticia anterior
- artículo que cambia solamente el medio
- artículo sobre popularidad general de GTA VI
- artículo sobre expectativas generales
- artículo sobre impacto cultural sin datos nuevos
- información sin fuente verificable
- una supuesta duración del juego no confirmada oficialmente

IMPORTANTE:

Una fuente secundaria SÍ puede publicarse si aporta
un dato concreto NUEVO y verificable.

No es obligatorio que toda noticia venga directamente
de Rockstar Games.

============================================================
PUBLICAR
============================================================

Ejemplos de información que puede ser publicable:

- anuncio oficial
- nueva fecha
- retraso confirmado
- nuevo tráiler
- nuevas imágenes oficiales
- nueva información de personajes
- nueva información de gameplay
- nueva información del mapa
- nueva información técnica
- declaraciones verificables de Rockstar
- declaraciones verificables de Take-Two
- información empresarial concreta
- información de desarrollo respaldada por una fuente fiable
- entrevista con información nueva
- información nueva descubierta por un medio fiable

============================================================
REGLA CRÍTICA
============================================================

NO conviertas un rumor en hecho.

NO conviertas una opinión en noticia.

NO conviertas una predicción en confirmación.

NO inventes información.

============================================================
HISTORIAL
============================================================

{previous_titles_text}

============================================================
ARTÍCULO
============================================================

TÍTULO:
{title}

FUENTE:
{final_source_url}

CONTENIDO:
{source_content}

============================================================
DECISIÓN
============================================================

Responde solamente con JSON válido:

{{
  "decision": "PUBLICAR",
  "reason": "motivo",
  "category": "Noticias",
  "title": "título en español",
  "content": "contenido final"
}}

o:

{{
  "decision": "DESCARTAR",
  "reason": "motivo",
  "category": "Noticias",
  "title": "",
  "content": ""
}}

============================================================
SI PUBLICAS
============================================================

Español.

500-1100 caracteres aproximadamente.

Profesional.

Natural.

Sin inventar.

Sin enlace dentro del contenido.

Estructura:

[Introducción]

**El dato clave**

[Dato]

**Por qué importa**

[Importancia]

⚠️ **Contexto**

[Contexto y nivel de confirmación]

============================================================
DECISIÓN FINAL
============================================================

Si no estás seguro:

DESCARTAR.
"""


# ============================================================
# GEMINI
# ============================================================

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

            "maxOutputTokens": 1800,

            "responseMimeType":
                "application/json"
        }
    }


    for attempt in range(3):

        try:

            gemini_response = requests.post(
                endpoint,
                headers={
                    "Content-Type":
                        "application/json"
                },
                json=payload,
                timeout=90
            )


            status = gemini_response.status_code

            print(
                "Gemini HTTP:",
                status
            )


            if status == 200:

                gemini_result = (
                    gemini_response.json()
                )

                break


            # Modelo inexistente:
            # no perder tiempo reintentando.
            if status == 404:

                print(
                    "Modelo no disponible. "
                    "Probando siguiente modelo."
                )

                break


            # Rate limit / servidor ocupado.
            if status in (
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
                gemini_response.text[:2000]
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


    if gemini_result is not None:

        break


# ============================================================
# ERROR GEMINI
# ============================================================

if gemini_result is None:

    print("=" * 60)
    print("ERROR: NINGÚN MODELO GEMINI RESPONDIÓ.")
    print("La noticia NO será guardada.")
    print("=" * 60)

    raise SystemExit(1)


# ============================================================
# EXTRAER JSON
# ============================================================

try:

    candidates = gemini_result.get(
        "candidates",
        []
    )

    if not candidates:

        raise ValueError(
            "Gemini no devolvió candidates."
        )


    parts = (
        candidates[0]
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
    print("ERROR INTERPRETANDO GEMINI")
    print(error)
    print("=" * 60)

    print(
        json.dumps(
            gemini_result,
            indent=2,
            ensure_ascii=False
        )[:5000]
    )

    raise SystemExit(1)


# ============================================================
# DECISIÓN
# ============================================================

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

    print("=" * 60)
    print("DESCARTADA POR CAL BOT")
    print("=" * 60)
    print(reason)

    raise SystemExit(0)


# ============================================================
# DATOS
# ============================================================

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


if not ai_title or not ai_content:

    print(
        "DESCARTADA: contenido inválido."
    )

    raise SystemExit(0)


# ============================================================
# SEGURIDAD
# ============================================================

for old_title in history["titles"]:

    if similarity(
        ai_title,
        old_title
    ) >= 0.88:

        print(
            "DESCARTADA: título repetido."
        )

        raise SystemExit(0)


# ============================================================
# LIMPIAR
# ============================================================

ai_content = ai_content.replace(
    "🔗 **Fuente:**",
    ""
)

ai_content = ai_content.replace(
    "🔗 **Fuente original:**",
    ""
)

ai_content = ai_content.strip()


# ============================================================
# FECHA
# ============================================================

date_text = datetime.now(
    timezone.utc
).strftime("%d/%m/%Y")


# ============================================================
# DISCORD
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
    f"-# Cal Bot V13 · {date_text}"
)


# ============================================================
# LIMITE DISCORD
# ============================================================

if len(final_message) > 1950:

    fixed = len(final_message) - len(ai_content)

    allowed = 1950 - fixed - 10

    if allowed < 300:

        print(
            "DESCARTADA: mensaje demasiado largo."
        )

        raise SystemExit(0)

    ai_content = ai_content[:allowed].rstrip()

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
        f"-# Cal Bot V13 · {date_text}"
    )


# ============================================================
# ENVIAR DISCORD
# ============================================================

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

    raise SystemExit(1)


print(
    "Discord HTTP:",
    discord_response.status_code
)


if not discord_response.ok:

    print("=" * 60)
    print("ERROR DE DISCORD")
    print(discord_response.status_code)
    print(discord_response.text)
    print("=" * 60)

    raise SystemExit(1)


# ============================================================
# GUARDAR HISTORIAL SOLO DESPUÉS DE DISCORD
# ============================================================

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


print("=" * 60)
print("DISCORD CONFIRMÓ EL MENSAJE.")
print("NOTICIA PUBLICADA Y GUARDADA.")
print("=" * 60)
