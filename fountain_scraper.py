"""
fountain_scraper.py

What this does, in plain words:
  1. Opens NC State's menu website (NetNutrition) the way a browser would.
  2. Asks for each dining hall's week of menus (Fountain, Clark, One Earth, Case and University Towers).
  3. Asks for each Breakfast, Brunch, Lunch and Dinner menu in that week.
  4. Saves everything into one tidy file called menu.json.

To add another dining hall later, add a line to UNITS below. If you don't know its
ID number, write None and the scraper finds it on the site by name.
"""
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE = "https://netmenu2.cbord.com/NetNutrition/ncstate-dining"
UNITS = {                                         # name -> unitOid (the site's ID number)
    "Fountain Dining Hall": 1,
    "Clark Dining Hall": None,                    # None = find the ID on the site by name
    "One Earth": None,
    "Case Dining Hall": None,
    "University Towers": None,
}
MEALS_TO_KEEP = {"Breakfast", "Brunch", "Lunch", "Dinner"}  # "Daily" repeats the same food, so skip it
PAUSE_SECONDS = 2                                 # wait between requests to be gentle on the site
OUTPUT_FILE = os.environ.get("MENU_OUTPUT", "docs/menu.json")


def make_session():
    """A session remembers cookies, like a browser tab does."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; StudentDiningMenuApp/0.1; personal student project)",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://netmenu2.cbord.com",
        "Referer": BASE,
    })
    return session


def panel_html(reply, panel_id):
    """The site's replies are split into 'panels'. This grabs one panel's page code."""
    for panel in reply.get("panels", []):
        if panel.get("id") == panel_id:
            return panel.get("html", "")
    return ""


def parse_week(menu_panel_html):
    """Reads the week list: one card per day, with a link (and ID number) per meal."""
    soup = BeautifulSoup(menu_panel_html, "html.parser")
    menus = []
    for card in soup.select("section.card"):
        header = card.select_one("header.card-title")
        if header is None:
            continue
        label = header.get_text(strip=True)
        try:
            iso_date = datetime.strptime(label, "%A, %B %d, %Y").date().isoformat()
        except ValueError:
            iso_date = None
        for link in card.select("a.cbo_nn_menuLink"):
            found = re.search(r"menuListSelectMenu\((\d+)\)", link.get("onclick", ""))
            if found:
                menus.append({
                    "date": iso_date,
                    "label": label,
                    "meal": link.get_text(strip=True),
                    "menuOid": int(found.group(1)),
                })
    return menus


def parse_items(item_panel_html):
    """Reads one meal's food list: stations (like Pizza, Grill) with their items and diet icons."""
    soup = BeautifulSoup(item_panel_html, "html.parser")
    stations = {}  # station ID -> {"name": ..., "items": [...]}
    for row in soup.find_all("tr"):
        classes = row.get("class", [])
        if "cbo_nn_itemGroupRow" in classes:
            found = re.search(r"toggleCourseItems\(this,\s*(\d+)\)", row.get("onclick", ""))
            if found:
                stations[found.group(1)] = {"name": row.get_text(strip=True), "items": []}
        elif row.get("data-categoryid") in stations:
            link = row.select_one("a.cbo_nn_itemHover")
            if link is None:
                continue
            name = (link.find(string=True, recursive=False) or "").strip()
            tags = [img.get("alt", "").strip() for img in link.find_all("img") if img.get("alt")]
            if name:
                stations[row["data-categoryid"]]["items"].append({"name": name, "tags": tags})
    return [s for s in stations.values() if s["items"]]


def post_with_retry(session, url, data, tries=3):
    """Try a request a few times, because websites sometimes hiccup."""
    for attempt in range(1, tries + 1):
        try:
            reply = session.post(url, data=data, timeout=30)
            reply.raise_for_status()
            return reply
        except requests.RequestException:
            if attempt == tries:
                raise
            time.sleep(5 * attempt)


def find_unit_ids(home_html):
    """Reads the list of dining halls on the home page: name -> ID number."""
    soup = BeautifulSoup(home_html, "html.parser")
    found = {}
    for tag in soup.find_all(attrs={"onclick": True}):
        match = re.search(r"unitsSelectUnit\((\d+)\)", tag["onclick"])
        name = tag.get_text(" ", strip=True)
        if match and name:
            found[name] = int(match.group(1))
    return found


