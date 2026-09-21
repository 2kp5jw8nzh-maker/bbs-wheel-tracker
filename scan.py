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

SEARCH_QUERY_EBAY = "BBS 19x8.5 ET35 5x112"
SEARCH_QUERY_JAPAN = "BBS 19x8.5 5H112"  # Offset dropped from the query itself - matched via
                                          # the OFFSET_MIN/OFFSET_MAX range below instead, since
                                          # exact ET35 listings are rare on any given day.

OFFSET_MIN = 32
OFFSET_MAX = 35  # Accepts ET32-ET35 only, nothing higher.

# Keywords indicating a listing is already for a full set/pair, not a single wheel.
SET_KEYWORDS_DE = ["satz", "kompletträder", "4x", "4 stück", "4stk", "satz von 4", "set of 4"]
SET_KEYWORDS_JP = ["4本", "セット", "1台分"]  # "4 pieces", "set", "one car's worth"

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
        return 1.48 if base_currency == "EUR" else 0.0095


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
    """JPY prices have no decimal places, e.g. '228,000円'."""
    cleaned = re.sub(r'[^0-9]', '', price_text)
    return float(cleaned) if cleaned else 0.0


def offset_in_range(title):
    """
    Looks for a +NN or ＋NN offset marker in the title (half-width or
    full-width plus sign, as both appear in Japanese listings) and checks
    it falls within [OFFSET_MIN, OFFSET_MAX] inclusive.
    """
    matches = re.findall(r'[+＋]\s*(\d{2})', title)
    for m in matches:
        offset = int(m)
        if OFFSET_MIN <= offset <= OFFSET_MAX:
            return True
    return False


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


# --- EBAY GERMANY ---

def build_ebay_search_url(query):
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
        listings = soup.find_all('li', class_='Product')

        if not listings:
            print("[WARN] Yahoo Auctions JP: No listings found - selectors may need updating.")
        else:
            print(f"[INFO] Yahoo Auctions JP: {len(listings)} listing elements found on page.")

        extraction_failures = 0
        filter_mismatches = 0
        matches_found = 0

        for item in listings:
            title_elem = item.find(class_=re.compile('Product__title'))
            price_elem = item.find(class_=re.compile('Product__price'))
            link_elem = item.find('a', href=True)

            if not (title_elem and price_elem and link_elem):
                extraction_failures += 1
                continue

            title_raw = title_elem.get_text(strip=True)
            title = title_raw.lower()
            link = link_elem['href'].split('?')[0]

            if "bbs" in title and "19" in title and "8.5" in title and offset_in_range(title_raw):
                matches_found += 1
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
            else:
                filter_mismatches += 1

        if listings:
            print(f"[INFO] Yahoo Auctions JP: extraction_failures={extraction_failures}, "
                  f"filter_mismatches={filter_mismatches}, matches_found={matches_found} "
                  f"(out of {len(listings)} total elements)")
    except Exception as e:
        print(f"[ERROR] Yahoo Auctions JP scan failed: {e}")
        print(f"[ERROR] Yahoo Auctions JP scan failed: {e}")


# --- MERCARI JAPAN: DISABLED ---
# Mercari has no public API, and its internal search endpoint requires
# cryptographically signed DPoP tokens - not something reasonable to
# reverse-engineer and maintain in a personal script (this is why
# third-party paid scraping services exist specifically for Mercari).
# Dropped from this tracker; eBay Germany + Yahoo Auctions Japan cover
# the search adequately.


if __name__ == "__main__":
    eur_to_sgd = get_sgd_exchange_rate("EUR")
    jpy_to_sgd = get_sgd_exchange_rate("JPY")
    print(f"Tracker run starting. Topic: {NTFY_TOPIC} | EUR/SGD: {eur_to_sgd:.4f} | JPY/SGD: {jpy_to_sgd:.6f}")

    scan_ebay_germany(eur_to_sgd)
    scan_yahoo_auctions_japan(jpy_to_sgd)

    print("Run complete.")
