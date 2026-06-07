# Frontend Manual Verification Artifacts

Date: 2026-06-07

Milestone 8 requires desktop and mobile verification for the ticket list, ticket detail,
and run console. The Playwright e2e suite includes a screenshot-generating verification
case:

```bash
cd frontend
mise exec -- pnpm run test:e2e -- --grep "frontend visual verification artifacts"
```

By default it writes:

- `/tmp/techbold-frontend-verification/desktop-ticket-detail-run-console.png`
- `/tmp/techbold-frontend-verification/mobile-ticket-detail-run-console.png`

The captured scenario uses mocked API data and shows:

- ticket overview with technician identity, filters, priority/status/date metadata, and a selected ticket;
- ticket detail with customer system host, port, username, OS, and notes;
- run console with connection state, pending command approval, live terminal transcript,
  logs/files checked, backup/rollback state, required validation checks, and activity review area.

Manual review checklist:

- Desktop: ticket rail, detail pane, and run console are visible without overlap at `1440x1000`.
- Mobile: the same workflow remains readable at `390x1100`, with sections stacked and no clipped control text.
- No Phoenix token, SSH key, `.env` value, or raw secret-like command output appears in either screenshot.
