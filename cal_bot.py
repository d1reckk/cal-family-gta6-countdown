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

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HISTORY_FILE = "seen_news.json"

ROLE_ID = "1504921814759903343"

MAX_HISTORY = 500
MAX_NEWS_AGE_HOURS = 72
MAX_CANDIDATES = 15

RSS_URL = (
    "https://news.google.com/rss/"
    "?q=GTA+VI"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)

# Gemini 3.7 es la primera opción.
# Gemini 3.6 funciona como respaldo.
GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash"
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
        "content_hashes": []
    }


history.setdefault("published", [])
history.setdefault("titles", [])
history.setdefault("content_hashes", [])


# ============================================================
# FILTRO LOCAL
# ============================================================

def obvious_local_reject(title):

    normalized = normalize_text(title)

    blocked_patterns = [

        # Opinión / reacción
        "opinion",
        "my thoughts",
        "what i think",
        "i think",
        "reaction",
        "reacts",
        "wowed",
        "driving me crazy",

        # Guías / recomendaciones
        "best console",
        "best pc",
        "buy for gta",
        "guide",
        "tips and tricks",

        # Recopilaciones
        "everything leaked so far",
        "everything revealed",
        "everything we know",
        "all the actors",
        "all the details",

        # Análisis
        "why rockstar",
        "why gta",
        "analysis",
        "explained",

    ]

    for pattern in blocked_patterns:

        if pattern in normalized:

            return True

    return False


# ============================================================
# BUSCAR RSS
# ============================================================

print("Buscando noticias nuevas...")

feed = feedparser.parse(RSS_URL)

if not feed.entries:

    print("No se encontraron noticias.")
    raise SystemExit(0)


# ============================================================
# RECOPILAR CANDIDATAS
# ============================================================

candidates = []

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


    if news_id in history["published"]:

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
    print("NO HAY NOTICIAS NUEVAS.")
    print("=" * 64)

    raise SystemExit(0)


print("=" * 64)
print(
    f"CANDIDATAS ENCONTRADAS: {len(candidates)}"
)
print("=" * 64)


# ============================================================
# HEADERS
# ============================================================

headers = {
    "User-Agent":
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
}


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
                "maxOutputTokens": 2200,
                "responseMimeType": "application/json"
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


                if response.status_code in (
                    429,
                    500,
                    502,
                    503,
                    504
                ):

                    wait = 5 * (attempt + 1)

                    print(
                        f"Reintentando en {wait} segundos..."
                    )

                    time.sleep(wait)

                    continue


                print(
                    response.text[:1500]
                )

                break


            except Exception as error:

                print(
                    "Error Gemini:",
                    error
                )

                if attempt < 2:

                    time.sleep(
                        5 * (attempt + 1)
                    )


    return None


# ============================================================
# EXTRAER JSON
# ============================================================

