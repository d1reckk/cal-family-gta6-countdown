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
# CAL BOT V14
# EDITOR DE NOTICIAS DE CAL FAMILY
# ============================================================

print("=" * 60)
print("CAL BOT V14")
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

# ============================================================
# MODELOS GEMINI
# ============================================================

GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash"
]


# ============================================================
# VALIDACIÓN
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
    text = re.sub(
        r"[^a-z0-9áéíóúüñ ]+",
        " ",
        text
    )

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
# RSS
# ============================================================

print("Buscando noticias nuevas...")

feed = feedparser.parse(RSS_URL)

if not feed.entries:

    print("No se encontraron noticias.")

    raise SystemExit(0)


# ============================================================
# BUSCAR CANDIDATO
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
    # TÍTULOS REPETIDOS
    # --------------------------------------------------------

    duplicate = False

    for old_title in history["titles"]:

        if similarity(
            title,
            old_title
        ) >= 0.90:

            duplicate = True

            break


    if duplicate:

        print(
            "Ignorada: título demasiado parecido."
        )

        continue


    # --------------------------------------------------------
    # CONTENIDO RSS
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
    print("NO HAY NOTICIAS NUEVAS.")
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
        "Chrome/124.0 Safari/537.36"
}


try:

    response = requests.get(
        google_url,
        headers=headers,
        timeout=30,
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
        "No se pudo extraer el artículo:"
    )

    print(error)


# ============================================================
# CONTENIDO FINAL
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
# HISTORIAL PARA GEMINI
# ============================================================

previous_titles = history["titles"][-80:]

previous_titles_text = "\n".join(
    f"- {old_title}"
    for old_title in previous_titles
)


# ============================================================
# PROMPT V14
# ============================================================

