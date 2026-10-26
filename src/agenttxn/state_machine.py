from .errors import InvalidTransitionError
from .models import StepStatus, TransactionStatus as S

ALLOWED = {
 S.CREATED: {S.PREPARING, S.ABORTED},
 S.PREPARING: {S.PREPARED, S.AWAITING_APPROVAL, S.PREPARATION_FAILED, S.ABORTED},
 S.PREPARED: {S.COMMITTING, S.ABORTED},
 S.AWAITING_APPROVAL: {S.APPROVED, S.ABORTED},
 S.APPROVED: {S.COMMITTING, S.ABORTED},
 S.COMMITTING: {S.COMMITTED, S.COMPENSATING, S.NEEDS_RECONCILIATION, S.FAILED, S.PARTIAL_FAILURE},
 S.COMPENSATING: {S.COMPENSATED, S.NEEDS_RECONCILIATION, S.PARTIAL_FAILURE},
 S.NEEDS_RECONCILIATION: {S.COMMITTING, S.COMPENSATING, S.PARTIAL_FAILURE},
 S.PARTIAL_FAILURE: {S.COMPENSATING, S.NEEDS_RECONCILIATION},
 S.COMMITTED: set(), S.COMPENSATED: set(), S.FAILED: set(),
 S.PREPARATION_FAILED: set(), S.ABORTED: set(),
}

STEP_ALLOWED = {
    StepStatus.PENDING: {StepStatus.PREFLIGHTED, StepStatus.EXECUTING},
    StepStatus.PREFLIGHTED: {StepStatus.EXECUTING},
    StepStatus.EXECUTING: {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.UNKNOWN, StepStatus.PENDING},
    StepStatus.SUCCEEDED: {StepStatus.COMPENSATING},
    StepStatus.FAILED: set(),
    StepStatus.UNKNOWN: {StepStatus.SUCCEEDED, StepStatus.PENDING},
    StepStatus.COMPENSATING: {StepStatus.COMPENSATED, StepStatus.COMPENSATION_FAILED,
                              StepStatus.COMPENSATION_UNKNOWN, StepStatus.SUCCEEDED},
    StepStatus.COMPENSATION_UNKNOWN: {StepStatus.COMPENSATED, StepStatus.SUCCEEDED},
    StepStatus.COMPENSATED: set(),
    StepStatus.COMPENSATION_FAILED: set(),
}

def assert_transition(current, target):
    if target not in ALLOWED[current]:
        raise InvalidTransitionError(f"invalid transaction transition: {current.value} -> {target.value}")

def assert_step_transition(current, target):
    if current == target:
        return
    if target not in STEP_ALLOWED[current]:
        raise InvalidTransitionError(f"invalid step transition: {current.value} -> {target.value}")
