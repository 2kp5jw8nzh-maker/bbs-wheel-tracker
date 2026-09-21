import re
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
NTFY_TOPIC = "audi_a6_bbs_wheels_monitor"  # Change this to any unique name
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

FORGED_BUDGET = 2000       # Total budget for a full set of 4 wheels, forged
FLOW_BUDGET = 1300         # Total budget for a full set of 4 wheels, flow-formed
WHEELS_NEEDED = 4          # A full set

ESTIMATED_SHIPPING_GERMANY_SGD = 500   # Germany -> Singapore, 4 bare rims
ESTIMATED_SHIPPING_JAPAN_SGD = 350     # Japan -> Singapore via proxy (Buyee/ZenMarket etc.),
                                        # includes proxy fee + domestic-to-warehouse + international.
                                        # Rough estimate - refine once you get a real quote.

SEARCH_QUERY_EBAY = "BBS 19x8.5 ET35 5x112"
SEARCH_QUERY_JAPAN = "BBS 19x8.5 5H112 +35"  # Japanese listings commonly use "5H112" and "+35" notation

# Keywords indicating a listing is already for a full set/pair, not a single wheel.
SET_KEYWORDS_DE = ["satz", "kompletträder", "4x", "4 stück", "4stk", "satz von 4", "set of 4"]
SET_KEYWORDS_JP = ["4本", "セット", "1台分"]  # "4 pieces", "set", "one car's worth" (i.e. a full set)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def get_sgd_exchange_rate(base_currency):
    """base_currency: 'EUR' or 'JPY'"""
    try:
        res = requests.get(f"https://open.er-api.com/v6/latest/{base_currency}", timeout=10).json()
        return res["rates"]["SGD"]
    except Exception as e:
        print(f"[WARN] Could not fetch live {base_currency}->SGD rate, using fallback. ({e})")
        return 1.48 if base_currency == "EUR" else 0.0095  # rough fallbacks


def parse_price_eur(price_text):
    """Handles both '1.234,56' (German) and '1,234.56' / '350.00' (English) formats."""
    cleaned = re.sub(r'[^0-9.,]', '', price_text)
    if ',' in cleaned and '.' in cleaned:
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        parts = cleaned.split(',')
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    return float(cleaned)


def parse_price_jpy(price_text):
    """JPY prices have no decimal places and often use commas as thousands separators, e.g. '228,000円'."""
    cleaned = re.sub(r'[^0-9]', '', price_text)
    return float(cleaned) if cleaned else 0.0


def send_phone_alert(source, wheel_type, title, total_price, link):
    message = f"MATCH FOUND [{source}] ({wheel_type})!\nItem: {title}\nTotal Est Cost (set of 4): SGD ${total_price:.2f}\nLink: {link}"
    headers = {
        "Title": "BBS 19x8.5J ET35 Alert",
        "Priority": "high",
        "Tags": "car,wheel"
    }
    try:
        requests.post(NTFY_URL, data=message.encode('utf-8'), headers=headers, timeout=10)
        print(f"[ALERT SENT] [{source}] {title} - SGD ${total_price:.2f}")
    except Exception as e:
        print(f"[WARN] Failed to send ntfy alert: {e}")


def check_budget_and_alert(source, title, set_price_local, exchange_rate, shipping_sgd, set_keywords):
    """Shared budget-check logic used by all three sources."""
    set_price_sgd = set_price_local * exchange_rate
    total_landed_sgd = set_price_sgd + shipping_sgd

    is_forged = any(kw in title for kw in ["forged", "geschmiedet", "ri-a", "lm", "ri-d", "鍛造"])  # 鍛造 = forged (JP)

    if is_forged and total_landed_sgd <= FORGED_BUDGET:
        send_phone_alert(source, "FORGED", title, total_landed_sgd, "")
        return True
    elif not is_forged and total_landed_sgd <= FLOW_BUDGET:
        send_phone_alert(source, "FLOW-FORMED", title, total_landed_sgd, "")
        return True
    return False


# --- EBAY GERMANY ---

