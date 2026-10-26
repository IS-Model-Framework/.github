# 隔离验收模板

模板只用于专用测试仓库，不会在公共 CI 仓库自动启用。历史实测和模拟范围见 [验收记录](../../AI_REVIEW_DISPOSITION_TEST_REPORT.md)。生产 caller 仍默认使用 `main`。

## 准备

1. 将上级目录的 `ai-review-disposition.yml`、`ai-review-events.yml` 复制到测试仓库 `.github/workflows/` 并提交默认分支。测试未合入 main 的候选时，把 caller 两处 workflow `uses` 和两处 `shared-ref` 同时改为 `feat/Ai-dispose`，或同一个候选 SHA。
2. 将本目录三个 `.yml` 也复制到测试仓库 `.github/workflows/`。设置 Variable `AI_REVIEW_SANDBOX=true`；实验脚本的 `AI_REVIEW_TEST_REF` 默认 `feat/Ai-dispose`，指定提交时应与 caller 使用相同 SHA。
3. 创建目标分支 `test/ai-review-merge-target`。测试 PR 来源分支以 `test/ai-review-e2e-` 开头，每条 PR 使用独立 head。
4. 在测试 PR 新增 `.ai-review-sandbox/sample.py`，第 5 行须在 diff 中：

```python
"""Disposable fixture; workflows never execute this PR file."""


def sample():
    value = 1
    return value
```

设置 `AI_REVIEW_BOTS=github-actions[bot]`，运行 **AI Review Sandbox Fixture** 的 `create-review`，填写 PR 编号和 `with-finding=true`。这是真实 GitHub Actions 身份，评论明确标注 fixture，不冒充 Infiscale / Codex。其 Review 使用 `GITHUB_TOKEN`，需要手动运行正式 caller 检查该 PR；随后作者新增、编辑、删除模板回复将走真实事件链自动复查。

`reply` 用于验证 Bot 不能自行处理意见；`edit-root` 用于验证原意见编辑无需重复确认。`target-comment-id` 只接受该 fixture Bot 在所选 PR 上自己发布的根评论。

## 测试规则

先在健康 PR 上产生一次成功的正式 Check 和 B-success 实验 Check，再应用 `ruleset.json`：

```bash
gh api repos/OWNER/TEST_REPO/rulesets --method POST \
  --input docs/examples/ai-review-sandbox/ruleset.json
```

JSON 仅约束 `test/ai-review-merge-target`，没有 bypass，要求正式 Check、A/B 实验 Check 和会话解决。它是服务器规则的配置模板，提交 Git 文件不会自动启用。不要将实验 Check 加入生产门禁，或把测试规则用于业务 main。

## 真实 API 实验

| 工作流 | 输入与用途 | 预期 |
| --- | --- | --- |
| AI Review Latest Result Lab | PR；B-success / B-failure / B-cancelled；暂停 0–60 秒 | B 处理中、失败、取消均阻止合并；A 的迟到结果不能覆盖 B |
| AI Review Final Read Lab | PR；开放 Issue；fixture 根评论 ID；issue-close / review-edit | 首次读取后真实修改数据，最后复核拒绝旧成功 |

最终复核实验要求人工 Deferred 指向该 Issue，Issue 标题以 `[Sandbox disposable]` 开头。实验只编辑 fixture Bot 自己的意见或关闭测试 Issue；结束后 dispatch 正式 caller 全仓复查。

实验 job 绿色表示断言成立，其独立实验 Check 可能按预期红色或取消。最终复核实验的 Check 不是 Required Check。A/B 实验还需核对真实合并区，不能只看 API 断言。

## 自动关 Issue

将上级目录 `ai-review-close-issue.yml` 复制到默认分支。让 Deferred 引用开放 Issue，再运行 **Close Issue and Recheck AI Dispositions**。应出现新的正式 caller `workflow_dispatch`，逐个检查开放 PR，使该 Deferred 失败。

其他使用 `GITHUB_TOKEN` 修改 Issue 的工作流也须在修改后触发同一 caller；负责 dispatch 的 job 需要 `actions: write`，关闭 Issue 需要 `issues: write`：

```bash
gh workflow run ai-review-disposition.yml --repo "$GITHUB_REPOSITORY" \
  --ref "$DEFAULT_BRANCH" -f all-open-prs=true
```

无需额外 Token 或 Issue 到 PR 的反向关联。不要由另一工作流直接运行正式检查脚本或另建 caller：新旧结果按同一 caller 的运行编号排序。

## 离线模拟与范围

```bash
python -m unittest discover -s scripts/tests -v
python -m scripts.tests.simulate_ai_review_disposition
```

模拟使用合成的 AI/人工身份和权限响应，运行实际判定与末次复核逻辑，不需要真实账号或密钥。它能证明规则行为，不能证明 App 已接入或真实授权已生效。

本次范围为同仓库 PR / Issue，fork PR、Issue 转移不作为验收前提；跨仓库链接拒绝校验保留。结束后恢复真实 `AI_REVIEW_BOTS` 并重新检查，也可设 `AI_REVIEW_SANDBOX=false` 停用夹具。
