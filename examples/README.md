# 示例 / Examples

[返回首页 / Project README](../README.md) · [English quickstart](../README.en.md#try-the-offline-examples)

先按首页安装 Python 3.11+ 和项目包，然后在源码根目录运行以下示例。Windows 使用 `.venv\Scripts\python.exe`；Linux/macOS 使用 `.venv/bin/python`，并把脚本路径中的 `\` 换为 `/`。

| 示例 | 命令（Windows PowerShell） | 预期结果 | 外部副作用 |
|---|---|---|---|
| [成功事务](successful_transaction.py) | `.venv\Scripts\python.exe examples\successful_transaction.py` | `COMMITTED`、`verification: True` | 无 |
| [失败与补偿](demo_transaction.py) | `.venv\Scripts\python.exe examples\demo_transaction.py` | `final: COMPENSATED`、`event chain valid: True` | 无 |
| [GitHub Issue](github_issue.py) | `.venv\Scripts\python.exe examples\github_issue.py` | 输入 `YES` 审批后，成功时显示 `COMMITTED` | 在指定测试仓库创建真实 Issue |

两个离线示例使用临时目录，在退出时清理，可重复运行。补偿示例中的失败是故意设置的，最终 `COMPENSATED` 是预期结果。它们显式关闭可逆操作审批；需要人工审批的用法见[首页 SDK 示例](../README.md#python-sdk-示例)。

The offline examples need no token or external service. Temporary state is cleaned up on exit. They disable approval for reversible actions for demonstration. `COMPENSATED` is the intended result of the failure demo, not a global rollback guarantee.

## GitHub 示例：真实写入

只使用测试仓库和最小权限 Token。此脚本读取 `GITHUB_TOKEN` 与 `AGENTTXN_GITHUB_REPO=owner/sandbox-repo`，通过 SDK 策略显式限制目标仓库。它不读取 CLI 的 `AGENTTXN_GITHUB_REPOS` 白名单变量。

脚本会显示预检结果，并要求输入 `YES` 才创建 Issue；拒绝时终止事务。成功创建的 Issue 不会自动关闭，请在测试仓库确认结果后手动关闭。脚本把本地状态保存在 `.agenttxn/github-demo.db`，对同一仓库使用固定幂等键；不要把它当成每次运行都新建 Issue 的脚本。

The GitHub demo creates a real Issue after interactive approval. Set `GITHUB_TOKEN` and `AGENTTXN_GITHUB_REPO` for a sandbox repository. The SDK policy allowlists that repository. Successful Issues are not automatically closed; inspect and close them manually. Durable local state and fixed idempotency keys are intentional.

Token 配置、CLI 与 SDK 白名单区别及 GitHub 查询边界见[集成说明](../README.md#可选-github-issues-集成)。不要把 Token 写入源码或 action payload。
