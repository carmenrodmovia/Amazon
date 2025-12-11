#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import threading
import random
import requests
import pandas as pd
import json
import traceback
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from flask import Flask

# ============================================================
#                 CONFIG (variables de entorno)
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TAG = os.getenv("TAG", "crt06f-21")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("❌ ERROR: Falta TELEGRAM_TOKEN o CHAT_ID en las variables de entorno.")
    time.sleep(5)

EXCEL_FILE = "productos.xlsx"
DESCARTADOS_FILE = "descartados.json"
LOG_FILE = "log.txt"

ENVIADOS_DIR = "enviados"
HISTORIAL_FILE = "enviados_historial.json"
NO_REPEAT_DAYS = 15

PALABRAS_CLAVE = [
     "cuidado personal", "sudaderas", "decoración navidad", "cocina", "reloj", "salud"
]

HEADERS_ROTATIVOS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X)"},
]

MIN_DESCUENTO_PCT = 10

# ============================================================
#                        COLORES
# ============================================================
OK = "\033[92m"
WARN = "\033[93m"
ERR = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"

# ============================================================
#                        UTILIDADES
# ============================================================
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def ensure_dirs():
    os.makedirs(ENVIADOS_DIR, exist_ok=True)

def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        return json.load(open(HISTORIAL_FILE, "r", encoding="utf-8"))
    except:
        return {}

def guardar_historial(hist):
    json.dump(hist, open(HISTORIAL_FILE, "w", encoding="utf-8"), indent=4, ensure_ascii=False)

def fue_enviado_recientemente(asin, historial):
    if asin not in historial:
        return False
    try:
        fecha = datetime.fromisoformat(historial[asin])
        return datetime.now() - fecha < timedelta(days=NO_REPEAT_DAYS)
    except:
        return False

def registrar_envio(asin, historial):
    historial[asin] = datetime.now().isoformat()
    guardar_historial(historial)

def registrar_descartado(asin, motivo, precio, precio_ant, descuento):
    entry = {
        "fecha": datetime.now().isoformat(),
        "asin": asin,
        "motivo": motivo,
        "precio_actual": precio,
        "precio_recomendado": precio_ant,
        "descuento_pct": descuento
    }
    data = []
    if os.path.exists(DESCARTADOS_FILE):
        try:
            data = json.load(open(DESCARTADOS_FILE, "r", encoding="utf-8"))
        except:
            pass

    data.append(entry)
    json.dump(data, open(DESCARTADOS_FILE, "w", encoding="utf-8"), indent=4, ensure_ascii=False)

    log(f"{WARN}[DESCARTADO]{RESET} {asin} → {motivo}")


