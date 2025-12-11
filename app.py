#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Amazon camouflaged scraper (Option B)
- Rotating, realistic headers (sec-ch-ua, sec-fetch-*, referer, etc.)
- Session with cookies, retries and backoff
- Random human-like delays
- CAPTCHA/robot detection and saving HTML to captcha/
- Persistence (data.json) for new products and price drops
- Telegram notifications (text + sendPhoto fallback)
- Daily summary and logs
"""
import os
import time
import json
import random
import threading
import traceback
from datetime import datetime, date
from urllib.parse import quote_plus
from flask import Flask

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# -------------------------
# Config via environment
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TAGS_ENV = os.getenv("TAGS", "decoración navidad")  # CSV
PAGES_PER_TAG = int(os.getenv("PAGES_PER_TAG", "3"))
SUMMARY_HOUR = int(os.getenv("SUMMARY_HOUR", "20"))  # 24h
DATA_FILE = os.getenv("DATA_FILE", "data.json")
DAILY_FILE = os.getenv("DAILY_FILE", "daily.json")
LOG_DIR = os.getenv("LOG_DIR", "logs")
CAPTCHA_DIR = os.getenv("CAPTCHA_DIR", "captcha")
PORT = int(os.getenv("PORT", "10000"))

# ensure necessary dirs
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(CAPTCHA_DIR, exist_ok=True)

# -------------------------
# Logging helper
# -------------------------
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = f"[{ts}] {msg}"
    print(out)
    try:
        logfile = os.path.join(LOG_DIR, f"{date.today().isoformat()}.txt")
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    except Exception as e:
        print(f"⚠️ No pude escribir log: {e}")

# -------------------------
# HTTP session with retries/backoff
# -------------------------
session = requests.Session()
retries = Retry(total=5, backoff_factor=1, status_forcelist=[429,500,502,503,504], allowed_methods=frozenset(['GET','POST']))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# -------------------------
# Flask keepalive for Render
# -------------------------
app_web = Flask(__name__)
@app_web.route("/")
def home():
    return "Amazon Bot running successfully on Render."

def start_web():
    log("🌐 Starting Flask web server...")
    app_web.run(host="0.0.0.0", port=PORT)

# -------------------------
# Advanced headers and variants
# -------------------------
# realistic UA list (desktop + mobile)
USER_AGENTS = [
    # Chrome desktop
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Firefox desktop
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    # Chrome mobile
    "Mozilla/5.0 (Linux; Android 13; Pixel 7a) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
    # iPhone Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

ACCEPT_LANGS = ["es-ES,es;q=0.9", "es-ES;q=0.9,es;q=0.8", "en-US,en;q=0.9,es;q=0.8"]

REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://www.duckduckgo.com/",
    "https://search.yahoo.com/",
    "https://www.google.es/",
    "https://www.amazon.es/"
]

SEC_CH_UA_OPTIONS = [
    '"Chromium";v="129", "Not A(Brand)";v="24", "Google Chrome";v="129"',
    '"Google Chrome";v="129", "Chromium";v="129", "Not A(Brand)";v="24"',
    '"Chromium";v="128", "Not A(Brand)";v="24", "Google Chrome";v="128"',
]

def build_headers():
    ua = random.choice(USER_AGENTS)
    sec_ch_ua = random.choice(SEC_CH_UA_OPTIONS)
    headers = {
        "User-Agent": ua,
        "Accept-Language": random.choice(ACCEPT_LANGS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": random.choice(REFERERS),
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        # emulated client hints (not all servers use them but add for realism)
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": "?0" if "Mobile" not in ua else "?1",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    }
    return headers

# -------------------------
# Telegram helpers
# -------------------------
def send_telegram_text(text: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("❌ Telegram not configured; skipping send.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        r = session.post(url, json=payload, timeout=15)
        log(f"Telegram sendMessage status: {r.status_code}")
        try:
            log(f"Telegram response: {r.json()}")
        except:
            pass
        return r.status_code == 200
    except Exception as e:
        log(f"❌ Exception sending Telegram text: {e}")
        return False

def send_telegram_photo_by_url(photo_url: str, caption: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("❌ Telegram not configured; skipping photo send.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    payload = {"chat_id": CHAT_ID, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
    try:
        r = session.post(url, json=payload, timeout=15)
        log(f"Telegram sendPhoto status: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        log(f"❌ Exception sending Telegram photo: {e}")
        return False

# -------------------------
# Persistence helpers
# -------------------------
def cargar_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ Error cargando {path}: {e}")
        return default

def guardar_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"⚠️ Error guardando {path}: {e}")

# -------------------------
# Utility: price parser
# -------------------------
def parse_price(price_text):
    if not price_text:
        return None
    try:
        txt = price_text.replace("\u20ac", "").replace("€", "").strip()
        txt = txt.replace(".", "").replace(",", ".")
        # filter digits and dot
        filtered = "".join(ch for ch in txt if (ch.isdigit() or ch == "."))
        if filtered == "":
            return None
        return float(filtered)
    except Exception:
        return None

# -------------------------
# Amazon page analysis with anti-block strategies
# -------------------------
def obtener_urls_busqueda(termino, paginas=3):
    q = quote_plus(termino)
    return [f"https://www.amazon.es/s?k={q}&page={p}" for p in range(1, paginas+1)]

def extract_asin(div):
    asin = div.get("data-asin")
    if asin and len(asin) > 5:
        return asin
    # look for links with /dp/ASIN
    a = div.select_one("a[href]")
    if a and a.get("href"):
        href = a.get("href")
        # common pattern /dp/B0... or /gp/product/B0...
        import re
        m = re.search(r"/(dp|gp/product)/([A-Z0-9]{8,12})", href)
        if m:
            return m.group(2)
    return None

def detect_captcha(html_text):
    if not html_text:
        return False
    low = html_text.lower()
    indicators = [
        "robot check", "captcha", "to discuss this problem please visit",
        "/errors/validatecaptcha", "type the characters you see in the image", "prove you're not a robot",
        "detected unusual traffic", "access to this page has been denied"
    ]
    for ind in indicators:
        if ind in low:
            return True
    return False

def save_captcha_html(html_text, url):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = url.replace("https://", "").replace("http://", "").replace("/", "_")[:120]
    path = os.path.join(CAPTCHA_DIR, f"captcha_{safe_name}_{ts}.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_text)
        log(f"CAPTCHA HTML salvado en {path}")
    except Exception as e:
        log(f"⚠️ No pude salvar captcha HTML: {e}")

def analyze_page(url, max_retries=3):
    """
    Try to fetch and parse a search page with anti-block measures.
    Returns list of product dicts: {asin, titulo, precio, link, image}
    """
    log(f"Analizando: {url}")
    attempt = 0
    last_html = ""
    while attempt < max_retries:
        attempt += 1
        headers = build_headers()
        try:
            r = session.get(url, headers=headers, timeout=20)
        except Exception as e:
            log(f"❌ Error GET {url}: {e} (attempt {attempt})")
            # backoff
            time.sleep(2 ** attempt + random.uniform(0.5, 1.5))
            continue

        status = r.status_code
        html = r.text or ""
        last_html = html[:2000]
        log(f"➡ Status {status} (attempt {attempt})")
        snippet = (html or "")[:1000].replace("\n", " ")
        log("➡ Snippet: " + snippet[:800] + ("..." if len(snippet) > 800 else ""))
        # detect captcha / robot
        if detect_captcha(html):
            log("⚠️ Detectado CAPTCHA/Robot check en la página.")
            save_captcha_html(html, url)
            # change session cookies and headers and retry
            session.cookies.clear()
            log("🧹 Cookies limpiadas; cambiando UA y reintentando...")
            time.sleep(random.uniform(3.0, 6.0))
            continue

        if status != 200:
            log(f"⚠️ Código {status} recibido; intentando reintentar con backoff.")
            time.sleep(2 ** attempt + random.uniform(1, 2))
            continue

        # Parse HTML
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception as e:
            log(f"⚠️ BeautifulSoup error: {e}")
            return []

        # Amazon uses div.s-result-item but also other structures; filter by ASIN
        product_divs = soup.select("div.s-result-item")
        resultados = []
        for div in product_divs:
            try:
                asin = extract_asin(div)
                if not asin:
                    continue
                # title
                h2 = div.select_one("h2")
                title = h2.get_text(strip=True) if h2 else None
                # price - Amazon often hides price in span.a-offscreen
                price_span = div.select_one("span.a-offscreen")
                price = parse_price(price_span.get_text(strip=True)) if price_span else None
                # link
                a = div.select_one("a.a-link-normal[href]")
                link = None
                if a and a.get("href"):
                    href = a.get("href")
                    if href.startswith("/"):
                        link = "https://www.amazon.es" + href
                    elif href.startswith("http"):
                        link = href
                # image: get src or data-src
                img = div.select_one("img")
                img_url = None
                if img:
                    img_url = img.get("data-src") or img.get("data-old-hires") or img.get("src") or img.get("data-a-dynamic-image")
                # keep entries with title and link
                if title and link:
                    resultados.append({"asin": asin, "titulo": title, "precio": price, "link": link, "image": img_url})
            except Exception as e:
                log(f"⚠️ Error parseando producto: {e}\n{traceback.format_exc()}")
                continue

        log(f"➡ Productos parseados: {len(resultados)}")
        # polite sleep after successful fetch
        time.sleep(random.uniform(1.5, 3.8))
        return resultados

    # exhausted retries
    log("❌ Exceso de reintentos; devolviendo vacío.")
    # save last html for debugging
    if last_html:
        save_captcha_html(last_html, url + "_lasthtml")
    return []

# -------------------------
# Daily summary helpers
# -------------------------
def append_daily_change(change_obj):
    daily = cargar_json(DAILY_FILE, {})
    today = date.today().isoformat()
    if today not in daily:
        daily[today] = []
    daily[today].append(change_obj)
    guardar_json(DAILY_FILE, daily)

def get_and_clear_today_changes():
    daily = cargar_json(DAILY_FILE, {})
    today = date.today().isoformat()
    changes = daily.get(today, [])
    if today in daily:
        daily[today] = []
        guardar_json(DAILY_FILE, daily)
    return changes

# -------------------------
# Small helpers for json funcs (use defined above)
# -------------------------
def cargar_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ Error cargando {path}: {e}")
        return default

def guardar_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"⚠️ Error guardando {path}: {e}")

# -------------------------
# Main process: new + price drop logic
# -------------------------
def process_once(history, tags):
    any_changes = False
    for tag in tags:
        tagsafe = tag.strip()
        if not tagsafe:
            continue
        urls = obtener_urls_busqueda(tagsafe, paginas=PAGES_PER_TAG)
        log(f"Buscando '{tagsafe}' en {len(urls)} páginas.")
        for url in urls:
            try:
                productos = analyze_page(url)
                for prod in productos:
                    asin = prod.get("asin")
                    titulo = prod.get("titulo")
                    precio = prod.get("precio")
                    link = prod.get("link")
                    image = prod.get("image")

                    if precio is None:
                        # skip items without price (can't compare)
                        continue

                    prev = history.get(asin)
                    # NEW
                    if prev is None:
                        msg = (f"🆕 <b>NUEVO PRODUCTO</b>\n"
                               f"🛒 <b>{titulo}</b>\n"
                               f"💶 Precio: {precio}€\n"
                               f"🔗 {link}")
                        sent = False
                        if image:
                            sent = send_telegram_photo_by_url(image, msg)
                            if not sent:
                                sent = send_telegram_text(msg)
                        else:
                            sent = send_telegram_text(msg)
                        log(f"Nuevo {asin} enviado: {sent}")
                        history[asin] = {"titulo": titulo, "precio": precio, "link": link, "last_seen": datetime.now().isoformat()}
                        append_daily_change({"type": "new", "asin": asin, "titulo": titulo, "new": precio, "link": link, "time": datetime.now().isoformat()})
                        any_changes = True
                    else:
                        prev_price = prev.get("precio") if prev.get("precio") is not None else prev.get("price")
                        if prev_price is not None and precio < prev_price:
                            msg = (f"📉 <b>BAJADA DE PRECIO</b>\n"
                                   f"🛒 <b>{titulo}</b>\n"
                                   f"⬇ Antes: {prev_price}€\n"
                                   f"🟢 Ahora: {precio}€\n"
                                   f"🔗 {link}")
                            sent = False
                            if image:
                                sent = send_telegram_photo_by_url(image, msg)
                                if not sent:
                                    sent = send_telegram_text(msg)
                            else:
                                sent = send_telegram_text(msg)
                            log(f"Bajada {asin} enviada: {sent}")
                            history[asin]["precio"] = precio
                            history[asin]["last_seen"] = datetime.now().isoformat()
                            append_daily_change({"type": "drop", "asin": asin, "titulo": titulo, "old": prev_price, "new": precio, "link": link, "time": datetime.now().isoformat()})
                            any_changes = True
                        else:
                            # update last seen only
                            history[asin]["last_seen"] = datetime.now().isoformat()

                # polite page wait
                time.sleep(random.uniform(3.5, 7.5))
            except Exception as e:
                log(f"⚠️ Error procesando URL {url}: {e}\n{traceback.format_exc()}")
                # short sleep on error
                time.sleep(random.uniform(2.0, 5.0))
    return any_changes

def send_daily_summary_if_time():
    now = datetime.now()
    if now.hour != SUMMARY_HOUR:
        return
    sentinel = os.path.join(LOG_DIR, f"summary_sent_{date.today().isoformat()}.flag")
    if os.path.exists(sentinel):
        return
    changes = get_and_clear_today_changes()
    if not changes:
        log("No hay cambios para el resumen diario.")
        open(sentinel, "w").close()
        return
    lines = [f"📝 <b>Resumen diario - {date.today().isoformat()}</b>\n"]
    for c in changes[:30]:
        if c["type"] == "new":
            lines.append(f"🆕 {c['titulo']}\n💶 {c['new']}€\n🔗 {c['link']}\n")
        else:
            lines.append(f"📉 {c['titulo']}\nAntes: {c.get('old')}€ → Ahora: {c.get('new')}€\n🔗 {c['link']}\n")
    if len(changes) > 30:
        lines.append(f"... y {len(changes)-30} más.")
    summary = "\n".join(lines)
    send_telegram_text(summary)
    log("Resumen diario enviado.")
    open(sentinel, "w").close()

# -------------------------
# Entrypoint loop
# -------------------------
def main_loop():
    log("🔔 Bot iniciado (camuflaje total).")
    send_telegram_text("🟢 Bot Amazon ejecutado en Render (modo camuflaje total).")
    history = cargar_json(DATA_FILE, {})
    tags = [t.strip() for t in TAGS_ENV.split(",") if t.strip()]
    if not tags:
        tags = ["decoración navidad"]

    while True:
        try:
            log(f"Comenzando ciclo: tags={tags}")
            changes = process_once(history, tags)
            guardar_json(DATA_FILE, history)
            send_daily_summary_if_time()
            if not changes:
                log("No se detectaron cambios en este ciclo.")
        except Exception as e:
            log(f"❌ Error en main loop: {e}\n{traceback.format_exc()}")
        # Wait before next cycle (10 minutes ± jitter)
        wait = 600 + random.uniform(-90, 90)
        log(f"Durmiendo {int(wait)} segundos...")
        time.sleep(wait)

if __name__ == "__main__":
    # Start keepalive webserver thread
    threading.Thread(target=start_web, daemon=True).start()
    # Run main loop
    main_loop()
