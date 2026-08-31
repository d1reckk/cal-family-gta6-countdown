import os
import re
import json
import time
import random
import hashlib
import calendar
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote

import feedparser
import requests
from bs4 import BeautifulSoup


# ================================================================
# CAL BOT V24
# Editor de noticias de CAL FAMILY / GTA VI
# ================================================================

BOT_VERSION = "V24"

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
HISTORY_FILE = os.environ.get("HISTORY_FILE", "seen_news.json")
ROLE_ID = os.environ.get("DISCORD_ROLE_ID", "1504921814759903343").strip()

DEFAULT_GEMINI_MODELS = (
    "gemini-2.5-flash,"
    "gemini-2.5-flash-lite"
)

GEMINI_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_MODELS",
        DEFAULT_GEMINI_MODELS
    ).split(",")
    if model.strip()
]

MAX_HISTORY = 1000
MAX_NEWS_AGE_HOURS = 72
MAX_FUTURE_NEWS_HOURS = 3
MAX_CANDIDATES_TO_EVALUATE = 12

REQUEST_TIMEOUT = 25
GEMINI_TIMEOUT = 90
DISCORD_TIMEOUT = 20

MAX_ARTICLE_CHARS = 18000
MAX_RSS_CHARS = 12000
SAFE_DISCORD_LIMIT = 1900
MIN_ARTICLE_CONTENT_CHARS = 300

HEADERS = {
    "User-Agent": (
        "CAL-Bot/24 (+news-editor) "
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

RSS_SOURCES = [
    {
        "name": "Google News GTA VI EN",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%20VI&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "name": "Google News GTA 6 EN",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%206&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "name": "Google News GTA VI ES",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%20VI&hl=es&gl=ES&ceid=ES:es"
        ),
    },
    {
        "name": "Google News GTA 6 ES",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%206&hl=es&gl=ES&ceid=ES:es"
        ),
    },
    {
        "name": "Bing News GTA VI",
        "url": (
            "https://www.bing.com/news/search"
            "?q=GTA%20VI&format=rss"
        ),
    },
    {
        "name": "Bing News GTA 6",
        "url": (
            "https://www.bing.com/news/search"
            "?q=GTA%206&format=rss"
        ),
    },
]

SOURCE_TIERS = {
    "rockstargames.com": 3,
    "take2games.com": 3,
    "reuters.com": 3,
    "bloomberg.com": 3,
    "bbc.com": 3,
    "apnews.com": 3,
    "forbes.com": 3,
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
    "guide",
)

TITLE_BLOCKLIST = (
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
)

HARD_TITLE_PATTERNS = (
    r"\bhow to\b",
    r"\bbest .* console to buy\b",
)

LEAK_ACQUISITION_PATTERNS = (
    r"\b(stolen|hacked|breached|exposed)\s+"
    r"(files?|database|source code|credentials?)\b",
    r"\b(source code|database|credentials?)\s+"
    r"(was|were)?\s*(stolen|hacked|leaked)\b",
    r"\bdata breach\b",
    r"\bcredential(s)?\s+leak\b",
    r"\bcyber\s*leak\b",
    r"\barchivos\s+robados\b",
    r"\bdatos\s+robados\b",
)


# ================================================================
# STARTUP / DIAGNOSTICS
# ================================================================

def startup_check():
    print("=" * 72)
    print(f"CAL BOT {BOT_VERSION}")
    print("PYTHON:", os.sys.executable)
    print("FILE:", os.path.abspath(__file__))
    print("WORKING DIRECTORY:", os.getcwd())
    print("=" * 72)

    if not WEBHOOK:
        raise RuntimeError("Falta el Secret NEWS_DRAFT_WEBHOOK.")

    if not GEMINI_KEY:
        raise RuntimeError("Falta el Secret GEMINI_API_KEY.")

    if not GEMINI_MODELS:
        raise RuntimeError("GEMINI_MODELS está vacío.")

    print("NEWS_DRAFT_WEBHOOK: OK")
    print("GEMINI_API_KEY: OK")
    print("HISTORY_FILE:", HISTORY_FILE)
    print("GEMINI_MODELS:", ", ".join(GEMINI_MODELS))
    print("ROLE_ID:", ROLE_ID or "desactivado")


