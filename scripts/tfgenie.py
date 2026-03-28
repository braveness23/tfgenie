#!/usr/bin/env python3
"""
tfgenie.py — Terraform HCL generator helper script.

Subcommands (called by Sparky via exec):
  setup   <resource_type> <resource_name> [--base-url <url>]  — create workdir, write stubs, terraform init
  import  <workdir> <address> <resource_id>  — terraform import
  schema  <workdir> <resource_type>          — get provider schema: required/optional/computed fields
  show    <workdir>                           — terraform show -json, returns all state attributes
  plan    <workdir>                           — terraform plan -json, output status + drift summary
  patch   <workdir> <patch_json>             — apply add/remove patch to main.tf
  result  <workdir>                          — print final main.tf
  cleanup <workdir>                          — delete workdir
"""

import json
import os
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Provider snippets — minimal providers.tf per provider prefix
# ---------------------------------------------------------------------------

PROVIDER_SNIPPETS = {
    "aws": """\
terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

provider "aws" {}
""",
    "aws": """\
terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

provider "aws" {}
""",
    "google": """\
terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}

provider "google" {}
""",
    "azurerm": """\
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}
""",
    "github": """\
terraform {
  required_providers {
    github = {
      source = "integrations/github"
    }
  }
}

provider "github" {}
""",
}


ALL_PROVIDERS = set(list(PROVIDER_SNIPPETS.keys()) + ["gitlab", "google", "azurerm", "github"])


def infer_provider(resource_type):
    prefix = resource_type.split("_")[0]
    if prefix in ALL_PROVIDERS:
        return prefix
    return None


def build_providers_tf(provider, base_url=None):
    """Generate providers.tf content, with optional base_url for supported providers."""
    if provider == "gitlab":
        base_url_line = f'\n  base_url = "{base_url}"' if base_url else ""
        return f"""\
terraform {{
  required_providers {{
    gitlab = {{
      source = "gitlabhq/gitlab"
    }}
  }}
}}

provider "gitlab" {{{base_url_line}
}}
"""
    if provider == "google":
        return """\
terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}

provider "google" {}
"""
    if provider == "azurerm":
        return """\
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}
"""
    if provider == "github":
        return """\
terraform {
  required_providers {
    github = {
      source = "integrations/github"
    }
  }
}

provider "github" {}
"""
    return PROVIDER_SNIPPETS.get(provider, "")


# ---------------------------------------------------------------------------
# Functionally-required fields per resource type
# These are schema-optional but must be explicitly set for correct recreation.
# They won't show as plan drift because terraform already has them in state.
# ---------------------------------------------------------------------------

FUNCTIONALLY_REQUIRED = {
    "gitlab_project": ["namespace_id"],
    "aws_instance": ["ami", "instance_type", "subnet_id"],
    "aws_s3_bucket": ["bucket"],
    "aws_security_group": ["vpc_id"],
    "aws_db_instance": ["allocated_storage", "engine", "instance_class", "username", "db_subnet_group_name"],
    "github_repository": ["name"],
}


