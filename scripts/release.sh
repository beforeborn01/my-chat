#!/usr/bin/env bash
# ============================================================
# 发布到火山 ECS（root@115.190.210.138，sshmeet 别名）
#
# 基于 git：本机 push → ECS git pull → docker compose up
#
# 用法：
#   ./scripts/release.sh           # 部署当前分支（HEAD 必须 commit + push）
#   ./scripts/release.sh init      # 首次部署（ECS 端首次 git clone + init）
#   ./scripts/release.sh -b main   # 切到 main
#   ./scripts/release.sh logs      # 跟日志
#   ./scripts/release.sh status    # 看容器状态
#   ./scripts/release.sh ssh       # 直接登入 ECS 该项目目录
#   ./scripts/release.sh --skip-checks   # 紧急用：跳过本地预检
#   ./scripts/release.sh --skip-smoke    # 跳过冒烟测试
#
# 可被环境变量覆盖：
#   ECS_HOST=root@115.190.210.138
#   ECS_KEY=~/program/volc/volc-meet.pem
#   ECS_PROJECT_DIR=/root/my-chat
#   GIT_REPO=git@github.com:beforeborn01/my-chat.git
#   PUBLIC_BASE_URL=http://115.190.210.138:8091
#   HEALTH_TIMEOUT=120
# ============================================================

set -euo pipefail

cd "$(dirname "$0")/.."

ECS_HOST="${ECS_HOST:-root@115.190.210.138}"
ECS_KEY="${ECS_KEY:-$HOME/program/volc/volc-meet.pem}"
ECS_PROJECT_DIR="${ECS_PROJECT_DIR:-/root/my-chat}"
GIT_REPO="${GIT_REPO:-git@github.com:beforeborn01/my-chat.git}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://115.190.210.138:8091}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

BRANCH=""
SKIP_CHECKS=0
SKIP_SMOKE=0
CMD=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--branch)   BRANCH="$2"; shift 2 ;;
    --skip-checks) SKIP_CHECKS=1; shift ;;
    --skip-smoke)  SKIP_SMOKE=1; shift ;;
    -h|--help)     sed -n '2,/^# ====*$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    init|update|logs|status|ssh)
                   CMD="$1"; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ -z "${BRANCH}" ]] && BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
CMD="${CMD:-update}"

log()  { printf '\033[36m[release]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[release][WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[release][ERROR]\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[32m[release][✓]\033[0m %s\n' "$*"; }

ssh_ecs()  { ssh -i "${ECS_KEY}" -o ConnectTimeout=15 "${ECS_HOST}" "$@"; }
sshA_ecs() { ssh -A -i "${ECS_KEY}" -o ConnectTimeout=15 "${ECS_HOST}" "$@"; }
sshq_ecs() { ssh -i "${ECS_KEY}" -o ConnectTimeout=15 -o BatchMode=yes "${ECS_HOST}" "$@"; }

precheck_ssh() {
  [[ -f "${ECS_KEY}" ]] || err "找不到 SSH key：${ECS_KEY}（设置 ECS_KEY 覆盖）"
  sshq_ecs 'echo ok' >/dev/null 2>&1 || err "无法 SSH 到 ${ECS_HOST}（检查 sshmeet / key 权限）"
  ok "ECS 可达：${ECS_HOST}"
}

local_git_checks() {
  log "本地预检：分支=${BRANCH}"
  [[ -d .git ]] || err "本目录不是 git 仓库。先 git init 并 push 到 ${GIT_REPO}"
  [[ -z "$(git status --porcelain)" ]] || err "工作树有未提交改动。先 git commit + git push。"

  local local_sha remote_sha
  local_sha=$(git rev-parse HEAD)
  remote_sha=$(git rev-parse "origin/${BRANCH}" 2>/dev/null) || \
    err "远端没有 origin/${BRANCH}。先 git push -u origin ${BRANCH}。"
  [[ "$local_sha" == "$remote_sha" ]] || \
    err "本地 HEAD 与 origin/${BRANCH} 不一致：本地=${local_sha:0:7} 远端=${remote_sha:0:7}；先 git push。"
  ok "本地干净，HEAD ${local_sha:0:7} 与 origin/${BRANCH} 一致"
}

