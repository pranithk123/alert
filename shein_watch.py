import json
import os
import random
import time
import threading
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from typing import Optional, Dict, Any

import requests
from playwright.sync_api import sync_playwright

# Configuration
URL = "https://www.sheinindia.in/c/sverse-5939-37961"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STATE_FILE = "state.json"
PORT = int(os.getenv("PORT", "8080"))

@dataclass
class Snapshot:
    ts: float
    men_count: int
    women_count: int

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
            self.send_response(200)
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
    data = {"ts": snap.ts, "men_count": snap.men_count, "women_count": snap.women_count}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def extract_number(text: str) -> int:
    """Extracts '54' from 'Women (54)'"""
    match = re.search(r'\((\d+)\)', text)
    return int(match.group(1)) if match else 0

def scrape_snapshot() -> Snapshot:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/pw-browsers"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process"]
        )
        page = browser.new_page(user_agent="Mozilla/5.0")
        
        print(f"Scraping counts from {URL}...", flush=True)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        
        # Wait for the filter sidebar row to appear
        page.wait_for_selector(".S-p-attr-row", timeout=15000)

        men_count = 0
        women_count = 0

        # Find all filter rows (Women, Men, etc.)
        rows = page.locator(".S-p-attr-row")
        for i in range(rows.count()):
            row_text = rows.nth(i).inner_text()
            if "Women" in row_text:
                women_count = extract_number(row_text)
            elif "Men" in row_text:
                men_count = extract_number(row_text)

        browser.close()
        return Snapshot(ts=time.time(), men_count=men_count, women_count=women_count)

def main_loop():
    print("⏳ Starting in 15s...", flush=True)
    time.sleep(15)
    telegram_send("✅ SHEIN Stock Bot is active (Checking every 40s).")
    
    while True:
        try:
            prev = load_state()
            curr = scrape_snapshot()
            
            if prev:
                pm, pw = int(prev.get("men_count", 0)), int(prev.get("women_count", 0))
                dm, dw = curr.men_count - pm, curr.women_count - pw

                if dm != 0 or dw != 0:
                    mi = "⬆️" if dm > 0 else "⬇️"
                    wi = "⬆️" if dw > 0 else "⬇️"
                    
                    msg = (
                        "🔔 Shein Stock Update\n\n"
                        f"👨 Men → {curr.men_count} {mi} {dm:+d}\n"
                        f"👩 Women → {curr.women_count} {wi} {dw:+d}\n\n"
                        f"⏰ {time.strftime('%d %b %Y, %I:%M %p')}\n\n"
                        f"Direct Link: {URL}"
                    )
                    telegram_send(msg)
            
            save_state(curr)
            print(f"Update: Men({curr.men_count}) Women({curr.women_count})", flush=True)
        except Exception as e:
            print(f"Error: {e}", flush=True)
        
        # Fixed 40-second interval as requested
        time.sleep(40)

if __name__ == "__main__":
    threading.Thread(target=main_loop, daemon=True).start()
    try:
        start_health_server()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}", flush=True)
        time.sleep(10)