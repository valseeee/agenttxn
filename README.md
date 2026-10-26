# AgentTxn

**Durable Saga transactions for AI agent actions.**

为 AI Agent 的外部写入提供持久化执行计划、人工审批、审计记录、补偿与未知结果核对。

[English](README.en.md) · [离线示例](#首次体验运行离线示例) · [示例导航](examples/README.md) · [v0.5.0 下载](https://github.com/valseeee/agenttxn/releases/tag/v0.5.0) · [部署与诊断](docs/operations.md)

AgentTxn 是 Python 3.11+ 的轻量级 Saga 事务协调器。默认使用 SQLite，可通过 Python SDK 选择 PostgreSQL；无需 LLM 框架。适合需要审批、追踪执行过程，并在失败后补偿或核对真实状态的多步操作。内置 GitHub Issues 适配器；其他工单系统和业务 API 通过自定义适配器接入。

> **不保证跨第三方 API 的 ACID 或 exactly-once。** 网络超时可能意味着“操作已成功、响应丢失”；结果无法确认时进入 `NEEDS_RECONCILIATION`，要求核对真实状态，而不是盲目重试。补偿是新的业务动作，不是全局回滚。CLI 当前使用 SQLite。

## 首次体验：运行离线示例

需要 **Python 3.11+**。下载并解压 [v0.5.0 源码包](https://github.com/valseeee/agenttxn/releases/download/v0.5.0/agenttxn-v0.5.0-source.zip)，进入解压后的 `agenttxn-v0.5.0` 目录；已有源码的用户直接在项目根目录执行。

两个示例无需 Token，不调用外部 API，使用临时文件并在结束后清理。这里只安装项目和运行示例，无需开发依赖。

Windows PowerShell（`py -3` 所选版本须为 3.11 或更新版本）：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe examples\successful_transaction.py
.venv\Scripts\python.exe examples\demo_transaction.py
```

Linux/macOS（`python3` 须为 3.11 或更新版本）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/successful_transaction.py
.venv/bin/python examples/demo_transaction.py
```

检查结果：

- 成功示例显示 `COMMITTED` 和 `verification: True`。
- 补偿示例故意让后续步骤失败，显示 `final: COMPENSATED` 和 `event chain valid: True`。

演示显式关闭可逆操作审批以便快速体验；真实集成应按策略启用审批。后面的 SDK 示例展示显式审批流程。

## 选择下一步

| 入口 | 展示内容 | 前提 |
|---|---|---|
| [成功事务](examples/successful_transaction.py) | 创建模拟资源并验证审计链 | 无外部服务 |
| [失败与补偿](examples/demo_transaction.py) | 后续步骤失败后补偿先前操作 | 无外部服务 |
| [GitHub Issue](examples/github_issue.py) | 审批后创建真实 Issue | 测试仓库与最小权限 Token，会产生真实写入 |
| [Python SDK](#python-sdk-示例) | 在自己的程序中构建并审批事务 | 已安装 Python 包 |
| [PostgreSQL、诊断与部署](docs/operations.md) | 共享后端、指标、告警和容器验证 | PostgreSQL 通过 SDK 使用 |

完整运行说明见[示例导航](examples/README.md)。

## 安装到现有项目

在 Python 3.11+ 虚拟环境中安装 v0.5.0 wheel：

```bash
python -m pip install "https://github.com/valseeee/agenttxn/releases/download/v0.5.0/agenttxn-0.5.0-py3-none-any.whl"
agenttxn version
```

版本检查应显示 `0.5.0`。安装 Python 包会同时注册 `agenttxn` 命令，无需独立 CLI 安装包。没有激活虚拟环境时，Windows 使用 `.venv\Scripts\agenttxn.exe`，Linux/macOS 使用 `.venv/bin/agenttxn`。

wheel 安装 SDK 和 CLI；运行仓库示例请另下载源码包。各发布资产的用途和校验方式见 [v0.5.0 安装与下载指南](docs/releases/v0.5.0.md)。

可选 PostgreSQL 依赖（仅 Python SDK 后端）：

```bash
python -m pip install "agenttxn[postgres] @ https://github.com/valseeee/agenttxn/releases/download/v0.5.0/agenttxn-0.5.0-py3-none-any.whl"
```

## v0.5.0 更新

- 可选 PostgreSQL 持久化；SQLite 仍是无额外运行时依赖的默认后端。
- 数据库写入在同一事务内校验 worker 身份，拒绝被接管 worker 的迟到结果；不提供外部 API fencing。
- `metrics --format json|prometheus`、`alerts`、`tx telemetry <ID>` 提供不含业务载荷的诊断。
- GitHub 写入与补偿绑定稳定 repository ID，标签查询读取全部分页。

历史修复见 [CHANGELOG](CHANGELOG.md)。已有用户请先阅读[升级与回滚说明](交接说明_0.5.0.md)；部署和后端配置见[运维文档](docs/operations.md)。

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
.venv\Scripts\python.exe examples\github_issue.py
```

`AGENTTXN_GITHUB_REPOS` 是 CLI 安全策略白名单，可用逗号分隔多个仓库；`AGENTTXN_GITHUB_REPO` 是演示脚本使用的目标仓库变量。演示脚本不读取 CLI 白名单，而是通过 `Policy(allowed_github_repositories=frozenset({repo}))` 显式限制目标仓库；自己的 SDK 集成也应配置仓库白名单。

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

详见[升级与回滚](交接说明_0.5.0.md)、[架构](docs/architecture.md)、[事务语义](docs/transaction-semantics.md)、[适配器](docs/adapters.md)和[威胁模型](docs/threat-model.md)。

许可证：Apache-2.0。

## 验证与开发

首次体验无需运行完整测试。修改代码或复测发布时，见 [CONTRIBUTING](CONTRIBUTING.md) 和[发布检查清单](docs/release-checklist.md)。

源码目录中可运行离线验证（Windows；Linux/macOS 将解释器路径换为 `.venv/bin/python`）：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe scripts\validate_release.py
```

已发布的验证记录见 [Release 验证资产](https://github.com/valseeee/agenttxn/releases/tag/v0.5.0)、[发布 CI](https://github.com/valseeee/agenttxn/actions/runs/40273519724)和[最小权限在线验证](https://github.com/valseeee/agenttxn/actions/runs/40273530021)。本地结果不证明远程 CI 或真实外部服务测试通过。容器验证在一个物理 CI 主机上运行，不代表物理多机容灾；独立代理审查不是第三方安全认证。
