"""Evolution reflection tools (L1/L2/L3)."""

from pathlib import Path


# L1 — update_prompt_patch
# ---------------------------------------------------------------------------

def build_update_prompt_patch_schema() -> dict:
    return {
        "name": "update_prompt_patch",
        "description": (
            "View, add, or replace your behavioral patches (rules injected into "
            "your system prompt in future sessions).\n\n"
            "**Actions:**\n"
            "- `add` — Append one new patch to the existing list.\n"
            "- `replace_all` — Replace the entire patch list with a new set. "
            "Use this to merge duplicates, remove outdated patches, or reorganize.\n\n"
            "The tool always returns your FULL current patch list after the operation.\n\n"
            "**Quality guidelines (MANDATORY):**\n"
            "- Patches MUST be general principles that apply to ANY project.\n"
            "- A patch MUST NOT mention specific file names, class names, module names, "
            "CLI flags, or library names — if it does, it's too specific.\n"
            "- Test: 'Would this help on a completely different project?' If no, generalize.\n"
            "- Prefer creating a **skill** over a text patch for multi-step workflows.\n"
            "- Do NOT record teammate observations as patches — use L2 tools for that.\n\n"
            "NOTE: Only available during L1 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace_all"],
                    "description": (
                        "'add' = append one new patch. "
                        "'replace_all' = replace entire patch list (use to merge/deduplicate/remove)."
                    ),
                },
                "patch": {
                    "type": "string",
                    "description": (
                        "For action='add': the single new patch to append. "
                        "Should be a general, actionable rule, e.g., "
                        "'Always read the full function context before making changes'."
                    ),
                },
                "patches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "For action='replace_all': the complete list of patches to keep. "
                        "Review the current patches shown below and provide the full "
                        "desired list (merged, deduplicated, updated)."
                    ),
                },
            },
            "required": ["action"],
        },
    }


def execute_update_prompt_patch(
    *,
    _agent_name: str,
    _agent_dir: str,
    action: str = "add",
    patch: str = "",
    patches: list[str] | None = None,
) -> str:
    evo_dir = Path(_agent_dir) / "evolution"
    evo_dir.mkdir(parents=True, exist_ok=True)
    patch_file = evo_dir / "prompt_patches.md"

    existing_patches: list[str] = []
    if patch_file.exists():
        raw = patch_file.read_text(encoding="utf-8")
        for line in raw.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                existing_patches.append(stripped[2:])
            elif stripped:
                existing_patches.append(stripped)

    if action == "add":
        if not patch or not patch.strip():
            return (
                "Error: 'patch' is required when action='add'.\n\n"
                + _format_patch_list(existing_patches, _agent_name)
            )
        existing_patches.append(patch.strip())
        _write_patches(patch_file, existing_patches)
        return (
            f"Patch added for {_agent_name}.\n\n"
            + _format_patch_list(existing_patches, _agent_name)
        )

    elif action == "replace_all":
        if patches is None or not isinstance(patches, list):
            return (
                "Error: 'patches' (array) is required when action='replace_all'.\n\n"
                + _format_patch_list(existing_patches, _agent_name)
            )
        cleaned = [p.strip() for p in patches if p and p.strip()]
        if not cleaned:
            if patch_file.exists():
                patch_file.unlink()
            return (
                f"All patches cleared for {_agent_name}.\n"
                "Patch list is now empty."
            )
        _write_patches(patch_file, cleaned)
        return (
            f"Patches replaced for {_agent_name} ({len(cleaned)} patches).\n\n"
            + _format_patch_list(cleaned, _agent_name)
        )

    else:
        return (
            f"Error: unknown action '{action}'. Use 'add' or 'replace_all'.\n\n"
            + _format_patch_list(existing_patches, _agent_name)
        )


def _format_patch_list(patches: list[str], agent_name: str) -> str:
    if not patches:
        return f"**Current patches for {agent_name}: (none)**"
    lines = [f"**Current patches for {agent_name} ({len(patches)} total):**"]
    for i, p in enumerate(patches, 1):
        lines.append(f"  {i}. {p}")
    return "\n".join(lines)


def _write_patches(patch_file: Path, patches: list[str]) -> None:
    content = "\n".join(f"- {p}" for p in patches) + "\n"
    patch_file.write_text(content, encoding="utf-8")


