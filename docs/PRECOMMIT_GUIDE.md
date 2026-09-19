# Pre-commit 使用指南

本文介绍 IS-Model-Framework 项目的本地检查与 CI 接入。检查内容以 [共享 hooks](../configs/.pre-commit-config.yaml)、[Ruff 配置](../configs/ruff.toml) 和 [pre-commit 工作流](../.github/workflows/reusable-precommit.yml) 为准；完整 CI 参数见 [README](../README.md)。

## 安装与首次检查

在目标项目根目录、已激活的 Python 开发环境中执行。首次运行需要下载 hook 仓库并创建环境，后续运行会复用缓存。

先安装 pre-commit：

```bash
python -m pip install pre-commit
mkdir -p .github/workflows
```

新项目下载以下两个配置；已有同名文件时，先对照组织配置合并差异，不要直接覆盖：

```bash
curl -fsSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/.pre-commit-config.yaml \
  -o .pre-commit-config.yaml
curl -fsSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/ruff.toml \
  -o .github/workflows/ruff.toml
```

共享 Ruff hooks 显式指定 `.github/workflows/ruff.toml`，仅下载 `.pre-commit-config.yaml` 不足以运行检查。codespell 通过 `--toml pyproject.toml` 读取项目配置；没有该文件的项目可创建以下最小配置，已有文件则合并对应表：

```toml
[tool.codespell]
ignore-words-list = "cann"
```

如果项目不使用 `pyproject.toml`，也可以从 codespell hook 的 `args` 中移除 `--toml` 与 `pyproject.toml`，保留 `['-L', 'cann']`。

安装 Git hook 并进行首次全量检查：

```bash
pre-commit validate-config
pre-commit install
pre-commit run --all-files
```

将 `.pre-commit-config.yaml`、`.github/workflows/ruff.toml` 和所需的 `pyproject.toml` 配置纳入版本控制，使本地与 CI 使用相同规则。每个开发者仍需在自己的 checkout 中执行 `pre-commit install`。

仓库也提供 [install-precommit.sh](../scripts/install-precommit.sh)。当前脚本的 Ruff 下载分支含非 ASCII 空白，可能导致命令执行失败，因此这里使用手动安装步骤。脚本还要求 `.git` 是目录、通过 `/dev/tty` 交互，不能直接用于 Git worktree 或无人值守 CI；可选全量检查失败时也不会让脚本以失败退出。

## 当前检查内容

| 工具与固定版本 | Hook ID | 行为 |
| --- | --- | --- |
| pre-commit-hooks `v6.0.0` | `trailing-whitespace`、`end-of-file-fixer`、`requirements-txt-fixer` | 修复行尾空白、文件末尾换行和 requirements 排序 |
| pre-commit-hooks `v6.0.0` | `check-yaml`、`check-json`、`check-toml`、`check-xml`、`check-ast` | 检查配置与 Python 语法 |
| pre-commit-hooks `v6.0.0` | `check-merge-conflict`、`debug-statements`、`detect-private-key` | 检查合并冲突标记、Python 调试语句与私钥 |
| pre-commit-hooks `v6.0.0` | `check-added-large-files` | 使用 `--maxkb=1000` 限制新增大文件 |
| Ruff `v0.14.1` | `ruff-check` | 对 `.py` 文件执行 lint，使用 `--fix` 自动修复可修复项 |
| Ruff `v0.14.1` | `ruff-format` | 格式化 `.py` 文件 |
| codespell `v2.4.1` | `codespell` | 检查拼写，读取 `pyproject.toml` 并通过 `-L cann` 增加忽略词；当前未开启自动改写 |
| clang-format `v21.1.2` | `clang-format` | 格式化 C、C++ 和 CUDA 文件 |

当前配置没有 isort、编码声明移除、mypy 或提交信息校验 hook。Ruff 的默认 lint 规则未启用 `I`（import 排序）。私钥检查也不等同于覆盖所有密码或凭据的检测。

组织 Ruff 配置使用 88 字符行宽、2 空格缩进、Python 3.11 目标、双引号、LF 换行及 preview 模式。完整规则见 [configs/ruff.toml](../configs/ruff.toml)。

## 日常使用

安装后，Git 提交会自动检查暂存文件。也可以手动指定检查范围：

```bash
# 暂存文件
pre-commit run

# 所有受版本控制的文件
pre-commit run --all-files

# 指定文件（替换为项目中的实际路径）
pre-commit run --files package/module.py tests/test_module.py

# 指定 hook
pre-commit run ruff-check --all-files
pre-commit run ruff-format --all-files
pre-commit run codespell --all-files
pre-commit run clang-format --all-files

# 两个 revision 之间的变更；确保本地已有 origin/main
pre-commit run --from-ref origin/main --to-ref HEAD
```

hooks 自动修改文件时，本次检查通常会报告失败。查看 `git diff`，确认修改符合预期，重新暂存修改的文件，再运行检查。CI 不会替你保存或提交这些修正。

临时排查特定 hook 时，可以使用正确的 hook ID：

```bash
SKIP=codespell pre-commit run --all-files
```

这只影响本次本地运行，不会关闭 CI 中的检查。`--from-ref` 的增量检查也不能代替 CI 的全量检查。

## 项目配置定制

### Ruff 配置路径

默认方式是修改项目的 `.github/workflows/ruff.toml`。例如在现有 `[lint]` 表中加入 `extend-select = ["I"]`，可额外启用 import 排序；不要重复创建同名 TOML 表。

若改用 `pyproject.toml`，需把两个 Ruff hooks 的路径一起修改。以下为替换共享配置中 Ruff repo 条目的片段，保留其他 repo 条目：

