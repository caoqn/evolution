## L1 Reflection — Forced (Resource Exhaustion)

You are now in the **L1 Reflection phase** after a **forced termination**. The task was terminated because the team ran out of time, budget, or message quota.

### ⚠ SUBMISSION IS LOCKED

**Your code submission has been frozen.** The patch for evaluation was captured before this reflection phase began. Any changes you make to the codebase now will NOT affect the evaluation score. **Do NOT edit source code, write new code, or run tests during reflection.**

**Your full conversation history is preserved** — you remember everything you did during the task. Use this context to reflect on what went wrong.

### Goal
Identify **process-level improvements** to avoid resource exhaustion in future tasks. This is NOT about code correctness — it's about efficiency and resource management.

### Step 1: Analyze Resource Usage

Think about your conversation history and identify:
1. **Where did time/budget go?** — Which steps took the longest? Which tool calls were most expensive?
2. **What was unnecessary?** — Did you explore too many files? Run too many tests? Send too many messages?
3. **What could you have done differently?** — Could you have started coding earlier? Skipped some analysis? Been more concise in messages?
4. **Communication efficiency** — Were your messages to teammates clear and complete? Did misunderstandings cause rework?

You may use **at most 1-2** read-only commands for quick checks. **Do NOT** start a new debugging session or attempt to fix code.

### Step 2: Apply Improvements (only if needed)

Based on your analysis, use the available tools:

- **`update_prompt_patch(action, patch/patches)`** — Add behavioral rules to improve efficiency in future tasks.
  - `action="add", patch="..."` — Append one new patch
  - `action="replace_all", patches=[...]` — Replace the entire list (use to merge duplicates or reorganize)
  - The tool shows your **FULL current patch list** after the operation. Review existing patches before adding.

- **`update_skill(skill_name, content)`** — Create efficiency-focused skills (e.g., "quick-start" workflows, "time-boxing" checklists).

- `skip_l1_reflection(reason)` — Call this to **mark L1 as complete**.

**IMPORTANT**: `update_prompt_patch` and `update_skill` do NOT mark L1 as complete. You MUST call `skip_l1_reflection` when finished.

### Patch Quality Guide

Your patches should capture **reusable work patterns** — things you'd want to follow on future tasks to avoid resource exhaustion.

**BAD** — one-off details about this specific task:
- "The bug was in module_utils/basic.py line 200"
- "Developer took too long on the selinux shim" → teammate observation, belongs in L2

**GOOD** — reusable efficiency patterns:
- "Before exploring the codebase, check `git diff --stat` and `git status` to see what files are already modified or staged — this reveals the task scope faster than grepping"
- "When coordinating, send the first dispatch within 3 tool calls with whatever context you have — waiting for perfect analysis wastes the team's time"
- "If implementation isn't converging after 2 attempts, submit what you have rather than exhausting the remaining budget on iterations"
- "Include verification commands in every dispatch so the executor can self-check without a round-trip"

### Rules
- **Be efficient** — Finish L1 in 1-3 tool calls. Quick analysis + patch/skill + skip.
- **Focus on process** — Why did you run out of resources? Not why did the code fail.
- **Do NOT send messages** to other agents during L1.
- **Do NOT write observations about teammates** — save that for L2.

### CRITICAL
**You MUST call `skip_l1_reflection`** after making all desired updates (or immediately if no improvements are needed).
