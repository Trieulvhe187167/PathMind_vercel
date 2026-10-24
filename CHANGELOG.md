# PathMind change log

Each round of work was shipped as a numbered patch (`pmNN`). Newest first.

## pm63 — 2026-10-01 · Courses is a paid feature
- New `courses` entitlement (Free off; Plus, Pro, Unlimited on), editable in `/admin/plans`.
- "Courses" entry in the sidebar (after Learning Space) with a lock and upgrade link on Free; `/courses` shows the upgrade notice.
- `/api/courses`: only the list stays open (answers empty + `locked` so chat and sidebars keep working); everything else is 402 with an upgrade hint. Existing courses are kept when a plan is downgraded.
- Pricing shows "Study courses" on paid plans.

## pm62 — 2026-09-30 · Review round on plan gating
- Community unread badge (`GET /api/community/notifications/unread-count`) is open to every plan, so the sidebar poll no longer gets a 402 every minute on Free (`community_guard`).
- Includes pm61 (see below).
- 30+ new tests in `tests/services/test_entitlements.py`: creation routes (REST + WebSocket) take and give back allowance, path/draft limits, lifetime trial across months, guards attached in `main.py`.

## pm61 — 2026-09-30 · Allowance reserved before creation
- `reserve_monthly` / `release_monthly`: the book/path allowance is checked and counted in one step before the slow creation, and given back if it fails. Parallel requests can no longer turn one free-trial book into several.
- Book WebSocket answers `{type: "error", code: "plan_limit"}` when the allowance is used up.
- Junk limit values in `subscription_plans.entitlements` keep the default instead of becoming "unlimited"; a DB error while resolving the plan falls back to Free and is not cached.

## pm60 — 2026-09-29 · Free trial + counters
- Free plan gets a one-time trial: 1 mastery path + 1 AI book (`allowance_period: "lifetime"`); Plus is 10 / month, Pro / Unlimited unlimited.
- "Books created" / "Paths created" counters for Free and Plus (Learning cards, book library, topic atlas).
- Admin plan editor: "Path & book allowance" (resets every month / one-time trial). Existing Free plan migrates automatically.

## pm59 / pm59b — 2026-09-29 · Plans and entitlements
- New **Plus** plan ($4.99/mo, $49/yr; 11M credits/month, 1M/day; 2 GB; 25 MB/file; 8 models).
- Per-plan entitlements (`services/entitlements.py`, editable in `/admin/plans`): Community, Notebooks / Question Bank / Skills, Reading and Watching are paid-only; own skills Free 0 / Plus 5 / Pro 20 / Unlimited ∞; partners 1 / 3 / 10 / ∞.
- Practice, chat history and the Knowledge Center stay open to everyone; admins are never limited; downgrading never deletes anything.
- Locked items stay visible with a lock and an upsell (Plus → Pro → Unlimited); pricing page lists the limits.
- pm59b: singular wording ("1 partner"), duplicate upgrade link removed.

## pm58 — 2026-09-29 · Partner chat privacy
- Admins can no longer read other users' Partner chats.

## push57 — 2026-09-28 · Community and study rooms (commit 03dc69a7)
- Community forum + Skill Hub, moderation, notifications with sidebar badge, topic chips.
- Study rooms in Friends, Partners/Friends UI follows the UI language, Skills open to all users again, Import from EduHub removed.

## pm1–pm56 — 2026-09-24 → 09-28
- Rebrand from DeepTutor to PathMind, subscription/credits/billing, themes, i18n (en/zh) and many UI and reliability fixes. Details in `CLAUDE.md`.
