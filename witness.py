#!/usr/bin/env python3
"""Martor pentru Monitorul Oficial Local (CityOn/ABP).
Pentru fiecare ședință: GET pagina (primește cookie-urile ABP + XSRF), apoi POST pe endpoint-urile JSON.
Fiecare răspuns brut + metadate e scris prin API-ul GitHub (commit server-side, semnat de GitHub)."""
import base64, hashlib, json, os, re, sys, time, urllib.request, urllib.error, http.cookiejar
from datetime import datetime, timezone

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
RUN = os.environ.get("GITHUB_RUN_ID", "local")
API = f"https://api.github.com/repos/{REPO}/contents/"
UA = f"mol-witness/1.0 (+https://github.com/{REPO})"

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

def record(name, url, request_body, t0, status, hdrs, body):
    sha = hashlib.sha256(body).hexdigest()
    stamp = t0.strftime("%Y%m%dT%H%M%SZ")
    meta = {"name": name, "url": url, "request_body": request_body, "fetched_at_utc": t0.isoformat(),
            "http_status": status, "response_headers": hdrs, "sha256": sha, "bytes": len(body),
            "github_run_id": RUN, "github_run_url": f"https://github.com/{REPO}/actions/runs/{RUN}"}
    ext = ".json" if body[:1] in (b"{", b"[") else ".html"
    base = f"data/{name}/{stamp}"
    msg = f"{name} {stamp} status={status} sha256={sha[:16]}"
    c1 = gh_commit({base + ext: body,
                    base + ".meta.json": json.dumps(meta, ensure_ascii=False, indent=1).encode()}, msg)
    c2 = c1
    # rezumat lizibil pentru hotărâri: câte și care
    summary = ""
    if ext == ".json":
        try:
            j = json.loads(body)
            items = j.get("result", {}).get("items") or j.get("result", {}).get("lista") or j.get("result") or []
            if isinstance(items, dict): items = items.get("items", [])
            summary = f" items={len(items)}"
        except Exception: pass
    print(f"{name}: {status} {len(body)}B{summary} sha256={sha[:16]} commits={c1[:7]},{c2[:7]}")
    time.sleep(1.5)

def post(base, url, body, referer):
    return do(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest", "X-XSRF-TOKEN": xsrf(),
        "Origin": base, "Referer": referer})

def discover(cfg):
    """Lista ședințelor din endpoint-ul ABP; returnează {id: eticheta}. Robust la nume de câmpuri necunoscute."""
    base = cfg["base"]
    do(base + cfg["list_page"])  # cookie-uri
    url = base + cfg["list_endpoint"]
    t0, st, h, b = post(base, url, cfg["list_body"], base + cfg["list_page"])
    record("lista-sedinte", url, cfg["list_body"], t0, st, h, b)
    out = {}
    try:
        res = json.loads(b).get("result", {})
        items = res.get("items") if isinstance(res, dict) else res
        for it in (items or [])[: cfg.get("max_sedinte", 25)]:
            sid = str(it.get("id") or it.get("idSedinta") or "")
            if not sid: continue
            date = next((str(v)[:10] for k, v in it.items() if "data" in k.lower() and v), "")
            tip = next((str(v) for k, v in it.items() if "tip" in k.lower() and isinstance(v, str)), "")
            out[sid] = re.sub(r"[^a-z0-9.-]+", "-", "-".join(x for x in (tip.lower()[:14], date) if x)) or "sedinta"
    except Exception as e:
        print("discover: nu pot parsa lista:", e, file=sys.stderr)
    for sid in cfg.get("always", []):
        out.setdefault(sid, "sedinta")
    return out

def main():
    cfg = json.load(open("targets.json"))
    base = cfg["base"]
    sedinte = discover(cfg)
    print("sedinte:", sedinte)
    for sid, label in sedinte.items():
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

if __name__ == "__main__":
    main()