# L1 — update_skill
# ---------------------------------------------------------------------------

def build_update_skill_schema() -> dict:
    return {
        "name": "update_skill",
        "description": (
            "Create or update a reusable skill. **Skills are your PRIMARY "
            "self-improvement tool** — prefer creating skills over adding "
            "prompt patches whenever a multi-step workflow or checklist is involved.\n\n"
            "**When to create a skill:**\n"
            "- You followed a multi-step process that could be reused → encode it\n"
            "- You wish you had a checklist to follow → create one\n"
            "- You discovered a useful workflow pattern → capture it\n"
            "- A procedure is too complex for a one-line patch → make it a skill\n\n"
            "**Example skills:**\n"
            "- `systematic-debugging`: Step-by-step debugging workflow\n"
            "- `code-impl-checklist`: Quality checks before submitting code\n"
            "- `test-design`: How to design comprehensive test suites\n\n"
            "Each skill must have YAML frontmatter (name, description, trigger) "
            "and step-by-step instructions in the body. "
            "Skills should be GENERAL (applicable across many tasks), not specific "
            "to the current problem.\n\n"
            "NOTE: Only available during L1 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "Name of the skill (lowercase, hyphenated). "
                        "E.g., 'systematic-debugging', 'test-design', 'code-impl-checklist'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Full content of the skill file (Markdown). Must include:\n"
                        "1. YAML frontmatter with 'name', 'description', and 'trigger' fields\n"
                        "2. Step-by-step instructions in the body\n\n"
                        "Example:\n"
                        "```\n"
                        "---\n"
                        "name: systematic-debugging\n"
                        "description: Step-by-step debugging workflow\n"
                        "trigger: When tests fail or code produces unexpected output\n"
                        "---\n"
                        "1. Read the complete error message and stack trace\n"
                        "2. Locate the exact failing line\n"
                        "3. Check input values at the failure point\n"
                        "4. Trace inputs back to their source\n"
                        "5. After fixing, check for similar patterns elsewhere\n"
                        "```"
                    ),
                },
            },
            "required": ["skill_name", "content"],
        },
    }


def execute_update_skill(
    skill_name: str,
    content: str,
    *,
    _agent_name: str,
    _agent_dir: str,
) -> str:
    if not skill_name.strip():
        return "Error: skill_name cannot be empty."
    if not content.strip():
        return "Error: content cannot be empty."

    clean_name = skill_name.strip()
    if ".." in clean_name or "/" in clean_name or "\\" in clean_name:
        return "Error: skill_name contains invalid characters (path traversal not allowed)."

    skill_dir = Path(_agent_dir) / "skills" / clean_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    is_update = skill_file.exists()
    try:
        skill_file.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error: could not write skill file: {e}"

    action = "updated" if is_update else "created"
    return (
        f"Skill '{skill_name}' {action} for {_agent_name}.\n"
        f"Path: {skill_file}\n"
        f"This skill will be available via use_skill in future sessions."
    )


# L1 — skip_l1_reflection
# ---------------------------------------------------------------------------

def build_skip_l1_reflection_schema() -> dict:
    return {
        "name": "skip_l1_reflection",
        "description": (
            "Skip L1 reflection — your performance was satisfactory "
            "and no self-improvement patches or skills are needed. "
            "NOTE: Only available during L1 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for skipping L1 reflection.",
                },
            },
            "required": ["reason"],
        },
    }


def execute_skip_l1_reflection(
    reason: str,
    *,
    _agent_name: str,
) -> str:
    if not reason.strip():
        return "Error: reason cannot be empty."
    return (
        f"L1 reflection skipped for {_agent_name}. Reason: {reason}\n"
        f"Moving on."
    )


# L2 — update_teammate_profile
# ---------------------------------------------------------------------------

