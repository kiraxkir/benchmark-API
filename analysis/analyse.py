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
    analysis/tables/*.tex           tables booktabs

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
def tex_table(caption, label, colspec, header, rows, note=None, resize=False) -> str:
    """Table booktabs ; resize=True ramène la table à la largeur de la colonne (\\resizebox)."""
    out = ["% Généré par analysis/analyse.py -- ne pas modifier à la main.",
           "\\begin{table}[htbp]", "\\centering", "\\small",
           f"\\caption{{{caption}}}", f"\\label{{{label}}}"]
    if resize:
        out.append("\\resizebox{\\linewidth}{!}{%")
    out += [f"\\begin{{tabular}}{{{colspec}}}", "\\toprule", header + " \\\\", "\\midrule"]
    out += rows
    out += ["\\bottomrule", "\\end{tabular}" + ("}" if resize else "")]
    if note:
        out.append(f"\\par\\smallskip\\footnotesize {note}")
    out.append("\\end{table}")
    return "\n".join(out) + "\n"


def cell(agg, t, e, v, m, nd=1, ci=False):
    r = agg[(agg.technologie == t) & (agg.endpoint == e) & (agg.utilisateurs == v) & (agg.metrique == m)].iloc[0]
    if ci:
        return f"{fr(r.mediane, nd)} [{fr(r.ic95_bas, nd)}~; {fr(r.ic95_haut, nd)}]"
    return fr(r.mediane, nd)


def table_global(agg, written):
    rows = []
    for t in TECHS:
        for i, v in enumerate(VUS):
            name = f"\\multirow{{{len(VUS)}}}{{*}}{{{TECH_LABEL[t]}}}" if i == 0 else ""
            rows.append(" & ".join([name, str(v), cell(agg, t, "all", v, "req_s"),
                                    cell(agg, t, "all", v, "p50_ms"), cell(agg, t, "all", v, "p95_ms"),
                                    cell(agg, t, "all", v, "p99_ms"), cell(agg, t, "all", v, "cpu_api_moy_pct", 0),
                                    cell(agg, t, "all", v, "ram_api_max_mo", 0),
                                    cell(agg, t, "all", v, "cpu_mysql_moy_pct", 0),
                                    cell(agg, t, "all", v, "efficacite_req_s_par_coeur", 0)]) + " \\\\")
        if t != TECHS[-1]:
            rows.append("\\midrule")
    write_tex("global.tex", tex_table(
        "Résultats globaux (tous endpoints) : médiane sur 30 essais de 15~s",
        "tab:global", "llrrrrrrrr",
        "API & VU & req/s & p50 (ms) & p95 (ms) & p99 (ms) & CPU API (\\%) & RAM (Mo) & CPU MySQL (\\%) & req/s/cœur",
        rows,
        "CPU en \\% d'un cœur (200~\\% = limite de 2 cœurs). Laravel : php-fpm + nginx sommés "
        "(limite 2,5 cœurs). RAM : maximum observé pendant l'essai. Efficacité = débit / (CPU API / 100).",
        resize=True),
        written)


def table_endpoints(agg, written, v=REF_VUS):
    rows = []
    for e in ENDPOINTS:
        rows.append(" & ".join([ENDPOINT_LABEL[e]] + [cell(agg, t, e, v, "p95_ms", ci=True) for t in TECHS]) + " \\\\")
    write_tex("p95_endpoints_50vu.tex", tex_table(
        f"Latence p95 par endpoint à {v}~VU (ms) : médiane [IC~95~\\% de la moyenne] sur 30 essais",
        "tab:p95-endpoints", "l" + "r" * len(TECHS),
        "Endpoint & " + " & ".join(TECH_LABEL[t] for t in TECHS), rows), written)


def table_throughput_endpoints(agg, written, v=REF_VUS):
    rows = []
    for e in ENDPOINTS:
        rows.append(" & ".join([ENDPOINT_LABEL[e]] + [cell(agg, t, e, v, "req_s") for t in TECHS]) + " \\\\")
    write_tex("debit_endpoints_50vu.tex", tex_table(
        f"Débit par endpoint à {v}~VU (req/s, médiane sur 30 essais)",
        "tab:debit-endpoints", "l" + "r" * len(TECHS),
        "Endpoint & " + " & ".join(TECH_LABEL[t] for t in TECHS), rows), written)


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
    write_tex("kruskal_p95_50vu.tex", tex_table(
        f"Kruskal-Wallis sur la latence p95 entre les 4 API à {v}~VU (30 essais par API)",
        "tab:kruskal", "lrrrp{4.2cm}",
        "Endpoint & $H$ & $p$ & $\\varepsilon^2$ & Paires non significatives",
        rows,
        "Paires : tests de Mann-Whitney bilatéraux, correction de Holm sur les 6 paires, "
        f"$\\alpha = {fr(ALPHA, 2).replace(',', '{,}')}$."), written)


def table_startup(dem, ram, img, written):
    rows = []
    for t in TECHS:
        d = dem[dem.technologie == t]["demarrage_ms"]
        if d.empty:
            continue
        lo, hi = ci95(d)
        cells = [TECH_LABEL[t], str(int(d.count())), fr(d.median(), 0), f"[{fr(lo, 0)}~; {fr(hi, 0)}]"]
        cells.append(fr(ram[ram.technologie == t]["ram_repos_mo"].median(), 1) if ram is not None else "--")
        if img is not None and not img[img.technologie == t].empty:
            cells.append(fr(img[img.technologie == t]["taille_mo"].iloc[0], 0))
        else:
            cells.append("--")
        rows.append(" & ".join(cells) + " \\\\")
    write_tex("demarrage.tex", tex_table(
        "Démarrage à froid, RAM au repos et taille des images",
        "tab:demarrage", "lrrrrr",
        "API & $n$ & Démarrage méd. (ms) & IC 95~\\% (ms) & RAM repos (Mo) & Image (Mo)", rows,
        "Démarrage : de \\texttt{docker compose up -d} à la première réponse 200 sur "
        "\\texttt{GET /api/posts?limit=1}. Laravel : php-fpm + nginx."), written)


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
        print("ABSENT : raw-results/demarrage.csv (bench/startup_time.py pas encore lancé) : "
              "figure et table du démarrage non produites.")

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
