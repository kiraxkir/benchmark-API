"""Analyse des mesures de la campagne (CLAUDE.md §11, FEUILLE_DE_ROUTE.md phase 6).

Usage :
    python analysis/analyse.py

Entrées (lecture seule) : raw-results/mesures.csv, raw-results/demarrage.csv (si présent),
raw-results/ram_repos.csv et raw-results/images.csv (si présents).
Aucune valeur n'est inventée ni complétée : une donnée absente est signalée et la sortie
correspondante n'est pas produite.

Sorties :
    analysis/controle_qualite.txt   essais présents, essais aberrants (erreurs > 1 %)
    analysis/agregats.csv           médiane, moyenne, écart-type, IC 95 % par techno × endpoint × charge
    analysis/kruskal_wallis.csv     Kruskal-Wallis sur p95 + comparaisons par paires (Mann-Whitney, Holm)
    analysis/figures/*.pdf          figures (largeur 3,4 pouces, lisibles en noir et blanc)
    analysis/figures/pgf_*.tex      mêmes données en pgfplots, au format du modèle de rapport
    analysis/tables/*.tex           tabulars booktabs au format du modèle de rapport

Dépendances : analysis/requirements.txt (pandas, matplotlib, scipy).
"""
import itertools
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from scipy import stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw-results"
OUT = ROOT / "analysis"
FIG = OUT / "figures"
TAB = OUT / "tables"

TECHS = ["laravel", "fastapi", "spring", "gin"]
TECH_LABEL = {"laravel": "Laravel", "fastapi": "FastAPI", "spring": "Spring Boot", "gin": "Gin"}
ENDPOINTS = ["list_posts", "create_post", "like", "follow", "comment", "all"]
ENDPOINT_LABEL = {"list_posts": "GET posts", "create_post": "POST posts", "like": "like",
                  "follow": "follow", "comment": "comment", "all": "global"}
VUS = [10, 50, 100]
REF_VUS = 50  # charge de référence pour les comparaisons à charge fixe
N_EXPECTED = 30
ERROR_THRESHOLD_PCT = 1.0
ALPHA = 0.05

# Styles distinguables en noir et blanc : trait + marqueur (courbes), niveau de gris + hachures (barres).
LINE = {"laravel": ("-", "o"), "fastapi": ("--", "s"), "spring": ("-.", "^"), "gin": (":", "D")}
BAR = {"laravel": ("0.3", ""), "fastapi": ("0.55", "////"), "spring": ("0.8", "xxx"), "gin": ("1.0", "....")}

METRICS = ["req_s", "p50_ms", "p95_ms", "p99_ms", "lat_moy_ms", "lat_max_ms", "erreurs_pct",
           "cpu_api_moy_pct", "ram_api_max_mo", "cpu_mysql_moy_pct", "efficacite_req_s_par_coeur"]

FIG_W = 3.4


# --------------------------------------------------------------------------- utilitaires
def fr(x: float, nd: int = 1) -> str:
    """Nombre au format français (virgule décimale, espace fine pour les milliers en LaTeX)."""
    if pd.isna(x):
        return "--"
    s = f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s.replace(" ", "\\,")


def fr_axis(ax, axis: str = "y") -> None:
    fmt = FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def ci95(values: pd.Series) -> tuple[float, float]:
    """IC 95 % de la moyenne (loi de Student)."""
    v = values.dropna().to_numpy(dtype=float)
    n = len(v)
    if n < 2:
        return (np.nan, np.nan)
    m = v.mean()
    h = stats.t.ppf(0.975, n - 1) * v.std(ddof=1) / np.sqrt(n)
    return (m - h, m + h)


def holm(pvalues: list[float]) -> list[float]:
    """Correction de Holm-Bonferroni (p ajustées, monotones, plafonnées à 1)."""
    m = len(pvalues)
    order = np.argsort(pvalues)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvalues[idx])
        adj[idx] = min(1.0, running)
    return adj.tolist()


def setup_matplotlib() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "legend.fontsize": 7,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "axes.grid": True,
        "grid.color": "0.85",
        "grid.linewidth": 0.5,
        "axes.axisbelow": True,
        "pdf.fonttype": 42,
        "hatch.linewidth": 0.4,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
    })


PNG_DIR: Path | None = None  # --png : aperçus PNG (vérification visuelle), hors du dépôt


def save(fig, name: str, written: list[str]) -> None:
    path = FIG / name
    fig.savefig(path)
    if PNG_DIR is not None:
        fig.savefig(PNG_DIR / name.replace(".pdf", ".png"), dpi=200)
    plt.close(fig)
    written.append(str(path.relative_to(ROOT)))


