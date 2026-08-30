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
# CAL BOT V17
# EDITOR DE NOTICIAS DE CAL FAMILY
#
# OBJETIVO V17
# ------------------------------------------------------------
# Mantener un filtro fuerte contra:
# - rumores
# - leaks sin respaldo
# - especulación
# - opiniones
# - reacciones
# - clickbait
# - recopilaciones
# - repeticiones
#
# PERO permitir:
# - noticias secundarias con fuente primaria identificable
# - entrevistas
# - declaraciones de desarrolladores
# - documentos públicos
# - anuncios de empresas
# - información empresarial verificable
# - información gubernamental
# - datos concretos procedentes de medios fiables
# ============================================================

print("=" * 64)
print("CAL BOT V17")
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
MAX_CANDIDATES = 18

MIN_ARTICLE_CHARS = 450
MAX_ARTICLE_CHARS = 24000

RSS_URL = (
    "https://news.google.com/rss/"
    "?q=GTA+VI"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)

# Orden de prioridad.
# Si un modelo falla por 429/503, pasa al siguiente.
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

    text = str(text).lower()

    replacements = {
        "grand theft auto vi": "gta vi",
        "grand theft auto 6": "gta vi",
        "grand theft auto": "gta",
        "gta 6": "gta vi"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"https?://\S+", " ", text)

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
# CONTENT HASH
# ============================================================

def content_hash(text):

    normalized = normalize_text(text)

    return hashlib.sha256(
        normalized.encode("utf-8")
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
        "topics": []
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
    "topics",
    []
)


# ============================================================
# FILTRO LOCAL
#
# IMPORTANTE:
# NO bloquear palabras como:
# "developer"
# "reveals"
# "says"
# "report"
# "according to"
#
# porque pueden indicar una noticia válida.
# ============================================================

def obvious_local_reject(title):

    normalized = normalize_text(title)

    blocked_patterns = [

        # ----------------------------------------------------
        # RUMORES / LEAKS
        # ----------------------------------------------------

        "rumor",
        "rumoured",
        "rumored",
        "reportedly",
        "leaked",
        "leak",
        "leaker",
        "insider",
        "anonymous source",
        "unconfirmed",

        # ----------------------------------------------------
        # OPINIÓN / REACCIÓN
        # ----------------------------------------------------

        "my thoughts",
        "what i think",
        "i think",
        "my reaction",
        "reaction",
        "reacts",
        "wowed",
        "holy shit",
        "driving me crazy",
        "impressions",
        "first impressions",

        # ----------------------------------------------------
        # GUÍAS / COMPRAS
        # ----------------------------------------------------

        "best console",
        "best pc",
        "best gaming",
        "buy for gta",
        "should you buy",
        "guide",
        "tips and tricks",
        "how to",

        # ----------------------------------------------------
        # RECOPILACIONES
        # ----------------------------------------------------

        "everything leaked so far",
        "everything revealed",
        "everything we know",
        "all the actors",
        "all the details",
        "all you need to know",
        "complete guide",

        # ----------------------------------------------------
        # ANÁLISIS PURAMENTE EDITORIAL
        # ----------------------------------------------------

        "why rockstar",
        "why gta",
        "analysis",
        "opinion piece",

    ]

    for pattern in blocked_patterns:

        if pattern in normalized:

            return True

    return False


# ============================================================
# DETECTAR FECHA
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


    if news_id in history[
        "published"
    ]:

        continue


    # --------------------------------------------------------
    # DUPLICADO DE TÍTULO
    # --------------------------------------------------------

    duplicate = False

    for old_title in history[
        "titles"
    ]:

        score = similarity(
            title,
            old_title
        )

        if score >= 0.86:

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
# SIN CANDIDATAS
# ============================================================

if not candidates:

    print("=" * 64)
    print("NO HAY NOTICIAS NUEVAS.")
    print("=" * 64)

    raise SystemExit(0)


