---
name: tfgenie
description: Generate production-ready Terraform HCL from live cloud resources using an iterative import/plan/patch loop. Use when the user wants to import existing infrastructure into Terraform, generate .tf files from real resources, or convert live cloud state into code.
metadata: { "openclaw": { "emoji": "🧞", "requires": { "bins": ["terraform", "python3"] } } }
---

# tfgenie — Terraform HCL Generator

Generates validated Terraform HCL from live cloud resources. Uses Terraform's own import/plan engine as the source of truth — not schemas or guesswork.

Helper script: `scripts/tfgenie.py` (relative to this skill directory).
Config: `config.json` (same directory) — provider credentials.

Resolve all paths relative to this skill's directory. The script path is `<skill-dir>/scripts/tfgenie.py`.

---

## Commands

### Import a single resource
User says something like:
- "import my GitLab project ID 12345678 into Terraform"
- "generate HCL for aws_instance i-1234abcd"
- "tfgenie import gitlab_project.myproject 12345678"

**Address format:** `<resource_type>.<resource_name> <resource_id>`

Examples:
- `gitlab_project.myproject 12345678`
- `aws_instance.web i-1234abcd`
- `github_repository.myrepo myrepo`

---

## Workflow: `/tfgenie import`

### 1. Set environment variables from config

Read `config.json`. Map provider credentials to environment variables before running any terraform commands:

| Provider | config key | env var |
|---|---|---|
| gitlab | `providers.gitlab.token` | `GITLAB_TOKEN` |
| gitlab | `providers.gitlab.base_url` | `GITLAB_BASE_URL` |
| aws | `providers.aws.access_key` | `AWS_ACCESS_KEY_ID` |
| aws | `providers.aws.secret_key` | `AWS_SECRET_ACCESS_KEY` |
| aws | `providers.aws.region` | `AWS_DEFAULT_REGION` |
| google | `providers.google.credentials_file` | `GOOGLE_APPLICATION_CREDENTIALS` |
| google | `providers.google.project` | `GOOGLE_PROJECT` |
| azurerm | `providers.azurerm.subscription_id` | `ARM_SUBSCRIPTION_ID` |
| azurerm | `providers.azurerm.tenant_id` | `ARM_TENANT_ID` |
| azurerm | `providers.azurerm.client_id` | `ARM_CLIENT_ID` |
| azurerm | `providers.azurerm.client_secret` | `ARM_CLIENT_SECRET` |
| github | `providers.github.token` | `GITHUB_TOKEN` |

Set these as environment variables in exec calls for all subsequent terraform commands.

If `config.json` doesn't exist or the provider isn't configured, check if the env var is already set in the environment. If neither, tell the user what credential is needed and stop.

### 2. Setup

```
python3 <skill-dir>/scripts/tfgenie.py setup <resource_type> <resource_name> [--base-url <url>]
```

For GitLab, if `config.json` has `providers.gitlab.base_url`, append `--base-url <value>` to this command.

Output: JSON with `workdir`. Save this — it's needed for all subsequent calls.

If error: report to user and stop.

### 3. Import

```
python3 <skill-dir>/scripts/tfgenie.py import <workdir> <resource_type>.<resource_name> <resource_id>
```

Pass the provider env vars to this exec call.

If error: check output for common issues:
- "Invalid resource ID" → ask user to verify the ID format
- Auth errors → ask user to check credentials in config.json
- "Resource not found" → resource may not exist or ID may be wrong

### 4. Get schema — identify required fields

```
python3 <skill-dir>/scripts/tfgenie.py schema <workdir> <resource_type>
```

Output: JSON with `required`, `optional`, `computed_only` field lists.

### 5. Seed only required fields from state

```
python3 <skill-dir>/scripts/tfgenie.py show <workdir>
```

Output: JSON with `attributes` — full live state values.

From the `attributes`, extract only the fields listed in `required` from the schema output. Build a patch containing only those fields and apply it:

```
python3 <skill-dir>/scripts/tfgenie.py patch <workdir> '<patch_json>'
```

**Goal:** The stub now has just enough to make `terraform plan` run — nothing more.

### 6. Plan → AI Patch Loop (max 5 iterations)

This loop produces **minimal HCL** — only fields that differ from provider defaults. Terraform itself is the oracle: if a field is at its default, it won't appear as drift.

**6a. Run plan:**
```
python3 <skill-dir>/scripts/tfgenie.py plan <workdir>
```

Output: JSON with `status` ("clean" or "drift"), `plan_text`, `plan_json_messages`.

**6b. If status is "clean" → skip to step 7.**

**6c. If status is "drift" → call the diff-parser subagent:**

Spawn a subagent with this exact prompt (fill in `{plan_text}`):

---
**DIFF-PARSER PROMPT:**

You are a Terraform plan analyzer. Your job is to read a `terraform plan` output and produce a JSON patch of fields to add to the HCL to eliminate the drift.

Rules:
1. **Add** only fields shown as drift in the plan (fields terraform wants to change). These are non-default values that must be explicitly set.
2. **Remove** any fields currently in the HCL that are computed-only (shown as known after apply, never user-settable).
3. **Do not add** fields that are already correct or not shown in the plan — if it's not drifting, it's already at its default and should be omitted.
4. For nested blocks, include the full nested value.
5. When unsure whether a drifting field is configurable vs computed, include it — the next iteration will catch it.

Computed fields to always remove if present:
`id`, `arn`, `owner_id`, `etag`, `checksum`, `created_at`, `updated_at`, `last_modified`, `fingerprint`, `unique_id`, `http_url_to_repo`, `ssh_url_to_repo`, `web_url`, `runners_token`

Provider-specific fields to skip (known to cause plan errors when set alone):
- `mirror` (gitlab_project) — requires `import_url` alongside it; omit unless actually mirroring

Output ONLY valid JSON in this exact format — no explanation, no markdown:
```json
{
  "add": {
    "field_name": "value",
    "another_field": 42,
    "tags": { "Name": "example" }
  },
  "remove": ["computed_field"]
}
```

Here is the terraform plan output:

{plan_text}

---

**6d. Apply the patch:**
```
python3 <skill-dir>/scripts/tfgenie.py patch <workdir> '<patch_json>'
```

**6e. Go back to 6a.** Repeat up to 5 total iterations.

**If still not clean after 5 iterations:** Proceed anyway, warn the user the HCL may need manual adjustment, show them the remaining drift.

### 5. Output result

```
python3 <skill-dir>/scripts/tfgenie.py result <workdir>
```

Present the HCL to the user in a code block. Tell them what provider/resource it's for.

### 6. Cleanup

```
python3 <skill-dir>/scripts/tfgenie.py cleanup <workdir>
```

Always clean up, even on error.

---

## Watch Mode (future)

`/tfgenie watch --provider <provider>` — Not yet implemented. Requires cron integration to periodically scan for new resources and auto-generate HCL.

---

## Troubleshooting

**"terraform: command not found"** — Terraform is not installed. On this system it should be at `/snap/bin/terraform`.

**"Error: No suitable version of provider"** — `terraform init` failed to download provider. Check internet connectivity.

**"Error acquiring the state lock"** — A previous run left a lock. Delete `<workdir>/.terraform.tfstate.lock.info` or run cleanup and start fresh.

**Patch loop not converging** — Some providers have computed fields that look configurable. After 5 iterations, present the best-effort HCL and show the user the remaining drift so they can decide.

**Missing credentials** — Check `config.json` in this skill directory. Copy from `config.example.json` if it doesn't exist.