def write_tex(name: str, body: str, written: list[str]) -> None:
    path = TAB / name
    path.write_text(body, encoding="utf-8")
    written.append(str(path.relative_to(ROOT)))


# --------------------------------------------------------------------------- chargement + contrôle
def load_measures() -> pd.DataFrame:
    path = RAW / "mesures.csv"
    if not path.exists():
        raise SystemExit(f"{path} introuvable : aucune analyse possible.")
    df = pd.read_csv(path)
    df["utilisateurs"] = df["utilisateurs"].astype(int)
    # Efficacité : débit par cœur consommé (cpu_api_moy_pct = % d'un cœur, 100 % = 1 cœur).
    df["efficacite_req_s_par_coeur"] = df["req_s"] / (df["cpu_api_moy_pct"] / 100.0)
    return df


def quality_control(df: pd.DataFrame) -> list[str]:
    lines = ["Contrôle qualité de raw-results/mesures.csv", "=" * 44, ""]
    lines.append(f"Lignes : {len(df)} ; essais (lignes endpoint=all) : {(df.endpoint == 'all').sum()}")
    empty = int(df.isna().sum().sum())
    lines.append(f"Cases vides : {empty}")
    dup = df.duplicated(["technologie", "endpoint", "utilisateurs", "essai"]).sum()
    lines.append(f"Doublons (technologie, endpoint, utilisateurs, essai) : {dup}")
    lines.append("")
    lines.append(f"Essais par technologie × charge (attendu : {N_EXPECTED}) :")
    counts = df[df.endpoint == "all"].groupby(["technologie", "utilisateurs"]).size()
    missing = []
    for t in TECHS:
        for v in VUS:
            n = int(counts.get((t, v), 0))
            flag = "" if n == N_EXPECTED else "   <-- INCOMPLET"
            if n != N_EXPECTED:
                missing.append((t, v, n))
            lines.append(f"  {TECH_LABEL[t]:<12} {v:>3} VU : {n}{flag}")
    lines.append("")
    bad = df[df.erreurs_pct > ERROR_THRESHOLD_PCT]
    lines.append(f"Essais aberrants (erreurs > {ERROR_THRESHOLD_PCT:g} %) : {len(bad)} ligne(s)")
    for _, r in bad.iterrows():
        lines.append(f"  {r.technologie} {r.endpoint} {r.utilisateurs} VU essai {r.essai} : {r.erreurs_pct} %")
    nonzero = df[df.erreurs_pct > 0]
    lines.append(f"Lignes avec un taux d'erreur > 0 : {len(nonzero)} ; maximum : {df.erreurs_pct.max():g} %")
    lines.append("Aucun essai n'est supprimé : les essais signalés restent dans l'analyse.")
    lines.append("")
    lines.append(f"Période de mesure : {df.horodatage.min()} -> {df.horodatage.max()}")
    lines.append(f"Requêtes HTTP mesurées au total : {int(df[df.endpoint == 'all'].requetes.sum())}")
    lines.append("")
    # Loi de Little (boucle fermée sans pause, 1 requête par itération) : VU ≈ débit × latence moyenne.
    lines.append("Cohérence (loi de Little) : VU / débit comparé à la latence moyenne mesurée (médianes) :")
    al = df[df.endpoint == "all"].assign(r_little=lambda d: d.utilisateurs / d.req_s * 1000)
    for (t, v), g in al.groupby(["technologie", "utilisateurs"]):
        rl, lm = g.r_little.median(), g.lat_moy_ms.median()
        lines.append(f"  {TECH_LABEL[t]:<12} {v:>3} VU : VU/débit = {rl:6.1f} ms ; latence moyenne = {lm:6.1f} ms"
                     f" ; écart {100 * (rl - lm) / lm:+.1f} %")
    if missing:
        lines.append("ATTENTION : combinaisons incomplètes : " + ", ".join(f"{t}/{v} VU ({n})" for t, v, n in missing))
    return lines


