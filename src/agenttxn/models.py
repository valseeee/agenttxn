from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import json

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

class EffectClass(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"

class TransactionStatus(StrEnum):
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    COMMITTING = "COMMITTING"
    COMPENSATING = "COMPENSATING"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    COMMITTED = "COMMITTED"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    ABORTED = "ABORTED"

class StepStatus(StrEnum):
    PENDING = "PENDING"
    PREFLIGHTED = "PREFLIGHTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    COMPENSATION_UNKNOWN = "COMPENSATION_UNKNOWN"

class ReconcileOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NOT_EXECUTED = "NOT_EXECUTED"
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class ActionSpec:
    kind: str
    payload: dict[str, Any]
    effect: EffectClass
    idempotency_key: str
    preconditions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("kind must be a non-empty string")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        object.__setattr__(self, "effect", EffectClass(self.effect))
        if not isinstance(self.payload, dict) or not isinstance(self.preconditions, dict):
            raise TypeError("payload and preconditions must be dictionaries")
        def validate_json(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str):
                        raise TypeError(f"JSON object keys must be strings ({path})")
                    validate_json(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    validate_json(child, f"{path}[{index}]")
            elif value is None or isinstance(value, (str, bool, int)):
                return
            elif isinstance(value, float):
                import math
                if not math.isfinite(value):
                    raise ValueError(f"non-finite numbers are not valid JSON ({path})")
            else:
                raise TypeError(f"unsupported JSON value {type(value).__name__} ({path})")
        validate_json(self.payload, "payload")
        validate_json(self.preconditions, "preconditions")
        canonical_json(self.to_dict())

    def to_dict(self):
        return {"kind": self.kind, "payload": self.payload, "effect": self.effect.value,
                "idempotency_key": self.idempotency_key, "preconditions": self.preconditions}

    @classmethod
    def from_dict(cls, d):
        return cls(d["kind"], d["payload"], EffectClass(d["effect"]),
                   d["idempotency_key"], d.get("preconditions", {}))

@dataclass
class TransactionRecord:
    id: str
    intent: str
    idempotency_key: str
    status: TransactionStatus
    policy_version: str
    plan_hash: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    last_error: dict[str, Any] | None = None
    row_version: int = 0

@dataclass
class StepRecord:
    id: str
    transaction_id: str
    sequence: int
    action: ActionSpec
    status: StepStatus
    attempt_count: int = 0
    preflight: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    compensation: dict[str, Any] | None = None
    last_error: dict[str, Any] | None = None

@dataclass
class ApprovalRecord:
    id: str
    transaction_id: str
    plan_hash: str
    approved_by: str
    reason: str | None
    status: str
    approved_at: str

@dataclass
class EventRecord:
    id: str
    transaction_id: str
    step_id: str | None
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str
    previous_hash: str | None
    event_hash: str

@dataclass
class PreparationReport:
    transaction_id: str
    status: TransactionStatus
    plan_hash: str | None
    approval_required: bool
    messages: list[str] = field(default_factory=list)

@dataclass
class CommitReport:
    transaction_id: str
    status: TransactionStatus
    completed_steps: int
    total_steps: int
    failed_step_id: str | None = None
    message: str | None = None

@dataclass
class VerificationReport:
    transaction_id: str
    valid: bool
    checks: dict[str, bool]
    messages: list[str] = field(default_factory=list)
