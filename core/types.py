"""Shared type definitions."""

import time
from dataclasses import dataclass


# ---------------------------------------------------------------------------

@dataclass
class TaskValidation:
    success: bool
    summary: str
    details: str | None = None


@dataclass
class ReflectionProposal:
    target_file: str       # pool.yaml / constitution.md / <agent>/config.yaml
    content: str
    reason: str


class ReflectionPlan:

    def __init__(self):
        self.proposals: dict[str, ReflectionProposal] = {}  # target_file -> proposal
        self.status: str = "drafting"  # drafting / reviewing / applied

    def upsert_proposal(self, target_file: str, content: str, reason: str) -> None:
        self.proposals[target_file] = ReflectionProposal(
            target_file=target_file,
            content=content,
            reason=reason,
        )
        self.status = "drafting"

    def summary(self) -> str:
        if not self.proposals:
            return "No proposals."
        parts = []
        for tf, p in self.proposals.items():
            parts.append(
                f"- **{tf}**: {p.reason[:120]}\n"
                f"  Content preview: {p.content[:200]}..."
            )
        return f"Status: {self.status}\n" + "\n".join(parts)


class ReviewState:

    def __init__(self):
        self._reviews: dict[str, dict] = {}   # {agent_name: {verdict, comment, ts}}

    def clear_reviews(self) -> None:
        self._reviews.clear()

    def summary(self) -> str:
        if not self._reviews:
            return "No reviews yet."
        parts = []
        for name, r in self._reviews.items():
            parts.append(f"{name}: {r['verdict']}")
        return ", ".join(parts)
