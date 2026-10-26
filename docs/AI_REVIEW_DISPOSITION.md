# AI Review / Disposition Check

合并门禁分工：本 Check 验证每个指定 AI 按下述轮次规则完成审查，以及 AI 和人工 reviewer 的每条行内线程都有有效模板回复；GitHub 原生的 **Require conversation resolution before merging** 规则负责要求讨论线程全部 resolved。两项都满足才能合并，无需采纳所有意见，也不代替人工 approval。

## 检查规则

- 默认明确指定一个 PR；closed/merged PR 跳过。只有显式的 `--all-open-prs` 才扫描本仓库全部开放 PR。Check 写入各 PR 的最新 head commit。
- `AI_REVIEW_BOTS` 是必填的逗号分隔 login 列表，每个账号都必须完成审查。按 login 精确匹配，忽略大小写；GraphQL `Bot` login 缺少 `[bot]` 后缀时补齐，普通 `User` 不补。
- 完成依据是该 Bot 发布的 `COMMENTED`、`APPROVED` 或 `CHANGES_REQUESTED` Review，或下表中的成功结束评论。`PENDING`、`DISMISSED`、开始/失败/配额不足提示本身不算完成。
- 每个 AI 单独按开始消息及时间划分轮次，同一 AI 在同一 PR 上的轮次不得重叠。新一轮开始或出现更新的 pending / 进行中状态后，旧结果不能提前放行。没有开始记录的历史数据，兼容已有的已发布 Review 或成功结束评论。
- 本轮发布的 Review 全部被 dismissed 后，本轮作废，允许更早的有效 Review 或成功摘要兜底；没有有效依据则阻断。随后又开始新一轮时，仍须等待该轮完成。
- 审查完成不绑定当前 head，修复后 push 无需强制重审；Accepted 的修复提交仍须属于当前 PR。
- 检查指定 AI 和 PR 作者之外的人工账号创建的全部已发布 inline threads，包括 outdated 和 resolved threads。PR 作者自己的人工线程、未配置 Bot 的线程、PR 顶层评论和 Review 总评不计为待处理线程；pending review 不计入。配置的 AI 即使是 `User` 或 PR 作者，仍按 AI 处理。
- 模板回复者须为 PR 作者或当前具有仓库写权限的人工账号；Bot 和配置的 AI 服务账号不能自行处理意见。组织成员身份本身不代表写权限。
- 采用编辑时间最新的明确模板回复，同一时间取 thread 中更后的回复；新的无效模板会替代旧模板，普通讨论不会。AI/人工使用相同规则，后续讨论或原始意见编辑不额外要求重复确认；新问题应新开线程。
- 本 Check 只验证每个纳入检查的线程有有效回复，不读取或判断 resolved 状态。仅 resolved、仅回复 “done”，或在 PR 总结中统一回答均不能通过。工具不自动回复或 resolve。
- 有效回复可以让本 Check 在未 resolved 时通过；GitHub 原生 conversation resolution 规则继续阻止合并。点击 Resolve/Unresolve 后，GitHub 更新原生合并条件，无需为了该状态重跑本 Check。原生规则覆盖所有代码讨论，范围可能比本 Check 筛选的线程更广。

| Bot login | 开始与完成依据 |
| --- | --- |
| `infiscale-bot` | 首行 `收到，开始 review`（允许前置 👀）为开始；之后有已发布 Review，或去除首尾空白后全文为 `未发现本次 PR 引入的新问题。LGTM！`，视为完成 |
| `chatgpt-codex-connector[bot]` | 自带摘要以 `<!-- codex-pull-request-review-summary -->` 开头，采用 `Code Review` 行自己的状态和时间；最新状态须为 `✅ **Completed**`，进行中或失败不能用旧 Review 提前放行 |

仅匹配相应 Bot 发布的 PR 顶层评论；人工引用、行内回复、Security Review 完成均不能作为完成依据。整条摘要的编辑时间不能刷新旧的 Code Review 完成时间。infiscale 的判定以其一轮 Review 几乎同时发布的行为约定为前提；CI 不修改 Bot，也不额外要求人工确认审查结束。

