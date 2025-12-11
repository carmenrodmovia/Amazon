import requests
from bs4 import BeautifulSoup
import time
import os
import threading
from flask import Flask
import json
import random

###############################################################
# SERVIDOR WEB PARA MANTENER EL SERVICIO VIVO EN RENDER
###############################################################
app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Amazon Bot running successfully on Render."

def start_web():
    port = int(os.getenv("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)


###############################################################
# HEADERS ROTATIVOS - EVITA BLOQUEOS DE AMAZON
###############################################################
def get_headers():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ]

    return {
        "User-Agent": random.choice(user_agents),
        "Accept-Language": "es-ES,es;q=0.9"
    }


###############################################################
# ENVIAR MENSAJE A TELEGRAM
###############################################################
def send_telegram(message: str):
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")

    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ ERROR TELEGRAM: Falta TELEGRAM_TOKEN o CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ ERROR TELEGRAM: {e}")
        return False


###############################################################
# ARCHIVO DE HISTORIAL (NUEVOS / BAJADAS DE PRECIO)
###############################################################
DATA_FILE = "data.json"

def cargar_historial():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def guardar_historial(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


###############################################################
# SCRAPER AMAZON
###############################################################
def obtener_urls_busqueda(termino, paginas=3):
    return [
        f"https://www.amazon.es/s?k={termino}&page={pagina}"
        for pagina in range(1, paginas + 1)
    ]

def extraer_asin(div):
    """Busca el ASIN dentro del div del producto."""
    asin = div.get("data-asin")
    if asin and len(asin) > 5:
        return asin
    return None

def analizar_pagina(url):
    try:
        headers = get_headers()
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            print(f"⚠️ Amazon devolvió {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, "lxml")
        productos = soup.select("div.s-result-item")

        resultados = []

        for p in productos:
            asin = extraer_asin(p)
            if not asin:
                continue

            titulo = p.select_one("h2")
            precio = p.select_one("span.a-offscreen")
            enlace = p.select_one("a.a-link-normal")

            if titulo and precio and enlace:
                resultados.append({
                    "asin": asin,
                    "titulo": titulo.get_text(strip=True),
                    "precio": float(precio.get_text(strip=True).replace("€", "").replace(",", ".")),
                    "link": "https://www.amazon.es" + enlace["href"]
                })

        return resultados

    except Exception as e:
        print(f"❌ ERROR analizando página {url}: {e}")
        return []


###############################################################
# PROCESO PRINCIPAL: SOLO ENVIAR NUEVOS Y BAJADAS DE PRECIO
###############################################################
def main():
    send_telegram("🟢 Bot Amazon ejecutado en Render (modo ofertas activado).")

    historial = cargar_historial()
    termino_busqueda = os.getenv("TAG", "decoración navidad")
    urls = obtener_urls_busqueda(termino_busqueda, paginas=3)

    for url in urls:
        productos = analizar_pagina(url)

        for prod in productos:
            asin = prod["asin"]
            titulo = prod["titulo"]
            precio = prod["precio"]
            link = prod["link"]

            # Producto NUEVO
            if asin not in historial:
                mensaje = (
                    f"🆕 <b>NUEVO PRODUCTO</b>\n"
                    f"🛒 <b>{titulo}</b>\n"
                    f"💶 Precio: {precio}€\n"
                    f"🔗 {link}"
                )
                send_telegram(mensaje)

                historial[asin] = {
                    "titulo": titulo,
                    "precio": precio,
                    "link": link
                }
                continue

            # Bajada de precio
            precio_anterior = historial[asin]["precio"]
            if precio < precio_anterior:
                mensaje = (
                    f"📉 <b>BAJADA DE PRECIO</b>\n"
                    f"🛒 <b>{titulo}</b>\n"
                    f"⬇ Antes: {precio_anterior}€\n"
                    f"🟢 Ahora: {precio}€\n"
                    f"🔗 {link}"
                )
                send_telegram(mensaje)

                historial[asin]["precio"] = precio

        # Después de cada página, guardamos
        guardar_historial(historial)
        time.sleep(10)

    # Ciclo cada 10 minutos
    time.sleep(600)
    main()


###############################################################
# EJECUCIÓN PARA RENDER
###############################################################
if __name__ == "__main__":
    threading.Thread(target=start_web).start()
    main()
