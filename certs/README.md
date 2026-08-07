# SSL 证书目录

将生产 SSL 证书放入本目录：

- `fullchain.pem` — 证书链（Let's Encrypt / certbot 或商业证书）
- `privkey.pem` — 私钥

未提供证书时，`nginx/entrypoint.sh` 会自动生成自签名证书（仅用于内网/演示）。

生成 Let's Encrypt 证书示例：

```bash
sudo certbot certonly --standalone -d your-domain.com
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ./certs/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem ./certs/
sudo chmod 644 ./certs/fullchain.pem
sudo chmod 600 ./certs/privkey.pem
docker compose up -d nginx
```

**注意**：证书含私钥，本目录下的 `*.pem / *.key` 已被 `.gitignore` 排除，切勿提交到仓库。
