#!/usr/bin/env python3
"""Capturi de ecran (Chromium headless) pentru ședințele urmărite: fără PV sau HCL, sau HCL < PHCL printre cele recente.
Peste pagină se suprapune ora UTC, URL-ul și rularea; imaginea + metadatele se scriu în `captures/` prin același
drum ca `data/`: createCommitOnBranch, commit semnat și datat de GitHub."""
import hashlib, json, os, sys, time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
import witness as w

CAP_DIR = "captures"

OVERLAY_JS = """(txt) => {
  const d = document.createElement('div');
  d.id = 'mol-witness-overlay';
  d.textContent = txt;
  Object.assign(d.style, {position:'relative', zIndex:'2147483647',
    background:'rgba(0,0,0,.85)', color:'#fff', font:'bold 15px/1.4 monospace', padding:'6px 10px',
    whiteSpace:'pre-wrap', pointerEvents:'none'});
  document.body.prepend(d);
}"""

def capture(page, cfg, sid, sel):
    base, cap = cfg["base"], cfg.get("capture", {})
    url = f"{base}/MonitorulOficialLocal/Sedinta/{sid}"
    t0 = datetime.now(timezone.utc)
    resp = page.goto(url, wait_until="networkidle", timeout=cap.get("timeout_s", 60) * 1000)
    page.wait_for_timeout(1500)  # XHR-urile ABP după load
    status = resp.status if resp else None
    title = page.title()
    overlay = (f"MOL witness  {t0.strftime('%Y-%m-%d %H:%M:%S UTC')}\n{url}\n"
               f"run https://github.com/{w.REPO}/actions/runs/{w.RUN}  motiv: {', '.join(sel['reasons'])}")
    page.evaluate(OVERLAY_JS, overlay)
    fmt = cap.get("format", "jpeg")
    kw = {"type": fmt, "full_page": True}
    if fmt == "jpeg": kw["quality"] = cap.get("quality", 85)
    img = page.screenshot(**kw)
    sha = hashlib.sha256(img).hexdigest()
    stamp = t0.strftime("%Y%m%dT%H%M%SZ")
    name = f"sedinta-{sid}-{sel['label']}"
    ext = ".jpg" if fmt == "jpeg" else ".png"
    basep = f"{CAP_DIR}/{name}/{stamp}"
    meta = {"name": name, "url": url, "captured_at_utc": t0.isoformat(), "http_status": status, "page_title": title,
            "reasons": sel["reasons"], "overlay_text": overlay, "viewport": cap.get("viewport", [1280, 900]),
            "format": fmt, "sha256": sha, "bytes": len(img), "browser": "chromium/playwright",
            "github_run_id": w.RUN, "github_run_url": f"https://github.com/{w.REPO}/actions/runs/{w.RUN}"}
    c = w.gh_commit({basep + ext: img, basep + ".meta.json": json.dumps(meta, ensure_ascii=False, indent=1).encode()},
                    f"captura {name} {stamp} status={status} sha256={sha[:16]}")
    print(f"{name}: {status} {len(img)}B sha256={sha[:16]} commit={c[:7]} motiv={sel['reasons']}")
    return basep + ext

def main():
    cfg = json.load(open("targets.json"))
    items = w.stored_list()
    if not items:
        items = w.fetch_list(cfg, save=False)
    by_id = {w.sid_of(it): it for it in items}
    manual = [x.strip() for x in os.environ.get("MOL_SEDINTE", "").split(",") if x.strip()]
    if manual:  # declanșare manuală cu listă explicită de id-uri
        watch = {sid: ["manual"] for sid in manual}
    else:
        watch = w.watch_set(items, cfg)
    targets = {sid: {"label": w.label_of(by_id[sid]) if sid in by_id else "sedinta", "reasons": r}
               for sid, r in watch.items()}
    print(f"de capturat: {len(targets)} sedinte", {k: v["reasons"] for k, v in targets.items()})
    done, failed = [], {}
    vp = cfg.get("capture", {}).get("viewport", [1280, 900])
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": vp[0], "height": vp[1]}, user_agent=w.UA, locale="ro-RO")
        page = ctx.new_page()
        for sid, sel in targets.items():
            try:
                done.append(capture(page, cfg, sid, sel))
            except Exception as e:
                failed[sid] = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"sedinta-{sid}: EROARE {failed[sid]}", file=sys.stderr)
            time.sleep(1.0)
        browser.close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = {"github_run_id": w.RUN, "github_run_url": f"https://github.com/{w.REPO}/actions/runs/{w.RUN}",
           "finished_at_utc": datetime.now(timezone.utc).isoformat(), "targets": targets, "captured": done, "failed": failed}
    w.gh_commit({f"{CAP_DIR}/_runs/{stamp}.json": json.dumps(log, ensure_ascii=False, indent=1).encode()},
                f"capturi {stamp} ok={len(done)} esuate={len(failed)}")
    if failed and not done:
        sys.exit("toate capturile au esuat")

if __name__ == "__main__":
    main()
