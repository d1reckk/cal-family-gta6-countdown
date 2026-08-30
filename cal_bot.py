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


# ================================================================
# CAL BOT V19 DEFINITIVO
# Editor de noticias de CAL FAMILY
#
# Objetivos:
# - JSON robusto: ningún KeyError debe tumbar el bot.
# - 429/503 de Gemini no detienen la evaluación.
# - No exige una fuente oficial de Rockstar.
# - Separa Noticias / Análisis / Opinión.
# - Evita leaks y cyberleaks.
# - Deduplica feed + historial.
# - Evalúa varias candidatas y publica la mejor.
# - Conserva el historial.
# - Un Extended Look puede ser válido si un medio fiable aporta
#   información nueva y sustancial.
# ================================================================

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HISTORY_FILE = "seen_news.json"
ROLE_ID = "1504921814759903343"

RSS_URL = (
    "https://news.google.com/rss/"
    "?q=GTA+VI"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)

GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]

MAX_HISTORY = 1000
MAX_NEWS_AGE_HOURS = 72
MAX_CANDIDATES_TO_EVALUATE = 12
REQUEST_TIMEOUT = 30
GEMINI_TIMEOUT = 60

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124 Safari/537.36"
    )
}


# ================================================================
# NORMALIZACIÓN / DUPLICADOS
# ================================================================

def normalize_text(text):
    if not text:
        return ""

    text = str(text).lower()

    replacements = {
        "grand theft auto vi": "gta vi",
        "grand theft auto 6": "gta vi",
        "grand theft auto": "gta",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9áéíóúüñ ]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def similarity(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0.0

    return SequenceMatcher(None, a, b).ratio()


def canonical_url(url):
    if not url:
        return ""

    try:
        p = urlparse(str(url))
        q = parse_qs(p.query, keep_blank_values=True)

        ignored = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "oc",
        }

        clean = {
            key: value
            for key, value in q.items()
            if key not in ignored
        }

        return urlunparse((
            p.scheme,
            p.netloc.lower(),
            p.path.rstrip("/"),
            "",
            urlencode(clean, doseq=True),
            "",
        ))

    except Exception:
        return str(url)


def stable_id(title, url):
    value = normalize_text(title) + "|" + canonical_url(url)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_hash(text):
    return hashlib.sha256(
        normalize_text(text).encode("utf-8")
    ).hexdigest()


def clean_html(text):
    if not text:
        return ""

    return BeautifulSoup(
        str(text),
        "html.parser"
    ).get_text(" ", strip=True)


# ================================================================
# HISTORIAL
# ================================================================

def load_history():
    default = {
        "published": [],
        "titles": [],
        "content_hashes": [],
        "source_urls": [],
    }

    if not os.path.exists(HISTORY_FILE):
        return default

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exc:
        print("No se pudo leer el historial:", exc)
        return default

    if isinstance(data, list):
        data = {"published": data}

    if not isinstance(data, dict):
        data = {}

    clean = {}

    for key in default:
        value = data.get(key, [])
        clean[key] = value if isinstance(value, list) else []

    return clean


def save_history(history):
    if not isinstance(history, dict):
        history = {}

    for key in (
        "published",
        "titles",
        "content_hashes",
        "source_urls",
    ):
        value = history.get(key, [])

        if not isinstance(value, list):
            value = []

        history[key] = value[-MAX_HISTORY:]

    temp_file = HISTORY_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            history,
            file,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(temp_file, HISTORY_FILE)


def published_title_duplicate(title, history, threshold=0.88):
    titles = history.get("titles", [])

    if not isinstance(titles, list):
        return False

    for old_title in titles:
        if similarity(title, old_title) >= threshold:
            return True

    return False


# ================================================================
# FILTROS LOCALES
#
# Estos filtros NO intentan decidir si una noticia es buena.
# Solo eliminan casos inequívocos que no merece la pena mandar
# a Gemini.
# ================================================================

TITLE_BLOCKLIST = [
    "cyberleek",
    "cyber leak",
    "data breach",
    "stolen files",
    "hacked files",
    "credential leak",
    "private files",
    "archivos robados",
    "archivos privados",
    "filtración de datos",
    "datos robados",
    "brecha de seguridad",
]

