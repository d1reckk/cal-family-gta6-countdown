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
# CAL BOT V18 DEFINITIVO
# Editor de noticias de CAL FAMILY
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

# Se prueban en orden. Los 429/5xx pasan al siguiente modelo.
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

# El objetivo es un punto intermedio:
# - No exigir comunicado oficial para cada noticia.
# - No publicar opinión, rumor o simple reacción.
# - Sí aceptar información concreta procedente de medios fiables.

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124 Safari/537.36"
    )
}

# Títulos que normalmente no aportan una noticia útil al canal.
TITLE_BLOCKLIST = [
    "cyberleek",
    "leak",
    "leaked",
    "filtration",
    "filtración",
    "filtrado",
    "what we know",
    "everything we know",
    "everything leaked",
    "things you may have missed",
    "takeaways",
    "things we want to see",
    "best console to buy",
    "made me say",
    "i'm not blown away",
    "opinion",
    "review",
    "reaction",
]

# Artículos que suelen ser listas, opiniones o contenido SEO reciclado.
WEAK_PATTERNS = [
    r"\bwhat we know\b",
    r"\beverything we know\b",
    r"\bthings you may have missed\b",
    r"\btakeaways\b",
    r"\bwe want to see\b",
    r"\bhow to\b",
    r"\bbest .* for gta\b",
]


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
        p = urlparse(url)
        q = parse_qs(p.query, keep_blank_values=True)
        ignored = {
            "utm_source", "utm_medium", "utm_campaign",
            "utm_term", "utm_content", "oc"
        }
        clean = {k: v for k, v in q.items() if k not in ignored}
        return urlunparse((
            p.scheme,
            p.netloc.lower(),
            p.path.rstrip("/"),
            "",
            urlencode(clean, doseq=True),
            ""
        ))
    except Exception:
        return url


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
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {
            "published": [],
            "titles": [],
            "content_hashes": [],
            "source_urls": []
        }

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    if isinstance(data, list):
        data = {"published": data}

    for key in ("published", "titles", "content_hashes", "source_urls"):
        data.setdefault(key, [])

    return data


def save_history(history):
    history["published"] = history["published"][-MAX_HISTORY:]
    history["titles"] = history["titles"][-MAX_HISTORY:]
    history["content_hashes"] = history["content_hashes"][-MAX_HISTORY:]
    history["source_urls"] = history["source_urls"][-MAX_HISTORY:]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def published_title_duplicate(title, history, threshold=0.88):
    for old in history["titles"]:
        if similarity(title, old) >= threshold:
            return True
    return False


def title_should_skip(title):
    normalized = normalize_text(title)

    for word in TITLE_BLOCKLIST:
        if normalize_text(word) in normalized:
            return True

    for pattern in WEAK_PATTERNS:
        if re.search(pattern, normalized, flags=re.I):
            return True

    return False


def fetch_article(google_url, rss_content):
    article_text = ""
    final_url = google_url

    try:
        response = requests.get(
            google_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        print("Estado de página:", response.status_code)
        final_url = response.url or google_url

        if response.ok:
            soup = BeautifulSoup(response.text, "html.parser")

            for tag in soup.find_all(
                ["script", "style", "noscript", "svg",
                 "nav", "footer", "header", "form"]
            ):
                tag.decompose()

            paragraphs = []
            for p in soup.find_all("p"):
                text = p.get_text(" ", strip=True)
                if len(text) >= 45:
                    paragraphs.append(text)

            article_text = "\n".join(paragraphs)
            article_text = article_text[:18000]

    except Exception as exc:
        print("No se pudo extraer el artículo:", exc)

    if len(article_text) >= 300:
        return article_text, final_url

    if len(rss_content) >= 100:
        return rss_content[:12000], final_url

    return "", final_url


def extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]

    return json.loads(text)


