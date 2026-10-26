from dataclasses import dataclass
from typing import Any, Protocol
from ..models import ActionSpec, ReconcileOutcome
from .capabilities import ReconciliationEvidence

@dataclass(frozen=True)
class AdapterContext:
    transaction_id: str
    step_id: str
    attempt_id: str
    phase: str = "forward"

class ActionAdapter(Protocol):
    kinds: frozenset[str]
    def validate(self, action: ActionSpec) -> None: ...
    def preflight(self, context: AdapterContext, action: ActionSpec) -> dict[str, Any]: ...
    def execute(self, context: AdapterContext, action: ActionSpec, preflight: dict[str, Any]) -> dict[str, Any]: ...
    def compensate(self, context: AdapterContext, action: ActionSpec, result: dict[str, Any], preflight: dict[str, Any] | None) -> dict[str, Any]: ...
    def reconcile(self, context: AdapterContext, action: ActionSpec, phase: str, result: dict[str, Any] | None, preflight: dict[str, Any] | None) -> tuple[ReconcileOutcome, dict[str, Any] | None] | ReconciliationEvidence: ...
    def can_compensate(self, action: ActionSpec) -> bool: ...
