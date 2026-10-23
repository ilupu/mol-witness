#!/usr/bin/env python3
"""Martor pentru Monitorul Oficial Local (CityOn/ABP).
Pentru fiecare ședință: GET pagina (primește cookie-urile ABP + XSRF), apoi POST pe endpoint-urile JSON.
Fiecare răspuns brut + metadate e scris prin API-ul GitHub (commit server-side, semnat de GitHub)."""
import base64, hashlib, json, os, re, sys, time, urllib.request, urllib.error, http.cookiejar
from datetime import datetime, timezone, timedelta

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
RUN = os.environ.get("GITHUB_RUN_ID", "local")
API = f"https://api.github.com/repos/{REPO}/contents/"
UA = f"mol-witness/1.0 (+https://github.com/{REPO})"
RUNS_DIR = "data/_runs"   # un fișier per rulare: mod (full/recent), ce s-a verificat, ce era neschimbat
RUNLOG = {"github_run_id": RUN, "github_run_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
          "started_at_utc": datetime.now(timezone.utc).isoformat(), "mode": None,
          "sedinte": {}, "recorded": [], "unchanged": []}

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def do(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"User-Agent": UA, **(headers or {})})
    t0 = datetime.now(timezone.utc)
    try:
        with opener.open(req, timeout=60) as r:
            return t0, r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return t0, e.code, dict(e.headers), e.read()

def xsrf():
    return next((c.value for c in jar if c.name == "XSRF-TOKEN"), "")

GQL = "https://api.github.com/graphql"
_head = {"oid": None, "branch": None}

def gql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(GQL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {TOKEN}", "User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub GraphQL {e.code}: {e.read().decode()[:300]}")
    if j.get("errors"):
        sys.exit(f"GitHub GraphQL errors: {json.dumps(j['errors'])[:500]}")
    return j["data"]

def gh_head():
    owner, name = REPO.split("/")
    d = gql("""query($o:String!,$n:String!){ repository(owner:$o,name:$n){
                 defaultBranchRef{ name target{ oid } } } }""", {"o": owner, "n": name})
    ref = d["repository"]["defaultBranchRef"]
    _head["branch"], _head["oid"] = ref["name"], ref["target"]["oid"]

def gh_commit(files, message):
    """files: {path: bytes}. Un commit, semnat de GitHub (createCommitOnBranch)."""
    if _head["oid"] is None:
        gh_head()
    d = gql("""mutation($in:CreateCommitOnBranchInput!){
                 createCommitOnBranch(input:$in){ commit{ oid } } }""", {"in": {
        "branch": {"repositoryNameWithOwner": REPO, "branchName": _head["branch"]},
        "message": {"headline": message},
        "expectedHeadOid": _head["oid"],
        "fileChanges": {"additions": [{"path": p, "contents": base64.b64encode(b).decode()} for p, b in files.items()]}}})
    _head["oid"] = d["createCommitOnBranch"]["commit"]["oid"]
    return _head["oid"]

def last_snapshot(name):
    """(sha256, http_status, fișier) din cel mai recent .meta.json salvat pentru `name`, din checkout."""
    d = f"data/{name}"
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".meta.json"))
    except FileNotFoundError:
        return None, None, None
    if not files:
        return None, None, None
    try:
        m = json.load(open(os.path.join(d, files[-1])))
        return m.get("sha256"), m.get("http_status"), files[-1]
    except Exception:
        return None, None, None

