# Pre-commit Hooks Guide - IS-Model-Framework Organization

本文档介绍如何在组织的项目中使用 pre-commit hooks。

## 快速开始

### 方法 1: 使用安装脚本（推荐）⭐
```bash
# 在项目根目录运行
curl -sSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/scripts/install-precommit.sh | bash
```

### 方法 2: 手动安装
```bash
# 1. 安装 pre-commit
pip install pre-commit

# 2. 下载组织配置
curl -sSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/.pre-commit-config.yaml \
    -o .pre-commit-config.yaml

# 3. 安装 hooks
pre-commit install
pre-commit install --hook-type commit-msg

# 4. (可选) 在所有文件上运行一次
pre-commit run --all-files
```

## Pre-commit 检查内容

### ✅ 自动修复的检查

这些检查会自动修复问题：

#### 通用文件检查
- **行尾空白**: 移除多余空格
- **文件结尾换行**: 确保文件以换行结束
- **Requirements.txt 排序**: 自动排序依赖文件
- **编码声明**: 移除 Python 文件的 `# -*- coding: utf-8 -*-`

#### Python 代码
- **代码格式化** (Ruff): 统一代码风格，符合 PEP 8
- **Import 排序** (isort + Ruff): 自动整理 import 语句
- **代码质量修复** (Ruff): 自动修复简单的代码问题

#### C/C++ 代码
- **代码格式化** (clang-format): 统一 C/C++ 代码风格

### ⚠️ 需要手动修复的检查

这些检查会报告问题，需要手动修复：

#### 安全检查
- **私钥检测**: 防止提交私钥、密码等敏感信息
- **调试语句检测**: 检测 `pdb`, `ipdb` 等调试语句

#### 代码质量
- **Python AST 语法检查**: 检查 Python 语法错误
- **YAML/JSON/TOML/XML 语法**: 检查配置文件格式
- **大文件检查**: 防止提交超过 1MB 的大文件
- **合并冲突标记**: 检查是否有未解决的合并冲突
- **拼写检查** (codespell): 检查代码和注释中的拼写错误

## 工具说明

### 🔧 Ruff - Python Linter & Formatter

Ruff 是一个极快的 Python linter 和 formatter，替代了多个工具：
- ✅ 替代 `black` (代码格式化)
- ✅ 替代 `flake8` (代码检查)
- ✅ 部分替代 `isort` (import 排序)
- ✅ 替代 `pyupgrade` (语法升级)

配置文件：`configs/ruff.toml`

### 📦 isort - Import 排序

虽然 Ruff 已包含 import 排序功能，但 isort 提供了更细粒度的控制。

如果你的项目只需要基本的 import 排序，可以考虑只使用 Ruff。

### 🔤 codespell - 拼写检查

自动检查代码、注释和文档中的常见拼写错误。

配置：通过 `pyproject.toml` 的 `[tool.codespell]` 部分

### 🔨 clang-format - C/C++ 格式化

如果你的项目包含 C/C++/CUDA 代码，clang-format 会自动格式化这些文件。

## 使用方法

### 日常使用

Pre-commit 会在每次 `git commit` 时自动运行：
```bash
git add .
git commit -m "feat[API]: add new endpoint"
# Pre-commit 自动运行检查
```

### 手动运行
```bash
# 检查所有文件
pre-commit run --all-files

# 只检查暂存的文件
pre-commit run

# 检查特定文件
pre-commit run --files src/main.py src/utils.py

# 运行特定 hook
pre-commit run ruff --all-files
pre-commit run isort --all-files
pre-commit run codespell --all-files
```

### 跳过 Pre-commit（紧急情况）
```bash
# ⚠️ 仅在紧急情况使用
git commit --no-verify -m "emergency fix"

# 或跳过特定的 hook
SKIP=ruff,isort git commit -m "WIP: work in progress"
```

## 项目自定义配置

### 1. 覆盖组织配置

如果项目需要特殊配置，创建项目自己的 `.pre-commit-config.yaml`：
```yaml
# 从组织配置开始，只修改特定规则
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.1
    hooks:
      - id: ruff
        args: 
          - --fix
          - --select=E,F,I  # 项目特定规则
```

### 2. Ruff 配置

在 `pyproject.toml` 中添加项目特定的 Ruff 配置：
```toml
[tool.ruff]
# 项目特定配置
line-length = 120

[tool.ruff.lint]
# 额外忽略某些规则
ignore = ["E501", "F401"]

# 为特定文件设置规则
[tool.ruff.lint.per-file-ignores]
"__init__.py" = ["F401"]
"tests/**/*.py" = ["S101"]
```

### 3. isort 配置

在 `pyproject.toml` 中配置：
```toml
[tool.isort]
profile = "black"
line_length = 100
skip_gitignore = true
known_first_party = ["your_package_name"]
```

### 4. codespell 配置

在 `pyproject.toml` 中配置：
```toml
[tool.codespell]
skip = '*.git,*.svg,*.lock,__pycache__,build,dist'
ignore-words-list = 'cann,som,nd'  # 忽略特定"拼写错误"
```

### 5. 禁用特定文件类型

如果项目不包含 C/C++ 代码，可以移除 clang-format：
```yaml
# .pre-commit-config.yaml
# 注释掉或删除 clang-format 部分
# - repo: https://github.com/pre-commit/mirrors-clang-format
#   rev: v21.1.2
#   hooks:
#     - id: clang-format
```

## CI/CD 集成

