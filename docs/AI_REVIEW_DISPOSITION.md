# AI Review / Disposition Check

通过条件：每个指定 AI 完成审查，且应处理的行内线程都有有效回复。GitHub 原生 **Require conversation resolution before merging** 负责要求线程 resolved；人工 approval 单独保留。检查器不执行 PR 代码，也不自动回复或 resolve。

## 审查与回复规则

- `AI_REVIEW_BOTS` 必填，逗号分隔精确 login，忽略大小写；每个账号分别判定。GraphQL Bot login 缺少 `[bot]` 时补齐。
- 已发布的 `COMMENTED`、`APPROVED`、`CHANGES_REQUESTED` Review 或下表成功摘要可证明完成。新开始消息、更新的 pending / 进行中状态使旧结果不能提前放行。同一 AI 的轮次不得重叠；新 commit 本身不强制重审。
- 本轮全部 Review 被 dismissed 后，本轮失效，可用更早有效 Review 或成功摘要兜底；没有依据则阻断。随后新一轮仍须重新完成。
- 指定 AI、PR 作者之外的人工 reviewer 创建的 inline threads 均须处理，包括 outdated / resolved。顶层评论、Review 总评、未发布的 pending 意见、作者自己的人工线程和未配置 Bot 的线程不作为处理目标。
- 回复须来自 PR 作者或当前有 write/admin 权限的人工账号；AI 账号和 Bot 不能自行处理。采用编辑时间最新的明确模板，同一时间取更后的回复；新无效模板会替代旧模板。普通讨论、原意见编辑不要求重复确认，新问题应另开线程。

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

不适用的 Fix / Tracking 可省略或填 `N/A`。只接受 `https://github.com/owner/repo/commit/<SHA>`、`https://github.com/owner/repo/issues/<正整数>`，不接受查询参数、锚点或跨仓库链接。转移重定向不能绕过仓库限制。force-push 移除修复提交、Issue 关闭或删除均使相应回复失效；作者需更新为有效回复。检查器不判断理由是否充分、commit 是否真正修复或延期是否合理。

## 接入

1. 将 [caller](examples/ai-review-disposition.yml) 和 [事件收集器](examples/ai-review-events.yml) 复制到目标仓库 `.github/workflows/`，合入默认分支。
2. 设置 Variable `AI_REVIEW_BOTS`，例如 `chatgpt-codex-connector[bot],infiscale-bot`。caller 需要 `contents: read`、`pull-requests: read`、`issues: read`、`checks: write`。
3. 首次运行后，将脚本发布的 **AI Review / Disposition Check** 设为 Required Status Check，来源 GitHub Actions；不要选择 Actions job 的状态，其 SHA 不一定是 PR head。
4. 同时启用 **Require conversation resolution before merging** 并保留人工 approval。Resolve/Unresolve 由 GitHub 原生规则处理，不需额外运行本检查。

PR opened/reopened/synchronize、目标分支及 PR 顶层评论变化触发单 PR 检查；不监听 ready_for_review。Review submitted/dismissed、行内评论 created/edited/deleted 先运行无权限收集器，再由默认分支的 workflow_run 启动可信检查器。收集器的 run-name 保持 PR 编号，不传入代码或审查结论。

本仓库 Issue closed/reopened/deleted/transferred 时复查全部开放 PR，不维护 Issue → PR 反向关联；一个 PR 失败不会跳过后续 PR。新建普通 Issue 不触发。

### 自动化修改 Issue

GitHub 不会因 `GITHUB_TOKEN` 修改 Issue 自动启动后续 Actions。使用 [关闭 Issue 并复查的示例](examples/ai-review-close-issue.yml)，或在已有自动化修改后 dispatch **同一份 caller**：

```bash
gh workflow run ai-review-disposition.yml --repo "$GITHUB_REPOSITORY" \
  --ref "$DEFAULT_BRANCH" -f all-open-prs=true
```

DEFAULT_BRANCH 为仓库默认分支，负责 dispatch 的 job 需要 `actions: write`，关闭 Issue 另需 `issues: write`。无需额外 Token；其他自动化发布 Review / 评论后同样须主动复查。不要由另一工作流直接运行正式检查脚本，结果顺序依赖同一 caller 的运行编号。

手动检查填写正整数 pr-number；全仓复查勾选 all-open-prs、保持编号 0。默认不会隐式全仓扫描。CI 始终实时读取 PR 并将 Check 写入最新 head；通过前再次读取数据，清缓存复核权限和 Issue 状态。数据变化或 API 错误不放行，Issue 的 404/410 会给出具体 Tracking 提示。

同一 PR 的新运行取消旧运行；全仓复查有独立并发组。B 接管后只认 B，运行中、失败或取消均不回退 A 的成功；旧 Check 改名保留，迟到结果不能恢复旧门禁名称。补查使用 Run workflow，重跑旧编号不能覆盖更新编号；读取/发布失败或遗留 in-progress 时发起新检查。

## 验证与边界

运行 `python -m unittest discover -s scripts/tests -v`。主测试包含 AI 轮次、第二人工账号权限/撤权、分页、最终复核和 A/B 迟到写入模拟，无需真实账号或 Token。真实 Bot 接入仍须现场验证；不能把模拟当成实际授权已生效。

自动关 Issue 的 [触发工作流](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40306714886) 和 [后续复查](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40306733764) 已在隔离仓库实测。一次性实验、规则配置和历史结果保留在[测试仓库](https://github.com/liaoxiang0304/VR_DexCopilot)，不作为生产接入文件。

仅支持 GitHub.com、同仓库 PR/Issue；fork PR、Issue 转移不作为本次验收前提。多 PR 共用同一 head 存在 GitHub 门禁关联限制，各 PR 应使用独立 head。不支持 merge queue 合成 SHA 或不可删改的历史审计。
