#!/usr/bin/env bash
# ============================================
# my-chat 生产部署脚本（ECS 端）
# 用法：
#   ./scripts/deploy.sh init      # 首次：生成 .env 并启动
#   ./scripts/deploy.sh update    # 重建并重启
#   ./scripts/deploy.sh logs [svc]
#   ./scripts/deploy.sh status
#   ./scripts/deploy.sh stop
#   ./scripts/deploy.sh restart [svc]
#   ./scripts/deploy.sh down      # 停止（保留数据卷）
#   ./scripts/deploy.sh nuke      # 停止并清空 ./data/（⚠️ 删数据库 + 图片）
#   ./scripts/deploy.sh backup    # 把 data/ 打包到 backups/
#
# 环境变量：
#   YUN_API_KEY  — init 时若 .env 缺，用此值覆盖
# ============================================

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"

log()  { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[deploy][WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[deploy][ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

sedi() {
  if sed --version >/dev/null 2>&1; then sed -i "$@"; else sed -i '' "$@"; fi
}

check_deps() {
  command -v docker >/dev/null || err "docker 未安装。可运行：curl -fsSL https://get.docker.com | sh"
  docker compose version >/dev/null 2>&1 || err "docker compose (v2) 未安装"
  command -v openssl >/dev/null || err "openssl 未安装"
}

gen_env() {
  if [[ -f .env ]]; then
    log ".env 已存在，跳过生成"
    return
  fi
  [[ -f .env.prod.example ]] || err "找不到 .env.prod.example"
  log "首次部署：生成 .env"
  cp .env.prod.example .env

  local secret
  secret=$(openssl rand -base64 48 | tr -d '\n')
  sedi "s|REPLACE_ME_SESSION_SECRET_AT_LEAST_32_CHARS_xxxxxxxxxxxx|${secret}|" .env

  if [[ -n "${YUN_API_KEY:-}" ]]; then
    sedi "s|sk-REPLACE_ME_WITH_YOUR_KEY|${YUN_API_KEY}|" .env
    log "已写入 YUN_API_KEY（来自环境变量）"
  else
    warn "未传入 YUN_API_KEY，.env 里仍是占位；编辑 .env 后再 ./scripts/deploy.sh restart"
  fi
}

init() {
  check_deps
  gen_env
  mkdir -p data/images backups

  log "构建并启动（首次约 1-2 分钟：拉镜像 + pip install）"
  $COMPOSE up -d --build

  log "等待健康..."
  local ready=false
  for _ in $(seq 1 30); do
    if $COMPOSE ps app 2>/dev/null | grep -q 'healthy'; then
      ready=true; break
    fi
    sleep 3
  done
  $ready || warn "app 超过 90 秒未健康，查看日志：./scripts/deploy.sh logs"

  $COMPOSE ps
  local port
  port=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2-)
  port=${port:-8091}
  log "完成。访问 http://<你的公网IP>:${port}/"
}

update() {
  check_deps
  [[ -f .env ]] || err ".env 不存在，先 ./scripts/deploy.sh init"
  log "重建并重启"
  $COMPOSE up -d --build
  log "清理无主镜像"
  docker image prune -f >/dev/null
  $COMPOSE ps
}

logs_cmd() { $COMPOSE logs -f --tail=200 "${2:-}"; }
status()   { $COMPOSE ps; }
stop()     { $COMPOSE stop; }
restart()  { $COMPOSE restart "${2:-}"; }
down_cmd() { $COMPOSE down; log "已停止。data/ 保留；彻底清空用 nuke"; }

nuke() {
  warn "即将停止并删除 ./data/（所有用户/对话/图片都会丢失）"
  read -rp "确认？输入 YES 继续：" ans
  [[ "$ans" == "YES" ]] || { log "取消"; exit 0; }
  $COMPOSE down
  rm -rf data
  mkdir -p data/images
  log "已清空 data/"
}

backup() {
  mkdir -p backups
  local ts out
  ts=$(date +%Y%m%d-%H%M%S)
  out="backups/data-${ts}.tar.gz"
  log "备份 data/ → $out"
  tar -czf "$out" data
  log "完成：$(du -h "$out" | cut -f1)"
}

case "${1:-}" in
  init)    init ;;
  update)  update ;;
  logs)    logs_cmd "$@" ;;
  status)  status ;;
  stop)    stop ;;
  restart) restart "$@" ;;
  down)    down_cmd ;;
  nuke)    nuke ;;
  backup)  backup ;;
  *)
    echo "用法：$0 {init|update|logs|status|stop|restart|down|nuke|backup}"
    exit 1 ;;
esac