prompt = f"""
Eres CAL BOT, editor automático de noticias de CAL FAMILY.

CAL FAMILY es una comunidad dedicada principalmente a
Grand Theft Auto VI.

Tu objetivo es encontrar noticias que realmente aporten
información útil a la comunidad.

PRIORIDAD:

CALIDAD > CANTIDAD.

============================================================
REGLA PRINCIPAL
============================================================

PUBLICA solamente si existe información:

- nueva
- concreta
- relevante
- suficientemente verificable

No publiques simplemente porque el artículo menciona GTA VI.

============================================================
EXTENDED LOOK / TRAILERS / GAMEPLAY
============================================================

ESTA REGLA ES MUY IMPORTANTE.

NO descartes automáticamente un artículo porque hable
del Extended Look, tráiler, gameplay o material oficial.

Un artículo SOBRE un evento ya conocido puede publicarse
SI contiene DATOS CONCRETOS que ayuden a documentar lo
que fue mostrado.

Ejemplos que PUEDEN ser publicables:

- una mecánica concreta mostrada
- una nueva ubicación identificada
- un personaje mostrado
- una misión concreta
- una actividad
- una característica de gameplay
- una interacción específica
- un vehículo mostrado
- un detalle del mundo
- una declaración nueva de Rockstar
- una explicación concreta de desarrollo
- información nueva proporcionada por desarrolladores
- un dato que el artículo documenta claramente y que
  no aparece en el historial

En esos casos:

PUBLICAR.

Pero si el artículo solamente dice:

"El Extended Look fue increíble."

"El nuevo vídeo muestra mucho contenido."

"GTA VI se ve espectacular."

"Esto es lo que pensamos del tráiler."

o simplemente da una opinión:

DESCARTAR.

============================================================
ARTÍCULOS QUE DEBEN DESCARTARSE
============================================================

DESCARTA:

- rumores reciclados
- filtraciones
- supuestas filtraciones sin fuente fiable
- especulación
- clickbait
- opiniones
- análisis sin información nueva
- artículos que solamente reaccionan
- artículos que repiten una noticia anterior
- artículos que cambian solamente el titular
- artículos que no contienen datos concretos
- artículos cuyo contenido no puede verificarse
- artículos que solamente hablan del impacto cultural
- artículos que solamente hablan de popularidad
- artículos que solamente dicen que GTA VI será enorme
- artículos que solamente comparan presupuestos
- artículos que solamente predicen ventas

============================================================
FUENTES SECUNDARIAS
============================================================

Una fuente secundaria SÍ puede utilizarse.

No es obligatorio que la información venga directamente
de Rockstar Games.

Sin embargo:

Si un medio secundario aporta información concreta,
atribuida claramente a una fuente o basada en material
oficial verificable:

PUEDE PUBLICARSE.

Si el medio simplemente repite un rumor:

DESCARTAR.

============================================================
HECHOS VS OPINIONES
============================================================

Clasifica mentalmente la información como:

1. HECHO OFICIAL
2. DECLARACIÓN DE ROCKSTAR
3. DECLARACIÓN DE TAKE-TWO
4. DECLARACIÓN DE DESARROLLADOR
5. INFORMACIÓN DE MEDIO
6. OBSERVACIÓN DEL MATERIAL OFICIAL
7. OPINIÓN
8. ANÁLISIS
9. RUMOR
10. ESPECULACIÓN

Nunca presentes:

OPINIÓN

RUMOR

ESPECULACIÓN

como hecho.

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
PREGUNTA EDITORIAL
============================================================

Antes de decidir, pregúntate:

"¿Qué información concreta aprendería un miembro
de CAL FAMILY leyendo esta noticia?"

Si la respuesta es algo concreto:

puede PUBLICARSE.

Si la respuesta es solamente:

"que otro medio habló de GTA VI"

DESCARTAR.

============================================================
DECISIÓN
============================================================

Selecciona:

PUBLICAR

o

DESCARTAR.

============================================================
FORMATO
============================================================

Devuelve exclusivamente JSON válido.

PUBLICAR:

{{
  "decision": "PUBLICAR",
  "reason": "motivo breve",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Contenido final"
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
CONTENIDO SI PUBLICAS
============================================================

Español.

500-1200 caracteres aproximadamente.

Profesional.

Natural.

No inventes datos.

No incluyas el enlace.

No digas que eres una IA.

No repitas información innecesariamente.

Utiliza esta estructura cuando tenga sentido:

[Introducción]

**El dato clave**

[Información concreta]

**Por qué importa**

[Importancia]

⚠️ **Contexto**

[Explica si procede de material oficial,
una declaración, un medio secundario, etc.]

============================================================
REGLA FINAL
============================================================

NO castigues automáticamente una noticia por hablar
del Extended Look.

Castiga la falta de información nueva.

Si contiene datos concretos nuevos:

PUBLICAR.

Si solamente comenta el evento:

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

            "temperature": 0.15,

            "maxOutputTokens": 2200,

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
                timeout=120
            )


            print(
                "Gemini HTTP:",
                gemini_response.status_code
            )


            if gemini_response.status_code == 200:

                gemini_result = (
                    gemini_response.json()
                )

                break


            if gemini_response.status_code in (
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
                gemini_response.text[:3000]
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
# SIN RESPUESTA
# ============================================================

if gemini_result is None:

    print("=" * 60)
    print("ERROR: GEMINI NO RESPONDIÓ.")
    print("La noticia NO será publicada.")
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
        "DESCARTADA: contenido vacío."
    )

    raise SystemExit(0)


# ============================================================
# SEGURIDAD CONTRA TÍTULOS REPETIDOS
# ============================================================

for old_title in history["titles"]:

    if similarity(
        ai_title,
        old_title
    ) >= 0.90:

        print("=" * 60)
        print(
            "DESCARTADA: título demasiado parecido "
            "a una noticia anterior."
        )
        print("=" * 60)

        raise SystemExit(0)


# ============================================================
# LIMPIEZA
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
# MENSAJE DISCORD
# ============================================================

final_message = (
    f"<@&{ROLE_ID}>\n\n"
    f"🧭 **{category}**\n\n"
    f"# {ai_title}\n\n"
    f"{ai_content}\n\n"
    f"🔗 **Fuente original:** {final_source_url}\n\n"
    f"⚠️ **REVISIÓN REQUERIDA**\n"
    f"Este contenido fue preparado por Cal Bot "
    f"como borrador editorial. "
    f"Revisa la información antes de publicarlo "
    f"en #noticias.\n\n"
    f"-# Cal Bot V14 · {date_text}"
)


# ============================================================
# LÍMITE DISCORD
# ============================================================

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

        raise SystemExit(0)


    ai_content = (
        ai_content[:allowed]
        .rsplit(" ", 1)[0]
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
        f"-# Cal Bot V14 · {date_text}"
    )


# ============================================================
# DISCORD
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
    print("La noticia NO será guardada.")
    print("=" * 60)

    raise SystemExit(1)


print(
    "Discord HTTP:",
    discord_response.status_code
)


if not discord_response.ok:

    print("=" * 60)
    print("ERROR DE DISCORD")
    print(
        discord_response.text
    )
    print("La noticia NO será guardada.")
    print("=" * 60)

    raise SystemExit(1)


# ============================================================
# CONFIRMACIÓN
# ============================================================

print("=" * 60)
print("DISCORD CONFIRMÓ EL MENSAJE.")
print("=" * 60)


# ============================================================
# GUARDAR HISTORIAL
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
print("NOTICIA PUBLICADA Y GUARDADA.")
print("CAL BOT V14 FINALIZADO.")
print("=" * 60)
