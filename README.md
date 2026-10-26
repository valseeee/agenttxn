# AgentTxn v0.5.0

**让 AI Agent 对真实世界的操作具有可审计、可暂停、可恢复的事务语义。**

AgentTxn 是 Python 3.11+ 的轻量级 Saga 事务协调器。它用 SQLite 持久化执行计划、人工审批、每一步的执行尝试、外部副作用结果及补偿记录。无需 LLM 框架，适合将 Agent 对 GitHub、工单系统、业务 API 的写入操作放进一个受控执行流程。

> **不保证跨任意第三方 API 的原子性或 exactly-once。** 网络超时可能意味着“操作已成功、响应丢失”；系统会进入 `NEEDS_RECONCILIATION`，要求查询真实状态，而不是盲目重试。补偿也是一个新的业务动作，不是历史回滚。

## v0.5.0 剩余交接事项

- 可选 PostgreSQL 后端：`pip install 'agenttxn[postgres]'`，SDK 使用 `Engine.open_postgres(dsn, ...)`；SQLite 保持默认且无需依赖。
- 数据库写入在同一事务内校验 worker 身份，阻止被接管的旧 worker 写回迟到结果。
- `metrics --format json|prometheus`、`alerts`、`tx telemetry <ID>` 提供不含业务载荷的诊断；SQLite 只读命令不建库、不迁移。
- GitHub 写入与补偿绑定稳定 repository ID，标签查询读取全部分页。
- 增加双后端合同测试、可重复故障基准、独立容器网络探针、最小权限 Actions Token 在线测试和独立代理安全审查。

兼容性与升级见 `交接说明_0.5.0.md`；后端和部署见 `docs/operations.md`。
真实验证记录分别见本地 `*_RESULTS.txt`、GitHub Actions 和 Release 验证资产。容器部署在一个 CI 主机上运行，不代表物理多机容灾。

## v0.3.1 修复与验证

- 核对状态与返回数据经过校验；非法状态、非对象结果和不可序列化证据保持 UNKNOWN，不授权执行或补偿重试。
- 在修改数据库结构和 journal mode 前拒绝未来 schema 版本。
- 增加真实子进程竞争、执行/补偿崩溃恢复和迁移失败重开测试；CLI 日志由脚本生成并自动计数。
- 保持现有 adapter API 和 SQLite schema v4。

安装开发依赖后，一次运行本地离线验证（包括隔离 wheel 安装与 CLI smoke）：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe scripts\validate_release.py
.venv\Scripts\python.exe scripts\build_zip.py
.venv\Scripts\python.exe scripts\verify_zip.py dist\agenttxn-v0.5.0-source.zip
```

Linux/macOS 使用 `.venv/bin/python`。当前运行证据见 `VALIDATION_RESULTS.txt` 和各 `*_RESULTS.txt`；远程 CI 与真实 GitHub 测试不由本地结果证明。

## v0.3.0 新增与修复

- **事务级 worker 所有权**：同一事务只允许一个执行者工作；SQLite 原子 claim 配合单步 claim，避免两个线程分别执行不同步骤而破坏顺序。异常退出留下的 worker token **不会自动过期或抢占**。
- **显式接管**：仅在操作者确认旧进程已停止后，才能通过 `tx reconcile <ID> --confirm-worker-stopped` 接管可能仍在执行的事务。强制接管并不能自动终止旧进程，误用有重复副作用风险。
- **准备阶段恢复**：`PREPARING` 中断后可以 `resume` 重跑只读 preflight 并重新冻结计划。
- **更严格的不确定结果**：适配器抛出意外异常、返回不可序列化的结果或补偿过程中发生未知异常时，保守进入核对流程，不误报为已知失败。
- **审批与审计加强**：冻结计划包含预检结果；步骤的预检、执行结果和补偿结果通过 SHA-256 摘要绑定到哈希事件链。策略身份使用完整 64 位十六进制 SHA-256，仍可读取 v0.2 的短指纹与旧计划。
- **前置条件**：`ActionSpec.preconditions` 与适配器返回的 preflight 字段逐项比较；执行前重新读取预检并复核前置条件（不能消除最终写入前的竞态窗口）。
- **SQLite schema v4**：首次打开旧数据库时迁移，添加显式 `action_idempotency_key` 列和普通唯一索引，不再依赖 SQLite JSON1 表达式索引；保留已有数据。
- **GitHub 安全默认值**：CLI 要求 `AGENTTXN_GITHUB_REPOS` 仓库白名单；`github.issue.add_labels` 默认**不允许自动删除标签进行补偿**，避免误删协作者的标签；Issue 创建仍支持关闭 Issue 的语义补偿。
- **更好用的 CLI**：`agenttxn version`、`tx events`、`tx show` 中显示 worker 状态；只读诊断不初始化网络适配器。
- **敏感信息保护**：外部适配器异常文本不会写入审计事件或数据库；仍需避免在 action payload 中放入密钥。

## Windows 快速启动

解压 ZIP 后进入 `agenttxn-v0.5.0` 文件夹，打开 PowerShell：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe examples\successful_transaction.py
.venv\Scripts\python.exe examples\demo_transaction.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

不需要安装任何独立 CLI 安装包。安装 Python 包时会同时注册 `agenttxn` 命令；若没有激活虚拟环境，可直接使用 `.venv\Scripts\agenttxn.exe`。

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python examples/successful_transaction.py
.venv/bin/python -m unittest discover -s tests -v
```

## CLI 使用流程

