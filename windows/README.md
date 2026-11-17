# Run PROJECT APEX on Windows

PROJECT APEX runs on your PC in Docker Desktop: PostgreSQL, Redis, the API, the background worker and
the web dashboard each run in a container. You open it at **http://localhost:3000**.

## One-time setup (about 15 minutes)

1. **Install Docker Desktop**: https://www.docker.com/products/docker-desktop/
   Keep "Use WSL 2" selected, restart Windows when asked, then open Docker Desktop once and accept
   its terms. Needs Windows 10 22H2 or Windows 11, and 8 GB of RAM or more.
2. **Get the code**, either way:
   - With Git for Windows: `git clone -b claude/project-apex-build-d1udfv https://github.com/leo21l1/the-best-apex-trade-bot.git C:\PROJECT-APEX`
   - Or on GitHub: switch to the branch `claude/project-apex-build-d1udfv` → **Code → Download ZIP**,
     then extract it to `C:\PROJECT-APEX`.
   Use a short path without special characters, and do not put it inside OneDrive.
3. Open `C:\PROJECT-APEX\windows` and double-click **Install desktop shortcut.bat**.
   If Windows SmartScreen warns about the file, choose **More info → Run anyway**.

## Every day

- Double-click **PROJECT APEX** on the desktop. The launcher:
  1. checks that Docker Desktop is installed and starts its engine if needed;
  2. creates `.env` on first run, with a random secret key and SIMULATED market data;
  3. checks that ports 3000 and 8000 are free;
  4. starts all five services and waits until the API and dashboard answer;
  5. opens http://localhost:3000 in your browser.
- The first start downloads and builds everything (5-15 minutes). Later starts take seconds.
- On your first visit, create your administrator account on the sign-in page.
- Double-click **Stop PROJECT APEX** to stop it. Your journal, settings and data are kept.

## Settings (`C:\PROJECT-APEX\.env`)

| Setting | Meaning |
|---|---|
| `APEX_MARKET_DATA_PROVIDER=simulated` | Synthetic prices, labelled SIMULATED everywhere. Use `databento` plus `APEX_DATABENTO_API_KEY=...` for licensed CME data. |
| `APEX_ANTHROPIC_API_KEY=` | Optional Claude API key for the AI copilot. |
| `APEX_PG_HOST_PORT=55432` | Host port of the bundled database (it avoids a PostgreSQL that is already installed). |

After changing `.env`, run Stop and then Start again. Never share `.env`.

## Problems

- The launcher log is in `windows\apex-launcher.log`.
- To see service logs, run `docker compose logs api` (or `web`, `worker`) in a terminal opened in `C:\PROJECT-APEX`.
- If a port is in use, close the other program (for example another development server) and start again.
- If you update the code, the next start rebuilds the changed parts automatically.

Live Apex/Tradovate order entry is permanently disabled. PROJECT APEX is analysis-only.
