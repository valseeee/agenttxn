"""Optional provider declarations and evidence; legacy adapters remain supported."""
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..models import ReconcileOutcome, canonical_json


@dataclass(frozen=True)
class ProviderCapabilities:
    native_idempotency: bool = False
    stable_lookup: bool = False


class EvidenceLevel(StrEnum):
    INCONCLUSIVE = 'INCONCLUSIVE'
    CONFIRMED = 'CONFIRMED'


@dataclass(frozen=True)
class ReconciliationEvidence:
    outcome: ReconcileOutcome
    result: dict[str, Any] | None = None
    level: EvidenceLevel = EvidenceLevel.INCONCLUSIVE


def provider_capabilities(adapter, action):
    method = getattr(adapter, 'capabilities', None)
    result = method(action) if method is not None else ProviderCapabilities()
    if not isinstance(result, ProviderCapabilities):
        raise ValueError('capabilities must return ProviderCapabilities')
    return result


def normalize_evidence(response):
    """Unconfirmed evidence never authorizes success or a new external call."""
    if isinstance(response, ReconciliationEvidence):
        outcome = ReconcileOutcome(response.outcome)
        result = response.result
        level = EvidenceLevel(response.level)
        if level != EvidenceLevel.CONFIRMED:
            outcome = ReconcileOutcome.UNKNOWN
    else:
        outcome, result = response
        outcome = ReconcileOutcome(outcome)
        # Legacy tuples retain the existing adapter's responsibility to prove the outcome.
        level = EvidenceLevel.INCONCLUSIVE if outcome == ReconcileOutcome.UNKNOWN else EvidenceLevel.CONFIRMED
    if result is not None and not isinstance(result, dict):
        raise ValueError('reconciliation result must be a JSON object or None')
    canonical_json(result)
    return outcome, result, level
