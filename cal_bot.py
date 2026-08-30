import re
import json
import time
import random
import hashlib
import calendar
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup


# ================================================================
# CAL BOT V20
# Editor de noticias de CAL FAMILY / GTA VI
#
# V20:
# - RSS Google News/Bing corregido y diagnóstico robusto.
# - Ranking de candidatas antes de gastar llamadas a Gemini.
# - Gemini actualizado a modelos GA actuales.
# - Structured JSON con responseSchema-compatible REST.
# - Sin temperature/top_p/top_k en Gemini 3.6/3.7.
# - Filtro de leaks/cyberleaks contextual y conservador.
# - Mejor deduplicación.
# - Discord con retry de rate-limit y límite seguro.
# - Historial atómico y tolerante a corrupción.
# - Nunca guarda una noticia como publicada antes de confirmar Discord.
# ================================================================


# ================================================================
# CONFIG
# ================================================================

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HISTORY_FILE = os.environ.get("HISTORY_FILE", "seen_news.json")
ROLE_ID = os.environ.get("DISCORD_ROLE_ID", "1504921814759903343").strip()

# Gemini 3.7 Flash es GA y el modelo principal recomendado para este
# flujo editorial. 3.6/3.5 son fallbacks GA.
GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]

MAX_HISTORY = 1000
MAX_NEWS_AGE_HOURS = 72
MAX_CANDIDATES_TO_EVALUATE = 12

REQUEST_TIMEOUT = 25
GEMINI_TIMEOUT = 60
DISCORD_TIMEOUT = 20

MAX_ARTICLE_CHARS = 18000
MAX_RSS_CHARS = 12000
MAX_DISCORD_CONTENT = 2000
SAFE_DISCORD_LIMIT = 1900

HEADERS = {
    "User-Agent": (
        "CAL-Bot/20 (+news-editor; "
        "https://github.com/) "
        "Mozilla/5.0"
    ),
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ================================================================
# RSS
# ================================================================

RSS_SOURCES = [
    {
        "name": "Google News GTA VI EN",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%20VI"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        ),
    },
    {
        "name": "Google News GTA 6 EN",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%206"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        ),
    },
    {
        "name": "Google News GTA VI ES",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%20VI"
            "&hl=es"
            "&gl=ES"
            "&ceid=ES:es"
        ),
    },
    {
        "name": "Google News GTA 6 ES",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%206"
            "&hl=es"
            "&gl=ES"
            "&ceid=ES:es"
        ),
    },
    {
        "name": "Bing News GTA VI",
        "url": (
            "https://www.bing.com/news/search"
            "?q=GTA%20VI"
            "&format=rss"
        ),
    },
    {
        "name": "Bing News GTA 6",
        "url": (
            "https://www.bing.com/news/search"
            "?q=GTA%206"
            "&format=rss"
        ),
    },
]


# ================================================================
# SOURCE QUALITY
# ================================================================

# Esto NO decide si una noticia es verdadera. Solo ayuda a ordenar
# candidatas para que Gemini vea primero fuentes generalmente más útiles.
SOURCE_TIERS = {
    # Tier 3: fuentes primarias / grandes agencias / medios de alta señal.
    "rockstargames.com": 3,
    "take2games.com": 3,
    "reuters.com": 3,
    "bloomberg.com": 3,
    "bbc.com": 3,
    "apnews.com": 3,

    # Tier 2: medios especializados conocidos.
    "ign.com": 2,
    "gamespot.com": 2,
    "eurogamer.net": 2,
    "videogameschronicle.com": 2,
    "vgc.news": 2,
    "kotaku.com": 2,
    "pcgamer.com": 2,
    "gamesindustry.biz": 2,
    "polygon.com": 2,
    "thegamer.com": 2,
    "pushsquare.com": 2,
    "playstation.com": 2,
    "xbox.com": 2,
}

GENERIC_LOW_SIGNAL_HINTS = (
    "faq",
    "how to",
    "best",
    "top 10",
    "everything we know",
    "release date guide",
    "wiki",
    "walkthrough",
)


# ================================================================
# STARTUP
# ================================================================

