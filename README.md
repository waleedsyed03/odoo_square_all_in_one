# Square Sync & Pay for Odoo 18

**Free** Square integration for Odoo 18: catalog & inventory sync (Odoo ↔ Square), hosted checkout with **Google Pay, Apple Pay, Cash App & Afterpay**, and Square Terminal for POS.

- **License:** LGPL-3  
- **Price:** Free (no `price` in manifest)  
- **Author:** Waleed Syed  
- **Support:** [WhatsApp & docs](https://payments-connect-square.pages.dev/support/)

## Requirements

- Odoo **18.0**
- Apps: Inventory, Sales, Website eCommerce, Point of Sale, Payment
- Python: `requests` (`pip install requests`)
- Square seller account (sandbox or production)

## Install (development)

1. Clone this repository and add the **repository root** (not the inner folder) to Odoo `addons_path`.
2. Restart Odoo and update the app list.
3. Install **Square Sync & Pay**.

Repository layout (required by Odoo Apps):

```
odoo_square_all_in_one/          ← git repo root → addons_path
  odoo_square_all_in_one/        ← Odoo module
    __manifest__.py
    models/
    ...
```

## Odoo Apps Store release

1. Create a vendor account at [apps.odoo.com](https://apps.odoo.com/apps/upload).
2. Zip the module folder (root must contain `odoo_square_all_in_one/__manifest__.py`).
3. Upload the zip — no `price` / `currency` keys (module is free).
4. License in manifest: **LGPL-3**.
5. After approval, merchants install from Apps with one click.

### Zip command (from parent of module folder)

```bash
# Linux / macOS / Git Bash
cd /path/to/addons/parent
zip -r odoo_square_all_in_one.zip odoo_square_all_in_one \
  -x "*/__pycache__/*" -x "*.pyc" -x "*/.git/*"
```

```powershell
# Windows PowerShell
Compress-Archive -Path "c:\path\to\odoo_square_all_in_one" -DestinationPath "odoo_square_all_in_one.zip" -Force
```

## Features

| Direction | Action |
|-----------|--------|
| Odoo → Square | **Push to Square** (products), inventory cron |
| Square → Odoo | **Import from Square**, **Import Inventory**, webhooks |

## Configuration

**Square → Configuration**

- Connect with Square (OAuth)
- Sync settings for push/import
- Fetch Locations → map warehouses

**Square → Support** — WhatsApp support with pre-filled diagnostics.

## Technical name

`odoo_square_all_in_one`