```powershell
agenttxn version
agenttxn --db .agenttxn\state.db init
agenttxn --db .agenttxn\state.db tx create --intent "创建测试资源" --idempotency-key "demo:tx:001"
```

把上一条命令打印的 UUID 替换下面的 `<TX_ID>`：

```powershell
agenttxn --db .agenttxn\state.db tx add <TX_ID> --kind demo.create --payload '{"name":"hello"}' --effect reversible --idempotency-key "demo:step:001"
agenttxn --db .agenttxn\state.db tx prepare <TX_ID>
agenttxn --db .agenttxn\state.db tx approve <TX_ID> --by "reviewer" --reason "reviewed"
agenttxn --db .agenttxn\state.db tx commit <TX_ID>
agenttxn --db .agenttxn\state.db tx show <TX_ID>
agenttxn --db .agenttxn\state.db tx events <TX_ID>
agenttxn --db .agenttxn\state.db tx verify <TX_ID>
```

**崩溃恢复：** `tx resume <TX_ID>` 可继续没有 in-flight 步骤的事务。若旧 worker 在 `CREATED`、`PREPARING`、`PREPARED` 等阶段崩溃并留下 worker token，请先确认其已停止，再运行 `tx resume <TX_ID> --confirm-worker-stopped`。如果提示仍有 in-flight 操作，先在操作系统中确认原 worker 已停止，再执行：

```powershell
agenttxn --db .agenttxn\state.db tx reconcile <TX_ID> --confirm-worker-stopped
```

不要在原进程仍可能向外部 API 发送请求时执行强制接管。

## Python SDK 示例

```python
from agenttxn import ActionSpec, EffectClass, Engine, Policy
from agenttxn.adapters import DemoAdapter

engine = Engine.open(
    "state.db",
    policy=Policy.default_safe(),
    adapters=[DemoAdapter("provider.json")],
)
tx = engine.create(intent="Create resource", idempotency_key="demo:tx:1")
tx.add(ActionSpec(
    kind="demo.create", payload={"name": "hello"},
    effect=EffectClass.REVERSIBLE,
    idempotency_key="demo:resource:1",
    preconditions={"exists": False},
))
tx.prepare()
tx.approve(approved_by="human", reason="plan reviewed")
result = tx.commit()
print(result.status.value, tx.verify().valid)
```

`preconditions` 的字段由适配器 `preflight` 决定。上例要求模拟资源在准备和执行前均不存在。真实外部系统的查询与写入之间仍可能出现竞态，不能把它当成数据库级 CAS。

## 可选 GitHub Issues 集成

支持 `github.issue.create` 和 `github.issue.add_labels`。请只用测试仓库、最小权限 Token，先完成代码审查。

```powershell
$env:GITHUB_TOKEN = "你的测试 Token"
$env:AGENTTXN_GITHUB_REPOS = "owner/sandbox-repo"
$env:AGENTTXN_GITHUB_REPO = "owner/sandbox-repo"
python examples\github_issue.py
```

`AGENTTXN_GITHUB_REPOS` 是 CLI 安全策略白名单，可用逗号分隔多个仓库；`AGENTTXN_GITHUB_REPO` 是演示脚本使用的目标仓库变量。演示脚本自己创建 `Policy.default_safe()`，不读取 CLI 白名单；如要用 SDK 做生产集成，请显式设置 `Policy(allowed_github_repositories=frozenset({...}))`。

默认禁止 GitHub 标签删除式补偿，因为无法证明某个同名标签是否由协作者同时添加。只有明确接受这个风险时，才可在 Python SDK 中使用 `GitHubIssuesAdapter(token, allow_label_compensation=True)`。

Issue 创建使用隐藏标记辅助核对；目前仅检查最近一页最多 100 个 Issue，因此“未找到”仍视为 UNKNOWN，不会推断操作未发生。在线验证采用显式触发的 GitHub Actions，Token 仅授予 contents:read 和 issues:write；结果见 Release 验证资产。

## 状态语义

| 事务状态 | 含义 |
|---|---|
| `COMMITTED` | 所有步骤有成功结果 |
| `FAILED` | 已知失败，且没有待补偿的成功副作用 |
| `COMPENSATED` | 已完成受支持的补偿，**不是**全局回滚 |
| `PARTIAL_FAILURE` | 仍有不可补偿或补偿失败的副作用 |
| `NEEDS_RECONCILIATION` | 存在未知结果，禁止盲目重试 |

## 已知限制

- SQLite 适合单机中小规模协调，不是分布式数据库或高可用事务服务。
- worker token 是持久排他标记，**不是外部 API 的 fencing token**；确认旧 worker 停止是接管的必要前提。
- 第三方 API 不保证全局 exactly-once。只有 provider 提供真正的幂等支持或可靠查询时，才可能更强。
- 审计哈希链不是不可篡改存储：具有数据库完全写权限的攻击者可重写整条链。结果摘要也不能证明外部 provider 的真实性。
- v0.2 旧计划在验证时兼容，但其 preflight 未被原始计划哈希绑定；`verify` 会提示 legacy。
- 旧数据库自动迁移前请先备份，尤其是用于真实外部操作的数据库。
- PostgreSQL 支持共享数据库协调，CLI 当前仍使用 SQLite。生产容量、TLS、HA、限流与告警投递须按实际环境配置；内置告警仅输出待处置事项。

详见 `交接说明_0.5.0.md`、`docs/architecture.md`、`docs/transaction-semantics.md`、`docs/adapters.md`、`docs/threat-model.md`。

许可证：Apache-2.0。
