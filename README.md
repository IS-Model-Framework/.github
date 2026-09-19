# Organization CI Infrastructure

本仓库为 IS-Model-Framework 组织提供可复用的 GitHub Actions 工作流、检查脚本和 pre-commit 配置，覆盖 Python CI，以及 C/C++/CUDA 的本地格式检查。当前 7 个工作流均通过 `workflow_call` 被项目调用；本仓库未定义发布或部署流程。

## 目录

| 路径 | 内容 |
| --- | --- |
| [.github/workflows/](.github/workflows/) | 完整 Python CI 与 6 个可单独调用的检查工作流 |
| [scripts/](scripts/) | 提交信息、格式、类型、PR 大小检查及本地安装脚本 |
| [configs/.pre-commit-config.yaml](configs/.pre-commit-config.yaml) | 组织共享 hooks |
| [configs/ruff.toml](configs/ruff.toml) | 组织共享 Ruff 规则 |
| [docs/PRECOMMIT_GUIDE.md](docs/PRECOMMIT_GUIDE.md) | pre-commit 使用指南；部分示例尚未同步，实际 hooks 和参数以配置及本 README 为准 |
| [profile/README.md](profile/README.md) | 组织主页介绍，与本仓库的 CI 使用说明分开维护 |

## 项目接入

### 1. 准备本地检查配置

在目标项目根目录运行以下命令。已有配置的项目应合并所需设置，避免覆盖项目规则。

```bash
python -m pip install pre-commit
mkdir -p .github/workflows
curl -fsSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/.pre-commit-config.yaml \
  -o .pre-commit-config.yaml
curl -fsSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/ruff.toml \
  -o .github/workflows/ruff.toml
pre-commit install
pre-commit run --all-files
```

共享 hooks 显式使用 `.github/workflows/ruff.toml`，因此需要保留该路径，或同步修改项目 `.pre-commit-config.yaml` 中两个 Ruff hook 的 `--config` 参数。codespell 显式读取 `pyproject.toml`；项目没有该文件时，需要创建它或移除 hook 中的 `--toml pyproject.toml` 参数。

[安装脚本](scripts/install-precommit.sh) 也提供下载配置、安装 hooks 和可选全量检查的流程；它会保留已有配置，并通过终端询问是否运行检查。共享配置没有提交信息校验 hook，CI 中的提交信息校验由单独的工作流完成。

### 2. 添加 CI 入口

将以下内容保存为项目的 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  ci:
    uses: IS-Model-Framework/.github/.github/workflows/reusable-python-ci.yml@main
    with:
      python-version: '3.11'
      ruff-config-path: '.github/workflows/ruff.toml'
      install-command: 'pip install -e ".[dev]"'