# --------------------------------------------------------------------------- agrégats + tests
def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (t, e, v), g in df.groupby(["technologie", "endpoint", "utilisateurs"]):
        for m in METRICS:
            s = g[m]
            lo, hi = ci95(s)
            rows.append({"technologie": t, "endpoint": e, "utilisateurs": v, "metrique": m, "n": int(s.count()),
                         "mediane": s.median(), "moyenne": s.mean(), "ecart_type": s.std(ddof=1),
                         "ic95_bas": lo, "ic95_haut": hi, "min": s.min(), "max": s.max()})
    agg = pd.DataFrame(rows)
    agg["technologie"] = pd.Categorical(agg["technologie"], TECHS, ordered=True)
    agg["endpoint"] = pd.Categorical(agg["endpoint"], ENDPOINTS, ordered=True)
    return agg.sort_values(["metrique", "endpoint", "technologie", "utilisateurs"]).reset_index(drop=True)


def kruskal_tests(df: pd.DataFrame, metric: str = "p95_ms") -> pd.DataFrame:
    """Kruskal-Wallis entre technologies, par endpoint et par charge ; paires : Mann-Whitney + Holm."""
    rows = []
    for v in VUS:
        for e in ENDPOINTS:
            sub = df[(df.endpoint == e) & (df.utilisateurs == v)]
            groups = {t: sub[sub.technologie == t][metric].to_numpy() for t in TECHS}
            if any(len(g) == 0 for g in groups.values()):
                continue
            h, p = stats.kruskal(*groups.values())
            n, k = sum(len(g) for g in groups.values()), len(groups)
            eps2 = (h - k + 1) / (n - k)  # epsilon² (taille d'effet)
            rows.append({"utilisateurs": v, "endpoint": e, "test": "kruskal-wallis", "paire": "toutes",
                         "statistique": h, "p": p, "p_holm": np.nan, "taille_effet": eps2,
                         "significatif": p < ALPHA})
            pairs = list(itertools.combinations(TECHS, 2))
            raw = []
            for a, b in pairs:
                u, pu = stats.mannwhitneyu(groups[a], groups[b], alternative="two-sided")
                # r = probabilité de supériorité P(a > b) (effet commun du langage).
                raw.append((u, pu, u / (len(groups[a]) * len(groups[b]))))
            adj = holm([r[1] for r in raw])
            for (a, b), (u, pu, cl), pa in zip(pairs, raw, adj):
                rows.append({"utilisateurs": v, "endpoint": e, "test": "mann-whitney", "paire": f"{a}-{b}",
                             "statistique": u, "p": pu, "p_holm": pa, "taille_effet": cl,
                             "significatif": pa < ALPHA})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- figures
def get(agg: pd.DataFrame, metric: str, endpoint: str = "all") -> pd.DataFrame:
    return agg[(agg.metrique == metric) & (agg.endpoint == endpoint)]


def grouped_bars(ax, agg, metric, categories, cat_key, ylabel, stat="moyenne", with_ci=True):
    width = 0.8 / len(TECHS)
    x = np.arange(len(categories))
    for i, t in enumerate(TECHS):
        vals, err_lo, err_hi = [], [], []
        for c in categories:
            r = agg[(agg.metrique == metric) & (agg.technologie == t) & (agg[cat_key] == c)]
            r = r.iloc[0]
            vals.append(r[stat])
            err_lo.append(r[stat] - r.ic95_bas)
            err_hi.append(r.ic95_haut - r[stat])
        face, hatch = BAR[t]
        ax.bar(x + (i - (len(TECHS) - 1) / 2) * width, vals, width, color=face, hatch=hatch,
               edgecolor="black", linewidth=0.5, label=TECH_LABEL[t],
               yerr=[err_lo, err_hi] if with_ci else None, capsize=1.5,
               error_kw={"elinewidth": 0.6, "capthick": 0.6})
    ax.set_xticks(x)
    ax.set_ylabel(ylabel)
    fr_axis(ax)
    return x


def fig_p95_endpoints(agg, written):
    sub = agg[agg.utilisateurs == REF_VUS]
    fig, ax = plt.subplots(figsize=(FIG_W, 2.4))
    x = grouped_bars(ax, sub, "p95_ms", ENDPOINTS, "endpoint", "Latence p95 (ms)")
    ax.set_xticklabels([ENDPOINT_LABEL[e] for e in ENDPOINTS], rotation=20, ha="right")
    ax.set_xlabel(f"Endpoint ({REF_VUS} VU)")
    ax.legend(ncol=2, frameon=False, loc="upper left")
    ax.set_ylim(0, ax.get_ylim()[1] * 1.25)
    save(fig, "p95_par_techno_50vu.pdf", written)


