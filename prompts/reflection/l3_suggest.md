## L3 Reflection — Team Improvement Suggestions

You are now in the **L3 Reflection phase**. Based on your experience during this task, suggest improvements to the team. The Chairman will review your suggestions and decide which to adopt.

### Reflect on Your Experience

Think back through this task from your perspective:

- **What slowed you down the most?** Was it waiting for instructions? Unclear requirements? Missing tools? Having to redo work?
- **What information did you wish you had earlier?** Did you have to discover something the hard way that could have been provided upfront?
- **If you could have handed off part of your work to a specialist, what would it be?** For example: test discovery, code exploration, verification, documentation reading. Would a dedicated agent for that subtask have been faster or more reliable than you doing it yourself?
- **How was the communication?** Were the messages you received clear and complete? Were your reports used effectively?

### What You Can Suggest

Use `suggest_team_improvement` to propose improvements in four categories:

#### 1. Agent Change (`category="agent_change"`)
Add or remove team members based on gaps or redundancy you observed.
```
suggest_team_improvement(
    category="agent_change",
    action="add",             # or "remove"
    agent_name="test-runner", # name of the agent to add/remove
    role="worker",            # "worker" or "manager" (for add only)
    expertise="Running and analyzing test suites",
    description="Add a dedicated test-runner to verify fixes independently",
    evidence="Developer had to both implement and test, creating a conflict of interest in verification"
)
```

#### 2. Workflow (`category="workflow"`)
Suggest changes to how the team works together — handoff protocols, testing process, task decomposition, etc.
```
suggest_team_improvement(
    category="workflow",
    description="Coordinator should delegate within 3 tool calls instead of doing extensive pre-analysis",
    evidence="Coordinator spent most of the budget analyzing before delegating, leaving little time for implementation"
)
```

#### 3. Constitution (`category="constitution"`)
Suggest changes to team principles, rules, or standards.
```
suggest_team_improvement(
    category="constitution",
    description="Add a rule requiring independent verification of all changes before submission",
    evidence="Self-reported results were inaccurate — independent verification would have caught this"
)
```

#### 4. Communication (`category="communication"`)
Suggest changes to communication patterns, message formats, or information sharing.
```
suggest_team_improvement(
    category="communication",
    description="When reporting completion, always include the verification evidence (test output or diff), not just a summary",
    evidence="Had to re-verify work that was reported as complete because the report lacked evidence"
)
```

### Rules
- Be **evidence-based** — reference what actually happened during this task
- Be **specific** — describe exactly what you'd change
- Even if the task succeeded, there are **always** efficiency improvements to be found

### CRITICAL
**You MUST call `skip_l3_reflection(reason)`** when you are done — either after submitting suggestions or if you genuinely cannot identify any improvement after careful thought.
