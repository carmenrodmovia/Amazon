#!/usr/bin/env python3
"""
Amazon scraper + Telegram notifier
Features:
- Multiple search terms (TAGS env var, CSV)
- Detect new products & price drops (persisted in data.json)
- Send product messages to Telegram, including product image via sendPhoto (by URL)
- Save logs to logs/YYYY-MM-DD.txt
- Daily summary of found new items & price drops at SUMMARY_HOUR (env var)
- Rotating headers and request retries
- Runs on Render with a small Flask webserver to keep the dyno alive
"""

import requests
from bs4 import BeautifulSoup
import time
import os
import threading
import json
import random
from datetime import datetime, date
from flask import Flask
from urllib.parse import urlencode, quote_plus
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import traceback

# -----------------------
# Configuration (ENV)
# -----------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TAGS_ENV = os.getenv("TAGS", "decoración navidad")  # CSV of search terms
PAGES_PER_TAG = int(os.getenv("PAGES_PER_TAG", "3"))
SUMMARY_HOUR = int(os.getenv("SUMMARY_HOUR", "20"))  # 24h hour to send daily summary
DATA_FILE = os.getenv("DATA_FILE", "data.json")
DAILY_FILE = os.getenv("DAILY_FILE", "daily.json")
LOG_DIR = os.getenv("LOG_DIR", "logs")
PORT = int(os.getenv("PORT", "10000"))

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("❌ ERROR: TELEGRAM_TOKEN and CHAT_ID environment variables are required.")
    # we continue but send_telegram will fail gracefully

# Ensure directories
os.makedirs(LOG_DIR, exist_ok=True)

# -----------------------
# Logging helper
# -----------------------
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

# -----------------------
# HTTP session with retries
# -----------------------
session = requests.Session()
retries = Retry(total=5, backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(['GET','POST']))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# -----------------------
# Flask webserver (keepalive for Render)
# -----------------------
app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Amazon Bot running successfully on Render."

def start_web():
    log("🌐 Starting Flask web server...")
    app_web.run(host="0.0.0.0", port=PORT)

# -----------------------
# Headers rotation
# -----------------------
def get_headers():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.amazon.es/"
    }

