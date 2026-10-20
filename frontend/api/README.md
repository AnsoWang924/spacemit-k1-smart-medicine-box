# K1 Frontend API Directory

This directory groups future browser calls by resource name and lifecycle. It is not loaded by the current `index.html`, so deploying these files alone does not change the released UI.

Each resource module exposes only its real lifecycle:

- Face, medicine box, prescription OCR, and pill count: `preload -> business call -> release -> status`.
- ASR: `preload -> start -> stop -> status`.
- Cabinet: `open -> state`.
- QA and TTS: their currently deployed operations only.
- Medication schedule: pure rule-based preview only; it never writes members,
  reminders, cabinet state, or hardware.

Do not migrate a page to this client until the matching gateway contract is verified on K1, Kiosk, and PC. The assistant `release` operation remains restricted until the backend implements shared-service leases.

The client resolves the current 8799 page origin without adding a second port. A page served from another port resolves to that host on 8799; a `data-api-base` value remains the explicit override.

This directory is preparation only. Updating it does not authorize changing existing page calls, gateway routes, service units, or hardware behavior.