def build_update_teammate_profile_schema(teammate_names: list[str]) -> dict:
    return {
        "name": "update_teammate_profile",
        "description": (
            "Update your observations about a teammate based on this task's "
            "interactions. The profile is stored in your evolution/teammate_profiles.yaml "
            "and will be injected into your system prompt in future sessions.\n\n"
            "**Quality guidelines:** Keep profiles concise and general. "
            "Strengths/weaknesses: max 5 items each, must be general patterns (not single-task events). "
            "Notes: max 3 items. Do NOT reference specific files, modules, PRs, or task details.\n\n"
            "NOTE: Only available during L2 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "teammate_name": {
                    "type": "string",
                    "enum": teammate_names,
                    "description": "Name of the teammate to profile.",
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "YAML-formatted observations about this teammate. "
                        "Include: reliability (high/medium/low), strengths, "
                        "weaknesses, communication_style, and notes."
                    ),
                },
            },
            "required": ["teammate_name", "profile"],
        },
    }


def execute_update_teammate_profile(
    teammate_name: str,
    profile: str,
    *,
    _agent_name: str,
    _agent_dir: str,
) -> str:
    import yaml as _yaml

    if not teammate_name.strip():
        return "Error: teammate_name cannot be empty."
    if not profile.strip():
        return "Error: profile cannot be empty."
    if teammate_name == _agent_name:
        return "Error: cannot profile yourself. Use update_prompt_patch for self-improvement."

    evo_dir = Path(_agent_dir) / "evolution"
    evo_dir.mkdir(parents=True, exist_ok=True)
    profile_file = evo_dir / "teammate_profiles.yaml"

    existing: dict = {}
    if profile_file.exists():
        raw = profile_file.read_text(encoding="utf-8")
        if raw.strip():
            try:
                existing = _yaml.safe_load(raw) or {}
            except Exception:
                existing = {}

    try:
        new_profile = _yaml.safe_load(profile)
        if not isinstance(new_profile, dict):
            new_profile = {"notes": profile.strip()}
    except Exception:
        new_profile = {"notes": profile.strip()}

    if teammate_name in existing and isinstance(existing[teammate_name], dict):
        old = existing[teammate_name]
        for key, new_val in new_profile.items():
            old_val = old.get(key)
            if isinstance(old_val, list) and isinstance(new_val, list):
                merged = list(old_val)
                seen = set(old_val)
                for item in new_val:
                    if item not in seen:
                        merged.append(item)
                        seen.add(item)
                old[key] = merged
            else:
                old[key] = new_val
    else:
        existing[teammate_name] = new_profile

    try:
        profile_file.write_text(
            _yaml.dump(existing, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError as e:
        return f"Error: could not write profile file: {e}"

    return (
        f"Teammate profile for '{teammate_name}' updated by {_agent_name}.\n"
        f"This will be injected into your system prompt in future sessions."
    )


# L2 — skip_l2_reflection
# ---------------------------------------------------------------------------

def build_skip_l2_reflection_schema() -> dict:
    return {
        "name": "skip_l2_reflection",
        "description": (
            "Mark L2 reflection as complete. Call this after recording relevant "
            "teammate profiles, or immediately if no "
            "interactions occurred. "
            "NOTE: Only available during L2 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason or summary of L2 reflection.",
                },
            },
            "required": ["reason"],
        },
    }


def execute_skip_l2_reflection(
    reason: str,
    *,
    _agent_name: str,
) -> str:
    if not reason.strip():
        return "Error: reason cannot be empty."
    return (
        f"L2 reflection skipped for {_agent_name}. Reason: {reason}\n"
        f"Moving on."
    )


# L3 — skip_l3_reflection (sub-team members)
# ---------------------------------------------------------------------------

def build_skip_l3_reflection_schema() -> dict:
    return {
        "name": "skip_l3_reflection",
        "description": (
            "Mark L3 reflection as complete — you have no team improvement "
            "suggestions, or you have already submitted your suggestions using "
            "suggest_team_improvement. Call this to signal you are done with L3. "
            "NOTE: Only available during L3 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason or summary of L3 reflection.",
                },
            },
            "required": ["reason"],
        },
    }


def execute_skip_l3_reflection(
    reason: str,
    *,
    _agent_name: str,
) -> str:
    if not reason.strip():
        return "Error: reason cannot be empty."
    return (
        f"L3 reflection completed for {_agent_name}. Reason: {reason}\n"
        f"You may now wait for the session to end."
    )


# L3 — propose_reflection (existing)
# ---------------------------------------------------------------------------


