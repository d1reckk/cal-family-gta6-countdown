import os
import re
import json
import time
import hashlib
import calendar
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ================================================================
# STARTUP CHECK
# ================================================================

BOT_FILE = os.path.abspath(__file__)

print("=" * 64)
print("CAL BOT FILE:", BOT_FILE)
print("PYTHON:", os.sys.executable)
print("OS MODULE:", getattr(os, "__file__", "built-in"))
print("=" * 64)

if not hasattr(os, "environ"):
    raise RuntimeError("El mÃ³dulo os no expone environ; instalaciÃ³n Python daÃ±ada.")

import feedparser
import requests
from bs4 import BeautifulSoup


# ================================================================
# CAL BOT V19 DEFINITIVO
# Editor de noticias de CAL FAMILY
#
# Objetivos:
# - JSON robusto: ningÃºn KeyError debe tumbar el bot.
# - 429/503 de Gemini no detienen la evaluaciÃ³n.
# - No exige una fuente oficial de Rockstar.
# - Separa Noticias / AnÃ¡lisis / OpiniÃ³n.
# - Evita leaks y cyberleaks.
# - Deduplica feed + historial.
# - EvalÃºa varias candidatas y publica la mejor.
# - Conserva el historial.
# - Un Extended Look puede ser vÃ¡lido si un medio fiable aporta
#   informaciÃ³n nueva y sustancial.
# ================================================================

