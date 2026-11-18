#!/usr/bin/env python3
"""Arhivează în repo logurile rulărilor GitHub Actions (witness, captures), înainte să le șteargă GitHub.
La un repo public logurile se păstrează 90 de zile; din 1.10.2026 dispar odată cu ele și paginile rulărilor, deci
și linkurile `github_run_url` din .meta.json și din overlay-ul capturilor. Pentru fiecare rulare terminată și nearhivată:
  actions-logs/<workflow>/<AAAA-LL>/<stamp>-<run_id>.<job>.log   logul brut al job-ului, octet cu octet cum îl dă API-ul
  actions-logs/<workflow>/<AAAA-LL>/<stamp>-<run_id>.meta.json   rularea: eveniment, concluzie, head_sha, pași, SHA-256 al logurilor
  actions-logs/SHA256SUMS                                        toate logurile + sumele: `cd actions-logs && sha256sum -c SHA256SUMS`
Scrierea merge pe același drum ca `data/`: createCommitOnBranch, un commit semnat și datat de GitHub per rulare.
MOL_DRY_RUN=1 scrie fișierele local în loc să comită; MOL_MAX_RUNS (implicit 200) limitează câte rulări arhivează o execuție."""
import base64, hashlib, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
RUN = os.environ.get("GITHUB_RUN_ID", "local")
DRY = os.environ.get("MOL_DRY_RUN", "").lower() in ("1", "true", "yes")
MAX_RUNS = int(os.environ.get("MOL_MAX_RUNS") or 200)
API = f"https://api.github.com/repos/{REPO}"
GQL = "https://api.github.com/graphql"
UA = f"mol-witness/1.0 (+https://github.com/{REPO})"
DIR = "actions-logs"
SUMS = f"{DIR}/SHA256SUMS"
MIN_AGE = timedelta(minutes=10)   # rulări terminate de mai puțin de atât rămân pe data viitoare: logul poate întârzia câteva secunde

class RateLimited(Exception): pass
class GqlError(Exception): pass

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

_api = urllib.request.build_opener(_NoRedirect)   # 302-ul către blob îl urmăm noi, fără token (nu merge la alt host)
_raw = urllib.request.build_opener()

def api(path, raw=False):
    """GET pe REST. (status, headers, body); body = JSON decodat sau, cu raw=True, octeții ca atare."""
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {TOKEN}", "User-Agent": UA,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with _api.open(req, timeout=60) as r:
            st, h, b = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        st, h, b = e.code, e.headers, e.read()
    if st in (301, 302, 307) and h.get("Location"):
        try:
            with _raw.open(urllib.request.Request(h["Location"], headers={"User-Agent": UA}), timeout=120) as r:
                st, h, b = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            st, h, b = e.code, e.headers, e.read()
    if st in (403, 429) and (h.get("X-RateLimit-Remaining") == "0" or h.get("Retry-After")):
        raise RateLimited(f"{st} {path}: {b[:200]!r}")
    if raw or st != 200:
        return st, h, b
    return st, h, json.loads(b)