def parse_gemini(result):

    if not result:
        return None

    try:

        candidates = result.get(
            "candidates",
            []
        )

        if not candidates:
            return None


        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )


        if not parts:
            return None


        text = (
            parts[0]
            .get("text", "")
            .strip()
        )


        if not text:
            return None


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


        return json.loads(text)


    except Exception as error:

        print(
            "Error interpretando Gemini:",
            error
        )

        return None


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
    # OBTENER ARTÍCULO
    # --------------------------------------------------------

    article_text = ""
    final_source_url = candidate["google_url"]


    try:

        response = requests.get(
            candidate["google_url"],
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


            article_text = article_text[:20000]


    except Exception as error:

        print(
            "Error obteniendo artículo:",
            error
        )


    # --------------------------------------------------------
    # CONTENIDO
    # --------------------------------------------------------

    if len(article_text) >= 300:

        source_content = article_text

    elif len(candidate["rss_content"]) >= 100:

        source_content = candidate["rss_content"]

    else:

        print(
            "Descartada: contenido insuficiente."
        )

        continue


    # --------------------------------------------------------
    # HISTORIAL
    # --------------------------------------------------------

    previous_titles = history[
        "titles"
    ][-80:]


    previous_titles_text = "\n".join(
        f"- {title}"
        for title in previous_titles
    )


    # --------------------------------------------------------
    # PROMPT V16
    # --------------------------------------------------------

    prompt = f"""
Eres el editor principal de noticias de CAL FAMILY,
una comunidad dedicada a Grand Theft Auto VI.

Tu trabajo es decidir si una noticia merece convertirse
en una publicación.

NO debes publicar basura, rumores reciclados,
opiniones o artículos que simplemente repiten noticias.

Pero tampoco debes exigir que toda información proceda
directamente de Rockstar Games.

============================================================
REGLA CENTRAL V16
============================================================

PUBLICAR si existe una NOVEDAD REAL que tenga:

1. Un dato concreto.
2. Una fuente identificable.
3. Una afirmación atribuible.
4. Información suficientemente verificable.
5. Relevancia para GTA VI.

La fuente puede ser:

- Rockstar Games.
- Take-Two.
- Un desarrollador identificado.
- Un actor identificado.
- Una autoridad gubernamental.
- Un documento público.
- Una entrevista.
- Un medio periodístico fiable.
- Una empresa involucrada directamente.
- Una fuente secundaria fiable que aporte información
  concreta y atribuida.

NO es obligatorio que Rockstar haya confirmado personalmente
cada dato.

============================================================
DESCARTAR
============================================================

DESCARTA si es principalmente:

- Rumor sin respaldo.
- Leak sin confirmación.
- Especulación.
- Predicción.
- Teoría.
- Opinión.
- Reacción.
- Review.
- Análisis subjetivo.
- Guía de compra.
- Comparación de consolas.
- Artículo genérico.
- Clickbait.
- Recopilación de información ya conocida.
- Repetición de una noticia anterior.
- Otro artículo que solamente comenta el mismo tráiler.
- Otro artículo que solamente comenta el Extended Look.
- Otro artículo que solamente comenta una noticia anterior.
- Una afirmación atribuida a un "insider" anónimo sin respaldo.

============================================================
IMPORTANTE SOBRE FUENTES SECUNDARIAS
============================================================

NO hagas esto:

"TechPowerUp dice que GTA VI dura 80 horas"
→ DESCARTAR automáticamente.

Primero determina:

¿El artículo presenta una declaración concreta?

¿Identifica al desarrollador?

¿Existe contexto suficiente?

¿El artículo contiene evidencia o una fuente verificable?

Si sí, puede PUBLICARSE aunque TechPowerUp sea una
fuente secundaria.

Pero si solamente repite una afirmación sin evidencia:

DESCARTAR.

============================================================
EJEMPLOS
============================================================

EJEMPLO 1:

"Un desarrollador de Rockstar revela en una entrevista
un detalle concreto sobre una mecánica del juego."

→ PUBLICAR.

EJEMPLO 2:

"Un medio dice que GTA VI probablemente tendrá 60 FPS."

→ DESCARTAR.

EJEMPLO 3:

"Un organismo gubernamental publica una propuesta
relacionada directamente con el lanzamiento de GTA VI."

→ PUBLICAR si el documento/propuesta está realmente
explicado en el artículo.

EJEMPLO 4:

"IGN publica su reacción al Extended Look."

→ DESCARTAR.

EJEMPLO 5:

"Un desarrollador explica públicamente por qué una
característica fue implementada de determinada manera."

→ PUBLICAR.

EJEMPLO 6:

"Un artículo recopila todo lo mostrado en el Extended Look."

→ DESCARTAR.

EJEMPLO 7:

"Un medio reporta una nueva información concreta
procedente de una entrevista/documento/fuente identificada."

→ PUBLICAR.

============================================================
DIFERENCIA ENTRE ANÁLISIS Y NOTICIA
============================================================

Un artículo puede contener análisis y aun así ser
publicable si dentro contiene un DATO NUEVO verificable.

No descartes automáticamente una noticia solo porque
también contiene análisis.

La pregunta principal es:

¿Existe un dato nuevo que CAL FAMILY pueda comunicar?

Si sí:

PUBLICAR.

Si no:

DESCARTAR.

============================================================
HISTORIAL
============================================================

{previous_titles_text}

============================================================
ARTÍCULO
============================================================

TÍTULO:
{candidate["title"]}

URL:
{final_source_url}

CONTENIDO:
{source_content}

============================================================
VERIFICACIÓN
============================================================

Antes de decidir:

1. Identifica cuál es el dato nuevo.
2. Identifica quién lo afirma.
3. Determina si la afirmación está respaldada.
4. Comprueba si ya aparece en el historial.
5. Determina si el artículo es realmente una novedad.
6. Determina si es relevante para GTA VI.

Si no puedes identificar claramente un dato nuevo:

DESCARTAR.

============================================================
DECISIÓN
============================================================

Devuelve solamente JSON válido.

Formato PUBLICAR:

{{
  "decision": "PUBLICAR",
  "reason": "Motivo breve",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Texto final"
}}

Formato DESCARTAR:

{{
  "decision": "DESCARTAR",
  "reason": "Motivo breve",
  "category": "Noticias",
  "title": "",
  "content": ""
}}

============================================================
SI PUBLICAS
============================================================

Escribe en español.

Entre 500 y 1100 caracteres.

Profesional.

Natural.

No inventes datos.

No inventes declaraciones.

No presentes rumores como hechos.

No incluyas el enlace dentro del contenido.

Utiliza esta estructura cuando tenga sentido:

[Introducción]

**El dato clave**

[Dato concreto]

**Por qué importa**

[Contexto]

⚠️ **Contexto**

[Indicar si procede de una declaración,
documento, entrevista, fuente secundaria, etc.]

============================================================
REGLA FINAL
============================================================

No seas demasiado estricto.

NO necesitas una confirmación directa de Rockstar
para cada noticia.

Pero tampoco aceptes rumores sin respaldo.

Busca el equilibrio:

NOVEDAD + DATO CONCRETO + FUENTE IDENTIFICABLE
+ INFORMACIÓN VERIFICABLE = PUBLICAR

OPINIÓN + ESPECULACIÓN + REPETICIÓN + RUMOR SIN RESPALDO
= DESCARTAR.
"""


    print(
        "CAL BOT ESTÁ EVALUANDO..."
    )


    gemini_raw = ask_gemini(
        prompt
    )


    result = parse_gemini(
        gemini_raw
    )


    if not result:

        print(
            "No se pudo interpretar la respuesta."
        )

        continue


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

        print("=" * 64)
        print("DESCARTADA POR CAL BOT")
        print("=" * 64)
        print(reason)

        continue


    # --------------------------------------------------------
    # DATOS PUBLICABLES
    # --------------------------------------------------------

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
            "Descartada: contenido generado inválido."
        )

        continue


    # --------------------------------------------------------
    # SEGURIDAD CONTRA REPETICIONES
    # --------------------------------------------------------

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
            "Descartada: título demasiado parecido "
            "a una publicación anterior."
        )

        continue


    # --------------------------------------------------------
    # HASH
    # --------------------------------------------------------

    content_hash = hashlib.sha256(
        normalize_text(
            source_content
        ).encode("utf-8")
    ).hexdigest()


    if content_hash in history[
        "content_hashes"
    ]:

        print(
            "Descartada: contenido idéntico."
        )

        continue


    # --------------------------------------------------------
    # SELECCIONADA
    # --------------------------------------------------------

    selected = {
        "news_id": candidate["id"],
        "title": ai_title,
        "content": ai_content,
        "category": category,
        "source_url": final_source_url,
        "content_hash": content_hash,
        "reason": reason
    }

    break


