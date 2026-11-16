"""Copie les figures et tables générées par analysis/analyse.py dans final-report/.

Usage : python final-report/sync.py   (après python analysis/analyse.py)

Le dossier final-report/ est ainsi autonome : il peut être zippé et importé tel quel dans
Overleaf (rapport.tex + figures/pgf_*.tex + tables/*.tex).
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent / "analysis"

for sub, pattern in (("figures", "pgf_*.tex"), ("tables", "*.tex")):
    dst = HERE / sub
    dst.mkdir(exist_ok=True)
    for src in sorted((ANALYSIS / sub).glob(pattern)):
        shutil.copy2(src, dst / src.name)
        print(f"{sub}/{src.name}")
