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

# 1. Define telegram_send FIRST so it's available to everything below
def telegram_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[WARN] Telegram creds missing. Message: {text}", flush=True)
        return
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(endpoint, json=payload, timeout=15).raise_for_status()
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Print to logs so you can see Railway's pings
            print(">>> Railway Health Probe answered", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, format, *args):
            return 

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ Health server active on port {PORT}", flush=True)
    server.serve_forever()

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
    # Use the browser path defined in your Dockerfile
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/pw-browsers"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process"]
        )
        context = browser.new_context(user_agent="Mozilla/5.0")
        page = context.new_page()
        
        print(f"Scraping {URL}...", flush=True)
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            # Common SHEIN link selectors
            anchors = page.locator("a[href*='/p/']")
            count = anchors.count()
            
            products = []
            seen = set()
            for i in range(min(count, 15)):
                href = anchors.nth(i).get_attribute("href") or ""
                if not href or href in seen: continue
                seen.add(href)
                products.append({"title": "Item", "href": href})

            return Snapshot(ts=time.time(), product_count=len(products), first_products=[normalize_product_signature(p) for p in products])
        finally:
            browser.close()

def main_loop():
    # Delay startup so health check passes before heavy scraping starts
    print("⏳ Waiting 15s for stability...", flush=True)
    time.sleep(15)
    
    print("🚀 Scraper thread started.", flush=True)
    # Sending test message to confirm connection
    telegram_send("✅ SHEIN Bot is now active and monitoring!")
    
    while True:
        try:
            prev = load_state()
            curr = scrape_snapshot()
            
            # Alert if count increases or items change
            if prev and (curr.product_count > int(prev.get("product_count", 0)) or curr.first_products != prev.get("first_products", [])):
                telegram_send(f"🚨 SHEIN UPDATE!\nItems found: {curr.product_count}\n{URL}")
            
            save_state(curr)
            print(f"Check complete. Found {curr.product_count} items.", flush=True)
        except Exception as e:
            print(f"Scraper Error: {e}", flush=True)
        
        time.sleep(random.randint(60, 150))

if __name__ == "__main__":
    # Start Scraper in background
    threading.Thread(target=main_loop, daemon=True).start()
    
    # Run Health Server in foreground to keep container alive
    try:
        start_health_server()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}", flush=True)
        time.sleep(10)