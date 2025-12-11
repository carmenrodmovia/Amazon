import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import os
import threading
from flask import Flask
import json

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
# FUNCIÓN MEJORADA PARA ENVIAR MENSAJES A TELEGRAM
###############################################################
def send_telegram(message: str):
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    TAG = "crt06f-21"

    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ ERROR TELEGRAM: Falta TELEGRAM_TOKEN o CHAT_ID en las variables de entorno.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        print("📤 Enviando mensaje a Telegram…")
        print(f"➡ URL: {url}")
        print(f"➡ PAYLOAD: {payload}")

        response = requests.post(url, json=payload, timeout=15)
        print(f"➡ STATUS CODE: {response.status_code}")

        try:
            data = response.json()
            print(f"➡ RESPUESTA TELEGRAM: {json.dumps(data, indent=2)}")
        except:
            print("⚠️ No se pudo interpretar JSON de Telegram.")

        if response.status_code != 200:
            print("❌ ERROR: Telegram devolvió un código no exitoso.")
            return False

        if not data.get("ok", False):
            print("❌ ERROR: Telegram respondió ok = false.")
            return False

        print("✅ Mensaje enviado a Telegram correctamente.")
        return True

    except Exception as e:
        print(f"❌ EXCEPCIÓN EN TELEGRAM: {e}")
        return False


###############################################################
# SCRAPER AMAZON (TU LÓGICA DE BÚSQUEDA)
###############################################################
def obtener_urls_busqueda(termino, paginas=3):
    urls = []
    for pagina in range(1, paginas + 1):
        url = f"https://www.amazon.es/s?k={termino}&page={pagina}"
        urls.append(url)
    return urls


def analizar_pagina(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "es-ES,es;q=0.9"
        }
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            print(f"⚠️ Amazon devolvió {r.status_code} en {url}")
            return []

        soup = BeautifulSoup(r.text, "lxml")
        productos = soup.select("div.s-result-item")

        resultados = []

        for p in productos:
            titulo = p.select_one("h2")
            precio = p.select_one("span.a-offscreen")
            enlace = p.select_one("a.a-link-normal")

            if titulo and precio and enlace:
                resultados.append({
                    "titulo": titulo.get_text(strip=True),
                    "precio": precio.get_text(strip=True),
                    "link": "https://www.amazon.es" + enlace["href"]
                })

        return resultados

    except Exception as e:
        print(f"❌ ERROR analizando página {url}: {e}")
        return []


###############################################################
# PROCESO PRINCIPAL DEL BOT
###############################################################
def main():
    print("🔎 Enviando mensaje de prueba a Telegram…")
    send_telegram("🟢 TEST: El bot de Amazon está funcionando en Render.")

    termino_busqueda = os.getenv("TAG", "decoración navidad")

    print(f"🔍 Buscando productos para: {termino_busqueda}")

    urls = obtener_urls_busqueda(termino_busqueda, paginas=3)

    for url in urls:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Analizando: {url}")

        productos = analizar_pagina(url)

        for prod in productos:
            mensaje = (
                f"🛒 <b>{prod['titulo']}</b>\n"
                f"💶 Precio: {prod['precio']}\n"
                f"🔗 {prod['link']}"
            )
            send_telegram(mensaje)

        time.sleep(10)

    print("🔁 Ciclo completado. Esperando 10 minutos…")
    time.sleep(600)
    main()  # bucle infinito


###############################################################
# EJECUCIÓN PARA RENDER
###############################################################
if __name__ == "__main__":
    # Servidor web en paralelo para Render
    threading.Thread(target=start_web).start()

    # Bot principal
    main()