def startup_check():
    print("=" * 70)
    print("CAL BOT V20")
    print("PYTHON:", os.sys.executable)
    print("FILE:", os.path.abspath(__file__))
    print("=" * 70)

    if not hasattr(os, "environ"):
        raise RuntimeError("Instalación de Python dañada: os.environ no existe.")

    if not WEBHOOK:
        raise RuntimeError("Falta el secret NEWS_DRAFT_WEBHOOK.")

    if not GEMINI_KEY:
        raise RuntimeError("Falta el secret GEMINI_API_KEY.")


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
        p = urlparse(str(url).strip())

        ignored = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "utm_id",
            "oc",
            "cmpid",
            "fbclid",
            "gclid",
        }

        q = parse_qs(p.query, keep_blank_values=True)
        clean = {
            key: value
            for key, value in q.items()
            if key.lower() not in ignored
        }

        return urlunparse(
            (
                p.scheme.lower(),
                p.netloc.lower(),
                p.path.rstrip("/"),
                "",
                urlencode(clean, doseq=True),
                "",
            )
        )
    except Exception:
        return str(url).strip()


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
        "html.parser",
    ).get_text(" ", strip=True)


# ================================================================
# HISTORY
# ================================================================

def empty_history():
    return {
        "published": [],
        "titles": [],
        "content_hashes": [],
        "source_urls": [],
    }


def load_history():
    default = empty_history()

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
        history = empty_history()

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

    directory = os.path.dirname(os.path.abspath(HISTORY_FILE))
    os.makedirs(directory, exist_ok=True)

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

    return any(
        similarity(title, old_title) >= threshold
        for old_title in titles
    )


# ================================================================
# LOCAL FILTERS
# ================================================================

TITLE_BLOCKLIST = [
    "cyberleek",
    "cyber leak",
    "credential leak",
    "stolen files",
    "hacked files",
    "private files",
    "archivos robados",
    "archivos privados",
    "datos robados",
    "brecha de seguridad",
]

HARD_TITLE_PATTERNS = [
    r"\bhow to\b",
    r"\bbest .* console to buy\b",
]

