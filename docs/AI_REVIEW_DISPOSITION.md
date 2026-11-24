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

## 单一检查的显示与状态

PR Checks 列表只发布一个固定名称 **AI Review / Disposition Check**。底层使用 Commit Status，同一 head SHA 和 context 只展示最新状态；不再创建自定义 Check Run、superseded 项、通知 job 或路由 job 的 PR 检查。GitHub 仍保留状态写入历史和后台 Actions 运行日志，这不等于 PR 列表新增条目。

显式复查先将此名称发布为真正的 `pending`，再读取并校验机器人完成记录、人工回复、Fix / Tracking 和最终实时证据，结束后发布 `success` 或 `failure`。失败的必要步骤不会放行。点击 Details 查看该次后台运行的日志和 Summary。已完成的 Check Run 不能可靠地重置为待检查，因此不再使用临时 failure / Checking 代替 pending。

| 变化 | PR 中这一行的结果 |
| --- | --- |
| 全部机器人完成，全部意见处理有效 | success |
| 显式复查开始，或自动扫描发现证据变化 | pending |
| 缺少完成记录、有效回复或证据 | failure |
| 再次复查并满足全部条件 | pending → success |

每个新 head 单独拥有这一名称的状态，旧提交的结果不能替代新 head 的检查。已有的其他 CI 检查仍分别保留。

## 接入与升级

1. 合入共享实现后，将 [caller](examples/ai-review-disposition.yml) 和 [失败恢复入口](examples/ai-review-recovery.yml) 放入目标仓库默认分支的 `.github/workflows/`。caller 文件名保持 `ai-review-disposition.yml`，run-name 保持 PR 编号或 `all-open-prs`，供初始化失败时恢复定位。
2. 删除旧的 `AI Review Events` 事件收集器，替换旧 caller / recovery，停用其他直接调用旧实现的工作流。只升级共享脚本不能消除下游旧工作流产生的原生 job Check。
3. 设置 Variable `AI_REVIEW_BOTS`，例如 `chatgpt-codex-connector[bot],infiscale-bot`。两个入口均需 `actions: read`、`contents: read`、`pull-requests: read`、`issues: read`、`statuses: write`，不再需要 `checks: write`。
4. 将 caller 的 `workflow_run.workflows: [CI]` 改为目标仓库**现有**代码 CI 的准确 workflow 名称；没有此工作流时删除这个加速入口，保留定时扫描。不要为接收 review 新增 PR 事件收集工作流。
5. 首次生成状态后，将 **AI Review / Disposition Check** 设置为 Required Status Check，来源匹配实际发布者 GitHub Actions。不要选择后台 worker 的 job 名称。同时启用 **Require conversation resolution before merging**，保留人工 approval。
6. 从旧版迁移的现存 PR，升级接入文件后需推送一个新提交，使当前 head 不再带旧 Check Run。GitHub 同名 Check Run 和 Commit Status 同时存在时会同时判定，旧 job / superseded 历史也不会被新状态隐藏。本实现不删除或重命名历史。新 head 才作为单行显示的验收对象。

本次实现无需外部服务。共享仓库的代码与示例更新不会自动修改消费者仓库的工作流、变量或 ruleset；必须完成上述接入才生效。

## 默认分支后台入口

所有功能运行均从目标仓库默认分支启动，只检出可信共享脚本并实时读取 API，不检出或执行 PR 代码。可复用工作流只有一个 worker，把目标选择、规则校验、结果发布和中断收尾放在同一个 job 内。

- 已有 `CI` workflow 完成：后台复查所有开放 PR，为打开 / 推送 PR 提供加速入口，不依赖来源运行提供的 artifact 或结论。
- PR 顶层评论 created / edited / deleted：显式复查对应 PR。
- 本仓库 Issue closed / reopened / deleted / transferred：显式复查所有开放 PR。
- 每五分钟定时扫描：检测 review submitted / dismissed、行内回复增删改、新 head、权限和 Tracking Issue 变化；cron 使用第 3、8、13…58 分钟，避开整点。GitHub 可能延迟或丢弃 schedule，五分钟是计划频率而非最大延迟保证。
- 手动 `workflow_dispatch`：填写正整数 `pr-number`；全仓复查勾选 `all-open-prs` 并保持编号 0。首次启用后建议立即手动全仓复查。

不直接监听 `push`、`pull_request*` 或部署事件；这些事件会让 GitHub 为工作流 job 额外发布 PR 检查。后台入口使用 `workflow_run`、`issue_comment`、`issues`、`schedule` 和 `workflow_dispatch`，原生 job 留在 Actions 运行记录中。消费者需要沿用该事件模型，不能另行把此可复用工作流挂到 PR 事件上。

排队及扫描有延迟：事件发生到下一次扫描 / worker 处理前，旧状态可能仍可见；执行器登记新一轮评估时才写入 pending。尚未发布状态的新 head 由 Required Status Check 保持等待。需要快速复查时，从默认分支手动 dispatch，不能要求纯异步方案在事件发生瞬间撤销旧成功。

