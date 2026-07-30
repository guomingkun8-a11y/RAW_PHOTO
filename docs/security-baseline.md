# Security Baseline

Production secrets must come from environment variables or a deployment secret manager. The application keeps JSON fallback support for local compatibility, but strict production mode rejects embedded secrets.

For local development, `.env.local` is loaded automatically and is ignored by both Git and Docker.

## Required steps

1. Copy `.env.example` to the deployment secret store, not into source control.
2. Replace every placeholder with a newly generated value.
3. Rotate any provider credential that was previously stored in `config.json`.
4. Set `CHATGPT2API_STRICT_SECRET_SOURCES=true` in production.
5. Run the audit before deployment:

```bash
uv run python backend/scripts/check_security.py --strict
```

Legacy local configuration can be migrated once with:

```bash
uv run python backend/scripts/migrate_config_secrets.py
```

The audit reports configuration paths only. It never prints secret values.

## Public configuration

Configuration responses redact relay keys, AI review keys, backup credentials, object-storage credentials, Cloudflare cookies, and passwords embedded in URLs. Boolean `has_*` fields indicate whether a credential is configured.

## Container images

The Docker image contains `config.example.json`, not the local `config.json`. This prevents local credentials from being baked into image layers.

## External rotation

Credential rotation is an external provider action and cannot be completed by source-code changes. Rotate Qiniu, relay, backup, proxy, and review-service credentials in their provider consoles before production rollout.
