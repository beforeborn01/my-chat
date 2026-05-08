# my-chat 部署说明

部署到火山 ECS（root@115.190.210.138，sshmeet 别名），单容器 Docker。

代码仓库：`git@github.com:beforeborn01/my-chat.git`

## 一次性初始化（首次部署）

前提：本地仓库已 push 到 `origin/main`，且 **ECS 上已有能访问该 GitHub 仓库的 SSH key**（已确认）。

```bash
cd /Users/bc/code/ai/my-chat
./scripts/release.sh init
```

脚本会：

1. 本地预检：HEAD 干净、已 push 到 `origin/main`
2. ssh 到 ECS，执行 `git clone git@github.com:beforeborn01/my-chat.git → /root/my-chat`（用 ECS 端的 GitHub key）
3. 在 ECS 跑 `./scripts/deploy.sh init`：
   - 复制 `.env.prod.example → .env`
   - 写入 `YUN_API_KEY`（从本机 `.env` 读，没读到则保留占位）
   - 随机生成 `SESSION_SECRET`
   - `docker compose up -d --build`
4. 等容器健康 + 冒烟 `/healthz`

完成后浏览器访问 `http://115.190.210.138:8091/` 即可。

## 日常更新

```bash
# 本机 commit + push 到 origin/main 后：
./scripts/release.sh           # ECS git pull + 重建 + 重启
```

## 常用运维（在本机 / ECS 上）

```bash
# 本机：
./scripts/release.sh logs      # 跟 ECS 日志
./scripts/release.sh status    # 看容器状态
./scripts/release.sh ssh       # 直接登入 ECS 该项目目录

# ECS 上（直接 sshmeet 进去后）：
cd /root/my-chat
./scripts/deploy.sh status     # 看容器状态
./scripts/deploy.sh logs       # 跟日志
./scripts/deploy.sh restart    # 重启
./scripts/deploy.sh down       # 停（保留 data/）
./scripts/deploy.sh nuke       # 停 + 删 data/（⚠️ 清空所有用户和对话）
./scripts/deploy.sh backup     # 把 data/ 打成 tar.gz 到 backups/
```

## 端口与防火墙

- ECS 安全组需放开 `8091/TCP`（默认；改 `WEB_PORT` 同步改安全组）
- 22/TCP 用于 SSH
- 不需要数据库端口 — SQLite 是文件，不走网络

## 数据位置

- ECS 上：`/root/my-chat/data/chat.db` + `/root/my-chat/data/images/<user>/<conv>/`
- 通过 docker bind mount 挂进容器，重建容器不丢数据
- 备份：`./scripts/deploy.sh backup` 把 `data/` 打成 tar.gz 到 `backups/`

## 与 calorie-log 的差别

| | calorie-log | my-chat |
|---|---|---|
| 部署目录 | /root/calorie-log | /root/my-chat |
| 端口 | 8088 | 8091 |
| 数据库 | Postgres + Redis 容器 | SQLite 文件 |
| 前端 | nginx + Vite SPA | Flask 服务端渲染 |
| 同步方式 | git pull | git pull |
| 仓库 | (private) | github.com/beforeborn01/my-chat |

## 排障

| 症状 | 检查 |
|---|---|
| 502 / 浏览器连不上 | `./scripts/release.sh logs`；安全组 8091 是否放开 |
| 登录后立即跳回登录页 | `SESSION_SECRET` 重启后变了，cookie 失效；让用户重新输入 |
| 图片生成报错 | 上游 `gpt-image-2` 偶发 502；后端最长重试 100s 才放弃 |
| `release.sh init` 卡在 git clone | ECS 端的 GitHub key 失效或没权限读这个 repo |
| 容器起不来 | `.env` 缺 `YUN_API_KEY` 或 `SESSION_SECRET` |
| `git pull` 在 ECS 失败 | ECS 端 GitHub key 失效；用 `./scripts/release.sh ssh` 进去手动 `git pull` 看具体错误 |
