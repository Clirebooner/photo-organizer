#!/usr/bin/env bash
#
# watcher --execute 隔离集成验证（可复现测试资产）
#
# 运行说明：
#   从仓库根目录运行：  bash tests/watch_execute_verify.sh
#   脚本会通过 git rev-parse 从自身位置自动定位仓库根，从任意目录运行亦可。
#
# 行为：
#   - 在 /tmp 创建独立临时目录（mktemp），inbox / dest / watch-state / executor 日志全部隔离在内；
#   - 全断言通过：自动清理本轮临时目录；
#   - 任一断言失败：保留临时目录作为现场，并打印检查指引与删除命令；
#   - 强制使用临时 --state，不碰 ~/.cache/photo-organizer/；
#   - 全程禁止触碰（仅只读 ls -ld 复核）：
#       /mnt/d/Photography_Progress_Test
#       /mnt/d/Photography_Progress_Test_inbox
#
# 目标（7 条）：
#   1) settle+quiet 后真实复制到临时目标库
#   2) 状态文件只在 execute 模式创建，且含 done 记录
#   3) 重启 watcher 后，相同 size/mtime 的已处理文件不再触发批次或复制
#   4) 目标文件存在且内容与源一致
#   5) 不修改仓库（executor 日志隔离到临时 config）、不提交、不 push
#   6) 不使用/修改上述两个受保护目录
#   7) 全断言通过才清理临时目录；失败保留现场
#
# 日志隔离：$RUN/cfg/config/default.toml 把 log_path 指到 $RUN/executor.log，
#   并用子 shell cd + exec 启动，进程 PID 不变、wait 可用。
# 夹具：2 个纯视频（photoA.mov / photoB.mp4，各 7 字节，无 EXIF、无 GPS -> 零网络）。
# 目标路径不硬编码日期：A5 用 find 动态定位后断言唯一、位于 Unknown_Location/RAW、cmp 与源一致。
#
# 维护提示：start_watcher / stop_watcher 必须在主 shell 调用（不可放进 $(...) 命令替换），
#   否则后台 watcher 变成命令替换子 shell 的孩子，wait 返回 127（not a child of this shell）。

set -u   # 不用 set -e：所有断言都要执行到底

# 定位仓库根：优先 git rev-parse；脚本必须位于仓库内
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR" && git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
    echo "错误：无法从脚本位置确定仓库根（$SCRIPT_DIR 不在 git 仓库内）" >&2
    exit 1
