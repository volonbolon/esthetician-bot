# Esthetician Appointment Bot (WhatsApp) — v1 Spec

Single esthetician, Argentina, Spanish, tz `America/Argentina/Buenos_Aires`.

## Stack
- WhatsApp Cloud API (direct with Meta), dedicated new number.
  - Coexistence: number is registered in the WhatsApp Business app first, then onboarded to Cloud API via Embedded Signup.
    She replies to clients from the Business app on the same number.
- Python + FastAPI, SQLite, one process. Single asyncio lock around booking confirm.
- Google Calendar via service account.
- Hosting: Fly.io, exactly one machine (`min_machines_running=1`, auto-stop off so pollers run), volume for SQLite.
  Daily Litestream backup to S3/R2. Health check + external uptime ping.

## Source of truth
- **Google Calendar is authoritative** for appointments. Manual edits in Turnos (move, delete, add) just work.
- DB stores only: clients (phone, name, `vip`, `muted_until`, `awaiting_name`), event_id ↔ phone cache,
  reminder rows, processed `wamid`s, VIP no-slot alert timestamps.
- Before acting on an appointment (reminder, "Mi turno", cancel, reschedule), re-read the event by ID:
  deleted → skip / tell client; moved → recompute.
- Losing the DB loses only names, VIP flags and mutes; appointments survive in Calendar.

## Phones
- One `normalize_phone()` → E.164. Handles WhatsApp's `549…` form and manual `011 15…`, `+54 11…`, etc.
  Used for client IDs, `ADMIN_PHONES`, admin command args, and matching phones in event descriptions.

## Conversation
- Fixed menu using interactive lists/buttons. No LLM, no free-text parsing.
- **Stateless payloads**: every row/button ID carries what it needs, e.g. `slot:svc=facial;t=2026-10-27T10:00`,
  `rem:cancel:evt=abc123` (≤200 chars). Every tap is re-validated against Calendar.
  Stale/invalid tap → "ese horario ya no está disponible" + fresh options.
- Taps that reference an event must be checked against the phone in that event's description (ownership).
- Lists: max 10 rows. Regular: 9 items + "Ver más". VIP day/time lists: 8 items + "Ver más" + "No encuentro horario".
- Main menu: Reservar / Mi turno / Hablar con [name].
- New number: one-line data notice (no consent button) + ask name once (only free text). Store name, phone.
- Off-script message → main menu.

## Hablar con [name]
- Alert esthetician (admin-alert template to her personal number) and mute the bot for that client for 12h.
- Mute silences menu replies only; reminders and booking confirmations still go out.
- Mute ends at 12h, or early if the client types `menu`, or she sends `bot +54911…`.
- Message echoes (her replies sent from the Business app) are never treated as client messages;
  an echo to a client extends that client's mute by 12h.

## Availability & slots
- "Available" calendar: open blocks. Event title containing `[VIP]` = VIP-only.
- "Turnos" calendar: bookings (title `Service – Name`, phone in description). Busy = Turnos events.
- `services.yaml` (dev-edited): services (name, duration), grid step (default 15 min), buffer (default 0),
  regular horizon 14d, VIP horizon 30d, min notice 2h, cancel cutoff 24h.
- A start time is offered only if service + buffer fits inside an Available block and overlaps no Turnos event.
- Flow: service -> day (next days with slots) -> time -> confirm.

## Booking rules
- On confirm: re-check calendar under lock, then create event. If gone: "ese horario ya no está disponible" + fresh slots.
- No holds. One active appointment per client.
- Active appointment lookup: list future Turnos events, match normalized phone in description
  (covers manual bookings if she includes the phone).
- Reschedule = book new first, then delete old. The one-active check excludes the old event during reschedule.
- Cancel/reschedule allowed until 24h before; inside cutoff -> tell client to contact her + alert her.
- Calendar API failure: tell client to retry later, alert her, never claim success.

## VIP
- `vip` flag per phone in DB. Regulars see 14d, VIPs 30d + `[VIP]` slots.
- No auto-bump. VIPs get a "No encuentro horario" row (end of day list, and in time list / when empty).
  Tap → alert her with client name + service, tell client she'll get in touch. Max one alert per VIP per 24h.
- Admin commands from numbers in `ADMIN_PHONES` env (her personal number):
  `vip +54911…`, `unvip +54911…`, `vips`, `bot +54911…` (unmute).

## Reminders & alerts
- Reminder template 48h before with Confirm / Reschedule / Cancel buttons.
  - Booked <48h ahead: no reminder (booking confirmation is enough).
  - Confirm: prefix event title with `✅`.
  - No reply: nothing (no chasing, no auto-cancel).
- Stored as DB rows (`event_id`, `send_at`, `sent_at`), polled every minute; restart-safe.
  Re-read event before sending: deleted → drop; moved → recompute `send_at`.
- Sync poller every 15 min: scan future Turnos events, create/move/drop reminder rows (covers manual edits).
- One approved admin-alert template for alerts outside the 24h window.
- Every bot booking / reschedule / cancellation → WhatsApp alert to her via the admin-alert template
  (replaces Google Calendar notifications, which are email-only).

## Reliability
- Verify `X-Hub-Signature-256` on every webhook; return 200 fast.
- Dedupe incoming messages by `wamid`.

## Testing
- pytest on pure functions: slot generation (blocks, busy, grid, buffer, VIP, horizon, notice),
  `normalize_phone`, payload encode/decode, and the conversation state machine.
- Google Calendar and WhatsApp behind thin clients, faked in tests.

## Risks / TODO
- Skipping explicit consent may conflict with Ley 25.326 (and AAIP database registration).
  Decision: launch without a consent button; revisit later. Confirm with a lawyer.
- Start Meta business verification, coexistence onboarding, and template approval
  (reminder, admin alert) early.