def execute_propose_reflection(
    target_file: str,
    content: str,
    reason: str,
    *,
    _agent_name: str,
    _reflection_plan,
    _reflection_review_state,
) -> str:
    err = validate_proposal_target_file(target_file)
    if err:
        return err
    if not content.strip():
        return "Error: content cannot be empty."
    if not reason.strip():
        return "Error: reason cannot be empty."

    _reflection_plan.upsert_proposal(target_file, content, reason)

    _reflection_review_state.clear_reviews()

    proposals_summary = _reflection_plan.summary()
    return (
        f"Reflection proposal recorded for '{target_file}'.\n"
        f"Reason: {reason}\n\n"
        f"Current proposals:\n{proposals_summary}\n\n"
        f"NOTE: Previous reflection reviews have been cleared. "
        f"The proposal(s) need to be reviewed before they can be applied."
    )



_GOVERNANCE_FILES = {"constitution.md", "pool.yaml"}
_VALID_AGENT_FILES = {"config.yaml", "prompt.md"}


def validate_proposal_target_file(target_file: str) -> str | None:
    if not target_file or not target_file.strip():
        return "Error: target_file cannot be empty."

    if ".." in target_file:
        return "Error: path traversal ('..') not allowed in target_file."

    if target_file in _GOVERNANCE_FILES:
        return None

    parts = Path(target_file).parts
    if len(parts) == 2:
        if parts[1] not in _VALID_AGENT_FILES:
            return (
                f"Error: only config.yaml and prompt.md can be proposed for agents, "
                f"got '{parts[1]}'."
            )
        if not parts[0] or parts[0].startswith("."):
            return f"Error: invalid path component '{parts[0]}' in target_file."
        return None
    elif len(parts) == 3:
        if parts[2] not in _VALID_AGENT_FILES:
            return (
                f"Error: only config.yaml and prompt.md can be proposed for agents, "
                f"got '{parts[2]}'."
            )
        for part in parts[:2]:
            if not part or part.startswith("."):
                return f"Error: invalid path component '{part}' in target_file."
        return None
    else:
        return (
            f"Error: invalid target_file '{target_file}'. "
            f"Must be a governance file ({', '.join(sorted(_GOVERNANCE_FILES))}) "
            f"or an agent file (<agent_name>/config.yaml|prompt.md)."
        )


# view_reflection
# ---------------------------------------------------------------------------

def build_view_reflection_schema() -> dict:
    return {
        "name": "view_reflection",
        "description": (
            "View the current reflection proposals and their review status. "
            "NOTE: This tool is only available during the reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }


def execute_view_reflection(
    *,
    _reflection_plan,
    _reflection_review_state,
) -> str:
    if not _reflection_plan.proposals:
        return "No reflection proposals yet."

    return (
        f"## Reflection Proposals\n\n"
        f"{_reflection_plan.summary()}\n\n"
        f"## Review Status\n\n"
        f"{_reflection_review_state.summary()}"
    )


# ---------------------------------------------------------------------------

def build_suggest_agent_change_schema(team_names: list[str],
                                       agent_names: list[str]) -> dict:
    return {
        "name": "suggest_team_improvement",
        "description": (
            "Suggest improvements to the team based on your experience during "
            "this task. The Chairman will review all suggestions and decide "
            "which to adopt.\n\n"
            "**Categories:**\n"
            "- `agent_change` — Suggest adding or removing a team member\n"
            "- `workflow` — Suggest changes to how the team works together "
            "(e.g., handoff protocols, testing process)\n"
            "- `constitution` — Suggest changes to team principles or rules\n"
            "- `communication` — Suggest changes to communication patterns\n\n"
            "NOTE: Only available during L3 reflection phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["agent_change", "workflow", "constitution", "communication"],
                    "description": (
                        "Type of improvement: 'agent_change' (add/remove member), "
                        "'workflow' (process changes), 'constitution' (team rules), "
                        "'communication' (interaction patterns)."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "What you are suggesting. Be specific and constructive. "
                        "For agent_change: describe who to add/remove and why. "
                        "For other categories: describe the specific change you recommend."
                    ),
                },
                "evidence": {
                    "type": "string",
                    "description": (
                        "Evidence from this task that supports your suggestion. "
                        "Reference specific events, bottlenecks, or issues you observed."
                    ),
                },
                # agent_change-specific fields (optional)
                "action": {
                    "type": "string",
                    "enum": ["add", "remove"],
                    "description": "Only for category='agent_change'. 'add' or 'remove'.",
                },
                "agent_name": {
                    "type": "string",
                    "description": (
                        "Only for category='agent_change'. "
                        "For 'add': proposed name. For 'remove': existing agent name."
                    ),
                },
                "team": {
                    "type": "string",
                    "description": "Only for category='agent_change'. Which pool the agent belongs to.",
                },
                "role": {
                    "type": "string",
                    "enum": ["worker", "manager"],
                    "description": "Only for category='agent_change' + action='add'.",
                },
                "expertise": {
                    "type": "string",
                    "description": "Only for category='agent_change' + action='add'. Core responsibility.",
                },
            },
            "required": ["category", "description", "evidence"],
        },
    }