def fig_metric_by_vu_bars(agg, metric, ylabel, name, written, endpoint="all"):
    sub = agg[agg.endpoint == endpoint]
    fig, ax = plt.subplots(figsize=(FIG_W, 2.2))
    grouped_bars(ax, sub, metric, VUS, "utilisateurs", ylabel)
    ax.set_xticklabels([str(v) for v in VUS])
    ax.set_xlabel("Utilisateurs virtuels simultanés (VU)")
    ax.legend(ncol=2, frameon=False, loc="upper left")
    ax.set_ylim(0, ax.get_ylim()[1] * 1.3)
    save(fig, name, written)


def fig_lines_vs_vu(agg, metric, ylabel, name, written, endpoint="all"):
    sub = get(agg, metric, endpoint)
    fig, ax = plt.subplots(figsize=(FIG_W, 2.2))
    for t in TECHS:
        r = sub[sub.technologie == t].sort_values("utilisateurs")
        ls, mk = LINE[t]
        ax.errorbar(r.utilisateurs, r.mediane, yerr=[r.mediane - r.ic95_bas.clip(upper=r.mediane),
                                                     (r.ic95_haut - r.mediane).clip(lower=0)],
                    linestyle=ls, marker=mk, markersize=3.5, color="black", linewidth=0.9,
                    markerfacecolor="white", capsize=1.5, elinewidth=0.5, label=TECH_LABEL[t])
    ax.set_xticks(VUS)
    ax.set_xlabel("Utilisateurs virtuels simultanés (VU)")
    ax.set_ylabel(ylabel)
    fr_axis(ax)
    ax.legend(frameon=False)
    save(fig, name, written)


def style_boxes(bp) -> None:
    """Boîtes en gris clair + hachures ; médiane épaisse noire, toujours visible."""
    for patch, t in zip(bp["boxes"], TECHS):
        face, hatch = BAR[t]
        patch.set(facecolor="0.92" if face == "0.3" else face, hatch=hatch, edgecolor="black", linewidth=0.6)
    for med in bp["medians"]:
        med.set(color="black", linewidth=1.6)


def fig_boxplot_p95(df, written, endpoint="all"):
    sub = df[(df.endpoint == endpoint) & (df.utilisateurs == REF_VUS)]
    data = [sub[sub.technologie == t]["p95_ms"].to_numpy() for t in TECHS]
    fig, ax = plt.subplots(figsize=(FIG_W, 2.2))
    bp = ax.boxplot(data, patch_artist=True, widths=0.55,
                    medianprops={"color": "black", "linewidth": 1.0},
                    flierprops={"marker": "o", "markersize": 2.5, "markerfacecolor": "none"})
    style_boxes(bp)
    ax.set_xticks(range(1, len(TECHS) + 1), [TECH_LABEL[t] for t in TECHS])
    ax.set_xlabel(f"API ({REF_VUS} VU, 30 essais)")
    ax.set_ylabel("Latence p95 globale (ms)")
    fr_axis(ax)
    save(fig, "boxplot_p95_50vu.pdf", written)


def fig_errors(agg, written):
    sub = get(agg, "erreurs_pct")
    fig, ax = plt.subplots(figsize=(FIG_W, 2.0))
    grouped_bars(ax, agg[agg.endpoint == "all"], "erreurs_pct", VUS, "utilisateurs",
                 "Taux d'erreur moyen (%)", with_ci=False)
    ax.set_xticklabels([str(v) for v in VUS])
    ax.set_xlabel("Utilisateurs virtuels simultanés (VU)")
    if float(sub.moyenne.max()) == 0.0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.35, "0 % d'erreur sur les 360 essais", transform=ax.transAxes,
                ha="center", va="center", fontsize=8)
    ax.legend(ncol=2, frameon=False, loc="upper left")
    save(fig, "taux_erreur.pdf", written)


def fig_startup(dem, written):
    fig, ax = plt.subplots(figsize=(FIG_W, 2.2))
    data = [dem[dem.technologie == t]["demarrage_ms"].to_numpy() / 1000 for t in TECHS]
    bp = ax.boxplot(data, patch_artist=True, widths=0.55, medianprops={"color": "black"},
                    flierprops={"marker": "o", "markersize": 2.5, "markerfacecolor": "none"})
    style_boxes(bp)
    ax.set_xticks(range(1, len(TECHS) + 1), [TECH_LABEL[t] for t in TECHS])
    ax.set_xlabel("API (30 démarrages)")
    ax.set_ylabel("Démarrage à froid (s)")
    fr_axis(ax)
    save(fig, "demarrage.pdf", written)