# Importante:
# No basta con que un artículo mencione "leak".
# Bloqueamos localmente solo cuando el titular presenta claramente
# el acceso ilícito/robo como el núcleo de la historia.
LEAK_ACQUISITION_PATTERNS = [
    r"\b(stolen|hacked|breached|exposed)\s+(files?|database|source code|credentials?)\b",
    r"\b(source code|database|credentials?)\s+(was|were)?\s*(stolen|hacked|leaked)\b",
    r"\bdata breach\b",
    r"\bcredential(s)?\s+leak\b",
    r"\bcyber\s*leak\b",
    r"\barchivos\s+robados\b",
    r"\bdatos\s+robados\b",
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
    """
    Filtro local conservador.

    NO bloquea artículos simplemente porque mencionen un leak histórico,
    una filtración pasada o una polémica.

    Sí bloquea cuando el titular/contenido contiene varias señales de
    adquisición ilícita y el propio artículo depende de ese material.
    La decisión contextual final sigue siendo de Gemini.
    """
    title_text = normalize_text(title)
    content_text = normalize_text(content)

    # Titulares inequívocos: bloqueo inmediato.
    if any(
        re.search(pattern, title_text, flags=re.I)
        for pattern in LEAK_ACQUISITION_PATTERNS
    ):
        return True

    # En el cuerpo exigimos más de una señal para reducir falsos positivos.
    signals = [
        r"\bhacked\b",
        r"\bhackeo\b",
        r"\bintrusion\b",
        r"\bintrusi[oó]n\b",
        r"\bunauthorized access\b",
        r"\baccesso no autorizado\b",
        r"\bstolen files?\b",
        r"\barchivos robados\b",
        r"\bstolen source code\b",
        r"\bc[oó]digo fuente robado\b",
        r"\bstolen database\b",
        r"\bbase de datos robada\b",
        r"\bcredentials? leak\b",
        r"\bcredenciales filtradas\b",
        r"\bdata breach\b",
        r"\bbrecha de seguridad\b",
    ]

    hits = sum(
        1
        for pattern in signals
        if re.search(pattern, content_text, flags=re.I)
    )

    # Dos o más señales + lenguaje de adquisición ilícita.
    acquisition = any(
        phrase in content_text
        for phrase in (
            "unauthorized access",
            "acceso no autorizado",
            "stolen files",
            "archivos robados",
            "stolen source code",
            "código fuente robado",
            "stolen database",
            "base de datos robada",
        )
    )

    return hits >= 2 and acquisition


# ================================================================
# RSS
# ================================================================

def fetch_rss_source(source):
    name = str(source.get("name") or "RSS desconocido")
    url = str(source.get("url") or "").strip()

    print("-" * 70)
    print("FEED:", name)
    print("URL:", url)

    if not url:
        print("FEED DESCARTADO: URL vacía.")
        return []

    try:
        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        print("FEED ERROR DE RED:", exc)
        return []

    print("HTTP:", response.status_code)
    print("FINAL URL:", response.url)
    print(
        "CONTENT-TYPE:",
        response.headers.get("content-type", "desconocido"),
    )
    print("BYTES:", len(response.content))

    if response.status_code != 200:
        print("FEED DESCARTADO: HTTP no satisfactorio.")
        return []

    if not response.content.strip():
        print("FEED DESCARTADO: respuesta vacía.")
        return []

    preview = response.content[:500].decode(
        response.encoding or "utf-8",
        errors="replace",
    ).replace("\n", " ").replace("\r", " ")

    print("PREVIEW:", preview[:300])

    parsed = feedparser.parse(response.content)

    if getattr(parsed, "bozo", False):
        parser_error = getattr(parsed, "bozo_exception", None)
        print(
            "PARSER WARNING:",
            str(parser_error)[:500]
            if parser_error
            else "XML no estándar",
        )

    entries = list(getattr(parsed, "entries", []) or [])
    print("ENTRIES:", len(entries))

    if not entries:
        low = preview.lower()

        if "<html" in low or "<!doctype html" in low:
            print("DIAGNÓSTICO: el servidor devolvió HTML en vez de RSS.")
        else:
            print("DIAGNÓSTICO: no hay entradas RSS utilizables.")

        return []

    for entry in entries:
        try:
            entry["_cal_feed_source"] = name
        except Exception:
            pass

    return entries


def load_news_feed():
    combined = []
    seen_urls = set()
    seen_titles = []

    print("=" * 70)
    print("INICIANDO DIAGNÓSTICO DE RSS")
    print("FUENTES CONFIGURADAS:", len(RSS_SOURCES))
    print("=" * 70)

    for source in RSS_SOURCES:
        try:
            entries = fetch_rss_source(source)
        except Exception as exc:
            print(
                f"ERROR INESPERADO EN {source.get('name', 'RSS')}:",
                exc,
            )
            continue

        for entry in entries:
            try:
                title = str(entry.get("title") or "").strip()
                url = str(entry.get("link") or "").strip()

                if not title or not url:
                    continue

                normalized_url = canonical_url(url)

                if normalized_url and normalized_url in seen_urls:
                    continue

                if any(
                    similarity(title, old_title) >= 0.90
                    for old_title in seen_titles
                ):
                    continue

                if normalized_url:
                    seen_urls.add(normalized_url)

                seen_titles.append(title)
                combined.append(entry)
            except Exception as exc:
                print("Entrada RSS inválida:", exc)

    print("=" * 70)
    print("TOTAL ENTRADAS RSS ÚNICAS:", len(combined))
    print("=" * 70)

    return combined


# ================================================================
# ARTICLE EXTRACTION
# ================================================================

def extract_entry_time(entry):
    for field in (
        "published_parsed",
        "updated_parsed",
    ):
        parsed_time = getattr(entry, field, None)

        if parsed_time:
            try:
                return datetime.fromtimestamp(
                    calendar.timegm(parsed_time),
                    timezone.utc,
                )
            except Exception:
                pass

    return None


def fetch_article(url, rss_content):
    article_text = ""
    final_url = url

    try:
        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        print("Estado página:", response.status_code)
        final_url = response.url or url

        if response.ok and response.text:
            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            # Elimina contenido no editorial.
            for tag in soup.find_all(
                [
                    "script",
                    "style",
                    "noscript",
                    "svg",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "aside",
                ]
            ):
                tag.decompose()

            paragraphs = []

            for paragraph in soup.find_all("p"):
                text = paragraph.get_text(
                    " ",
                    strip=True,
                )

                if 45 <= len(text) <= 4000:
                    paragraphs.append(text)

            # Deduplicación de párrafos.
            clean_paragraphs = []
            seen = set()

            for paragraph in paragraphs:
                key = normalize_text(paragraph)

                if not key or key in seen:
                    continue

                seen.add(key)
                clean_paragraphs.append(paragraph)

            article_text = "\n".join(clean_paragraphs)
            article_text = article_text[:MAX_ARTICLE_CHARS]

    except requests.RequestException as exc:
        print("No se pudo extraer artículo:", exc)
    except Exception as exc:
        print("Error procesando artículo:", exc)

    print("Longitud artículo:", len(article_text))
    print("Longitud RSS:", len(rss_content))
    print("URL final:", final_url)

    if len(article_text) >= 300:
        return article_text, final_url

    if len(rss_content) >= 100:
        return rss_content[:MAX_RSS_CHARS], final_url

    return "", final_url


# ================================================================
# CANDIDATE RANKING
# ================================================================

def source_domain(url):
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def source_quality(url):
    domain = source_domain(url)

    if domain in SOURCE_TIERS:
        return SOURCE_TIERS[domain]

    for known, tier in SOURCE_TIERS.items():
        if domain.endswith("." + known):
            return tier

    return 1


def low_signal_title(title):
    normalized = normalize_text(title)
    return any(
        hint in normalized
        for hint in GENERIC_LOW_SIGNAL_HINTS
    )


def candidate_priority(candidate, now):
    published_time = candidate.get("published_time")

    if published_time:
        age_hours = max(
            0.0,
            (now - published_time).total_seconds() / 3600,
        )
        recency_score = max(
            0.0,
            36.0 - min(age_hours, 36.0),
        )
    else:
        recency_score = 8.0

    quality_score = source_quality(candidate.get("google_url", "")) * 10
    content_score = min(
        len(candidate.get("rss_content", "")) / 250.0,
        20.0,
    )
    low_signal_penalty = 10.0 if low_signal_title(
        candidate.get("title", "")
    ) else 0.0

    return (
        recency_score
        + quality_score
        + content_score
        - low_signal_penalty
    )


def get_candidates(feed, history):
    now = datetime.now(timezone.utc)
    candidates = []

    entries = (
        feed.entries
        if hasattr(feed, "entries")
        else feed
    )

    entries = list(entries or [])

    published_ids = set(history.get("published", []))
    history_urls = set(history.get("source_urls", []))

    history_titles = history.get("titles", [])
    if not isinstance(history_titles, list):
        history_titles = []

    seen_feed_ids = set()
    seen_feed_urls = set()
    seen_feed_titles = []

    for entry in entries:
        if not hasattr(entry, "get"):
            continue

        title = str(entry.get("title") or "").strip()
        google_url = str(entry.get("link") or "").strip()

        if not title or not google_url:
            continue

        if title_should_skip(title):
            print("Ignorada por filtro local:", title)
            continue

        published_time = extract_entry_time(entry)

        if (
            published_time
            and now - published_time > timedelta(
                hours=MAX_NEWS_AGE_HOURS
            )
        ):
            continue

        if (
            published_time
            and published_time - now > timedelta(hours=3)
        ):
            # Evita fechas RSS claramente futuras por errores de feed.
            continue

        normalized_url = canonical_url(google_url)
        item_id = stable_id(title, normalized_url)

        if item_id in published_ids:
            continue

        if normalized_url and normalized_url in history_urls:
            continue

        if any(
            similarity(title, old_title) >= 0.88
            for old_title in history_titles
        ):
            continue

        if item_id in seen_feed_ids:
            continue

        if normalized_url and normalized_url in seen_feed_urls:
            continue

        if any(
            similarity(title, old_title) >= 0.90
            for old_title in seen_feed_titles
        ):
            continue

        rss_content = clean_html(
            entry.get("summary") or ""
        )

        description = clean_html(
            entry.get("description") or ""
        )

        combined_content = (
            rss_content + "\n" + description
        ).strip()

        candidate = {
            "id": item_id,
            "title": title,
            "google_url": google_url,
            "rss_content": combined_content,
            "feed_source": str(
                entry.get("_cal_feed_source")
                or "RSS desconocido"
            ),
            "published_time": published_time,
        }

        candidates.append(candidate)

        seen_feed_ids.add(item_id)

        if normalized_url:
            seen_feed_urls.add(normalized_url)

        seen_feed_titles.append(title)

    candidates.sort(
        key=lambda item: candidate_priority(item, now),
        reverse=True,
    )

    return candidates


# ================================================================
# GEMINI JSON
# ================================================================

EDITOR_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["PUBLICAR", "DESCARTAR"],
        },
        "reason": {
            "type": "string",
        },
        "category": {
            "type": "string",
            "enum": ["Noticias", "Análisis", "Opinión"],
        },
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 100,
        },
        "title": {
            "type": "string",
        },
        "content": {
            "type": "string",
        },
    },
    "required": [
        "decision",
        "reason",
        "category",
        "score",
        "title",
        "content",
    ],
}