def run(cmd, cwd=None, capture=False):
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=capture, text=True
    )
    return result


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def cmd_setup(resource_type, resource_name, base_url=None):
    provider = infer_provider(resource_type)
    if provider is None:
        print(json.dumps({
            "error": f"Unknown provider prefix for resource type '{resource_type}'. "
                     f"Supported: {', '.join(sorted(ALL_PROVIDERS))}"
        }))
        sys.exit(1)

    workdir = tempfile.mkdtemp(prefix="tfgenie-")

    # Write stub main.tf
    stub = f'resource "{resource_type}" "{resource_name}" {{}}\n'
    with open(os.path.join(workdir, "main.tf"), "w") as f:
        f.write(stub)

    # Write providers.tf
    with open(os.path.join(workdir, "providers.tf"), "w") as f:
        f.write(build_providers_tf(provider, base_url=base_url))

    # terraform init (quiet)
    result = run(["terraform", "init", "-no-color"], cwd=workdir, capture=True)
    if result.returncode != 0:
        print(json.dumps({
            "error": "terraform init failed",
            "stderr": result.stderr,
            "stdout": result.stdout,
            "workdir": workdir,
        }))
        sys.exit(1)

    print(json.dumps({
        "status": "ok",
        "workdir": workdir,
        "provider": provider,
        "resource_type": resource_type,
        "resource_name": resource_name,
    }))


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def cmd_import(workdir, address, resource_id):
    result = run(
        ["terraform", "import", "-no-color", address, resource_id],
        cwd=workdir,
        capture=True,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        print(json.dumps({
            "status": "error",
            "returncode": result.returncode,
            "output": output,
        }))
        sys.exit(1)

    print(json.dumps({
        "status": "ok",
        "output": output,
    }))


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def cmd_schema(workdir, resource_type):
    result = run(["terraform", "providers", "schema", "-json"], cwd=workdir, capture=True)
    if result.returncode != 0:
        print(json.dumps({"status": "error", "output": result.stderr}))
        sys.exit(1)

    try:
        schema_data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "output": "Could not parse schema JSON"}))
        sys.exit(1)

    # Find the resource schema — search all provider schemas
    resource_schema = None
    for provider_schema in schema_data.get("provider_schemas", {}).values():
        resources = provider_schema.get("resource_schemas", {})
        if resource_type in resources:
            resource_schema = resources[resource_type]
            break

    if resource_schema is None:
        print(json.dumps({"status": "error", "output": f"Resource type '{resource_type}' not found in schema"}))
        sys.exit(1)

    attributes = resource_schema.get("block", {}).get("attributes", {})
    block_types = resource_schema.get("block", {}).get("block_types", {})

    required = []
    optional = []
    computed_only = []

    for name, attr in attributes.items():
        is_required = attr.get("required", False)
        is_optional = attr.get("optional", False)
        is_computed = attr.get("computed", False)

        if is_required:
            required.append(name)
        elif is_computed and not is_optional:
            computed_only.append(name)
        else:
            optional.append(name)

    # Block types (nested blocks) are always optional
    for name in block_types:
        optional.append(name)

    # Add functionally-required fields (schema-optional but needed for correct recreation)
    functional = FUNCTIONALLY_REQUIRED.get(resource_type, [])

    print(json.dumps({
        "status": "ok",
        "resource_type": resource_type,
        "required": sorted(required),
        "functional": functional,
        "optional": sorted(optional),
        "computed_only": sorted(computed_only),
    }))


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def cmd_show(workdir):
    result = run(["terraform", "show", "-json"], cwd=workdir, capture=True)
    if result.returncode != 0:
        print(json.dumps({
            "status": "error",
            "output": result.stderr,
        }))
        sys.exit(1)

    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "output": "Could not parse terraform show output"}))
        sys.exit(1)

    # Extract the resource attributes from state
    attributes = {}
    try:
        resources = state.get("values", {}).get("root_module", {}).get("resources", [])
        if resources:
            attributes = resources[0].get("values", {})
    except (KeyError, IndexError):
        pass

    print(json.dumps({
        "status": "ok",
        "attributes": attributes,
    }))


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def cmd_plan(workdir):
    result = run(
        ["terraform", "plan", "-no-color", "-json"],
        cwd=workdir,
        capture=True,
    )

    # Parse NDJSON plan output
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    messages = []
    changes = []
    plan_status = "unknown"

    for line in lines:
        try:
            msg = json.loads(line)
            messages.append(msg)
            if msg.get("type") == "change_summary":
                body = msg.get("changes", {})
                add = body.get("add", 0)
                change = body.get("change", 0)
                remove = body.get("remove", 0)
                if add == 0 and change == 0 and remove == 0:
                    plan_status = "clean"
                else:
                    plan_status = "drift"
            if msg.get("type") == "planned_change":
                changes.append(msg)
        except json.JSONDecodeError:
            pass

    # Also capture text output for AI consumption
    text_result = run(
        ["terraform", "plan", "-no-color"],
        cwd=workdir,
        capture=True,
    )

    print(json.dumps({
        "status": plan_status,
        "returncode": result.returncode,
        "changes": changes,
        "plan_text": text_result.stdout + text_result.stderr,
        "plan_json_messages": messages,
    }))


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------

def _parse_hcl_body(content, resource_type, resource_name):
    """Extract the lines between the outermost braces of a resource block."""
    pattern = rf'resource\s+"{re.escape(resource_type)}"\s+"{re.escape(resource_name)}"\s*\{{'
    match = re.search(pattern, content)
    if not match:
        return None, None, None

    start = match.end()
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    end = i - 1  # position of closing brace

    header = content[:match.start()]
    body = content[start:end]
    footer = content[end + 1:]  # skip the closing brace (we write it explicitly)
    return header, body, footer


