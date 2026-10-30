# Nginx 部署配置检查清单

面向本项目的公网部署：应用只监听 `127.0.0.1:8000`（HTTP），由 Nginx 终止 TLS 并补齐安全响应头。
配套模板：[deploy/nginx.conf.example](../deploy/nginx.conf.example)；应用侧验收见 [security-acceptance.md](security-acceptance.md) 第 6、7 节。

用法：按顺序逐项执行，全部命中“期望”才算通过。命令默认在服务器上用 bash 执行；`<域名>`、`<账号>`、`<密码>` 按实际替换。

## 0. 应用侧前置（未通过则不要开流量）

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 0.1 | 应用以 production 启动 | `docker compose config \| grep TRAVELAI_ENV` | `production` |
| 0.2 | Cookie 必须 Secure | `docker compose config \| grep TRAVELAI_COOKIE_SECURE` | `true` |
| 0.3 | 审计密钥为自定义长串 | `docker compose config \| grep TRAVELAI_AUDIT_KEY` | 非空且 ≥16 位、非模板默认值 |
| 0.4 | 代理头信任默认关闭 | `docker compose config \| grep TRAVELAI_TRUST_PROXY` | 初始为 `false`，确认 Nginx 覆写 XFF 后再改 `true` |
| 0.5 | 应用端口不暴露公网 | `ss -ltnp \| grep 8000` | 仅 `127.0.0.1:8000`，无 `0.0.0.0:8000` |
| 0.6 | 容器以非 root 运行 | `docker exec <容器> id` | `uid=10001(appuser)` |
| 0.7 | 生产关闭 API 文档 | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/openapi.json` | `404` |

## 1. 配置语法与生效

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 1.1 | 语法正确 | `nginx -t` | `syntax is ok` / `test is successful` |
| 1.2 | 实际加载的配置与模板一致 | `nginx -T \| sed -n '/server_name <域名>/,/^}/p'` | 含 `ssl_protocols`、全部 `add_header`、`proxy_set_header X-Forwarded-For $remote_addr` |
| 1.3 | 没有重复 `server_name` 抢占 | `nginx -T \| grep -n 'server_name'` | 每个域名只出现一次（含 `default_server` 的默认站点） |
| 1.4 | `location` 内没有新增 `add_header` | `nginx -T \| grep -A20 'location /'` | `location` 内只有 `proxy_*`，否则会丢弃 server 级响应头 |

> Nginx 规则：`location` 内一旦出现任意 `add_header`，server 级的 `add_header` 全部失效。需要新增头时必须写在 `location` 内并重复全部头，或改用 `map`。

## 2. TLS 与端口

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 2.1 | HTTP 跳转 HTTPS | `curl -sI http://<域名>/ \| head -1` | `301`，`Location: https://<域名>/` |
| 2.2 | 证书链有效且域名匹配 | `curl -sI https://<域名>/ \| head -1` | `200`（无证书告警） |
| 2.3 | 仅启用 TLS 1.2/1.3 | `openssl s_client -connect <域名>:443 -tls1 </dev/null 2>&1 \| grep -i 'protocol'` | 握手失败（TLS 1.0/1.1 被拒） |
| 2.4 | 证书自动续期 | `systemctl list-timers \| grep certbot` | 存在续期任务；`certbot renew --dry-run` 成功 |
| 2.5 | 续期后自动 reload | `grep -R 'reload' /etc/letsencrypt/renewal-hooks/deploy/` | 存在 `nginx -s reload` 钩子 |
| 2.6 | 容器端口未映射到公网 | `docker compose config \| grep -A2 ports` | `127.0.0.1:8000:8000` |

