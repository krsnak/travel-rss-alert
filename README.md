# travel-rss-alert

TG notifikace RSS feedů včetně filtrů.

Filtry (`include_keywords`/`exclude_keywords`) jsou case-insensitive, ignorují diakritiku a používají prefixové párování slov, takže zachytí i běžné české tvary (např. `Turecko` → `Turecka`, `Turecku`).

## GitHub Actions (bez lokálního setupu)

Repo obsahuje workflow `.github/workflows/rss-alert.yml`, který:
- jde spustit ručně (`workflow_dispatch`),
- běží plánovaně každou hodinu,
- nainstaluje závislosti z `requirements.txt`,
- a spustí `python rss_alert.py` v one-shot režimu (`RUN_ONCE=true`).

### Povinné GitHub Secrets

V repozitáři nastavte v **Settings → Secrets and variables → Actions** tyto secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Workflow je používá jako proměnné prostředí; credentials nejsou hardcoded v kódu.

### Ruční spuštění

1. Otevřete **Actions** tab v GitHub repozitáři.
2. Vyberte workflow **RSS Alert**.
3. Klikněte **Run workflow**.

Volitelně nastavte input `test_telegram=true`, pokud chcete pouze otestovat Telegram notifikaci bez zpracování RSS feedů. V tomto režimu aplikace pošle zprávu `✅ travel-rss-alert test OK` a skončí.

Další volitelné inputs pro ruční spuštění:
- `log_level` (default `INFO`) – nastaví verbositu logování (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
- `seed_on_first_run` (default `false`) – když je `true` a `seen.db` je prázdná, aplikace pouze označí aktuální položky feedů jako viděné (bez odeslání Telegram alertů), aby se při prvním deploymentu neposílaly staré články.

### Plánované spuštění

Workflow běží automaticky každou hodinu (cron `0 * * * *`) a kontroluje RSS feedy.

### Persistovaný stav `seen.db`

Workflow perzistuje stav tak, že po každém běhu commitne změněný soubor `seen.db` zpět do větve `main`. Díky tomu se stav mezi běhy zachová spolehlivě i bez `actions/cache`.

První seed aktuálních položek feedu provedete jednorázově ručním spuštěním workflow s inputem `seed_on_first_run=true`. Pokud je DB prázdná, položky se pouze označí jako viděné a neposílají se alerty.

Po seedingu spouštějte workflow normálně (schedule nebo manual bez seed parametru) a budou se odesílat už jen nové položky, které ještě nejsou v `seen.db`.

Pokud `seen.db` neexistuje, aplikace ho vytvoří automaticky při startu.


## Nové ENV proměnné

- `LOG_LEVEL` (default `INFO`) – úroveň logování aplikace.
- `SEED_ON_FIRST_RUN` (default `false`) – seedování položek při prvním běhu nad prázdnou DB.
