from pathlib import Path

tmpl_dir = Path("templates")
new_name = "Talent Management Platform for Employee Performance and Career Growth"

for p in tmpl_dir.glob("*.html"):
    txt = p.read_text(encoding="utf-8")
    txt_updated = txt.replace("Talent Sphere Elevate", new_name)
    if txt_updated != txt:
        p.write_text(txt_updated, encoding="utf-8")
        print(f"Updated platform title in {p.name}")

# Also update base.html sidebar brand title & sub
base_p = Path("templates/base.html")
base_txt = base_p.read_text(encoding="utf-8")
base_txt = base_txt.replace('<div class="sb-brand-title">Talent Sphere</div>', '<div class="sb-brand-title" style="font-size: 0.88rem; line-height: 1.2;">Talent Management Platform</div>')
base_txt = base_txt.replace('<div class="sb-brand-sub">Elevate Platform</div>', '<div class="sb-brand-sub" style="font-size: 0.62rem; color: #94a3b8;">Employee Performance & Career Growth</div>')
base_p.write_text(base_txt, encoding="utf-8")

# Also update login.html main heading
login_p = Path("templates/login.html")
login_txt = login_p.read_text(encoding="utf-8")
login_txt = login_txt.replace('<h1>Talent Sphere Elevate</h1>', '<h1 style="font-size: 1.35rem; line-height: 1.3;">Talent Management Platform</h1><div style="font-size: 0.85rem; color: #94a3b8; font-weight: 600; margin-bottom: 0.75rem;">for Employee Performance and Career Growth</div>')
login_p.write_text(login_txt, encoding="utf-8")

print("All template titles updated successfully!")
