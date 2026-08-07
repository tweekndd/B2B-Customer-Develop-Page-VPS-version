#!/bin/sh
# =============================================================================
# B2B Customer Develop Platform — 自签名证书生成脚本
# 挂载到 /docker-entrypoint.d/20-gen-certs.sh（nginx 官方镜像启动前自动执行）
#
# 注意：此脚本可能被 nginx 官方 entrypoint 以 source 方式执行，
# 因此不能使用 exit（会直接退出容器进程），只允许用 if/else 分支。
#
# 逻辑：
#   - ./certs 目录下已有 fullchain.pem + privkey.pem → 跳过，使用真实证书
#   - 缺失 → 自动生成自签名证书（仅用于无域名/内网演示，生产请用 certbot）
# =============================================================================

CERT_DIR=/etc/nginx/certs
FULLCHAIN=$CERT_DIR/fullchain.pem
PRIVKEY=$CERT_DIR/privkey.pem

if [ -f "$FULLCHAIN" ] && [ -f "$PRIVKEY" ]; then
    echo "[nginx] 检测到已配置证书，跳过自签名生成"
else
    echo "[nginx] 未找到证书，正在生成自签名证书（仅演示/内网使用）..."

    # 生成 RSA 2048 自签名证书（1 年有效期）
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$PRIVKEY" \
        -out "$FULLCHAIN" \
        -subj "/CN=localhost" \
        >/dev/null 2>&1

    chmod 644 "$FULLCHAIN" 2>/dev/null || true
    chmod 600 "$PRIVKEY" 2>/dev/null || true

    echo "[nginx] 自签名证书已生成：$FULLCHAIN"
fi