HARD_TITLE_PATTERNS = [
    r"\bhow to\b",
    r"\bbest .* console to buy\b",
]


def title_should_skip(title):
    normalized = normalize_text(title)

    for word in TITLE_BLOCKLIST:
        if normalize_text(word) in normalized:
            return True

    for pattern in HARD_TITLE_PATTERNS:
        if re.search(pattern, normalized, flags=re.I):
            return True

    return False


def looks_like_leak_or_cyberleak(title, content):
    text = normalize_text(
        f"{title} {content}"
    )

    strong_patterns = [
        r"\bcyber ?leak\b",
        r"\bdata breach\b",
        r"\bstolen files?\b",
        r"\bhacked files?\b",
        r"\bprivate files?\b",
        r"\bstolen source code\b",
        r"\bstolen database\b",
        r"\bcredential(s)? leak\b",
        r"\barchivos robados\b",
        r"\barchivos privados\b",
        r"\bfiltracion de datos\b",
        r"\bdatos robados\b",
        r"\bintrusion\b",
        r"\bhackeo\b",
        r"\bbrecha de seguridad\b",
    ]

    return any(
        re.search(pattern, text, flags=re.I)
        for pattern in strong_patterns
    )


# ================================================================
# EXTRACCIÓN DE ARTÍCULOS
# ================================================================

