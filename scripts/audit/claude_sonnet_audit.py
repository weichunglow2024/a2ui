#!/usr/bin/env python3
# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Programmatic Independent Verification Audit Script using Claude Sonnet 5 on Vertex AI.

Executes all test suites, validates CLI commands, captures rendered snapshots, and invokes
Claude Sonnet 5 for independent forensic review against requirements R1-R5.
"""

import base64
import os
from pathlib import Path
import subprocess
import sys
from anthropic import AnthropicVertex
import google.auth
from google.auth import impersonated_credentials

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RENDER_OUTPUT_PNG = Path("/tmp/claude_audit_render.png")
AUDIT_REPORT_PATH = PROJECT_ROOT / "AUDIT_REPORT.md"


def get_clean_env() -> dict[str, str]:
    """Returns environment with ~/.local/bin in PATH."""
    env = os.environ.copy()
    local_bin = os.path.expanduser("~/.local/bin")
    current_path = env.get("PATH", "")
    if local_bin not in current_path.split(os.pathsep):
        env["PATH"] = f"{local_bin}:{current_path}"
    return env


def run_cmd(cmd: list[str], cwd: Path = PROJECT_ROOT) -> tuple[int, str, str]:
    """Executes a command and returns (returncode, stdout, stderr)."""
    print(f"Executing: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=get_clean_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    print("=" * 70)
    print("A2UI Independent Claude Sonnet 5 Verification Audit Runner")
    print("=" * 70)

    # 1. Setup Vertex AI client with Service Account Impersonation
    print(
        "\n[Step 1/5] Authenticating to Vertex AI via Service Account Impersonation..."
    )
    source_creds, _ = google.auth.default()
    target_creds = impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal="gab-sa@default-project-376801.iam.gserviceaccount.com",
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=3600,
    )
    client = AnthropicVertex(
        region="global",
        project_id="default-project-376801",
        credentials=target_creds,
    )
    print("✓ Successfully created AnthropicVertex client targeting region 'global'.")

    # 2. Programmatically execute test suites
    print("\n[Step 2/5] Executing automated test suites...")
    test_suites = [
        ("agent_sdks/python/a2ui_agent/tests/cli/", "CLI Unit & Contract Suite"),
        ("agent_sdks/python/a2ui_agent/tests/render/", "Headless Render Engine Suite"),
        ("tools/a2ui_mcp/tests/", "FastMCP Perception-Action Server Suite"),
        ("tests/e2e/", "End-to-End Opaque-Box Suite (Tiers 1-4)"),
    ]

    test_transcripts = {}
    for test_dir, name in test_suites:
        cmd = ["uv", "run", "pytest", test_dir, "-v"]
        code, out, err = run_cmd(cmd)
        test_transcripts[name] = {
            "cmd": " ".join(cmd),
            "exit_code": code,
            "stdout": out,
            "stderr": err,
        }
        status = "PASSED" if code == 0 else f"FAILED (exit {code})"
        print(f"  - {name}: {status}")
        if code != 0:
            print(f"ERROR running {name}:\n{out}\n{err}")
            return 1

    # 3. Programmatically execute CLI verification commands
    print("\n[Step 3/5] Executing CLI verification commands...")
    cli_commands = [
        {
            "name": "Check Valid Basic Payload",
            "cmd": [
                "uv",
                "run",
                "a2ui",
                "check",
                "tests/e2e/fixtures/basic_button.json",
            ],
            "expected_exit": 0,
        },
        {
            "name": "Check Typo Payload (Suggestions)",
            "cmd": [
                "uv",
                "run",
                "a2ui",
                "check",
                "tests/e2e/fixtures/invalid_property.json",
            ],
            "expected_exit": 1,
        },
        {
            "name": "Check Gemini Enterprise Incompatible Version (v0.9.1)",
            "cmd": [
                "uv",
                "run",
                "a2ui",
                "check",
                "tests/e2e/fixtures/incompatible_version_091.json",
                "--target",
                "gemini-enterprise",
            ],
            "expected_exit": 1,
        },
        {
            "name": "Check Gemini Enterprise Valid Payload",
            "cmd": [
                "uv",
                "run",
                "a2ui",
                "check",
                "tests/e2e/fixtures/valid_ge_payload.json",
                "--target",
                "gemini-enterprise",
            ],
            "expected_exit": 0,
        },
        {
            "name": "Catalog Describe Basic",
            "cmd": ["uv", "run", "a2ui", "catalog", "describe", "basic"],
            "expected_exit": 0,
        },
        {
            "name": "Catalog Diff Basic to Gemini Enterprise Composite",
            "cmd": [
                "uv",
                "run",
                "a2ui",
                "catalog",
                "diff",
                "basic",
                "gemini_enterprise_composite",
            ],
            "expected_exit": 0,
        },
        {
            "name": "Render Basic Button Payload to PNG",
            "cmd": [
                "uv",
                "run",
                "a2ui",
                "render",
                "tests/e2e/fixtures/basic_button.json",
                "--png",
                str(RENDER_OUTPUT_PNG),
            ],
            "expected_exit": 0,
        },
    ]

    cli_transcripts = {}
    for item in cli_commands:
        code, out, err = run_cmd(item["cmd"])
        cli_transcripts[item["name"]] = {
            "cmd": " ".join(item["cmd"]),
            "expected_exit": item["expected_exit"],
            "actual_exit": code,
            "stdout": out,
            "stderr": err,
        }
        match_str = (
            "MATCH" if code == item["expected_exit"] else f"MISMATCH (got {code})"
        )
        print(f"  - {item['name']}: {match_str}")
        if code != item["expected_exit"]:
            print(
                f"ERROR: Exit code mismatch for {item['name']}! Expected"
                f" {item['expected_exit']}, got {code}"
            )
            return 1

    # Check that rendered PNG exists and is non-empty
    if not RENDER_OUTPUT_PNG.exists() or RENDER_OUTPUT_PNG.stat().st_size == 0:
        print(
            f"ERROR: Render output PNG at {RENDER_OUTPUT_PNG} does not exist or is"
            " empty."
        )
        return 1
    png_bytes = RENDER_OUTPUT_PNG.read_bytes()
    png_base64 = base64.b64encode(png_bytes).decode("utf-8")
    print(f"✓ Rendered PNG captured: {len(png_bytes)} bytes at {RENDER_OUTPUT_PNG}")

    # Capture git status and diff summary
    _, git_status, _ = run_cmd(["git", "status", "-s"])
    _, git_diff_stat, _ = run_cmd(["git", "diff", "--stat"])

    # 4. Construct prompt and invoke Claude Sonnet 5
    print("\n[Step 4/5] Sending Multimodal Audit Request to Claude Sonnet 5...")

    prompt_text = f"""You are an Independent Forensic Software Auditor conducting a formal, rigorous verification audit of the open-source A2UI Developer Toolchain (Python SDK, CLI, FastMCP Perception-Action Server, Headless Render Engine, and Host Target Profiles).