print("=" * 64)
print(
    f"CANDIDATAS ENCONTRADAS: "
    f"{len(candidates)}"
)
print("=" * 64)


# ============================================================
# HEADERS
# ============================================================

headers = {

    "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36",

    "Accept-Language":
        "en-US,en;q=0.9"

}


# ============================================================
# EXTRACCIÓN AVANZADA DE ARTÍCULO
# ============================================================

def extract_article(response):

    if not response:
        return ""

    try:

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

    except Exception:

        return ""


    collected = []


    # --------------------------------------------------------
    # META DESCRIPTION
    # --------------------------------------------------------

    meta_selectors = [

        ("meta", {
            "name": "description"
        }),

        ("meta", {
            "property": "og:description"
        }),

        ("meta", {
            "name": "twitter:description"
        })

    ]


    for tag_name, attrs in meta_selectors:

        tag = soup.find(
            tag_name,
            attrs
        )

        if tag:

            value = tag.get(
                "content",
                ""
            ).strip()

            if len(value) >= 80:

                collected.append(
                    value
                )


    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        try:

            raw = script.string

            if not raw:
                continue

            data = json.loads(
                raw
            )

            if isinstance(
                data,
                dict
            ):

                article_body = data.get(
                    "articleBody",
                    ""
                )

                description = data.get(
                    "description",
                    ""
                )

                if article_body:

                    collected.append(
                        str(article_body)
                    )

                if description:

                    collected.append(
                        str(description)
                    )

            elif isinstance(
                data,
                list
            ):

                for item in data:

                    if not isinstance(
                        item,
                        dict
                    ):

                        continue

                    article_body = item.get(
                        "articleBody",
                        ""
                    )

                    description = item.get(
                        "description",
                        ""
                    )

                    if article_body:

                        collected.append(
                            str(article_body)
                        )

                    if description:

                        collected.append(
                            str(description)
                        )

        except Exception:

            continue


    # --------------------------------------------------------
    # LIMPIAR ELEMENTOS
    # --------------------------------------------------------

    for element in soup([

        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "header",
        "form",
        "aside",
        "iframe"

    ]):

        element.decompose()


    # --------------------------------------------------------
    # PÁRRAFOS
    # --------------------------------------------------------

    for paragraph in soup.find_all(
        "p"
    ):

        text = paragraph.get_text(
            " ",
            strip=True
        )

        if len(text) >= 45:

            collected.append(
                text
            )


    # --------------------------------------------------------
    # DEDUPLICAR
    # --------------------------------------------------------

    final_parts = []

    seen = set()

    for item in collected:

        item = re.sub(
            r"\s+",
            " ",
            item
        ).strip()

        if not item:
            continue

        key = normalize_text(
            item
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        final_parts.append(
            item
        )


    text = "\n".join(
        final_parts
    )


    return text[
        :MAX_ARTICLE_CHARS
    ]


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

                "maxOutputTokens": 2400,

                "temperature": 0.15,

                "responseMimeType":
                    "application/json"

            }

        }


        for attempt in range(2):

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

                    print(
                        "Modelo temporalmente "
                        "no disponible."
                    )

                    if attempt == 0:

                        time.sleep(5)

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

                if attempt == 0:

                    time.sleep(5)


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

    final_source_url = (
        candidate["google_url"]
    )


    try:

        response = requests.get(

            candidate["google_url"],

            headers=headers,

            timeout=30,

            allow_redirects=True

        )


        print(
            "Estado de página:",
            response.status_code
        )


        final_source_url = (
            response.url
            or candidate["google_url"]
        )


        if response.ok:

            article_text = extract_article(
                response
            )


    except Exception as error:

        print(
            "Error obteniendo artículo:",
            error
        )


    # --------------------------------------------------------
    # FALLBACK RSS
    # --------------------------------------------------------

    if len(article_text) >= MIN_ARTICLE_CHARS:

        source_content = article_text

    else:

        source_content = (
            candidate["rss_content"]
        )


    if len(source_content) < 120:

        print(
            "Descartada: contenido insuficiente."
        )

        continue


    # --------------------------------------------------------
    # HISTORIAL
    # --------------------------------------------------------

    previous_titles = history[
        "titles"
    ][-100:]


    previous_titles_text = "\n".join(

        f"- {title}"

        for title in previous_titles

    )


    # --------------------------------------------------------
    # PROMPT V17
    # --------------------------------------------------------

    prompt = f"""
Eres el EDITOR PRINCIPAL de noticias de CAL FAMILY.

CAL FAMILY cubre exclusivamente información relevante
sobre Grand Theft Auto VI.

Tu objetivo NO es publicar todo.

Tu objetivo es encontrar noticias que tengan una
NOVEDAD FACTUAL REAL y evitar:

- rumores
- especulación
- opiniones
- reacciones
- clickbait
- teorías
- leaks sin respaldo
- contenido reciclado
- artículos que solamente resumen algo ya conocido
- artículos que solamente reaccionan al Extended Look

============================================================
REGLA PRINCIPAL V17
============================================================

Una noticia puede PUBLICARSE si cumple:

NOVEDAD FACTUAL
+
DATO CONCRETO
+
FUENTE IDENTIFICABLE
+
CONTEXTO SUFICIENTE
+
RELEVANCIA PARA GTA VI

NO es obligatorio que la fuente sea Rockstar Games.

============================================================
FUENTES VÁLIDAS
============================================================

Acepta información procedente de:

1. Rockstar Games.
2. Take-Two Interactive.
3. Rockstar North.
4. Un desarrollador identificado.
5. Un director identificado.
6. Un productor identificado.
7. Un actor identificado.
8. Un representante oficial.
9. Una empresa involucrada directamente.
10. Una autoridad gubernamental.
11. Un documento público.
12. Una entrevista.
13. Una presentación empresarial.
14. Un informe financiero.
15. Un medio periodístico fiable que cite claramente
    cualquiera de las fuentes anteriores.
16. Una fuente secundaria fiable que presente
    evidencia concreta y atribuible.

============================================================
MUY IMPORTANTE
============================================================

NO descartes una noticia solamente porque:

- no proviene directamente de Rockstar;
- fue publicada por un medio secundario;
- contiene análisis además de información factual;
- no tiene una cita textual;
- el título parece llamativo.

Primero lee el contenido.

Si el artículo dice, por ejemplo:

"Rob Nelson, co-head de Rockstar North, explicó que..."

y proporciona contexto suficiente:

PUBLICAR.

Si dice:

"Según Rockstar Games..."

y desarrolla el dato:

PUBLICAR.

Si dice:

"Un documento presentado por una autoridad..."

y el documento está explicado:

PUBLICAR.

Si dice:

"Los analistas creen que..."

sin evidencia adicional:

DESCARTAR.

============================================================
REGLA DE ATRIBUCIÓN
============================================================

La pregunta NO es:

"¿Rockstar lo confirmó?"

La pregunta correcta es:

"¿Existe una fuente identificable que permita verificar
de dónde sale esta información?"

Si la respuesta es SÍ:

puede publicarse.

Si la respuesta es NO:

descartar.

============================================================
EJEMPLOS
============================================================

CASO A:

"Rob Nelson explica por qué GTA VI solo tiene dos protagonistas."

→ PUBLICAR.

CASO B:

"Un desarrollador de Rockstar explica una decisión
de diseño durante una entrevista."

→ PUBLICAR.

CASO C:

"Un medio afirma que GTA VI probablemente tendrá
60 FPS."

→ DESCARTAR.

CASO D:

"Un insider asegura que GTA VI tendrá 100 misiones."

→ DESCARTAR.

CASO E:

"Take-Two informa una cifra concreta de preventas
en una presentación financiera."

→ PUBLICAR.

CASO F:

"Un analista estima que GTA VI venderá 100 millones."

→ DESCARTAR.

CASO G:

"Un gobierno local publica una propuesta relacionada
con el lanzamiento de GTA VI."

→ PUBLICAR si la propuesta está documentada.

CASO H:

"IGN dice que el Extended Look es increíble."

→ DESCARTAR.

CASO I:

"Un medio analiza el Extended Look pero además cita
una nueva declaración de un desarrollador."

→ PUBLICAR si esa declaración contiene información nueva.

CASO J:

"Artículo recopilando todo lo visto en el Extended Look."

→ DESCARTAR.

CASO K:

"Un medio revela que Rockstar cambió una característica
después de una entrevista con sus desarrolladores."

→ PUBLICAR si la fuente está identificada.

============================================================
OPINIÓN VS INFORMACIÓN
============================================================

Un artículo NO debe descartarse automáticamente porque
contenga opinión.

Muchos artículos periodísticos contienen:

- análisis
- contexto
- opinión editorial

junto con información factual.

Debes separar ambas cosas.

Si existe un dato nuevo verificable:

PUBLICAR.

Si todo el artículo es opinión:

DESCARTAR.

============================================================
EXTENDED LOOK
============================================================

NO publiques artículos que simplemente:

- reaccionen al Extended Look;
- digan que se ve increíble;
- recopilen detalles ya mostrados;
- identifiquen personajes sin nueva información;
- hagan teorías basadas en imágenes;
- expliquen escenas ya conocidas.

PERO:

Si después del Extended Look aparece una entrevista,
declaración o información empresarial nueva:

puede PUBLICARSE.

============================================================
REPETICIONES
============================================================

Compara la noticia con el historial.

No te limites a comparar títulos.

Compara también:

- dato principal;
- afirmación;
- sujeto;
- acontecimiento;
- fuente;
- información central.

Si dos artículos hablan esencialmente de la misma noticia:

DESCARTAR el segundo.

Una redacción diferente NO convierte una noticia repetida
en una noticia nueva.

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
PROCESO OBLIGATORIO
============================================================

Antes de decidir, identifica internamente:

1. ¿Cuál es el dato principal?
2. ¿Qué información es realmente nueva?
3. ¿Quién proporciona esa información?
4. ¿La fuente está identificada?
5. ¿Existe contexto suficiente?
6. ¿Es una noticia o solamente una opinión?
7. ¿Es rumor?
8. ¿Es especulación?
9. ¿Es una repetición?
10. ¿Ya apareció este mismo dato en el historial?

Si no puedes identificar un dato factual nuevo:

DESCARTAR.

============================================================
DECISIÓN
============================================================

Devuelve ÚNICAMENTE JSON válido.

PUBLICAR:

{{
  "decision": "PUBLICAR",
  "reason": "Motivo breve explicando el dato nuevo y su fuente",
  "category": "Noticias",
  "title": "Título en español",
  "content": "Contenido final"
}}

DESCARTAR:

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

El contenido debe:

- ser informativo;
- ser natural;
- ser profesional;
- explicar el dato;
- indicar quién proporcionó la información;
- no inventar citas;
- no inventar datos;
- no convertir rumores en hechos.

NO incluyas URL dentro del contenido.

Cuando sea apropiado usa:

[Introducción]

**El dato clave**

[Dato concreto]

**Por qué importa**

[Contexto]

⚠️ **Contexto**

[Fuente o atribución]

============================================================
REGLA FINAL V17
============================================================

FUENTE IDENTIFICABLE
+
DATO NUEVO
+
EVIDENCIA/CONTEXTO
+
RELEVANCIA
=
PUBLICAR

RUMOR
+
ESPECULACIÓN
+
OPINIÓN SIN DATO
+
REPETICIÓN
=
DESCARTAR

NO confundas:

"no confirmado por Rockstar"

con

"no verificable".

Una noticia secundaria puede ser válida si identifica
correctamente la fuente primaria.

Tu prioridad es CALIDAD, no cantidad.
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
            "No se pudo interpretar "
            "la respuesta."
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


    # --------------------------------------------------------
    # DESCARTAR
    # --------------------------------------------------------

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


    for old_title in history[
        "titles"
    ]:

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
    # HASH DEL CONTENIDO FUENTE
    # --------------------------------------------------------

    source_hash = content_hash(
        source_content
    )


    if source_hash in history[
        "content_hashes"
    ]:

        print(
            "Descartada: contenido idéntico."
        )

        continue


    # --------------------------------------------------------
    # HASH DEL DATO GENERADO
    # --------------------------------------------------------

    generated_hash = content_hash(
        ai_content
    )


    # --------------------------------------------------------
    # SELECCIONADA
    # --------------------------------------------------------

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
            source_hash,

        "generated_hash":
            generated_hash,

        "reason":
            reason

    }


    break


# ============================================================
# NINGUNA NOTICIA
# ============================================================

if selected is None:

    print("=" * 64)
    print(
        "NINGUNA NOTICIA CUMPLIÓ "
        "LOS CRITERIOS."
    )
    print("=" * 64)

    raise SystemExit(0)


# ============================================================
# LIMPIAR CONTENIDO
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


for unwanted in [

    "🔗 **Fuente:**",
    "🔗 **Fuente original:**",
    "Fuente:",
    "Fuente original:"

]:

    ai_content = ai_content.replace(
        unwanted,
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

    f"Este contenido fue preparado por "
    f"Cal Bot como borrador editorial. "
    f"Revisa la información antes de "
    f"publicarlo en #noticias.\n\n"

    f"-# Cal Bot V17 · {date_text}"

)


# ============================================================
# LÍMITE DISCORD
# ============================================================

if len(final_message) > 1950:

    print(
        "Mensaje demasiado largo. "
        "Recortando..."
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
            "Descartada: mensaje "
            "demasiado largo."
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

        f"Este contenido fue preparado por "
        f"Cal Bot como borrador editorial. "
        f"Revisa la información antes de "
        f"publicarlo en #noticias.\n\n"

        f"-# Cal Bot V17 · {date_text}"

    )


# ============================================================
# DISCORD
# ============================================================

print("=" * 64)
print("NOTICIA APROBADA.")
print("ENVIANDO A DISCORD...")
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


except Exception as error:

    print("=" * 64)

    print(
        "ERROR DE CONEXIÓN "
        "CON DISCORD"
    )

    print(error)

    print(
        "La noticia NO se guardará."
    )

    print("=" * 64)

    raise SystemExit(1)


print(
    "Discord HTTP:",
    discord_response.status_code
)


# ============================================================
# ERROR DISCORD
# ============================================================

if not discord_response.ok:

    print("=" * 64)

    print(
        "ERROR DE DISCORD"
    )

    print(
        discord_response.text
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
    selected[
        "news_id"
    ]
)


history[
    "titles"
].append(
    ai_title
)


history[
    "content_hashes"
].append(
    selected[
        "content_hash"
    ]
)


history[
    "topics"
].append({

    "title":
        ai_title,

    "source":
        final_source_url,

    "date":
        date_text,

    "reason":
        selected[
            "reason"
        ]

})


# ============================================================
# LIMITAR HISTORIAL
# ============================================================

history[
    "published"
] = (

    history[
        "published"
    ][-MAX_HISTORY:]

)


history[
    "titles"
] = (

    history[
        "titles"
    ][-MAX_HISTORY:]

)


history[
    "content_hashes"
] = (

    history[
        "content_hashes"
    ][-MAX_HISTORY:]

)


history[
    "topics"
] = (

    history[
        "topics"
    ][-MAX_HISTORY:]

)


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

    print(
        "ERROR guardando historial:"
    )

    print(error)

    print(
        "La noticia sí fue enviada "
        "a Discord."
    )


# ============================================================
# FINAL
# ============================================================

print("=" * 64)
print(
    "NOTICIA PUBLICADA "
    "Y GUARDADA EN HISTORIAL."
)
print(
    "CAL BOT V17 FINALIZADO."
)
print("=" * 64)