fi
cd "$REPO_ROOT"
SCRIPT_REL=${SCRIPT_DIR#"$REPO_ROOT/"}    # 脚本在仓库内的相对目录，如 "tests"

ENTRY="$REPO_ROOT/.venv/bin/photo-organizer"
PYTHON="$REPO_ROOT/.venv/bin/python3"
if [ ! -x "$ENTRY" ] || [ ! -x "$PYTHON" ]; then
    echo "错误：缺少 $ENTRY 或 $PYTHON" >&2
    exit 1
fi

RUN=$(mktemp -d -t watchvfy.XXXXXX)
INBOX=$RUN/inbox
DEST=$RUN/dest
STATE=$RUN/watch_state.json
OUT1=$RUN/watch1.out
OUT2=$RUN/watch2.out
CFG_HOME=$RUN/cfg

PASS=0
FAIL=0
ok()  { echo "PASS  $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL  $1"; FAIL=$((FAIL + 1)); }
expect_eq() {
    # expect_eq <描述> <实际> <期望>
    if [ "$2" = "$3" ]; then ok "$1（=$2）"; else bad "$1（=$2，期望 $3）"; fi
}

cleanup() {
    if [ "$FAIL" -gt 0 ]; then
        echo ""
        echo "=== 存在 FAIL：临时目录已保留（现场） ==="
        echo "  查看 watcher 输出：cat $OUT1 $OUT2"
        echo "  查看 executor 日志：cat $RUN/executor.log"
        echo "  查看状态文件：cat $STATE"
        echo "  查看目标库：find $DEST -type f"
        echo "  检查后手动删除：rm -rf $RUN"
    else
        rm -rf "$RUN"
        echo "全部断言通过，临时目录已清理"
    fi
}
trap cleanup EXIT

start_watcher() {
    # start_watcher <输出文件>；后台启动 watcher，PID 存入全局 WATCH_PID
    local out=$1
    ( cd "$CFG_HOME" && exec env PYTHONUNBUFFERED=1 "$ENTRY" watch "$INBOX" "$DEST" \
        --execute --interval 1 --settle 2 --quiet 5 --state "$STATE" \
        >"$out" 2>&1 ) &
    WATCH_PID=$!
}

stop_watcher() {
    # stop_watcher <PID> <输出文件>；退出码存全局 STOP_CODE，最后一行存 STOP_LAST
    # 注意：必须在主 shell 调用（不能放进 $(...) 命令替换），否则 wait 找不到该子进程。
    local pid=$1 out=$2
    kill -TERM "$pid"
    for _ in $(seq 1 30); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "… 未在 6s 内优雅退出，SIGKILL 兜底" >&2
        kill -KILL "$pid"
    fi
    wait "$pid"
    STOP_CODE=$?
    STOP_LAST=$(tail -n 1 "$out")
}

check_git_clean() { # check_git_clean <前/后>：除脚本自身（未跟踪）外无任何改动
    local label=$1
    local dirty
    dirty=$(git status --porcelain | grep -vF "?? $SCRIPT_REL/watch_execute_verify.sh" || true)
    if [ -z "$dirty" ]; then ok "git 干净（$label）"; else bad "git 干净（$label）"; fi
}

# ---- 阶段 A0：前置（受保护目录 + git 干净） ----
if ls -ld /mnt/d/Photography_Progress_Test /mnt/d/Photography_Progress_Test_inbox >/dev/null 2>&1; then
    ok "受保护目录存在（前）"
else
    bad "受保护目录存在（前）"
fi
check_git_clean "前"

mkdir -p "$INBOX" "$CFG_HOME/config"
cat > "$CFG_HOME/config/default.toml" <<EOF
inbox = "~/Pictures/Inbox"
dest_root = "~/Pictures/Organized"
mode = "copy"
dry_run = false
log_path = "$RUN/executor.log"
EOF
printf 'history-media-bytes' > "$INBOX/history.mov"   # 启动前放入 -> 历史，永不处理

# ---- 阶段 A1：启动 watcher1（--execute），等 banner ----
start_watcher "$OUT1"
PID1=$WATCH_PID
sleep 8
if grep -q 'quiet=5s, execute' "$OUT1"; then ok "A1 banner 含 execute 与正确时序"; else bad "A1 banner 含 execute 与正确时序"; fi
if grep -q 'dry-run' "$OUT1"; then bad "A1 banner 不含 dry-run"; else ok "A1 banner 不含 dry-run"; fi
if [ ! -e "$DEST" ]; then ok "A1 dest 尚不存在"; else bad "A1 dest 尚不存在"; fi
if [ ! -e "$STATE" ]; then ok "A1 state 尚不存在"; else bad "A1 state 尚不存在"; fi

# ---- 阶段 A2：首轮基线（历史不产生批次；execute 下也只有处理批次才写 state） ----
n=$(grep -cE '^\[batch\] [0-9]+ file' "$OUT1" || true)
expect_eq "A2 首轮基线：历史不产生批次" "$n" "0"
if [ ! -e "$STATE" ]; then ok "A2 state 仍不存在"; else bad "A2 state 仍不存在"; fi
if [ ! -e "$DEST" ]; then ok "A2 dest 仍不存在"; else bad "A2 dest 仍不存在"; fi

# ---- 阶段 A3：放入两个纯视频夹具 + 等待 settle+quiet ----
printf 'photo-A' > "$INBOX/photoA.mov"
printf 'photo-B' > "$INBOX/photoB.mp4"
mkdir -p "$RUN/refs"
cp "$INBOX/photoA.mov" "$INBOX/photoB.mp4" "$RUN/refs/"
sleep 10
n=$(grep -cE '^\[batch\] [0-9]+ file' "$OUT1" || true)
expect_eq "A3 恰好一个批次" "$n" "1"
for f in photoA.mov photoB.mp4; do
    if grep -q "$f" "$OUT1"; then ok "A3 批次含 $f"; else bad "A3 批次含 $f"; fi
done
if grep -q 'success:2 failed:0 skipped:0' "$OUT1"; then ok "A3 复制成功 2/失败 0/跳过 0"; else bad "A3 复制成功 2/失败 0/跳过 0"; fi

# ---- 阶段 A4：优雅停止 watcher1 ----
stop_watcher "$PID1" "$OUT1"
expect_eq "A4 退出码=0（优雅停止）" "$STOP_CODE" "0"
expect_eq "A4 停止后最后一行 Stopped." "$STOP_LAST" "Stopped."

# ---- 阶段 A5：execute 结果断言（动态 find 定位目标，不硬编码日期） ----
if [ -d "$DEST" ]; then ok "A5 目标库已创建"; else bad "A5 目标库已创建"; fi
for name in photoA.mov photoB.mp4; do
    n=$(find "$DEST" -type f -name "$name" 2>/dev/null | grep -c . || true)
    expect_eq "A5 目标唯一：$name" "$n" "1"
    dest=$(find "$DEST" -type f -name "$name" 2>/dev/null | head -n 1)
    case "$dest" in
        *Unknown_Location/RAW/*) ok "A5 $name 位于 Unknown_Location/RAW" ;;
        *) bad "A5 $name 位于 Unknown_Location/RAW（实际：$dest）" ;;
    esac
    if [ -n "$dest" ] && cmp -s "$INBOX/$name" "$dest"; then
        ok "A5 $name 与源一致"
    else
        bad "A5 $name 与源一致"
    fi
done
n=$(find "$DEST" -name '*.part-*' 2>/dev/null | grep -c . || true)
expect_eq "A5 无 .part- 残留" "$n" "0"

STOUT=$("$PYTHON" - "$STATE" "$INBOX" <<'PY'
import json, sys
from pathlib import Path
state_path, inbox = sys.argv[1], sys.argv[2]
data = json.load(open(state_path))
fs = data.get("files", {})
print("count", len(fs))
print("names", " ".join(sorted(Path(k).name for k in fs)))
print("all_done", all(v.get("status") == "done" for v in fs.values()))
for name in ("photoA.mov", "photoB.mp4"):
    p = Path(inbox) / name
    st = p.stat()
    e = fs.get(str(p))
    done = e is not None and e.get("status") == "done" and e.get("size") == st.st_size and abs(e.get("mtime", 0.0) - st.st_mtime) < 1e-6
    print(f"is_done:{name}", done)
print("has_history", str(Path(inbox) / "history.mov") in fs)
PY
)
cnt=$(printf '%s\n' "$STOUT" | sed -n 's/^count //p')
expect_eq "A5 state 条目数=2" "$cnt" "2"
if printf '%s\n' "$STOUT" | grep -q '^names photoA\.mov photoB\.mp4$'; then ok "A5 state 只含两个新文件"; else bad "A5 state 只含两个新文件"; fi
if printf '%s\n' "$STOUT" | grep -q '^all_done True$'; then ok "A5 state 全部 status=done"; else bad "A5 state 全部 status=done"; fi
for name in photoA.mov photoB.mp4; do
    if printf '%s\n' "$STOUT" | grep -q "^is_done:$name True$"; then ok "A5 is_done:$name（size/mtime 一致）"; else bad "A5 is_done:$name（size/mtime 一致）"; fi
done
if printf '%s\n' "$STOUT" | grep -q '^has_history False$'; then ok "A5 history.mov 不在 state"; else bad "A5 history.mov 不在 state"; fi

for f in photoA.mov photoB.mp4; do
    if cmp -s "$INBOX/$f" "$RUN/refs/$f"; then ok "A5 源文件未动：$f"; else bad "A5 源文件未动：$f"; fi
done

# ---- 阶段 B：重启验证不重复处理 ----
STHASH_BEFORE=$(sha256sum "$STATE" | awk '{print $1}')

start_watcher "$OUT2"             # B1：同一 --state，--execute
PID2=$WATCH_PID
sleep 8                            # B2：等待超过 quiet=5
n=$(grep -cE '^\[batch\] [0-9]+ file' "$OUT2" || true)
expect_eq "B2 重启后无批次触发" "$n" "0"
n=$(find "$DEST" -type f 2>/dev/null | grep -c . || true)
expect_eq "B2 dest 仍 2 个文件（无重复复制）" "$n" "2"
for name in photoA.mov photoB.mp4; do
    if find "$DEST" -type f -name "$name" 2>/dev/null | grep -q .; then ok "B2 $name 目标仍存在"; else bad "B2 $name 目标仍存在"; fi
done
STHASH_AFTER=$(sha256sum "$STATE" | awk '{print $1}')
expect_eq "B2 state 文件未变（无重触发写入）" "$STHASH_AFTER" "$STHASH_BEFORE"

stop_watcher "$PID2" "$OUT2"          # B3：优雅停止
expect_eq "B3 退出码=0（优雅停止）" "$STOP_CODE" "0"
expect_eq "B3 停止后最后一行 Stopped." "$STOP_LAST" "Stopped."

# ---- 收尾：受保护目录 + git 干净 ----
if ls -ld /mnt/d/Photography_Progress_Test /mnt/d/Photography_Progress_Test_inbox >/dev/null 2>&1; then
    ok "受保护目录存在（后）"
else
    bad "受保护目录存在（后）"
fi
check_git_clean "后"

echo "------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