# ============================================================
#                     AMAZON SCRAPING
# ============================================================
def extract_asin(url):
    pats = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:[/?]|$)"
    ]
    for p in pats:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def crear_url_afiliado(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&psc=1"

def crear_url_scrape(asin):
    return f"https://www.amazon.es/dp/{asin}"

def scraperapi_get(url):
    headers = random.choice(HEADERS_ROTATIVOS)
    try:
        time.sleep(random.uniform(1.5, 3))
        r = requests.get(url, headers=headers, timeout=20)

        if r.status_code == 200:
            return r.text

        if r.status_code in (403, 503):
            log(f"{WARN}Amazon bloqueó ({r.status_code}). Reintentando...{RESET}")
            time.sleep(2)
            r = requests.get(url, headers=random.choice(HEADERS_ROTATIVOS), timeout=20)
            if r.status_code == 200:
                return r.text

        log(f"{ERR}HTTP {r.status_code} → {url}{RESET}")
        return None

    except Exception as e:
        log(f"{ERR}ERROR GET {url}: {e}{RESET}")
        return None


def parse_num(text):
    if not text:
        return None
    text = text.replace("€", "").replace(",", ".").replace("\xa0", "")
    try:
        n = re.findall(r"[0-9.]+", text)[0]
        return float(n)
    except:
        return None

def extraer_precio_cupon(soup):
    try:
        tag = soup.select_one(".ct-coupon-tile .ct-coupon-tile-price-content .a-offscreen")
        if tag:
            return parse_num(tag.get_text(strip=True))
    except:
        pass
    return None


def extraer_precios(soup):
    UNIDAD_PATTERN = re.compile(
        r"(\/\s?(kg|g|l|ml|cl|unidad|ud|100\s?g|100\s?ml|m))",
        re.IGNORECASE
    )

    def es_precio_unidad(t):
        return bool(t and UNIDAD_PATTERN.search(t))

    precio_actual = None
    for tag in soup.select(".a-price .a-offscreen"):
        txt = tag.get_text(strip=True)
        if not es_precio_unidad(txt):
            num = parse_num(txt)
            if num:
                precio_actual = num
                break

    precio_anterior = None
    for tag in soup.select(".a-price.a-text-price .a-offscreen"):
        txt = tag.get_text(strip=True)
        if not es_precio_unidad(txt):
            num = parse_num(txt)
            if num:
                precio_anterior = num
                break

    if precio_actual and precio_anterior:
        desc = round((precio_anterior - precio_actual) / precio_anterior * 100)
    else:
        desc = 0

    return precio_actual, precio_anterior, desc


def fmt(v):
    if v is None:
        return "ND"
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def buscar_productos():
    kw = random.choice(PALABRAS_CLAVE)
    pag = random.randint(1, 3)
    log(f"{CYAN}Buscando '{kw}' página {pag}...{RESET}")

    url = f"https://www.amazon.es/s?k={kw}&page={pag}"
    html = scraperapi_get(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    enlaces = soup.select("a.a-link-normal.s-no-hover, h2 a.a-link-normal")

    urls = []
    for a in enlaces:
        href = a.get("href", "")
        if "/dp/" in href:
            asin = extract_asin(href)
            if asin:
                urls.append(crear_url_scrape(asin))

    urls = list(set(urls))
    log(f"Encontradas {len(urls)} URLs")
    return urls


def get_product_info(url):
    asin = extract_asin(url)
    if not asin:
        return None

    html = scraperapi_get(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    t = soup.select_one("#productTitle")
    titulo = t.get_text(strip=True) if t else "Sin título"

    img = soup.select_one("#landingImage")
    imagen = img.get("src") if img else None

    precio, recomendado, desc = extraer_precios(soup)
    precio_cupon = extraer_precio_cupon(soup)
    tiene_cupon = False

    if precio_cupon:
        if precio and precio_cupon >= precio:
            registrar_descartado(asin, "Cupón falso (no reduce precio)", precio_cupon, recomendado, desc)
            return None

        precio = precio_cupon
        tiene_cupon = True
        if recomendado:
            desc = round((recomendado - precio) / recomendado * 100)

    if not precio or not recomendado:
        registrar_descartado(asin, "Precios no disponibles", precio, recomendado, desc)
        return None

    if precio >= recomendado:
        registrar_descartado(asin, "Precio >= recomendado", precio, recomendado, desc)
        return None

    if desc < MIN_DESCUENTO_PCT:
        registrar_descartado(asin, "Descuento insuficiente", precio, recomendado, desc)
        return None

    return {
        "asin": asin,
        "titulo": titulo,
        "imagen": imagen,
        "precio": precio,
        "tiene_cupon": tiene_cupon,
        "recomendado": recomendado,
        "descuento": desc,
        "url": crear_url_afiliado(asin)
    }


# ============================================================
#                       TELEGRAM
# ============================================================
def enviar_telegram(p):
    try:
        cap = f"<b>{p['titulo']}</b>\n"

        if p.get("tiene_cupon"):
            cap += "🔥 <b>CUPÓN ACTIVADO</b>\n"

        cap += f"\n<b>Precio:</b> {fmt(p['precio'])}\n"
        cap += f"<b>Recomendado:</b> {fmt(p['recomendado'])}\n"
        cap += f"<b>Descuento:</b> -{p['descuento']}%\n\n"
        cap += p["url"]

        img = requests.get(p["imagen"], timeout=15).content

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": cap, "parse_mode": "HTML"},
            files={"photo": ("img.jpg", img)}
        )

        if r.status_code == 200:
            log(f"{OK}Enviado Telegram → {p['asin']}{RESET}")
        else:
            log(f"{ERR}Error Telegram {r.status_code}: {r.text}{RESET}")

    except Exception as e:
        log(f"{ERR}ERROR Telegram: {e}{RESET}")


# ============================================================
#                    GUARDAR EXCEL
# ============================================================
def guardar_excel(productos):
    if not productos:
        return
    df = pd.DataFrame(productos)
    df["precio"] = df["precio"].apply(fmt)
    df["recomendado"] = df["recomendado"].apply(fmt)
    try:
        df.to_excel(EXCEL_FILE, index=False)
    except Exception as e:
        log(f"{ERR}Error guardando Excel: {e}{RESET}")


# ============================================================
#                    BUCLE PRINCIPAL
# ============================================================
def bot_loop():
    ensure_dirs()
    historial = cargar_historial()

    while True:
        try:
            urls = buscar_productos()
            productos = []

            for u in urls:
                p = get_product_info(u)
                if p and not fue_enviado_recientemente(p["asin"], historial):
                    enviar_telegram(p)
                    registrar_envio(p["asin"], historial)
                    productos.append(p)

                    log(f"{CYAN}⏳ Esperando 15 minutos...{RESET}")
                    time.sleep(900)

            guardar_excel(productos)
            log("✔ Ciclo completo. Esperando 15 minutos...\n")
            time.sleep(900)

        except Exception as e:
            log(f"{ERR}ERROR general: {e}{RESET}")
            log(traceback.format_exc())
            time.sleep(30)


# ============================================================
#               SERVIDOR WEB (mantiene activo en Render)
# ============================================================
app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Amazon Bot is running on Render."


def start_web():
    app_web.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


# ============================================================
#            LANZAR EL WEB SERVER + BOT EN PARALELO
# ============================================================
if __name__ == "__main__":
    threading.Thread(target=start_web).start()
    bot_loop()