def fetch_article(google_url, rss_content):
    article_text = ""
    final_url = google_url

    try:
        response = requests.get(
            google_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        print("Estado de página:", response.status_code)

        final_url = response.url or google_url

        if response.ok:
            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            for tag in soup.find_all([
                "script",
                "style",
                "noscript",
                "svg",
                "nav",
                "footer",
                "header",
                "form",
            ]):
                tag.decompose()

            paragraphs = []

            for paragraph in soup.find_all("p"):
                text = paragraph.get_text(
                    " ",
                    strip=True,
                )

                if len(text) >= 45:
                    paragraphs.append(text)

            article_text = "\n".join(
                paragraphs
            )[:18000]

    except requests.RequestException as exc:
        print("No se pudo extraer el artículo:", exc)

    except Exception as exc:
        print("Error procesando el artículo:", exc)

    if len(article_text) >= 300:
        return article_text, final_url

    if len(rss_content) >= 100:
        return rss_content[:12000], final_url

    return "", final_url


# ================================================================
# JSON DE GEMINI
# ================================================================

def extract_json(text):
    if not isinstance(text, str):
        raise ValueError("Gemini no devolvió texto.")

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    start = text.find("{")
    end = text.rfind("}")

    if start < 0 or end <= start:
        raise ValueError(
            "No se encontró un objeto JSON en la respuesta."
        )

    json_text = text[start:end + 1]

    return json.loads(json_text)


# ================================================================
# PROMPT EDITORIAL
# ================================================================

def build_editorial_prompt(
    title,
    source_url,
    source_content,
    history,
):
    previous = history.get("titles", [])

    if not isinstance(previous, list):
        previous = []

    previous = previous[-80:]

    previous_text = (
        "\n".join(
            f"- {item}"
            for item in previous
        )
        or "- Ninguna"
    )

    return f"""
Eres CAL BOT, editor de noticias de CAL FAMILY,
una comunidad dedicada a GTA VI.

OBJETIVO
Selecciona información realmente útil para el canal.
No publiques por llenar espacio, pero tampoco descartes
una noticia legítima solo porque la fuente no sea Rockstar Games.

CLASIFICACIÓN OBLIGATORIA
Usa EXACTAMENTE una:

- "Noticias": información factual o hechos verificables.
- "Análisis": análisis periodístico que aporta información,
  contexto o conclusiones nuevas basadas en material real.
- "Opinión": valoración personal, reacción, predicción o
  comentario editorial.

Una opinión NO debe convertirse en una noticia factual.

Un análisis puede publicarse si aporta información sustancial
nueva y está claramente presentado como análisis.

ESTÁNDAR DE PUBLICACIÓN
PUBLICA solo si hay información concreta y relevante.

Ejemplos:
- declaración atribuida;
- cambio confirmado;
- fecha;
- cifra;
- decisión empresarial;
- información de desarrollo;
- casting;
- tecnología;
- lanzamiento;
- plataformas;
- características;
- producción;
- marketing;
- clasificación;
- distribución;
- otro dato verificable y relevante.

FUENTES
- Una fuente secundaria fiable puede ser suficiente.
- Una declaración de un desarrollador, actor, ejecutivo u otra
  persona identificable puede ser noticia aunque no sea un
  comunicado de Rockstar.
- Un informe periodístico puede publicarse si explica claramente
  de dónde sale el dato.
- NO exijas una fuente oficial cuando existe evidencia
  periodística sólida.

LEAKS / CYBERLEAKS
DESCARTA si la información depende de material obtenido mediante:
- hackeo;
- intrusión;
- acceso no autorizado;
- robo de archivos;
- credenciales;
- bases de datos robadas;
- código fuente robado;
- archivos privados;
- cyberleaks.

No conviertas un leak en noticia solo porque varios medios
lo reproduzcan.

La mera palabra "leak" en un contexto histórico no basta para
descartar. Evalúa de qué depende realmente la afirmación principal.

DUPLICADOS
Compara el HECHO CENTRAL con el historial, no solo los títulos.

Dos artículos distintos que cubren el mismo anuncio o hecho son
la misma noticia salvo que el segundo aporte un dato
sustancialmente nuevo.

EXTENDED LOOK / TRÁILERS
NO descartes automáticamente un artículo por tratar sobre un
Extended Look, tráiler o material promocional.

- Si solo enumera escenas o detalles ya visibles, DESCARTA.
- Si un medio fiable analiza ese material y aporta información
  nueva, contexto verificable o una conclusión periodística
  sustancial, puede publicarse como "Análisis".
- No inventes novedades a partir de imágenes o escenas.

DESCARTA cuando:
- el contenido principal sea rumor, especulación o predicción
  sin respaldo suficiente;
- sea una opinión/reacción/review sin información nueva;
- sea SEO/FAQ/recopilación que solo repite lo conocido;
- el titular prometa algo que el artículo no demuestra;
- la información sea demasiado ambigua para comprobarla;
- el hecho central ya esté cubierto por el historial y no exista
  novedad sustancial;
- sea un leak/cyberleak según las reglas anteriores.

REDACCIÓN SI PUBLICAR
- Español natural.
- Título claro y preciso.
- No uses clickbait engañoso.
- 550-950 caracteres aproximadamente.
- Explica primero qué ocurrió y después por qué importa.
- Atribuye declaraciones y reportes:
  "X dijo...", "según X...", "el medio informa...".
- No inventes nombres, cifras, fechas, citas ni contexto.
- No pongas la URL dentro de "content".
- Si category es "Análisis", deja claro que es análisis y no
  un anuncio oficial.
- No afirmes como hecho lo que el artículo presenta como posibilidad.

PUNTUACIÓN
Asigna "score" de 0 a 100.

90-100:
Muy sólida, nueva y relevante.

75-89:
Buena candidata, con evidencia y novedad suficientes.

60-74:
Interesante pero con limitaciones.

0-59:
No alcanza el estándar.

Una opinión no debe recibir una puntuación alta solo por ser
interesante.

RESPUESTA
Devuelve ÚNICAMENTE JSON válido.
NO uses Markdown.
NO escribas texto fuera del JSON.

Formato exacto:

{{
  "decision": "PUBLICAR" o "DESCARTAR",
  "reason": "motivo breve",
  "category": "Noticias" o "Análisis" o "Opinión",
  "score": 0,
  "title": "título en español",
  "content": "texto final"
}}

Si DESCARTAR:
- "content" debe ser "";
- "score" debe reflejar por qué no alcanza el estándar.

HISTORIAL DE PUBLICACIONES:
{previous_text}

ARTÍCULO CANDIDATO:
TÍTULO: {title}
FUENTE: {source_url}

CONTENIDO:
{source_content}
"""


# ================================================================
# GEMINI
# ================================================================

def ask_gemini(
    title,
    source_url,
    source_content,
    history,
):
    prompt = build_editorial_prompt(
        title,
        source_url,
        source_content,
        history,
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
            "maxOutputTokens": 1600,
            "responseMimeType": "application/json",
        },
    }

    if not GEMINI_KEY:
        print("Gemini no configurado.")
        return None

    last_error = None

    for model in GEMINI_MODELS:
        endpoint = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{model}:generateContent"
            f"?key={GEMINI_KEY}"
        )

        print("Intentando modelo:", model)

        for attempt in range(3):
            try:
                response = requests.post(
                    endpoint,
                    headers={
                        "Content-Type": "application/json"
                    },
                    json=payload,
                    timeout=GEMINI_TIMEOUT,
                )

                print(
                    "Gemini HTTP:",
                    response.status_code,
                )

                if response.status_code == 200:
                    data = response.json()

                    candidates = data.get(
                        "candidates"
                    ) or []

                    if (
                        not isinstance(candidates, list)
                        or not candidates
                    ):
                        raise RuntimeError(
                            "Gemini no devolvió candidates."
                        )

                    first_candidate = (
                        candidates[0]
                        if isinstance(
                            candidates[0],
                            dict,
                        )
                        else {}
                    )

                    content = (
                        first_candidate.get("content")
                        or {}
                    )

                    parts = (
                        content.get("parts")
                        or []
                    )

                    if (
                        not isinstance(parts, list)
                        or not parts
                    ):
                        raise RuntimeError(
                            "Gemini no devolvió parts."
                        )

                    text = ""

                    for part in parts:
                        if not isinstance(part, dict):
                            continue

                        value = part.get("text")

                        if (
                            isinstance(value, str)
                            and value.strip()
                        ):
                            text = value.strip()
                            break

                    if not text:
                        raise RuntimeError(
                            "Gemini devolvió texto vacío."
                        )

                    result = extract_json(text)

                    if not isinstance(result, dict):
                        raise RuntimeError(
                            "Gemini no devolvió un objeto JSON."
                        )

                    return result

                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:1000]}"
                )

                # 429 y 5xx son recuperables.
                # Tras los reintentos se pasa al siguiente modelo.
                if response.status_code in (
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    wait = min(
                        12,
                        2 * (attempt + 1),
                    )

                    print(
                        "Gemini temporalmente no disponible. "
                        f"Reintentando en {wait}s..."
                    )

                    time.sleep(wait)
                    continue

                # Otros 4xx no suelen solucionarse repitiendo
                # exactamente la misma petición.
                print(
                    "Error no recuperable en este modelo."
                )
                break

            except (
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                last_error = (
                    f"JSON inválido: {exc}"
                )

                print(
                    "Respuesta JSON inválida:",
                    exc,
                )

                if attempt < 2:
                    time.sleep(2)
                    continue

                break

            except requests.RequestException as exc:
                last_error = str(exc)

                print(
                    "Error de red Gemini:",
                    exc,
                )

                if attempt < 2:
                    time.sleep(
                        3 * (attempt + 1)
                    )
                    continue

                break

            except Exception as exc:
                last_error = str(exc)

                print(
                    "Error Gemini:",
                    exc,
                )

                if attempt < 2:
                    time.sleep(
                        3 * (attempt + 1)
                    )
                    continue

                break

    print(
        "ERROR: ningún modelo de Gemini "
        "respondió correctamente."
    )

    print(last_error or "")

    # IMPORTANTE:
    # None significa "esta candidata no pudo evaluarse".
    # main() continúa con las demás candidatas.
    return None


# ================================================================
# DISCORD
# ================================================================

def send_discord(message):
    payload = {
        "content": message,
        "username": "Cal Bot",
        "allowed_mentions": {
            "roles": [ROLE_ID]
        },
    }

    try:
        response = requests.post(
            WEBHOOK,
            json=payload,
            timeout=30,
        )

    except requests.RequestException as exc:
        print(
            "ERROR DE CONEXIÓN CON DISCORD:",
            exc,
        )
        return False

    print(
        "Discord HTTP:",
        response.status_code,
    )

    if response.status_code not in (
        200,
        204,
    ):
        print(
            "Respuesta Discord:",
            response.text[:3000],
        )
        return False

    return True


# ================================================================
# CANDIDATAS
# ================================================================

def get_candidates(feed, history):
    now = datetime.now(timezone.utc)
    candidates = []

    published_ids = set(
        history.get("published", [])
    )

    history_urls = set(
        history.get("source_urls", [])
    )

    history_titles = history.get(
        "titles",
        [],
    )

    if not isinstance(
        history_titles,
        list,
    ):
        history_titles = []

    seen_feed_ids = set()
    seen_feed_urls = set()
    seen_feed_titles = []

    for entry in feed.entries[:40]:
        if not hasattr(entry, "get"):
            continue

        title = str(
            entry.get("title") or ""
        ).strip()

        google_url = str(
            entry.get("link") or ""
        ).strip()

        if not title or not google_url:
            continue

        if title_should_skip(title):
            print(
                "Ignorada por filtro local:",
                title,
            )
            continue

        published_time = None

        parsed_time = getattr(
            entry,
            "published_parsed",
            None,
        )

        if parsed_time:
            try:
                published_time = (
                    datetime.fromtimestamp(
                        calendar.timegm(
                            parsed_time
                        ),
                        timezone.utc,
                    )
                )
            except Exception:
                published_time = None

        if (
            published_time
            and now - published_time
            > timedelta(
                hours=MAX_NEWS_AGE_HOURS
            )
        ):
            print(
                "Ignorada: demasiado antigua."
            )
            continue

        normalized_url = canonical_url(
            google_url
        )

        item_id = stable_id(
            title,
            normalized_url,
        )

        if item_id in published_ids:
            print(
                "Ignorada: artículo ya procesado."
            )
            continue

        if (
            normalized_url
            and normalized_url in history_urls
        ):
            print(
                "Ignorada: URL ya presente "
                "en historial."
            )
            continue

        if any(
            similarity(title, old_title) >= 0.88
            for old_title in history_titles
        ):
            print(
                "Ignorada: título demasiado "
                "parecido al historial."
            )
            continue

        # Deduplicación dentro del feed.
        if item_id in seen_feed_ids:
            continue

        if (
            normalized_url
            and normalized_url in seen_feed_urls
        ):
            continue

        if any(
            similarity(title, old_title) >= 0.90
            for old_title in seen_feed_titles
        ):
            print(
                "Ignorada: duplicado dentro del feed."
            )
            continue

        rss_content = clean_html(
            entry.get("summary") or ""
        )

        description = clean_html(
            entry.get("description") or ""
        )

        rss_content = (
            rss_content
            + "\n"
            + description
        ).strip()

        candidates.append({
            "id": item_id,
            "title": title,
            "google_url": google_url,
            "rss_content": rss_content,
        })

        seen_feed_ids.add(item_id)

        if normalized_url:
            seen_feed_urls.add(
                normalized_url
            )

        seen_feed_titles.append(title)

    return candidates


# ================================================================
# NORMALIZACIÓN DE LA RESPUESTA EDITORIAL
# ================================================================

def normalize_editor_result(result):
    """
    Normaliza el JSON de Gemini sin asumir que ninguna clave existe.
    Nunca se usa result["clave"] directamente.
    """

    if not isinstance(result, dict):
        return None

    decision = str(
        result.get("decision")
        or "DESCARTAR"
    ).upper().strip()

    reason = str(
        result.get("reason")
        or ""
    ).strip()

    category = str(
        result.get("category")
        or "Noticias"
    ).strip()

    title = str(
        result.get("title")
        or ""
    ).strip()

    content = str(
        result.get("content")
        or ""
    ).strip()

    raw_score = result.get(
        "score",
        0,
    )

    try:
        score = float(raw_score)
    except (
        TypeError,
        ValueError,
    ):
        score = 0.0

    score = max(
        0.0,
        min(100.0, score),
    )

    if decision not in (
        "PUBLICAR",
        "DESCARTAR",
    ):
        decision = "DESCARTAR"

    if category not in (
        "Noticias",
        "Análisis",
        "Opinión",
    ):
        category = "Noticias"

    # Opinión nunca se publica automáticamente.
    if category == "Opinión":
        decision = "DESCARTAR"

        if not reason:
            reason = (
                "El contenido está clasificado "
                "como opinión."
            )

    return {
        "decision": decision,
        "reason": reason,
        "category": category,
        "score": score,
        "title": title,
        "content": content,
    }


# ================================================================
# MENSAJE DISCORD
# ================================================================

def build_discord_message(
    category,
    title,
    content,
    source_url,
):
    date_text = datetime.now(
        timezone.utc
    ).strftime("%d/%m/%Y")

    return (
        f"<@&{ROLE_ID}>\n\n"
        f"🧭 **{category}**\n\n"
        f"# {title}\n\n"
        f"{content}\n\n"
        f"🔗 **Fuente original:** "
        f"{source_url}\n\n"
        f"⚠️ **REVISIÓN REQUERIDA**\n"
        f"Este contenido fue preparado por Cal Bot "
        f"como borrador editorial. Revisa la "
        f"información antes de publicarlo en "
        f"#noticias.\n\n"
        f"-# Cal Bot · {date_text}"
    )


# ================================================================
# MAIN
# ================================================================

def main():
    print("=" * 64)
    print("CAL BOT V19 DEFINITIVO")
    print("EDITOR DE NOTICIAS DE CAL FAMILY")
    print("=" * 64)

    if not WEBHOOK:
        print(
            "ERROR: falta el secret "
            "NEWS_DRAFT_WEBHOOK."
        )
        raise SystemExit(1)

    if not GEMINI_KEY:
        print(
            "ERROR: falta el secret "
            "GEMINI_API_KEY."
        )
        raise SystemExit(1)

    history = load_history()

    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as exc:
        print(
            "ERROR leyendo RSS:",
            exc,
        )
        return

    if not feed.entries:
        print(
            "No se encontraron noticias."
        )
        return

    print(
        "Buscando noticias nuevas..."
    )

    candidates = get_candidates(
        feed,
        history,
    )

    print("=" * 64)
    print(
        f"CANDIDATAS ENCONTRADAS: "
        f"{len(candidates)}"
    )
    print("=" * 64)

    if not candidates:
        print(
            "Ninguna noticia nueva pasó "
            "los filtros iniciales."
        )
        return

    evaluated_results = []

    limit = min(
        len(candidates),
        MAX_CANDIDATES_TO_EVALUATE,
    )

    # ------------------------------------------------------------
    # Evaluamos TODAS las candidatas permitidas.
    # Nunca publicamos simplemente la primera.
    # ------------------------------------------------------------

    for index, candidate in enumerate(
        candidates[:limit],
        start=1,
    ):
        print("=" * 64)
        print(
            f"EVALUANDO CANDIDATA "
            f"{index}/{limit}"
        )
        print(candidate["title"])
        print(candidate["google_url"])
        print("=" * 64)

        try:
            source_content, final_source_url = (
                fetch_article(
                    candidate["google_url"],
                    candidate["rss_content"],
                )
            )

        except Exception as exc:
            print(
                "Error obteniendo artículo; "
                "continuando:",
                exc,
            )
            continue

        if not source_content:
            print(
                "Descartada: no hay "
                "información suficiente."
            )
            continue

        source_hash = content_hash(
            source_content
        )

        history_hashes = set(
            history.get(
                "content_hashes",
                [],
            )
        )

        if source_hash in history_hashes:
            print(
                "Descartada: contenido "
                "idéntico al historial."
            )
            continue

        # --------------------------------------------------------
        # Filtro fuerte de leaks.
        # Se aplica al texto real, no solo al titular.
        # --------------------------------------------------------

        if looks_like_leak_or_cyberleak(
            candidate["title"],
            source_content,
        ):
            print(
                "Descartada: señales de "
                "leak/cyberleak."
            )
            continue

        print(
            "CAL BOT ESTÁ EVALUANDO..."
        )

        try:
            raw_result = ask_gemini(
                candidate["title"],
                final_source_url,
                source_content,
                history,
            )

        except Exception as exc:
            print(
                "Fallo de Gemini para esta "
                "candidata; continuando:",
                exc,
            )
            continue

        # 429/503 o fallo completo de Gemini:
        # esta candidata falla, pero el bucle sigue.
        if raw_result is None:
            print(
                "No se pudo evaluar esta "
                "candidata. Continuando..."
            )
            continue

        result = normalize_editor_result(
            raw_result
        )

        if not result:
            print(
                "Respuesta editorial inválida. "
                "Continuando..."
            )
            continue

        if result["decision"] != "PUBLICAR":
            print(
                "DESCARTADA POR CAL BOT"
            )

            print(
                result["reason"]
                or "No cumple el estándar editorial."
            )

            continue

        ai_title = result["title"]
        ai_content = result["content"]
        category = result["category"]
        score = result["score"]

        if not ai_title or not ai_content:
            print(
                "Descartada: respuesta "
                "editorial incompleta."
            )
            continue

        if category == "Opinión":
            print(
                "Descartada: categoría Opinión."
            )
            continue

        # Umbral mínimo de publicación.
        if score < 75:
            print(
                "Descartada: puntuación "
                f"insuficiente ({score:.1f}/100)."
            )
            continue

        if published_title_duplicate(
            ai_title,
            history,
            0.86,
        ):
            print(
                "Descartada: el título final "
                "coincide con historial."
            )
            continue

        # Evita que Gemini añada la fuente
        # dentro del cuerpo.
        ai_content = re.sub(
            r"🔗\s*\*\*Fuente original:\*\*"
            r".*?(?=\n|$)",
            "",
            ai_content,
            flags=re.I,
        ).strip()

        final_message = (
            build_discord_message(
                category,
                ai_title,
                ai_content,
                final_source_url,
            )
        )

        if len(final_message) > 1950:
            print(
                "Descartada: mensaje demasiado "
                "largo para Discord."
            )
            continue

        evaluated_results.append({
            "candidate": candidate,
            "source_hash": source_hash,
            "source_url": final_source_url,
            "title": ai_title,
            "content": ai_content,
            "category": category,
            "score": score,
            "reason": result["reason"],
            "message": final_message,
        })

        print(
            f"Candidata aceptable: "
            f"{score:.1f}/100"
        )

    # ------------------------------------------------------------
    # Selección final
    # ------------------------------------------------------------

    if not evaluated_results:
        print("=" * 64)
        print(
            "NINGUNA NOTICIA CUMPLIÓ "
            "LOS CRITERIOS."
        )
        print(
            "El bot revisó las candidatas "
            "disponibles sin publicar basura."
        )
        print("=" * 64)
        return

    category_priority = {
        "Noticias": 2,
        "Análisis": 1,
        "Opinión": 0,
    }

    evaluated_results.sort(
        key=lambda item: (
            item["score"],
            category_priority.get(
                item["category"],
                0,
            ),
        ),
        reverse=True,
    )

    best = evaluated_results[0]

    print("=" * 64)
    print("MEJOR NOTICIA SELECCIONADA")
    print("Título:", best["title"])
    print("Categoría:", best["category"])
    print(
        "Puntuación:",
        f'{best["score"]:.1f}/100',
    )
    print(
        "Candidatas publicables evaluadas:",
        len(evaluated_results),
    )
    print(
        "Enviando a Discord..."
    )
    print("=" * 64)

    if not send_discord(
        best["message"]
    ):
        print(
            "La noticia NO se guardará "
            "como publicada."
        )
        raise SystemExit(1)

    # ------------------------------------------------------------
    # Historial: SOLO después de confirmar Discord.
    # ------------------------------------------------------------

    history.setdefault(
        "published",
        [],
    ).append(
        best["candidate"]["id"]
    )

    history.setdefault(
        "titles",
        [],
    ).append(
        best["title"]
    )

    history.setdefault(
        "content_hashes",
        [],
    ).append(
        best["source_hash"]
    )

    history.setdefault(
        "source_urls",
        [],
    ).append(
        best["source_url"]
    )

    save_history(history)

    print("=" * 64)
    print("DISCORD CONFIRMÓ.")
    print(
        "NOTICIA PUBLICADA Y "
        "GUARDADA EN HISTORIAL."
    )
    print("=" * 64)


if __name__ == "__main__":
    main()