# ================================================================
# TEXT / URL HELPERS
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
        parsed = urlparse(str(url).strip())

        ignored = {
            "utm_source", "utm_medium", "utm_campaign",
            "utm_term", "utm_content", "utm_id",
            "oc", "cmpid", "fbclid", "gclid",
            "ref", "ref_src"
        }

        query = parse_qs(parsed.query, keep_blank_values=True)

        clean_query = {
            key: value
            for key, value in query.items()
            if key.lower() not in ignored
        }

        hostname = (parsed.hostname or "").lower()
        port = parsed.port
        netloc = f"{hostname}:{port}" if port else hostname

        return urlunparse((
            parsed.scheme.lower(),
            netloc,
            parsed.path.rstrip("/"),
            "",
            urlencode(clean_query, doseq=True),
            "",
        ))
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
        "html.parser"
    ).get_text(" ", strip=True)


def is_google_news_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "news.google.com"
    except Exception:
        return False


def is_bing_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in {"bing.com", "www.bing.com"}
    except Exception:
        return False


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
        print("Historial no encontrado. Se creará uno nuevo.")
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
            ensure_ascii=False
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
    title_text = normalize_text(title)
    content_text = normalize_text(content)

    for pattern in LEAK_ACQUISITION_PATTERNS:
        if re.search(pattern, title_text, flags=re.I):
            return True

    signals = (
        r"\bhacked\b",
        r"\bhackeo\b",
        r"\bintrusion\b",
        r"\bintrusi[oó]n\b",
        r"\bunauthorized access\b",
        r"\bacceso no autorizado\b",
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
    )

    hits = sum(
        1
        for pattern in signals
        if re.search(pattern, content_text, flags=re.I)
    )

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
    name = str(source.get("name") or "RSS desconocido").strip()
    url = str(source.get("url") or "").strip()

    print("-" * 72)
    print("FEED:", name)

    if not url:
        print("FEED DESCARTADO: URL vacía.")
        return []

    try:
        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
    except requests.RequestException as exc:
        print("FEED ERROR DE RED:", exc)
        return []

    print("HTTP:", response.status_code)
    print("FINAL URL:", response.url)
    print("BYTES:", len(response.content))

    if response.status_code != 200:
        print("FEED DESCARTADO: HTTP no satisfactorio.")
        return []

    if not response.content.strip():
        print("FEED DESCARTADO: respuesta vacía.")
        return []

    parsed = feedparser.parse(response.content)

    if getattr(parsed, "bozo", False):
        parser_error = getattr(parsed, "bozo_exception", None)
        print(
            "PARSER WARNING:",
            str(parser_error)[:500]
            if parser_error else "XML no estándar"
        )

    entries = list(getattr(parsed, "entries", []) or [])
    print("ENTRIES:", len(entries))

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

    print("=" * 72)
    print("INICIANDO DIAGNÓSTICO DE RSS")
    print("FUENTES CONFIGURADAS:", len(RSS_SOURCES))
    print("=" * 72)

    for source in RSS_SOURCES:
        try:
            entries = fetch_rss_source(source)
        except Exception as exc:
            print("ERROR INESPERADO:", exc)
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

    print("=" * 72)
    print("TOTAL ENTRADAS RSS ÚNICAS:", len(combined))
    print("=" * 72)

    return combined


# ================================================================
# TIME / ARTICLE EXTRACTION
# ================================================================

def extract_entry_time(entry):
    for field in ("published_parsed", "updated_parsed"):
        parsed_time = getattr(entry, field, None)

        if parsed_time:
            try:
                return datetime.fromtimestamp(
                    calendar.timegm(parsed_time),
                    timezone.utc
                )
            except Exception:
                continue

    return None


def extract_source_metadata(soup, fallback_time=None):
    article_date = ""

    date_selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"property": "article:modified_time"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "publish-date"}),
        ("meta", {"name": "publication-date"}),
        ("time", {}),
    ]

    for tag_name, attrs in date_selectors:
        try:
            for element in soup.find_all(tag_name, attrs):
                value = (
                    element.get("content")
                    or element.get("datetime")
                    or element.get_text(" ", strip=True)
                )

                if value and len(str(value).strip()) > 5:
                    article_date = str(value).strip()
                    break

            if article_date:
                break
        except Exception:
            continue

    if not article_date and fallback_time:
        article_date = fallback_time.strftime("%d/%m/%Y")

    return article_date


