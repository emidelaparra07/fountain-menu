# Fountain Dining Menu

An independent student project that shows Fountain Dining Hall menus on a phone-friendly page.

- `fountain_scraper.py` gets the latest menus from NC State Dining's public menu portal and saves them to `docs/menu.json`.
- `.github/workflows/update-menu.yml` runs the scraper every morning on GitHub and saves the result.
- `docs/index.html` is the web page. It reads `docs/menu.json` and shows it.

Not affiliated with NC State Dining. Allergy and diet labels come from the official portal and may be incomplete.
