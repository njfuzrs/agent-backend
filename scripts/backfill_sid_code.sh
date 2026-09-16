#!/bin/bash
# backfill_sid_code.sh —— 补传 sid-code 本地积压的轨迹会话
#
# 背景：sid-code 的自动上传只挂在 SessionEnd（collector.ts:1783），而实测绝大多数
# 进程走不到 SessionEnd（Ctrl-C / kill / 关终端都不触发），导致本地会话从未上传。
# 详见 docs-research/sid-code/bugfixes/todo/ 下的分析文档。
#
# 本脚本按 uploader.ts 的协议（gzip + X-Content-SHA256 + multipart）逐文件补传。
# 只读本地数据，不删除任何东西；已在云端存在的 session 默认跳过。
#
# 用法：
#   scripts/backfill_sid_code.sh                 # 补传全部未上传会话
#   scripts/backfill_sid_code.sh --dry-run       # 只列出将要传什么，不实际传
#   scripts/backfill_sid_code.sh --force         # 云端已存在也重传（服务端幂等覆盖）
#   TRAJ_SESSIONS_DIR=... TRAJ_UPLOAD_URL=... TRAJ_UPLOAD_TOKEN=... scripts/backfill_sid_code.sh
set -uo pipefail

SESSIONS_DIR="${TRAJ_SESSIONS_DIR:-$HOME/.sid-code/trajectories/sessions}"
UPLOAD_URL="${TRAJ_UPLOAD_URL:-https://www.sid-code.cc/traj}"
TOKEN="${TRAJ_UPLOAD_TOKEN:-}"
TOOL_SOURCE="${TRAJ_TOOL_SOURCE:-sid-code}"
MAX_RETRIES="${TRAJ_MAX_RETRIES:-3}"
# 可选：管理台的 Basic Auth 凭据（"user:pass"），仅用于「云端是否已存在」预检。
# 不给也能正常补传 —— 已存在的 traj 服务端返回 409，脚本按幂等成功处理。
BASIC_AUTH="${TRAJ_BASIC_AUTH:-}"

DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force)   FORCE=1 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  # 未显式给 token 时，从 sid-code 自己的配置里读（它就是上传端的真实来源）
  CFG="$HOME/.sid-code/settings.json"
  if [[ -f "$CFG" ]]; then
    TOKEN=$(python3 -c "
import json,sys
try:
    d=json.load(open('$CFG'))
    print((d.get('trace') or {}).get('upload',{}).get('token',''))
except Exception: print('')
")
  fi
fi
if [[ -z "$TOKEN" ]]; then
  echo "错误：缺少上传 token。设置 TRAJ_UPLOAD_TOKEN 或在 ~/.sid-code/settings.json 的 trace.upload.token 配置" >&2
  exit 1
fi

if [[ ! -d "$SESSIONS_DIR" ]]; then
  echo "错误：会话目录不存在: $SESSIONS_DIR" >&2
  exit 1
fi

# 健康检查：服务端不可达就别浪费时间逐个超时
health=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$UPLOAD_URL/api/v1/health" || echo 000)
if [[ "$health" != "200" ]]; then
  echo "错误：服务端不可达（$UPLOAD_URL/api/v1/health → HTTP $health）" >&2
  exit 1
fi

echo "════════════════════════════════════════════════════════════"
echo " sid-code 轨迹补传"
echo "   会话目录: $SESSIONS_DIR"
echo "   上传地址: $UPLOAD_URL"
echo "   tool_source: $TOOL_SOURCE"
[[ $DRY_RUN == 1 ]] && echo "   模式: DRY-RUN（不实际上传）"
[[ $FORCE == 1 ]] && echo "   模式: FORCE（云端已存在也重传）"
echo "════════════════════════════════════════════════════════════"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 文件名 → file_type，对齐 uploader.ts 的 FILE_TYPE_MAP
declare -a FILES=("session.traj:traj" "raw.jsonl:raw" "events.jsonl:events")

total_sessions=0; skipped_sessions=0; done_sessions=0; failed_sessions=0
total_files=0; ok_files=0; fail_files=0; missing_files=0; exists_files=0

for sdir in "$SESSIONS_DIR"/*/; do
  [[ -d "$sdir" ]] || continue
  sid=$(basename "$sdir")
  total_sessions=$((total_sessions+1))

  # 云端已存在则跳过。只在提供了 Basic Auth 凭据时才做这个预检 ——
  # /trajectories/{sid} 走的是 verify_basic_auth（用户名口令），不认 X-Upload-Token，
  # 只带 token 查会恒得 401，预检永远不生效（实测 skipped 恒为 0）。
  # 没有凭据时不预检也无害：服务端对已存在的 traj 返回 409，下面按幂等成功处理。
  if [[ $FORCE == 0 && -n "$BASIC_AUTH" ]]; then
    code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' \
      -u "$BASIC_AUTH" "$UPLOAD_URL/api/v1/trajectories/$sid" 2>/dev/null || echo 000)
    if [[ "$code" == "200" ]]; then
      echo "⏭  $sid  云端已存在，跳过"
      skipped_sessions=$((skipped_sessions+1))
      continue
    fi
  fi

  echo "▶  $sid"
  session_ok=1

  for pair in "${FILES[@]}"; do
    fname="${pair%%:*}"
    ftype="${pair##*:}"
    fpath="$sdir$fname"

    if [[ ! -f "$fpath" ]]; then
      echo "     $fname: 不存在，跳过"
      missing_files=$((missing_files+1))
      continue
    fi

    total_files=$((total_files+1))
    gz="$TMP/$fname.gz"
    gzip -c "$fpath" > "$gz"
    sha=$(shasum -a 256 "$gz" | cut -d' ' -f1)
    size=$(stat -f%z "$gz" 2>/dev/null || stat -c%s "$gz")

    if [[ $DRY_RUN == 1 ]]; then
      echo "     $fname: [dry-run] ${size}B gz, type=$ftype"
      ok_files=$((ok_files+1))
      continue
    fi

    uploaded=0
    for ((a=1; a<=MAX_RETRIES; a++)); do
      body=$(curl -s -m 120 -w $'\n%{http_code}' \
        -X POST "$UPLOAD_URL/api/v1/upload/session-file" \
        -H "X-Upload-Token: $TOKEN" \
        -H "X-Content-SHA256: $sha" \
        -F "file=@$gz;filename=$fname.gz;type=application/gzip" \
        -F "session_id=$sid" \
        -F "file_type=$ftype" \
        -F "tool_source=$TOOL_SOURCE" 2>/dev/null)
      http="${body##*$'\n'}"
      payload="${body%$'\n'*}"

      if [[ "$http" == "200" ]]; then
        status=$(echo "$payload" | python3 -c "
import sys,json
try: print(json.load(sys.stdin).get('status','ok'))
except Exception: print('ok')
" 2>/dev/null || echo ok)
        echo "     $fname: ✅ $status (${size}B gz)"
        ok_files=$((ok_files+1)); uploaded=1; break
      fi

      # 409 = 服务端已有同名 traj。这是**幂等成功**，不是失败 ——
      # uploader.ts:296 自己就把 409 判成 status="skipped"，这里必须对齐它的口径，
      # 否则重复跑脚本会把"上次已经传成功"报成一片失败（实测踩过：154/155 里那 1 个）。
      if [[ "$http" == "409" ]]; then
        echo "     $fname: ⏭  已存在（幂等跳过）"
        exists_files=$((exists_files+1)); uploaded=1; break
      fi

      # 401 = token 不对，重试多少次都一样，直接失败并提示
      if [[ "$http" == "401" ]]; then
        echo "     $fname: ❌ 认证失败（token 无效），不重试"
        fail_files=$((fail_files+1)); session_ok=0; break
      fi

      if (( a < MAX_RETRIES )); then
        sleep $((a*2))
      else
        echo "     $fname: ❌ HTTP $http  $(echo "$payload" | head -c 200)"
        fail_files=$((fail_files+1)); session_ok=0
      fi
    done
    rm -f "$gz"
  done

  if [[ $session_ok == 1 ]]; then
    done_sessions=$((done_sessions+1))
  else
    failed_sessions=$((failed_sessions+1))
  fi
done

echo "════════════════════════════════════════════════════════════"
echo " 会话:  共 $total_sessions   成功 $done_sessions   跳过 $skipped_sessions   失败 $failed_sessions"
echo " 文件:  尝试 $total_files   新传 $ok_files   已存在 $exists_files   失败 $fail_files   缺失 $missing_files"
echo "════════════════════════════════════════════════════════════"
# 只有真失败才非零退出。「已存在」是幂等成功，重复跑脚本时它会是大多数 ——
# 把它算进失败会让「补传已完成」这件事看起来像出错了。
[[ $fail_files -gt 0 ]] && exit 1
exit 0