def extract_rss_content(rss_content):
    return clean_html(rss_content)[:MAX_RSS_CHARS]


def extract_google_news_source(entry):
    # Google News RSS normally exposes the publisher through <source>.
    source = getattr(entry, "source", None)

    if isinstance(source, dict):
        return (
            str(source.get("title") or "").strip(),
            str(source.get("href") or "").strip()
        )

    try:
        title = str(getattr(source, "title", "") or "").strip()
        href = str(getattr(source, "href", "") or "").strip()
        return title, href
    except Exception:
        return "", ""


def fetch_article(url, rss_content, fallback_time=None, rss_source_name=""):
    article_text = ""
    final_url = url
    article_date = ""
    source_name = rss_source_name or ""

    try:
        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        print("Estado página:", response.status_code)
        final_url = response.url or url

        if response.ok and response.text:
            soup = BeautifulSoup(response.text, "html.parser")

            try:
                source_meta = (
                    soup.find("meta", property="og:site_name")
                    or soup.find(
                        "meta",
                        attrs={"name": "application-name"}
                    )
                )

                if source_meta:
                    source_name = (
                        source_meta.get("content") or ""
                    ).strip()
            except Exception:
                pass

            article_date = extract_source_metadata(
                soup,
                fallback_time
            )

            for tag in soup.find_all([
                "script", "style", "noscript", "svg",
                "nav", "footer", "header", "form",
                "aside", "iframe", "button"
            ]):
                try:
                    tag.decompose()
                except Exception:
                    pass

            paragraphs = []

            for paragraph in soup.find_all("p"):
                text = paragraph.get_text(" ", strip=True)

                if 45 <= len(text) <= 4000:
                    paragraphs.append(text)

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
    print("URL final:", final_url)

    if len(article_text) >= MIN_ARTICLE_CONTENT_CHARS:
        return article_text, final_url, source_name, article_date

    fallback = extract_rss_content(rss_content)

    if len(fallback) >= 100:
        return fallback, final_url, source_name, article_date

    return "", final_url, source_name, article_date


# ================================================================
# SOURCE
# ================================================================

def source_domain(url):
    try:
        domain = (urlparse(url).hostname or "").lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain
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


def source_display_name(url, detected_name=""):
    if detected_name and detected_name.lower() not in {
        "google news", "bing news"
    }:
        return detected_name

    domain = source_domain(url)

    special_names = {
        "forbes.com": "Forbes",
        "ign.com": "IGN",
        "reuters.com": "Reuters",
        "bbc.com": "BBC",
        "apnews.com": "AP News",
        "rockstargames.com": "Rockstar Games",
        "take2games.com": "Take-Two Interactive",
        "gamespot.com": "GameSpot",
        "eurogamer.net": "Eurogamer",
        "videogameschronicle.com": "VGC",
        "vgc.news": "VGC",
        "kotaku.com": "Kotaku",
        "pcgamer.com": "PC Gamer",
        "polygon.com": "Polygon",
        "thegamer.com": "TheGamer",
        "gamesindustry.biz": "GamesIndustry.biz",
        "pushsquare.com": "Push Square",
        "playstation.com": "PlayStation",
        "xbox.com": "Xbox",
    }

    return special_names.get(domain, domain or "Fuente")


# ================================================================
# CANDIDATES
# ================================================================

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
            (now - published_time).total_seconds() / 3600
        )

        recency_score = max(
            0.0,
            36.0 - min(age_hours, 36.0)
        )
    else:
        recency_score = 8.0

    feed_bonus = 5.0

    content_score = min(
        len(candidate.get("rss_content", "")) / 250.0,
        20.0
    )

    low_signal_penalty = (
        10.0 if low_signal_title(candidate.get("title", ""))
        else 0.0
    )

    return recency_score + feed_bonus + content_score - low_signal_penalty


