# Odoo Apps — Git repository registration

## Repository URL (Odoo 18)

Use this exact format on [apps.odoo.com](https://apps.odoo.com/apps/upload) → **Register your Git repository**:

```
ssh://git@github.com/waleedsyed03/odoo_square_all_in_one.git#18.0
```

Replace `waleedsyed03` with your GitHub username if different.

## One-time GitHub setup

1. Log in to GitHub with **xayed.waleed@gmail.com** (username: **waleedsyed03** if not changed).
2. Add SSH key (already generated on this PC):
   - Open: https://github.com/settings/ssh/new
   - Title: `Odoo Square PC`
   - Key (paste entire line):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHpNxGLObK1vtByXECdxCFQzWukq4lsF+Ggttq2+fuZ8 xayed.waleed@gmail.com
```

3. Create a new **public** repository:
   - https://github.com/new
   - Name: `odoo_square_all_in_one`
   - Visibility: **Public**
   - Do **not** add README, .gitignore, or license
4. Push the module:

```powershell
cd c:\xampp\htdocs\site1\odoo_square_all_in_one
git push -u origin 18.0
```

## Branch naming

Odoo Apps expects the branch to match the series: **`18.0`** for Odoo 18 modules.

## Vendor contact

- Email: wordpress.ingenious@gmail.com
- Support: https://payments-connect-square.pages.dev/support/
