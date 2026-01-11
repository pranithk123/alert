import json
import os
import random
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

URL = "https://www.sheinindia.in/c/sverse-5939-37961"

# ---- Telegram config ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "state.json"


@dataclass
class Snapshot:
    ts: float
    product_count: int
    first_products: List[str]  # some stable identifiers (title+price+href)


def telegram_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram creds not set; printing message instead:\n", text)
        return

    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": False}
    r = requests.post(endpoint, json=payload, timeout=15)
    r.raise_for_status()


def load_state() -> Optional[Dict[str, Any]]:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(snap: Snapshot) -> None:
    data = {
        "ts": snap.ts,
        "product_count": snap.product_count,
        "first_products": snap.first_products,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_product_signature(item: Dict[str, Any]) -> str:
    # signature should be stable-ish: title + href + price text
    title = (item.get("title") or "").strip()
    href = (item.get("href") or "").strip()
    price = (item.get("price") or "").strip()
    return f"{title} | {price} | {href}"


def scrape_snapshot() -> Snapshot:
    """
    Uses Playwright to render JS and extract product cards.
    Selectors may need adjustment if SHEIN changes markup.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)

        # Let the page hydrate and load product grid
        # We'll wait for either product cards OR an "empty" state.
        # These selectors are best-effort and might need tweaking.
        possible_card_selectors = [
            "a[href*='/p/']",           # product links often include /p/
            "a[href*='-p-']",           # some shops use -p- pattern
            "[data-testid*='goods']",   # common in modern sites
            ".product-card a",          # fallback
        ]

        # Give time for JS network calls
        page.wait_for_timeout(4000)

        # Try to detect products by counting candidate anchors.
        # We'll collect a small list to detect meaningful changes, not just count.
        products: List[Dict[str, Any]] = []
        seen = set()

        for sel in possible_card_selectors:
            try:
                anchors = page.locator(sel)
                count = anchors.count()
                if count == 0:
                    continue

                # Sample up to first 20 anchors to build signatures.
                take = min(count, 20)
                for i in range(take):
                    a = anchors.nth(i)
                    href = a.get_attribute("href") or ""
                    if not href:
                        continue
                    # Avoid non-product junk links
                    if "javascript:" in href.lower():
                        continue

                    # Convert relative to absolute
                    if href.startswith("/"):
                        href = "https://www.sheinindia.in" + href

                    # Try to find a nearby title/price; if not, just use href
                    title = (a.get_attribute("title") or "").strip()
                    text = (a.inner_text(timeout=2000) or "").strip()

                    # price is hard; sometimes in sibling nodes. We can just store text snippet.
                    price = ""
                    if text:
                        # crude: keep short snippet
                        price = " ".join(text.split())[:40]

                    sig_key = (title, href)
                    if sig_key in seen:
                        continue
                    seen.add(sig_key)

                    products.append({"title": title or text[:60], "href": href, "price": price})

                # If we found enough, stop early
                if len(products) >= 8:
                    break
            except PWTimeoutError:
                continue
            except Exception:
                continue

        browser.close()

        signatures = [normalize_product_signature(x) for x in products]
        # Keep first 10 signatures to detect changes
        signatures = signatures[:10]

        return Snapshot(
            ts=time.time(),
            product_count=len(products),
            first_products=signatures,
        )


def should_alert(prev: Optional[Dict[str, Any]], curr: Snapshot) -> bool:
    if prev is None:
        # First run: don’t alert, just baseline (change to True if you want)
        return False

    prev_count = int(prev.get("product_count", 0))
    prev_sigs = prev.get("first_products", []) or []

    # Alert conditions:
    # 1) product count increases
    # 2) signatures changed (new products) even if count same
    if curr.product_count > prev_count:
        return True

    if curr.first_products and curr.first_products != prev_sigs:
        return True

    return False


def main_loop():
    print("BOOT: watcher process started")
    telegram_send("✅ SHEIN watcher started.\n" + URL)

    while True:
        try:
            prev = load_state()
            curr = scrape_snapshot()

            if should_alert(prev, curr):
                msg = (
                    "🚨 Restock/change detected!\n"
                    f"URL: {URL}\n"
                    f"Products found now: {curr.product_count}\n\n"
                    "Top items (signatures):\n"
                    + "\n".join(f"- {s}" for s in curr.first_products[:8])
                )
                telegram_send(msg)

            save_state(curr)

        except Exception as e:
            telegram_send(f"⚠️ Watcher error: {type(e).__name__}: {e}")

        # Sleep with jitter (avoid fixed pattern)
        base = 30  # seconds (adjust; keep reasonable)
        jitter = random.randint(-20, 30)
        time.sleep(max(30, base + jitter))


if __name__ == "__main__":
    main_loop()
