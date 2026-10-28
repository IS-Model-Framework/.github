# AI Review / Disposition Check

通过条件：每个指定 AI 完成审查，且应处理的行内线程都有有效回复。GitHub 原生 **Require conversation resolution before merging** 负责要求线程 resolved；人工 approval 单独保留。检查器不执行 PR 代码，也不自动回复或 resolve。

## 审查与回复规则

- `AI_REVIEW_BOTS` 必填，逗号分隔精确 login，忽略大小写；每个账号分别判定。GraphQL Bot login 缺少 `[bot]` 时补齐。
- 已发布的 `COMMENTED`、`APPROVED`、`CHANGES_REQUESTED` Review 或下表成功摘要可证明完成。Codex 存在可识别摘要时，以 Code Review 行状态为准：进行中或失败期间，即使随后发布新 Review，也须等摘要 Completed；全部 dismissed 的兜底规则见下一条。无摘要时可使用已发布 Review。新开始消息、更新的 pending / 进行中状态使旧结果不能提前放行。同一 AI 的轮次不得重叠；新 commit 本身不强制重审。
- 本轮全部 Review 被 dismissed 后，本轮失效，可用更早有效 Review 或成功摘要兜底；没有依据则阻断。随后新一轮仍须重新完成。
- 指定 AI、PR 作者之外的人工 reviewer 创建的 inline threads 均须处理，包括 outdated / resolved。顶层评论、Review 总评、未发布的 pending 意见、作者自己的人工线程和未配置 Bot 的线程不作为处理目标。
- 回复须来自 PR 作者或当前有 write/admin 权限的人工账号；编辑后的模板还须由这样的人工账号编辑。AI 账号和 Bot 不能借用人工评论作者身份处理，编辑者不可读时也不放行。采用编辑时间最新的明确模板，同一时间取更后的回复；新无效模板或未授权编辑会替代旧模板并阻断，不能回退旧有效回复。普通讨论、原意见编辑不要求重复确认，新问题应另开线程。
- AI 完成/开始评论的当前内容也须由相应 AI 编辑。人工编辑的 Bot 评论只使旧完成记录失效，不能构成完成；随后由该 AI 发布的新有效完成依据可恢复。

| AI | 开始与完成依据（仅接受相应账号的 PR 顶层评论） |
| --- | --- |
| `infiscale-bot` | 首行 `收到，开始 review`（允许前置 👀）为开始；之后发布 Review 或全文为 `未发现本次 PR 引入的新问题。LGTM！` 即完成。前提是一轮 Review 几乎同时发布 |
| `chatgpt-codex-connector[bot]` | 摘要以 `<!-- codex-pull-request-review-summary -->` 开头；取 Code Review 行自己的状态与时间，须为 `✅ **Completed**`。进行中、失败或 Security Review 更新不能刷新旧完成记录 |

在对应线程发送普通文本，每个字段一行；字段名和状态不区分大小写，允许空行，不接受引用、代码块、重复或未知字段：

```text
Disposition: Accepted
Reason: 已修复边界判断。
Fix: <当前 PR 中的完整 40 位 commit SHA>
```

| Disposition | 必填内容（所有状态均须非空 Reason） |
| --- | --- |
| Accepted | Fix 为完整 40 位 SHA，或本仓库标准 commit URL；提交必须仍属于当前 PR |
| Rejected / Invalid | 拒绝或误报理由 |
| Deferred | Tracking 为本仓库真实、可读且开放的 Issue URL，不能是 PR |

不适用的 Fix / Tracking 可省略或填 `N/A`。只接受 `https://github.com/owner/repo/commit/<SHA>`、`https://github.com/owner/repo/issues/<正整数>`，不接受查询参数、锚点或跨仓库链接。转移重定向不能绕过仓库限制。PR commits 连接截断时，使用固定 base/head SHA 的分页 Compare API 补全，并校验提交总数；超过 250 条的 PR 无需改填其他 Fix。force-push 移除修复提交、Issue 关闭或删除均使相应回复失效；作者需更新为有效回复。检查器不判断理由是否充分、commit 是否真正修复或延期是否合理。

## 接入

1. 将 [caller](examples/ai-review-disposition.yml)、[事件收集器](examples/ai-review-events.yml) 和 [失败恢复入口](examples/ai-review-recovery.yml) 复制到目标仓库 `.github/workflows/`，合入默认分支。caller 文件名保持 `ai-review-disposition.yml`，run-name 保持 PR 编号或 `all-open-prs`，供初始化失败时恢复路由。
2. 设置 Variable `AI_REVIEW_BOTS`，例如 `chatgpt-codex-connector[bot],infiscale-bot`。caller 和恢复入口需要 `actions: read`、`contents: read`、`pull-requests: read`、`issues: read`、`checks: write`。
3. 首次运行后，将脚本发布的 **AI Review / Disposition Check** 设为 Required Status Check，来源 GitHub Actions；不要选择 Actions job 的状态，其 SHA 不一定是 PR head。
4. 同时启用 **Require conversation resolution before merging** 并保留人工 approval。Resolve/Unresolve 由 GitHub 原生规则处理，不需额外运行本检查。

升级时先合入共享实现，再同步更新目标仓库 caller 和恢复入口；旧 caller 缺少上述 run-name 时，恢复入口无法自动定位初始化失败的目标。

PR opened/reopened/synchronize、目标分支及 PR 顶层评论变化触发单 PR 检查；不监听 ready_for_review。Review submitted/dismissed、行内评论 created/edited/deleted 先运行无权限收集器，再由默认分支的 workflow_run 启动可信检查器。收集器的 run-name 保持 PR 编号，不传入代码或审查结论。

