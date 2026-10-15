# AI Review / Disposition Check

目标：所有需要响应的 AI inline findings 都有可审计的处理记录，并且对话已 Resolve。检查不要求采纳 AI 建议，也不代替 Human Review。

本仓库提供 [Python 检查器](../scripts/check_ai_review_disposition.py)、[reusable workflow](../.github/workflows/reusable-ai-review-disposition.yml) 和两个项目接入示例。规则单独运行，不并入现有 `CI Result`：代码 CI 与 review 的完成时间不同，回复评论也不应该重新运行测试。

## 合并条件

```text
每个指定 AI Bot 已完成当前 head SHA 的 review
AND 每个 AI finding 有授权人员的有效 disposition
AND 每个 finding 的 conversation 已 Resolve
```

检查范围为配置的 Bot 创建的所有已发布 inline review threads，包括历史提交上的 outdated threads 和已 Resolve 的 threads。每个 thread 是一条 finding；Bot 应把不同问题分成不同 threads。以人类评论开头的 thread 不计入，PR 顶层评论/总结也不计为 finding。尚未提交的 pending review 不计入已发布 findings。

Bot 由精确 GitHub login 匹配，例如 `reviewer[bot]`；不根据显示名、正文或模糊子串识别。GraphQL 的 `Bot` 类型若返回不带后缀的 login，会转换成标准 `[bot]` 格式；普通 `User` 不做此转换。`AI_REVIEW_BOTS` 中的每个 Bot 都必须完成 review。空配置是错误，不会当作“无 AI 问题”通过。

回复者必须是 PR 作者或当前拥有仓库 `write` / `maintain` / `admin` 权限的人员。其他人员的回复不生效；GitHub Bot 和已配置的 AI 服务账号不能自行 disposition。仅有组织成员身份不足以授权回复。

| Disposition | 必须提供的字段 | 检查的证据 |
| --- | --- | --- |
| Accepted | `Reason`、`Fix` | 完整的 40 位 commit SHA，必须仍在当前 PR 的 commits 中 |
| Rejected | `Reason` | 非空拒绝理由 |
| Invalid | `Reason` | 非空误报理由 |
| Deferred | `Reason`、`Tracking` | 存在且开放的 GitHub Issue，或指定 Jira 域名下的 ticket URL |

`Fix` 证明提交引用有效；工具不判断该提交是否真正修复问题。理由的充分性、Jira ticket 的存在与状态，以及 deferral 的业务可接受性由 Human Review 审查。当前版本不支持无 tracking 的口头豁免；若未来接入审批，应验证审批人的身份及审批记录，而不是接受 Contributor 自填 `Approved: yes`。

## Contributor 回复格式

直接在对应 inline thread 中回复。第一行必须是 `Disposition: ...`，每个字段单独一行；允许空行和字段名/状态大小写变化。不要加引号、Markdown 引用、代码块或额外字段。`Reason` 保持一行，可以用中文。

Accepted：

```text
Disposition: Accepted
Reason: 已补充空数组边界判断，并增加回归测试。
Fix: 0123456789abcdef0123456789abcdef01234567
```

Rejected：

```text
Disposition: Rejected
Reason: 调用入口已验证该条件，此处不会收到负数输入。
```

Invalid：

```text
Disposition: Invalid
Reason: 此实现使用异步执行，评论所述的同步调用路径不存在。
```

Deferred：

```text
Disposition: Deferred
Reason: 本次先完成兼容修复，性能优化在后续版本处理。
Tracking: https://github.com/IS-Model-Framework/example/issues/42
```

配置 `AI_REVIEW_JIRA_URL=https://example.atlassian.net` 后，也接受 `Tracking: https://example.atlassian.net/browse/PROJ-123`。不接受任意 HTTPS 链接、裸 ticket 编号或 PR 链接。GitHub Issue 需要当前 token 可读取；其他仓库的私有 Issue 可能需要 GitHub App installation token。