### AUDIT SCOPE & OBJECTIVES
You must evaluate empirical test and runtime evidence across all five core requirements:
1. **R1: Headless CLI (`a2ui check`, `a2ui catalog describe`, `a2ui catalog diff`)**
   - Did `a2ui check` exit 0 on valid payloads?
   - Did `a2ui check` catch typos and suggest corrections with exit code 1?
   - Did `a2ui catalog describe` output component inventory, required props, and reference topology?
   - Did `a2ui catalog diff` accurately identify all 34 added components between `basic` and `gemini_enterprise_composite`?
2. **R2: Headless Rendering Engine (`a2ui render --png`) & Visual Verification**
   - Inspect the attached rendered PNG image generated from `tests/e2e/fixtures/basic_button.json`.
   - Evaluate visual fidelity: Does it properly display the button, primary styling, and text?
   - Confirm headless Chromium rendering lifecycle (`whenSettled`, `updateComplete`) functioned without browser crashes or hanging.
3. **R3: `a2ui-mcp` Perception-Action Server**
   - Review the FastMCP test suite results for `a2ui_list_components`, `a2ui_validate`, `a2ui_render`, and `a2ui_simulate_action`.
   - Verify zero-browser action dispatching with in-memory `SurfaceModel` and dynamic data context binding.
   - Verify multimodal image perception returning valid `ImageContent`.
