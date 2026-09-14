# PP Translation Services Portal

Django portal for UAE Federal Public Prosecution translation work orders —
work-order lifecycle, translator assignment, dual approval, completion
certificates, PDF export and LiveKit video conferencing (with recording and
live captions), all with an Arabic (RTL) interface.

The portal is run by **SmartWorld for Language & Technology** under contract
`3922001306` (ceiling 500,000 AED).

---

## Project status

> Last reviewed **2026-09-14** against commit `39f6bad` (2026-09-09). The
> high-priority issues found in that review were fixed the same day on branch
> `fix/review-issues`.

| Area                         | Status |
| ---------------------------- | ------ |
| Core work-order workflow     | ✅ Done — end-to-end script passes (99/99 checks) |
| Translator assignment        | ✅ Done — partial test coverage |
| PDF export & e-mail          | ✅ Done |
| Document upload pipeline     | ✅ Built — files to translate, translations, revision notes; **not deployed yet** |
| Video conferencing           | ✅ Built — needs a LiveKit server; not covered by tests |
| Recording & live captions    | ✅ Built — needs LiveKit Egress / OpenAI; not covered by tests |
| Access control               | ✅ Hardened — per-order read access, scoped approval, POST-only actions |
| Automated test suite         | ⚠️ 39 regression tests + the workflow script; conference flows untested |
| Production deployment        | ⚠️ Security settings ready (`check --deploy` clean); no server config yet |