def build_ebay_search_url(query):
    """
    eBay's /sch/i.html search endpoint can return a stripped generic page
    for requests it flags as automated. /shop/<slug> has proven more
    reliable for plain HTTP requests without JS execution.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')
    return f"https://www.ebay.de/shop/{slug}?_nkw={requests.utils.quote(query)}"


def scan_ebay_germany(eur_to_sgd):
    print("Scanning eBay Germany for BBS wheels...")

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.headers.update({"Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"})

    try:
        homepage_resp = session.get("https://www.ebay.de/", timeout=15)
        print(f"[INFO] eBay DE homepage visit status: {homepage_resp.status_code}, "
              f"cookies received: {len(session.cookies)}")

        url = build_ebay_search_url(SEARCH_QUERY_EBAY)
        search_headers = {"Referer": "https://www.ebay.de/", "Sec-Fetch-Site": "same-origin"}
        response = session.get(url, headers=search_headers, timeout=15)
        print(f"[INFO] eBay DE search status: {response.status_code}, final URL: {response.url}")

        soup = BeautifulSoup(response.text, 'html.parser')
        listings = soup.find_all('div', class_='su-card-container')

        if not listings:
            print("[WARN] eBay DE: No listings found - page layout may have changed, or request blocked.")

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

            if "bbs" in title and "19" in title and "8.5" in title and ("et35" in title or "et 35" in title or "is35" in title):
                try:
                    item_price_eur = parse_price_eur(price_elem.get_text())
                    is_full_set = any(kw in title for kw in SET_KEYWORDS_DE)
                    multiplier = 1 if is_full_set else WHEELS_NEEDED
                    set_price_eur = item_price_eur * multiplier

                    set_price_sgd = set_price_eur * eur_to_sgd
                    total_landed_sgd = set_price_sgd + ESTIMATED_SHIPPING_GERMANY_SGD
                    is_forged = any(kw in title for kw in ["forged", "geschmiedet", "ri-a", "lm", "ri-d"])

                    if is_forged and total_landed_sgd <= FORGED_BUDGET:
                        send_phone_alert("eBay DE", "FORGED", title, total_landed_sgd, link)
                    elif not is_forged and total_landed_sgd <= FLOW_BUDGET:
                        send_phone_alert("eBay DE", "FLOW-FORMED", title, total_landed_sgd, link)
                except ValueError:
                    continue
    except Exception as e:
        print(f"[ERROR] eBay DE scan failed: {e}")


# --- YAHOO AUCTIONS JAPAN ---
# NOTE: Selectors below are a best-effort starting point based on Yahoo Auctions
# Japan's known page structure, but have NOT been verified against a live page
# the way the eBay ones were. Treat this as a first draft to debug together
# once you're back at a machine where I can inspect the real HTML in a browser.

def scan_yahoo_auctions_japan(jpy_to_sgd):
    print("Scanning Yahoo Auctions Japan for BBS wheels...")

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.headers.update({"Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"})

    try:
        url = f"https://auctions.yahoo.co.jp/search/search?p={requests.utils.quote(SEARCH_QUERY_JAPAN)}&va={requests.utils.quote(SEARCH_QUERY_JAPAN)}"
        response = session.get(url, timeout=15)
        print(f"[INFO] Yahoo Auctions JP search status: {response.status_code}, final URL: {response.url}")

        soup = BeautifulSoup(response.text, 'html.parser')
        # Yahoo Auctions listing cards - class name is a best guess, verify and fix in browser.
        listings = soup.find_all('li', class_='Product')

        if not listings:
            print("[WARN] Yahoo Auctions JP: No listings found - selectors likely need updating "
                  "once verified against the live page.")

        for item in listings:
            title_elem = item.find(class_=re.compile('Product__title'))
            price_elem = item.find(class_=re.compile('Product__price'))
            link_elem = item.find('a', href=True)

            if not (title_elem and price_elem and link_elem):
                continue

            title = title_elem.get_text(strip=True).lower()
            link = link_elem['href'].split('?')[0]

            if "bbs" in title and "19" in title and "8.5" in title and ("35" in title):
                try:
                    item_price_jpy = parse_price_jpy(price_elem.get_text())
                    is_full_set = any(kw in title for kw in SET_KEYWORDS_JP)
                    multiplier = 1 if is_full_set else WHEELS_NEEDED
                    set_price_jpy = item_price_jpy * multiplier

                    set_price_sgd = set_price_jpy * jpy_to_sgd
                    total_landed_sgd = set_price_sgd + ESTIMATED_SHIPPING_JAPAN_SGD
                    is_forged = any(kw in title for kw in ["forged", "鍛造", "ri-a", "lm", "ri-d"])

                    if is_forged and total_landed_sgd <= FORGED_BUDGET:
                        send_phone_alert("Yahoo Auctions JP", "FORGED", title, total_landed_sgd, link)
                    elif not is_forged and total_landed_sgd <= FLOW_BUDGET:
                        send_phone_alert("Yahoo Auctions JP", "FLOW-FORMED", title, total_landed_sgd, link)
                except ValueError:
                    continue
    except Exception as e:
        print(f"[ERROR] Yahoo Auctions JP scan failed: {e}")


# --- MERCARI JAPAN ---
# NOTE: Same caveat as Yahoo Auctions above - Mercari's search results are
# rendered heavily via JavaScript, so a plain requests+BeautifulSoup scrape
# may return an empty shell page with no listings in the raw HTML at all.
# This may end up needing a different approach (e.g. Mercari's internal
# search API, if one can be found) rather than HTML scraping. Flagging this
# now so it's not a surprise - we'll find out for sure once we test it live.

def scan_mercari_japan(jpy_to_sgd):
    print("Scanning Mercari Japan for BBS wheels...")

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.headers.update({"Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"})

    try:
        url = f"https://jp.mercari.com/search?keyword={requests.utils.quote(SEARCH_QUERY_JAPAN)}"
        response = session.get(url, timeout=15)
        print(f"[INFO] Mercari JP search status: {response.status_code}, final URL: {response.url}")

        soup = BeautifulSoup(response.text, 'html.parser')
        # Mercari is a JS-rendered SPA - this selector is a placeholder and will
        # very likely need to be replaced with a proper API call once we test this.
        listings = soup.find_all('li', {'data-testid': 'item-cell'})

        if not listings:
            print("[WARN] Mercari JP: No listings found in raw HTML - the page is likely "
                  "JavaScript-rendered, meaning this scraping approach may not work at all "
                  "and needs a different strategy (e.g. their internal search API).")

        for item in listings:
            title_elem = item.find(class_=re.compile('itemLabel'))
            price_elem = item.find(class_=re.compile('number'))
            link_elem = item.find('a', href=True)

            if not (title_elem and price_elem and link_elem):
                continue

            title = title_elem.get_text(strip=True).lower()
            link = "https://jp.mercari.com" + link_elem['href'].split('?')[0]

            if "bbs" in title and "19" in title and "8.5" in title and "35" in title:
                try:
                    item_price_jpy = parse_price_jpy(price_elem.get_text())
                    is_full_set = any(kw in title for kw in SET_KEYWORDS_JP)
                    multiplier = 1 if is_full_set else WHEELS_NEEDED
                    set_price_jpy = item_price_jpy * multiplier

                    set_price_sgd = set_price_jpy * jpy_to_sgd
                    total_landed_sgd = set_price_sgd + ESTIMATED_SHIPPING_JAPAN_SGD
                    is_forged = any(kw in title for kw in ["forged", "鍛造", "ri-a", "lm", "ri-d"])

                    if is_forged and total_landed_sgd <= FORGED_BUDGET:
                        send_phone_alert("Mercari JP", "FORGED", title, total_landed_sgd, link)
                    elif not is_forged and total_landed_sgd <= FLOW_BUDGET:
                        send_phone_alert("Mercari JP", "FLOW-FORMED", title, total_landed_sgd, link)
                except ValueError:
                    continue
    except Exception as e:
        print(f"[ERROR] Mercari JP scan failed: {e}")


if __name__ == "__main__":
    eur_to_sgd = get_sgd_exchange_rate("EUR")
    jpy_to_sgd = get_sgd_exchange_rate("JPY")
    print(f"Tracker run starting. Topic: {NTFY_TOPIC} | EUR/SGD: {eur_to_sgd:.4f} | JPY/SGD: {jpy_to_sgd:.6f}")

    scan_ebay_germany(eur_to_sgd)
    scan_yahoo_auctions_japan(jpy_to_sgd)
    scan_mercari_japan(jpy_to_sgd)

    print("Run complete.")