采用最新编辑时间的明确 disposition 回复；同一时间取 thread 中更后的回复。新的不完整 disposition 会替代旧的有效回复，避免沿用过期决定。普通交流回复不会替代 disposition。AI 编辑 finding 或追加 inline 回复后，需要重新确认 disposition，并重新提交完成 review 的标记。修复提交被 force-push 移出 PR 后，Accepted 失效。

有效回复之后，由 Contributor / Maintainer Resolve Conversation；工具不会自动 Resolve。只 Resolve、只写 “done / 已修复”、在 PR 总结里统一回答，都不能通过。

## AI Bot 完成协议

Bot 的 review 流程必须遵守：

1. 对固定的 PR head SHA 生成 findings，用 inline threads 发布。
2. 全部 findings 发布完毕后，提交一个非 pending 的 review，其正文包含独立一行：

   ```text
   AI-Review-Complete: <当前 PR 的完整 head SHA>
   ```

3. review 本身的 `commit.oid` 也必须等于该 SHA。没有 findings 时仍需要这个 review；这样“零问题”和“尚未开始 review”不会混淆。
4. 当前 head 改变后，重新 review 并提交新完成标记。后续提交的新 review 未含标记、review 被 dismissed，或者 Bot 在完成标记之后继续增加/编辑 finding，均阻断通过。

这是本工具的集成协议，不是 GitHub 自带的字段。现有 Bot 若不能提供该标记，需要修改其发布流程；不能仅凭 “没有 unresolved threads” 推断 review 已完成。不要在 findings 尚未全部发布时写入完成标记。

## Actions 接入

`.github` 仓库的 workflow 不会自动在所有组织项目执行。每个目标项目需要 caller workflow，并由组织 ruleset 覆盖目标仓库。

1. 将 [ai-review-events.yml](examples/ai-review-events.yml) 与 [ai-review-disposition.yml](examples/ai-review-disposition.yml) 复制到目标项目 `.github/workflows/`，合入默认分支。
2. 在组织/仓库 Variables 设置 `AI_REVIEW_BOTS` 为准确的 login 列表，逗号分隔。需要 Jira 时设置 `AI_REVIEW_JIRA_URL`。示例中的 Bot 名称不是实际默认配置。
3. 生产环境将 caller 的 `uses: ...@main` 与 `shared-ref: main` 同时固定到本仓库同一个已审核 commit SHA。共享脚本从可信 CI 仓库加载，完全不 checkout PR 代码。
4. 为 `GITHUB_TOKEN` 提供 `contents: read`、`pull-requests: read`、`issues: read`、`checks: write`。如果组织 CI 仓库是 private，还需要开启 reusable workflow 的组织访问，并通过可选 secret `shared-read-token` 提供 CI 仓库的只读 checkout token；caller 的 `GITHUB_TOKEN` 不会自动获得其他 private 仓库的读取权限。
5. 完成一次运行后，将 **`AI Review / Disposition Check`** 加入 ruleset 的 Required Status Checks，检查来源选择 GitHub Actions。保持现有 Human Review / approval 规则。

选择的是脚本通过 Checks API 发布到 PR **head commit** 上的 Check，不是 `Evaluate AI Review Dispositions` 这个 Actions job。`pull_request_target`、`issue_comment`、`workflow_run` 的 `GITHUB_SHA` 不一定是 PR head；直接使用 job success 作合并门禁会关联到错误提交。

| 事件 | 示例中的路径 |
| --- | --- |
| PR opened / reopened / synchronize / ready_for_review | `pull_request_target` 调用可信检查器 |
| Review submitted / edited / dismissed | 无权限的 event collector → 默认分支 `workflow_run` → 检查器 |
| Inline comment created / edited / deleted | 同上 |
| PR 顶层评论 created / edited / deleted | `issue_comment` 触发复查，可用一条 “AI review recheck” 评论重新计算 |
| Resolve / Unresolve | Actions 不直接支持此事件；手动复查、顶层评论，或 App webhook → `repository_dispatch` |
| Bot 用 `GITHUB_TOKEN` 发布 review/comments | 通常不会触发后续 Actions；review workflow 结束后显式复查，或改用 App token 发布 |