def record(name, url, request_body, t0, status, hdrs, body):
    sha = hashlib.sha256(body).hexdigest()
    stamp = t0.strftime("%Y%m%dT%H%M%SZ")
    prev_sha, prev_status, prev_file = last_snapshot(name)
    if prev_sha == sha and prev_status == status:
        # identic cu ultimul snapshot: nu mai comitem încă o copie, doar notăm verificarea în jurnalul rulării
        RUNLOG["unchanged"].append({"name": name, "url": url, "fetched_at_utc": t0.isoformat(),
                                    "http_status": status, "sha256": sha, "bytes": len(body),
                                    "same_as": f"data/{name}/{prev_file}"})
        print(f"{name}: {status} {len(body)}B sha256={sha[:16]} neschimbat (= {prev_file})")
        time.sleep(0.5)
        return
    meta = {"name": name, "url": url, "request_body": request_body, "fetched_at_utc": t0.isoformat(),
            "http_status": status, "response_headers": hdrs, "sha256": sha, "bytes": len(body),
            "github_run_id": RUN, "github_run_url": f"https://github.com/{REPO}/actions/runs/{RUN}"}
    ext = ".json" if body[:1] in (b"{", b"[") else ".html"
    base = f"data/{name}/{stamp}"
    msg = f"{name} {stamp} status={status} sha256={sha[:16]}"
    c1 = gh_commit({base + ext: body,
                    base + ".meta.json": json.dumps(meta, ensure_ascii=False, indent=1).encode()}, msg)
    RUNLOG["recorded"].append(base + ext)
    # rezumat lizibil pentru hotărâri: câte și care
    summary = ""
    if ext == ".json":
        try:
            j = json.loads(body)
            items = j.get("result", {}).get("items") or j.get("result", {}).get("lista") or j.get("result") or []
            if isinstance(items, dict): items = items.get("items", [])
            summary = f" items={len(items)}"
        except Exception: pass
    print(f"{name}: {status} {len(body)}B{summary} sha256={sha[:16]} commit={c1[:7]}")
    time.sleep(1.5)

def post(base, url, body, referer):
    return do(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest", "X-XSRF-TOKEN": xsrf(),
        "Origin": base, "Referer": referer})

def label_of(it):
    """Eticheta directorului: tip + dată, ex. `extraordinara-2026-10-20`."""
    date = next((str(v)[:10] for k, v in it.items() if "data" in k.lower() and v), "")
    tip = next((str(v) for k, v in it.items() if "tip" in k.lower() and isinstance(v, str)), "")
    return re.sub(r"[^a-z0-9.-]+", "-", "-".join(x for x in (tip.lower()[:14], date) if x)) or "sedinta"

def sid_of(it):
    return str(it.get("id") or it.get("idSedinta") or "")

def fetch_list(cfg, save=True):
    """Lista ședințelor din endpoint-ul ABP (cea mai recentă prima). Cu save=True o și înregistrează."""
    base = cfg["base"]
    do(base + cfg["list_page"])  # cookie-uri
    url = base + cfg["list_endpoint"]
    t0, st, h, b = post(base, url, cfg["list_body"], base + cfg["list_page"])
    if save:
        record("lista-sedinte", url, cfg["list_body"], t0, st, h, b)
    try:
        res = json.loads(b).get("result", {})
        items = res.get("items") if isinstance(res, dict) else res
        return [it for it in (items or []) if sid_of(it)]
    except Exception as e:
        print("fetch_list: nu pot parsa lista:", e, file=sys.stderr)
        return []

def stored_list():
    """Ultima listă de ședințe salvată în checkout (fără rețea), sau None."""
    d = "data/lista-sedinte"
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".json") and not f.endswith(".meta.json"))
        res = json.load(open(os.path.join(d, files[-1]))).get("result", {})
        items = res.get("items") if isinstance(res, dict) else res
        return [it for it in (items or []) if sid_of(it)]
    except Exception:
        return None

def _after_since(it, cfg):
    since = cfg.get("watch_since")
    return not since or str(it.get("dataSedinta", ""))[:10] >= since

def watch_set(items, cfg):
    """Ședințele de urmărit la fiecare rulare (și de capturat zilnic): fără PV sau fără HCL
    (printre primele max_sedinte, după watch_since), plus HCL < PHCL printre primele max_sedinte_recent."""
    out = {}
    recent = cfg.get("max_sedinte_recent", 10)
    for i, it in enumerate(items[: cfg.get("max_sedinte", 250)]):
        hcl, phcl = it.get("numarHotarariConsiliuLocal") or 0, it.get("numarProiecteDeHotarare") or 0
        reasons = []
        if _after_since(it, cfg):
            if not it.get("areProcesVerbal"): reasons.append("fara-pv")
            if not hcl: reasons.append("fara-hcl")
        if i < recent and hcl < phcl: reasons.append(f"hcl{hcl}<phcl{phcl}")
        if reasons:
            out[sid_of(it)] = reasons
    return out

