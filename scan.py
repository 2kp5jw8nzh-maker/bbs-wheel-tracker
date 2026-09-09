import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
NTFY_TOPIC = "audi_a6_bbs_wheels_monitor"  # Change this to any unique name
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

FORGED_BUDGET = 2000
FLOW_BUDGET = 1300
ESTIMATED_SHIPPING_SGD = 500  # Average cost to ship 4 bare rims internationally

SEARCH_QUERY = "BBS 19x8.5 ET35 5x112"

def get_sgd_exchange_rate():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/EUR", timeout=10).json()
        return res["rates"]["SGD"]
    except Exception as e:
        print(f"[WARN] Could not fetch live rate, using fallback. ({e})")
        return 1.48  # Baseline fallback rate

def send_phone_alert(wheel_type, title, total_price, link):
    message = f"MATCH FOUND ({wheel_type})!\nItem: {title}\nTotal Est Cost: SGD ${total_price:.2f}\nLink: {link}"
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

def scan_ebay_germany(eur_to_sgd):
    print("Scanning eBay Germany for CH-R CH125 (Flow-Formed)...")
    url = f"https://www.ebay.de/sch/i.html?_nkw={requests.utils.quote(SEARCH_QUERY)}&_sacat=0"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        listings = soup.find_all('div', class_='s-item__info')

        if not listings:
            print("[WARN] No listings found - eBay may have changed its page layout, "
                  "or the request may have been blocked.")

        for item in listings:
            title_elem = item.find('span', role='heading')
            price_elem = item.find('span', class_='s-item__price')
            link_elem = item.find('a', class_='s-item__link')

            if title_elem and price_elem and link_elem:
                title = title_elem.text.lower()
                link = link_elem['href']

                if "19" in title and "8.5" in title and ("et35" in title or "et 35" in title or "is35" in title):
                    price_str = price_elem.text.replace("EUR", "").replace(".", "").replace(",", ".").strip()
                    try:
                        item_price_eur = float(''.join(c for c in price_str if c.isdigit() or c == '.'))
                        item_price_sgd = item_price_eur * eur_to_sgd
                        total_landed_sgd = item_price_sgd + ESTIMATED_SHIPPING_SGD

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