## 回复模板

直接在对应 inline thread 中发送普通文本。第一行必须为 `Disposition: ...`；每个字段一行，允许空行及字段名/状态大小写变化，不接受引用、代码块、重复字段或额外字段。

```text
Disposition: Accepted
Reason: 已修复边界判断。
Fix: https://github.com/IS-Model-Framework/example/commit/0123456789abcdef0123456789abcdef01234567
Tracking: N/A
```

| Disposition | 必填证据（所有状态都须有一行非空 `Reason`） |
| --- | --- |
| Accepted | `Fix` 为完整 40 位 SHA，或当前仓库的标准 commit 链接；提交必须仍在当前 PR 的 commits 中 |
| Rejected | 拒绝理由 |
| Invalid | 误报理由 |
| Deferred | `Tracking` 为当前仓库的 GitHub Issue 完整链接；API 确认该 Issue 存在、可读、仍开放且不是 PR |

不适用的 `Fix` / `Tracking` 可省略或填 `N/A`。commit 链接仅接受 `https://github.com/owner/repo/commit/<40位SHA>`，Issue 链接仅接受 `https://github.com/owner/repo/issues/<正整数>`，均不接受查询参数或锚点。两种链接均限定当前仓库；跨仓库 Issue 在请求前拒绝，转移后的重定向也不能绕过仓库校验。只使用目标仓库的 `GITHUB_TOKEN`，无需额外钥匙。

force-push 移除修复提交后，Accepted 失效。Issue 关闭后，Deferred 失效，需要作者新增或编辑有效处理回复，例如改为 Accepted 并提供当前 PR 的修复 commit，或改为 Rejected 并说明理由。工具不会自动替作者修改模板。

工具验证引用有效，不判断理由是否充分、提交是否真正修复问题或延期是否合理；这些由人工审查确认。

## CI 接入

本组织有共享 Python CI 和独立 CI；本检查通过独立 caller 接入，保持现有测试及 `CI Result` 不变。组织 `.github` 仓库的 workflow 不会自动在其他仓库运行。

1. 将 [ai-review-events.yml](examples/ai-review-events.yml) 和 [ai-review-disposition.yml](examples/ai-review-disposition.yml) 复制到目标仓库 `.github/workflows/` 并合入默认分支。
2. 设置组织/仓库 Variable `AI_REVIEW_BOTS`，如 `chatgpt-codex-connector[bot],infiscale-bot`。空值会报错，不提供隐式默认 Bot。
3. caller 需要 `contents: read`、`pull-requests: read`、`issues: read`、`checks: write`；不执行或检出 PR 代码。
4. 首次运行后，将脚本发布的 **`AI Review / Disposition Check`** 加入组织/仓库 ruleset 的 Required Status Checks，来源选 GitHub Actions。不要选择 Actions job 的成功状态，事件的 `GITHUB_SHA` 不一定是 PR head。
5. 在目标分支的 ruleset / 分支保护中启用 [**Require conversation resolution before merging**](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-a-pull-request-before-merging)，并保留人工 approval 要求。此项必须与 Required Status Check 同时配置；脚本不会修改仓库规则，也不会因未 resolved 而失败。

PR opened/reopened/synchronize、目标分支变化和 PR 顶层评论变化触发单 PR 检查；不监听 ready_for_review。普通聊天不会开启新审查轮次。Review submitted/dismissed、行内评论 created/edited/deleted 先运行无权限 collector，再通过默认分支的 `workflow_run` 触发检查器。collector 的 `run-name` 必须保持为 PR 编号；该值仅用于路由，判定始终重新读取实时 API，不接受 collector 的代码、artifact 或审查结论。本次验收范围为同仓库 PR / Issue，不要求 fork PR 或 Issue 转移场景。

本仓库 Issue closed/reopened/deleted/transferred 时，caller 使用显式的 `all-open-prs: true` 检查所有开放 PR，不维护 Issue 到 PR 的反向关联，也不把 Issue 编号当作 PR 编号。仍引用无效 Issue 的 Deferred 会阻断；其他 PR 按各自实时状态判断。一个 PR 失败不会跳过后续 PR，新建普通 Issue 不触发。

