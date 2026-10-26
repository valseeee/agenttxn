# 事务语义

## 设计目标

AgentTxn 将事务意图、动作计划、步骤状态、执行尝试、审批与事件日志持久化。它在调用适配器之前先记录步骤认领和 attempt，发生不确定结果时暂停，不盲目重试。

## 事务与步骤状态

事务状态和步骤状态都通过显式状态机限制。SQLite 的原子步骤认领确保同一个待执行步骤只有一个 worker 能从 `PENDING` / `PREFLIGHTED` 认领为 `EXECUTING`，或从 `SUCCEEDED` 认领为 `COMPENSATING`。

在调用外部 API 时，SQLite 写事务已经结束；网络调用不会占用数据库写锁。若进程在外部调用中途退出，持久化状态会保留为 in-flight，恢复流程必须先向适配器核对实际状态。

## 保证与非保证

AgentTxn 会在外部调用前持久化执行尝试，审批绑定到计划哈希，并拒绝在不确定结果下盲目重试。

它**不**保证任意外部 API 的 exactly-once，也不提供多个 SaaS API 之间的全局原子提交。只有适配器与提供方支持可靠幂等和可核对查询时，才能对具体动作给出更强保证。

## 结果

- `SUCCEEDED`：适配器有足够证据确认动作完成。
- `FAILED`：适配器确定动作没有成功。
- `UNKNOWN`：动作可能已经发生；不得盲目重试。
- `NEEDS_RECONCILIATION`：事务暂停，等待适配器核对外部状态。

“查询没有找到”并不总能证明动作从未执行。

## Saga 补偿

已知失败后，AgentTxn 按成功步骤的逆序尝试补偿。只补偿动作声明为可逆、且适配器明确支持补偿的步骤。不可逆动作、补偿失败或补偿结果不确定都可能导致 `PARTIAL_FAILURE` 或 `NEEDS_RECONCILIATION`。

补偿是新的业务操作，不一定是数学意义上的逆操作。例如关闭一个已创建的 Issue 不会抹除其历史。

## 审批与策略

审批绑定到计划哈希。事务同时保存完整策略配置指纹；如果事务重新载入时的策略配置与创建时不同，执行会失败关闭，而不是默默套用另一套策略。

## 审计

事件记录采用 SHA-256 哈希链，并在恢复时记录 attempt 的核对结果。它可以检测意外损坏或局部篡改，但不能抵御拥有数据库完整写权限的攻击者重写整条链。

## v0.3.0 explicit worker takeover

Transaction-wide worker claims prevent concurrent forward/compensation execution on one
SQLite database. A crashed worker leaves a token. Do not auto-expire it: first establish
that the old process has stopped, then use `reconcile(confirm_worker_stopped=True)` or
`tx reconcile ID --confirm-worker-stopped`. This token does not fence a live process.

An unexpected adapter exception or an invalid/unserializable provider result is treated
as an uncertain write. Known failure is reserved for explicit `ActionExecutionError`.
Preconditions compare adapter preflight fields both at preparation and immediately before
execution; they are not atomic with remote writes. New plans bind preflight data.

In v0.3.1, reconciliation accepts only `SUCCEEDED`, `NOT_EXECUTED`, or `UNKNOWN` and
a JSON-serializable object (or `None`). Invalid evidence leaves the step unresolved;
it never authorizes forward execution or compensation. This preserves the existing
three-way adapter contract rather than introducing a new capability API.
