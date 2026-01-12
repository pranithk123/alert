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
PORT = int(os.getenv("PORT", "8080")) 

@dataclass
class Snapshot:
    ts: float
    product_count: int
    first_products: List[str]

def start_health_server():
    """
    Runs in the main thread to prevent Railway from stopping the container.
    Provides a robust health endpoint and keep-alive logging.
    """
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Log the probe to show activity in Railway logs
            print("Railway Health Probe received", flush=True) 
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return # Silence standard library logging to keep clean

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ Health server active on port {PORT}. Container will remain persistent.", flush=True)
    server.serve_forever()

def telegram_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[WARN] Telegram credentials missing. Printing locally:\n{text}", flush=True)
        return
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": False}
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
    # Set the path for Playwright browsers inside the container
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/pw-browsers"
    
    with sync_playwright() as p:
        # Optimized arguments to prevent RAM spikes and OOM crashes
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--js-flags='--max-old-space-size=256'"
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
        )
        page = context.new_page()
        
        print(f"Scraping {URL}...", flush=True)
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            products = []
            seen = set()
            selectors = ["a[href*='/p/']", "a[href*='-p-']", ".product-card a"]

            for sel in selectors:
                anchors = page.locator(sel)
                for i in range(min(anchors.count(), 15)):
                    a = anchors.nth(i)
                    href = a.get_attribute("href") or ""
                    if not href or "javascript:" in href.lower(): continue
                    if href.startswith("/"): href = "https://www.sheinindia.in" + href
                    
                    text = (a.inner_text() or "").strip()
                    if href in seen: continue
                    seen.add(href)
                    products.append({"title": text[:50], "href": href, "price": ""})
                if len(products) > 5: break

            sigs = [normalize_product_signature(x) for x in products[:10]]
            return Snapshot(ts=time.time(), product_count=len(products), first_products=sigs)
        finally:
            browser.close()

def main_loop():
    """Scraper logic running in a daemon background thread."""
    print("🚀 Watcher background thread started...", flush=True)
    telegram_send("✅ SHEIN watcher started.\n" + URL)
    
    while True:
        try:
            prev = load_state()
            curr = scrape_snapshot()
            
            # Simple alert logic based on count or signature changes
            if prev and (curr.product_count > int(prev.get("product_count", 0)) or 
                         curr.first_products != prev.get("first_products", [])):
                msg = f"🚨 SHEIN RESTOCK DETECTED!\nItems: {curr.product_count}\nLink: {URL}"
                telegram_send(msg)
            
            save_state(curr)
            print(f"Check complete. Found {curr.product_count} items.", flush=True)
        except Exception as e:
            print(f"Scraper Error: {e}", flush=True)
        
        # Jittered sleep to reduce predictability
        time.sleep(random.randint(60, 120))

if __name__ == "__main__":
    # 1. Start the Scraper in the background
    threading.Thread(target=main_loop, daemon=True).start()
    
    # 2. Run the Health Server in the main thread (Blocking)
    # This prevents the main process from exiting, keeping the container ALIVE.
    try:
        start_health_server()
    except Exception as e:
        print(f"CRITICAL: Health Server failed: {e}", flush=True)
        time.sleep(30)