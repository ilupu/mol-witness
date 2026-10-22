#!/usr/bin/env python3
"""Raport antedatare: data publicării afișată vs. CreationDate din PDF (din backfill/manifest.csv).
python3 report.py [backfill/manifest.csv] → backfill/raport.md + backfill/raport_hcl.csv"""
import csv, re, sys, statistics as st
from collections import defaultdict
from datetime import datetime, date

src = sys.argv[1] if len(sys.argv) > 1 else "backfill/manifest.csv"
MON = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}

def pdfdate(s):
    """'Tue Feb 18 16:21:07 2020 CET' sau 'D:20200218162107+01'00'' → date"""
    if not s: return None
    m = re.match(r"\w{3} (\w{3})\s+(\d+) [\d:]+ (\d{4})", s)
    if m: return date(int(m[3]), MON[m[1]], int(m[2]))
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", s)
    if m: return date(int(m[1]), int(m[2]), int(m[3]))
    return None

rows = list(csv.DictReader(open(src, encoding="utf-8")))
hcl = [r for r in rows if r["tip"] == "hotarari" and "anexa" not in r["atasament_nume"].lower()]
out = []
for r in hcl:
    pd, pub = pdfdate(r["pdf_CreationDate"]), (date.fromisoformat(r["data_publicarii_site"]) if r["data_publicarii_site"] else None)
    sed = date.fromisoformat(r["sedinta_data"]) if r["sedinta_data"] else None
    lag = (pd - pub).days if pd and pub else None
    out.append({**r, "pdf_date": pd, "pub": pub, "sed": sed, "lag": lag,
                "scanat": "konica" in r["pdf_Producer"].lower() or "bizhub" in r["pdf_Producer"].lower()})

with open("backfill/raport_hcl.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["an", "sedinta", "nr", "data_publicarii_site", "pdf_creat", "intarziere_zile", "scanat", "producer", "fisier", "sha256"])
    for r in sorted(out, key=lambda x: (x["sed"] or date.min, x["doc_numar"])):
        w.writerow([r["sed"].year if r["sed"] else "", r["sed"], r["doc_numar"], r["pub"], r["pdf_date"], r["lag"], r["scanat"], r["pdf_Producer"], r["atasament_nume"], r["sha256"][:16]])

L = ["# Raport: data publicării afișată vs. data creării fișierului PDF", "",
     f"Sursa: `{src}`, {len(rows)} atașamente, din care {len(hcl)} hotărâri (fără anexe).", "",
     "`întârziere` = zile între „Data publicării” afișată în MOL și CreationDate din metadatele PDF. "
     "O valoare > 0 înseamnă că fișierul a fost creat (scanat) DUPĂ data la care site-ul pretinde că a fost publicat — imposibil fizic.", "",
     "## Pe ani", "", "| an | HCL | cu PDF creat după „data publicării” | % | mediană întârziere (zile) | max | pub = data ședinței |", "|---|---|---|---|---|---|---|"]
by = defaultdict(list)
for r in out:
    if r["sed"]: by[r["sed"].year].append(r)
for y in sorted(by):
    g = [r for r in by[y] if r["lag"] is not None]
    late = [r for r in g if r["lag"] > 0]
    same = sum(1 for r in by[y] if r["pub"] and r["sed"] and r["pub"] == r["sed"])
    L.append(f"| {y} | {len(by[y])} | {len(late)} | {100*len(late)/len(g):.0f}% | {st.median([r['lag'] for r in late]) if late else 0:.0f} | {max((r['lag'] for r in late), default=0)} | {same}/{len(by[y])} |")

L += ["", "## Pe ședințe (2025–2026)", "", "| ședință | HCL | „data publicării” afișată | PDF creat (min–max) | întârziere min–max |", "|---|---|---|---|---|"]
bs = defaultdict(list)
for r in out:
    if r["sed"] and r["sed"].year >= 2025: bs[r["sed"]].append(r)
for s in sorted(bs, reverse=True):
    g = [r for r in bs[s] if r["pdf_date"]]
    pubs = sorted({str(r["pub"]) for r in bs[s]})
    lags = [r["lag"] for r in g if r["lag"] is not None]
    L.append(f"| {s} | {len(bs[s])} | {', '.join(pubs)} | {min(r['pdf_date'] for r in g) if g else ''} – {max(r['pdf_date'] for r in g) if g else ''} | {min(lags) if lags else ''} – {max(lags) if lags else ''} |")

top = sorted((r for r in out if r["lag"]), key=lambda r: -r["lag"])[:25]
L += ["", "## Cele mai mari întârzieri", "", "| ședință | HCL nr. | afișat publicat | PDF creat | zile | producer |", "|---|---|---|---|---|---|"]
for r in top:
    L.append(f"| {r['sed']} | {r['doc_numar']} | {r['pub']} | {r['pdf_date']} | {r['lag']} | {r['pdf_Producer'][:30]} |")
nopdf = [r for r in out if not r["pdf_date"]]
L += ["", f"Fără CreationDate utilizabil: {len(nopdf)} fișiere.", "",
      "Notă: pentru PDF-urile generate din Word (nu scanate), CreationDate poate preceda ședința — relevante sunt cele cu întârziere pozitivă, "
      "în special cele scanate (producer Konica Minolta), unde CreationDate = momentul scanării documentului semnat."]
open("backfill/raport.md", "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L[:20])); print("...\n→ backfill/raport.md, backfill/raport_hcl.csv")
