# AI Review 验收与模拟记录

本次变更迁移了隔离测试材料，并修正手动编号、删除 Issue 的 HTTP 410 提示；新增自动关闭 Issue 后触发原 caller 的接入示例。

验证分为两类：离线模拟使用虚拟 Infiscale / Codex 与第二个人工账号，真实 API 实验使用明确标注的 GitHub Actions 测试审查者。两类都不冒充真实 Bot 已接入或真实第二账号已授权。

当前范围是同仓库 PR 与 Issue。fork PR、Issue 转移按用户使用范围排除，不作为本次接入前提。共享 head 多 PR 在真实 GitHub 中可以出现，现有实现的限制仍保留；使用独立 head 的 PR。

## 本次可复现检查

```bash
python -m unittest discover -s scripts/tests -v
python -m scripts.tests.simulate_ai_review_disposition
```

模拟涵盖：Infiscale 新轮、发布意见、零问题、dismissed 历史兜底；Codex 进行中/失败/完成；第二人工账号 write/admin/read/none；最终复核期间撤权；Bot 不得自行处理；Issue 关闭、删除和检查期间变更。模拟通过说明判定逻辑符合这些输入，不能证明真实 App 回调与账号授权。

测试模板及规则见 [隔离验收说明](examples/ai-review-sandbox/README.md)。下面保留对旧提交 `be25a22a1c39b9c64fe8931e858e29829be1ada5` 的历史记录；其中“本轮未修复”等措辞描述当时状态，本次新结果另行追加。

## 原候选版本的历史验收记录

日期：2026-10-26。仓库：[liaoxiang0304/VR_DexCopilot](https://github.com/liaoxiang0304/VR_DexCopilot)。

**结论：已经完成真实 Actions / Checks API / 合并门禁测试，但尚未全部验收通过，暂不建议直接推广。**

共享脚本与 reusable workflow 均固定到候选提交 `be25a22a1c39b9c64fe8931e858e29829be1ada5`，无需合入公共 CI main。模拟审查者为真实的 `github-actions[bot]`，其评论均明确标注 SANDBOX FIXTURE；本次不声称 Infiscale / Codex 实际完成审查。

## 已验证的场景

| 场景 | 实际结果与证据 |
| --- | --- |
| 没有 AI 完成记录 | 创建 PR 自动失败，不能因零线程放行：[初次运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40285069563) |
| 作者行内回复自动复查 | collector → workflow_run 成功：[自动运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40285663160) |
| 手动触发 | 原版失败；测试仓库转换输入为数字后成功：[修正后](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40285803976) |
| 普通后续讨论 | 会复查，但沿用有效完成记录和模板：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286031460) |
| 有效 Deferred / Issue 关闭 | 关闭后自动全仓复查，PR #3 失败、PR #4 仍被检查且通过；实际合并被拒绝：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286270592) |
| Issue 重开 | 未编辑 Deferred 也恢复通过：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286378424)；该 sweep 的总状态失败是因为已有 PR #1 无完成记录，不能只看 workflow 总颜色 |
| Accepted 缺 Fix / Fix 被移出 PR | 均失败：[缺 Fix](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286513076)、[force-push 移除](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286847889)；有效 Fix 在运行 15 曾通过 |
| resolved 且 outdated 线程 | 实际 GraphQL 状态均为 true；仍要求模板。删除唯一回复后失败，重新回复后通过：[删除](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40287573105)、[恢复](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40287616423) |
| B 处理中，A 曾成功 | 实验固定 Check 仍为 in_progress，实际合并返回 HTTP 405：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40285932263) |
| B 失败，A 迟到写成功 | 保留 B 的失败，实际合并返回 HTTP 405：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40285932263) |
| B 取消，A 迟到写成功 | 保留 B 的取消，实际合并返回 HTTP 405：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286136866) |
| B 成功，A 迟到写失败 | 保留 B 成功：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40286025437)；真实 [PR #4](https://github.com/liaoxiang0304/VR_DexCopilot/pull/4) 在两个 Required Checks 通过后合并成功 |

A/B 实验通过待测 `current_check()` 对真实 Checks API 制造迟到写入，并用独立 Check 名 `AI Review / Disposition Integration Test` 验证。它验证 Check 改名、接管和真实合并阻断；不等同于覆盖 GitHub 全部调度竞态。

## 不依赖真实 AI 的补测

用户选择先完成其余测试，真实 Bot 验收标为待完成。下列均使用明确标注的 GitHub Actions 测试审查者，不冒充 Infiscale / Codex。

| 场景 | 实测结果 |
| --- | --- |
| Deferred 链接实际指向本仓库 PR | 拒绝通过：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40289406119) |
| Deferred 链接指向本仓库不存在的 Issue | 拒绝通过：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40289455526) |
| Deferred 链接指向本仓库已关闭 Issue | 拒绝通过：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40289512716) |
| Bot 用有效模板回复自己的意见，作者未回复 | 仍为 Unaddressed：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40289851068) |
| 编辑 Bot 原始意见，人工旧回复仍有效 | 无需重复确认：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40289746270) |
| 删除一次性 Issue | 自动触发全仓复查并失败：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40289979556)；API 返回 HTTP 410，目前提示为通用 HTTPError |
| 首次验证 Issue 开放后立刻关闭它 | 最后复核重新读取，拒绝旧成功；真实 API 共读取 Issue 两次：[实验](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40290499879) |
| 首次评估后立刻编辑原意见 | 第二次 PR 快照检测到变化并拒绝旧成功；重新检查后通过：[实验](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40290629552) |
| 修改目标分支，再改回 | 两次均自动复查，无需强制 AI 重审：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40291961436) |
| PR 关闭后重开 | 自动复查：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40292010661) |
| 只改 PR 标题 | workflow 跳过评估，Check ID 保持不变：[运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40292060503) |

