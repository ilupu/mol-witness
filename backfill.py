#!/usr/bin/env python3
"""Backfill complet MOL Miroslava (CityOn/ABP): toate ședințele + hotărâri/proiecte/prezență, registrele pe ani,
atașamentele fiecărui document, PDF-urile (local), manifest CSV cu sha256 + metadata PDF.
Rulare:  python3 backfill.py [--out backfill] [--years 2019-2026] [--no-pdf]
Reluabil: ce există pe disc nu se mai descarcă."""
import argparse, csv, hashlib, http.cookiejar, json, os, subprocess, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://miroslava.cityon.ro"
UA = "mol-witness-backfill/1.0 (+https://github.com/ilupu/mol-witness)"
EP = {"hotarari": "/api/services/app/SedintaMol/IncarcaListaHotarari",
      "proiecte": "/api/services/app/ListaProiecteDeHotarare/ListaProiecteByIdSedinta",
      "prezenta": "/api/services/app/PrezentaSedinta/IncarcaPrezentaByIdSedinta"}
REG = {"proiecte": ("/api/services/app/RegistruMonitorulOficialLocal/IncarcaRegistruProiecte", 7, "/MonitorulOficialLocal/RegistruProiecte"),
       "hotarari": ("/api/services/app/RegistruMonitorulOficialLocal/IncarcaRegistruHotarari", 6, "/MonitorulOficialLocal/RegistruHotarari"),
       "pv":       ("/api/services/app/RegistruMonitorulOficialLocal/IncarcaRegistruProceseVerbale", 6, "/MonitorulOficialLocal/RegistruProceseVerbale")}
LIST_PAGE = "/MonitorulOficialLocal/HotararileAutoritatiiDeliberative"
LIST_EP = "/api/services/app/SedintaMol/IncarcaListaSedinteMol"
ATT_EP = "/api/services/app/AtasamentMol/IncarcaListaAtasamentePerDocument"
SLEEP = 1.0

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def xsrf(): return next((c.value for c in jar if c.name == "XSRF-TOKEN"), "")

def get(url):
    with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
        return r.read()

def post(path, body, referer):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest", "X-XSRF-TOKEN": xsrf(), "Origin": BASE, "Referer": BASE + referer})
    for attempt in range(4):
        try:
            with opener.open(req, timeout=120) as r:
                time.sleep(SLEEP); return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print(f"  ! {path} {e} (retry {attempt+1})", file=sys.stderr); time.sleep(5 * (attempt + 1))
    raise SystemExit(f"abandon: {path}")

def items_of(j):
    res = j.get("result", j)
    if isinstance(res, dict):
        for k in ("items", "lista", "data"):
            if isinstance(res.get(k), list): return res[k], res.get("totalCount")
        return [], None
    return (res if isinstance(res, list) else []), None

def paged(path, body, referer, page=200):
    out, skip = [], 0
    while True:
        j = post(path, {**body, "maxResultCount": page, "skipCount": skip}, referer)
        it, total = items_of(j); out += it; skip += page
        if not it or len(it) < page or (total is not None and skip >= total): return out

def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=1)

def attachments(doc_id, out):
    """XHR → [(idAtasament, denumire)]; răspunsul brut se păstrează ca dovadă document→fișier."""
    fn = f"{out}/atasamente/{doc_id}.json"
    if os.path.exists(fn): j = json.load(open(fn, encoding="utf-8"))
    else:
        j = post(ATT_EP, {"idDocument": str(doc_id), "maxResultCount": 100, "skipCount": 0},
                 f"/MonitorulOficialLocal/FisaDocumentGeneral/{doc_id}")
        save(fn, j)
    items, _ = items_of(j)
    return [(it["idAtasament"], it.get("denumireAtasament", "")) for it in items if it.get("idAtasament")]