## 3. 代理头与来源 IP

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 3.1 | XFF 由代理覆写（非追加） | `nginx -T \| grep 'X-Forwarded-For'` | `proxy_set_header X-Forwarded-For $remote_addr;` |
| 3.2 | 协议头正确传递 | `nginx -T \| grep 'X-Forwarded-Proto'` | `$scheme` |
| 3.3 | 客户端伪造 XFF 无效 | `curl -s -X POST https://<域名>/api/v1/auth/login -H 'Content-Type: application/json' -H 'X-Forwarded-For: 1.2.3.4' -d '{"username":"nobody","password":"wrong-pass-1"}'` 后查审计/日志 | 记录到真实出口 IP，而非 `1.2.3.4` |
| 3.4 | 信任开关与时序正确 | `docker compose config \| grep TRAVELAI_TRUST_PROXY` | 3.1 与 3.2 确认后才是 `true`；否则保持 `false` |

> 顺序很重要：先确认 Nginx 已覆写 XFF，再把 `TRAVELAI_TRUST_PROXY` 改成 `true`。反了就等于放开 IP 伪造，限流与审计同时失真。

## 4. 安全响应头

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 4.1 | HSTS | `curl -sI https://<域名>/ \| grep -i strict-transport` | `max-age=31536000` |
| 4.2 | CSP | `curl -sI https://<域名>/ \| grep -i content-security-policy` | 含 `default-src 'self'`、`frame-ancestors 'none'` |
| 4.3 | 点击劫持防护 | `curl -sI https://<域名>/ \| grep -i x-frame-options` | `DENY` |
| 4.4 | 类型嗅探防护 | `curl -sI https://<域名>/ \| grep -i x-content-type-options` | `nosniff` |
| 4.5 | Referrer 与权限策略 | `curl -sI https://<域名>/ \| grep -Ei 'referrer-policy\|permissions-policy'` | `same-origin` / 明确禁用项 |
| 4.6 | 页面无 CSP 报错 | 浏览器打开首页，F12 Console | 无 CSP violation，Vue 页面与接口正常 |

> 若后续引入内联脚本、外链 CDN、地图瓦片或图片外域，必须同步放宽 CSP（`script-src`/`img-src`/`connect-src`），否则功能会静默失效。

## 5. Cookie 与会话

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 5.1 | 登录 Set-Cookie 属性 | `curl -si -X POST https://<域名>/api/v1/auth/login -H 'Content-Type: application/json' -d '{"username":"<账号>","password":"<密码>"}' \| grep -i set-cookie` | 同时含 `HttpOnly`、`Secure`、`SameSite=lax`、`Path=/` |
| 5.2 | 会话可跨请求保持 | 用浏览器或 `curl -c jar.txt -b jar.txt` 依次登录、访问 `/api/v1/trips` | 登录 200，随后接口 200 |
| 5.3 | 登出后会话失效 | 登出后带同一 Cookie 访问 `/api/v1/trips` | `401` |
| 5.4 | 非 HTTPS 不产生 Secure Cookie 例外 | 用 HTTP 直连应用端口（不经过 Nginx） | 不要在此场景登录；生产只走 HTTPS |

## 6. 请求约束与超时

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 6.1 | 请求体大小受限 | 构造 >1MB JSON 提交到任意写接口 | `413 Request Entity Too Large` |
| 6.2 | 规划接口不超时 | 通过 HTTPS 创建一次真实行程 | 正常返回，耗时低于 `proxy_read_timeout`（60s） |
| 6.3 | 无多余方法放开 | `nginx -T \| grep -Ei 'limit_except\|allow\|deny'` | 未额外放开上传/代理；写接口仍由应用校验 JSON |
| 6.4 | 未引入 multipart/CORS 放宽 | `curl -s -o /dev/null -w '%{http_code}' -X OPTIONS https://<域名>/api/v1/trips -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: POST'` | 无 `Access-Control-Allow-Origin` 响应头 |

> 6.4 关系到 CSRF 前提：一旦 Nginx 层放开 CORS 或改写请求类型，`docs/csrf-session-lifecycle.md` 的“维持现状”结论即失效，必须升级 CSRF 方案。

## 7. 可用性与运维

