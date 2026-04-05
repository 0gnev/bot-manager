# GitHub Actions CI/CD

This project uses two GitHub Actions workflows:

- `CI`: installs Python dependencies, runs `pytest`, and validates the Bridge Docker image build
- `CD`: deploys `main` to the production server after CI succeeds

## Required repository secrets

Add these secrets in GitHub:

- `DEPLOY_HOST`: production server hostname or IP
- `DEPLOY_USER`: SSH user for deployments
- `DEPLOY_PATH`: absolute path to the repo on the server, for example `/opt/bot-manager`
- `DEPLOY_SSH_KEY`: private SSH key used by GitHub Actions
- `DEPLOY_KNOWN_HOSTS`: pinned `known_hosts` entry for the server
- `DEPLOY_PORT`: optional SSH port, defaults to `22`

## How deploy works

The deploy workflow SSHes into the server and runs:

```bash
bash scripts/deploy-server.sh main
```

That script:

1. fetches the latest Git refs
2. checks out `main`
3. updates the Python virtualenv
4. runs `pytest` on the server
5. runs `docker compose --env-file .env up -d --build --force-recreate`

The `--force-recreate` flag is important here because `bridge` uses bind mounts for
`app/` and `config/`. A plain `up -d --build` can leave the existing container
running with the old Python process, while `--force-recreate` guarantees that the
deployed code is actually picked up.

If tests fail on the server, deployment stops before containers are recreated.

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