def pdf_meta(fn):
    try:
        out = subprocess.run(["pdfinfo", fn], capture_output=True, text=True, timeout=30).stdout
        return {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in out.splitlines() if ":" in l)}
    except Exception:
        try:
            from pypdf import PdfReader
            m = PdfReader(fn).metadata or {}
            return {"CreationDate": str(m.get("/CreationDate", "")), "ModDate": str(m.get("/ModDate", "")),
                    "Producer": str(m.get("/Producer", "")), "Title": str(m.get("/Title", ""))}
        except Exception as e:
            return {"error": str(e)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backfill"); ap.add_argument("--years", default="2019-2026")
    ap.add_argument("--no-pdf", action="store_true"); a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    out = a.out; pdfdir = os.path.join(out, "pdf"); os.makedirs(pdfdir, exist_ok=True)
    get(BASE + LIST_PAGE)  # cookie-uri

    # 1. ședințele
    if os.path.exists(f"{out}/sedinte.json"):
        sed = json.load(open(f"{out}/sedinte.json", encoding="utf-8"))
    else:
        sed = paged(LIST_EP, {"filter": "", "deLaData": "", "panaLaData": ""}, LIST_PAGE)
        save(f"{out}/sedinte.json", sed)
    print(f"sedinte: {len(sed)}")

    # 2. registrele pe ani
    for y in range(y0, y1 + 1):
        for name, (path, cat, ref) in REG.items():
            fn = f"{out}/registre/{y}/{name}.json"
            if os.path.exists(fn): continue
            rows = paged(path, {"filter": "", "deLaData": "", "panaLaData": "", "an": str(y), "idCategorieDocument": cat}, ref)
            save(fn, rows); print(f"registru {name} {y}: {len(rows)}")

    # 3. per ședință
    docs = []
    for s in sed:
        sid = str(s.get("id") or s.get("idSedinta") or "")
        if not sid: continue
        label = next((str(v)[:10] for k, v in s.items() if "data" in k.lower() and v), "")
        d = f"{out}/sedinte/{sid}"
        if not os.path.exists(f"{d}/prezenta.json"):
            get(f"{BASE}/MonitorulOficialLocal/Sedinta/{sid}")
            for tip, path in EP.items():
                save(f"{d}/{tip}.json", paged(path, {"idSedinta": sid}, f"/MonitorulOficialLocal/Sedinta/{sid}"))
            print(f"sedinta {sid} {label}: ok")
        for tip in ("hotarari", "proiecte"):
            for rec in json.load(open(f"{d}/{tip}.json", encoding="utf-8")):
                docs.append((sid, label, tip, rec))

    # 4. atașamente + PDF + manifest
    man_path = f"{out}/manifest.csv"
    seen = {r["file_id"] for r in csv.DictReader(open(man_path, encoding="utf-8"))} if os.path.exists(man_path) else set()
    new = not os.path.exists(man_path)
    with open(man_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["sedinta_id", "sedinta_data", "tip", "doc_numar", "doc_titlu", "data_publicarii_site",
                            "doc_id", "atasament_nume", "file_id", "bytes", "sha256",
                            "pdf_CreationDate", "pdf_ModDate", "pdf_Producer", "pdf_Title", "downloaded_at_utc"])
        for sid, label, tip, rec in docs:
            doc_id = rec.get("id")
            if not doc_id: continue
            numar, titlu = rec.get("numar", ""), rec.get("titlu", "")
            datap = str(rec.get("dataPublicare") or "")[:10]
            atts = attachments(doc_id, out)
            if not atts: print(f"  {tip} {numar} doc {doc_id}: 0 atasamente")
            for v, nume in atts:
                if v in seen: continue
                seen.add(v)
                fn = os.path.join(pdfdir, v + ".pdf")
                meta, size, sha, when = {}, "", "", ""
                if not a.no_pdf:
                    if not os.path.exists(fn):
                        try:
                            b = get(f"{BASE}/File/DownloadBinaryFile/{v}"); open(fn, "wb").write(b); time.sleep(SLEEP)
                        except Exception as e:
                            print(f"  ! {v}: {e}", file=sys.stderr); continue
                    b = open(fn, "rb").read(); size, sha = len(b), hashlib.sha256(b).hexdigest()
                    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    meta = pdf_meta(fn) if b[:4] == b"%PDF" else {"error": "not a PDF"}
                w.writerow([sid, label, tip, numar, titlu, datap, doc_id, nume, v, size, sha,
                            meta.get("CreationDate", ""), meta.get("ModDate", ""), meta.get("Producer", ""), meta.get("Title", ""), when])
                f.flush()
                print(f"  {tip} {numar or '?'} {nume or v} {meta.get('CreationDate', '')}")
    print("done:", man_path)

if __name__ == "__main__":
    main()