remote_init() {
  log "ECS：首次部署（git clone + ./scripts/deploy.sh init）"
  # ssh -A 转发本机 SSH agent，以便 ECS 端用本机的 GitHub key 认证 clone
  sshA_ecs bash -s <<EOF
set -euo pipefail
mkdir -p "\$(dirname '${ECS_PROJECT_DIR}')"
if [ -d '${ECS_PROJECT_DIR}/.git' ]; then
  echo "[ecs] 已经是 git 仓库，跳过 clone"
  cd '${ECS_PROJECT_DIR}'
  git fetch --prune origin
  git checkout '${BRANCH}'
  git pull --ff-only origin '${BRANCH}'
else
  echo "[ecs] git clone ${GIT_REPO} → ${ECS_PROJECT_DIR}"
  git clone --branch '${BRANCH}' '${GIT_REPO}' '${ECS_PROJECT_DIR}'
fi
cd '${ECS_PROJECT_DIR}'
chmod +x scripts/*.sh
YUN_API_KEY='${YUN_API_KEY:-}' ./scripts/deploy.sh init
EOF
}

remote_update() {
  log "ECS：git pull + ./scripts/deploy.sh update（分支：${BRANCH}）"
  ssh_ecs bash -s <<EOF
set -euo pipefail
cd "${ECS_PROJECT_DIR}"

# 显式拒绝 ECS 端的脏工作树（git pull 会无声失败）
if [ -n "\$(git status --porcelain)" ]; then
  echo "[ecs][ERROR] ECS 工作树有未提交改动，请人工进去看：" >&2
  git status --short >&2
  exit 1
fi

current=\$(git branch --show-current)
if [ "\$current" != "${BRANCH}" ]; then
  echo "[ecs] 切分支：\$current -> ${BRANCH}"
  git fetch --prune origin
  git checkout "${BRANCH}"
fi

git pull --ff-only origin "${BRANCH}"
chmod +x scripts/*.sh
./scripts/deploy.sh update
EOF
}

wait_healthy() {
  log "等容器健康（最多 ${HEALTH_TIMEOUT}s）"
  ssh_ecs bash -s <<EOF
set -e
cd '${ECS_PROJECT_DIR}'
deadline=\$(( \$(date +%s) + ${HEALTH_TIMEOUT} ))
while [ \$(date +%s) -lt \$deadline ]; do
  cid=\$(docker compose -f docker-compose.prod.yml ps -q app 2>/dev/null)
  if [ -n "\$cid" ]; then
    health=\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' "\$cid")
    if [ "\$health" = "healthy" ]; then
      echo "[ecs] app healthy"; exit 0
    fi
  fi
  sleep 3
done
echo "[ecs][WARN] app 超时未健康，最近日志：" >&2
docker compose -f docker-compose.prod.yml logs --tail=80 app >&2
exit 1
EOF
}

smoke() {
  log "冒烟：GET ${PUBLIC_BASE_URL}/healthz"
  local resp
  resp=$(curl -sS --max-time 15 "${PUBLIC_BASE_URL}/healthz" 2>&1) || { warn "$resp"; return 1; }
  if [[ "$resp" == "ok"* ]]; then
    ok "/healthz OK"
  else
    warn "意外响应：$resp"; return 1
  fi
}

case "$CMD" in
  init)
    [[ $SKIP_CHECKS -eq 1 ]] || local_git_checks
    precheck_ssh
    remote_init
    wait_healthy || warn "健康检查未过，但已部署；人工查 \`./scripts/release.sh logs\`"
    [[ $SKIP_SMOKE -eq 1 ]] || smoke || true
    ok "完成。访问 ${PUBLIC_BASE_URL}/"
    ;;
  update)
    [[ $SKIP_CHECKS -eq 1 ]] || local_git_checks
    precheck_ssh
    remote_update
    wait_healthy || warn "健康检查未过，人工查 \`./scripts/release.sh logs\`"
    [[ $SKIP_SMOKE -eq 1 ]] || smoke || true
    ok "完成。访问 ${PUBLIC_BASE_URL}/"
    ;;
  logs)
    ssh_ecs "cd '${ECS_PROJECT_DIR}' && ./scripts/deploy.sh logs"
    ;;
  status)
    ssh_ecs "cd '${ECS_PROJECT_DIR}' && ./scripts/deploy.sh status"
    ;;
  ssh)
    exec ssh -i "${ECS_KEY}" -t "${ECS_HOST}" "cd '${ECS_PROJECT_DIR}' && bash -l"
    ;;
  *)
    echo "用法：$0 {init|update|logs|status|ssh}"
    exit 2
    ;;
esac