def lookup_id(unit_name, known_ids):
    """Finds a hall's ID by name, e.g. "Clark Dining Hall" matches "Clark"."""
    key = unit_name.lower().replace("dining hall", "").strip()
    if not key:
        return None
    for name, oid in known_ids.items():  # an exact name wins first
        if name.lower().replace("dining hall", "").strip() == key:
            return oid
    for name, oid in known_ids.items():  # then a whole-word match, so "Case" won't match "Showcase"
        if re.search(rf"\b{re.escape(key)}\b", name.lower()):
            return oid
    return None


def fetch_unit(session, unit_name, unit_oid, pause=PAUSE_SECONDS):
    """Gets the whole week for one dining hall."""
    reply = post_with_retry(session, f"{BASE}/Unit/SelectUnitFromUnitsList", {"unitOid": unit_oid})
    reply.raise_for_status()
    data = reply.json()
    menus = parse_week(panel_html(data, "menuPanel"))
    if not menus:
        panels = [p.get("id") for p in data.get("panels", [])]
        print(f"  No week list came back for {unit_name}. Panels in the reply: {panels}")
    days = {}
    for menu in menus:
        if menu["meal"] not in MEALS_TO_KEEP:
            continue
        time.sleep(pause)
        try:
            reply = post_with_retry(session, f"{BASE}/Menu/SelectMenu", {"menuOid": menu["menuOid"]})
            reply.raise_for_status()
            stations = parse_items(panel_html(reply.json(), "itemPanel"))
        except Exception as problem:  # one bad meal should not ruin the whole week
            print(f"  Skipped {menu['label']} {menu['meal']}: {problem}")
            continue
        day = days.setdefault(menu["date"], {"date": menu["date"], "label": menu["label"], "meals": []})
        day["meals"].append({"meal": menu["meal"], "stations": stations})
    return {"name": unit_name, "days": [days[d] for d in sorted(days, key=lambda d: d or "")]}


def run(session, units=UNITS, pause=PAUSE_SECONDS):
    home = session.get(BASE, timeout=30)  # visit the home page first so the site gives us a session
    known_ids = find_unit_ids(home.text)
    result = {"fetchedAt": datetime.now().astimezone().isoformat(timespec="seconds"), "units": []}
    for name, oid in units.items():
        if oid is None:
            oid = lookup_id(name, known_ids)
            if oid is None:
                print(f"Couldn't find {name} on the site. Halls it lists: {known_ids}")
                result["units"].append({"name": name, "days": []})
                continue
            print(f"Found {name}: ID {oid}")
        print(f"Getting {name}...")
        try:
            result["units"].append(fetch_unit(session, name, oid, pause))
        except Exception as problem:  # one broken hall should not ruin the others
            print(f"  Couldn't get {name}: {problem}")
            result["units"].append({"name": name, "days": []})
    return result


def unit_items(unit):
    return sum(len(st["items"]) for d in unit["days"] for m in d["meals"] for st in m["stations"])


def keep_old_when_empty(result, path):
    """If a hall came back empty today, keep yesterday's copy of that hall instead of nothing."""
    try:
        with open(path, encoding="utf-8") as f:
            old = {u["name"]: u for u in json.load(f).get("units", [])}
    except (OSError, ValueError):
        return
    for i, unit in enumerate(result["units"]):
        if unit_items(unit) == 0 and unit["name"] in old:
            print(f"  Keeping the earlier menu for {unit['name']}.")
            result["units"][i] = old[unit["name"]]


def count_items(result):
    return sum(unit_items(u) for u in result["units"])


def save_if_good(result, path=OUTPUT_FILE):
    """Only replace the old menu.json if we really got food. Never overwrite good data with nothing."""
    keep_old_when_empty(result, path)
    result["units"] = [u for u in result["units"] if u["days"]]
    total = count_items(result)
    if total == 0:
        print("Found no food items, so the existing menu.json was NOT changed.")
        sys.exit(1)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    os.replace(temp, path)
    print(f"Saved {total} food items to {path}.")


if __name__ == "__main__":
    save_if_good(run(make_session()))