```

项目开发依赖需要包含 `ruff`、`mypy`、`pytest` 和 `pytest-cov`；有相关 C/C++ 文件时，独立格式检查还需要可执行的 `git-clang-format` 及 clang-format。工作流仅执行传入的安装命令，不会单独安装这些检查工具；pre-commit 的 hooks 使用各自环境。

示例中的 `pull-requests: write` 用于默认开启的覆盖率 PR 评论。完整 CI 没有暴露关闭评论的参数；需要关闭时可单独调用测试工作流。工作流会从调用项目所属组织的 `${{ github.repository_owner }}/.github` 拉取共享脚本和配置，因此默认面向组织内部项目。

### 3. 配置合并检查

完整 CI 提供名称为 **CI Result** 的稳定汇总检查，供 ruleset / 分支保护选择为 required check。它汇总 pre-commit、提交信息、格式、类型和测试的结果，并按 `skip-*` 参数接受显式跳过。

PR 大小检查不计入 `CI Result`，也不阻止测试启动。超过大小上限仍会让 PR size job 失败；若需要以此阻止合并，应将该检查单独加入 required checks。

## 工作流与执行关系

| 工作流 | 实际行为 |
| --- | --- |
| [reusable-python-ci.yml](.github/workflows/reusable-python-ci.yml) | 编排全部检查，并输出 `CI Result` |
| [reusable-precommit.yml](.github/workflows/reusable-precommit.yml) | 运行 `pre-commit run --all-files`；失败后追加一次回退检查 |
| [reusable-commit-check.yml](.github/workflows/reusable-commit-check.yml) | 检查 PR head / push after 对应的最新一条提交信息 |
| [reusable-format-check.yml](.github/workflows/reusable-format-check.yml) | 对改动文件运行 Ruff 格式检查和 git-clang-format |
| [reusable-type-check.yml](.github/workflows/reusable-type-check.yml) | 对改动的 Python 文件运行 mypy |
| [reusable-tests.yml](.github/workflows/reusable-tests.yml) | 运行 pytest，生成 JUnit 与覆盖率报告，按参数评论 PR / 上传 Codecov |
| [reusable-pr-size-check.yml](.github/workflows/reusable-pr-size-check.yml) | 仅在 `pull_request` 事件统计新增行数并输出警告或失败 |

完整 CI 的顺序如下：

1. pre-commit 与 PR 大小检查独立启动；pre-commit 不支持跳过。
2. pre-commit 成功后，提交信息、格式、类型检查并行运行，各自可通过参数跳过。
3. 测试等待上述四项：pre-commit 必须成功，其余检查不能失败或取消；显式跳过可接受。
4. `CI Result` 使用 `always()` 汇总必需检查；未显式跳过的检查必须成功。

所有检查 job 均使用 `ubuntu-latest`。完整 CI 的提交信息、格式和类型检查依赖 `pull_request` / `push` 的事件字段，接入示例采用这两种触发方式。

## 完整 Python CI 参数

以下参数均为可选：

| 参数 | 类型 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `python-version` | string | `'3.11'` | 所有子工作流的 Python 版本 |
| `ruff-config-path` | string | `''` | 独立格式检查使用的 Ruff 配置路径 |
| `install-command` | string | `pip install -e ".[dev]"` | 格式、类型及默认测试依赖安装命令 |
| `test-install-command` | string | `''` | 仅覆盖测试安装命令；为空时使用 `install-command` |
| `skip-commit-check` | boolean | `false` | 跳过提交信息检查 |
| `skip-format-check` | boolean | `false` | 跳过独立格式检查；pre-commit 中的 Ruff 仍会运行 |
| `skip-type-check` | boolean | `false` | 跳过 mypy |
| `skip-tests` | boolean | `false` | 跳过 pytest |
| `skip-pr-size-check` | boolean | `false` | 跳过 PR 大小检查 |
| `pr-size-warning-threshold` | number | `300` | 计入的新增行数严格大于该值时警告 |
| `pr-size-block-threshold` | number | `500` | 计入的新增行数严格大于该值时 PR size job 失败 |

完整 CI 使用 `||` 回退默认值，大小阈值传入 `0` 时也会回退到默认值。警告阈值必须小于阻断阈值。

可选 secret `proton_samples_token` 会传给测试工作流，仅在测试依赖安装步骤中为以下组织仓库的 HTTPS Git URL 设置认证：`ProtonSamples`、`PallasKernels`、`ProtonFramework`、`Pallas_training`。它不用于 checkout、格式或类型检查中的依赖安装。需要时在调用 job 中显式传递：

```yaml
    secrets:
      proton_samples_token: ${{ secrets.PROTON_SAMPLES_TOKEN }}
```

## 单独调用检查

可以只接入所需的子工作流，例如全量 pre-commit 与测试：

```yaml
jobs:
  pre-commit:
    uses: IS-Model-Framework/.github/.github/workflows/reusable-precommit.yml@main
    with:
      precommit-config-path: '.pre-commit-config.yaml'

  tests:
    needs: pre-commit
    uses: IS-Model-Framework/.github/.github/workflows/reusable-tests.yml@main
    with:
      install-command: 'pip install -e ".[dev]"'
      upload-coverage: false
      comment-on-pr: false
