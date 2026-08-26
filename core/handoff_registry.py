"""Canonical, family-scoped persistence for inter-agent handoff rules."""

from __future__ import annotations

import json
from uuid import uuid4
from pathlib import Path
from typing import Any

from core.evolution_models import HandoffDecision, HandoffRule, now_iso


class HandoffRegistry:
    SCHEMA_VERSION = 2

    def __init__(
        self,
        *,
        bindings: dict[str, str] | None = None,
        rules: list[HandoffRule] | None = None,
    ) -> None:
        self._bindings = dict(bindings or {})
        self._rules = list(rules or [])
        self.validate()

    def validate(self, known_agent_ids: set[str] | None = None) -> None:
        seen: set[str] = set()
        for team_family_id, handoff_family_id in self._bindings.items():
            if not str(team_family_id).strip() or not str(handoff_family_id).strip():
                raise ValueError("handoff family bindings cannot be empty")
        for rule in self._rules:
            rule.validate(known_agent_ids)
            if rule.rule_id in seen:
                raise ValueError(f"duplicate handoff rule_id: {rule.rule_id}")
            seen.add(rule.rule_id)

    def bind_team_family(self, team_family_id: str, handoff_family_id: str) -> None:
        if not team_family_id.strip() or not handoff_family_id.strip():
            raise ValueError("handoff family binding requires both ids")
        existing = self._bindings.get(team_family_id)
        if existing is not None and existing != handoff_family_id:
            raise ValueError("team family is already bound to another handoff family")
        self._bindings[team_family_id] = handoff_family_id

    def handoff_family_for(self, team_family_id: str) -> str | None:
        return self._bindings.get(team_family_id)

    def rules_for_team_family(self, team_family_id: str) -> list[HandoffRule]:
        handoff_family_id = self.handoff_family_for(team_family_id)
        if not handoff_family_id:
            return []
        return [
            rule for rule in self._rules
            if rule.handoff_family_id == handoff_family_id and rule.status == "active"
        ]

    def list_rules(self) -> list[HandoffRule]:
        return sorted(
            self._rules,
            key=lambda rule: (rule.handoff_family_id, rule.rule_id),
        )

    def _find_target(self, decision: HandoffDecision) -> HandoffRule | None:
        if decision.rule_id:
            return next(
                (rule for rule in self._rules if rule.rule_id == decision.rule_id),
                None,
            )
        matching = [
            rule for rule in self._rules
            if rule.handoff_family_id == decision.handoff_family_id
            and rule.from_agent == decision.from_agent
            and rule.to_agent == decision.to_agent
            and rule.status == "active"
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    def apply_decision(
        self,
        decision: HandoffDecision,
        *,
        task_id: str,
        known_agent_ids: set[str],
        team_family_id: str | None = None,
    ) -> HandoffRule | None:
        decision.validate()
        self.validate(known_agent_ids)
        if team_family_id:
            self.bind_team_family(team_family_id, decision.handoff_family_id)
        current = self._find_target(decision)
        if current is not None and (
            current.handoff_family_id != decision.handoff_family_id
            or current.from_agent != decision.from_agent
            or current.to_agent != decision.to_agent
        ):
            raise ValueError(
                "handoff_rule_id does not match the decision family or agent pair"
            )
        if decision.action == "no_update":
            return current
        if decision.action == "retain":
            if current is None:
                raise ValueError("handoff retain requires an existing rule")
            if task_id and task_id not in current.evidence_task_ids:
                current.evidence_task_ids.append(task_id)
            return current
        if decision.action == "create":
            rule_id = decision.rule_id or f"hr_{uuid4().hex[:12]}"
            if any(rule.rule_id == rule_id for rule in self._rules):
                raise ValueError(f"handoff rule_id already exists: {rule_id}")
            rule = HandoffRule(
                handoff_family_id=decision.handoff_family_id,
                from_agent=decision.from_agent,
                to_agent=decision.to_agent,
                version=1,
                instruction=decision.instruction,
                payload_schema=dict(decision.payload_schema),
                required_evidence=list(decision.required_evidence),
                verification=decision.verification,
                fallback=decision.fallback,
                context_notes=decision.context_notes,
                evidence_task_ids=[task_id] if task_id else [],
                rule_id=rule_id,
            )
            rule.validate(known_agent_ids)
            self._rules.append(rule)
            self.validate(known_agent_ids)
            return rule
        if decision.action == "retire":
            if current is None:
                raise ValueError("handoff retire requires an existing rule")
            current.version_history.append({
                "version": current.version,
                "instruction": current.instruction,
                "payload_schema": current.payload_schema,
                "required_evidence": current.required_evidence,
                "verification": current.verification,
                "fallback": current.fallback,
                "context_notes": current.context_notes,
                "reason": decision.reason,
                "task_id": task_id,
                "changed_at": now_iso(),
                "status": "retired",
            })
            current.status = "retired"
            if task_id and task_id not in current.evidence_task_ids:
                current.evidence_task_ids.append(task_id)
            current.validate(known_agent_ids)
            return current
        if current is None:
            raise ValueError("handoff refine requires an existing rule")
        current.version_history.append({
            "version": current.version,
            "instruction": current.instruction,
            "payload_schema": current.payload_schema,
            "required_evidence": current.required_evidence,
            "verification": current.verification,
            "fallback": current.fallback,
            "context_notes": current.context_notes,
            "reason": decision.reason,
            "task_id": task_id,
            "changed_at": now_iso(),
            "status": current.status,
        })
        current.version += 1
        current.instruction = decision.instruction
        current.payload_schema = dict(decision.payload_schema)
        current.required_evidence = list(decision.required_evidence)
        current.verification = decision.verification
        current.context_notes = decision.context_notes
        current.fallback = decision.fallback
        if task_id and task_id not in current.evidence_task_ids:
            current.evidence_task_ids.append(task_id)
        current.validate(known_agent_ids)
        return current

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.SCHEMA_VERSION,
            "bindings": dict(sorted(self._bindings.items())),
            "rules": [rule.to_dict() for rule in self.list_rules()],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HandoffRegistry":
        if not isinstance(payload, dict):
            raise ValueError("handoff registry must be an object")
        version = int(payload.get("schema_version") or 0)
        if version not in {1, cls.SCHEMA_VERSION}:
            raise ValueError(f"unsupported handoff registry schema_version: {version}")
        bindings = payload.get("bindings", {})
        rows = payload.get("rules", [])
        if not isinstance(bindings, dict) or not isinstance(rows, list):
            raise ValueError("handoff registry bindings/rules have invalid types")
        normalized_rows = []
        for index, row in enumerate(rows):
            item = dict(row)
            # Schema v1 had no rule id and could contain only one rule per pair.
            # Preserve those rules with deterministic ids during migration.
            item.setdefault(
                "rule_id",
                f"hr_legacy_{index + 1}_{str(item.get('from_agent') or 'agent')[:24]}",
            )
            normalized_rows.append(item)
        return cls(
            bindings={str(k): str(v) for k, v in bindings.items()},
            rules=[HandoffRule(**row) for row in normalized_rows],
        )

    @classmethod
    def load(cls, path: str | Path) -> "HandoffRegistry":
        target = Path(path)
        if not target.exists():
            return cls()
        return cls.from_dict(json.loads(target.read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