# -----------------------
# Telegram helpers
# -----------------------
def send_telegram_text(text: str):
    """Send a text message (HTML mode). Returns True if ok."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("❌ Telegram not configured; skipping send.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        r = session.post(url, json=payload, timeout=15)
        log(f"Telegram sendMessage status: {r.status_code}")
        try:
            data = r.json()
            log(f"Telegram response: {json.dumps(data, ensure_ascii=False)}")
        except:
            log("⚠️ Telegram response not JSON.")
        return r.status_code == 200
    except Exception as e:
        log(f"❌ Exception sending Telegram message: {e}")
        return False

def send_telegram_photo_by_url(photo_url: str, caption: str):
    """Send a photo by URL (Telegram will fetch it). Returns True if ok."""
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

# -----------------------
# Persistence helpers
# -----------------------
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

# -----------------------
# Amazon scraping helpers
# -----------------------
def obtener_urls_busqueda(termino, paginas=3):
    # Amazon expects term URL-encoded
    q = quote_plus(termino)
    return [f"https://www.amazon.es/s?k={q}&page={pagina}" for pagina in range(1, paginas+1)]

def extraer_asin(div):
    asin = div.get("data-asin")
    if asin and len(asin) > 5:
        return asin
    # fallback: look for data-asin in children attributes
    for attr in ("data-asin","data-asin-bfk"):
        if div.get(attr):
            return div.get(attr)
    return None

def parse_price(price_text):
    # price_text example: "29,99 €" or "1.299,00 €"
    try:
        # Remove currency symbols and whitespace
        txt = price_text.replace("€", "").replace("\u20ac", "").strip()
        # Replace dots thousands and comma decimal
        txt = txt.replace(".", "").replace(",", ".")
        # Keep only digits and dot
        filtered = "".join(ch for ch in txt if (ch.isdigit() or ch == "."))
        if filtered == "":
            return None
        return float(filtered)
    except Exception:
        return None

def analizar_pagina(url):
    log(f"🔎 Analizando {url}")
    headers = get_headers()
    log(f"➡ Headers: {headers['User-Agent']}")
    try:
        r = session.get(url, headers=headers, timeout=15)
    except Exception as e:
        log(f"❌ Error GET {url}: {e}")
        return []
    log(f"➡ Status {r.status_code}")
    # print first part for diagnostics
    snippet = (r.text or "")[:1200]
    log("➡ HTML snippet:\n" + snippet.replace("\n", " ")[:800] + ("..." if len(snippet) > 800 else ""))
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    productos_divs = soup.select("div.s-result-item")
    resultados = []
    for div in productos_divs:
        try:
            asin = extraer_asin(div)
            if not asin:
                continue
            # title
            h2 = div.select_one("h2")
            titulo = h2.get_text(strip=True) if h2 else None
            # price (span.a-offscreen is common)
            sp = div.select_one("span.a-offscreen")
            precio = parse_price(sp.get_text(strip=True)) if sp else None
            # link
            a = div.select_one("a.a-link-normal[href]")
            link = "https://www.amazon.es" + a["href"] if a and a.get("href") else None
            # image
            img = div.select_one("img")
            img_url = None
            if img:
                img_url = img.get("src") or img.get("data-src") or img.get("data-image-lazy-src")
            # only keep if title and link; price may be None (we skip those without price for offers)
            if titulo and link:
                resultados.append({
                    "asin": asin,
                    "titulo": titulo,
                    "precio": precio,
                    "link": link,
                    "image": img_url
                })
        except Exception as e:
            log(f"⚠️ Error extrayendo producto: {e}\n{traceback.format_exc()}")
    log(f"➡ Productos parseados: {len(resultados)}")
    return resultados

# -----------------------
# Daily summary helpers
# -----------------------
def append_daily_change(change_obj):
    """
    change_obj example:
    { "type": "new" or "drop", "asin": "...", "titulo": "...", "old": 29.9, "new": 19.9, "link": "...", "time": "2025-12-11 12:34:00" }
    """
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
    # clear today's after fetching
    if today in daily:
        daily[today] = []
        guardar_json(DAILY_FILE, daily)
    return changes

# -----------------------
# Main process
# -----------------------
def process_once(history, tags):
    any_changes = False
    for tag in tags:
        urls = obtener_urls_busqueda(tag, paginas=PAGES_PER_TAG)
        for url in urls:
            productos = analizar_pagina(url)
            for prod in productos:
                asin = prod["asin"]
                titulo = prod["titulo"]
                precio = prod["precio"]
                link = prod["link"]
                image = prod.get("image")

                # Only act when price is known. If price is None skip (can't compare).
                if precio is None:
                    continue

                prev = history.get(asin)
                # New product
                if prev is None:
                    msg = (f"🆕 <b>NUEVO PRODUCTO</b>\n"
                           f"🛒 <b>{titulo}</b>\n"
                           f"💶 Precio: {precio}€\n"
                           f"🔗 {link}")
                    # Try to send photo first (if available)
                    sent = False
                    if image:
                        sent = send_telegram_photo_by_url(image, msg)
                        if not sent:
                            sent = send_telegram_text(msg)
                    else:
                        sent = send_telegram_text(msg)
                    log(f"Sent new product {asin}: {sent}")
                    history[asin] = {"titulo": titulo, "precio": precio, "link": link, "last_seen": datetime.now().isoformat()}
                    append_daily_change({"type": "new", "asin": asin, "titulo": titulo, "new": precio, "link": link, "time": datetime.now().isoformat()})
                    any_changes = True
                else:
                    # Price drop
                    prev_price = prev.get("precio") if prev.get("precio") is not None else prev.get("price") or None
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
                        log(f"Sent price drop {asin}: {sent}")
                        # update history
                        history[asin]["precio"] = precio
                        history[asin]["last_seen"] = datetime.now().isoformat()
                        append_daily_change({"type": "drop", "asin": asin, "titulo": titulo, "old": prev_price, "new": precio, "link": link, "time": datetime.now().isoformat()})
                        any_changes = True
                    else:
                        # update last seen timestamp (no change)
                        history[asin]["last_seen"] = datetime.now().isoformat()
            # polite wait between pages
            time.sleep(random.uniform(4, 8))
    return any_changes

def send_daily_summary_if_time():
    """Check if current hour == SUMMARY_HOUR and send summary once per day."""
    now = datetime.now()
    if now.hour != SUMMARY_HOUR:
        return
    # use a local sentinel file to ensure we send only once per day
    sentinel = os.path.join(LOG_DIR, f"summary_sent_{date.today().isoformat()}.flag")
    if os.path.exists(sentinel):
        return
    changes = get_and_clear_today_changes()
    if not changes:
        log("No hay cambios para el resumen diario.")
        # create sentinel anyway to avoid repeated checks this hour
        open(sentinel, "w").close()
        return
    # Build summary message (limit to first 30 items to avoid huge message)
    lines = [f"📝 <b>Resumen diario - {date.today().isoformat()}</b>", ""]
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

# -----------------------
# Main loop
# -----------------------
def main_loop():
    log("🔔 Bot iniciado.")
    # initial send
    send_telegram_text("🟢 Bot Amazon ejecutado en Render (modo ofertas activado).")

    history = cargar_json(DATA_FILE, {})
    tags = [t.strip() for t in TAGS_ENV.split(",") if t.strip()]
    if not tags:
        tags = ["decoración navidad"]

    # Main continuous loop
    while True:
        try:
            log(f"Comenzando ciclo para tags: {tags}")
            changes = process_once(history, tags)
            # Save history after processing
            guardar_json(DATA_FILE, history)
            # Send daily summary if it's the configured hour (and not yet sent today)
            send_daily_summary_if_time()
            # If there were no changes, log that
            if not changes:
                log("No se detectaron nuevos productos ni bajadas en este ciclo.")
        except Exception as e:
            log(f"❌ Error en main loop: {e}\n{traceback.format_exc()}")
        # Wait before next cycle (10 minutes default, jitter added)
        sleep_seconds = 600 + random.uniform(-60, 60)
        log(f"Durmiendo {int(sleep_seconds)} segundos antes del siguiente ciclo...")
        time.sleep(sleep_seconds)

# -----------------------
# Entrypoint
# -----------------------
if __name__ == "__main__":
    # Start webserver thread for Render
    threading.Thread(target=start_web, daemon=True).start()
    # Start main loop
    main_loop()
