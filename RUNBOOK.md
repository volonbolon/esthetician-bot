# Esthetician Bot — Human Runbook & Demo Script

Step-by-step instructions to set up, test, and walk through the Esthetician WhatsApp Bot locally.

---

## 1. Prerequisites & Setup

Ensure `uv` (with Python 3.12) is installed, then sync dependencies from the repository root:

```bash
uv sync
```

Run the linter and full test suite to verify everything is green:

```bash
make check
```

---

## 2. Interactive Terminal Simulator (Default Local Mode)

Start the REPL simulator using the mocked WhatsApp console adapter (`ConsoleMessenger`), in-memory/seeded calendar (`FakeCalendar` from `dev/seed_calendar.yaml`), fake clock (`2026-12-18 09:00`), and local SQLite file (`sim.db`):

```bash
uv run python -m app.sim
```

Optional flags:
- `--phone +5491100000001` — starting client phone number
- `--db sim.db` — SQLite database path
- `--time 2026-12-18T09:00:00` — initial simulated clock timestamp

### How to interact in the prompt
- **Send text**: Type any message (`hola`, `Ana`, `menu`) and press Enter.
- **Tap a button or list item**: Type its number (`1`, `2`, `3`, …) and press Enter.
- **Exit**: Press `Ctrl+C` or `Ctrl+D`.

---

## 3. Guided Walkthrough Script (Copy-Paste Line by Line)

Start a clean session (`uv run python -m app.sim`), then type the following lines one at a time to exercise every major flow:

### Step A — Onboarding & Booking an Appointment
```text
/reset
hola
Ana
1
1
1
1
1
```
*What happens:*
1. `hola` triggers the privacy notice and asks for the client's name.
2. `Ana` saves her name and shows the 3-button main menu (`Reservar`, `Mi turno`, `Hablar con Lorena`).
3. `1` (`Reservar`) -> `1` (`Limpieza facial`) -> `1` (`Vie 18/12`) -> `1` (`11:00`) -> `1` (`Confirmar`) books the appointment and sends an `alerta_admin` template to Lorena.

### Step B — Inspect State (`/cal` and `/db`)
```text
/cal
/db
```
*What happens:* Shows the `Available` blocks, the newly created `Turnos` event (`Limpieza facial – Ana | +5491100000001`), the `clients` row, and the scheduled `reminders` row.

### Step C — Check or Reschedule (`Mi turno`)
```text
hola
2
1
1
3
1
1
```
*What happens:*
1. `hola` -> `2` (`Mi turno`) displays `Tu turno: Limpieza facial el Vie 18/12 11:00` with `Reprogramar` and `Cancelar` buttons.
2. `1` (`Reprogramar`) -> `1` (`Limpieza facial`) -> `3` (`Lun 21/12`) -> `1` (`09:00`) -> `1` (`Confirmar`) books the new slot (`Lun 21/12 09:00`) first, deletes the old one, and alerts Lorena.

### Step D — 24h Reminder & Confirmation (`✅`)
```text
/time 2026-12-20T09:00
/tick
1
/cal
```
*What happens:*
1. `/time 2026-12-20T09:00` advances the fake clock to 24 hours before the Monday appointment.
2. `/tick` runs the sync poller and reminder sender, delivering `[TEMPLATE recordatorio_turno]` with `Confirmar`, `Reprogramar`, and `Cancelar` buttons.
3. `1` (`Confirmar`) prefixes the Google Calendar event title with `✅` (`✅ Limpieza facial – Ana`).

### Step E — Handoff to Esthetician (`Hablar con Lorena`) & Echo Reply
```text
hola
3
Tengo una consulta sobre cremas
/echo +5491100000001 Hola Ana! Contame qué estás usando
menu
```
*What happens:*
1. `3` (`Hablar con Lorena`) alerts Lorena and mutes automated menu replies for 12 hours.
2. Free-text messages from Ana while muted stay silent so Lorena can chat directly.
3. `/echo +5491100000001 ...` simulates Lorena replying from the WhatsApp Business app (extending the 12h mute).
4. Typing `menu` unmutes the bot immediately and shows the main menu.

### Step F — Admin Commands & VIP Flow
```text
/admin
vip +5491100000001
vips
/phone +5491100000001
hola
1
1
```
*What happens:*
1. `/admin` switches to Lorena's admin number (`+5491100000099`).
2. `vip +5491100000001` marks Ana as VIP (unlocking `[VIP]` calendar blocks, 30-day horizon, and the `No encuentro horario` option).
3. `/phone +5491100000001` switches back to Ana so you can see `[VIP]` days (`Sáb 19/12`) and `No encuentro horario` in her day list.

---

## 4. Simulator Slash-Command Reference

| Command | Description |
| :--- | :--- |
| `/phone <num>` | Switch active client phone (e.g. `/phone +5491122334455`) |
| `/admin` | Switch active phone to the first `ADMIN_PHONES` number (`+5491100000099`) |
| `/echo <num> <text>` | Simulate esthetician sending a message to `<num>` from WhatsApp Business app |
| `/time` | Show current simulated time |
| `/time +2d` / `+3h` / `<ISO>` | Advance or set simulated clock (e.g. `/time 2026-12-20T09:00`) |
| `/tick` | Run background jobs (`run_sync` + `run_reminders`) at current simulated time |
| `/cal` | List events in both `Available` and `Turnos` calendars |
| `/cal add <cal> <start> <end> <title> [\| <desc>]` | Add a calendar event (e.g. `/cal add turnos 2026-12-22T10:00 2026-12-22T11:00 Peeling – Ana \| +5491100000001`) |
| `/cal move <id> <new_start> [new_end]` | Move an existing event to a new time |
| `/cal delete <id>` | Delete an event by ID |
| `/calfail` | Force the next calendar operation to fail with `CalendarError` |
| `/db` | Dump `clients`, `reminders`, and active `mutes` from SQLite |
| `/reset` | Wipe SQLite tables and reset `FakeCalendar` to `dev/seed_calendar.yaml` |

---

## 5. Replaying Golden Scenarios

All automated conversation transcripts live in `tests/scenarios/`. Replay any scenario and verify its output matches `.expected`:

```bash
uv run python -m app.sim --script tests/scenarios/book.txt
uv run python -m app.sim --script tests/scenarios/reminders.txt
uv run python -m app.sim --script tests/scenarios/manual_calendar_sync.txt
```

---

## 6. Running Against a Real Google Test Calendar (`CALENDAR_BACKEND=google`)

1. Share two Google Calendars (`Available` and `Turnos`) with your Google Cloud service account email (with **Make changes to events** permission).
2. Export your credentials and calendar IDs, then launch the simulator:

```bash
export CALENDAR_BACKEND="google"
export GOOGLE_SA_JSON="/absolute/path/to/service-account.json"
export CAL_AVAILABLE_ID="your-available-id@group.calendar.google.com"
export CAL_TURNOS_ID="your-turnos-id@group.calendar.google.com"

uv run python -m app.sim
```

You can also run the live Google Calendar contract + simulator test suite:

```bash
RUN_GCAL_CONTRACT=1 uv run pytest tests/test_gcal.py
```

---

## 7. Starting the FastAPI Server

To run the HTTP server (`/healthz` + background reminder/sync loops when `MESSENGER != console`):

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Verify the health endpoint in another terminal:

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```