WEBHOOK = os.environ.get("NEWS_DRAFT_WEBHOOK", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HISTORY_FILE = "seen_news.json"
ROLE_ID = "1504921814759903343"

RSS_SOURCES = [
    {
        "name": "Google News GTA VI (direct)",
        "url": (
            "https://news.google.com/rss/search"
            "?q=GTA%20VI"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        ),
    },
    {
        "name": "Google News EN",
        "url": (
            "https://news.google.com/rss/"
            "?q=GTA+VI"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        ),
    },
    {
        "name": "Google News ES",
        "url": (
            "https://news.google.com/rss/"
            "?q=GTA+VI"
            "&hl=es"
            "&gl=ES"
            "&ceid=ES:es"
        ),
    },
    {
        "name": "Google News GTA 6",
        "url": (
            "https://news.google.com/rss/"
            "?q=GTA+6"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        ),
    },
    {
        "name": "Bing News GTA VI",
        "url": (
            "https://www.bing.com/news/search"
            "?q=GTA+VI"
            "&format=rss"
        ),
    },
]

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
# NORMALIZACIÃ“N / DUPLICADOS
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
    text = re.sub(r"[^a-z0-9Ã¡Ã©Ã­Ã³ÃºÃ¼Ã± ]+", " ", text)

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
# Solo eliminan casos inequÃ­vocos que no merece la pena mandar
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
    "filtraciÃ³n de datos",
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
    """
    Filtro local conservador.
    Solo bloquea titulares inequÃ­vocamente centrados en cyberleaks.
    La evaluaciÃ³n contextual del contenido la hace Gemini.
    """
    title_text = normalize_text(title)

    hard_patterns = [
        r"\bcyber ?leak\b",
        r"\bcyberleek\b",
        r"\bdata breach\b",
        r"\bstolen files?\b",
        r"\bhacked files?\b",
        r"\bstolen source code\b",
        r"\bstolen database\b",
        r"\bcredential(s)? leak\b",
        r"\barchivos robados\b",
        r"\bdatos robados\b",
    ]

    return any(
        re.search(pattern, title_text, flags=re.I)
        for pattern in hard_patterns
    )


# ================================================================
# RSS ROBUSTO + DIAGNÃ“STICO
# ================================================================

def fetch_rss_source(source):
    """Descarga una fuente RSS sin permitir que una fuente caÃ­da detenga el bot."""
    name = str(source.get("name") or "RSS desconocido")
    url = str(source.get("url") or "").strip()

    print("-" * 64)
    print(f"FEED: {name}")
    print(f"URL: {url}")

    if not url:
        print("FEED DESCARTADO: URL vacÃ­a.")
        return []

    try:
        response = requests.get(
            url,
            headers={
                **HEADERS,
                "Accept": (
                    "application/rss+xml, application/atom+xml, "
                    "application/xml, text/xml, text/html;q=0.9, */*;q=0.8"
                ),
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        print(f"FEED ERROR DE RED: {exc}")
        return []

    print("FEED HTTP:", response.status_code)
    print("FEED FINAL URL:", response.url)
    print(
        "FEED CONTENT-TYPE:",
        response.headers.get("content-type", "desconocido"),
    )
    print("FEED BYTES:", len(response.content))

    if response.status_code != 200:
        print("FEED DESCARTADO: HTTP no satisfactorio.")
        return []

    if not response.content.strip():
        print("FEED DESCARTADO: respuesta vacÃ­a.")
        return []

    preview = response.content[:400].decode(
        response.encoding or "utf-8",
        errors="replace",
    ).replace("\n", " ").replace("\r", " ")

    print("FEED PREVIEW:", preview[:250])

    parsed = feedparser.parse(response.content)

    if getattr(parsed, "bozo", False):
        parser_error = getattr(parsed, "bozo_exception", None)
        print(
            "FEED PARSER WARNING:",
            str(parser_error)[:500] if parser_error else "XML no estÃ¡ndar",
        )

    entries = list(getattr(parsed, "entries", []) or [])
    print("FEED ENTRIES:", len(entries))

    if not entries:
        low = preview.lower()
        if "<html" in low or "<!doctype html" in low:
            print(
                "FEED DIAGNÃ“STICO: el servidor devolviÃ³ HTML "
                "en lugar de RSS/XML; posible bloqueo o redirecciÃ³n."
            )
        else:
            print(
                "FEED DIAGNÃ“STICO: la respuesta no contiene "
                "entradas RSS utilizables."
            )
        return []

    for entry in entries:
        try:
            entry["_cal_feed_source"] = name
        except Exception:
            pass

    for index, entry in enumerate(entries[:3], start=1):
        print(
            f"  {index}. "
            f"{str(entry.get('title') or '(sin tÃ­tulo)')[:140]}"
        )

    return entries


def load_news_feed():
    """Combina todos los RSS y elimina duplicados entre fuentes."""
    combined = []
    seen_urls = set()
    seen_titles = []

    print("=" * 64)
    print("INICIANDO DIAGNÃ“STICO DE FUENTES RSS")
    print(f"FUENTES CONFIGURADAS: {len(RSS_SOURCES)}")
    print("=" * 64)

    for source in RSS_SOURCES:
        try:
            entries = fetch_rss_source(source)
        except Exception as exc:
            print(
                f"FEED ERROR INESPERADO en "
                f"{source.get('name', 'RSS desconocido')}: {exc}"
            )
            continue

        for entry in entries:
            title = str(entry.get("title") or "").strip()
            url = str(entry.get("link") or "").strip()

            if not title or not url:
                continue

            normalized_url = canonical_url(url)
            normalized_title = normalize_text(title)

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

    print("=" * 64)
    print(f"TOTAL ENTRADAS RSS ÃšNICAS: {len(combined)}")
    print("=" * 64)

    return combined


# ================================================================
# EXTRACCIÃ“N DE ARTÃCULOS
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

        print("Estado de pÃ¡gina:", response.status_code)

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
        print("No se pudo extraer el artÃ­culo:", exc)

    except Exception as exc:
        print("Error procesando el artÃ­culo:", exc)

    print("Longitud artÃ­culo extraÃ­do:", len(article_text))
    print("Longitud RSS disponible:", len(rss_content))
    print("URL final del artÃ­culo:", final_url)

    if len(article_text) >= 300:
        print("Usando TEXTO DEL ARTÃCULO.")
        return article_text, final_url

    if len(rss_content) >= 100:
        print("Usando EXTRACTO RSS como fallback.")
        return rss_content[:12000], final_url

    return "", final_url


# ================================================================
# JSON DE GEMINI
# ================================================================

def extract_json(text):
    if not isinstance(text, str):
        raise ValueError("Gemini no devolviÃ³ texto.")

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
            "No se encontrÃ³ un objeto JSON en la respuesta."
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
        "\n".join(f"- {item}" for item in previous)
        or "- Ninguna"
    )

    return f"""
Eres CAL BOT, editor de noticias de CAL FAMILY, una comunidad dedicada a GTA VI.

MISIÃ“N
Selecciona la mejor informaciÃ³n periodÃ­stica disponible. No publiques basura,
pero tampoco exijas una gran revelaciÃ³n para aceptar una noticia legÃ­tima.

CLASIFICACIÃ“N OBLIGATORIA
Usa exactamente una:
- "Noticias": hechos, declaraciones, cifras, fechas, decisiones o informaciÃ³n
  verificable presentada como hecho.
- "AnÃ¡lisis": un medio fiable interpreta material real y aporta contexto,
  observaciones concretas, comparaciones o conclusiones periodÃ­sticas
  sustanciales.
- "OpiniÃ³n": reacciÃ³n personal, review, gusto, predicciÃ³n o especulaciÃ³n.
  La opiniÃ³n no se publica automÃ¡ticamente.

FUENTES
NO necesitas una fuente oficial de Rockstar.
Una fuente secundaria fiable puede ser suficiente si identifica claramente
el dato, la declaraciÃ³n, la evidencia o el contexto.
Una declaraciÃ³n de un desarrollador, actor, ejecutivo u otra persona
identificable puede ser noticia aunque Rockstar no la haya publicado.
No confundas "no es Rockstar" con "no es fiable".

EXTENDED LOOK / TRÃILER / MATERIAL PROMOCIONAL
NO lo descartes automÃ¡ticamente.
- Si solo dice que el material es impresionante o enumera escenas, es opiniÃ³n
  o contenido superficial y debe descartarse.
- Si un medio fiable analiza el material y aporta detalles concretos,
  contexto verificable, observaciones relevantes o conclusiones periodÃ­sticas
  sustanciales, puede publicarse como "AnÃ¡lisis".
- Si el material oficial contiene un dato nuevo verificable, puede ser
  "Noticias".
- Nunca inventes novedades a partir de imÃ¡genes.

LEAKS / CYBERLEAKS
DESCARTA si la afirmaciÃ³n principal DEPENDE de material obtenido mediante
hackeo, intrusiÃ³n, acceso no autorizado, robo de archivos, credenciales,
bases de datos robadas, cÃ³digo fuente robado o cyberleaks.
NO descartes un artÃ­culo legÃ­timo solo porque mencione, recuerde o analice
leaks histÃ³ricos. EvalÃºa de quÃ© depende la afirmaciÃ³n principal.

DUPLICADOS
Compara el hecho central, no solo el tÃ­tulo.
Si el segundo artÃ­culo aporta un dato sustancialmente nuevo, puede ser vÃ¡lido.
Si repite el mismo anuncio sin novedad, descÃ¡rtalo.

ESTÃNDAR DE PUBLICACIÃ“N
Acepta una candidata cuando ofrece al menos una de estas cosas:
- declaraciÃ³n atribuida a una persona identificable;
- fecha o cambio confirmado;
- cifra o dato concreto;
- informaciÃ³n de desarrollo o producciÃ³n;
- plataformas, lanzamiento o distribuciÃ³n;
- casting;
- tecnologÃ­a;
- caracterÃ­sticas del juego;
- marketing;
- contexto verificable;
- anÃ¡lisis periodÃ­stico sustancial basado en material real.

IMPORTANTE
NO exijas que el artÃ­culo revele algo jamÃ¡s mencionado por ningÃºn otro medio.
La novedad puede estar en el dato, la declaraciÃ³n, el contexto o el anÃ¡lisis.

No confundas un RSS breve con un artÃ­culo inÃºtil: el contenido suministrado
puede ser un extracto. Si el extracto contiene una afirmaciÃ³n concreta y
atribuida a una fuente identificable, puede ser publicable.
Si no existe informaciÃ³n suficiente para redactar sin inventar, descarta.

DESCARTA:
- rumor/especulaciÃ³n sin respaldo;
- opiniÃ³n o reacciÃ³n pura;
- SEO/FAQ/recopilaciÃ³n que solo repite lo conocido;
- clickbait sin sustancia;
- cyberleak cuando la afirmaciÃ³n depende del material ilÃ­cito;
- duplicado sin novedad sustancial.

REDACCIÃ“N SI PUBLICAR
- EspaÃ±ol natural.
- 550-950 caracteres aproximadamente.
- Explica primero quÃ© ocurriÃ³ y despuÃ©s por quÃ© importa.
- Atribuye: "X dijo...", "segÃºn X...", "el medio informa...".
- No inventes nombres, cifras, fechas, citas ni contexto.
- Si category es "AnÃ¡lisis", deja claro que es anÃ¡lisis.
- No conviertas una posibilidad en un hecho.
- No pongas la URL dentro de "content".

PUNTUACIÃ“N
90-100 = excelente.
80-89 = muy buena.
75-79 = vÃ¡lida y publicable.
60-74 = interesante pero insuficiente.
0-59 = no publicable.

Una noticia factual bien respaldada debe puntuar por encima de una opiniÃ³n.
Un anÃ¡lisis sustancial puede superar a una noticia menor.

DECISIÃ“N
PUBLICAR solo si score >= 75 y category no es "OpiniÃ³n".
Si es anÃ¡lisis, usa "AnÃ¡lisis"; no lo fuerces a "Noticias".

RESPUESTA
Devuelve ÃšNICAMENTE JSON vÃ¡lido, sin Markdown ni texto fuera del JSON.

Formato:
{{
  "decision": "PUBLICAR" o "DESCARTAR",
  "reason": "motivo breve",
  "category": "Noticias" o "AnÃ¡lisis" o "OpiniÃ³n",
  "score": 0,
  "title": "tÃ­tulo en espaÃ±ol",
  "content": "texto final"
}}

Si DESCARTAR, content debe ser "".

HISTORIAL DE PUBLICACIONES:
{previous_text}

ARTÃCULO CANDIDATO:
TÃTULO: {title}
FUENTE: {source_url}

CONTENIDO DISPONIBLE:
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
                            "Gemini no devolviÃ³ candidates."
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
                            "Gemini no devolviÃ³ parts."
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
                            "Gemini devolviÃ³ texto vacÃ­o."
                        )

                    result = extract_json(text)

                    if not isinstance(result, dict):
                        raise RuntimeError(
                            "Gemini no devolviÃ³ un objeto JSON."
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
                # exactamente la misma peticiÃ³n.
                print(
                    "Error no recuperable en este modelo."
                )
                break

            except (
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                last_error = (
                    f"JSON invÃ¡lido: {exc}"
                )

                print(
                    "Respuesta JSON invÃ¡lida:",
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
        "ERROR: ningÃºn modelo de Gemini "
        "respondiÃ³ correctamente."
    )

    print(last_error or "")

    # IMPORTANTE:
    # None significa "esta candidata no pudo evaluarse".
    # main() continÃºa con las demÃ¡s candidatas.
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
            "ERROR DE CONEXIÃ“N CON DISCORD:",
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

    entries = (
        feed.entries
        if hasattr(feed, "entries")
        else feed
    )

    entries = list(entries or [])

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

    for entry in entries[:40]:
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
                "Ignorada: artÃ­culo ya procesado."
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
                "Ignorada: tÃ­tulo demasiado "
                "parecido al historial."
            )
            continue

        # DeduplicaciÃ³n dentro del feed.
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
            "feed_source": str(
                entry.get("_cal_feed_source")
                or "RSS desconocido"
            ),
        })

        seen_feed_ids.add(item_id)

        if normalized_url:
            seen_feed_urls.add(
                normalized_url
            )

        seen_feed_titles.append(title)

    return candidates


# ================================================================
# NORMALIZACIÃ“N DE LA RESPUESTA EDITORIAL
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
        "AnÃ¡lisis",
        "OpiniÃ³n",
    ):
        category = "Noticias"

    # OpiniÃ³n nunca se publica automÃ¡ticamente.
    if category == "OpiniÃ³n":
        decision = "DESCARTAR"

        if not reason:
            reason = (
                "El contenido estÃ¡ clasificado "
                "como opiniÃ³n."
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
        f"ðŸ§­ **{category}**\n\n"
        f"# {title}\n\n"
        f"{content}\n\n"
        f"ðŸ”— **Fuente original:** "
        f"{source_url}\n\n"
        f"âš ï¸ **REVISIÃ“N REQUERIDA**\n"
        f"Este contenido fue preparado por Cal Bot "
        f"como borrador editorial. Revisa la "
        f"informaciÃ³n antes de publicarlo en "
        f"#noticias.\n\n"
        f"-# Cal Bot Â· {date_text}"
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
        feed_entries = load_news_feed()
    except Exception as exc:
        print(
            "ERROR GENERAL LEYENDO RSS:",
            exc,
        )
        return

    if not feed_entries:
        print("=" * 64)
        print("NO SE ENCONTRARON NOTICIAS EN NINGÃšN FEED.")
        print("El bot NO va a fallar silenciosamente.")
        print(
            "Revisa arriba FEED HTTP, CONTENT-TYPE, BYTES, PREVIEW "
            "y FEED ENTRIES para identificar el bloqueo."
        )
        print(
            "Si todas las fuentes devuelven 0, el problema es el "
            "acceso RSS desde GitHub Actions, no Gemini ni Discord."
        )
        print("=" * 64)
        return

    print(
        "Buscando noticias nuevas..."
    )

    candidates = get_candidates(
        feed_entries,
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
            "Ninguna noticia nueva pasÃ³ "
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
        print("Feed:", candidate.get("feed_source", "RSS desconocido"))
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
                "Error obteniendo artÃ­culo; "
                "continuando:",
                exc,
            )
            continue

        if not source_content:
            print(
                "Descartada: no hay "
                "informaciÃ³n suficiente."
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
                "idÃ©ntico al historial."
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
                "Descartada: seÃ±ales de "
                "leak/cyberleak."
            )
            continue

        print(
            "CAL BOT ESTÃ EVALUANDO..."
        )

        try:
            editorial_source = (
                f"{final_source_url}\n"
                f"FUENTE RSS DETECTADA: "
                f"{candidate.get('feed_source', 'RSS desconocido')}"
            )

            raw_result = ask_gemini(
                candidate["title"],
                editorial_source,
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
                "Respuesta editorial invÃ¡lida. "
                "Continuando..."
            )
            continue

        if result["decision"] != "PUBLICAR":
            print("DESCARTADA POR CAL BOT")
            print(
                f"CategorÃ­a: {result.get('category', 'Noticias')} | "
                f"PuntuaciÃ³n: {result.get('score', 0):.1f}/100"
            )

            print(
                result["reason"]
                or "No cumple el estÃ¡ndar editorial."
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

        if category == "OpiniÃ³n":
            print(
                "Descartada: categorÃ­a OpiniÃ³n."
            )
            continue

        # Umbral mÃ­nimo de publicaciÃ³n.
        if score < 75:
            print(
                "Descartada: puntuaciÃ³n "
                f"insuficiente ({score:.1f}/100)."
            )
            continue

        if published_title_duplicate(
            ai_title,
            history,
            0.86,
        ):
            print(
                "Descartada: el tÃ­tulo final "
                "coincide con historial."
            )
            continue

        # Evita que Gemini aÃ±ada la fuente
        # dentro del cuerpo.
        ai_content = re.sub(
            r"ðŸ”—\s*\*\*Fuente original:\*\*"
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
            "source_url": final_source_url.split("\n", 1)[0],
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
    # SelecciÃ³n final
    # ------------------------------------------------------------

    if not evaluated_results:
        print("=" * 64)
        print(
            "NINGUNA NOTICIA CUMPLIÃ“ "
            "LOS CRITERIOS."
        )
        print(
            "El bot revisÃ³ las candidatas "
            "disponibles sin publicar basura."
        )
        print("=" * 64)
        return

    category_priority = {
        "Noticias": 2,
        "AnÃ¡lisis": 1,
        "OpiniÃ³n": 0,
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
    print("TÃ­tulo:", best["title"])
    print("CategorÃ­a:", best["category"])
    print(
        "PuntuaciÃ³n:",
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
            "La noticia NO se guardarÃ¡ "
            "como publicada."
        )
        raise SystemExit(1)

    # ------------------------------------------------------------
    # Historial: SOLO despuÃ©s de confirmar Discord.
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
    print("DISCORD CONFIRMÃ“.")
    print(
        "NOTICIA PUBLICADA Y "
        "GUARDADA EN HISTORIAL."
    )
    print("=" * 64)


if __name__ == "__main__":
    main()