| # | 检查项 | 验证命令 | 期望 |
|---|---|---|---|
| 7.1 | 本机健康检查 | `curl -s http://127.0.0.1:8000/health` | `{"status":"ok",...}` |
| 7.2 | 经代理健康检查 | `curl -s https://<域名>/health` | 同上 |
| 7.3 | 进程自动拉起 | `docker compose config \| grep restart` | `unless-stopped`（或 systemd `Restart=always`） |
| 7.4 | 日志不落敏感信息 | `grep -n 'log_format' /etc/nginx/nginx.conf` | 默认 `combined`；无 `$http_cookie`、`$http_authorization`、`$request_body` |
| 7.5 | 日志轮转 | `ls /etc/logrotate.d/ \| grep nginx` | 存在轮转配置 |
| 7.6 | 备份与审计任务 | `crontab -l \| grep -Ei 'backup\|cleanup\|audit'` | 会话清理、审计留存/校验、数据库备份均有调度 |
| 7.7 | 磁盘余量 | `df -h /` | 可用空间 > 20%，数据库目录单独监控 |

## 8. 一键复验脚本

```bash
DOMAIN="travelai.example.com"
APP="http://127.0.0.1:8000"

echo "== 1 配置 =="; nginx -t
echo "== 2 跳转 =="; curl -sI "http://$DOMAIN/" | head -1
echo "== 3 头 =="; curl -sI "https://$DOMAIN/" | grep -Ei 'strict-transport|content-security-policy|x-frame-options|x-content-type-options|referrer-policy|permissions-policy'
echo "== 4 文档关闭 =="; curl -s -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/openapi.json"
echo "== 5 健康 =="; curl -s "$APP/health"; echo; curl -s "https://$DOMAIN/health"; echo
echo "== 6 监听 =="; ss -ltnp | grep 8000
echo "== 7 Cookie =="; curl -si -X POST "https://$DOMAIN/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"<账号>","password":"<密码>"}' | grep -i set-cookie
echo "== 8 无 CORS =="; curl -si -X OPTIONS "https://$DOMAIN/api/v1/trips" \
  -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: POST' | grep -i access-control || echo "no CORS headers"
```

期望：`nginx -t` 成功；跳转 301；六个安全头齐全；`openapi.json` 为 404；两处 `/health` 均 200；8000 只监听 127.0.0.1；`Set-Cookie` 含 `HttpOnly; Secure; SameSite=lax`；无 `access-control-*` 头。

## 9. 常见故障对照

| 症状 | 可能原因 | 处理 |
|---|---|---|
| 浏览器反复要求登录 | 缺 `Secure` / Nginx 未传 `X-Forwarded-Proto` / 仍走 HTTP | 查 5.1、3.2；确认通过 HTTPS 访问 |
| 登录后立刻 401 | `TRAVELAI_AUDIT_KEY` 变更导致会话校验异常（不应发生），或应用重启前会话未持久化 | 查应用日志；确认数据库卷挂载正确 |
| 所有请求 502 | 应用未启动或未监听 127.0.0.1:8000 | `docker compose ps`、`ss -ltnp` |
| 首页白屏、控制台 CSP 拦截 | CSP 未覆盖实际资源来源（图片外域/CDN/内联脚本） | 按报错放宽 `img-src`/`script-src`，不要整段放开 |
| 响应头全部消失 | `location` 内写了新的 `add_header` | 按 1.4 移到 server 级或补齐全部头 |
| 限流把所有人一起封 | `TRAVELAI_TRUST_PROXY=true` 但 Nginx 未覆写 XFF，所有请求被当成同一 IP | 先修 3.1，再观察 `login_failures` 的 `key` 是否为真实 IP |
| 上传/表单请求 422 | 应用只接受 JSON 写请求（CSRF 前提） | 不要在前端改用 form；如需上传必须先升级 CSRF 方案 |

## 10. 完成标准

- 第 0–7 节全部勾选通过，第 8 节脚本输出与期望一致。
- 浏览器完成一次：注册 → 创建行程 → 修改某天 → 版本对比 → 登出，全程无报错、无 CSP 拦截。
- 把本次 `nginx -T` 输出、第 8 节结果与 `docs/security-acceptance.md` 第 7 节复验结果一并归档到发版记录。
