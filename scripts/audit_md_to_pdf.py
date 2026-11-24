"""Convierte FULL-AUDIT-REPORT.md + ACTION-PLAN.md en un PDF A4 (HTML + matplotlib + Playwright).

Alternativa a google_report.py cuando WeasyPrint no carga (p. ej. Windows sin GTK) o
cuando no hay datos de Google APIs. Estilo del proyecto: azul marino, dorado, Times New Roman.

Uso:
    python scripts/audit_md_to_pdf.py <carpeta_del_reporte> [--cwv lcp=4.3,ttfb=2.83,cls=0.11,inp=118]
        [--target 70] [--json]

La carpeta debe contener FULL-AUDIT-REPORT.md y ACTION-PLAN.md. Genera en ella
<dominio>.pdf, <dominio>.html y charts/. Dependencias: markdown, matplotlib, pypdf, playwright.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

NAVY, GOLD, GREEN, AMBER, RED = "#1e3a5f", "#b8860b", "#2d6a4f", "#d4740e", "#c53030"


def _color(v):
    return GREEN if v >= 75 else AMBER if v >= 50 else RED


def parse_scores(section_md):
    """Lee la tabla de categorias del resumen: | Categoria | Peso | Puntaje | ..."""
    rows = []
    for line in section_md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.match(r"^\d+\s*%$", cells[1]):
            m = re.match(r"(\d+)", cells[2])
            if m:
                rows.append((cells[0], int(m.group(1))))
    return rows


def chart_scores(rows, target, charts):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ys = list(range(len(rows)))[::-1]
    ax.barh(ys, [v for _, v in rows], color=[_color(v) for _, v in rows], height=0.6)
    for y, (_, v) in zip(ys, rows):
        ax.text(v + 1.2, y, str(v), va="center", fontsize=11, color=NAVY, fontweight="bold")
    ax.set_yticks(ys)
    ax.set_yticklabels([n for n, _ in rows])
    ax.set_xlim(0, 100)
    ax.set_xlabel("Puntaje (0–100)")
    ax.axvline(target, color=GOLD, ls="--", lw=1)
    ax.text(target + 0.8, len(rows) - 0.45, f"objetivo {target}", color=GOLD, fontsize=9)
    fig.tight_layout()
    p = charts / "scores.png"
    fig.savefig(p, dpi=200)
    plt.close(fig)
    return p


def chart_cwv(vals, charts):
    import matplotlib.pyplot as plt

    spec = {"lcp": ("LCP (s)", 2.5, 4.0), "ttfb": ("TTFB (s)", 0.8, 1.8),
            "cls": ("CLS", 0.10, 0.25), "inp": ("INP (ms)", 200, 500)}
    items = [(spec[k][0], vals[k], spec[k][1], spec[k][2]) for k in spec if k in vals]
    fig, axes = plt.subplots(1, len(items), figsize=(8, 2.9), squeeze=False)
    for ax, (name, val, good, poor) in zip(axes[0], items):
        ax.bar([0], [val], color=GREEN if val <= good else AMBER if val <= poor else RED, width=0.5)
        ax.axhline(good, color=GREEN, ls="--", lw=1)
        ax.axhline(poor, color=RED, ls="--", lw=1)
        ax.set_ylim(0, max(val, poor) * 1.15)
        ax.set_xticks([])
        ax.set_title(name, fontsize=10, color=NAVY)
        ax.text(0, val, f"{val:g}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    fig.tight_layout()
    p = charts / "cwv.png"
    fig.savefig(p, dpi=200)
    plt.close(fig)
    return p


def _split_h2(text):
    parts = re.split(r"^## ", text, flags=re.M)
    return parts[0], [(s.partition("\n")[0].strip(), s.partition("\n")[2]) for s in parts[1:]]


CSS = f"""
@page {{ size: A4; margin: 20mm 18mm; }}
body {{ font-family: 'Times New Roman', serif; font-size: 11pt; line-height: 1.45; color: #222; background: #fff; }}
h1,h2,h3 {{ color: {NAVY}; }}
h2 {{ font-size: 18pt; border-bottom: 2px solid {GOLD}; padding-bottom: 4px; margin-top: 0; }}
h3 {{ font-size: 13pt; margin-top: 16px; }}
.section {{ break-before: page; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.5pt; }}
th {{ background: {NAVY}; color: #fff; text-align: left; padding: 5px 7px; }}
td {{ border-bottom: 1px solid #ddd; padding: 4px 7px; vertical-align: top; }}
tr:nth-child(even) td {{ background: #faf9f7; }}
code {{ font-size: 9pt; background: #faf9f7; padding: 0 3px; }}
blockquote {{ border-left: 3px solid {GOLD}; margin: 8px 0; padding: 2px 12px; background: #faf9f7; }}
.cap {{ text-align: center; font-size: 9.5pt; color: #555; }}
.cap img {{ width: 85%; max-height: 120mm; }}
.title {{ height: 250mm; display: flex; flex-direction: column; justify-content: center; border-left: 8px solid {NAVY}; padding-left: 24px; }}
.title h1 {{ font-size: 30pt; margin: 0 0 8px; }}
.title .sub {{ color: {GOLD}; font-size: 15pt; margin-bottom: 30px; }}
.score {{ font-size: 54pt; font-weight: bold; margin: 10px 0 0; }}
.toc {{ break-before: page; }}
.toc ul {{ list-style: none; padding: 0; font-size: 12pt; }} .toc li {{ padding: 5px 0; border-bottom: 1px dotted #bbb; }}
.toc a {{ color: {NAVY}; text-decoration: none; }}
"""


def build_html(folder, cwv_vals, target):
    import markdown

    full = (folder / "FULL-AUDIT-REPORT.md").read_text(encoding="utf-8")
    plan = (folder / "ACTION-PLAN.md").read_text(encoding="utf-8")
    head, sections = _split_h2(full)
    charts = folder / "charts"
    charts.mkdir(exist_ok=True)

    domain = (re.search(r"^#\s*.*?:\s*(\S+)", head, flags=re.M) or [None, "sitio"])[1]
    date_line = next((l for l in head.splitlines() if l.startswith("Fecha")), "")
    date_line = date_line.split("·")[0].strip()
    m = re.search(r"Health Score:\*{0,2}\s*(\d+)", full)
    score = int(m.group(1)) if m else None

    def md(t):
        return markdown.markdown(t, extensions=["tables"])

    scores_png = None
    toc, body = [], []
    n = 0
    for title, text in sections:
        if re.match(r"^\d+\.\s*(Cumplimiento|Compliance)", title, flags=re.I):
            continue  # seccion interna del proyecto
        n += 1
        clean = re.sub(r"^\d+\.\s*", "", title)
        html = md(text)
        rows = parse_scores(text)
        if rows and scores_png is None:
            scores_png = chart_scores(rows, target, charts)
            html += (f'<p class="cap"><img src="{scores_png.as_uri()}" alt="Puntajes"><br>'
                     f"<em>Figura 1. Puntaje por categoría (línea punteada: objetivo {target}).</em></p>")
        if cwv_vals and re.search(r"rendimiento|performance", clean, flags=re.I):
            html += (f'<p class="cap"><img src="{chart_cwv(cwv_vals, charts).as_uri()}" alt="CWV"><br>'
                     "<em>Figura 2. Datos de campo frente a umbrales bueno (verde) y malo (rojo).</em></p>")
        toc.append((f"s{n}", clean))
        body.append(f'<div class="section" id="s{n}"><h2>{n}. {clean}</h2>{html}</div>')

    n += 1
    plan_body = re.sub(r"^# .*\n", "", plan, count=1, flags=re.M)
    plan_body = re.sub(r"^## ", "### ", plan_body, flags=re.M)
    toc.append(("plan", "Plan de acción priorizado"))
    body.append(f'<div class="section" id="plan"><h2>{n}. Plan de acción priorizado</h2>{md(plan_body)}</div>')

    toc_html = "".join(f'<li><a href="#{i}">{t}</a></li>' for i, t in toc)
    score_html = (f'<div>SEO Health Score</div><div class="score" style="color:{_color(score)}">{score} / 100</div>'
                  if score is not None else "")
    html = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Auditoría SEO {domain}</title>'
            f"<style>{CSS}</style></head><body>"
            f'<div class="title"><h1>Auditoría SEO</h1><div class="sub">{domain}</div>{score_html}'
            f'<p style="margin-top:40px">{date_line}</p></div>'
            f'<div class="toc"><h2>Contenido</h2><ul>{toc_html}</ul></div>{"".join(body)}</body></html>')
    safe = re.sub(r"[^A-Za-z0-9.-]", "_", domain)
    out = folder / f"{safe}.html"
    out.write_text(html, encoding="utf-8")
    return out, html


def render_pdf(html_path, pdf_path):
    from playwright.sync_api import sync_playwright

    footer = ('<div style="font-size:8px;width:100%;text-align:center;color:#777">'
              'Auditoría SEO · <span class="pageNumber"></span>/<span class="totalPages"></span></div>')
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        pg.pdf(path=str(pdf_path), format="A4", print_background=True,
               margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
               display_header_footer=True, header_template="<span></span>", footer_template=footer)
        b.close()


def review(pdf_path, html):
    """Misma logica que _review_pdf() de google_report.py (que no importa sin WeasyPrint)."""
    r = {"issues": [], "page_count": None, "file_size_kb": round(os.path.getsize(pdf_path) / 1024, 1)}
    try:
        from pypdf import PdfReader
        r["page_count"] = len(PdfReader(str(pdf_path)).pages)
    except ImportError:
        pass
    if re.findall(r'<img[^>]*src=""[^>]*>', html):
        r["issues"].append("imagen vacía")
    for i, s in enumerate(html.split('div class="section"')[1:], 1):
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s[:2000])).strip()
        if len(t) < 50:
            r["issues"].append(f"sección {i} casi sin texto")
    tables = re.findall(r"<table>.*?</table>", html, re.DOTALL)
    if len(tables) != len(set(tables)):
        r["issues"].append("tablas duplicadas")
    r["status"] = "PASS" if not r["issues"] else f"WARN ({len(r['issues'])} issues)"
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="Carpeta con FULL-AUDIT-REPORT.md y ACTION-PLAN.md")
    ap.add_argument("--cwv", help="Valores de campo, p. ej. lcp=4.3,ttfb=2.83,cls=0.11,inp=118")
    ap.add_argument("--target", type=int, default=70, help="Objetivo de score en la gráfica (def. 70)")
    ap.add_argument("--json", "-j", action="store_true", help="Salida JSON")
    a = ap.parse_args()

    folder = Path(a.folder).resolve()
    for f in ("FULL-AUDIT-REPORT.md", "ACTION-PLAN.md"):
        if not (folder / f).exists():
            sys.exit(f"Falta {f} en {folder}")
    cwv = {}
    if a.cwv:
        cwv = {k.strip().lower(): float(v) for k, v in (kv.split("=") for kv in a.cwv.split(","))}

    html_path, html = build_html(folder, cwv, a.target)
    pdf_path = html_path.with_suffix(".pdf")
    render_pdf(html_path, pdf_path)
    result = {"pdf": str(pdf_path), "html": str(html_path), "review": review(pdf_path, html)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