**Overall:** the portal is a working MVP with every feature in place and the
known high-priority bugs fixed. What stands between it and production is
mostly operational: server configuration, PostgreSQL, and the LiveKit/Egress
setup. See [Remaining work](#remaining-work).

---

## What's done

### Roles

| Role                   | Can do |
| ---------------------- | ------ |
| PP staff (`PP_STAFF`)  | Raise work orders, approve or dispute logged service |
| SmartWorld admin/staff | Accept orders, supply meeting links, log actual service |
| Contract manager       | Accept orders, assign translators, create conference rooms |
| Translator             | Accept/decline assignments, self-assign (AUTO mode), log own service |
| Superuser              | Everything, plus the Django admin |

### Work-order lifecycle

```
DRAFT → SUBMITTED → ACCEPTED → ASSIGNED → PENDING_APPROVAL → COMPLETED
                                                  │
                                                  └→ DISPUTED → (re-log) → PENDING_APPROVAL
```

- **Order creation**: prosecution and prosecutor picker (loaded with HTMX), or
  a typed prosecutor name. Multiple language lines per order, each with its
  translator count and estimated hours or pages. The estimated cost is
  calculated live from the rate card. Order numbers are generated
  automatically (`2026/AO/0001`).
- **Service types**: interpretation (billed hourly), written translation,
  review, and AI review (billed per page).
- **Location types**: onsite, online with a link supplied, or online where
  SmartWorld must supply the link.
- **Acceptance**: by SmartWorld or by the contract manager.
- **Translator assignment**:
  - **MANUAL mode**: the contract manager assigns a translator to each
    language line. The choice is filtered by the translator's languages and
    whether they interpret or translate. The translator then accepts or
    declines, and a decline notifies the CM so they can reassign.
  - **AUTO mode**: translators see matching open lines and assign themselves.
  - When every assignment is completed, the order moves to pending approval.
  - The mode is a singleton `WorkflowConfig`, set in the Django admin.
- **Service logging**: SmartWorld logs actual hours or pages per language, or
  each translator logs their own. Amounts use the language's hourly or page
  rate.
- **Dual approval**: the SmartWorld sign-off is recorded when service is
  logged. PP staff then approve, or dispute with a reason.
- **Completion certificate**: created automatically on approval
  (`2026/SC/0001`) with a 5% VAT line and an invoice number.
- **Contract budget**: a SmartWorld admin finalizes each certificate once it
  is invoiced. The dashboard shows the finalized total, the remaining
  balance, and a usage progress bar against the 500,000 AED ceiling.

### Documents & notifications

- **WeasyPrint PDFs** for work orders and certificates, with the official PP
  logo and UAE emblem embedded as base64.
- **E-mail at every transition**: new order (work-order PDF attached),
  accepted, meeting link, actuals logged, approved (certificate PDF attached),
  disputed, and translator assigned/accepted/declined/all completed.
  `info@swlt.ae` is always copied.

### Work-order files

Documents travel through the portal instead of e-mail. E-mails only carry a
link to the order.

- **Files to translate**: uploaded when the order is created, or later from
  the order page, several at a time. PP staff (for their own or their
  prosecution's orders), SmartWorld and the contract manager can upload them.
- **Translations**: the assigned translator uploads the translation for their
  language line, or SmartWorld / the CM uploads it.
  - On written, review and AI-review orders, a translator must upload the
    translation before logging service.
  - Every version is kept, and the newest is marked "الأحدث".
- **Revision notes**: when a translation has a problem, PP staff, SmartWorld
  or the CM add a note on it.
  - The translator is e-mailed and sees the note on their dashboard.
  - Uploading a corrected version marks the line's notes as addressed.
  - The order can't be approved while any note is open.
- **Access**: files are private.
  - They're stored outside `media/`, which nginx serves publicly, and are
    downloaded only through a logged-in view: nginx `X-Accel-Redirect` in
    production, or 60-second presigned URLs on S3.
  - Translators only see files of orders they're assigned to.
- **Preview**: PDFs and images open in the browser.
- **Limits**: allowed file types, 25 MB per file and 500 MB per order by
  default.
- **Virus scanning** (optional): ClamAV, enabled with
  `DOCUMENT_VIRUS_SCAN=required`. Uploads are refused while the scanner is
  unreachable.
- **Retention** (optional): `manage.py purge_documents` deletes the files of
  completed orders after `DOCUMENT_RETENTION_DAYS`. It's a dry run unless
  `--apply` is given, and the records are kept for audit.
- **Storage**: a local private directory by default, or S3 with
  `DOCUMENT_STORAGE=s3`.

### Video conferencing (LiveKit)

- **Rooms**: one room per order (`pp-2026-AO-0001`), created by a SmartWorld
  admin or the CM. The room's join URL is written into the order.
- **Joining**: token-based. Allowed for SmartWorld, the CM, the order's
  creator, PP staff of the same prosecution, and assigned translators. A
  reconnect token endpoint is included.
- **Recording** via LiveKit Egress: start and stop, a status indicator polled
  by every participant, and a download list on the order page. In production
  downloads are served through nginx `X-Accel-Redirect`.
- **Live captions**:
  - Speech-to-text runs in the browser (Web Speech API).
  - The manager can switch captions on for the whole room; this uses room
    metadata.
  - Captions can be translated by OpenAI `gpt-4o-mini` with a
    legal-translation prompt.

### UI & admin

- Government-portal theme with Arabic RTL layout, Bootstrap 5 and HTMX.
- A dashboard for each role, plus an order timeline, list filters and a
  translator dashboard.
- Every model is registered in the Django admin with inlines. The user admin
  includes the role profile.
- Seed data: 7 prosecutions and 11 languages with rates (`initial_data`
  fixture).

### Developer tooling

- **`.env.example`**: a local configuration template.
- **`run.ps1`**: migrates and starts the dev server on Windows.
- **`manage.py seed_demo`**: creates demo accounts and prosecutors (DEBUG
  only).
- **`GTK_BIN_DIR` setting**: makes WeasyPrint find the GTK/Pango DLLs on
  Windows.
- **`requests` added to `requirements.txt`**: `core/livekit_utils.py` imports
  it, but it was previously missing from the requirements.

---

## Remaining work

### Fixed on `fix/review-issues` (2026-09-14)

Each fix has a regression test in `core/tests.py`.

- [x] **Per-order read access.** Order detail, the PDFs, certificate pages,
      the order list and dashboard activity show only orders the user is
      entitled to:
  - SmartWorld and the CM see every order.
  - PP staff see their own orders and their prosecution's.
  - Translators see orders they're assigned to, plus open orders in their
    languages in AUTO mode.
- [x] **PP approval is scoped** to the approver's prosecution.
- [x] **Login `?next=`** only follows same-host URLs.
- [x] **State-changing views are POST-only** (`@require_POST`): accept, CM
      accept, assignment accept/decline, self-assign and conference create.
- [x] **Prosecutor options are HTML-escaped.**
- [x] **The contract balance moves.** A SmartWorld admin finalizes a
      certificate from its page (the "اعتماد نهائي" button), which counts it
      against the contract.
- [x] **`seed_demo` runs on a default Windows console.**
- [x] **Invoice numbers are unique**: `INV_PP_SWLT_<yymmdd>_<certificate
      sequence>`. Existing certificates keep their old numbers.
- [x] **No numbering race.** Order and certificate numbers are regenerated
      and retried when they collide on the unique constraint.
- [x] **Re-logging keeps translator records**, correcting them in place
      instead of deleting them.
- [x] **Invalid service-log input** re-renders the form with errors instead
      of returning a 500. Each line now needs hours or pages.
- [x] **Production settings.** `DEBUG` defaults to off and a weak secret key
      is refused. HTTPS redirect, secure cookies and HSTS switch on whenever
      `DEBUG` is off.

### Medium priority — gaps & loose ends

- [ ] **Unused status.** `IN_PROGRESS` exists in the model and timeline but is
      never set.
- [ ] **Disputes in translator mode.** Assignments are already `COMPLETED`, so
      only SmartWorld can re-log. Decide what the intended flow is.
- [ ] **No in-app management screens.** The workflow mode (MANUAL/AUTO),
      translator profiles and the rate card can only be managed in the Django
      admin.
- [ ] **Hard-coded values that should come from settings or `.env`:**
  - the contract value and number in `settings.py`
  - `COMPANY_EMAIL`
- [ ] **Duplicated PDF code.** The rendering and logo embedding appear four
      times across `views.py` and `signals.py`. Extract one helper.
- [ ] **Captions only work in Chromium browsers**, because Firefox doesn't
      support the Web Speech API. Document this or add a server-side
      speech-to-text fallback.
- [ ] **Silent e-mail failures.** Mail is sent synchronously with
      `fail_silently=True`, so failures go unnoticed. Consider a task queue
      and logging.

### Testing

- [ ] **Port `test_workflow.py` to `TestCase`s.** It is still a standalone
      script outside `manage.py test`, and it writes to the configured
      database.
- [ ] **Add coverage for:**
  - the full translator assignment flow (MANUAL and AUTO)
  - conference, recording and captions views, with LiveKit and OpenAI mocked
  - certificate and VAT arithmetic
- [ ] **Add CI** (GitHub Actions) to run the tests and
      `makemigrations --check`.

### Deployment & operations

- [ ] **Deploy the document pipeline.** Before `git pull`,
      `pip install -r requirements.txt`, `migrate` and the restart on the
      server:
  - Create `/var/www/pp-portal/private`, owned by `www-data`, mode 750.
  - Set `PRIVATE_FILES_ROOT=/var/www/pp-portal/private` and
    `SITE_URL=https://pp.swlt.ae` in `.env`.
  - In nginx `sites-enabled/pp-portal` (a standalone copy, not a symlink):
    - add `location /internal-documents/ { internal; alias /var/www/pp-portal/private/; }`
    - add `client_max_body_size 100M;` — the 1 MB default rejects uploads
    - run `nginx -t`, then reload
  - Include the private directory in backups.
  - Optional:
    - ClamAV — needs about 1 GB of RAM on a server shared with other sites
    - an S3 bucket
    - a retention period

- [ ] **Raise `SECURE_HSTS_SECONDS`** from the cautious 3600 default to
      31536000 once HTTPS is confirmed stable.
- [ ] **No deployment config in the repo** for gunicorn, nginx (including the
      `/internal-recordings/` internal location), systemd or Docker.
- [ ] **PostgreSQL** is supported through `DB_*` variables but untested in
      this repo.
- [ ] **Operational basics missing:** logging config, static files via
      `collectstatic`, a media/recordings retention policy, and backups.
- [ ] **LiveKit + Egress server setup** isn't documented. `LIVEKIT_HTTP_URL`
      assumes the server runs on the same host.

---

## Local setup (Windows)

### Prerequisites

- **Python 3.12** — `winget install Python.Python.3.12`
- **GTK/Pango** (only needed for PDF export) — via MSYS2:

  ```powershell
  winget install MSYS2.MSYS2
  C:\msys64\usr\bin\bash.exe -lc "pacman -S --noconfirm --needed mingw-w64-x86_64-pango mingw-w64-x86_64-fontconfig"
  ```

### Install

```powershell
git clone https://github.com/hanie841/pp-project.git
cd pp-project

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Configure

Copy `.env.example` to `.env` and adjust as needed. The defaults use SQLite
and print e-mail to the console, so no external services are required.

`DJANGO_DEBUG` defaults to off. Without a `.env`, the app runs in production
mode and refuses to start with the placeholder secret key.

`GTK_BIN_DIR` must point at the directory holding `libgobject-2.0-0.dll`
(`C:\msys64\mingw64\bin` for the MSYS2 install above). Settings prepends it to
`PATH`, so it takes precedence over unrelated copies of those DLLs that other
applications put on the system path. Leave it blank on Linux/macOS.

### Initialise the database

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py loaddata initial_data   # languages + prosecutions
.\.venv\Scripts\python.exe manage.py seed_demo               # demo users (DEBUG only)
```

### Run

```powershell
.\run.ps1          # optional port: .\run.ps1 8080
```

Then open <http://127.0.0.1:8000/>.

## Demo accounts

`seed_demo` creates these. It refuses to run unless `DEBUG=True`.

| Username        | Password   | Role              |
| --------------- | ---------- | ----------------- |
| `pp_staff`      | `pp123`    | PP staff          |
| `admin`         | `admin123` | SmartWorld admin (superuser) |
| `contract_mgr`  | `cm123`    | Contract manager  |
| `translator`    | `tr123`    | Translator (English, Urdu) |

The Django admin is at `/admin/`.

## Tests

The regression tests cover:
- access control, numbering, certificates and service logging
- the document pipeline: uploads, downloads, revision notes, virus scanning
  and retention

 They run under Django's test runner on a throwaway
in-memory database:

```powershell
.\.venv\Scripts\python.exe manage.py test core
```

`test_workflow.py` is an end-to-end script covering the core order lifecycle:

- create, accept and meeting link
- log service, approve and certificate
- PDFs, dispute and HTMX
- e-mail, access control and admin

It writes to the configured database, so point it at a throwaway one:

```powershell
$env:DB_NAME = "$env:TEMP\pp_test.sqlite3"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py loaddata initial_data
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe test_workflow.py
Remove-Item Env:\DB_NAME
```

## Configuration reference

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `DJANGO_DEBUG` | `False` | Debug mode — set `True` for local development |
| `DJANGO_SECRET_KEY` | insecure dev key | Django secret key; 50+ random characters required when debug is off |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma-separated hosts |
| `CSRF_TRUSTED_ORIGINS` | empty | Comma-separated origins, e.g. `https://pp.swlt.ae` |
| `SECURE_SSL_REDIRECT` | `True` (debug off) | Redirect HTTP to HTTPS |
| `SECURE_HSTS_SECONDS` | `3600` (debug off) | HSTS max-age |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | SQLite `db.sqlite3` | Database |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | console backend | Outgoing mail |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | empty | LiveKit credentials |
| `LIVEKIT_WS_URL` | `wss://livekit.swlt.ae` | Browser-facing LiveKit URL |
| `LIVEKIT_HTTP_URL` | `http://127.0.0.1:7880` | Server API (rooms, egress) |
| `RECORDING_ROOT` | `/var/www/gfad-portal/storage/app/recordings` | Where Egress writes recordings |
| `OPENAI_API_KEY` | empty | Caption translation |
| `GTK_BIN_DIR` | empty | GTK/Pango DLLs for WeasyPrint (Windows) |
| `SITE_URL` | `http://127.0.0.1:8000` | Base address for links in e-mails |
| `DOCUMENT_STORAGE` | `local` | `local` (private directory) or `s3` |
| `PRIVATE_FILES_ROOT` | `private/` in the project | Where local document files are stored — never under `media/` |
| `DOCUMENT_MAX_UPLOAD_SIZE` | 25 MB | Per-file limit, in bytes |
| `DOCUMENT_MAX_ORDER_TOTAL_SIZE` | 500 MB | Per-order quota, in bytes |
| `DOCUMENT_ALLOWED_EXTENSIONS` | pdf, doc(x), xls(x), ppt(x), rtf, txt, jpg, jpeg, png, tif(f) | Accepted file types |
| `DOCUMENT_VIRUS_SCAN` | `off` | `required` scans uploads with ClamAV and refuses them while it's down |
| `CLAMD_ADDRESS` | `unix:///var/run/clamav/clamd.ctl` | ClamAV daemon address (`unix://` or `tcp://`) |
| `DOCUMENT_RETENTION_DAYS` | empty (keep forever) | Days after approval before `purge_documents` may delete files |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | empty | S3 settings when `DOCUMENT_STORAGE=s3` |

## Optional services

Both are inert until configured. The rest of the app works without them.

- **LiveKit** (`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`): video conferencing
  and session recording. Recording also requires LiveKit Egress writing to
  `RECORDING_ROOT`.
- **OpenAI** (`OPENAI_API_KEY`): live caption translation.

## Project layout

```
pp_portal/            Django project (settings, urls, wsgi/asgi)
core/
  models.py           Work orders, languages, assignments, approvals, certificates, recordings, documents, review notes
  views.py            All views: orders, approvals, PDFs, translators, conference, recording, captions
  forms.py            Order/language formsets, assignment & service forms
  signals.py          E-mail notifications (+ PDF attachments)
  livekit_utils.py    LiveKit room, token and Egress API helpers
  storage.py          Private document storage (local directory or S3)
  antivirus.py        ClamAV client for scanning uploads
  admin.py            Django admin configuration
  fixtures/           initial_data.json — prosecutions and language rate card
  management/         seed_demo and purge_documents commands
  templatetags/       Arabic number/currency filters
templates/            Dashboard, order pages, conference room, PDF templates
static/               CSS, JS, logos
test_workflow.py      End-to-end workflow test script
run.ps1               Windows dev-server launcher
```

## Tech stack

Django 5, WeasyPrint, django-htmx, Bootstrap 5 (RTL), LiveKit (JS SDK +
Twirp API), OpenAI, SQLite/PostgreSQL, gunicorn, django-storages (S3,
optional), ClamAV (optional).
