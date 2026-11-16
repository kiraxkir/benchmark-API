"""Aperçu visuel du rapport SANS LaTeX : rapport.tex -> HTML deux colonnes -> PDF (Edge).

Usage : python final-report/apercu.py
Sortie : final-report/apercu/rapport_apercu.html et rapport_apercu.pdf

Ce n'est PAS le PDF officiel : la mise en page imite le modèle IEEEtran (deux colonnes,
Times, sections I, II…, « Fig. n », « TABLE n »), mais la coupure des pages, les
césures et le placement des flottants diffèrent de pdfLaTeX. Les graphiques sont redessinés
avec matplotlib à partir des mêmes fichiers figures/pgf_*.tex (mêmes valeurs, mêmes styles).
Le PDF officiel se compile sur Overleaf (ou avec pdflatex).
"""
import html
import re
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "apercu"
BS = "\\"
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


# --------------------------------------------------------------------------- texte LaTeX -> HTML
def math(m: str) -> str:
    rep = {r"\times": "×", r"\pm": "±", r"\cdot": "·", r"\varepsilon": "ε", r"\alpha": "α",
           r"\lambda": "λ", r"<": "&lt;", "{,}": ","}
    for a, b in rep.items():
        m = m.replace(a, b)
    m = re.sub(r"\^\{([^}]*)\}", r"<sup>\1</sup>", m)
    m = re.sub(r"\^(\w)", r"<sup>\1</sup>", m)
    return f"<i>{m.replace('{', '').replace('}', '')}</i>"


def inline(t: str, refs: dict, cites: dict) -> str:
    t = t.replace(BS + "%", "\x00PCT").replace(BS + "{", "\x00LB").replace(BS + "}", "\x00RB")
    t = re.sub(r"(?<!\\)%.*", "", t)  # commentaires
    t = html.escape(t, quote=False)
    t = re.sub(r"\$([^$]*)\$", lambda m: math(html.unescape(m.group(1))), t)
    t = re.sub(r"\\ref\{([^}]*)\}", lambda m: refs.get(m.group(1), "??"), t)
    t = re.sub(r"\\cite\{([^}]*)\}", lambda m: f"[{cites.get(m.group(1), '?')}]", t)
    t = re.sub(r"\\url\{([^}]*)\}", r'<span class="tt">\1</span>', t)
    for _ in range(3):  # imbrications simples
        t = re.sub(r"\\textbf\{([^{}]*)\}", r"<b>\1</b>", t)
        t = re.sub(r"\\textit\{([^{}]*)\}", r"<i>\1</i>", t)
        t = re.sub(r"\\texttt\{([^{}]*)\}", r'<span class="tt">\1</span>', t)
        t = re.sub(r"\\textsuperscript\{([^{}]*)\}", r"<sup>\1</sup>", t)
    rep = [(BS + "og ", "« "), (BS + "og", "«"), (" " + BS + "fg{}", " »"), (" " + BS + "fg", " »"),
           (BS + "fg{}", "»"), (BS + "fg", "»"), (BS + "ldots", "…"), (BS + "_", "_"), (BS + ",", "\u202f"),
           ("~", "\u00a0"), ("---", "—"), ("--", "–"), (BS + "&amp;", "&amp;"), (BS + BS, "<br>")]
    for a, b in rep:
        t = t.replace(a, b)
    t = t.replace("{", "").replace("}", "")
    return t.replace("\x00PCT", "%").replace("\x00LB", "{").replace("\x00RB", "}")


def tabular_to_html(src: str, refs, cites) -> str:
    body = re.search(r"\\begin\{tabular\}\{.*?\}\n(.*?)\\end\{tabular\}", src, re.S).group(1)
    rows, head = [], True
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        if line == BS + "toprule":
            continue
        if line == BS + "midrule":
            if head:
                head = False
            else:
                rows.append('<tr class="mid"></tr>')
            continue
        if line == BS + "bottomrule":
            continue
        cells = [inline(c.strip(), refs, cites) for c in line.rstrip("\\").rstrip().rstrip("\\").split("&")]
        tag = "th" if head else "td"
        rows.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
    return '<table class="tab">' + "".join(rows) + "</table>"


# --------------------------------------------------------------------------- graphiques pgfplots -> PNG
STYLE = {"black, thick, mark=*": ("-", "o", "black"),
         "black, thick, dashed, mark=square*": ("--", "s", "black"),
         "black, thick, dotted, mark=triangle*": (":", "^", "black"),
         "gray, thick, dashdotted, mark=diamond*": ("-.", "D", "0.45")}