最终复核实验使用同一个候选脚本，API 包装只在精确时间点对测试 Issue / Bot 自己的意见进行真实写入，既不伪造读取结果，也不修改候选评估逻辑。独立 Check `AI Review / Final Read Integration Test` 按预期失败，实验 job 因断言正确而成功；它不是 Required Check。

## 发现的问题

### 1. 手动 PR 编号类型错误，已仅在测试仓库修正

[原始失败运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40285281539) 的注释为：`Unexpected value '3'`。PR 编号进入 reusable workflow 时仍为字符串，脚本根本没启动。

需要在公共 CI 的 `docs/examples/ai-review-disposition.yml` 同步这一行；公共 PR #24 本次尚未修改：

```diff
-            || inputs.pr-number
+            || fromJSON(inputs.pr-number || '0')
```

[测试仓库修复提交](https://github.com/liaoxiang0304/VR_DexCopilot/commit/9075f49c568aa79e98c2be6cad76bee669103535) 已实测成功。交付的本地测试包也已同步该修正，因此它含有这处尚未进入公共候选的 caller 修正。

### 2. 共享 head 多 PR 的关联限制已复现，尚未解决

原实测 [PR #6](https://github.com/liaoxiang0304/VR_DexCopilot/pull/6) 和 [PR #7](https://github.com/liaoxiang0304/VR_DexCopilot/pull/7) 共用 head `2db00048253c38e7090765bec363f07632ec1cdf`。只有 #6 有测试审查：脚本分别给出成功 / 失败，但两条 PR 的实际合并均被 GitHub 拒绝，提示 `2 of 2 required status checks are expected`。

补测给 #6 追加一个 **tree 完全不变** 的独立提交 `e5315a94450879e073ca7cc097d5fb9a63bfb861`。[重新运行实验](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40291018228) 后，浏览器确认两个 Required Checks 成功、合并按钮恢复可用。没有执行 #6 的合并。

因此独立 head 的对照恢复正常；共享 head 场景仍未支持，不能标为通过。当前 #6 已改为独立 head，原同 head 状态及 API 拒绝记录保存在测试证据中。

### 3. 自动化使用 GITHUB_TOKEN 关闭 Issue，不会自动触发 caller

实测 [工作流关闭 Issue 的实验](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40290499879) 后，正式 Check ID 仍为 `121464063640`，保持原成功状态；独立复核实验已能检测到关闭并失败。[主动触发正式检查](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40290556150) 后，正式 Check 才失败。

这是 [GitHub 的事件抑制规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) 的实际影响。人工关闭 Issue 的自动复查已通过；现方案不能宣称覆盖所有自动化关闭来源。负责关闭 Issue 的 Actions 工作流需要主动调用全仓复查，例如 reusable workflow 的 `all-open-prs: true`；不需要 Issue 反查 PR。该接入补充本轮未写入公共 CI。

### 4. 删除 Issue 已阻断，错误提示仍可改进

一次性 [Issue #8](https://github.com/liaoxiang0304/VR_DexCopilot/issues/8) 确实被删除，GitHub REST 返回 HTTP 410。现脚本把它作为 API 错误保守阻断，未误放行；但用户只看到 `evaluation failed (HTTPError)`。可考虑把 410 与 404 一样归为无效 Tracking，从而给出清晰的处理提示。本轮未改候选脚本。

## 尚未验收与范围限制

- **真实 Bot 验收：待完成（按用户选择暂缓）**。包括实际开始/完成格式、零问题、新轮和 dismissed 历史兜底。
- **需要第二个人工账号**：非作者 write/admin 回复、无权限人工回复、权限被撤回、最终权限复核竞态。Bot 回复被拒已验证，不能替代这些人工权限测试。尝试将 Bot 创建的测试 Issue 转成 PR 后，PR 作者仍是当前用户，因此没有将该尝试计为通过。
- **需要另一授权测试仓库**：真实 fork PR、Issue 转移。当前授权仅限 VR_DexCopilot，未创建或写入其他仓库。
- 外仓库链接的实测被自动审批拒绝，理由是此前限定只考虑本仓库；已移除该测试，未在评论里发布该外仓库链接。
- 受控的 Issue 与意见变更竞态已实测；大规模分页及其余单元测试边界不代表都已现场执行。

## 当前现场

- [接入 PR #2](https://github.com/liaoxiang0304/VR_DexCopilot/pull/2) 和 [正常合并对照 PR #4](https://github.com/liaoxiang0304/VR_DexCopilot/pull/4) 已合并。
- 一次性权限测试 [PR #10](https://github.com/liaoxiang0304/VR_DexCopilot/pull/10) 已关闭且未合并；[Issue #8](https://github.com/liaoxiang0304/VR_DexCopilot/issues/8) 为删除事件测试对象，已删除；[Issue #9](https://github.com/liaoxiang0304/VR_DexCopilot/issues/9) 已关闭。
- [PR #3](https://github.com/liaoxiang0304/VR_DexCopilot/pull/3) 保留用于后续真实 Bot 验收；[Issue #5](https://github.com/liaoxiang0304/VR_DexCopilot/issues/5) 已关闭。
- `AI_REVIEW_BOTS` 已从测试身份恢复为 `chatgpt-codex-connector[bot],infiscale-bot`，并通过 Issue 关闭事件重新检查开放 PR。真实 Bot 尚未完成时当前红色是预期行为。最新恢复复查为 [运行](https://github.com/liaoxiang0304/VR_DexCopilot/actions/runs/40292442890)。
- `AI_REVIEW_SANDBOX=true` 保留，方便继续手动 A/B 实验；fixture / lab 都只用于本测试仓库。
- [规则集](https://github.com/liaoxiang0304/VR_DexCopilot/rules/25862703) 仅约束 `test/ai-review-merge-target`，没有 bypass actor；同时要求正式 Check、实验 Check 和解决会话。`main` 未加入这些门禁。
- 只新增测试工作流、验收文档和专用分支测试文件。原 `README.md`、`flexiv_team14.py` 的 blob SHA 均与测试前一致；既有 PR #1 的内容未改动，但全仓复查会给它增加检查结果。

验收依据为具体 Check 和实际合并 API 返回，不能把预期失败的 workflow 总颜色当成验收失败，也不能用实验 job 的绿色代替正式 Check。

## 参考

- [GitHub：Ruleset 参数与检查来源](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset)
- [GitHub：GITHUB_TOKEN 产生事件的触发限制](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

补测不修改公共 CI PR #24；本次新增的 fixture / final-read workflow 只在测试仓库使用。
