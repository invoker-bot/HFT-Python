#!/bin/bash
# Codex CLI 一键安装脚本 (Linux/macOS)
# https://api5.ai

# 设置颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# 输出函数
print_color() {
    echo -e "${1}${2}${NC}"
}

# 错误处理
set -e
trap 'last_command=$current_command; current_command=$BASH_COMMAND' DEBUG
trap 'echo -e "${RED}错误: 命令 \"${last_command}\" 在第 ${LINENO} 行失败${NC}"' ERR

# 显示欢迎信息
clear
print_color "$CYAN" "================================================"
print_color "$CYAN" "         Codex CLI 一键安装程序"
print_color "$CYAN" "         支持最新 GPT-5-CODEX 模型"
print_color "$CYAN" "================================================"
echo ""

# 输入 API KEY
API_KEY=""
while [ -z "$API_KEY" ]; do
    print_color "$YELLOW" "请输入您的 API KEY："
    read -r API_KEY
    if [ -z "$API_KEY" ]; then
        print_color "$RED" "API KEY 不能为空，请重新输入！"
    fi
done

print_color "$GREEN" "✓ API KEY 已接收"
echo ""

# 检测操作系统
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        # 检测 Linux 发行版
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            DISTRO=$ID
        elif [ -f /etc/debian_version ]; then
            DISTRO="debian"
        elif [ -f /etc/redhat-release ]; then
            DISTRO="rhel"
        else
            DISTRO="unknown"
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        DISTRO="macos"
    else
        OS="unknown"
        DISTRO="unknown"
    fi

    print_color "$GREEN" "✓ 检测到操作系统: $OS ($DISTRO)"
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检查 Node.js 是否已安装
check_node() {
    if command_exists node; then
        NODE_VERSION=$(node --version 2>/dev/null)
        print_color "$GREEN" "✓ 检测到 Node.js 已安装: $NODE_VERSION"
        return 0
    else
        print_color "$YELLOW" "未检测到 Node.js"
        return 1
    fi
}

# 安装 Node.js - macOS
install_node_macos() {
    print_color "$YELLOW" "正在为 macOS 安装 Node.js..."

    # 检查是否有 Homebrew
    if command_exists brew; then
        print_color "$YELLOW" "使用 Homebrew 安装 Node.js..."
        brew install node
    else
        # 安装 Homebrew
        print_color "$YELLOW" "未检测到 Homebrew，正在安装..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # 配置 Homebrew 环境变量
        if [[ -d "/opt/homebrew" ]]; then
            echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi

        # 安装 Node.js
        brew install node
    fi

    print_color "$GREEN" "✓ Node.js 安装完成"
}

# 安装 Node.js - Linux
install_node_linux() {
    print_color "$YELLOW" "正在为 Linux 安装 Node.js..."

    case $DISTRO in
        ubuntu|debian)
            print_color "$YELLOW" "使用 APT 安装 Node.js..."
            sudo apt update
            sudo apt install -y curl
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt install -y nodejs
            ;;
        fedora|rhel|centos)
            print_color "$YELLOW" "使用 YUM/DNF 安装 Node.js..."
            sudo yum install -y curl
            curl -fsSL https://rpm.nodesource.com/setup_lts.x | sudo bash -
            sudo yum install -y nodejs
            ;;
        arch)
            print_color "$YELLOW" "使用 Pacman 安装 Node.js..."
            sudo pacman -S nodejs npm
            ;;
        *)
            print_color "$YELLOW" "使用 NodeSource 通用安装方法..."
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt-get install -y nodejs || sudo yum install -y nodejs
            ;;
    esac

    print_color "$GREEN" "✓ Node.js 安装完成"
}

# 安装 Codex CLI
install_codex() {
    print_color "$YELLOW" "正在安装 Codex CLI..."

    # 使用 npm 安装
    npm install -g @openai/codex@latest

    if [ $? -eq 0 ]; then
        print_color "$GREEN" "✓ Codex CLI 安装成功"
        return 0
    else
        print_color "$RED" "✗ Codex CLI 安装失败"
        return 1
    fi
}