def build_editorial_prompt(
    title,
    source_url,
    source_content,
    history,
):
    previous = history.get("titles", [])

    if not isinstance(previous, list):
        previous = []

    previous_text = "\n".join(
        f"- {item}"
        for item in previous[-60:]
    ) or "- Ninguna"

    return f"""
Eres CAL BOT V20, editor de noticias de CAL FAMILY, una comunidad dedicada a GTA VI.

OBJETIVO
Selecciona información periodística útil y redacta un borrador en español.
No publiques basura, pero tampoco exijas que cada noticia sea una exclusiva mundial.

CLASIFICACIÓN
Usa exactamente una:
- "Noticias": hechos, declaraciones, cifras, fechas, decisiones o información
  verificable presentada como hecho.
- "Análisis": un medio fiable interpreta material real y aporta contexto,
  observaciones concretas, comparaciones o conclusiones periodísticas
  sustanciales.
- "Opinión": reacción personal, review, gusto, predicción o especulación.
  Opinión NO se publica automáticamente.

FUENTES
No necesitas una fuente oficial de Rockstar.
Una fuente secundaria fiable puede ser suficiente si identifica claramente
el dato, declaración, evidencia o contexto.
No confundas "no es Rockstar" con "no es fiable".

EXTENDED LOOK / TRÁILER / MATERIAL PROMOCIONAL
No lo descartes automáticamente.
- Si solo elogia el material o enumera escenas, es superficial/opinión.
- Si un medio fiable analiza el material y aporta observaciones concretas,
  contexto verificable o conclusiones sustanciales, puede ser "Análisis".
- Si material oficial contiene un dato nuevo verificable, puede ser "Noticias".
- Nunca inventes novedades a partir de imágenes.

LEAKS / CYBERLEAKS — REGLA CRÍTICA
DESCARTA cuando la afirmación principal DEPENDE de material obtenido mediante:
hackeo, intrusión, acceso no autorizado, robo de archivos, credenciales,
bases de datos robadas, código fuente robado o cyberleaks.

NO descartes automáticamente un artículo porque:
- mencione un leak histórico;
- explique una filtración pasada;
- informe sobre la respuesta de Rockstar;
- cite un leak únicamente como contexto histórico.

Pregunta clave:
"¿La noticia necesita el material obtenido ilícitamente para sostener su afirmación principal?"
Si la respuesta es sí -> DESCARTAR.
Si solo lo menciona como contexto -> puede ser válida.

DUPLICADOS
Compara el hecho central, no solo el título.
Si el segundo artículo aporta un dato sustancialmente nuevo, puede ser válido.
Si repite el mismo anuncio sin novedad sustancial, descártalo.

ESTÁNDAR DE PUBLICACIÓN
Una candidata puede ser publicable si ofrece al menos una:
- declaración atribuida a una persona identificable;
- fecha o cambio confirmado;
- cifra o dato concreto;
- información de desarrollo o producción;
- plataformas, lanzamiento o distribución;
- casting;
- tecnología;
- características del juego;
- marketing;
- contexto verificable;
- análisis periodístico sustancial basado en material real.

NO exijas que sea una exclusiva mundial.
La novedad puede estar en el dato, declaración, contexto o análisis.

REDACCIÓN
- Español natural.
- Aproximadamente 550-950 caracteres.
- Explica primero qué ocurrió y después por qué importa.
- Atribuye: "X dijo...", "según X...", "el medio informa...".
- No inventes nombres, cifras, fechas, citas ni contexto.
- Si es análisis, deja claro que es análisis.
- No conviertas una posibilidad en un hecho.
- No pongas la URL dentro de "content".
- Si no hay información suficiente para redactar sin inventar, DESCARTAR.

DESCARTA:
- rumor/especulación sin respaldo;
- opinión o reacción pura;
- SEO/FAQ/recopilación superficial;
- clickbait sin sustancia;
- cyberleak cuando la afirmación depende del material ilícito;
- duplicado sin novedad sustancial.

PUNTUACIÓN
90-100 excelente
80-89 muy buena
75-79 válida y publicable
60-74 interesante pero insuficiente
0-59 no publicable

Una noticia factual bien respaldada debe puntuar por encima de una opinión.
Un análisis sustancial puede superar a una noticia menor.

DECISIÓN
PUBLICAR solo si score >= 75 y category != "Opinión".
Si category es "Análisis", conserva "Análisis".

Devuelve SOLO JSON válido siguiendo exactamente el schema solicitado.

HISTORIAL RECIENTE:
{previous_text}

ARTÍCULO CANDIDATO
TÍTULO:
{title}

FUENTE:
{source_url}

CONTENIDO:
{source_content}
""".strip()


