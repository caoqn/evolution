## L1 Reflection — Individual Agent Self-Improvement

You are now in the **L1 Reflection phase**. The task has been completed. Reflect on YOUR OWN performance during this task and identify improvements for the future.

### Goal
Improve your own prompt and skills that would make you more effective in future tasks.

### SUBMISSION IS LOCKED

**Your code submission has been frozen.** The patch for evaluation was captured before this reflection phase began. Any changes you make to the codebase now will NOT affect the evaluation score. **Do NOT edit source code, write new code, or attempt to fix bugs during reflection.** This phase is purely for learning and self-improvement.

**If the task was truly trivial** (required no significant decisions or effort), you can choose to skip L1. But if you applied any noteworthy strategy or learned something useful, record it even if the task succeeded.

### Step 1: Self-Reflection

Reflect deeply on your own behavior during this task. You may use read-only environment commands as evidence to support your reflection, but the focus is on your **decision-making process**, not on diagnosing the code.

Think honestly about:
1. **What did I do well?** — Which decisions, strategies, or habits contributed positively? Learning from success is just as important as learning from failure.
2. **Where did I go wrong?** — What mistakes did I make? Where did my reasoning break down? Did I make incorrect assumptions?
3. **What was inefficient?** — Even if the task succeeded, did I waste steps? Over-explore? Under-delegate? Miss a faster path?
4. **What would I do differently next time?** — If I faced a similar (but not identical) task tomorrow, what would I change about my strategy?

### Step 2: Identify Reusable Patterns

Your goal is to identify **patterns and strategies** that will help on future similar tasks — not to record what happened on this specific task.

**The key question**: Would this insight help me if I encountered a similar (but not identical) task in this codebase? If yes, it's worth recording.

**What to avoid**: Don't record one-off details about this specific bug or this specific file. If your patch only makes sense in the context of this exact task, it's too narrow.

**What's valuable**: Strategies, workflows, verification steps, and patterns that you'd want to remember across many tasks in this codebase.

**Even if the task succeeded**, reflect on whether your approach was efficient. Could you have solved it in fewer steps? Did you explore unnecessary files? Was there a faster path?

### Step 3: Apply Improvements (only if needed)

Based on your reflection, use the available tools:

- **`update_prompt_patch(action, patch/patches)`** — Manage your behavioral patches (rules injected into your system prompt in future sessions).
  - `action="add", patch="..."` — Append one new patch to your current list
  - `action="replace_all", patches=[...]` — Replace the entire list (use to merge duplicates, remove outdated patches, or reorganize)
  - The tool always shows your **FULL current patch list** after the operation, so you can see what you already have.
  - **Review your existing patches** before adding new ones. If you have 5+ patches, consider using `replace_all` to merge related ones.

- **`update_skill(skill_name, content)`** — Create or update a reusable skill file. **Skills are your PRIMARY self-improvement tool** — use them for:
  - Multi-step procedures or checklists you discovered during the task
  - Reusable workflows that would help in similar future tasks
  - Complex decision trees or validation sequences

- `skip_l1_reflection(reason)` — Call this to **mark L1 as complete**. Use it after making any updates, or immediately if no improvements are needed.

**IMPORTANT**: `update_prompt_patch` and `update_skill` do NOT mark L1 as complete. You can call them multiple times. When you are finished with ALL your updates, you MUST call `skip_l1_reflection` to mark completion.

### Patch Quality Guide

Your patches should capture **reusable patterns** — things you'd want to remember for future tasks in this codebase.

**BAD** — one-off details about this specific task, for example:
- "The bug was in line 42 of foo.py where the return type was wrong"
- "Developer forgot to check strategy plugins for PlayIterator" → teammate observation, belongs in L2

**GOOD** — reusable work patterns you'd want to follow on future tasks, for example:
- "After implementing a fix, run `git diff --stat` and verify the changed files match your intent — if a file you edited doesn't appear, the edit wasn't applied"
- "When a PR changes a function signature, grep for all callers and update every one before testing — partial caller updates cause partial test failures"
- "Run the failing tests locally before implementing to verify they actually fail — if a test doesn't exist locally, the test file may need to be created as part of the task"
- "In projects with an installed package and a source tree, check which one Python imports before editing — edits to the wrong copy have no effect"

### Skills > Patches

When your improvement involves a **multi-step workflow**, **checklist**, or **decision procedure**, create a **skill** instead of a text patch. Skills are structured, reusable, and more powerful than single-line rules.

**Use a patch** for: simple behavioral rules ("Always X before Y")
**Use a skill** for: procedures with multiple steps, conditional logic, or detailed checklists

### Rules
- **Do NOT modify code** — The submission is locked. No editing files, no writing code, no running tests. Read-only diagnosis only.
- **Be efficient** — If nothing noteworthy happened, call `skip_l1_reflection` to skip this phase immediately.
- Focus on **your own behavior** — later phases will handle teammate observations
- Patches are **additive** — they supplement your base prompt, not replace it
- **Do NOT send messages** to other agents during L1 — save collaboration feedback for L2
- **Do NOT spend too much time on environment interaction** — This is reflection, not a debugging session.

### CRITICAL
**You MUST call `skip_l1_reflection`** after making all desired updates (or immediately if no improvements are needed). After calling it, you will wait in idle until the system advances to L2.