collector 不运行 PR 内容、不上传 artifact。后续有写权限的 `workflow_run` 不信任 collector 输出，而是读取 GitHub API 的实时状态，支持 fork PR 的评论事件。每次默认检查所有 open PR，绕开 fork 的 `workflow_run.pull_requests` 可能为空，以及同一 PR 密集事件被 concurrency 合并的问题。

所有扫描使用仓库级 concurrency，`cancel-in-progress: false`。发布 Check 后发生 API 错误会标记 failure；超时/取消可能留下 in-progress Check，仍然阻止合并。通过之前再完整读取一次 review 数据，发现提交或评论变化就失败。扫描规模大时应改用 App，按 PR 串行处理。

手动复查可在 Actions 页面运行 `AI Review Disposition`。单独调试某个 PR：

```bash
GH_TOKEN="<token>" python scripts/check_ai_review_disposition.py \
  --repository IS-Model-Framework/example \
  --pr-number 128 \
  --bots 'reviewer[bot]'
```

这条命令会通过 Checks API 写入 PR Check，不是 dry-run。Token 通过环境变量提供；不应提交到仓库。未指定 PR（`--pr-number 0`）时检查全部 open PR。退出码：0 全部通过，1 存在未处理项，2 参数/API 等执行错误。

## 严格组织门禁：GitHub App

Actions 方案适合初期接入，但不保证评论变化后瞬间撤销 success：事件排队、运行延迟、Resolve/Unresolve 缺少原生 trigger，都会留下状态更新窗口。需要严格覆盖 thread 状态变化时，建议组织安装专用 GitHub App，用同一个检查器作为执行核心：

```mermaid
flowchart LR
  A[PR / Review / Comment / Thread webhook] --> B[验证签名并按 PR 串行排队]
  B --> C[将当前 head 的固定 Check 标记 in_progress]
  C --> D[分页读取实时 reviews / threads / comments / commits]
  D --> E{完成协议 + disposition + resolved}
  E -->|满足| F[success]
  E -->|未满足或 API 失败| G[failure]
  F --> H[Human Review 与 Required Approval]
```

App 应订阅 `pull_request`、`pull_request_review`、`pull_request_review_comment`、`pull_request_review_thread` 的相关 actions，并支持 Check rerequest；用 webhook signature 与 delivery ID 校验/去重，每个 PR 串行更新同一个 head 的 Check，变化时立即撤销旧 success。重试失败保持阻断，并以定期 reconciliation 恢复漏发/丢失事件。

权限为 Pull requests read、Contents read、Issues read、Checks write；安装 token 只覆盖获授权仓库。若调用跨仓库 Issue，需要相应仓库访问权限。当前提交未包含 App 的 webhook 服务、部署或持久化账本。

GitHub 的 Status Check 仍是异步状态，无法把“读所有评论”和“执行 merge”做成一个原子事务。高要求场景还应启用分支保护的 conversation resolution，并通过受控 merge 入口在合并前再次核验。AI findings 被删除后，实时 API 不再保留它们；若要求删除也必须留痕，App 需要持久化 finding ID 与 disposition 历史。此版本不会把 API 数据当作不可删改的审计账本。

该 Check 以 PR head 为对象；当前不支持 merge queue 生成的合成 SHA。使用 merge queue 的项目需要另外设计 `merge_group` 的成员 PR 核验，不能直接沿用此 caller 作为队列 required check。

## 验证与参考

```bash
python -m unittest discover -s scripts/tests -v
```

测试覆盖处理状态、回复权限、完成协议、outdated/resolved threads、force-push、分页、API 错误与读取期间新增 findings。API 通过 mock 验证；仍需在目标仓库做一次真实的 Bot Review → 回复 → Resolve → 复查 → Required Check 试运行。

- [GitHub Actions 事件与 fork / workflow_run 行为](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Webhook 事件：pull_request_review_thread](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request_review_thread)
- [Checks API：发布并更新 Check Runs](https://docs.github.com/en/rest/checks/runs)
- [Reusable workflows 的组织接入与权限](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
