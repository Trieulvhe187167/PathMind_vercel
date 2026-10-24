# PathMind on Vercel

The root `vercel.json` deploys two Vercel Services (currently Beta): the Next.js
frontend in `web/` and a small FastAPI gateway in `cloud-gateway/`. Public API,
file and WebSocket requests pass through the gateway on the same domain.

The full Python application runs in a named, resumable Vercel Sandbox. A Vercel
Drive mounted at `/runtime` preserves accounts, signing keys, settings, uploaded
documents, chat history, and the SQLite/vector files used by existing features.
The native entrypoint refuses to start without that mount. Relational billing
and usage tables use Neon PostgreSQL. The gateway takes a PostgreSQL transaction
advisory lock before starting/resuming a backend, so competing function instances
cannot start competing writers. Production and preview branches have distinct
Sandbox names, Drives, and PostgreSQL schemas.

## Resources and environment

Link the repository root to a Vercel project, connect GitHub, and attach a Neon
database. Configure these variables in production and preview:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Pooled Neon connection, used for gateway coordination |
| `DATABASE_URL_UNPOOLED` | Direct Neon connection, supports native schema search path |
| `PATHMIND_GATEWAY_SECRET` | Random secret of at least 32 characters; private transport authentication |
| `PATHMIND_BOOTSTRAP_TOKEN` | Separate random secret; initial owner setup only |
| `PATHMIND_SECRETS_KEY` | Stable random application encryption key; preserve it for existing encrypted settings |
| `PATHMIND_BACKEND_SNAPSHOT_ID` | Vercel Sandbox snapshot containing the Python environment below |
| `NEXT_PUBLIC_AUTH_ENABLED` | `true` |
| `PATHMIND_AUTH_ENABLED` | `true` |

Vercel supplies the Git SHA, branch, hostname and runtime OIDC credentials. Use
Git deployments so every backend is tied to an immutable commit. The gateway
does not forward Vercel control-plane credentials to the native application.
Never commit `.env*`, `.vercel/`, user data or credentials.

## Dependency image

Create a Sandbox in `sin1`, clone the GitHub repository into
`/vercel/PathMind_vercel`, then create Python 3.12 at
`/vercel/pathmind-venv`. Install `requirements/vercel-native.lock.txt` with uv/pip.
Capture a Sandbox snapshot with no expiration and store its ID in the Vercel
environment variable above. Keep the snapshot while deployments reference it.
When changing native dependencies, build a new snapshot and update the variable
before deploying the new Git commit. Application-only updates fetch the new
commit without reinstalling packages.

The gateway creates `pathmind-production-api` and a 1 GiB
`pathmind-runtime-production` Drive lazily. Preview resource names include a
hash of their branch. Delete unused preview resources through Vercel once they
are no longer needed. Never attach the production Drive to a preview.

## First owner and model settings

Open `/api/cloud/bootstrap?token=<PATHMIND_BOOTSTRAP_TOKEN>` privately as the
project owner. It sets a secure, HttpOnly, 15-minute setup cookie and redirects
to registration. The first account becomes the administrator under PathMind's
existing policy. Public first registration is blocked; the setup link stops
working after an active administrator exists. Enter your own password in the
browser, then configure your model provider through the application settings.
Existing local accounts, uploads and AI credentials are not copied automatically.

## Runtime limits and verification

The gateway and backend must become ready together: verify the frontend,
`/health/ready`, `/api/auth/status`, owner creation/login, streaming and WebSocket
reconnection. Direct requests to the native Sandbox without the gateway header
must receive 403. Test a stop/resume and confirm a synthetic file on the Drive
persists before storing user data.

Sandbox sessions are configured for 30 minutes. The gateway resumes/restarts
the backend when needed; WebSockets must reconnect after session or function
timeouts. Work in flight at shutdown can be interrupted. This is suitable for
interactive use within the account's quotas, not uninterrupted workers. Native
scheduled jobs run only while the Sandbox is awake. Hobby resource, storage,
function duration and upload-size limits still apply; requests through Functions
are subject to their payload limits. This configuration does not purchase or
upgrade a plan. Monitor Sandbox, Drive and Neon usage in Vercel.

Neon and Drive backups have different ownership: export/back up both the
PostgreSQL schema and Drive files, preserving `PATHMIND_SECRETS_KEY`. A Sandbox
dependency snapshot is not a backup of the mounted Drive. Blob storage may be
attached to the project but is not the application's filesystem in this setup.

References: [Services](https://vercel.com/docs/services),
[Drives](https://vercel.com/docs/sandbox/concepts/drives),
[Python Sandbox SDK](https://vercel.com/docs/sandbox/python-sdk-reference),
[WebSockets](https://vercel.com/docs/functions/websockets).