本仓库 Issue closed/reopened/deleted/transferred 时复查全部开放 PR，不维护 Issue → PR 反向关联。路由 job 只读取开放 PR 元数据，输出 worker matrix，不直接写 Check；单 PR、全仓复查和失败恢复均进入相同的按 PR 编号分组的 worker。每个运行最多并行评估 4 个 PR，一个 PR 失败不会取消其他目标；单 worker 最长 15 分钟。Actions matrix 单次最多支持 256 个目标，超限明确失败。排队中的目标在 worker 开始登记前仍可能显示旧结果。新建普通 Issue 不触发。

### 自动化修改 Issue

GitHub 不会因 `GITHUB_TOKEN` 修改 Issue 自动启动后续 Actions。使用 [关闭 Issue 并复查的示例](examples/ai-review-close-issue.yml)，或在已有自动化修改后 dispatch **同一份 caller**：

```bash
gh workflow run ai-review-disposition.yml --repo "$GITHUB_REPOSITORY" \
  --ref "$DEFAULT_BRANCH" -f all-open-prs=true
```

DEFAULT_BRANCH 为仓库默认分支，负责 dispatch 的 job 需要 `actions: write`，关闭 Issue 另需 `issues: write`。无需额外 Token；其他自动化发布 Review / 评论后同样须主动复查。不要由另一工作流或本地 CLI 绕过共享 worker 直接写正式 Check。检查器使用 GITHUB_RUN_ID/run_attempt 标识运行，caller 和恢复工作流的局部 run_number 不再相互比较。

手动检查填写正整数 pr-number；全仓复查勾选 all-open-prs、保持编号 0。默认不会隐式全仓扫描。CI 始终实时读取 PR 并将 Check 写入最新 head；通过前再次读取数据，清缓存复核权限和 Issue 状态。只读 REST/GraphQL 请求遇瞬时网络错误、429、500/502/503/504 或带 Retry-After 的 403 时最多尝试 3 次；遵守 Retry-After，等待超过 60 秒则中止并报告失败，不提前重试。创建/修改 Check 不盲重试。数据变化或读取失败不发布成功，Issue 的 404/410 会给出具体 Tracking 提示。

同一 PR 的全部 worker 共用一个 concurrency group，cancel-in-progress=false；正在运行的 worker 保持执行，等待中的重复触发由最新任务合并替代。不同 PR 可并行。worker 开始后重新读取当前 head；同一仓库、PR、head 和 GitHub Actions App 复用同一 Check ID，重新设为 in_progress，再更新结果。新 head 单独创建 Check；不会再创建或改名出 superseded 项，既有历史记录不整理。重跑旧 run ID/attempt 不能接管更新运行。

此方案无需外部服务。GitHub 自动产生的路由、评估及事件收集 job Check 仍可能出现在 PR 页面；唯一业务门禁是 AI Review / Disposition Check，不能据此承诺整页仅一项。实验工作流发布的 Integration Test 也不属于正式检查器管理范围。

普通退出由 always 收尾在释放同一个 PR 并发槽前读取本机登记日志，仅将仍由自己持有的未完成 Check 原地标记 failure。硬超时、取消、检出/初始化失败由默认分支的 workflow_run 恢复入口定位目标，再进入同一个 worker 队列重新检查实时数据；恢复不直接写 Check，也不会创建替代 Check。恢复重新评估可能得到正常的 success 或业务 failure。仅读取可信脚本写出的本机日志或实时 API，不下载或执行来源工作流的 artifact。最终发布失败会记录 Check ID 和重新 dispatch 的提示。

Checks API 的 ownership 校验与 PATCH 不是原子操作。并发槽约束正常运行的 Actions writer；人为硬取消时已经送出的 HTTP 写入仍有极短的竞态，复用单一 Check ID 无法提供旧方案不同 ID 的隔离保证。不要另行直接调用 Checks API 写这个门禁；发生中断或 API 异常后应等待恢复并检查最新结果。

恢复是异步的：事件发生到新 Check 登记、或失败到恢复运行之间存在窗口。若首次读取、枚举或登记失败，旧 success 可能在恢复前仍可见；GitHub API 完全不可读写、恢复入口未安装或自身失败时，脚本无法保证立即撤销它。必须监控 caller/恢复运行失败并重新 dispatch；要求这个窗口内也绝不允许合并时，需额外的 GitHub 侧合并门禁，不能仅依赖本异步检查。登记后的遗留 in_progress 会继续阻断。

## 验证与边界

运行 `python -m unittest discover -s scripts/tests -v`。测试包含 AI 轮次、编辑者授权、第二人工账号权限/撤权、超过 250 条提交、限流重试、初始化/发布失败、20 次检查复用同一 ID、旧运行接管拦截和全仓/恢复路由，无需真实账号或 Token。接入时另验收 caller 与恢复入口的 run-name、取消/超时和初始化失败路由。真实 Bot 接入仍须现场验证；不能把模拟当成实际授权已生效。

自动关 Issue 的 [触发工作流](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40306714886) 和 [后续复查](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40306733764) 已在隔离仓库实测。一次性实验、规则配置和历史结果保留在[测试仓库](https://github.com/liaoxiang0304/VR_DexCopilot)，不作为生产接入文件。

仅支持 GitHub.com、同仓库 PR/Issue；fork PR、Issue 转移不作为本次验收前提。多 PR 共用同一 head 存在 GitHub 门禁关联限制，各 PR 应使用独立 head。不支持 merge queue 合成 SHA 或不可删改的历史审计。
