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
    """Foreground server to satisfy Railway's health checks."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Explicitly log to confirm the platform is reaching the app
            print(">>> Railway Health Probe received and answered", flush=True) 
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return 

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ Health server active on port {PORT}", flush=True)
    server.serve_forever()

def scrape_snapshot() -> Snapshot:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/pw-browsers"
    
    with sync_playwright() as p:
        # Heaviest possible optimization for low-cost hosting
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process"
            ]
        )
        context = browser.new_context(user_agent="Mozilla/5.0")
        page = context.new_page()
        
        print(f"Scraping {URL}...", flush=True)
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            
            # Simple selector for product links
            anchors = page.locator("a[href*='/p/']")
            count = anchors.count()
            
            products = []
            for i in range(min(count, 10)):
                href = anchors.nth(i).get_attribute("href") or ""
                products.append(href)

            return Snapshot(ts=time.time(), product_count=len(products), first_products=products)
        finally:
            browser.close()

def main_loop():
    """Background scraper thread."""
    # 🔥 CRITICAL: Delay startup so the health server can pass the first check
    print("⏳ Waiting 10s for health check stability...", flush=True)
    time.sleep(10)
    
    print("🚀 Scraper thread fully engaged.", flush=True)
    while True:
        try:
            curr = scrape_snapshot()
            print(f"Check complete. Found {curr.product_count} items.", flush=True)
        except Exception as e:
            print(f"Scraper Error: {e}", flush=True)
        
        time.sleep(random.randint(60, 120))

if __name__ == "__main__":
    # Start Scraper in background
    threading.Thread(target=main_loop, daemon=True).start()
    
    # Run Health Server in main thread (blocks forever)
    try:
        start_health_server()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}", flush=True)