def execute_suggest_agent_change(
    action: str = "",
    agent_name: str = "",
    reason: str = "",
    team: str = "",
    role: str = "worker",
    expertise: str = "",
    *,
    _agent_name: str,
    _suggestions_pool: list,
    category: str = "",
    description: str = "",
    evidence: str = "",
) -> str:
    effective_category = category or ("agent_change" if action else "")
    effective_description = description or reason or ""
    effective_evidence = evidence or reason or ""

    if not effective_category:
        return "Error: 'category' is required."
    if not effective_description.strip():
        return "Error: 'description' is required."
    if not effective_evidence.strip():
        return "Error: 'evidence' is required."

    if effective_category == "agent_change":
        effective_action = action or ""
        if effective_action not in ("add", "remove"):
            return "Error: 'action' must be 'add' or 'remove' for agent_change category."
        if not agent_name or not agent_name.strip():
            return "Error: 'agent_name' is required for agent_change category."

        clean_name = agent_name.strip()
        if ".." in clean_name or "/" in clean_name or "\\" in clean_name:
            return "Error: agent_name contains invalid characters."

        suggestion = {
            "category": "agent_change",
            "action": effective_action,
            "agent_name": clean_name,
            "team": team.strip() if team else "",
            "role": role.strip() if role else "worker",
            "expertise": expertise.strip() if expertise else "",
            "description": effective_description.strip(),
            "evidence": effective_evidence.strip(),
            "reason": effective_description.strip(),
            "suggested_by": _agent_name,
        }
        _suggestions_pool.append(suggestion)

        return (
            f"Suggestion recorded: {effective_action.upper()} agent '{clean_name}'"
            f"{f' in team {team}' if team else ''}.\n"
            f"Description: {effective_description[:300]}\n"
            f"Evidence: {effective_evidence[:200]}\n\n"
            f"The Chairman will review this suggestion."
        )
    else:
        # workflow / constitution / communication
        suggestion = {
            "category": effective_category,
            "action": "",
            "agent_name": "",
            "team": "",
            "role": "",
            "expertise": "",
            "description": effective_description.strip(),
            "evidence": effective_evidence.strip(),
            "reason": effective_description.strip(),
            "suggested_by": _agent_name,
        }
        _suggestions_pool.append(suggestion)

        return (
            f"Suggestion recorded [{effective_category}] by {_agent_name}.\n"
            f"Description: {effective_description[:300]}\n"
            f"Evidence: {effective_evidence[:200]}\n\n"
            f"The Chairman will review this suggestion."
        )


# ---------------------------------------------------------------------------


def execute_view_current_config(
    target_file: str,
    *,
    _team_dir: str,
) -> str:
    err = validate_proposal_target_file(target_file)
    if err:
        return err

    file_path = Path(_team_dir) / target_file
    if not file_path.exists():
        return (
            f"File '{target_file}' does not exist yet.\n"
            f"You can create it by using propose_reflection with new content."
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading '{target_file}': {e}"

    if not content.strip():
        return f"File '{target_file}' exists but is empty."

    return (
        f"## Current content of `{target_file}`\n\n"
        f"```\n{content}\n```\n\n"
        f"**IMPORTANT**: When proposing changes to this file, you MUST "
        f"include ALL existing content that should be preserved, plus "
        f"your additions. Do NOT delete existing sections unless you have "
        f"a specific reason."
    )