def build_editorial_prompt(title, source_url, source_content, history):
    """Construye el prompt sin str.format(), evitando el KeyError de V18."""
    previous = history["titles"][-80:]
    previous_text = "\n".join(f"- {x}" for x in previous) or "- Ninguna"

    # Las llaves del JSON se escriben literalmente porque este prompt
    # se construye con f-string y NO con .format().
    return f"""
Eres CAL BOT, editor de noticias de CAL FAMILY, una comunidad dedicada a GTA VI.

OBJETIVO EDITORIAL
Encuentra el punto intermedio entre publicar demasiado y publicar demasiado poco.
NO publiques basura para llenar el canal, pero tampoco descartes una noticia útil
solo porque no provenga directamente de Rockstar Games.

CRITERIO PRINCIPAL
PUBLICA si el artículo aporta al menos UN dato concreto y comprobable que sea
nuevo para el historial: una declaración atribuida a una persona identificable,
un cambio confirmado, una fecha, una cifra, una decisión empresarial, información
de desarrollo, casting, tecnología, lanzamiento, plataformas, características,
producción, marketing, clasificación, distribución u otro hecho relevante.

FUENTES
- Una fuente secundaria puede PUBLICARSE si aporta información concreta y verificable.
- Una declaración de un desarrollador, actor, ejecutivo o fuente identificable puede
  ser noticia aunque no sea un comunicado oficial de Rockstar.
- Un informe periodístico puede publicarse si explica claramente de dónde sale el dato.
- NO exijas una fuente oficial cuando el artículo tiene evidencia periodística sólida.

DESCARTA cuando:
- Es rumor, filtración o especulación sin respaldo suficiente.
- Es opinión personal, reacción o review sin información nueva.
- Es un artículo recopilatorio que solo repite información conocida.
- Es SEO/FAQ tipo "todo lo que sabemos" sin novedad real.
- Es una lista de detalles del Extended Look/tráiler ya vistos.
- Es una predicción financiera o de ventas sin datos primarios sólidos.
- El titular promete una afirmación que el contenido no demuestra.
- La información es demasiado corta o ambigua para comprobarla.
- Solo repite una noticia ya cubierta por el historial.

REGLA DE NOVEDAD
No compares solamente titulares. Compara el HECHO central de la noticia con el
historial. Dos titulares diferentes que hablan del mismo anuncio deben tratarse
como la misma noticia.

REGLA DE INCERTIDUMBRE
Si una afirmación es presentada como rumor o posibilidad, no la conviertas en un
hecho. Si no puedes determinar si es cierta, DESCARTA.

REGLA DE REDACCIÓN
Si PUBLICAR:
- Escribe en español natural.
- Título claro, atractivo y preciso; no uses clickbait engañoso.
- Contenido de aproximadamente 550-950 caracteres.
- Explica primero qué ocurrió y después por qué importa.
- Atribuye las declaraciones: "X dijo...", "según X...".
- Si la información no es oficial, deja claro su carácter periodístico.
- No inventes nombres, cifras, fechas, citas ni contexto.
- No pongas la URL dentro de content.

Si DESCARTAR:
- content debe ser una cadena vacía.
- reason debe explicar brevemente por qué no alcanza el estándar.

HISTORIAL DE PUBLICACIONES:
{previous_text}

ARTÍCULO CANDIDATO:
TÍTULO: {title}
FUENTE: {source_url}

CONTENIDO:
{source_content}

RESPONDE ÚNICAMENTE CON JSON VÁLIDO, SIN MARKDOWN:
{{
  "decision": "PUBLICAR" o "DESCARTAR",
  "reason": "motivo breve",
  "category": "Noticias" o "Análisis",
  "title": "título en español",
  "content": "texto final"
}}
"""