4. **R4: Host Target Profiles & Gemini Enterprise Specifications**
   - Review target profile validation: Did `a2ui check --target gemini-enterprise` reject incompatible version `0.9.1` with exit code 1?
   - Did `a2ui check --target gemini-enterprise` pass the valid GE payload with exit code 0?
   - Confirm adherence to wire version `v0.9`, MIME `application/json+a2ui`, and quirks (`side-panel-requires-canvas-root`, `mime-exact-match`).
5. **R5: Automated Test Suite & Code Quality**
   - Analyze the complete test results across CLI, Render, FastMCP, and E2E suites.
   - Verify 100% test pass rate and absence of any skipped/failing critical paths.

---

### EMPIRICAL EVIDENCE COLLECTED

#### 1. Automated Test Suite Transcripts
"""
    for name, data in test_transcripts.items():
        prompt_text += f"\n##### {name}\n"
        prompt_text += f"Command: `{data['cmd']}` | Exit Code: `{data['exit_code']}`\n"
        prompt_text += f"```text\n{data['stdout'][-3000:]}\n```\n"

    prompt_text += "\n#### 2. CLI Command Transcripts\n"
    for name, data in cli_transcripts.items():
        prompt_text += f"\n##### {name}\n"
        prompt_text += (
            f"Command: `{data['cmd']}` | Expected Exit: `{data['expected_exit']}` |"
            f" Actual Exit: `{data['actual_exit']}`\n"
        )
        prompt_text += f"```text\n{data['stdout'].strip()}\n```\n"

    prompt_text += f"""
#### 3. Repository State & Diff Summary
```text
Git Status:
{git_status.strip()}

Git Diff Stat:
{git_diff_stat.strip()}
```

---

### AUDIT REPORT INSTRUCTIONS
Produce a comprehensive, publication-ready **Formal Verification Audit Report** in markdown format.
Keep each section substantive yet concise (~200-300 words per section) to ensure all 7 sections are completely generated and properly concluded with the official sign-off without being truncated.

Structure your report with:
1. **Executive Summary & Audit Verdict**: Clear PASS/FAIL statement and summary of findings.
2. **Acceptance Criteria Verification Matrix**: Table summarizing R1 through R5 with Claim, Empirical Evidence, and Verification Status (VERIFIED PASS / FAIL).
3. **Multimodal Visual Inspection of Rendered Artifact**: Critique of the attached rendered PNG image (`/tmp/claude_audit_render.png`) evaluating visual fidelity, button styling, and layout.
4. **Wire Contract & Host Profile Audit**: Analysis of Gemini Enterprise compatibility (versions, MIME, quirks, error handling).
5. **FastMCP Perception-Action Loop Evaluation**: Analysis of MCP tools, multimodal image output, in-memory action dispatching, and concurrency.
6. **Test Coverage, Reliability & Edge-Case Assessment**: Review of test breadth (Tiers 1-4, boundary conditions, edge cases, 100% pass rate).
7. **Final Auditor Certification & Sign-off**: Formal certification and official sign-off signature block.
"""

    messages = [{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": png_base64,
                },
            },
            {
                "type": "text",
                "text": prompt_text,
            },
        ],
    }]

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=8192,
        messages=messages,
    )

    report_content = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )

    # 5. Write AUDIT_REPORT.md
    print(f"\n[Step 5/5] Writing verbatim response to {AUDIT_REPORT_PATH}...")
    AUDIT_REPORT_PATH.write_text(report_content, encoding="utf-8")
    print(f"✓ Successfully wrote AUDIT_REPORT.md ({len(report_content)} characters).")

    print("\n" + "=" * 70)
    print("Claude Sonnet 5 Verification Audit Completed Successfully!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