def select(items, cfg, mode):
    """{sid: {"label", "reasons"}}: primele N (10 recent / max_sedinte full) + watch_set + `always`."""
    n = cfg.get("max_sedinte", 250) if mode == "full" else cfg.get("max_sedinte_recent", 10)
    watch = watch_set(items, cfg)
    out = {}
    for i, it in enumerate(items[: cfg.get("max_sedinte", 250)]):
        sid = sid_of(it)
        reasons = (["recent"] if i < n else []) + watch.get(sid, [])
        if reasons:
            out[sid] = {"label": label_of(it), "reasons": reasons}
    for sid in cfg.get("always", []):
        out.setdefault(sid, {"label": "sedinta", "reasons": ["always"]})
    return out

def last_full_sweep():
    """Momentul ultimei rulări `full`, din jurnalele de rulare din checkout."""
    try:
        files = sorted(f for f in os.listdir(RUNS_DIR) if f.endswith(".json"))
    except FileNotFoundError:
        return None
    for f in reversed(files):
        try:
            j = json.load(open(os.path.join(RUNS_DIR, f)))
            if j.get("mode") == "full":
                return datetime.fromisoformat(j["started_at_utc"])
        except Exception:
            continue
    return None

def pick_mode(cfg):
    """`full` (toate max_sedinte) o dată la full_sweep_hours sau la cerere (MOL_FULL=1); altfel `recent`."""
    if os.environ.get("MOL_FULL", "").lower() in ("1", "true", "yes"):
        return "full"
    last = last_full_sweep()
    if last is None:
        return "full"
    age = datetime.now(timezone.utc) - last
    return "full" if age >= timedelta(hours=cfg.get("full_sweep_hours", 22)) else "recent"

def write_runlog():
    RUNLOG["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    stamp = datetime.fromisoformat(RUNLOG["started_at_utc"]).strftime("%Y%m%dT%H%M%SZ")
    n_rec, n_unch = len(RUNLOG["recorded"]), len(RUNLOG["unchanged"])
    msg = f"run {stamp} mode={RUNLOG['mode']} sedinte={len(RUNLOG['sedinte'])} noi={n_rec} neschimbate={n_unch}"
    c = gh_commit({f"{RUNS_DIR}/{stamp}.json": json.dumps(RUNLOG, ensure_ascii=False, indent=1).encode()}, msg)
    print(f"{msg} commit={c[:7]}")

def main():
    cfg = json.load(open("targets.json"))
    base = cfg["base"]
    mode = pick_mode(cfg)
    RUNLOG["mode"] = mode
    print(f"mod: {mode}")
    try:
        sedinte = select(fetch_list(cfg), cfg, mode)
        RUNLOG["sedinte"] = sedinte
        print("sedinte:", {k: v["reasons"] for k, v in sedinte.items()})
        for sid, sel in sedinte.items():
            label = sel["label"]
            page_url = f"{base}/MonitorulOficialLocal/Sedinta/{sid}"
            do(page_url)  # doar pentru cookie-uri (conținutul vine prin XHR, HTML-ul nu se salvează)
            for ep_name, ep in cfg["endpoints"].items():
                url = base + ep
                req_body = {"idSedinta": sid, "maxResultCount": 100, "skipCount": 0}
                t0, st, h, b = post(base, url, req_body, page_url)
                record(f"sedinta-{sid}-{label}/{ep_name}", url, req_body, t0, st, h, b)
        for name, ep in cfg.get("extra_endpoints", {}).items():
            if name.startswith("_"): continue
            t0, st, h, b = post(base, base + ep["path"], ep["body"], base + ep.get("referer", "/"))
            record(name, base + ep["path"], ep["body"], t0, st, h, b)
    finally:
        write_runlog()

if __name__ == "__main__":
    main()
