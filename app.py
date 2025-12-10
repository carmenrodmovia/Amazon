#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import random
import time
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import json
import traceback
import re



# ---------------- CONFIG ----------------
#TELEGRAM_TOKEN = "7711722254:AAFV4bj2aQtbVKpa1gkMUyqlhkCzytRoubg"
TELEGRAM_TOKEN  = "7711722254:AAFAscovZ44PJpbYuJHKVgFevSNy-himSc4"
#ofertas prime
#CHAT_ID = "-1002428790704"
#ofertas colegio
CHAT_ID = "-1003179746715"
TAG = "crt06f-21"

EXCEL_FILE = "productos.xlsx"
LOG_FILE = "log.txt"

ENVIADOS_DIR = "enviados"
HISTORIAL_FILE = "enviados_historial.json"
NO_REPEAT_DAYS = 15

PALABRAS_CLAVE = [
    "Hogar",
    "ropa",
    "juguetes",
    "juegos",
    "bebé",
    "deporte"
]

HEADERS_ROTATIVOS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0"},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15"},
]

MIN_DESCUENTO_PCT = 10
BLACK_FRIDAY_PCT = 30

# ----------------- UTILIDADES -----------------
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
    if not os.path.exists(ENVIADOS_DIR):
        os.makedirs(ENVIADOS_DIR, exist_ok=True)

def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def guardar_historial(hist):
    try:
        with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Error guardando historial: {e}")

def fue_enviado_recientemente(asin, historial):
    if asin not in historial:
        return False
    try:
        fecha_envio = datetime.fromisoformat(historial[asin])
        return datetime.now() - fecha_envio < timedelta(days=NO_REPEAT_DAYS)
    except:
        return False

def registrar_envio(asin, historial):
    historial[asin] = datetime.now().isoformat()
    guardar_historial(historial)

# ----------------- ASIN & URL -----------------
def extract_asin(url):
    try:
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        if m: return m.group(1)
        m = re.search(r"/gp/product/([A-Z0-9]{10})", url)
        if m: return m.group(1)
        m = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", url)
        if m: return m.group(1)
    except:
        return None
    return None