### 无变化扫描与状态写入上限

定时扫描及已有 CI 完成后的扫描使用 `only-changed: true`。每次仍读取当前 PR、机器人和回复，并重新查询实际使用的人工权限与 Tracking Issue；只有完整证据指纹、机器人配置和判定结果都未变化时才保留已有终态，不发布新的 pending / success。发现任何变化、上次中断、读取异常或无有效指纹时重新发布 pending 并评估。显式手动复查和评论 / Issue 事件不跳过 pending。

GitHub 对同一 SHA、同一 status context 最多接收 1000 次状态写入，因此不能承诺无限次更新。未变化扫描不消耗此额度；接近上限时预留写入将状态固定为 failure，并提示推送新提交，避免留下无法撤销的 success。不要由其他工作流、App 或本地 CLI 写这个正式 context；它们会破坏序号、指纹、发布者校验和额度计数。

### 自动化修改 Issue / review

GitHub 不会因 `GITHUB_TOKEN` 修改 Issue 或评论自动触发后续 Actions。定时扫描会检测变化；如需立即排队，使用 [关闭 Issue 并复查的示例](examples/ai-review-close-issue.yml)，或在自动化后 dispatch **同一份 caller**：

```bash
gh workflow run ai-review-disposition.yml --repo "$GITHUB_REPOSITORY" \
  --ref "$DEFAULT_BRANCH" -f all-open-prs=true
```

负责 dispatch 的 job 需要 `actions: write`，关闭 Issue 另需 `issues: write`。不需要额外 Token，不另建对 PR 显示的通知 job。

## 并发、最终复核与失败恢复

所有单 PR、全仓和恢复运行共用仓库级 concurrency group，`cancel-in-progress=false`，避免两个入口同时写相同 context。等待中的重复触发可能被最新任务替代，后续定时扫描会重新覆盖全部开放 PR。单次运行串行处理目标，一个 PR 失败后仍继续其余 PR；worker 最长 30 分钟，目标过多时需关注超时和 API 配额。

后台 worker 使用 `--background`：正常发布的业务 failure（例如等待机器人或缺少回复）由唯一 PR 状态表达，后台运行可成功，避免每次定时扫描又启动一次无意义恢复。读取 / 校验异常、结果发布失败或状态额度耗尽仍使 worker 失败并启动恢复；不能把后台 job 的成功当成 PR 门禁通过。直接运行 CLI 时不加该参数，业务 failure 仍返回非零退出码。

显式检查在获取当前 head 后先发布 pending。通过前再次读取完整 PR 快照，清缓存复核人工权限和 Issue 状态，确认结果仍有效。只读 REST / GraphQL 请求的瞬时网络错误、429、500/502/503/504 或带 Retry-After 的 403 最多重试三次；不早于 Retry-After 重试，等待超过 60 秒则中止。状态写入不盲重试。

状态 Details URL 关联实际 run ID / attempt，并携带受控的 PR、写入计数和证据指纹。旧 run 的重跑不能覆盖已登记的新 run；最终发布和 cleanup 先检查当前状态 ID 是否仍属于自己。普通退出通过本机登记日志将自己遗留的 pending 标记为中断 failure；最终写入失败时保留 pending 阻断。取消、硬超时或初始化失败由独立的默认分支恢复入口定位原目标，经过同一个 worker 队列实时重查；恢复不直接写旁路结果。定时扫描也会重试遗留 pending / failure。

所有权读取与状态 POST 不是原子操作，硬取消时已发送的 HTTP 写入仍存在短暂竞态。API 不可读写、首次读取 / 初始化失败或恢复入口自身失败时，也无法保证立即撤销旧成功；必须监控后台 Actions 的异常并重新 dispatch。严禁绕过共享队列并发写正式状态。

## 验证与边界

运行 `python -m unittest discover -s scripts/tests -v`。测试覆盖原有审查规则、权限和分页，以及同名状态的反复 pending / success / failure、无变化扫描去重、Issue 关闭 / 权限撤销、新轮次、中断收尾、旧运行防覆盖和状态额度耗尽时的阻断。工作流另用 actionlint 验证。

单行契约是当前 PR Checks 列表中的一个 status context，不是 GitHub 不保留底层记录。旧提交、旧工作流和旧 Check Run 历史不清理。只支持 GitHub.com 同仓库 PR / Issue；同一 head 被多个开放 PR 共用时无法分别表达各 PR 的不同审查结果，应推送不同 head。fork 和 merge queue 尚未验收。模拟 / API 状态生命周期测试不能证明真实机器人已经完成审查或实际权限已生效。

GitHub 机制参考：[Commit Status 与 1000 次上限](https://docs.github.com/en/rest/commits/statuses)、[工作流原生检查的事件关联及同名检查冲突](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)。