def get_candidates(feed, history):
    now = datetime.now(timezone.utc)
    candidates = []

    published_ids = set(history.get("published", []))
    history_urls = set(history.get("source_urls", []))
    history_titles = history.get("titles", [])

    if not isinstance(history_titles, list):
        history_titles = []

    seen_feed_ids = set()
    seen_feed_urls = set()
    seen_feed_titles = []

    for entry in list(feed or []):
        if not hasattr(entry, "get"):
            continue

        title = str(entry.get("title") or "").strip()
        google_url = str(entry.get("link") or "").strip()

        if not title or not google_url:
            continue

        if title_should_skip(title):
            continue

        published_time = extract_entry_time(entry)

        if published_time:
            age = now - published_time

            if age > timedelta(hours=MAX_NEWS_AGE_HOURS):
                continue

            if published_time - now > timedelta(
                hours=MAX_FUTURE_NEWS_HOURS
            ):
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

        source_title, source_href = extract_google_news_source(entry)

        candidate = {
            "id": item_id,
            "title": title,
            "google_url": google_url,
            "rss_content": combined_content,
            "feed_source": str(
                entry.get("_cal_feed_source") or "RSS desconocido"
            ),
            "publisher_name": source_title,
            "publisher_url": source_href,
            "published_time": published_time,
        }

        candidates.append(candidate)

        seen_feed_ids.add(item_id)

        if normalized_url:
            seen_feed_urls.add(normalized_url)

        seen_feed_titles.append(title)

    candidates.sort(
        key=lambda item: candidate_priority(item, now),
        reverse=True
    )

    return candidates


# ================================================================
# GEMINI SCHEMA
# IMPORTANT: Gemini REST structured-output schema uses lowercase
# JSON Schema types. The old V23 used "OBJECT", "STRING", etc.
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
    "additionalProperties": False,
}


# ================================================================
# GEMINI PROMPT
# ================================================================

def build_editorial_prompt(title, source_url, source_content, history):
    previous = history.get("titles", [])

    if not isinstance(previous, list):
        previous = []

    previous_text = "\n".join(
        f"- {item}"
        for item in previous[-60:]
    ) or "- Ninguna"

    return f"""
Eres CAL BOT {BOT_VERSION}, editor de noticias de CAL FAMILY, una comunidad dedicada exclusivamente a GTA VI.

Evalúa la fuente y decide si existe una noticia real, relevante y publicable.

CATEGORÍAS:
- Noticias: hechos verificables, declaraciones, cifras, fechas, decisiones o información periodística presentada como hecho.
- Análisis: análisis periodístico sustancial de material real, incluyendo material oficial.
- Opinión: gustos, reviews, predicciones o especulación personal. NO se publica automáticamente.

LEAKS:
Descarta si la afirmación principal depende de hackeo, intrusión, acceso no autorizado, archivos robados, bases de datos robadas, código fuente robado o credenciales robadas.
Mencionar un leak histórico como contexto no basta para descartar.

DUPLICADOS:
Compara el hecho central. Si el artículo aporta información nueva y sustancial puede publicarse; si repite lo mismo sin novedad, descarta.

PUBLICAR:
Solo si score >= 75 y category != "Opinión".
Si falta información suficiente para redactar correctamente, descarta.

REDACCIÓN:
Escribe en español natural.
Content: aproximadamente 550-950 caracteres cuando sea posible.
Debe explicar qué ocurre, el dato/evidencia importante y por qué importa para GTA VI.
Atribuye correctamente análisis y conclusiones.
Nunca presentes una interpretación de un medio como confirmación de Rockstar.
No inventes nombres, fechas, cifras, citas ni declaraciones.
No incluyas URLs dentro de content.
No incluyas "Fuente original" dentro de content.

TÍTULO:
Claro, atractivo y sin clickbait. No conviertas especulación en hecho.

DEVUELVE SOLO JSON COMPATIBLE CON EL ESQUEMA.

HISTORIAL RECIENTE:
{previous_text}

ARTÍCULO:
TÍTULO:
{title}

FUENTE:
{source_url}

CONTENIDO:
{source_content}
""".strip()


# ================================================================
# JSON
# ================================================================

def extract_json(text):
    if not isinstance(text, str):
        raise ValueError("Gemini no devolvió texto.")

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I
    )

    text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start < 0 or end <= start:
        raise ValueError("No se encontró JSON válido.")

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido: {exc}") from exc


# ================================================================
# GEMINI
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