# --------------------------------------------------------------------------- tables LaTeX
# Format du modèle de rapport (TP2 MRI model Rapport.tex) : seul le tabular est généré
# (@{}...@{}, booktabs, en-têtes en gras, ligne des unités) ; le rapport l'entoure de
# \begin{table} \caption \label \centering, comme dans le modèle.
def tex_tabular(colspec, header, units, rows) -> str:
    out = ["% Généré par analysis/analyse.py -- ne pas modifier à la main.",
           f"\\begin{{tabular}}{{@{{}}{colspec}@{{}}}}", "\\toprule",
           " & ".join(f"\\textbf{{{h}}}" if h else "" for h in header) + " \\\\"]
    if units:
        out.append(" & ".join(units) + " \\\\")
    out += ["\\midrule", *rows, "\\bottomrule", "\\end{tabular}"]
    return "\n".join(out) + "\n"


def row_of(agg, t, e, v, m):
    return agg[(agg.technologie == t) & (agg.endpoint == e) & (agg.utilisateurs == v) & (agg.metrique == m)].iloc[0]


def cell(agg, t, e, v, m, nd=1, ci=False):
    """Médiane ; avec ci=True : moyenne ± demi-largeur de l'IC 95 % (Student)."""
    r = row_of(agg, t, e, v, m)
    if ci:
        return f"{fr(r.moyenne, nd)} $\\pm$ {fr((r.ic95_haut - r.ic95_bas) / 2, nd)}"
    return fr(r.mediane, nd)


def table_global(agg, written):
    """Synthèse des mesures (médianes sur 30 essais), toutes charges : table* dans le rapport."""
    rows = []
    for t in TECHS:
        for i, v in enumerate(VUS):
            rows.append(" & ".join([TECH_LABEL[t] if i == 0 else "", str(v),
                                    cell(agg, t, "all", v, "req_s"), cell(agg, t, "all", v, "p50_ms"),
                                    cell(agg, t, "all", v, "p95_ms"), cell(agg, t, "all", v, "p99_ms"),
                                    cell(agg, t, "all", v, "cpu_api_moy_pct", 0),
                                    cell(agg, t, "all", v, "ram_api_max_mo", 0),
                                    cell(agg, t, "all", v, "cpu_mysql_moy_pct", 0),
                                    cell(agg, t, "all", v, "efficacite_req_s_par_coeur", 0)]) + " \\\\")
        if t != TECHS[-1]:
            rows.append("\\midrule")
    write_tex("global.tex", tex_tabular(
        "lccccccccc",
        ["Technologie", "Charge", "Débit", "p50", "p95", "p99", "CPU API", "RAM API", "CPU MySQL", "Efficacité"],
        ["", "VU", "req/s", "ms", "ms", "ms", "\\%", "Mo", "\\%", "req/s par cœur"], rows), written)


def table_endpoints(agg, written, v=REF_VUS):
    rows = [" & ".join([ENDPOINT_LABEL[e]] + [cell(agg, t, e, v, "p95_ms", ci=True) for t in TECHS]) + " \\\\"
            for e in ENDPOINTS]
    write_tex("p95_endpoints_50vu.tex", tex_tabular(
        "l" + "c" * len(TECHS), ["Route"] + [TECH_LABEL[t] for t in TECHS],
        [""] + ["ms"] * len(TECHS), rows), written)


def table_throughput_endpoints(agg, written, v=REF_VUS):
    rows = [" & ".join([ENDPOINT_LABEL[e]] + [cell(agg, t, e, v, "req_s") for t in TECHS]) + " \\\\"
            for e in ENDPOINTS]
    write_tex("debit_endpoints_50vu.tex", tex_tabular(
        "l" + "c" * len(TECHS), ["Route"] + [TECH_LABEL[t] for t in TECHS],
        [""] + ["req/s"] * len(TECHS), rows), written)


def fmt_p(p: float) -> str:
    return "$<10^{-6}$" if p < 1e-6 else fr(p, 4)


def table_kruskal(kw, written, v=REF_VUS):
    sub = kw[(kw.utilisateurs == v)]
    rows = []
    for e in ENDPOINTS:
        k = sub[(sub.endpoint == e) & (sub.test == "kruskal-wallis")]
        if k.empty:
            continue
        k = k.iloc[0]
        pairs = sub[(sub.endpoint == e) & (sub.test == "mann-whitney") & (~sub.significatif)]
        ns = ", ".join(" / ".join(TECH_LABEL[x] for x in p.split("-")) for p in pairs.paire) or "aucune"
        rows.append(f"{ENDPOINT_LABEL[e]} & {fr(k.statistique, 1)} & {fmt_p(k.p)} & {fr(k.taille_effet, 2)} & {ns} \\\\")
    write_tex("kruskal_p95_50vu.tex", tex_tabular(
        "lcccp{2.9cm}", ["Route", "$H$", "$p$", "$\\varepsilon^2$", "Paires non signif."], None, rows), written)