在项目的 `.github/workflows/ci.yml` 中添加：
```yaml
jobs:
  pre-commit:
    uses: IS-Model-Framework/.github/.github/workflows/reusable-precommit.yml@main
    with:
      python-version: '3.11'
    secrets:
      ORG_CI_TOKEN: ${{ secrets.ORG_CI_TOKEN }}
```

## 常见问题

### Q: Pre-commit 太慢了怎么办？
```bash
# 使用缓存
pre-commit run --all-files

# 只检查改动的文件
pre-commit run

# 禁用某些慢的 hooks（如 codespell）
SKIP=codespell pre-commit run --all-files
```

### Q: 如何更新 pre-commit hooks？
```bash
# 自动更新到最新版本
pre-commit autoupdate

# 更新后重新安装
pre-commit install
```

### Q: 如何临时禁用某个 hook？

在 `.pre-commit-config.yaml` 中：
```yaml
repos:
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.1
    hooks:
      - id: codespell
        stages: [manual]  # 改为 manual，不会自动运行
```

### Q: Ruff 和 isort 冲突怎么办？

Ruff 已经包含了 import 排序功能。如果冲突，可以：

**选项 1**: 只使用 Ruff（推荐）
```yaml
# 移除 isort，只保留 ruff
```

**选项 2**: 配置 Ruff 不处理 imports
```toml
[tool.ruff.lint]
ignore = ["I"]  # 禁用 ruff 的 isort 功能
```

### Q: codespell 误报怎么办？

在 `pyproject.toml` 中添加忽略词汇：
```toml
[tool.codespell]
ignore-words-list = 'cann,som,nd,yourmistakeword'
```

或在代码中添加注释：
```python
# codespell:ignore yourmistakeword
variable_name = "yourmistakeword"  # codespell:ignore
```

### Q: Pre-commit 与 IDE 格式化冲突？

推荐在 IDE 中也使用相同的工具：

**VS Code** (`settings.json`):
```json
{
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  }
}
```

**PyCharm**:
1. Settings → Tools → External Tools
2. 添加 Ruff 和 isort 作为外部工具

### Q: 如何处理 C/C++ 代码格式化？

clang-format 使用项目根目录的 `.clang-format` 配置文件：
```yaml
# .clang-format
BasedOnStyle: Google
IndentWidth: 4
ColumnLimit: 100
```

## 最佳实践

### 1. 早提交，常提交
Pre-commit 让小的提交更容易通过检查，避免积累大量需要修复的问题。

### 2. 先运行一次完整检查
在新项目设置 pre-commit 后，先运行一次完整检查：
```bash
pre-commit run --all-files
```

### 3. 保持配置更新
定期更新 hooks 版本：
```bash
pre-commit autoupdate
```

### 4. 团队协作
- 确保所有团队成员都安装了 pre-commit
- 在 `CONTRIBUTING.md` 中说明 pre-commit 使用方法
- 定期同步组织级别的配置更新

### 5. 性能优化
- 对于大型项目，考虑只在 CI 中运行某些慢的 hooks
- 使用 `files` 和 `exclude` 参数限制检查范围
- 利用 pre-commit 的缓存机制

### 6. 处理遗留代码
如果在大型遗留项目中引入 pre-commit：
```bash
# 只对新提交的代码运行检查
pre-commit run --from-ref origin/main --to-ref HEAD
```

## 工具链对比

| 工具                 | 用途                 | 是否自动修复 | 速度     |
| -------------------- | -------------------- | ------------ | -------- |
| **Ruff**             | Linting + Formatting | ✅            | ⚡⚡⚡ 极快 |
| **isort**            | Import 排序          | ✅            | ⚡⚡ 快    |
| **codespell**        | 拼写检查             | ✅ (部分)     | ⚡⚡ 快    |
| **clang-format**     | C/C++ 格式化         | ✅            | ⚡⚡ 快    |
| **pre-commit-hooks** | 通用检查             | ✅ (部分)     | ⚡⚡⚡ 极快 |

## 支持的语言和文件类型

- ✅ **Python** (.py)
- ✅ **C/C++** (.c, .cpp, .h, .hpp)
- ✅ **CUDA** (.cu)
- ✅ **YAML** (.yaml, .yml)
- ✅ **JSON** (.json)
- ✅ **TOML** (.toml)
- ✅ **XML** (.xml)
- ✅ **Markdown** (.md)
- ✅ **Requirements files** (requirements.txt)

## 支持

遇到问题？

- 📖 查看 [Pre-commit 官方文档](https://pre-commit.com/)
- 📖 查看 [Ruff 文档](https://docs.astral.sh/ruff/)
- 📖 查看 [isort 文档](https://pycqa.github.io/isort/)
- 💬 在 Organization Discussions 中提问
- 🐛 在 `.github` 仓库提交 Issue

## 相关链接

- [Ruff 文档](https://docs.astral.sh/ruff/)
- [isort 文档](https://pycqa.github.io/isort/)
- [codespell 文档](https://github.com/codespell-project/codespell)
- [clang-format 文档](https://clang.llvm.org/docs/ClangFormat.html)
- [Pre-commit Hooks 列表](https://pre-commit.com/hooks.html)

---

## 附录：完整配置示例

### Python 项目示例
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.1
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pycqa/isort
    rev: 5.13.2
    hooks:
      - id: isort
```

### 混合项目（Python + C++）示例
```yaml
# .pre-commit-config.yaml
repos:
  # Python
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.1
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  # C/C++
  - repo: https://github.com/pre-commit/mirrors-clang-format
    rev: v21.1.2
    hooks:
      - id: clang-format
        types_or: [c++, c]

  # Common
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.1
    hooks:
      - id: codespell
```