def _value_to_hcl(val, indent=2):
    """Convert a Python value to an HCL-compatible string."""
    pad = " " * indent
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return f'"{val}"'
    if isinstance(val, list):
        items = ", ".join(_value_to_hcl(v) for v in val)
        return f"[{items}]"
    if isinstance(val, dict):
        lines = ["{"]
        for k, v in val.items():
            lines.append(f"{pad}  {k} = {_value_to_hcl(v, indent + 2)}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    return f'"{val}"'


def cmd_patch(workdir, patch_json):
    try:
        patch = json.loads(patch_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid patch JSON: {e}"}))
        sys.exit(1)

    main_tf_path = os.path.join(workdir, "main.tf")
    with open(main_tf_path) as f:
        content = f.read()

    # Find resource type and name from the file
    m = re.search(r'resource\s+"([^"]+)"\s+"([^"]+)"', content)
    if not m:
        print(json.dumps({"error": "Could not find resource block in main.tf"}))
        sys.exit(1)

    resource_type = m.group(1)
    resource_name = m.group(2)

    header, body, footer = _parse_hcl_body(content, resource_type, resource_name)
    if header is None:
        print(json.dumps({"error": "Could not parse resource body"}))
        sys.exit(1)

    # Parse existing lines into a dict of attr -> raw_line
    existing = {}
    body_lines = body.split("\n")
    for line in body_lines:
        stripped = line.strip()
        if stripped and "=" in stripped:
            key = stripped.split("=")[0].strip()
            existing[key] = line

    # Apply removes
    for key in patch.get("remove", []):
        existing.pop(key, None)

    # Apply adds
    for key, val in patch.get("add", {}).items():
        hcl_val = _value_to_hcl(val)
        existing[key] = f"  {key} = {hcl_val}"

    # Reassemble
    new_body = "\n".join(existing[k] for k in existing if existing[k].strip())
    new_content = f'{header}resource "{resource_type}" "{resource_name}" {{\n{new_body}\n}}{footer.rstrip()}\n'

    with open(main_tf_path, "w") as f:
        f.write(new_content)

    print(json.dumps({
        "status": "ok",
        "added": list(patch.get("add", {}).keys()),
        "removed": patch.get("remove", []),
        "main_tf": new_content,
    }))


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------

def cmd_result(workdir):
    main_tf_path = os.path.join(workdir, "main.tf")
    with open(main_tf_path) as f:
        content = f.read()
    print(json.dumps({"status": "ok", "main_tf": content}))


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

def cmd_cleanup(workdir):
    import shutil
    if os.path.isdir(workdir) and workdir.startswith(tempfile.gettempdir()):
        shutil.rmtree(workdir)
        print(json.dumps({"status": "ok", "removed": workdir}))
    else:
        print(json.dumps({"error": f"Refusing to remove: {workdir}"}))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    subcmd = sys.argv[1]

    if subcmd == "setup":
        if len(sys.argv) < 4:
            print("Usage: tfgenie.py setup <resource_type> <resource_name> [--base-url <url>]")
            sys.exit(1)
        base_url = None
        if "--base-url" in sys.argv:
            idx = sys.argv.index("--base-url")
            if idx + 1 < len(sys.argv):
                base_url = sys.argv[idx + 1]
        cmd_setup(sys.argv[2], sys.argv[3], base_url=base_url)

    elif subcmd == "import":
        if len(sys.argv) < 5:
            print("Usage: tfgenie.py import <workdir> <address> <resource_id>")
            sys.exit(1)
        cmd_import(sys.argv[2], sys.argv[3], sys.argv[4])

    elif subcmd == "schema":
        if len(sys.argv) < 4:
            print("Usage: tfgenie.py schema <workdir> <resource_type>")
            sys.exit(1)
        cmd_schema(sys.argv[2], sys.argv[3])

    elif subcmd == "show":
        if len(sys.argv) < 3:
            print("Usage: tfgenie.py show <workdir>")
            sys.exit(1)
        cmd_show(sys.argv[2])

    elif subcmd == "plan":
        if len(sys.argv) < 3:
            print("Usage: tfgenie.py plan <workdir>")
            sys.exit(1)
        cmd_plan(sys.argv[2])

    elif subcmd == "patch":
        if len(sys.argv) < 4:
            print("Usage: tfgenie.py patch <workdir> '<patch_json>'")
            sys.exit(1)
        cmd_patch(sys.argv[2], sys.argv[3])

    elif subcmd == "result":
        if len(sys.argv) < 3:
            print("Usage: tfgenie.py result <workdir>")
            sys.exit(1)
        cmd_result(sys.argv[2])

    elif subcmd == "cleanup":
        if len(sys.argv) < 3:
            print("Usage: tfgenie.py cleanup <workdir>")
            sys.exit(1)
        cmd_cleanup(sys.argv[2])

    else:
        print(f"Unknown subcommand: {subcmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
