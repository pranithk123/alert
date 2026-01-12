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
# IMPORTANT: Ensure 'PORT' is set to 8080 in Railway Variables
PORT = int(os.getenv("PORT", "8080")) 

@dataclass
class Snapshot:
    ts: float
    product_count: int
    first_products: List[str]

def start_health_server():
    """Runs in the main thread to keep Railway happy."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, format, *args):
            return 

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ Health server active on port {PORT}", flush=True)
    server.serve_forever()

def telegram_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[WARN] Telegram credentials missing. Message: {text}", flush=True)
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
    return f"{title} | {href}"

def scrape_snapshot() -> Snapshot:
    # Explicitly set the path where Dockerfile installed the browser
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/pw-browsers"
    
    with sync_playwright() as p:
        # Optimized args to survive on Railway's low-RAM plans
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process"  # Consolidates memory usage
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
        )
        page = context.new_page()
        
        print(f"Scraping {URL}...", flush=True)
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
                if not href or "javascript" in href: continue
                if href.startswith("/"): href = "https://www.sheinindia.in" + href
                
                text = (a.inner_text() or "").strip()
                if href in seen: continue
                seen.add(href)
                products.append({"title": text[:50], "href": href})
            if len(products) > 5: break

        browser.close()
        sigs = [normalize_product_signature(x) for x in products[:10]]
        return Snapshot(ts=time.time(), product_count=len(products), first_products=sigs)

def main_loop():
    """Background thread logic."""
    print("🚀 Watcher background thread started...", flush=True)
    telegram_send("✅ SHEIN watcher started.\n" + URL)
    
    while True:
        try:
            prev = load_state()
            curr = scrape_snapshot()
            
            # Count check or signature change check
            if prev and (curr.product_count > int(prev.get("product_count", 0)) or 
                         curr.first_products != prev.get("first_products", [])):
                msg = f"🚨 SHEIN UPDATE!\nItems found: {curr.product_count}\nLink: {URL}"
                telegram_send(msg)
            
            save_state(curr)
            print(f"Check complete. Found {curr.product_count} items.", flush=True)
        except Exception as e:
            print(f"Scraper Error: {e}", flush=True)
        
        # Sleep 1-2 minutes between checks
        time.sleep(random.randint(60, 120))

if __name__ == "__main__":
    # 1. Start Scraper in Background
    scraper_thread = threading.Thread(target=main_loop, daemon=True)
    scraper_thread.start()
    
    # 2. Run Health Server in Foreground (Main Thread)
    # This ensures the container stays "Started" as long as the server is up.
    try:
        start_health_server()
    except Exception as e:
        print(f"Health Server Crash: {e}", flush=True)
        time.sleep(10)