def table_startup(dem, ram, img, written):
    rows = []
    for t in TECHS:
        d = dem[dem.technologie == t]["demarrage_ms"]
        if d.empty:
            continue
        lo, hi = ci95(d)
        cells = [TECH_LABEL[t], fr(d.median(), 0), f"{fr(d.mean(), 0)} $\\pm$ {fr((hi - lo) / 2, 0)}"]
        cells.append(fr(ram[ram.technologie == t]["ram_repos_mo"].median(), 1) if ram is not None else "--")
        if img is not None and not img[img.technologie == t].empty:
            cells.append(fr(img[img.technologie == t]["taille_mo"].iloc[0], 0))
        else:
            cells.append("--")
        rows.append(" & ".join(cells) + " \\\\")
    write_tex("demarrage.tex", tex_tabular(
        "lcccc", ["Technologie", "Médiane", "Moyenne", "RAM repos", "Image"],
        ["", "ms", "ms $\\pm$ IC 95~\\%", "Mo", "Mo"], rows), written)


# --------------------------------------------------------------------------- figures pgfplots
# Mêmes réglages que les exemples du modèle de rapport : width=\columnwidth, height=5cm,
# barres gris clair à contour noir avec valeurs affichées, courbes noires distinguées par
# style de trait + marqueur. Seul le tikzpicture est généré ; le rapport l'entoure de
# \begin{figure} ... \caption ... \label, comme dans le modèle. Valeurs : raw-results/ via agrégats.
PGF_CURVE = {"laravel": "black, thick, mark=*",
             "fastapi": "black, thick, dashed, mark=square*",
             "spring": "black, thick, dotted, mark=triangle*",
             "gin": "gray, thick, dashdotted, mark=diamond*"}
PGF_FILL = {"laravel": "gray!80", "fastapi": "gray!50", "spring": "gray!20", "gin": "white"}
PGF_COMMON = ["tick label style={font=\\footnotesize}", "label style={font=\\footnotesize}"]


def num(x: float, nd: int = 2) -> str:
    """Nombre pour pgfplots (point décimal ; l'affichage est francisé par pgf)."""
    return f"{x:.{nd}f}"


def pgf_picture(options: list[str], plots: list[str]) -> str:
    out = ["% Généré par analysis/analyse.py -- ne pas modifier à la main.",
           "\\begin{tikzpicture}", "\\begin{axis}["]
    out.append(",\n".join("  " + o for o in options) + "]")
    out += plots
    out += ["\\end{axis}", "\\end{tikzpicture}"]
    return "\n".join(out) + "\n"


def pgf_curves(agg, metric, ylabel, name, written, legend="north west", ymax=None):
    """Courbes métrique vs charge : moyenne ± IC 95 % (barres d'erreur), une courbe par techno."""
    sub = get(agg, metric)
    plots = []
    for t in TECHS:
        r = sub[sub.technologie == t].sort_values("utilisateurs")
        pts = " ".join(f"({int(v)},{num(m)}) +- (0,{num((hi - lo) / 2)})"
                       for v, m, lo, hi in zip(r.utilisateurs, r.moyenne, r.ic95_bas, r.ic95_haut))
        plots.append(f"\\addplot[{PGF_CURVE[t]}, error bars/.cd, y dir=both, y explicit]\n  coordinates {{{pts}}};")
    plots.append("\\legend{" + ", ".join(TECH_LABEL[t] for t in TECHS) + "}")
    anchor = {"north west": "at={(0.03,0.97)}, anchor=north west",
              "south east": "at={(0.97,0.03)}, anchor=south east",
              "south west": "at={(0.03,0.03)}, anchor=south west"}[legend]
    opts = ["width=\\columnwidth, height=5cm",
            "xlabel={Utilisateurs virtuels simultanés (VU)}", f"ylabel={{{ylabel}}}",
            "xtick={10,50,100}", *PGF_COMMON,
            f"legend style={{font=\\scriptsize, {anchor}}}", "ymin=0"]
    if ymax is not None:
        opts.append(f"ymax={ymax}")
    write_tex_fig(name, pgf_picture(opts, plots), written)