```yaml
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.14.1
  hooks:
    - id: ruff-check
      args: [--fix, --config=pyproject.toml]
      files: \.py$
    - id: ruff-format
      args: [--config=pyproject.toml]
      files: \.py$
```

在 `pyproject.toml` 中通过 `extend` 保留组织规则，再增加项目覆盖项：

```toml
[tool.ruff]
extend = ".github/workflows/ruff.toml"
line-length = 100

[tool.ruff.lint]
extend-select = ["I"]
```

此示例仍依赖项目中的 `.github/workflows/ruff.toml`。IDE 格式化也应指向相同配置，并尽量与 hook 固定的 Ruff 版本一致。完整 CI 的独立格式检查需同步设置 `ruff-config-path: 'pyproject.toml'`；该参数不会自动修改 pre-commit hooks。

### codespell 忽略词与文件

在现有 `pyproject.toml` 中合并以下配置：

```toml
[tool.codespell]
skip = "*.svg,*.lock,build,dist"
ignore-words-list = "cann,som,nd"
```

为避免命令行参数与配置文件重复维护忽略词，可将该 hook 的 `args` 改为 `['--toml', 'pyproject.toml']`，统一在 TOML 中保留所需词汇。修改后单独运行 `pre-commit run codespell --all-files` 验证。

### C/C++/CUDA

项目可以在根目录维护 `.clang-format`，例如：

```yaml
BasedOnStyle: Google
IndentWidth: 2
ColumnLimit: 88
```

组织 hook 已声明 `types_or: [c++, c, cuda]`。没有匹配文件时会跳过该 hook；纯 Python 项目也可以从项目配置中移除这个 repo 条目。

## CI 接入

独立接入 pre-commit 时，在项目 `.github/workflows/ci.yml` 使用：

```yaml
name: Pre-commit

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  pre-commit:
    uses: IS-Model-Framework/.github/.github/workflows/reusable-precommit.yml@main
    with:
      python-version: '3.11'
      precommit-config-path: '.pre-commit-config.yaml'
```

该工作流仅接受两个可选输入，不声明 secret，无需传入 `ORG_CI_TOKEN`：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `python-version` | `'3.11'` | 运行 pre-commit 的 Python 版本 |
| `precommit-config-path` | `''` | 相对项目根目录的自定义 hooks 配置路径 |

配置选择顺序是有效的自定义路径、项目根目录 `.pre-commit-config.yaml`、组织共享配置。非空自定义路径会关闭组织配置下载，因此务必保证指定文件存在，不能依赖组织回退。

未传自定义路径时，工作流从 `${{ github.repository_owner }}/.github` 下载组织配置，并在运行前将组织 `ruff.toml` 复制到项目根目录。共享 Ruff hooks 仍读取 `.github/workflows/ruff.toml`，根目录副本不能替代该文件。

工作流缓存 `~/.cache/pre-commit`，先执行 `pre-commit run --all-files`。失败后会尝试回退：PR 事件检查与目标分支之间的变更，其他事件执行普通 `pre-commit run`。回退成功不会消除前面全量步骤的失败。

使用完整 [Python CI](../.github/workflows/reusable-python-ci.yml) 的项目已经包含 pre-commit，无需重复添加上述 job。完整 CI 中 pre-commit 不可跳过，且没有暴露 `precommit-config-path`；项目应在根目录维护 `.pre-commit-config.yaml`。`skip-format-check` 仅跳过独立格式检查，`CI Result` 仍要求 pre-commit 成功。

## 提交信息检查

`pre-commit install` 安装的是提交前检查。共享配置没有提交信息校验 hook，即使安装 `commit-msg` 阶段也不会自动获得组织提交模板校验。

完整 CI 通过 [reusable-commit-check.yml](../.github/workflows/reusable-commit-check.yml) 和 [check_mr_logs.py](../scripts/check_mr_logs.py) 检查最新提交。格式要求包括 `<type>[<SCOPE>]: <short-summary>` 标题、`Problem:` 或 `Task:`、`Solution:`、`Test:`、有效 `JIRA:` 字段，详见 [README](../README.md)。本地 pre-commit 通过不代表提交信息已通过 CI 校验。

## 排查与维护

| 现象 | 处理方式 |
| --- | --- |
| 找不到 `.github/workflows/ruff.toml` | 补齐首次安装步骤中的文件，或同步修改两个 Ruff hooks 的 `--config` |
| codespell 报 TOML 文件不存在 | 添加 `pyproject.toml`，或移除对应 `--toml` 参数 |
| 找不到 `ruff` / `isort` hook | 当前 ID 为 `ruff-check`、`ruff-format`；共享配置没有 isort |
| 本地通过、CI 失败 | 本地运行 `--all-files`，确认配置已提交，并比较 CI 实际选择的配置及工具版本 |
| Ruff 修改风格与 IDE 不一致 | 统一配置路径和 Ruff 版本；显式 `--config` 不会自动改为项目其他配置 |
| hooks 修改了文件 | 检查 diff，重新暂存修正后再运行 |

日常运行复用缓存。环境异常时再清理并重建：

```bash
pre-commit clean
pre-commit install-hooks
pre-commit run --all-files
```

更新项目的 hook 版本时运行：

```bash
pre-commit autoupdate
pre-commit validate-config
pre-commit run --all-files
```

`autoupdate` 修改项目配置中的版本，不会同步组织 `ruff.toml` 或组织 hooks 的新增规则。检查配置 diff 和格式化结果后再提交；组织配置更新也需要明确合并到项目副本中。
