#!/bin/bash
# Quick setup for 'jobhunt' command

echo "Setting up 'jobhunt' command..."
echo ""

# Detect shell
if [ -n "$ZSH_VERSION" ]; then
    SHELL_RC="$HOME/.zshrc"
    SHELL_NAME="zsh"
elif [ -n "$BASH_VERSION" ]; then
    SHELL_RC="$HOME/.bashrc"
    SHELL_NAME="bash"
else
    echo "⚠️  Unknown shell. Add this manually to your shell config:"
    echo "alias jobhunt='cd \"$PWD\" && ./hunt.sh'"
    exit 1
fi

# Create alias
ALIAS_CMD="alias jobhunt='cd \"$PWD\" && ./hunt.sh'"

# Check if alias exists
if grep -q "alias jobhunt=" "$SHELL_RC" 2>/dev/null; then
    echo "✅ Alias already exists in $SHELL_RC"
else
    echo "" >> "$SHELL_RC"
    echo "# Crypto Job Hunter" >> "$SHELL_RC"
    echo "$ALIAS_CMD" >> "$SHELL_RC"
    echo "✅ Added to $SHELL_RC"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "To use:"
echo "  1. Reload your shell: source $SHELL_RC"
echo "  2. Run from anywhere: jobhunt"
echo ""
echo "Or run directly: ./hunt.sh"