def ask_gemini(title, source_url, source_content, history):
    prompt = build_editorial_prompt(
        title,
        source_url,
        source_content,
        history
    )

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 1600,
            "responseMimeType": "application/json"
        }
    }

    last_error = None

    for model in GEMINI_MODELS:
        endpoint = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{model}:generateContent"
            f"?key={GEMINI_KEY}"
        )

        print("Intentando:", model)

        for attempt in range(3):
            try:
                response = requests.post(
                    endpoint,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=GEMINI_TIMEOUT
                )

                print("Gemini HTTP:", response.status_code)

                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        raise RuntimeError("Gemini no devolvió candidates.")

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        raise RuntimeError("Gemini no devolvió parts.")

                    text = parts[0].get("text", "").strip()
                    if not text:
                        raise RuntimeError("Gemini devolvió texto vacío.")

                    result = extract_json(text)
                    if not isinstance(result, dict):
                        raise RuntimeError("Gemini no devolvió un objeto JSON.")
                    return result

                last_error = response.text[:2000]

                if response.status_code in (429, 500, 502, 503, 504):
                    wait = min(12, 3 * (attempt + 1))
                    print(f"Reintentando en {wait}s...")
                    time.sleep(wait)
                    continue

                break

            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"JSON inválido: {exc}"
                print("Respuesta JSON inválida:", exc)
                # Reintentar una vez en el mismo modelo.
                time.sleep(2)

            except Exception as exc:
                last_error = str(exc)
                print("Error Gemini:", exc)
                time.sleep(3 * (attempt + 1))

    print("ERROR: ningún modelo de Gemini respondió correctamente.")
    print(last_error or "")
    return None


def send_discord(message):
    payload = {
        "content": message,
        "username": "Cal Bot",
        "allowed_mentions": {"roles": [ROLE_ID]}
    }

    try:
        response = requests.post(
            WEBHOOK,
            json=payload,
            timeout=30
        )
    except Exception as exc:
        print("ERROR DE CONEXIÓN CON DISCORD:", exc)
        return False

    print("Discord HTTP:", response.status_code)

    if response.status_code not in (200, 204):
        print("Respuesta Discord:", response.text[:3000])
        return False

    return True


def get_candidates(feed, history):
    now = datetime.now(timezone.utc)
    candidates = []

    for entry in feed.entries[:40]:
        title = entry.get("title", "").strip()
        google_url = entry.get("link", "").strip()

        if not title or not google_url:
            continue

        if title_should_skip(title):
            print("Ignorada por filtro local:", title)
            continue

        published_time = None
        parsed_time = getattr(entry, "published_parsed", None)

        if parsed_time:
            try:
                published_time = datetime.fromtimestamp(
                    calendar.timegm(parsed_time),
                    timezone.utc
                )
            except Exception:
                pass

        if published_time and now - published_time > timedelta(
            hours=MAX_NEWS_AGE_HOURS
        ):
            print("Ignorada: demasiado antigua.")
            continue

        normalized_url = canonical_url(google_url)
        item_id = stable_id(title, normalized_url)

        if item_id in history["published"]:
            print("Ignorada: URL/artículo ya procesado.")
            continue

        if published_title_duplicate(title, history):
            print("Ignorada: título demasiado parecido.")
            continue

        rss_content = clean_html(entry.get("summary", ""))
        description = clean_html(entry.get("description", ""))
        rss_content = (rss_content + "\n" + description).strip()

        candidates.append({
            "id": item_id,
            "title": title,
            "google_url": google_url,
            "rss_content": rss_content,
        })

    return candidates


def normalize_editor_result(result):
    if not isinstance(result, dict):
        return None

    decision = str(result.get("decision", "DESCARTAR")).upper().strip()
    reason = str(result.get("reason", "")).strip()
    category = str(result.get("category", "Noticias")).strip()
    title = str(result.get("title", "")).strip()
    content = str(result.get("content", "")).strip()

    if decision not in ("PUBLICAR", "DESCARTAR"):
        decision = "DESCARTAR"

    if category not in ("Noticias", "Análisis"):
        category = "Noticias"

    return {
        "decision": decision,
        "reason": reason,
        "category": category,
        "title": title,
        "content": content,
    }


def build_discord_message(category, title, content, source_url):
    date_text = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    return (
        f"<@&{ROLE_ID}>\n\n"
        f"🧭 **{category}**\n\n"
        f"# {title}\n\n"
        f"{content}\n\n"
        f"🔗 **Fuente original:** {source_url}\n\n"
        f"⚠️ **REVISIÓN REQUERIDA**\n"
        f"Este contenido fue preparado por Cal Bot como borrador editorial. "
        f"Revisa la información antes de publicarlo en #noticias.\n\n"
        f"-# Cal Bot · {date_text}"
    )


