#!/bin/bash
# Quick installation script for organization pre-commit hooks

set -e

echo "🔧 Installing Pre-commit Hooks for IS-Model-Framework Organization"
echo "=================================================="

# Check if in a git repository
if [ ! -d .git ]; then
    echo "❌ Error: Not in a git repository"
    exit 1
fi

# Install pre-commit if not already installed
if ! command -v pre-commit &> /dev/null; then
    echo "📦 Installing pre-commit..."
    pip install pre-commit
fi

# Download organization pre-commit config if not exists
if [ ! -f .pre-commit-config.yaml ]; then
    echo "📥 Downloading organization pre-commit config..."
    curl -sSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/.pre-commit-config.yaml \
        -o .pre-commit-config.yaml
else
    echo "ℹ️  Using existing .pre-commit-config.yaml"
fi

# Define ruff.toml target path
RUFF_CONFIG_PATH=".github/workflows/ruff.toml"

# Check if ruff.toml already exists
if [ ! -f "$RUFF_CONFIG_PATH" ]; then
    echo "📥 Downloading organization ruff config to $RUFF_CONFIG_PATH..."
    
    # Ensure target directory exists (.github/workflows/)
    mkdir -p "$(dirname "$RUFF_CONFIG_PATH")"

    # Download ruff.toml to the target path
    curl -sSL https://raw.githubusercontent.com/IS-Model-Framework/.github/main/configs/ruff.toml \
        -o "$RUFF_CONFIG_PATH"
fi

# Install pre-commit hooks
echo "🎣 Installing pre-commit hooks..."
pre-commit install
pre-commit install --hook-type commit-msg

# Optional: Run on all files
read -p "🔍 Run pre-commit on all files now? (y/N) " -n 1 -r < /dev/tty
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🚀 Running pre-commit on all files..."
    pre-commit run --all-files || true
fi

echo ""
echo "✅ Pre-commit hooks installed successfully!"
echo ""
echo "📝 Next steps:"
echo "   1. Commit your changes"
echo "   2. Pre-commit will automatically check your code before each commit"
echo "   3. To run manually: pre-commit run --all-files"
echo ""
# echo "🔗 Documentation: https://github.com/IS-Model-Framework/.github/blob/main/docs/PRECOMMIT_GUIDE.md"
