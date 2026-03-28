#!/bin/bash
# SSH Tunnel - 系统级实现（替代 paramiko Python 脚本）
# 使用系统 OpenSSH，性能远超 Python paramiko
#
# 用法:
#   bash scripts/ssh_tunnel.sh           # 启动隧道
#   bash scripts/ssh_tunnel.sh --setup   # 首次配置密钥免密登录

SSH_HOST="172.16.108.231"
SSH_PORT=22
SSH_USER="flash_swap_test"
SSH_PASS="flash_swap"
LOCAL_PORT=18545
REMOTE_HOST="127.0.0.1"
REMOTE_PORT=18545

# ---- 配置 SSH 密钥 ----
if [ "$1" = "--setup" ]; then
    KEY_PATH="$HOME/.ssh/id_rsa"

    if [ ! -f "$KEY_PATH" ]; then
        echo "[1/3] 生成 SSH 密钥..."
        ssh-keygen -t rsa -b 4096 -f "$KEY_PATH" -N "" -q
    else
        echo "[1/3] SSH 密钥已存在: $KEY_PATH"
    fi

    echo "[2/3] 上传公钥到 ${SSH_USER}@${SSH_HOST}..."
    echo "  需要输入密码: $SSH_PASS"
    ssh-copy-id -p "$SSH_PORT" "${SSH_USER}@${SSH_HOST}" 2>/dev/null || {
        # ssh-copy-id 不可用时手动上传
        cat "$KEY_PATH.pub" | ssh -p "$SSH_PORT" "${SSH_USER}@${SSH_HOST}" \
            "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh"
    }

    echo "[3/3] 测试免密登录..."
    ssh -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=5 "${SSH_USER}@${SSH_HOST}" "echo OK"
    if [ $? -eq 0 ]; then
        echo "密钥配置成功！"
    else
        echo "密钥配置可能失败，请手动检查。"
    fi
    exit 0
fi

# ---- 启动 SSH 隧道（自动重连） ----
echo "========================================="
echo "  SSH Tunnel (系统级 OpenSSH)"
echo "  本地 127.0.0.1:${LOCAL_PORT}"
echo "  远程 ${REMOTE_HOST}:${REMOTE_PORT}"
echo "  跳板 ${SSH_USER}@${SSH_HOST}:${SSH_PORT}"
echo "  按 Ctrl+C 停止"
echo "========================================="

while true; do
    echo "[$(date +%H:%M:%S)] 启动 SSH 隧道..."

    ssh -N \
        -L "${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}" \
        -p "${SSH_PORT}" \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=3 \
        -o TCPKeepAlive=yes \
        -o ExitOnForwardFailure=yes \
        -o ConnectTimeout=10 \
        -o StrictHostKeyChecking=no \
        "${SSH_USER}@${SSH_HOST}"

    EXIT_CODE=$?
    echo "[$(date +%H:%M:%S)] SSH 隧道断开 (exit=$EXIT_CODE)，5 秒后重连..."
    sleep 5
done