def pgf_bars(values: dict, ylabel, name, written, precision=0):
    """Barres simples par technologie (style du premier exemple du modèle)."""
    coords = " ".join(f"({TECH_LABEL[t]},{num(values[t])})" for t in TECHS)
    opts = ["width=\\columnwidth, height=5cm",
            "ybar, bar width=12pt, ymin=0",
            "symbolic x coords={" + ",".join(TECH_LABEL[t] for t in TECHS) + "}",
            "xtick=data", f"ylabel={{{ylabel}}}",
            f"nodes near coords={{\\pgfmathprintnumber[fixed,precision={precision}]{{\\pgfplotspointmeta}}}}",
            "nodes near coords style={font=\\scriptsize}", *PGF_COMMON,
            "enlarge x limits=0.18", "enlarge y limits={upper, value=0.15}"]
    write_tex_fig(name, pgf_picture(opts, [f"\\addplot[fill=gray!40, draw=black] coordinates\n  {{{coords}}};"]),
                  written)


def pgf_routes(agg, written, v=REF_VUS):
    """Barres groupées : p95 par route à charge fixe, moyenne ± IC 95 %."""
    labels = [ENDPOINT_LABEL[e] for e in ENDPOINTS]
    plots = []
    for t in TECHS:
        pts = []
        for e in ENDPOINTS:
            r = row_of(agg, t, e, v, "p95_ms")
            pts.append(f"({ENDPOINT_LABEL[e]},{num(r.moyenne)}) +- (0,{num((r.ic95_haut - r.ic95_bas) / 2)})")
        plots.append(f"\\addplot[fill={PGF_FILL[t]}, draw=black, error bars/.cd, y dir=both, y explicit]\n"
                     f"  coordinates {{{' '.join(pts)}}};")
    plots.append("\\legend{" + ", ".join(TECH_LABEL[t] for t in TECHS) + "}")
    opts = ["width=\\columnwidth, height=5cm",
            "ybar, bar width=4pt, ymin=0",
            "symbolic x coords={" + ",".join(labels) + "}",
            "xtick=data", "x tick label style={rotate=30, anchor=east}",
            f"ylabel={{Latence p95 (ms), {v}~VU}}", *PGF_COMMON,
            "legend style={font=\\scriptsize, at={(0.5,1.03)}, anchor=south, legend columns=4}",
            "enlarge x limits=0.1", "enlarge y limits={upper, value=0.1}"]
    write_tex_fig("pgf_p95_routes_50vu.tex", pgf_picture(opts, plots), written)


def pgf_boxplot(df, written, v=REF_VUS):
    """Boîtes à moustaches préparées (quartiles calculés ici ; moustaches = min et max)."""
    sub = df[(df.endpoint == "all") & (df.utilisateurs == v)]
    plots = []
    for i, t in enumerate(TECHS, start=1):
        x = sub[sub.technologie == t]["p95_ms"]
        q1, med, q3 = x.quantile([0.25, 0.5, 0.75])
        plots.append(f"\\addplot+[boxplot prepared={{draw position={i}, lower whisker={num(x.min())}, "
                     f"lower quartile={num(q1)}, median={num(med)}, upper quartile={num(q3)}, "
                     f"upper whisker={num(x.max())}}}, draw=black, fill=gray!40, solid] coordinates {{}};")
    opts = ["width=\\columnwidth, height=5cm", "boxplot/draw direction=y",
            "xtick={1,2,3,4}", "xticklabels={" + ",".join(TECH_LABEL[t] for t in TECHS) + "}",
            f"ylabel={{Latence p95 globale (ms), {v}~VU}}", *PGF_COMMON]
    write_tex_fig("pgf_boxplot_p95_50vu.tex", pgf_picture(opts, plots), written)


def write_tex_fig(name, body, written):
    path = FIG / name
    path.write_text(body, encoding="utf-8")
    written.append(str(path.relative_to(ROOT)))


