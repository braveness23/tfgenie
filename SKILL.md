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
python3 <skill-dir>/scripts/tfgenie.py setup <resource_type> <resource_name>
```

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

### 4. Plan → AI Patch Loop (max 5 iterations)

**4a. Run plan:**
```
python3 <skill-dir>/scripts/tfgenie.py plan <workdir>
```

Output: JSON with `status` ("clean" or "drift"), `plan_text`, `plan_json_messages`.

**4b. If status is "clean" → skip to step 5.**

**4c. If status is "drift" → call the diff-parser subagent:**

Spawn a subagent with this exact prompt (fill in `{plan_text}`):

---
**DIFF-PARSER PROMPT:**

You are a Terraform plan analyzer. Your job is to read a `terraform plan` output and produce a JSON patch that will make the plan clean.

Rules:
1. **Add** fields that are user-configurable and currently missing from the HCL (shown as changes terraform wants to make).
2. **Remove** field names that are computed by Terraform or the cloud provider and should never appear in HCL (things like `id`, `arn`, `etag`, timestamps, fingerprints, internal IDs).
3. **Skip** fields that are already correct — don't add them again.
4. For nested blocks (like `tags`, `settings`), include the full nested value in `add`.
5. If you see a field in the plan that you're unsure about (computed vs configurable), include it in `add` — the next iteration will catch it if it's wrong.

Computed fields to always remove (non-exhaustive):
`id`, `arn`, `owner_id`, `etag`, `checksum`, `created_at`, `updated_at`, `last_modified`, `fingerprint`, `unique_id`, `http_url_to_repo`, `ssh_url_to_repo`, `web_url`, `runners_token`, `request_access_enabled` (if shown as computed)

Output ONLY valid JSON in this exact format — no explanation, no markdown:
```json
{
  "add": {
    "field_name": "value",
    "another_field": 42,
    "tags": { "Name": "example" }
  },
  "remove": ["id", "arn", "computed_field"]
}
```

Here is the terraform plan output:

{plan_text}

---

**4d. Apply the patch:**
```
python3 <skill-dir>/scripts/tfgenie.py patch <workdir> '<patch_json>'
```

Where `<patch_json>` is the JSON output from the subagent.

**4e. Go back to 4a.** Repeat up to 5 total iterations.

**If still not clean after 5 iterations:** Proceed to step 5 anyway, but warn the user that the HCL may need manual adjustment. Show them the remaining plan drift.

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