def gql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(GQL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {TOKEN}", "User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        raise GqlError(f"GitHub GraphQL {e.code}: {e.read().decode()[:300]}")
    if j.get("errors"):
        raise GqlError(json.dumps(j["errors"])[:500])
    return j["data"]

_head = {"oid": None, "branch": None}

def gh_head():
    owner, name = REPO.split("/")
    ref = gql("""query($o:String!,$n:String!){ repository(owner:$o,name:$n){
                   defaultBranchRef{ name target{ oid } } } }""", {"o": owner, "n": name})["repository"]["defaultBranchRef"]
    _head["branch"], _head["oid"] = ref["name"], ref["target"]["oid"]

def gh_commit(files, message):
    """files: {path: bytes}. Un commit semnat de GitHub (createCommitOnBranch). Dacă între timp a comis altă rulare
    (witness, captures), expectedHeadOid nu mai e bun: reîmprospătăm HEAD și reîncercăm."""
    for attempt in range(1, 4):
        if _head["oid"] is None:
            gh_head()
        try:
            d = gql("""mutation($in:CreateCommitOnBranchInput!){
                         createCommitOnBranch(input:$in){ commit{ oid } } }""", {"in": {
                "branch": {"repositoryNameWithOwner": REPO, "branchName": _head["branch"]},
                "message": {"headline": message}, "expectedHeadOid": _head["oid"],
                "fileChanges": {"additions": [{"path": p, "contents": base64.b64encode(b).decode()} for p, b in files.items()]}}})
        except GqlError as e:
            if attempt == 3:
                raise
            print(f"commit esuat ({e}); reincerc cu HEAD nou", file=sys.stderr)
            _head["oid"] = None
            time.sleep(3)
            continue
        _head["oid"] = d["createCommitOnBranch"]["commit"]["oid"]
        return _head["oid"]

def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def archived_runs():
    """Id-urile rulărilor deja arhivate, după numele .meta.json din checkout."""
    ids = set()
    for _, _, files in os.walk(DIR):
        for f in files:
            m = re.fullmatch(r"\d{8}T\d{6}Z-(\d+)\.meta\.json", f)
            if m:
                ids.add(int(m[1]))
    return ids

def completed_runs():
    """Toate rulările terminate pe care GitHub le mai are, cele mai vechi primele."""
    runs, page = [], 1
    while True:
        st, _, j = api(f"/actions/runs?status=completed&per_page=100&page={page}")
        if st != 200:
            sys.exit(f"GET /actions/runs: {st} {j[:300]!r}")
        runs += j["workflow_runs"]
        if len(j["workflow_runs"]) < 100:
            return sorted(runs, key=lambda r: r["id"])
        page += 1

def archive(run, sums):
    """Logurile tuturor job-urilor rulării + meta + SHA256SUMS actualizat, într-un commit. Întoarce mesajul commit-ului."""
    rid = run["id"]
    wf = re.sub(r"\.ya?ml$", "", os.path.basename(run["path"]))
    t = ts(run.get("run_started_at") or run["created_at"])
    stamp = t.strftime("%Y%m%dT%H%M%SZ")
    base = f"{DIR}/{wf}/{t:%Y-%m}/{stamp}-{rid}"
    st, _, jl = api(f"/actions/runs/{rid}/jobs?per_page=100&filter=all")
    if st != 200:
        print(f"run {rid}: jobs {st}, sar peste", file=sys.stderr)
        return None
    files, jobs, shas = {}, [], []
    for jb in jl["jobs"]:
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", jb["name"]).strip("-") or "job"
        att = f".a{jb['run_attempt']}" if (jb.get("run_attempt") or 1) > 1 else ""
        path = f"{base}.{name}{att}.log"
        info = {k: jb.get(k) for k in ("id", "name", "run_attempt", "status", "conclusion", "started_at", "completed_at",
                                        "runner_name", "html_url")}
        info["steps"] = [{k: s.get(k) for k in ("number", "name", "status", "conclusion", "started_at", "completed_at")}
                         for s in jb.get("steps") or []]
        st, _, body = api(f"/actions/jobs/{jb['id']}/logs", raw=True)
        if st == 200:
            sha = hashlib.sha256(body).hexdigest()
            files[path] = body
            shas.append(sha)
            sums.append(f"{sha}  {path[len(DIR) + 1:]}")
            info.update({"log": path, "log_sha256": sha, "log_bytes": len(body)})
        else:   # 404/410: GitHub nu (mai) are logul; rămâne măcar meta
            info.update({"log": None, "log_error": f"HTTP {st}"})
            print(f"run {rid} job {jb['name']}: log {st}", file=sys.stderr)
        jobs.append(info)
    meta = {"run_id": rid, "run_number": run.get("run_number"), "run_attempt": run.get("run_attempt"),
            "workflow": run.get("name"), "workflow_path": run["path"], "event": run.get("event"),
            "status": run.get("status"), "conclusion": run.get("conclusion"),
            "actor": (run.get("actor") or {}).get("login"), "head_branch": run.get("head_branch"), "head_sha": run.get("head_sha"),
            "created_at": run.get("created_at"), "run_started_at": run.get("run_started_at"), "updated_at": run.get("updated_at"),
            "html_url": run.get("html_url"), "jobs": jobs,
            "archived_at_utc": datetime.now(timezone.utc).isoformat(),
            "archived_by_run_id": RUN, "archived_by_run_url": f"https://github.com/{REPO}/actions/runs/{RUN}"}
    files[base + ".meta.json"] = json.dumps(meta, ensure_ascii=False, indent=1).encode()
    files[SUMS] = ("\n".join(sums) + "\n").encode()
    msg = f"actions-log {wf} {stamp} run={rid} {run.get('conclusion')} jobs={len(jobs)}"
    if shas:
        msg += " sha256=" + ",".join(s[:16] for s in shas)
    if DRY:
        for p, b in files.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b)
        print(f"{msg} (dry-run: scris local)")
    else:
        c = gh_commit(files, msg)
        print(f"{msg} commit={c[:7]}")
        time.sleep(1.0)
    return msg

def main():
    done = archived_runs()
    sums = open(SUMS, encoding="utf-8").read().splitlines() if os.path.exists(SUMS) else []
    runs = completed_runs()
    now = datetime.now(timezone.utc)
    todo = [r for r in runs if r["id"] not in done and str(r["id"]) != RUN and now - ts(r["updated_at"]) >= MIN_AGE]
    print(f"rulari terminate pe GitHub: {len(runs)}, arhivate deja: {len(done)}, de arhivat: {len(todo)}"
          + (f", acum cel mult {MAX_RUNS}" if len(todo) > MAX_RUNS else ""))
    n = 0
    try:
        for r in todo[:MAX_RUNS]:
            if archive(r, sums):
                n += 1
    except RateLimited as e:
        print(f"limita API GitHub atinsa, restul la urmatoarea rulare: {e}", file=sys.stderr)
    print(f"arhivate acum: {n}, ramase: {len(todo) - n}")

if __name__ == "__main__":
    main()