def pgf_figures(df, agg, dem, written):
    pgf_curves(agg, "req_s", "Débit global (req/s)", "pgf_debit_vs_vu.tex", written, legend="south east")
    pgf_curves(agg, "p95_ms", "Latence p95 globale (ms)", "pgf_p95_vs_vu.tex", written)
    pgf_curves(agg, "cpu_mysql_moy_pct", "CPU moyen de MySQL (\\%)", "pgf_cpu_mysql_vs_vu.tex", written,
               legend="south east", ymax=250)
    pgf_routes(agg, written)
    pgf_boxplot(df, written)
    at = {t: row_of(agg, t, "all", REF_VUS, "cpu_api_moy_pct").moyenne for t in TECHS}
    pgf_bars(at, f"CPU moyen de l'API (\\%), {REF_VUS}~VU", "pgf_cpu_api_50vu.tex", written)
    at = {t: row_of(agg, t, "all", REF_VUS, "ram_api_max_mo").moyenne for t in TECHS}
    pgf_bars(at, f"RAM maximale de l'API (Mo), {REF_VUS}~VU", "pgf_ram_api_50vu.tex", written)
    if dem is not None:
        med = {t: dem[dem.technologie == t]["demarrage_ms"].median() for t in TECHS}
        pgf_bars(med, "Démarrage à froid (ms)", "pgf_demarrage.tex", written)


# --------------------------------------------------------------------------- main
def main() -> int:
    global PNG_DIR
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--png", metavar="DOSSIER", help="écrit aussi un aperçu PNG de chaque figure dans DOSSIER")
    args = ap.parse_args()
    if args.png:
        PNG_DIR = Path(args.png)
        PNG_DIR.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(errors="replace")
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    setup_matplotlib()
    written: list[str] = []

    df = load_measures()
    qc = quality_control(df)
    (OUT / "controle_qualite.txt").write_text("\n".join(qc) + "\n", encoding="utf-8")
    written.append("analysis/controle_qualite.txt")
    print("\n".join(qc))
    print()

    agg = aggregate(df)
    agg.to_csv(OUT / "agregats.csv", index=False, float_format="%.4f")
    written.append("analysis/agregats.csv")

    kw = kruskal_tests(df)
    kw.to_csv(OUT / "kruskal_wallis.csv", index=False, float_format="%.6g")
    written.append("analysis/kruskal_wallis.csv")

    # Figures demandées (CLAUDE.md §11).
    fig_p95_endpoints(agg, written)
    fig_lines_vs_vu(agg, "req_s", "Débit global (req/s)", "debit_vs_vu.pdf", written)
    fig_lines_vs_vu(agg, "p95_ms", "Latence p95 globale (ms)", "p95_vs_vu.pdf", written)
    fig_boxplot_p95(df, written)
    fig_metric_by_vu_bars(agg, "cpu_api_moy_pct", "CPU API moyen (%)", "cpu_api.pdf", written)
    fig_metric_by_vu_bars(agg, "ram_api_max_mo", "RAM API maximale (Mo)", "ram_api.pdf", written)
    fig_metric_by_vu_bars(agg, "cpu_mysql_moy_pct", "CPU MySQL moyen (%)", "cpu_mysql.pdf", written)
    fig_metric_by_vu_bars(agg, "efficacite_req_s_par_coeur", "Efficacité (req/s par cœur)",
                          "efficacite.pdf", written)
    fig_errors(agg, written)

    table_global(agg, written)
    table_endpoints(agg, written)
    table_throughput_endpoints(agg, written)
    table_kruskal(kw, written)

    dem_path = RAW / "demarrage.csv"
    if dem_path.exists():
        dem = pd.read_csv(dem_path)
        ram = pd.read_csv(RAW / "ram_repos.csv") if (RAW / "ram_repos.csv").exists() else None
        img = pd.read_csv(RAW / "images.csv") if (RAW / "images.csv").exists() else None
        fig_startup(dem, written)
        table_startup(dem, ram, img, written)
    else:
        dem = None
        print("ABSENT : raw-results/demarrage.csv (bench/startup_time.py pas encore lancé) : "
              "figure et table du démarrage non produites.")

    # Figures pgfplots pour le rapport (style du modèle).
    pgf_figures(df, agg, dem, written)

    # Résumé des tests à la charge de référence.
    print(f"\nKruskal-Wallis sur p95 à {REF_VUS} VU :")
    for _, r in kw[(kw.utilisateurs == REF_VUS) & (kw.test == "kruskal-wallis")].iterrows():
        ns = kw[(kw.utilisateurs == REF_VUS) & (kw.endpoint == r.endpoint) & (kw.test == "mann-whitney")
                & (~kw.significatif)].paire.tolist()
        print(f"  {r.endpoint:<12} H = {r.statistique:7.2f}  p = {r.p:.3g}  eps² = {r.taille_effet:.2f}  "
              f"paires non significatives : {', '.join(ns) or 'aucune'}")
    print("\nFichiers écrits :")
    for w in written:
        print("  " + w.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