FILL = {"gray!80": "0.35", "gray!50": "0.6", "gray!20": "0.85", "white": "1.0", "gray!40": "0.72"}


def opt(src, key):
    m = re.search(key + r"=\{([^}]*)\}", src)
    return m.group(1).replace(BS + "%", "%").replace("~", " ") if m else ""


def pgf_to_png(pgf: Path, png: Path) -> None:
    src = pgf.read_text(encoding="utf-8")
    plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "DejaVu Serif"],
                         "font.size": 8})
    fig, ax = plt.subplots(figsize=(3.45, 2.15))
    ylabel = opt(src, "ylabel")
    legend = re.search(r"\\legend\{([^}]*)\}", src)
    names = [n.strip() for n in legend.group(1).split(",")] if legend else []
    plots = re.findall(r"\\addplot\+?\[(.*?)\]\s*(?:coordinates)?\s*\{(.*?)\};", src, re.S)
    if "boxplot prepared" in src:
        stats = []
        for o, _ in plots:
            d = dict(re.findall(r"(lower whisker|lower quartile|median|upper quartile|upper whisker)=([\d.]+)", o))
            stats.append({"whislo": float(d["lower whisker"]), "q1": float(d["lower quartile"]),
                          "med": float(d["median"]), "q3": float(d["upper quartile"]),
                          "whishi": float(d["upper whisker"])})
        bp = ax.bxp(stats, showfliers=False, patch_artist=True)
        for b in bp["boxes"]:
            b.set(facecolor="0.72", edgecolor="black")
        for m in bp["medians"]:
            m.set(color="black", linewidth=1.4)
        ax.set_xticks(range(1, len(stats) + 1), opt(src, "xticklabels").split(","))
    elif "ybar" in src:
        cats = opt(src, "symbolic x coords").split(",")
        n = len(plots)
        width = 0.8 / n if n > 1 else 0.45
        for i, (o, coords) in enumerate(plots):
            pts = re.findall(r"\(([^,()]+),([\d.]+)\)(?:\s*\+-\s*\(0,([\d.]+)\))?", coords)
            ys = {c: float(y) for c, y, _ in pts}
            es = {c: float(e) for c, _, e in pts if e}
            fill = FILL.get(re.search(r"fill=([^,\]]+)", o).group(1).strip(), "0.72")
            xs = [k + (i - (n - 1) / 2) * width for k in range(len(cats))]
            bars = ax.bar(xs, [ys[c] for c in cats], width, color=fill, edgecolor="black", linewidth=0.6,
                          yerr=[es.get(c, 0) for c in cats] if es else None, capsize=1.5,
                          label=names[i] if i < len(names) else None)
            if "nodes near coords=" in src:
                for b, c in zip(bars, cats):
                    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{ys[c]:,.0f}".replace(",", "\u202f"),
                            ha="center", va="bottom", fontsize=7)
        rot = 30 if "rotate=30" in src else 0
        ax.set_xticks(range(len(cats)), cats, rotation=rot, ha="right" if rot else "center")
        ax.set_ylim(0, ax.get_ylim()[1] * (1.3 if n > 1 else 1.12))
        if names:
            ax.legend(ncol=4, fontsize=6, frameon=True, loc="upper center")
    else:
        for i, (o, coords) in enumerate(plots):
            style = STYLE[o.split(", error bars")[0].strip()]
            pts = re.findall(r"\((\d+),([\d.]+)\)\s*\+-\s*\(0,([\d.]+)\)", coords)
            x = [int(a) for a, _, _ in pts]
            y = [float(b) for _, b, _ in pts]
            e = [float(c) for _, _, c in pts]
            ax.errorbar(x, y, yerr=e, linestyle=style[0], marker=style[1], color=style[2], linewidth=1.2,
                        markersize=4, capsize=2, label=names[i] if i < len(names) else None)
        ax.set_xticks([10, 50, 100])
        ax.set_xlabel(opt(src, "xlabel"))
        top = re.search(r"ymax=([\d.]+)", src)
        ax.set_ylim(0, float(top.group(1)) if top else None)
        loc = "lower right" if "south east" in src else "upper left"
        ax.legend(fontsize=6, loc=loc)
    ax.set_ylabel(ylabel)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}".replace(".", ",")))
    fig.savefig(png, dpi=220, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------- document
CSS = """
@page { size: A4; margin: 19mm 14mm 25mm 14mm; }
body { font-family: 'Times New Roman', Times, serif; font-size: 10pt; line-height: 1.18; color: #000; }
h1.title { font-size: 22pt; font-weight: normal; text-align: center; margin: 0 0 10pt; line-height: 1.15; }
.authors { display: flex; flex-wrap: wrap; justify-content: center; gap: 4pt 26pt; margin-bottom: 14pt; }
.author { text-align: center; font-size: 10pt; } .author .n { font-size: 11pt; }
.author .a { font-style: italic; }
.cols { column-count: 2; column-gap: 0.2in; text-align: justify; hyphens: auto; }
.abstract { font-weight: bold; font-size: 9pt; margin: 0 0 6pt; }
.abstract i.h { font-style: italic; }
.kw { font-weight: bold; font-size: 9pt; margin-bottom: 10pt; }
h2 { font-size: 10pt; font-weight: normal; font-variant: small-caps; text-align: center; margin: 10pt 0 5pt; }
h3 { font-size: 10pt; font-weight: normal; font-style: italic; margin: 7pt 0 3pt; }
p { margin: 0; text-indent: 1em; } p.first { text-indent: 0; }
ul, ol { margin: 3pt 0; padding-left: 1.4em; } li { margin-bottom: 1.5pt; }
.tt { font-family: 'Courier New', monospace; font-size: 9pt; }
figure { margin: 8pt 0; break-inside: avoid; text-align: center; }
figure img { width: 100%; }
figcaption { font-size: 8pt; text-align: justify; margin-top: 3pt; }
.tblock { margin: 8pt 0; break-inside: avoid; text-align: center; }
.tcap { font-size: 8pt; font-variant: small-caps; margin-bottom: 4pt; text-align: center; }
.tcap .num { display: block; }
.wide { column-span: all; }
table.tab { border-collapse: collapse; margin: 0 auto; font-size: 8pt; }
table.tab th, table.tab td { padding: 1.5pt 5pt; text-align: center; }
table.tab th:first-child, table.tab td:first-child { text-align: left; }
table.tab tr:first-child th { border-top: 1.2px solid #000; }
table.tab tr:last-child td { border-bottom: 1.2px solid #000; }
table.tab tr.mid { border-top: 0.6px solid #000; height: 0; }
table.tab tr:has(th) + tr:has(td) td { border-top: 0.6px solid #000; }
table.tab tr:has(th) + tr:has(th) th { font-weight: normal; }
.bib { font-size: 8pt; } .bib li { margin-bottom: 2pt; }
.note { font-family: Arial, sans-serif; font-size: 7.5pt; color: #a00; text-align: center; margin-bottom: 6pt; }
"""


def build() -> Path:
    OUT.mkdir(exist_ok=True)
    tex = (HERE / "rapport.tex").read_text(encoding="utf-8")
    body = tex.split(BS + "begin{document}", 1)[1].split(BS + "end{document}", 1)[0]
    body = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith("%"))

    # Numérotation : sections, figures, tables, références.
    refs, cites = {}, {}
    sec = sub = fig = tab = 0
    for m in re.finditer(r"\\(section|subsection)\{|\\begin\{(figure|table\*?)\}|\\label\{([^}]*)\}|\\bibitem\{([^}]*)\}",
                         body):
        if m.group(1) == "section":
            sec += 1
            sub = 0
            cur = ROMAN[sec - 1]
        elif m.group(1) == "subsection":
            sub += 1
            cur = chr(64 + sub)
        elif m.group(2) == "figure":
            fig += 1
            cur = str(fig)
        elif m.group(2):
            tab += 1
            cur = ROMAN[tab - 1]
        elif m.group(3):
            refs[m.group(3)] = cur
        elif m.group(4):
            cites[m.group(4)] = str(len(cites) + 1)

    title = re.search(r"\\title\{(.*?)\}\n", body, re.S).group(1)
    authors = re.findall(r"\\IEEEauthorblockN\{(.*?)\}\s*\\IEEEauthorblockA\{(.*?)\\\\\s*\{(.*?)\}\}", body, re.S)
    abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.S).group(1)
    keywords = re.search(r"\\begin\{IEEEkeywords\}(.*?)\\end\{IEEEkeywords\}", body, re.S).group(1)
    main = body.split(BS + "end{IEEEkeywords}", 1)[1]

    out = [f'<div class="note">Aperçu généré sans LaTeX (final-report/apercu.py) — mise en page approchée ; '
           f'le PDF officiel se compile sur Overleaf.</div>',
           f'<h1 class="title">{inline(title, refs, cites)}</h1>', '<div class="authors">']
    for n, a, e in authors:
        out.append(f'<div class="author"><div class="n">{inline(n, refs, cites)}</div>'
                   f'<div class="a">{inline(a, refs, cites)}</div><div>{inline(e, refs, cites)}</div></div>')
    out.append("</div>")
    cols = [f'<div class="abstract"><i class="h">Résumé</i>—{inline(abstract, refs, cites)}</div>',
            f'<div class="kw"><i>Mots-clés</i>—{inline(keywords, refs, cites)}</div>']

    sec = sub = fig = tab = 0
    para: list[str] = []
    first = True

    def flush():
        nonlocal para, first
        text = " ".join(x.strip() for x in para).strip()
        if text:
            cls = ' class="first"' if first else ""
            cols.append(f"<p{cls}>{inline(text, refs, cites)}</p>")
            first = False
        para = []

    lines = main.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        m = re.match(r"\\(section|subsection)\{(.*?)\}", s)
        if m:
            flush()
            if m.group(1) == "section":
                sec += 1
                sub = 0
                cols.append(f"<h2>{ROMAN[sec - 1]}. {inline(m.group(2), refs, cites)}</h2>")
            else:
                sub += 1
                cols.append(f"<h3>{chr(64 + sub)}. {inline(m.group(2), refs, cites)}</h3>")
            first = True
            i += 1
            continue
        env = re.match(r"\\begin\{(figure|table\*?|itemize|enumerate|thebibliography)\}", s)
        if env:
            flush()
            name = env.group(1)
            end = BS + "end{" + name + "}"
            block = []
            i += 1
            while end not in lines[i]:
                block.append(lines[i])
                i += 1
            blk = "\n".join(block)
            cap = re.search(r"\\caption\{(.*?)\}\n\\label", blk, re.S)
            cap = inline(cap.group(1), refs, cites) if cap else ""
            if name == "figure":
                fig += 1
                src = re.search(r"\\input\{(.*?)\}", blk).group(1)
                png = OUT / (Path(src).name + ".png")
                pgf_to_png(HERE / (src + ".tex"), png)
                cols.append(f'<figure><img src="{png.name}"><figcaption>Fig. {fig}.&nbsp; {cap}</figcaption></figure>')
            elif name.startswith("table"):
                tab += 1
                src = re.search(r"\\input\{(.*?)\}", blk)
                tsrc = (HERE / (src.group(1) + ".tex")).read_text(encoding="utf-8") if src else blk
                wide = " wide" if name == "table*" else ""
                cols.append(f'<div class="tblock{wide}"><div class="tcap"><span class="num">Table {ROMAN[tab - 1]}</span>'
                            f'{cap}</div>{tabular_to_html(tsrc, refs, cites)}</div>')
            elif name in ("itemize", "enumerate"):
                items = [x for x in re.split(r"\\item\s*", blk) if x.strip()]
                tag = "ul" if name == "itemize" else "ol"
                cols.append(f"<{tag}>" + "".join(f"<li>{inline(x, refs, cites)}</li>" for x in items) + f"</{tag}>")
            else:
                cols.append("<h2>Références</h2>")
                items = re.findall(r"\\bibitem\{[^}]*\}(.*?)(?=\\bibitem|\Z)", blk, re.S)
                cols.append('<ol class="bib">' + "".join(f"<li>{inline(x, refs, cites)}</li>" for x in items) + "</ol>")
            first = True
            i += 1
            continue
        if not s:
            flush()
        elif not s.startswith(BS + "maketitle"):
            para.append(line)
        i += 1
    flush()

    page = ("<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Aperçu du rapport</title>"
            f"<style>{CSS}</style></head><body>" + "".join(out) + '<div class="cols">' + "\n".join(cols)
            + "</div></body></html>")
    path = OUT / "rapport_apercu.html"
    path.write_text(page, encoding="utf-8")
    return path


def main() -> int:
    page = build()
    edge = next((e for e in EDGE if e.exists()), None)
    if edge is None:
        print(f"HTML écrit : {page} (Edge introuvable : pas de PDF)")
        return 1
    pdf = OUT / "rapport_apercu.pdf"
    subprocess.run([str(edge), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", page.as_uri()], check=True, capture_output=True, timeout=120)
    print(f"HTML : {page}\nPDF  : {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
