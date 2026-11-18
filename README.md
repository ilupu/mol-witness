# mol-witness

Martor independent pentru starea Monitorului Oficial Local Miroslava (miroslava.cityon.ro).

La fiecare 3 ore, o rulare GitHub Actions descarcă paginile/endpoint-urile din `targets.json` și salvează
răspunsul brut (`.json`/`.html`) și metadatele (`.meta.json`: ora UTC, status HTTP, headere, SHA-256, link la rulare).

- Rularea obișnuită verifică cele mai recente `max_sedinte_recent` (10) ședințe; o dată la `full_sweep_hours`
  (sau manual, cu input-ul `full`) verifică toate (`max_sedinte`).
- „Urmărite” sunt, dintre cele 10 recente, ședințele fără proces-verbal, fără hotărâri, sau cu mai puține
  hotărâri decât proiecte. Ședințele mai vechi nu se evaluează după aceste reguli.
- De două ori pe zi, la 9 și 21 ora României (`captures.yml`, sau manual, opțional cu o listă de id-uri în input-ul `sedinte`), pentru ședințele
  urmărite se face și o captură de ecran a paginii în Chromium headless,
  cu ora UTC, URL-ul și rularea suprapuse pe imagine; se salvează în `captures/<sedinta>/<stamp>.jpg` + `.meta.json`
  (SHA-256, motiv, titlu pagină), prin același commit semnat de GitHub. Se capturează și paginile din `capture_pages`
  (lista hotărârilor, lista dispozițiilor). Jurnalul rulării: `captures/_runs/`.
- Un răspuns identic (același SHA-256 și status) cu ultimul snapshot nu mai este salvat încă o dată;
  verificarea este consemnată în jurnalul rulării, `data/_runs/<stamp>.json`, cu referință la snapshot-ul
  cu care coincide. Un fișier nou în `data/<endpoint>/` înseamnă deci că endpoint-ul s-a schimbat.
- Zilnic (`archive-logs.yml`, sau manual, cu input-ul `max`), logurile rulărilor GitHub Actions terminate se copiază în repo:
  GitHub le șterge după 90 de zile și, din octombrie 2026, odată cu ele dispar și paginile rulărilor, deci linkurile
  `github_run_url` din `.meta.json` și din overlay-ul capturilor. `actions-logs/<workflow>/<AAAA-LL>/<stamp>-<run_id>.<job>.log`
  este logul brut al job-ului, octet cu octet cum îl dă API-ul GitHub; `<stamp>-<run_id>.meta.json` păstrează rularea
  (eveniment, concluzie, `head_sha` al codului care a rulat, pașii cu orele lor, SHA-256 și mărimea logurilor).
  `actions-logs/SHA256SUMS` listează toate logurile cu sumele lor: `cd actions-logs && sha256sum -c SHA256SUMS`.
  Un link mort `…/actions/runs/<run_id>` se regăsește cu `grep <run_id> actions-logs/SHA256SUMS`.

Fiecare fișier este scris **prin API-ul GitHub**, nu prin `git push`: data commit-ului este pusă de serverul
GitHub, iar commit-ul este semnat GPG de GitHub (badge „Verified”). Nu poate fi antedatat de proprietarul repo-ului.

Verificare independentă: istoricul commit-urilor, log-urile din tab-ul Actions (90 de zile) sau copiile lor din `actions-logs/`, și
`gpg --verify` pe commit-urile semnate cu cheia GitHub web-flow.
