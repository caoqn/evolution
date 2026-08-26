"""Shared semantics for reusable task-family identification."""

from __future__ import annotations


FAMILY_POLICY = {
    "family_unit": "a reusable multi-agent collaboration pattern",
    "identity_dimensions": [
        "work type",
        "change or investigation scope",
        "required specialist roles",
        "verification strategy",
    ],
    "positive_rules": [
        "Reuse a family when these collaboration dimensions are substantially the same.",
        "Create a family only when the task requires a materially different collaboration pattern.",
        "Name and describe a family in terms of its collaboration pattern.",
    ],
    "negative_rules": [
        "A shared benchmark wrapper alone is not evidence that two tasks belong to one family.",
        "Benchmark names, repository names, domains, business topics, entities, and filenames do not define family identity.",
        "A different subject or requested feature alone is not evidence for a new family.",
    ],
}

