# Deployment

How to run the reconciliation worklist on a server for several people, keep it safe, and release new versions. For what the app does, see [TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md).

## 1. What runs

| Service | Port (container) | Needed | Notes |
|---|---|---|---|
| `worklist` | 8000 | Yes | The exception worklist and the Reconciliation API. **One process only**: claims, acknowledgments and the change log are held in memory. Never run more than one replica or `--workers`. |
| `proxy` (Caddy) | 80, 443 | For HTTPS | Profile `tls`. Obtains and renews the certificate for `RECON_DOMAIN`. |
| `simulator` | 8100 | Demos only | Profile `simulation`. Synthetic data. Never run it next to a production worklist. |

State is kept on the `recon-output` volume (`/app/output`):

| File | Contents | Lost if deleted |
|---|---|---|
| `users.json` | Accounts: username, name, role, password hash | Nobody can sign in until accounts are added |
| `feedback.json` (`sim/feedback.json` in event mode) | Planner decisions and learned rule confidence | Decisions and learning |
| `sim/corrections.json` | Write-back corrections and their history | The correction audit trail |
| `sim/events.db` | Simulated event log | The simulation's state |
| `recon_*.xlsx/html/json` | Reports from `run` | Nothing (regenerated) |

**Back up the volume.** Claims and acknowledgments are not on it: a restart clears them.

## 2. First deployment (Docker Compose)

Needs Docker Engine 24+ with Compose v2, and the five daily extracts.

```bash
git clone https://github.com/Ajinkya2101/ReconAgent.git && cd ReconAgent
git checkout v0.4.0                     # deploy a release tag, not main
cp .env.example .env                    # then edit it (section 3)
mkdir -p data && cp /path/to/extracts/*.xlsx data/
docker compose up -d --build            # or set RECON_VERSION=0.4.0 and `docker compose pull` (section 6)
docker compose logs worklist            # the demo account passwords, printed once on the first start
curl -s http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.4.0",...}
```

The worklist listens on `127.0.0.1:8000` only. To serve it to other people, put HTTPS in front (section 4).

## 3. Configuration (`.env`)

Required on a server:

| Variable | Set to |
|---|---|
| `RECON_SECRET_KEY` | A long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Without it every restart signs everyone out. Changing it also signs everyone out. |
| `RECON_SOURCE` | `excel` for the daily extracts; `events` for the simulation |

