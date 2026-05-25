# travel-rss-alert

TG notifikace RSS feedů včetně filtrů.

## GitHub Actions (bez lokálního setupu)

Repo obsahuje workflow `.github/workflows/rss-alert.yml`, který:
- jde spustit ručně (`workflow_dispatch`),
- běží plánovaně každých 10 minut,
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

### Plánované spuštění

Workflow běží automaticky každých 10 minut (cron `*/10 * * * *`) a kontroluje RSS feedy.

### Persistovaný stav `seen.db`

Workflow používá `actions/cache` pro obnovu/uložení `seen.db` mezi běhy. Cache v GitHub Actions je **best-effort** (není to 100% garantované dlouhodobé úložiště), proto pro spolehlivý dlouhodobý perzistentní stav doporučujeme nasazení na VPS / Docker s trvalým volume.

Pokud `seen.db` neexistuje, aplikace ho vytvoří automaticky při startu.