def crear_url_afiliado(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&linkCode=ogi&th=1&psc=1"

def crear_url_scrape(asin):
    return f"https://www.amazon.es/dp/{asin}"

# ----------------- HTTP -----------------
def scraperapi_get(url):
    headers = random.choice(HEADERS_ROTATIVOS)
    try:
        time.sleep(random.uniform(1.5, 3.0))
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code == 200:
            return r.text
        if r.status_code in (403, 503):
            log(f"Amazon bloqueó ({r.status_code}). Reintentando con otro header...")
            time.sleep(random.uniform(2, 5))
            headers = random.choice(HEADERS_ROTATIVOS)
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                return r.text
        log(f"Error HTTP {r.status_code} para {url}")
        return None
    except Exception as e:
        log(f"Error GET {url}: {e}")
        return None

# ----------------- PARSERS -----------------
def parse_number_like_amazon(text):
    if not text:
        return None
    text = text.replace("\xa0", "").replace("\u202f", "").replace("€","").strip()
    text = text.replace(",", ".")
    try:
        return float(re.findall(r"[\d\.]+", text)[0])
    except:
        return None

def extraer_precios(soup):
    # Precio actual
    precio_actual_tag = soup.select_one(".aok-offscreen")
    precio_actual = parse_number_like_amazon(precio_actual_tag.get_text(strip=True)) if precio_actual_tag else None

    # Precio anterior
    precio_anterior_tag = soup.select_one(".a-price.a-text-price .a-offscreen")
    if precio_anterior_tag:
        precio_anterior = parse_number_like_amazon(precio_anterior_tag.get_text(strip=True))
    else:
        # fallback últimos 30 días
        precio_anterior_tag = soup.select_one(".a-price.a-text-price.srpPriceBlockAUI .a-offscreen")
        precio_anterior = parse_number_like_amazon(precio_anterior_tag.get_text(strip=True)) if precio_anterior_tag else None

    # Descuento
    descuento_tag = soup.select_one(".savingPriceOverride.aok-align-center.reinventPriceSavingsPercentageMargin.savingsPercentage")
    if descuento_tag:
        descuento = parse_number_like_amazon(descuento_tag.get_text(strip=True))
    elif precio_actual and precio_anterior:
        descuento = round((precio_anterior - precio_actual) / precio_anterior * 100)
    else:
        descuento = 0

    return precio_actual, precio_anterior, descuento

def formatear_precio_europeo(valor):
    if valor is None:
        return "No disponible"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"

# ----------------- BÚSQUEDA PRODUCTOS -----------------
def buscar_productos():
    keyword = random.choice(PALABRAS_CLAVE)
    pagina = random.randint(1, 3)
    log(f"🔎 Buscando '{keyword}' página {pagina}...")
    search_url = f"https://www.amazon.es/s?k={requests.utils.requote_uri(keyword)}&page={pagina}"
    html = scraperapi_get(search_url)
    if not html:
        log("Sin HTML de búsqueda")
        return []
    soup = BeautifulSoup(html, "html.parser")
    enlaces = soup.select("a.a-link-normal.s-no-hover.s-underline-text.s-underline-link-text, a.a-link-normal.s-no-outline, h2 a.a-link-normal")
    urls = set()
    for a in enlaces:
        href = a.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = "https://www.amazon.es" + href
        if "/dp/" in href or "/gp/product/" in href:
            asin = extract_asin(href)
            if asin:
                urls.add(crear_url_scrape(asin))
    urls = sorted(list(urls))
    log(f"URLs encontradas: {len(urls)}")
    return urls

# ----------------- INFO PRODUCTO -----------------
def get_product_info(url):
    asin = extract_asin(url)
    if not asin:
        return None
    html = scraperapi_get(url)
    
    ### FIX AQUÍ — indentación correcta ###
    with open("debug.html", "w", encoding="utf-8") as f:
        if html:
            f.write(html)
        else:
            f.write("SIN HTML")
    ### FIN DEL FIX ###

    

    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    titulo_tag = (soup.select_one("#productTitle")
                  or soup.select_one("span.a-size-large.product-title-word-break")
                  or soup.select_one("span.a-size-medium.a-color-base.a-text-normal")
                  or soup.select_one("h1 span"))
    titulo = titulo_tag.get_text(" ", strip=True) if titulo_tag else "Sin título"

    imagen_tag = (soup.select_one("#landingImage")
                  or soup.select_one("img#imgBlkFront")
                  or soup.select_one("img.s-image")
                  or soup.select_one("div#imgTagWrapperId img"))
    imagen = None
    if imagen_tag:
        imagen = imagen_tag.get("src") or imagen_tag.get("data-src")

    precio_actual, precio_anterior, descuento = extraer_precios(soup)
    if not precio_actual:
        return None
    if descuento < MIN_DESCUENTO_PCT:
        return None

    producto = {
        "asin": asin,
        "titulo": titulo,
        "imagen": imagen,
        "precio_actual": precio_actual,
        "precio_anterior": precio_anterior,
        "descuento": descuento,
        "url_scrape": url,
        "url": crear_url_afiliado(asin)
    }
    log(f"Producto OK: {asin} | -{descuento}% | {formatear_precio_europeo(precio_actual)} (antes {formatear_precio_europeo(precio_anterior)})")
    return producto

# ----------------- TELEGRAM -----------------
def enviar_telegram(producto):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("TOKEN o CHAT_ID no configurado. Saltando envío Telegram.")
        return
    try:
        bf_msg = "🔥🔥🔥 <b>BLACK FRIDAY</b> 🔥🔥🔥\n\n" if producto['descuento'] > BLACK_FRIDAY_PCT else ""
        caption = f"{bf_msg}<b>{producto['titulo']}</b>\n\n"
        caption += f"<b>💰 Precio:</b> {formatear_precio_europeo(producto['precio_actual'])}\n"
        if producto.get('precio_anterior'):
            caption += f"<b>📉 Precio recomendado:</b> {formatear_precio_europeo(producto['precio_anterior'])}\n"
        if producto.get('descuento'):
            caption += f"<b>🔥 -{producto['descuento']}% de descuento</b>\n\n"
        caption += f"{producto['url']}"  # solo link de afiliado

        img_resp = requests.get(producto['imagen'], timeout=20)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
        files = {"photo": ("image.jpg", img_bytes)}

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "disable_web_page_preview": "false"},
            files=files,
            timeout=30
        )

        if r.status_code == 200:
            log(f"Enviado Telegram: {producto['asin']}")
        else:
            log(f"Error Telegram {r.status_code}: {r.text}")

    except Exception as e:
        log(f"ERROR enviando Telegram {producto.get('asin','?')}: {e}")

# ----------------- GUARDADO -----------------
def deduplicar_y_guardar(productos):
    asin_map = {p["asin"]: p for p in productos}
    lista = list(asin_map.values())
    if not lista:
        return
    df = pd.DataFrame(lista)
    df['precio_actual'] = df['precio_actual'].apply(lambda x: formatear_precio_europeo(x))
    df['precio_anterior'] = df['precio_anterior'].apply(lambda x: formatear_precio_europeo(x))
    try:
        df.to_excel(EXCEL_FILE, index=False)
    except Exception as e:
        log(f"Error guardando Excel: {e}")

# ----------------- BUCLE PRINCIPAL -----------------
def main_loop():
    ensure_dirs()
    historial = cargar_historial()
    while True:
        try:
            urls = buscar_productos()
            if not urls:
                log("No se encontraron URLs. Reintentando pronto...")
                time.sleep(10)
                continue
            productos_encontrados = []
            for url in urls:
                p = get_product_info(url)
                if p and not fue_enviado_recientemente(p["asin"], historial):
                    enviar_telegram(p)
                    registrar_envio(p["asin"], historial)
                    productos_encontrados.append(p)
                    log("⏳ Esperando 15 minutos antes del siguiente envío...")
                    time.sleep(15 * 60)
            if productos_encontrados:
                deduplicar_y_guardar(productos_encontrados)
            log("⏳ Ciclo terminado. Esperando 15 minutos...\n")
            time.sleep(15 * 60)
        except KeyboardInterrupt:
            log("Interrupción por teclado")
            break
        except Exception as e:
            log(f"ERROR inesperado: {e}")
            log(traceback.format_exc())
            time.sleep(60)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("⚠️ Atención: TELEGRAM_TOKEN o CHAT_ID no configurado.")
    log("🚀 Sistema Amazon iniciado (precio extraído de aok-offscreen, descuento calculado).")
    main_loop()
