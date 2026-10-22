# AI Review / Disposition Check

合并前检查三件事：每个指定 AI 完成过审查；AI 和人工 reviewer 的每条行内线程都有有效模板回复；这些线程全部 resolved。无需采纳所有意见，检查也不代替人工 approval。

## 检查规则

- 每次明确指定一个 PR；closed/merged PR 跳过。Check 写入该 PR 的最新 head commit。
- `AI_REVIEW_BOTS` 是必填的逗号分隔 login 列表，每个账号都必须完成审查。按 login 精确匹配，忽略大小写；GraphQL `Bot` login 缺少 `[bot]` 后缀时补齐，普通 `User` 不补。
- 完成依据是该 Bot 发布的 `COMMENTED`、`APPROVED` 或 `CHANGES_REQUESTED` Review，或下表中的成功结束评论。`PENDING`、`DISMISSED`、开始/失败/配额不足提示不算完成。
- 完成按整个 PR 判断，不绑定当前 head；后续 push 无需重审。新的 pending review 或开始评论不撤销已有完成结果；所有有效 Review 被 dismissed 且无成功评论时重新阻断。
- 检查指定 AI 和 PR 作者之外的人工账号创建的全部已发布 inline threads，包括 outdated 和 resolved threads。PR 作者自己的人工线程、未配置 Bot 的线程、PR 顶层评论和 Review 总评不计为待处理线程；pending review 不计入。配置的 AI 即使是 `User` 或 PR 作者，仍按 AI 处理。
- 模板回复者须为 PR 作者或当前具有仓库写权限的人工账号；Bot 和配置的 AI 服务账号不能自行处理意见。组织成员身份本身不代表写权限。
- 采用编辑时间最新的明确模板回复，同一时间取 thread 中更后的回复；新的无效模板会替代旧模板，普通讨论不会。AI/人工使用相同规则，后续讨论或原始意见编辑不额外要求重复确认；新问题应新开线程。
- 每个线程必须同时满足有效回复和 resolved。仅 resolved、仅回复 “done”，或在 PR 总结中统一回答均不能通过。工具不自动回复或 resolve。

| Bot login | 可替代正式 Review 的成功结束评论 |
| --- | --- |
| `infiscale-bot` | 去除首尾空白后全文为 `未发现本次 PR 引入的新问题。LGTM！` |
| `chatgpt-codex-connector[bot]` | 自带摘要以 `<!-- codex-pull-request-review-summary -->` 开头，且 `Code Review` 行状态为 `✅ **Completed**` |

仅匹配相应 Bot 发布的 PR 顶层评论；人工引用、行内回复、Security Review 完成均不能作为完成依据。Bot 须在全部意见发布完毕后才提交正式 Review 或成功结束评论。

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
| Deferred | `Tracking` 为 GitHub Issue 完整链接；API 确认该 Issue 存在、当前 token 可读、仍开放且不是 PR |

不适用的 `Fix` / `Tracking` 可省略或填 `N/A`。commit 链接仅接受 `https://github.com/owner/repo/commit/<40位SHA>`，Issue 链接仅接受 `https://github.com/owner/repo/issues/<正整数>`，均不接受查询参数或锚点。force-push 移除修复提交后，Accepted 失效。Issue 可跨仓库，但当前 token 必须能读取；目标仓库的 `GITHUB_TOKEN` 无法读取其他私有仓库时需改用具有对应访问权限的调用方式。

工具验证引用有效，不判断理由是否充分、提交是否真正修复问题或延期是否合理；这些由人工审查确认。

## CI 接入

本组织有共享 Python CI 和独立 CI；本检查通过独立 caller 接入，保持现有测试及 `CI Result` 不变。组织 `.github` 仓库的 workflow 不会自动在其他仓库运行。

1. 将 [ai-review-events.yml](examples/ai-review-events.yml) 和 [ai-review-disposition.yml](examples/ai-review-disposition.yml) 复制到目标仓库 `.github/workflows/` 并合入默认分支。
2. 设置组织/仓库 Variable `AI_REVIEW_BOTS`，如 `chatgpt-codex-connector[bot],infiscale-bot`。空值会报错，不提供隐式默认 Bot。
3. 生产接入时，将 caller 中 `uses: ...@main` 和 `shared-ref: main` 同时固定到本公共 CI 仓库的同一个已审核 commit SHA。
4. caller 需要 `contents: read`、`pull-requests: read`、`issues: read`、`checks: write`；不执行或检出 PR 代码。
5. 首次运行后，将脚本发布的 **`AI Review / Disposition Check`** 加入组织/仓库 ruleset 的 Required Status Checks，来源选 GitHub Actions。保留人工 approval 与 conversation resolution 规则。不要选择 Actions job 的成功状态，事件的 `GITHUB_SHA` 不一定是 PR head。

PR opened/reopened/synchronize/ready_for_review 和 PR 顶层评论变化直接触发检查。Review/行内评论变化先运行无权限 collector，再通过默认分支的 `workflow_run` 触发检查器，因此 fork PR 也可复查。collector 的 `run-name` 必须保持为 PR 编号；该值仅用于路由，判定始终重新读取实时 API，不接受 collector 的代码、artifact 或审查结论。

Resolve/Unresolve 没有原生 Actions trigger：resolve 后，在 PR 顶层发一条评论或通过 Actions 手动运行 `AI Review Disposition`，填写 `pr-number`。使用 `GITHUB_TOKEN` 发布的 Review/评论通常不会触发后续 Actions，应由审查流程显式触发复查，或用 App token 发布。

同一 PR 串行检查，不取消正在执行的检查；不同 PR 独立。通过前再完整读取一次数据，提交或评论变化则阻断。API 错误不放行；取消、超时或 Check 写入失败可能留下 in-progress 状态，需重跑。该方案是异步门禁，不提供不可删改的历史审计或 merge queue 合成 SHA 支持。

CLI（会写入指定 PR 的 Check）：

```bash
GH_TOKEN="<token>" python scripts/check_ai_review_disposition.py \
  --repository IS-Model-Framework/example --pr-number 128 \
  --bots 'chatgpt-codex-connector[bot],infiscale-bot'
```

仅支持 GitHub.com。退出码：0 通过或 closed PR 跳过，1 未通过，2 参数错误或未捕获的执行错误。验证：`python -m unittest discover -s scripts/tests -v`；正式启用 required check 前，应在目标仓库试运行 Bot 完成 → 模板回复 → resolve → 复查流程。

参考：[Actions 事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、[Checks API](https://docs.github.com/en/rest/checks/runs)、[复用 workflow](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)。
