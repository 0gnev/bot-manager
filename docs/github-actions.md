# GitHub Actions CI/CD

This project uses three GitHub Actions workflows:

- `CI`: installs Python dependencies, runs `pytest`, and validates the Bridge Docker image build
- `CD`: deploys `main` to the production server after CI succeeds
- `Live LLM Smoke`: manually starts a real `openclaw` container and runs one
  opt-in smoke test against the configured provider

## Required repository secrets

Add these secrets in GitHub:

- `DEPLOY_HOST`: production server hostname or IP
- `DEPLOY_USER`: SSH user for deployments
- `DEPLOY_PATH`: absolute path to the repo on the server, for example `/opt/bot-manager`
- `DEPLOY_SSH_KEY`: private SSH key used by GitHub Actions
- `DEPLOY_KNOWN_HOSTS`: pinned `known_hosts` entry for the server
- `DEPLOY_PORT`: optional SSH port, defaults to `22`
- `OPENAI_API_KEY`: required for the `Live LLM Smoke` workflow

## How deploy works

The deploy workflow SSHes into the server and runs:

```bash
bash scripts/deploy-server.sh main
```

That script:

1. fetches the latest Git refs
2. checks out `main`
3. runs `docker compose --env-file .env up -d --build --force-recreate`
4. waits for `bridge` to report healthy on `http://127.0.0.1:8081/health`
5. prints recent `bridge` logs and fails if health never comes up

The `--force-recreate` flag is important here because `bridge` uses bind mounts for
`app/` and `config/`. A plain `up -d --build` can leave the existing container
running with the old Python process, while `--force-recreate` guarantees that the
deployed code is actually picked up.

`pytest` runs in `CI`, where the workflow provisions a dedicated PostgreSQL test
service. `CD` no longer re-runs the full test suite on the production server,
because the PostgreSQL-backed tests require separate test infrastructure and were
causing deploys to fail before container recreation.

## Live LLM smoke workflow

`Live LLM Smoke` is intentionally separate from `CI`.

It is manual-only and should be used when you want to verify that:

- `openclaw` boots with the intended project config
- the configured provider credentials are valid
- a simple known booking question does not immediately fall back to escalation

It runs:

- the real `alpine/openclaw` container from [compose.yaml](/private/var/www/bot-manager/compose.yaml)
- the live test [test_openclaw_live.py](/private/var/www/bot-manager/tests/live/test_openclaw_live.py)

It does not replace the deterministic `CI` suite.

The workflow is attached to the `production` GitHub environment so it can reuse
the same provider secrets as deploy. If those secrets are stored only at the
environment level, the job will fail fast during secret validation when the
environment binding is missing.

`GATEWAY_AUTH_TOKEN` is generated per workflow run and is not stored as a
GitHub secret. That is intentional: the token is only used inside the single
runner job between the temporary `openclaw` container and the smoke test.

The workflow also copies [openclaw.json](/private/var/www/bot-manager/config/openclaw.json)
into a writable runtime path under `data/openclaw/` before starting the
container. This avoids OpenClaw startup failures when it tries to persist
plugin auto-enable state back into `OPENCLAW_CONFIG_PATH`.

## Recommended GitHub environment setup

Create a `production` environment in GitHub and attach the deploy secrets to it.

You can also add environment protection rules such as:

- required reviewers before deploy
- branch restrictions to `main`

## Generating `DEPLOY_KNOWN_HOSTS`

Run this from a trusted machine:

```bash
ssh-keyscan -p 22 your-server.example.com
```

Store the exact output in the `DEPLOY_KNOWN_HOSTS` secret.