def extract_json(text):
    if not isinstance(text, str):
        raise ValueError("Gemini no devolvió texto.")

    text = text.strip()

    # Structured output normalmente ya llega como JSON puro.
    # Este fallback ayuda si un endpoint devuelve fences.
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
    ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            raise ValueError(
                "No se encontró JSON válido en la respuesta de Gemini."
            )

        return json.loads(text[start:end + 1])


# ================================================================
# GEMINI API
# ================================================================

def gemini_endpoint(model):
    return (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )


def parse_retry_after(response, default_seconds):
    value = response.headers.get("Retry-After")

    try:
        if value is not None:
            return max(1.0, float(value))
    except (TypeError, ValueError):
        pass

    return default_seconds


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

    # Gemini 3.6/3.7 ya no debe recibir temperature/top_p/top_k.
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt,
                    }
                ],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 1800,
            "responseMimeType": "application/json",
            "responseSchema": EDITOR_SCHEMA,
        },
    }

    last_error = None

    for model in GEMINI_MODELS:
        endpoint = gemini_endpoint(model)

        print("Intentando Gemini:", model)

        for attempt in range(3):
            try:
                response = SESSION.post(
                    endpoint,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": GEMINI_KEY,
                    },
                    json=payload,
                    timeout=GEMINI_TIMEOUT,
                )

                print("Gemini HTTP:", response.status_code)

                if response.status_code == 200:
                    data = response.json()

                    candidates = data.get("candidates") or []

                    if not isinstance(candidates, list) or not candidates:
                        raise RuntimeError(
                            "Gemini no devolvió candidates."
                        )

                    first = (
                        candidates[0]
                        if isinstance(candidates[0], dict)
                        else {}
                    )

                    content = first.get("content") or {}
                    parts = content.get("parts") or []

                    text = ""

                    for part in parts:
                        if not isinstance(part, dict):
                            continue

                        value = part.get("text")

                        if isinstance(value, str) and value.strip():
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

                # Rate limit / servidor temporalmente indisponible.
                if response.status_code in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    wait = parse_retry_after(
                        response,
                        min(15.0, 2.0 ** (attempt + 1))
                        + random.uniform(0, 0.75),
                    )

                    print(
                        f"Gemini temporalmente no disponible. "
                        f"Reintentando en {wait:.1f}s..."
                    )

                    time.sleep(wait)
                    continue

                # 400/401/403/404: normalmente cambiar de modelo
                # o configuración es más útil que repetir 3 veces.
                print(
                    "Error no recuperable en este modelo; "
                    "probando fallback."
                )
                break

            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                print("Error Gemini:", exc)

                if attempt < 2:
                    wait = 2.0 * (attempt + 1)
                    time.sleep(wait)

            except Exception as exc:
                last_error = str(exc)
                print("Error inesperado Gemini:", exc)

                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))

    print("ERROR: ningún modelo de Gemini respondió correctamente.")
    print(last_error or "")
    return None


