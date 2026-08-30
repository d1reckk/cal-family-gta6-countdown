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
# CAL BOT V12
# EDITOR DE NOTICIAS DE CAL FAMILY
# ============================================================

print("=" * 60)
print("CAL BOT V12")
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

# Modelos Gemini actuales.
# Se intenta primero el principal y después el respaldo.
GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite"
]


# ============================================================
# VALIDAR SECRETS
# ============================================================

if not WEBHOOK:
    print("ERROR: falta el secret NEWS_DRAFT_WEBHOOK.")
    raise SystemExit(1)

if not GEMINI_KEY:
    print("ERROR: falta el secret GEMINI_API_KEY.")
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
# ID DE NOTICIA
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
# BUSCAR RSS
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
    # URL YA PROCESADA
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
# NO HAY CANDIDATO
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
# OBTENER ARTÍCULO REAL
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
# ELEGIR CONTENIDO
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
# HASH DEL CONTENIDO
# ============================================================

content_hash = hashlib.sha256(
    normalize_text(
        source_content
    ).encode("utf-8")
).hexdigest()


if content_hash in history["content_hashes"]:

    print("=" * 60)
    print("DESCARTADA: contenido idéntico.")
    print("=" * 60)

    raise SystemExit(0)


# ============================================================
# HISTORIAL PARA GEMINI
# ============================================================

previous_titles = history["titles"][-60:]

previous_titles_text = "\n".join(
    f"- {old_title}"
    for old_title in previous_titles
)


# ============================================================
# PROMPT EDITORIAL
# ============================================================