Resolve/Unresolve 无需触发本 Check，也不需要额外发评论或手动复查；它们由 GitHub 原生 conversation resolution 规则处理。

使用 `GITHUB_TOKEN` 修改 Issue 不会自动启动 `issues` 监听。自动关闭 Issue 的接入示例见 [ai-review-close-issue.yml](examples/ai-review-close-issue.yml)：关闭后 dispatch **同一份 caller**，设置 `all-open-prs=true`，由新的 caller 运行复查所有开放 PR。该自动化需要 `issues: write`、`actions: write`，仍使用自带 Token；无需额外钥匙或 Issue 到 PR 的反向关联。其他自动化修改 Issue、发布 Review 或评论后，也须显式触发复查。

手动 Run workflow 可填写正整数 `pr-number`；全仓复查则勾选 `all-open-prs` 并保持 PR 编号为 0。未指定有效目标会失败，不能意外退化为全仓扫描。手动编号经 JSON 数字转换后传给 reusable workflow。删除 Issue 的 HTTP 410 与不存在的 HTTP 404 均给出无效 Tracking 的具体提示，其余 API 错误仍保守阻断。

同一 PR 的新运行取消旧运行；Issue 全仓复查有独立并发组，仍按每个 PR 争取结果发布权。只部署一份 caller，按其运行编号及 attempt 判断新旧。B 接管后只认 B：B 运行中、失败或取消时均不回退到 A 的通过结果。旧 Check 改为带 superseded 后缀的历史名称，只有当前检查使用固定门禁名；最后写结果不设置名称，因此旧请求迟到也不能重新占用 B 的门禁名称。历史记录保留与否不影响 B 的判定。

通过前再次完整读取 PR 状态、目标分支、提交、Review 和评论，并清除缓存重新校验回复者权限及 Issue 有效性。数据变化或 API 错误不放行。补查使用 caller 的 Run workflow；重跑旧编号不能覆盖更新的运行编号。取消、超时或 Check 写入失败可能保留当前 in-progress 状态，需发起新检查。该方案是异步门禁，不提供不可删改的历史审计或 merge queue 合成 SHA 支持。

不同分支可以指向同一 commit 并分别创建真实 PR。实测多 PR 共用 head 时 GitHub 门禁关联异常；当前每条开放 PR 应使用独立 head。对照测试中，给其中一条 PR 追加文件内容不变的 commit 后恢复正常；这不是脚本已支持共享 head 的证明。

CLI（会写入指定 PR 的 Check）：

```bash
GH_TOKEN="<token>" python scripts/check_ai_review_disposition.py \
  --repository IS-Model-Framework/example --pr-number 128 \
  --bots 'chatgpt-codex-connector[bot],infiscale-bot'
```

仅支持 GitHub.com。退出码：0 通过、closed PR 跳过或已被更新检查取代，1 未通过，2 参数错误或未捕获的执行错误。全仓复查使用 `--all-open-prs` 替代 `--pr-number`，逐一处理全部开放 PR 并汇总退出码。验证：`python -m unittest discover -s scripts/tests -v`。

正式启用门禁前，在目标仓库验证：新轮开始阻断、完成后恢复；全部 dismissed 时允许有效旧依据兜底；Issue 关闭使 Deferred 失败、更新模板后恢复；B 失败不能被 A 的迟到成功覆盖，B 成功不被旧记录阻塞。还需验证本 Check 通过但线程未 resolved 时，GitHub 原生规则继续阻止合并；Resolve/Unresolve 只更新原生条件，无需重跑本 Check。

测试夹具、隔离 ruleset 和运行步骤见 [隔离验收模板](examples/ai-review-sandbox/README.md)，历史实测及模拟边界见 [验收记录](AI_REVIEW_DISPOSITION_TEST_REPORT.md)。离线模拟使用 `python -m scripts.tests.simulate_ai_review_disposition`，不会访问 GitHub 或冒充真实账号。

参考：[Actions 事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、[Checks API](https://docs.github.com/en/rest/checks/runs)、[复用 workflow](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)。
