# Odoo Apps — Git repository registration

## Repository URL (Odoo 18)

Use this exact format on [apps.odoo.com](https://apps.odoo.com/apps/upload) → **Register your Git repository**:

```
ssh://git@github.com/waleedsyed03/odoo_square_all_in_one.git#18.0
```

Replace `waleedsyed03` with your GitHub username if different.

## One-time GitHub setup

1. Log in to GitHub as **xayed.waleed@gmail.com**
2. Create a new **public** repository:
   - Name: `odoo_square_all_in_one`
   - Do **not** add README, .gitignore, or license (already in this repo)
3. Add your SSH key to GitHub (Settings → SSH keys) if not already done
4. From this folder, run:

```powershell
cd c:\xampp\htdocs\site1\odoo_square_all_in_one
git remote add origin git@github.com:waleedsyed03/odoo_square_all_in_one.git
git push -u origin 18.0
```

## Branch naming

Odoo Apps expects the branch to match the series: **`18.0`** for Odoo 18 modules.

## Vendor contact

- Email: xayed.waleed@gmail.com
- Support: https://payments-connect-square.pages.dev/support/