def main():
    print("=" * 64)
    print("CAL BOT V18 DEFINITIVO")
    print("EDITOR DE NOTICIAS DE CAL FAMILY")
    print("=" * 64)

    if not WEBHOOK:
        print("ERROR: falta el secret NEWS_DRAFT_WEBHOOK.")
        raise SystemExit(1)

    if not GEMINI_KEY:
        print("ERROR: falta el secret GEMINI_API_KEY.")
        raise SystemExit(1)

    history = load_history()
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        print("No se encontraron noticias.")
        return

    print("Buscando noticias nuevas...")
    candidates = get_candidates(feed, history)

    print("=" * 64)
    print(f"CANDIDATAS ENCONTRADAS: {len(candidates)}")
    print("=" * 64)

    if not candidates:
        print("Ninguna noticia nueva pasó los filtros iniciales.")
        return

    evaluated = 0

    # IMPORTANTE: si la primera noticia es mala, no termina el programa.
    # Continúa con las siguientes candidatas hasta encontrar una publicable.
    for candidate in candidates[:MAX_CANDIDATES_TO_EVALUATE]:
        evaluated += 1

        print("=" * 64)
        print(f"EVALUANDO CANDIDATA {evaluated}/{min(len(candidates), MAX_CANDIDATES_TO_EVALUATE)}")
        print(candidate["title"])
        print(candidate["google_url"])
        print("=" * 64)

        source_content, final_source_url = fetch_article(
            candidate["google_url"],
            candidate["rss_content"]
        )

        if not source_content:
            print("Descartada: no hay información suficiente.")
            continue

        source_hash = content_hash(source_content)

        if source_hash in history["content_hashes"]:
            print("Descartada: contenido idéntico al historial.")
            continue

        print("=" * 64)
        print("CAL BOT ESTÁ EVALUANDO...")
        print("=" * 64)

        result = ask_gemini(
            candidate["title"],
            final_source_url,
            source_content,
            history
        )

        if result is None:
            print("No se pudo evaluar esta candidata. Continuando...")
            continue

        result = normalize_editor_result(result)

        if result["decision"] != "PUBLICAR":
            print("=" * 64)
            print("DESCARTADA POR CAL BOT")
            print("=" * 64)
            print(result["reason"] or "No cumple el estándar editorial.")
            continue

        ai_title = result["title"]
        ai_content = result["content"]
        category = result["category"]

        if not ai_title or not ai_content:
            print("Descartada: respuesta editorial incompleta.")
            continue

        if published_title_duplicate(ai_title, history, 0.86):
            print("Descartada: el título final coincide con historial.")
            continue

        # Evita que Gemini añada la fuente dentro del cuerpo.
        ai_content = re.sub(
            r"🔗\s*\*\*Fuente original:\*\*.*?(?=\n|$)",
            "",
            ai_content,
            flags=re.I
        ).strip()

        final_message = build_discord_message(
            category,
            ai_title,
            ai_content,
            final_source_url
        )

        if len(final_message) > 1950:
            print("Descartada: mensaje demasiado largo para Discord.")
            continue

        print("=" * 64)
        print("NOTICIA ACEPTADA POR CAL BOT")
        print("=" * 64)
        print("Título:", ai_title)
        print("Categoría:", category)
        print("Enviando a Discord...")

        if not send_discord(final_message):
            print("La noticia NO se guardará como publicada.")
            raise SystemExit(1)

        # Solo guardar después de confirmar el envío.
        history["published"].append(candidate["id"])
        history["titles"].append(ai_title)
        history["content_hashes"].append(source_hash)
        history["source_urls"].append(final_source_url)
        save_history(history)

        print("=" * 64)
        print("DISCORD CONFIRMÓ.")
        print("NOTICIA PUBLICADA Y GUARDADA EN HISTORIAL.")
        print("=" * 64)
        return

    print("=" * 64)
    print("NINGUNA NOTICIA CUMPLIÓ LOS CRITERIOS.")
    print("El bot revisó las candidatas disponibles sin publicar basura.")
    print("=" * 64)


if __name__ == "__main__":
    main()