```

各子工作流均接受 `python-version`（默认 `'3.11'`），其余参数如下：

| 子工作流 | 额外参数及默认值 |
| --- | --- |
| precommit | `precommit-config-path: ''` |
| commit-check | 无 |
| format-check | `ruff-config-path: ''`、`install-command: 'pip install -e ".[dev]"'` |
| type-check | `install-command: 'pip install -e ".[dev]"'` |
| tests | `install-command: 'pip install -e ".[dev]"'`、`upload-coverage: true`、`comment-on-pr: true`；可选 secret `proton_samples_token` |
| pr-size-check | `warning-threshold: 300`、`block-threshold: 500` |

`precommit-config-path`、`upload-coverage` 和 `comment-on-pr` 只在对应子工作流暴露，不能直接传给完整 CI。

## 检查规则与配置

### Pre-commit 与 Ruff

当前共享配置包含：

- 通用 hooks：行尾空白、文件末尾换行、YAML/JSON/TOML/XML、Python AST、调试语句、私钥、合并冲突、requirements 排序，以及 `--maxkb=1000` 大文件检查。
- Ruff `v0.14.1`：`ruff-check --fix` 和 `ruff-format`，匹配 `.py` 文件。
- codespell `v2.4.1`：读取 `pyproject.toml`，额外忽略词 `cann`。
- clang-format `v21.1.2`：匹配 C、C++ 和 CUDA 文件。

共享配置没有 isort hook，Ruff 默认也未启用 `I`（import 排序）规则。Ruff 配置使用 88 字符行宽、2 空格缩进、Python 3.11 目标、双引号和 LF 换行，并启用 preview；lint 规则以 [configs/ruff.toml](configs/ruff.toml) 为准。

pre-commit 配置选择顺序为：有效的 `precommit-config-path` → 项目根目录 `.pre-commit-config.yaml` → 组织配置。指定非空自定义路径时必须确保文件存在，因为此时不会下载组织配置。

未传自定义路径时，工作流还会把组织 Ruff 配置复制到项目根目录 `ruff.toml`；共享 hooks 仍指向 `.github/workflows/ruff.toml`，所以根目录副本不能替代接入步骤中的文件。完整 CI 的 `ruff-config-path` 仅影响独立格式检查，不改变 hooks 参数。

pre-commit 全量检查失败后的回退步骤不会消除之前的失败。遇到 hooks 自动修改文件时，应在本地运行检查、保存修正后重新提交。

### 提交信息

[check_mr_logs.py](scripts/check_mr_logs.py) 仅校验 `--end-rev` 指定的最新提交，接受 `--start-rev` 但不遍历提交范围。它检查组织自定义格式：

- 标题：`<type>[<SCOPE>]: <short-summary>`，type 和 scope 匹配 `\w+`。
- 正文中存在以 `Problem:` 或 `Task:` 开头的行。
- 正文中存在以 `Solution:`、`Test:` 和 `JIRA:` 开头的行。
- `JIRA:` 的值以类似 `PROJ-123` 的编号开头；当前未启用作者邮箱检查。

### 格式与类型检查

独立格式检查通过 [code_format_helper.py](scripts/code_format_helper.py) 对变更 `.py` 文件运行 `ruff format --check --diff --force-exclude`，对脚本支持的 C/C++ 文件运行 `git-clang-format --diff`。CUDA 由共享 pre-commit 的 clang-format hook 覆盖，当前独立格式脚本的扩展名列表不包含 `.cu`。

Ruff 配置首先选择存在的 `ruff-config-path`，否则使用 `.ci-shared/configs/ruff.toml`；如果变更文件列表中出现包含 `ruff.toml` 的路径，工作流会优先采用其中第一个。新分支 push 的 `before` 为全零 SHA 时，当前独立格式步骤不调用检查脚本；全量 pre-commit 仍运行。

[typing_helper.py](scripts/typing_helper.py) 对改动的 `.py` 文件运行 `mypy --ignore-missing-imports`，排除 `test/unittests/lit.cfg.py`。新分支 push 时扫描 Python 文件；无 Python 改动时跳过。仓库没有共享 `mypy.ini`，类型配置由调用项目维护。

### 测试与覆盖率

测试工作流将项目根目录加入 `PYTHONPATH`，执行：

```bash
pytest --cov=. --cov-report=xml:coverage.xml --cov-report=term-missing \
  --junitxml=pytest.xml -v -s --tb=short
```

当前没有自定义测试命令参数，测试发现范围由项目 pytest 配置决定。默认生成 `coverage.xml` 和 `pytest.xml`，在 PR 上评论覆盖率并上传 Codecov；没有 `upload-artifact` 步骤。评论和上传步骤未使用 `always()`，测试失败时不会继续执行它们。

### PR 大小

[check_pr_size.py](scripts/check_pr_size.py) 使用 `git diff --numstat base...head`，统计相对于 merge-base 的新增行数，不以删除行抵消新增行。默认 `301–500` 行警告，`501` 行及以上失败，非 PR 事件不执行统计。

排除范围包括脚本列出的锁文件、`generated` / `__generated__` / `vendor` / `node_modules` / `build` / `dist` / `.venv` 目录、压缩前端文件及 source map、二进制 diff，以及指定静态资源扩展名（包括 SVG、PDF）。完整清单以脚本常量为准。结果写入日志和 Actions job summary，不发布 PR 评论。
