# mol-witness

Martor independent pentru starea Monitorului Oficial Local Miroslava (miroslava.cityon.ro).

La fiecare 3 ore, o rulare GitHub Actions descarcă paginile/endpoint-urile din `targets.json` și salvează
răspunsul brut (`.body`) și metadatele (`.meta.json`: ora UTC, status HTTP, headere, SHA-256, link la rulare).

Fiecare fișier este scris **prin API-ul GitHub**, nu prin `git push`: data commit-ului este pusă de serverul
GitHub, iar commit-ul este semnat GPG de GitHub (badge „Verified”). Nu poate fi antedatat de proprietarul repo-ului.

Verificare independentă: istoricul commit-urilor, log-urile din tab-ul Actions, și
`gpg --verify` pe commit-urile semnate cu cheia GitHub web-flow.