# ============================================================
# NINGUNA NOTICIA
# ============================================================

if selected is None:

    print("=" * 64)
    print("NINGUNA NOTICIA CUMPLIÓ LOS CRITERIOS.")
    print("=" * 64)

    raise SystemExit(0)


# ============================================================
# LIMPIAR
# ============================================================

ai_title = selected["title"]
ai_content = selected["content"]
category = selected["category"]
final_source_url = selected["source_url"]


ai_content = ai_content.replace(
    "🔗 **Fuente:**",
    ""
)

ai_content = ai_content.replace(
    "🔗 **Fuente original:**",
    ""
)

ai_content = ai_content.strip()


date_text = datetime.now(
    timezone.utc
).strftime("%d/%m/%Y")


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
# LÍMITE DISCORD
# ============================================================

if len(final_message) > 1950:

    print(
        "Mensaje demasiado largo. Recortando..."
    )

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
            "Descartada: mensaje demasiado largo."
        )

        raise SystemExit(0)


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
        f"-# Cal Bot V16 · {date_text}"
    )


# ============================================================
# DISCORD
# ============================================================

print("=" * 64)
print("NOTICIA APROBADA.")
print("ENVIANDO A DISCORD...")
print("=" * 64)


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

    print("=" * 64)
    print("ERROR DE CONEXIÓN CON DISCORD")
    print(error)
    print("La noticia NO se guardará.")
    print("=" * 64)

    raise SystemExit(1)


print(
    "Discord HTTP:",
    discord_response.status_code
)


if not discord_response.ok:

    print("=" * 64)
    print("ERROR DE DISCORD")
    print(discord_response.text)
    print("La noticia NO se guardará.")
    print("=" * 64)

    raise SystemExit(1)


# ============================================================
# DISCORD CONFIRMÓ
# ============================================================

print("=" * 64)
print("DISCORD CONFIRMÓ EL MENSAJE.")
print("=" * 64)


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


print("=" * 64)
print("NOTICIA PUBLICADA Y GUARDADA EN HISTORIAL.")
print("CAL BOT V16 FINALIZADO.")
print("=" * 64)