# ================================================================
# EDITORIAL RESULT
# ================================================================

def normalize_editor_result(result):
    if not isinstance(result, dict):
        return None

    decision = str(
        result.get("decision") or "DESCARTAR"
    ).upper().strip()

    reason = str(
        result.get("reason") or ""
    ).strip()

    category = str(
        result.get("category") or "Noticias"
    ).strip()

    title = str(
        result.get("title") or ""
    ).strip()

    content = str(
        result.get("content") or ""
    ).strip()

    try:
        score = float(result.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0

    score = max(0.0, min(100.0, score))

    if decision not in ("PUBLICAR", "DESCARTAR"):
        decision = "DESCARTAR"

    if category not in (
        "Noticias",
        "Análisis",
        "Opinión",
    ):
        category = "Noticias"

    if category == "Opinión":
        decision = "DESCARTAR"

        if not reason:
            reason = "El contenido es opinión."

    if score < 75:
        decision = "DESCARTAR"

    if decision == "DESCARTAR":
        content = ""

    return {
        "decision": decision,
        "reason": reason,
        "category": category,
        "score": score,
        "title": title,
        "content": content,
    }


# ================================================================
# DISCORD
# ================================================================

def trim_discord_content(text, limit=SAFE_DISCORD_LIMIT):
    text = str(text or "").strip()

    if len(text) <= limit:
        return text

    trimmed = text[: limit - 1].rstrip()

    # Evita cortar en medio de una palabra si hay un espacio cerca.
    last_space = trimmed.rfind(" ")

    if last_space >= int(limit * 0.80):
        trimmed = trimmed[:last_space]

    return trimmed + "…"


def build_discord_message(
    category,
    title,
    content,
    source_url,
):
    date_text = datetime.now(
        timezone.utc
    ).strftime("%d/%m/%Y")

    mention = (
        f"<@&{ROLE_ID}>\n\n"
        if ROLE_ID
        else ""
    )

    message = (
        f"{mention}"
        f"🧭 **{category}**\n\n"
        f"# {title}\n\n"
        f"{content}\n\n"
        f"🔗 **Fuente original:** <{source_url}>\n\n"
        f"⚠️ **REVISIÓN REQUERIDA**\n"
        f"Este contenido fue preparado por Cal Bot como "
        f"borrador editorial. Revisa la información antes "
        f"de publicarlo en #noticias.\n\n"
        f"-# Cal Bot · {date_text}"
    )

    return trim_discord_content(
        message,
        SAFE_DISCORD_LIMIT,
    )


def send_discord(message):
    if not WEBHOOK:
        print("Discord: webhook vacío.")
        return False

    payload = {
        "content": message,
        "username": "Cal Bot",
    }

    if ROLE_ID:
        payload["allowed_mentions"] = {
            "roles": [ROLE_ID],
        }
    else:
        payload["allowed_mentions"] = {
            "parse": [],
        }

    for attempt in range(3):
        try:
            response = SESSION.post(
                WEBHOOK,
                json=payload,
                timeout=DISCORD_TIMEOUT,
            )
        except requests.RequestException as exc:
            print("ERROR DE CONEXIÓN CON DISCORD:", exc)

            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue

            return False

        print("Discord HTTP:", response.status_code)

        if response.status_code in (200, 204):
            return True

        if response.status_code == 429:
            try:
                data = response.json()
                wait = float(data.get("retry_after", 2))
            except Exception:
                wait = 2

            wait = min(max(wait, 1), 20)

            print(
                f"Discord rate limit. Esperando {wait:.1f}s..."
            )

            time.sleep(wait)
            continue

        print(
            "Respuesta Discord:",
            response.text[:2000],
        )

        # Repetir solo errores de servidor.
        if response.status_code >= 500 and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue

        return False

    return False


# ================================================================
# MAIN
# ================================================================

def main():
    startup_check()

    history = load_history()

    try:
        feed_entries = load_news_feed()
    except Exception as exc:
        print("ERROR GENERAL LEYENDO RSS:", exc)
        return

    if not feed_entries:
        print("=" * 70)
        print("NO SE ENCONTRARON NOTICIAS EN NINGÚN FEED.")
        print(
            "Revisa HTTP, CONTENT-TYPE, BYTES, PREVIEW y ENTRIES."
        )
        print("=" * 70)
        return

    candidates = get_candidates(
        feed_entries,
        history,
    )

    print("=" * 70)
    print("CANDIDATAS DESPUÉS DE FILTROS:", len(candidates))
    print("=" * 70)

    if not candidates:
        print("Ninguna noticia nueva pasó los filtros iniciales.")
        return

    limit = min(
        len(candidates),
        MAX_CANDIDATES_TO_EVALUATE,
    )

    evaluated_results = []

    for index, candidate in enumerate(
        candidates[:limit],
        start=1,
    ):
        print("=" * 70)
        print(f"EVALUANDO CANDIDATA {index}/{limit}")
        print(candidate["title"])
        print(candidate["google_url"])
        print("Feed:", candidate.get("feed_source"))
        print("=" * 70)

        try:
            source_content, final_source_url = fetch_article(
                candidate["google_url"],
                candidate["rss_content"],
            )
        except Exception as exc:
            print(
                "Error obteniendo artículo; continuando:",
                exc,
            )
            continue

        if not source_content:
            print("Descartada: información insuficiente.")
            continue

        source_hash = content_hash(source_content)

        history_hashes = set(
            history.get("content_hashes", [])
        )

        if source_hash in history_hashes:
            print("Descartada: contenido idéntico al historial.")
            continue

        # Filtro local contextual.
        if looks_like_leak_or_cyberleak(
            candidate["title"],
            source_content,
        ):
            print(
                "Descartada por señales locales fuertes "
                "de leak/cyberleak."
            )
            continue

        editorial_source = (
            f"{final_source_url}\n"
            f"RSS: {candidate.get('feed_source', 'desconocido')}"
        )

        print("CAL BOT EVALUANDO CON GEMINI...")

        try:
            raw_result = ask_gemini(
                candidate["title"],
                editorial_source,
                source_content,
                history,
            )
        except Exception as exc:
            print(
                "Fallo de Gemini para esta candidata; "
                "continuando:",
                exc,
            )
            continue

        if raw_result is None:
            continue

        result = normalize_editor_result(raw_result)

        if not result:
            print("Respuesta editorial inválida.")
            continue

        print(
            f"Resultado: {result['decision']} | "
            f"{result['category']} | "
            f"{result['score']:.1f}/100"
        )

        if result["decision"] != "PUBLICAR":
            print(
                "Motivo:",
                result["reason"] or "No cumple el estándar.",
            )
            continue

        ai_title = result["title"]
        ai_content = result["content"]
        category = result["category"]
        score = result["score"]

        if not ai_title or not ai_content:
            print("Descartada: respuesta incompleta.")
            continue

        if published_title_duplicate(
            ai_title,
            history,
            0.86,
        ):
            print("Descartada: título final duplicado.")
            continue

        # Evita que Gemini meta la fuente en el cuerpo.
        ai_content = re.sub(
            r"(?:🔗\s*)?\**Fuente original:\**.*?(?=\n|$)",
            "",
            ai_content,
            flags=re.I,
        ).strip()

        final_message = build_discord_message(
            category,
            ai_title,
            ai_content,
            final_source_url,
        )

        if len(final_message) > MAX_DISCORD_CONTENT:
            print(
                "Descartada: mensaje supera el límite de Discord."
            )
            continue

        evaluated_results.append(
            {
                "candidate": candidate,
                "source_hash": source_hash,
                "source_url": final_source_url,
                "title": ai_title,
                "content": ai_content,
                "category": category,
                "score": score,
                "reason": result["reason"],
                "message": final_message,
            }
        )

    # ============================================================
    # SELECCIÓN FINAL
    # ============================================================

    if not evaluated_results:
        print("=" * 70)
        print("NINGUNA NOTICIA CUMPLIÓ LOS CRITERIOS.")
        print("=" * 70)
        return

    category_priority = {
        "Noticias": 2,
        "Análisis": 1,
        "Opinión": 0,
    }

    evaluated_results.sort(
        key=lambda item: (
            item["score"],
            category_priority.get(item["category"], 0),
            source_quality(item["source_url"]),
        ),
        reverse=True,
    )

    best = evaluated_results[0]

    print("=" * 70)
    print("MEJOR NOTICIA SELECCIONADA")
    print("Título:", best["title"])
    print("Categoría:", best["category"])
    print("Puntuación:", f'{best["score"]:.1f}/100')
    print(
        "Candidatas publicables evaluadas:",
        len(evaluated_results),
    )
    print("=" * 70)

    if not send_discord(best["message"]):
        print(
            "La noticia NO se guardará como publicada "
            "porque Discord no confirmó el envío."
        )
        raise SystemExit(1)

    # ============================================================
    # HISTORIAL SOLO DESPUÉS DE DISCORD
    # ============================================================

    history.setdefault("published", []).append(
        best["candidate"]["id"]
    )

    history.setdefault("titles", []).append(
        best["title"]
    )

    history.setdefault("content_hashes", []).append(
        best["source_hash"]
    )

    history.setdefault("source_urls", []).append(
        canonical_url(best["source_url"])
    )

    save_history(history)

    print("=" * 70)
    print("DISCORD CONFIRMÓ.")
    print("NOTICIA ENVIADA Y GUARDADA EN HISTORIAL.")
    print("=" * 70)


if __name__ == "__main__":
    main()