Usually left at the default: `RECON_SESSION_HOURS` (12), `RECON_COOKIE_SECURE` (`on`), `RECON_API_DOCS` (`off`). The full list is in [.env.example](../../.env.example) and [TECHNICAL_OVERVIEW.md §7](TECHNICAL_OVERVIEW.md#7-configuration).

`.env` holds secrets (the OCI private key, the session key). Keep it out of git (`.gitignore` does), readable only by the deploying user (`chmod 600 .env`), and preferably fed from a secret store (OCI Vault) rather than kept on disk.

## 4. HTTPS

Session cookies are `Secure`: browsers send them only over HTTPS (and to `http://localhost`). Serve the worklist over HTTPS.

**Built-in (Caddy):** point a DNS name at the server, open ports 80 and 443, then:

```bash
echo "RECON_DOMAIN=recon.example.com" >> .env
docker compose --profile tls up -d
```

**Your own proxy or load balancer** (nginx, OCI Load Balancer, API Gateway): forward to `127.0.0.1:8000` and

- pass `Host` through, or set `RECON_ALLOWED_ORIGINS=https://recon.example.com`;
- set `X-Forwarded-For` and `X-Forwarded-Proto` (Compose already trusts them: `RECON_FORWARDED_ALLOW_IPS=*`, because the port is published to localhost only);
- do not buffer `/stream` (server-sent events). nginx: `proxy_buffering off; proxy_read_timeout 1h;` for that location;
- set HSTS at the proxy if it terminates TLS for other sites too.

A plain-HTTP test server reached by another name than `localhost` needs `RECON_COOKIE_SECURE=off`. Never use that on a shared URL.

## 5. Accounts

Username and password sign-in. The role belongs to the account; nobody chooses it at sign-in.

| Role | Can |
|---|---|
| `supervisor` | Claim, acknowledge, accept and reject flags; ask the assistant |
| `planner` | All of the above, and approve write-back corrections |
| `manager` | Same as planner |

**Demo accounts.** On the first start, when `output/users.json` does not exist, the worklist creates `planner1`, `planner2`, `supervisor1`, `supervisor2` and `manager1` and prints their random passwords to its log once. Set `RECON_DEMO_PASSWORD` (10+ characters) before the first start to give them all one known password instead. Before real users arrive, change the demo passwords or remove the demo accounts.

```bash
docker compose exec worklist python -m recon_agent users list
docker compose exec worklist python -m recon_agent users add pat.lee --name "Pat Lee" --role planner
docker compose exec worklist python -m recon_agent users passwd planner1      # prompts twice
docker compose exec worklist python -m recon_agent users role pat.lee manager
docker compose exec worklist python -m recon_agent users remove planner2
docker compose exec worklist python -m recon_agent users seed                 # re-create missing demo accounts
```

(`-T` and `--password-stdin` for scripts: `echo "$PW" | docker compose exec -T worklist python -m recon_agent users passwd pat.lee --password-stdin`.)

The name is what appears on claims and decisions. A password change or a removed account ends that person's open sessions at once; a role change applies on their next click. Passwords need at least 10 characters. After 5 failed sign-ins a username is blocked for 15 minutes (50 per network address).

On Windows with `start_container.bat`: `start_container.bat users list`, `start_container.bat users passwd planner1`.

## 6. Versions and releases

The version is `__version__` in [recon_agent/__init__.py](../../recon_agent/__init__.py), following [Semantic Versioning](https://semver.org/): MAJOR for changes that break a deployment (renamed variables, state file formats), MINOR for features, PATCH for fixes. Every change goes in [CHANGELOG.md](../../CHANGELOG.md) under `[Unreleased]`.

`main` is the only long-lived branch ([REPOSITORY_HISTORY.md](REPOSITORY_HISTORY.md)). Work in progress goes on a
short-lived branch that is merged to `main` and then deleted.

To release:

1. Move the `[Unreleased]` entries under a new `## [X.Y.Z] - YYYY-MM-DD` heading and set `__version__ = "X.Y.Z"`.
2. Merge to `main` (CI runs the tests and builds the image on every push and pull request).
3. Tag and push: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.

The **Release** workflow checks that the tag matches `__version__`, runs the tests, publishes `ghcr.io/ajinkya2101/reconagent:X.Y.Z` and `:latest` to GitHub Container Registry, and creates the GitHub release from the CHANGELOG entry. The package is private by default: the server needs `docker login ghcr.io` with a token that has `read:packages`, or make the package public in GitHub.

To upgrade a server:

```bash
git fetch --tags && git checkout vX.Y.Z      # compose file and Caddyfile for that version
docker compose pull worklist                 # with RECON_VERSION=X.Y.Z in .env
docker compose up -d
curl -s http://127.0.0.1:8000/healthz        # shows the new version
```

To roll back, set `RECON_VERSION` to the previous version and run `docker compose up -d` again. Read the CHANGELOG first: a MAJOR version may change the state files.

## 7. Operations

- **Health:** `GET /healthz` (no sign-in) returns `{"status":"ok","version":...,"source":...}`. The image's health check uses it.
- **Logs:** `docker compose logs -f worklist`. Uvicorn writes one access line per request; the app logs reload failures and AI fallbacks.
- **New extracts:** copy the five files into `data/`. The worklist sees the new modification times and reconciles within 2 seconds.
- **Restart:** `docker compose restart worklist`. Claims and acknowledgments are cleared; decisions, corrections and accounts are kept; sessions survive when `RECON_SECRET_KEY` is set.
- **Containers** run as a non-root user (uid 10001) on a read-only filesystem with all capabilities dropped; only `/app/output` and `/tmp` are writable.

## 8. Before you share the URL

- [ ] `RECON_SECRET_KEY` set; `.env` not readable by other users
- [ ] HTTPS in front; `RECON_COOKIE_SECURE` is `on`
- [ ] Demo passwords changed or demo accounts removed; one account per person
- [ ] Deploying a release tag, with `/healthz` showing the expected version
- [ ] The `recon-output` volume is backed up
- [ ] Simulator not running next to a production worklist; if it runs for a demo, `RECON_CORRECTIONS_TOKEN` is set
- [ ] `RECON_API_DOCS` is `off` unless someone needs Swagger UI
- [ ] The OCI API key in `.env` belongs to a least-privilege user that can only call Generative AI in its compartment

## 9. Not covered yet

- Single sign-on (OCI IAM Identity Domains): accounts are local to the app.
- Persistent claims and acknowledgments: they are held in memory, so a restart clears them.
- More than one worklist process (state is per process).
- A limit on assistant questions per person (each AI question is an OCI Generative AI call).
