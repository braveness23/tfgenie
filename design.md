# tfgenie — Design Document

## What It Is

An AI-powered Terraform HCL generator, implemented as an OpenClaw skill (no compiled code required). It generates accurate, production-ready `.tf` files from live cloud resources by using Terraform's own engine as the source of truth.

---

## Core Concept

**Don't guess what Terraform needs — let Terraform tell you.**

Instead of inferring resource schemas or guessing field values, tfgenie converges to a correct HCL config through an iterative import/plan loop. The exit condition (`terraform plan` shows zero drift) *is* the correctness proof.

---

## How It Works

### The Loop

```
Input: resource ID (in terraform import format)
  → Create bare stub HCL:  resource "type" "name" {}
    → terraform import      (pulls live state into .tfstate)
      → terraform plan -json (reveals drift between stub and real state)
        → AI: parse plan diff
               - identify fields that are user-configurable
               - skip computed fields (id, arn, etag, timestamps, etc.)
               - output structured patch: {"add": {...}, "remove": [...]}
          → patch HCL with diff
            → repeat from plan step
              → exit when plan shows zero changes
                → output validated HCL
```

### Termination

- **Success**: `terraform plan` exits clean (no changes)
- **Failure**: max iteration limit reached → emit warning, output best-effort HCL for manual review

---

## AI's Specific Role

The AI subagent is scoped narrowly to one job: **parse `terraform plan -json` output and classify fields**.

Input: raw plan JSON
Output: structured diff
```json
{
  "add": {
    "tags": { "Name": "web-prod" },
    "instance_type": "t3.micro"
  },
  "remove": ["id", "arn", "private_ip"]
}
```

Rules the AI applies:
- **Add** fields that are user-controlled and show as drift
- **Remove** fields that Terraform computes (never set by the user)
- **Skip** fields that are already correct in the stub
- Flag circular dependencies or ambiguous cases for human review

The AI does NOT generate HCL from scratch — it patches toward convergence.

---

## Architecture

### Implementation: Pure OpenClaw Skill

No binary, no compilation. The skill orchestrates:
- `exec` tool → shells out to `terraform import`, `terraform plan -json`
- Subagent calls → AI sessions for plan diff parsing
- Loop logic → encoded in skill prompt / flow control
- `cron` integration → enables watch mode

### Skill Structure

```
~/.openclaw/workspace/skills/tfgenie/
├── SKILL.md          # Skill metadata and invocation docs
├── prompts.md        # AI prompt templates (diff parser, stub generator)
├── design.md         # This document (symlinked or copied from repo)
└── config/
    └── gitlab.json   # Example provider auth template
```

---

## Planned Commands

| Command | Description |
|---|---|
| `/tfgenie import [resource_id]` | Generate HCL from a single live resource |
| `/tfgenie bulk --provider gitlab` | Bulk import all resources of a type |
| `/tfgenie watch --provider [aws\|gitlab]` | Cron-based auto-discovery of new resources |

---

## Why Not Existing Tools

| Tool | Problem |
|---|---|
| **Terraformer** | Dumps all fields including computed ones; output is messy and non-idiomatic |
| **Schema-based generators** | Miss provider quirks, edge cases, non-standard field mappings |
| **tfgenie** | Terraform validates correctness by construction; works with any provider |

---

## Provider Support

Provider-agnostic by design — works with anything `terraform import` supports. GitLab is the first targeted test provider.

---

## Key Design Decisions

- **Stub-first**: Always start from the minimal valid stub, never from a schema dump
- **Plan as oracle**: `terraform plan` is the only correctness check that matters
- **AI scope is narrow**: AI parses diffs only; it does not write HCL directly
- **Iterative convergence**: Multiple passes are expected and normal; single-pass is a bonus
- **No code required (v1)**: Pure skill orchestration via OpenClaw's exec + subagent tools