prompt = f"""
Eres CAL BOT, el editor de noticias de CAL FAMILY.

CAL FAMILY es una comunidad dedicada principalmente a
Grand Theft Auto VI.

Tu función es actuar como un EDITOR HUMANO.

Tu prioridad absoluta es:

CALIDAD > CANTIDAD.

============================================================
REGLA PRINCIPAL
============================================================

PUBLICA solamente cuando exista información NUEVA,
CONCRETA y VERIFICABLE.

DESCARTA cuando el artículo:

- Repite una noticia anterior.
- Es una nueva versión del mismo anuncio.
- Solo cambia el medio que publicó la noticia.
- Solo reacciona a un tráiler o vídeo ya conocido.
- Es una opinión personal.
- Es un análisis sin datos nuevos.
- Es especulación.
- Es un rumor reciclado.
- Repite información que ya está en el historial.
- Habla de expectativas generales.
- Habla de que GTA VI es popular.
- Habla de que el juego será grande.
- No proporciona información concreta.
- No puede verificarse correctamente.

Si tienes dudas:

DESCARTA.

Es preferible no publicar nada antes que publicar
información reciclada.

============================================================
ARTÍCULO NUEVO VS. ARTÍCULO NUEVO SOBRE UN EVENTO VIEJO
============================================================

No confundas:

NOTICIA NUEVA

con:

ARTÍCULO NUEVO SOBRE UNA NOTICIA VIEJA.

Si Rockstar publica un vídeo, tráiler, anuncio o información
y después aparecen muchos artículos reaccionando al mismo
acontecimiento, esos artículos NO son automáticamente
noticias nuevas.

Solo PUBLICA si el nuevo artículo aporta un dato concreto
que anteriormente no estaba disponible.

============================================================
FUENTES
============================================================

Una fuente secundaria puede utilizarse si aporta información
nueva y verificable.

Pero si solamente repite un rumor:

DESCARTA.

Las opiniones de medios como IGN, GameSpot, PC Gamer,
Kotaku, etc. no deben publicarse como noticia salvo que
contengan información concreta nueva.

============================================================
HISTORIAL
============================================================

{previous_titles_text}

============================================================
ARTÍCULO ACTUAL
============================================================

TÍTULO:
{title}

URL:
{final_source_url}

CONTENIDO:
{source_content}

============================================================
CLASIFICACIÓN
============================================================

Determina si lo encontrado es:

1. HECHO OFICIAL
2. DECLARACIÓN DE UNA FUENTE
3. INFORMACIÓN DE UN MEDIO
4. OPINIÓN
5. ANÁLISIS
6. RUMOR
7. ESPECULACIÓN

Nunca presentes opinión, rumor o especulación como hecho.

============================================================
NO INVENTAR
============================================================

No inventes ni atribuyas a Rockstar Games o Take-Two
información que no aparezca en la fuente.

No inventes:

- fechas
- precios
- duración
- tamaño
- personajes
- mapa
- vehículos
- armas
- misiones
- ventas
- requisitos
- características
- declaraciones
- fechas de lanzamiento

============================================================
DECISIÓN
============================================================

Selecciona:

PUBLICAR

o

DESCARTAR.

Solo selecciona PUBLICAR si el artículo aporta una
novedad suficientemente importante para CAL FAMILY.

============================================================
FORMATO JSON
============================================================

Devuelve exclusivamente JSON válido.

Si PUBLICAR:

{{
  "decision": "PUBLICAR",
  "reason": "motivo breve",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Texto final"
}}

Si DESCARTAR:

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

El texto debe:

- Estar en español.
- Ser profesional.
- Ser natural.
- Tener aproximadamente 500-1100 caracteres.
- No inventar información.
- No incluir el enlace.
- No mencionar que eres una IA.
- No rellenar con información genérica.

Estructura:

[Introducción]

**El dato clave**

[Información concreta]

**Por qué importa**

[Importancia real]

⚠️ **Contexto**

[Explicación sobre si es oficial, declaración,
análisis o información secundaria.]

============================================================
REGLA FINAL
============================================================

Si no aporta información concreta nueva:

DESCARTAR.

Si solamente vuelve a hablar del mismo acontecimiento:

DESCARTAR.

Si es opinión:

DESCARTAR.

Si es rumor reciclado:

DESCARTAR.

Si es noticia realmente nueva y verificable:

PUBLICAR.
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
            "temperature": 0.1,
            "maxOutputTokens": 1800,
            "responseMimeType": "application/json"
        }
    }


    model_success = False


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


            print(
                "Gemini HTTP:",
                gemini_response.status_code
            )


            if gemini_response.status_code == 200:

                gemini_result = gemini_response.json()
                model_success = True

                break


            # Errores temporales.
            if gemini_response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                wait_time = 5 * (attempt + 1)

                print(
                    f"Reintentando en {wait_time} segundos..."
                )

                time.sleep(
                    wait_time
                )

                continue


            # Si el modelo devuelve 404,
            # pasamos directamente al modelo de respaldo.
            if gemini_response.status_code == 404:

                print(
                    "Modelo no disponible. "
                    "Pasando al siguiente modelo."
                )

                break


            print(
                gemini_response.text[:2000]
            )

            break


        except Exception as error:

            print(
                "Error de conexión con Gemini:",
                error
            )

            if attempt < 2:

                wait_time = 5 * (attempt + 1)

                time.sleep(
                    wait_time
                )


    if model_success:

        break


# ============================================================
# GEMINI NO RESPONDIÓ
# ============================================================

if gemini_result is None:

    print("=" * 60)
    print("ERROR: NINGÚN MODELO GEMINI RESPONDIÓ.")
    print("La noticia NO será publicada.")
    print("La noticia NO será guardada como publicada.")
    print("=" * 60)

    raise SystemExit(1)


# ============================================================
# EXTRAER RESPUESTA
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
    print("ERROR INTERPRETANDO RESPUESTA DE GEMINI")
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


# ============================================================
# DESCARTADA
# ============================================================

if decision != "PUBLICAR":

    print("=" * 60)
    print("DESCARTADA POR CAL BOT")
    print("=" * 60)
    print(reason)

    raise SystemExit(0)


# ============================================================
# DATOS GENERADOS
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


# ============================================================
# VALIDACIÓN
# ============================================================

if not ai_title or not ai_content:

    print(
        "DESCARTADA: Gemini no generó contenido válido."
    )

    raise SystemExit(0)


# ============================================================
# SEGURIDAD CONTRA TÍTULOS REPETIDOS
# ============================================================

for old_title in history["titles"]:

    if similarity(
        ai_title,
        old_title
    ) >= 0.88:

        print("=" * 60)
        print(
            "DESCARTADA: título demasiado parecido "
            "a una publicación anterior."
        )
        print("=" * 60)

        raise SystemExit(0)


# ============================================================
# LIMPIAR CONTENIDO
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
    f"-# Cal Bot V12 · {date_text}"
)


# ============================================================
# DISCORD 2000 CARACTERES
# ============================================================

if len(final_message) > 1950:

    print(
        "Mensaje demasiado largo. "
        "Recortando contenido..."
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
            "DESCARTADA: mensaje demasiado largo."
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
        f"Revisa la información antes de "
        f"publicarlo en #noticias.\n\n"
        f"-# Cal Bot V12 · {date_text}"
    )


# ============================================================
# ENVIAR A DISCORD
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
    print("La noticia NO se guardará.")
    print("=" * 60)

    raise SystemExit(1)


print(
    "Discord HTTP:",
    discord_response.status_code
)


# Discord webhook normalmente devuelve 204.
if not discord_response.ok:

    print("=" * 60)
    print("ERROR DE DISCORD")
    print(discord_response.status_code)
    print(discord_response.text)
    print("La noticia NO se guardará.")
    print("=" * 60)

    raise SystemExit(1)


# ============================================================
# DISCORD CONFIRMÓ
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
print("NOTICIA PUBLICADA Y GUARDADA EN HISTORIAL.")
print("=" * 60)