# 配置 Codex CLI
configure_codex() {
    print_color "$YELLOW" "正在配置 Codex CLI..."

    CODEX_DIR="$HOME/.codex"

    # 创建配置目录
    mkdir -p "$CODEX_DIR"

    # 创建 config.toml 文件
    cat > "$CODEX_DIR/config.toml" << EOF
model_provider = "crs"
model = "gpt-5.1-codex"
model_reasoning_effort = "high"

disable_response_storage = true

[model_providers.crs]
name = "crs"
base_url = "https://api5.ai"
wire_api = "responses"
EOF

    print_color "$GREEN" "✓ 配置文件 config.toml 已创建"

    # 创建 auth.json 文件
    cat > "$CODEX_DIR/auth.json" << EOF
{
  "OPENAI_API_KEY": "$API_KEY"
}
EOF

    print_color "$GREEN" "✓ 认证文件 auth.json 已创建"
}

# 验证安装
verify_installation() {
    print_color "$YELLOW" "正在验证安装..."

    if command_exists codex; then
        VERSION=$(codex --version 2>&1 || echo "版本信息不可用")
        print_color "$GREEN" "✓ Codex CLI 安装验证成功"
        print_color "$CYAN" "  版本: $VERSION"
        return 0
    else
        print_color "$RED" "✗ 验证失败"
        return 1
    fi
}

# 主安装流程
main() {
    print_color "$CYAN" "开始安装流程..."
    echo ""

    # 步骤 1: 检测操作系统
    print_color "$CYAN" "[1/5] 检测操作系统..."
    detect_os
    echo ""

    # 步骤 2: 检查并安装 Node.js
    print_color "$CYAN" "[2/5] 检查 Node.js..."
    if ! check_node; then
        print_color "$YELLOW" "正在安装 Node.js..."

        if [ "$OS" == "macos" ]; then
            install_node_macos
        elif [ "$OS" == "linux" ]; then
            install_node_linux
        else
            print_color "$RED" "不支持的操作系统: $OS"
            exit 1
        fi

        # 重新检查安装
        if ! check_node; then
            print_color "$RED" "Node.js 安装失败，请手动安装后重试"
            exit 1
        fi
    fi
    echo ""

    # 步骤 3: 安装 Codex CLI
    print_color "$CYAN" "[3/5] 安装 Codex CLI..."
    if ! install_codex; then
        print_color "$RED" "Codex CLI 安装失败"
        exit 1
    fi
    echo ""

    # 步骤 4: 配置 Codex CLI
    print_color "$CYAN" "[4/5] 配置 Codex CLI..."
    configure_codex
    echo ""

    # 步骤 5: 验证安装
    print_color "$CYAN" "[5/5] 验证安装..."
    if verify_installation; then
        echo ""
        print_color "$GREEN" "================================================"
        print_color "$GREEN" "         Codex CLI 安装成功！"
        print_color "$GREEN" "================================================"
        echo ""
        print_color "$CYAN" "使用方法："
        print_color "$WHITE" "  在终端输入 'codex' 启动 Codex CLI"
        echo ""
        print_color "$YELLOW" "配置文件位置："
        print_color "$WHITE" "  $HOME/.codex/config.toml"
        print_color "$WHITE" "  $HOME/.codex/auth.json"
        echo ""

        # 提示重新加载 shell 配置
        print_color "$YELLOW" "提示："
        print_color "$WHITE" "  如果命令未找到，请运行以下命令刷新环境变量："
        if [ "$OS" == "macos" ]; then
            print_color "$CYAN" "  source ~/.zshrc"
        else
            print_color "$CYAN" "  source ~/.bashrc"
        fi
    else
        print_color "$RED" "安装验证失败，请检查错误信息"
        exit 1
    fi

    echo ""
    print_color "$CYAN" "安装完成！"
}

# 运行主函数
main