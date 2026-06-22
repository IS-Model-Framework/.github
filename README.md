## Organization CI/CD Infrastructure

组织级别的 CI/CD 工具和配置，为所有 Python 和 C++ 项目提供统一的代码质量标准。

## 🚀 快速开始

### 新项目设置
```bash
# 1. 安装 pre-commit hooks
curl -sSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/scripts/install-precommit.sh | bash

# 2. 在项目中添加 CI workflow
cat > .github/workflows/ci.yml << 'EOF'
name: CI

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

permissions:
  contents: write
  checks: write
  pull-requests: write

jobs:
  ci:
    uses: IS-Model-Framework/.github/.github/workflows/reusable-python-ci.yml@main
    with:
      python-version: '3.11'
      install-command: 'pip install -e ".[dev]"'
EOF

# 3. 提交并推送
git add .github/workflows/ci.yml .pre-commit-config.yaml
git commit -m "chore[CI]: add organization CI/CD workflows"
git push
```

## 📦 组件

### Reusable Workflows

可复用的 GitHub Actions workflows，用于标准化 CI/CD 流程：

| Workflow                    | 功能                | 适用场景               |
| --------------------------- | ------------------- | ---------------------- |
| `reusable-python-ci.yml`    | 完整 Python CI 流程 | 所有 Python 项目       |
| `reusable-precommit.yml`    | Pre-commit 检查     | 所有项目               |
| `reusable-commit-check.yml` | 提交信息检查        | 需要规范提交信息的项目 |
| `reusable-format-check.yml` | 代码格式检查        | Python 项目            |
| `reusable-type-check.yml`   | 类型检查            | 使用类型注解的项目     |
| `reusable-tests.yml`        | 测试运行            | 有测试的项目           |
| `reusable-pr-size-check.yml` | PR 新增行数限制     | 控制 PR review 规模    |

### Pre-commit Configuration

组织标准的 pre-commit hooks，确保代码提交前的质量：

#### 通用检查 ✅
- **文件检查**: 行尾空白、文件结尾换行、大文件检测
- **语法检查**: YAML、JSON、TOML、XML 格式验证
- **安全检查**: 私钥检测、合并冲突标记检查

#### Python 工具 🐍
- **Ruff**: 极速 linter 和 formatter
  - 替代 `black`、`flake8`、部分 `isort` 和 `pyupgrade`
  - 速度比传统工具快 10-100 倍
- **isort**: Import 语句排序和组织
- **codespell**: 代码和注释拼写检查

#### C/C++ 工具 ⚙️
- **clang-format**: C/C++/CUDA 代码格式化

### Shared Scripts

共享的 CI 检查脚本：

| 脚本                    | 功能                                     |
| ----------------------- | ---------------------------------------- |
| `check_mr_logs.py`      | 验证最新提交信息格式（Conventional Commits） |
| `code_format_helper.py` | 代码格式辅助检查                         |
| `check_pr_size.py`      | 检查 PR 新增行数并在过大时 warning/block |
| `typing_helper.py`      | Python 类型注解检查                      |

### Shared Configs

共享的配置文件：

| 配置文件                  | 用途                          |
| ------------------------- | ----------------------------- |
| `ruff.toml`               | Ruff linter 和 formatter 配置 |
| `mypy.ini`                | MyPy 类型检查配置             |
| `.pre-commit-config.yaml` | Pre-commit hooks 配置         |

## 🛠️ 工具说明

### Ruff - 新一代 Python 工具链

Ruff 是一个用 Rust 编写的极速 Python linter 和 formatter：

**替代的工具**:
- ✅ Black (格式化)
- ✅ Flake8 (代码检查)
- ✅ 部分 isort (import 排序)
- ✅ pyupgrade (语法升级)
- ✅ 部分 pylint (代码质量)

**性能对比**:
```
Ruff:     0.3s  ⚡⚡⚡
Black:    5.2s  
Flake8:   12.1s
pylint:   45.3s
```

**为什么还保留 isort？**
- isort 提供更细粒度的 import 分组控制
- 对于复杂的 import 结构，isort 更灵活
- 如果项目只需要基本排序，可以移除 isort，只用 Ruff

### codespell - 拼写检查

自动检测代码、注释和文档中的常见拼写错误。

**特点**:
- ✅ 支持多种编程语言
- ✅ 可自定义忽略词汇
- ✅ 快速，不影响开发体验

### clang-format - C/C++ 格式化

如果项目包含 C/C++/CUDA 代码，自动格式化以保持风格一致。

## 📖 文档

- [Pre-commit 使用指南](docs/PRECOMMIT_GUIDE.md) - 详细的 pre-commit 配置和使用说明
- [贡献指南](docs/CONTRIBUTING.md) - 如何为组织项目贡献代码

## 💡 使用示例

### Python 项目
```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [ main ]
  pull_request:

permissions:
  contents: write
  checks: write
  pull-requests: write

jobs:
  ci:
    uses: IS-Model-Framework/.github/.github/workflows/reusable-python-ci.yml@main
    with:
      python-version: '3.11'
      install-command: 'pip install -e ".[dev]"'
```

### 混合项目（Python + C++）

Pre-commit 会自动检测文件类型并应用相应的工具：
- `.py` 文件 → Ruff + isort + codespell
- `.cpp`, `.h` 文件 → clang-format + codespell
- 配置文件 → 语法检查

### 自定义配置

如果项目需要特殊配置，创建 `pyproject.toml`:
```toml
[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N"]
ignore = ["E501"]

[tool.isort]
profile = "black"
line_length = 120

[tool.codespell]
skip = '*.svg,*.lock'
ignore-words-list = 'cann,som'
```

## 🎓 最佳实践

### 1. 在本地运行完整检查
在提交前运行完整检查，避免 CI 失败：
```bash
pre-commit run --all-files
```

### 2. 保持工具更新
定期更新 pre-commit hooks：
```bash
pre-commit autoupdate
```

### 3. 团队规范
- 所有团队成员安装 pre-commit
- 使用相同的 IDE 配置（Ruff、isort）
- 定期同步组织配置更新

### 4. 性能优化
对于大型项目：
- 使用 `files` 和 `exclude` 限制检查范围
- 在 CI 中运行耗时较长的检查
- 利用 pre-commit 缓存

## 🔧 故障排除

### Pre-commit 失败
```bash
# 跳过特定 hook
SKIP=codespell git commit -m "message"

# 完全跳过（紧急情况）
git commit --no-verify -m "emergency fix"
```

### 更新冲突
```bash
# 重新安装 hooks
pre-commit clean
pre-commit install
```

### 工具冲突

如果 Ruff 和 isort 冲突：
1. 优先使用 Ruff 的配置
2. 或在 `pyproject.toml` 中禁用 Ruff 的 import 检查：
```toml
[tool.ruff.lint]
ignore = ["I"]
```

## 📊 支持的项目类型

| 项目类型         | 支持的工具                        |
| ---------------- | --------------------------------- |
| **纯 Python**    | Ruff, isort, codespell, 通用检查  |
| **Python + C++** | 以上 + clang-format               |
| **纯 C++**       | clang-format, codespell, 通用检查 |
| **混合项目**     | 所有工具（自动检测文件类型）      |
