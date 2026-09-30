"""Startar certifikatgeneratorn lokalt och öppnar den i webbläsaren.

Servern lyssnar bara på 127.0.0.1, så appen nås enbart från den här datorn.
Mallar och intyg sparas i DATA_DIR (standard: mappen data/ bredvid appen).
Stäng fönstret (eller tryck Ctrl+C) för att avsluta.
"""
import os
import sys
import threading
import urllib.request
import webbrowser

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
URL = f"http://{HOST}:{PORT}/"


def already_running():
    try:
        with urllib.request.urlopen(URL, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def main():
    if already_running():
        print(f"Certifikatgeneratorn körs redan. Öppnar {URL}")
        webbrowser.open(URL)
        return

    from waitress import serve

    from app import app

    print("Certifikatgeneratorn körs på", URL)
    print("Sparade mallar och intyg:", app.config["DATA_DIR"])
    print("Stäng det här fönstret för att avsluta.")
    threading.Timer(1.0, webbrowser.open, args=(URL,)).start()
    try:
        serve(app, host=HOST, port=PORT, threads=4)
    except OSError as exc:
        print(f"Kunde inte starta på port {PORT}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
