import json
import os
import random
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# Configuration
URL = "https://www.sheinindia.in/c/sverse-5939-37961"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STATE_FILE = "state.json"
# Matches the Railway Healthcheck path
PORT = int(os.getenv("PORT", "8080")) 

def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, format, *args):
            return 

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Health server listening on {PORT}", flush=True)
    server.serve_forever()

@dataclass
class Snapshot:
    ts: float
    product_count: int
    first_products: List[str]

def telegram_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials not set.", flush=True)
        return
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(endpoint, json=payload, timeout=15).raise_for_status()
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def load_state() -> Optional[Dict[str, Any]]:
    if not os.path.exists(STATE_FILE): return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return None

def save_state(snap: Snapshot) -> None:
    data = {"ts": snap.ts, "product_count": snap.product_count, "first_products": snap.first_products}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_product_signature(item: Dict[str, Any]) -> str:
    title = (item.get("title") or "").strip()
    href = (item.get("href") or "").strip()
    price = (item.get("price") or "").strip()
    return f"{title} | {price} | {href}"

def scrape_snapshot() -> Snapshot:
    # Forces Playwright to use the folder we created in the Dockerfile
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/pw-browsers"
    
    with sync_playwright() as p:
        # Args added to prevent crashes in low-RAM environments
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        products = []
        seen = set()
        # Common SHEIN selectors
        selectors = ["a[href*='/p/']", "a[href*='-p-']", ".product-card a"]

        for sel in selectors:
            anchors = page.locator(sel)
            for i in range(min(anchors.count(), 15)):
                a = anchors.nth(i)
                href = a.get_attribute("href") or ""
                if not href or "javascript" in href: continue
                if href.startswith("/"): href = "https://www.sheinindia.in" + href
                
                text = (a.inner_text() or "").strip()
                sig_key = href
                if sig_key in seen: continue
                seen.add(sig_key)
                products.append({"title": text[:50], "href": href, "price": ""})
            if len(products) > 5: break

        browser.close()
        sigs = [normalize_product_signature(x) for x in products[:10]]
        return Snapshot(ts=time.time(), product_count=len(products), first_products=sigs)

def should_alert(prev: Optional[Dict[str, Any]], curr: Snapshot) -> bool:
    if prev is None: return False
    if curr.product_count > int(prev.get("product_count", 0)): return True
    if curr.first_products != prev.get("first_products", []): return True
    return False

def main_loop():
    print("Watcher process started...", flush=True)
    telegram_send("✅ SHEIN watcher started.\n" + URL)
    while True:
        try:
            prev = load_state()
            curr = scrape_snapshot()
            if should_alert(prev, curr):
                msg = f"🚨 Change detected!\nItems: {curr.product_count}\n{URL}"
                telegram_send(msg)
            save_state(curr)
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(random.randint(40, 80))

if __name__ == "__main__":
    # 1. Start health server in a background thread
    threading.Thread(target=start_health_server, daemon=True).start()
    
    # 2. Keep the main thread alive FOREVER
    print("BOOT: Starting main watcher loop", flush=True)
    while True:
        try:
            main_loop() # This function has its own while True loop inside
        except Exception as e:
            print(f"CRITICAL: Main loop crashed with {e}. Restarting in 30s...", flush=True)
            time.sleep(30)