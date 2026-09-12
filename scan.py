import re
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
NTFY_TOPIC = "audi_a6_bbs_wheels_monitor"  # Change this to any unique name
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

FORGED_BUDGET = 2000       # Total budget for a full set of 4 wheels, forged
FLOW_BUDGET = 1300         # Total budget for a full set of 4 wheels, flow-formed
ESTIMATED_SHIPPING_SGD = 500  # Average cost to ship 4 bare rims internationally
WHEELS_NEEDED = 4          # A full set

SEARCH_QUERY = "BBS 19x8.5 ET35 5x112"

# Keywords indicating a listing is already for a full set/pair, not a single wheel.
SET_KEYWORDS = ["satz", "kompletträder", "4x", "4 stück", "4stk", "satz von 4", "set of 4"]

# A realistic set of headers, matching what a real Chrome browser on Windows sends.
# eBay's anti-bot systems weigh header completeness/consistency, not just User-Agent.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

def get_sgd_exchange_rate():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/EUR", timeout=10).json()
        return res["rates"]["SGD"]
    except Exception as e:
        print(f"[WARN] Could not fetch live rate, using fallback. ({e})")
        return 1.48  # Baseline fallback rate

def parse_price_eur(price_text):
    """Handles both '1.234,56' (German) and '1,234.56' / '350.00' (English) formats."""
    cleaned = re.sub(r'[^0-9.,]', '', price_text)
    if ',' in cleaned and '.' in cleaned:
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')  # German: comma is decimal
        else:
            cleaned = cleaned.replace(',', '')  # English: dot is decimal
    elif ',' in cleaned:
        parts = cleaned.split(',')
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(',', '.')  # comma used as decimal
        else:
            cleaned = cleaned.replace(',', '')   # comma used as thousands separator
    return float(cleaned)

def send_phone_alert(wheel_type, title, total_price, link):
    message = f"MATCH FOUND ({wheel_type})!\nItem: {title}\nTotal Est Cost (set of 4): SGD ${total_price:.2f}\nLink: {link}"
    headers = {
        "Title": "BBS 19x8.5J ET35 Alert",
        "Priority": "high",
        "Tags": "car,wheel"
    }
    try:
        requests.post(NTFY_URL, data=message.encode('utf-8'), headers=headers, timeout=10)
        print(f"[ALERT SENT] {title} - SGD ${total_price:.2f}")
    except Exception as e:
        print(f"[WARN] Failed to send ntfy alert: {e}")

def build_search_url(query):
    """
    eBay's /sch/i.html search endpoint can return a stripped generic page
    for requests it flags as automated. /shop/<slug> has proven more
    reliable for plain HTTP requests without JS execution.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')
    return f"https://www.ebay.de/shop/{slug}?_nkw={requests.utils.quote(query)}"

def scan_ebay_germany(eur_to_sgd):
    print("Scanning eBay Germany for BBS wheels...")

    # Use a session so cookies from the homepage visit carry over to the
    # search request, similar to how a real browser establishes a session
    # before navigating to a search results page.
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    try:
        # Step 1: visit the homepage first to pick up session cookies.
        homepage_resp = session.get("https://www.ebay.de/", timeout=15)
        print(f"[INFO] Homepage visit status: {homepage_resp.status_code}, "
              f"cookies received: {len(session.cookies)}")

        # Step 2: now hit the actual search URL, with the Referer header
        # pointing back at the homepage like a real navigation would.
        url = build_search_url(SEARCH_QUERY)
        search_headers = {"Referer": "https://www.ebay.de/", "Sec-Fetch-Site": "same-origin"}
        response = session.get(url, headers=search_headers, timeout=15)
        print(f"[INFO] Search request status: {response.status_code}, "
              f"final URL: {response.url}")

        soup = BeautifulSoup(response.text, 'html.parser')

        # eBay's current (2026) search results layout uses su-card-container cards.
        listings = soup.find_all('div', class_='su-card-container')

        if not listings:
            print("[WARN] No listings found - eBay may have changed its page layout again, "
                  "or the request may have been blocked.")

        for item in listings:
            title_div = item.find('div', class_='s-card__title')
            price_elem = item.find('span', class_='s-card__price')

            if not (title_div and price_elem):
                continue

            link_elem = title_div.find_parent('a') or item.find('a', class_='s-card__link')
            if not link_elem or not link_elem.get('href'):
                continue

            title = title_div.get_text(strip=True).lower()
            link = link_elem['href'].split('?')[0]

            # eBay's fallback ("similar items") search can surface non-BBS
            # wheels that happen to match the size/offset, so the brand
            # name must be checked explicitly, not just the dimensions.
            if "bbs" in title and "19" in title and "8.5" in title and ("et35" in title or "et 35" in title or "is35" in title):
                try:
                    item_price_eur = parse_price_eur(price_elem.get_text())

                    # Most listings are priced per single wheel unless they
                    # explicitly say otherwise. Multiply by WHEELS_NEEDED so
                    # the budget comparison reflects the cost of a full set,
                    # not just one wheel.
                    is_full_set_listing = any(kw in title for kw in SET_KEYWORDS)
                    multiplier = 1 if is_full_set_listing else WHEELS_NEEDED

                    set_price_eur = item_price_eur * multiplier
                    set_price_sgd = set_price_eur * eur_to_sgd
                    total_landed_sgd = set_price_sgd + ESTIMATED_SHIPPING_SGD

                    is_forged = any(kw in title for kw in ["forged", "geschmiedet", "ri-a", "lm", "ri-d"])

                    if is_forged and total_landed_sgd <= FORGED_BUDGET:
                        send_phone_alert("FORGED", title, total_landed_sgd, link)
                    elif not is_forged and total_landed_sgd <= FLOW_BUDGET:
                        send_phone_alert("FLOW-FORMED", title, total_landed_sgd, link)
                except ValueError:
                    continue
    except Exception as e:
        print(f"Error scanning eBay: {e}")

if __name__ == "__main__":
    eur_to_sgd = get_sgd_exchange_rate()
    print(f"Tracker run starting. Topic: {NTFY_TOPIC} | Rate: {eur_to_sgd:.4f} SGD/EUR")
    scan_ebay_germany(eur_to_sgd)
    print("Run complete.")