def get_gemini_text(data):
    candidates = data.get("candidates") or []

    if not candidates:
        feedback = data.get("promptFeedback") or {}
        raise RuntimeError(
            "Gemini no devolvió candidates. "
            f"promptFeedback={feedback}"
        )

    first = candidates[0] if isinstance(candidates[0], dict) else {}
    content = first.get("content") or {}
    parts = content.get("parts") or []

    texts = []

    for part in parts:
        if not isinstance(part, dict):
            continue

        value = part.get("text")

        if isinstance(value, str) and value.strip():
            texts.append(value.strip())

    if not texts:
        finish_reason = first.get("finishReason")
        raise RuntimeError(
            "Gemini devolvió texto vacío. "
            f"finishReason={finish_reason}"
        )

    return "\n".join(texts).strip()


def ask_gemini(title, source_url, source_content, history):
    prompt = build_editorial_prompt(
        title,
        source_url,
        source_content,
        history
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1800,

            # Legacy generateContent structured-output fields.
            # Keep these here; the schema itself uses lowercase JSON
            # Schema types.
            "responseMimeType": "application/json",
            "responseSchema": EDITOR_SCHEMA,
        },
    }

    last_error = None

    for model in GEMINI_MODELS:
        print("=" * 72)
        print("INTENTANDO GEMINI:", model)

        endpoint = gemini_endpoint(model)

        for attempt in range(3):
            try:
                response = SESSION.post(
                    endpoint,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": GEMINI_KEY,
                    },
                    json=payload,
                    timeout=GEMINI_TIMEOUT
                )

                print(
                    f"Gemini HTTP: {response.status_code} "
                    f"(modelo={model}, intento={attempt + 1}/3)"
                )

                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise RuntimeError(
                            "Gemini devolvió una respuesta no-JSON."
                        ) from exc

                    text = get_gemini_text(data)
                    result = extract_json(text)

                    if not isinstance(result, dict):
                        raise RuntimeError(
                            "Gemini devolvió JSON que no es un objeto."
                        )

                    print("Gemini OK:", model)
                    return result

                response_text = response.text[:3000]
                last_error = (
                    f"HTTP {response.status_code}: {response_text}"
                )

                print("Gemini error:", last_error)

                if response.status_code in (
                    408, 409, 429, 500, 502, 503, 504
                ):
                    base_wait = min(20.0, 2.0 ** (attempt + 1))
                    retry_after = parse_retry_after(
                        response,
                        base_wait
                    )

                    wait = max(retry_after, base_wait)
                    wait += random.uniform(0, 0.75)

                    print(
                        f"Reintentando Gemini en {wait:.1f}s..."
                    )

                    time.sleep(wait)
                    continue

                # 400/404 usually means model or request configuration.
                # Try the next configured model rather than looping forever.
                if response.status_code in (400, 404):
                    print(
                        "Solicitud/modelo rechazado; "
                        "probando el siguiente modelo."
                    )
                    break

                break

            except requests.RequestException as exc:
                last_error = str(exc)
                print("Error de red Gemini:", exc)

                if attempt < 2:
                    wait = 2 * (attempt + 1) + random.uniform(0, 0.5)
                    time.sleep(wait)

            except (ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                print("JSON Gemini inválido:", exc)

                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

            except Exception as exc:
                last_error = str(exc)
                print("Error inesperado Gemini:", exc)

                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

    print("=" * 72)
    print("ERROR: ningún modelo de Gemini respondió correctamente.")
    print("Último error:", last_error or "desconocido")
    print("=" * 72)

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

    reason = str(result.get("reason") or "").strip()

    category = str(
        result.get("category") or "Noticias"
    ).strip()

    title = str(result.get("title") or "").strip()
    content = str(result.get("content") or "").strip()

    try:
        score = float(result.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0

    score = max(0.0, min(100.0, score))

    if decision not in ("PUBLICAR", "DESCARTAR"):
        decision = "DESCARTAR"

    if category not in ("Noticias", "Análisis", "Opinión"):
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

    trimmed = text[:limit - 1].rstrip()
    last_space = trimmed.rfind(" ")

    if last_space >= int(limit * 0.80):
        trimmed = trimmed[:last_space]

    return trimmed + "…"


def clean_ai_content(content):
    content = str(content or "").strip()

    content = re.sub(
        r"(?:🔗\s*)?\**Fuente original:\**.*",
        "",
        content,
        flags=re.I | re.S
    )

    content = re.sub(r"https?://\S+", "", content)
    content = re.sub(r"```+", "", content)

    return content.strip()


def category_emoji(category):
    return {
        "Noticias": "📰",
        "Análisis": "🧭",
        "Opinión": "💬",
    }.get(category, "📰")


def build_discord_message(
    category,
    title,
    content,
    source_url,
    source_name="Fuente",
    article_date=""
):
    mention = f"<@&{ROLE_ID}>\n\n" if ROLE_ID else ""
    emoji = category_emoji(category)

    content = clean_ai_content(content)
    source_name = source_name or "Fuente"

    footer_date = (
        f" · {article_date}"
        if article_date
        else " · " + datetime.now(timezone.utc).strftime("%d/%m/%Y")
    )

    title = title.strip() or "GTA VI"

    message = (
        f"{mention}"
        f"{emoji} **{category}**\n\n"
        f"# {title}\n\n"
        f"{content}\n\n"
        f"🔗 **Fuente original:** <{source_url}>\n\n"
        f"⚠️ **Esto no es un anuncio ni una filtración.** "
        f"Este contenido fue preparado por Cal Bot como borrador "
        f"editorial a partir de la fuente indicada. "
        f"Revisa la información antes de publicarlo.\n\n"
        f"-# {source_name}{footer_date} · visto en @gtasix_"
    )

    return trim_discord_content(
        message,
        SAFE_DISCORD_LIMIT
    )


def send_discord(message):
    if not WEBHOOK:
        print("Discord: webhook vacío.")
        return False

    if not isinstance(message, str) or not message.strip():
        print("Discord: mensaje vacío.")
        return False

    if len(message) > 2000:
        print(
            "Discord: mensaje supera 2000 caracteres "
            "antes del envío."
        )
        return False

    payload = {
        "content": message,
        "username": "Cal Bot",
        "allowed_mentions": (
            {"roles": [ROLE_ID]}
            if ROLE_ID
            else {"parse": []}
        ),
    }

    for attempt in range(3):
        try:
            response = SESSION.post(
                WEBHOOK,
                json=payload,
                timeout=DISCORD_TIMEOUT
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
                wait = 2.0

            wait = min(max(wait, 1.0), 20.0)

            print(
                f"Discord rate limit. Esperando {wait:.1f}s..."
            )

            time.sleep(wait)
            continue

        print("Respuesta Discord:", response.text[:2000])

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

    # ------------------------------------------------------------
    # RSS
    # ------------------------------------------------------------

    try:
        feed_entries = load_news_feed()
    except Exception as exc:
        print("ERROR GENERAL LEYENDO RSS:", exc)
        return

    if not feed_entries:
        print("=" * 72)
        print("NO SE ENCONTRARON NOTICIAS.")
        print("=" * 72)
        return

    # ------------------------------------------------------------
    # CANDIDATES
    # ------------------------------------------------------------

    candidates = get_candidates(feed_entries, history)

    print("=" * 72)
    print("CANDIDATAS DESPUÉS DE FILTROS:", len(candidates))
    print("=" * 72)

    if not candidates:
        print("Ninguna noticia nueva pasó los filtros.")
        return

    limit = min(len(candidates), MAX_CANDIDATES_TO_EVALUATE)
    evaluated_results = []

    # ------------------------------------------------------------
    # EVALUATION
    # ------------------------------------------------------------

    for index, candidate in enumerate(candidates[:limit], start=1):
        print("=" * 72)
        print(f"EVALUANDO CANDIDATA {index}/{limit}")
        print("Título:", candidate["title"])
        print("RSS:", candidate["google_url"])
        print("=" * 72)

        try:
            (
                source_content,
                final_source_url,
                detected_source_name,
                article_date
            ) = fetch_article(
                candidate["google_url"],
                candidate["rss_content"],
                candidate.get("published_time"),
                candidate.get("publisher_name", "")
            )
        except Exception as exc:
            print("Error obteniendo artículo:", exc)
            continue

        # If the HTTP redirect didn't leave Google News, use publisher URL
        # when the feed exposed one.
        if (
            is_google_news_url(final_source_url)
            and candidate.get("publisher_url")
            and candidate["publisher_url"].startswith("http")
        ):
            print(
                "Google News no redirigió al publisher; "
                "probando URL del publisher."
            )

            try:
                (
                    publisher_content,
                    publisher_final_url,
                    publisher_name,
                    publisher_date
                ) = fetch_article(
                    candidate["publisher_url"],
                    candidate["rss_content"],
                    candidate.get("published_time"),
                    candidate.get("publisher_name", "")
                )

                if len(publisher_content) > len(source_content):
                    source_content = publisher_content
                    final_source_url = publisher_final_url
                    detected_source_name = (
                        publisher_name or detected_source_name
                    )
                    article_date = (
                        publisher_date or article_date
                    )
            except Exception as exc:
                print(
                    "No se pudo abrir URL del publisher:",
                    exc
                )

        if not source_content:
            print(
                "Descartada: información insuficiente."
            )
            continue

        source_hash = content_hash(source_content)

        history_hashes = set(
            history.get("content_hashes", [])
        )

        if source_hash in history_hashes:
            print(
                "Descartada: contenido idéntico al historial."
            )
            continue

        if looks_like_leak_or_cyberleak(
            candidate["title"],
            source_content
        ):
            print(
                "Descartada por señales fuertes de leak/cyberleak."
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
                history
            )
        except Exception as exc:
            print("Fallo Gemini:", exc)
            continue

        if raw_result is None:
            continue

        result = normalize_editor_result(raw_result)

        if not result:
            print("Respuesta editorial inválida.")
            continue

        print(
            "Resultado:",
            result["decision"],
            "|",
            result["category"],
            "|",
            f'{result["score"]:.1f}/100'
        )

        if result["decision"] != "PUBLICAR":
            print(
                "Motivo:",
                result["reason"] or "No cumple."
            )
            continue

        ai_title = result["title"]
        ai_content = result["content"]
        category = result["category"]
        score = result["score"]

        if not ai_title or not ai_content:
            print(
                "Descartada: respuesta incompleta."
            )
            continue

        if published_title_duplicate(
            ai_title,
            history,
            0.86
        ):
            print(
                "Descartada: título duplicado."
            )
            continue

        ai_content = clean_ai_content(ai_content)

        if len(ai_content) < 100:
            print(
                "Descartada: contenido demasiado corto."
            )
            continue

        final_source_name = source_display_name(
            final_source_url,
            detected_source_name
        )

        final_message = build_discord_message(
            category,
            ai_title,
            ai_content,
            final_source_url,
            final_source_name,
            article_date
        )

        if len(final_message) > 2000:
            print(
                "Descartada: mensaje supera el límite de Discord."
            )
            continue

        evaluated_results.append({
            "candidate": candidate,
            "source_hash": source_hash,
            "source_url": final_source_url,
            "source_name": final_source_name,
            "article_date": article_date,
            "title": ai_title,
            "content": ai_content,
            "category": category,
            "score": score,
            "reason": result["reason"],
            "message": final_message,
        })

    # ------------------------------------------------------------
    # FINAL SELECTION
    # ------------------------------------------------------------

    if not evaluated_results:
        print("=" * 72)
        print("NINGUNA NOTICIA CUMPLIÓ LOS CRITERIOS.")
        print("=" * 72)
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
        reverse=True
    )

    best = evaluated_results[0]

    print("=" * 72)
    print("MEJOR NOTICIA SELECCIONADA")
    print("Título:", best["title"])
    print("Categoría:", best["category"])
    print("Puntuación:", f'{best["score"]:.1f}/100')
    print("Fuente:", best["source_name"])
    print("Candidatas publicables:", len(evaluated_results))
    print("=" * 72)

    print("\nMENSAJE QUE SE ENVIARÁ A DISCORD:\n")
    print(best["message"])
    print()

    # ------------------------------------------------------------
    # SEND
    # ------------------------------------------------------------

    if not send_discord(best["message"]):
        print("Discord NO confirmó el envío.")
        raise SystemExit(1)

    # ------------------------------------------------------------
    # HISTORY ONLY AFTER DISCORD SUCCESS
    # ------------------------------------------------------------

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

    try:
        save_history(history)
    except Exception as exc:
        print(
            "ADVERTENCIA: Discord recibió la noticia pero no "
            "se pudo guardar el historial:",
            exc
        )
        raise SystemExit(1)

    print("=" * 72)
    print("DISCORD CONFIRMÓ.")
    print("NOTICIA ENVIADA.")
    print("HISTORIAL ACTUALIZADO.")
    print("=" * 72)


# ================================================================
# ENTRY POINT
# ================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCAL BOT detenido manualmente.")
    except Exception as exc:
        print("=" * 72)
        print("ERROR FATAL:")
        print(repr(exc))
        print("=" * 72)
        raise
