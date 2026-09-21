#!/usr/bin/env bash
# PreToolUse hook(matcher `Agent|Task`。Claude Code 側 .claude/settings.json のみに配線。gate-hooks.json には
# 配線しない=VS Code は matcher を無視し Copilot の runSubagent は入力形が異なるため):
# サブエージェント呼出の model パラメータを役割別モデル方針(.github/harness/model-policy.yml)と照合する。
# 三層強制(permissions.deny / 本フック / PostToolUse・SubagentStop の記録)の第2層(監査 2026-09-09 §4.5)。
# 判定:
#   - tool_input.model が方針外(候補外モデル or 役割の許可集合に無い名前)なら deny
#   - model 省略: 役割の既定が inherit なら素通し({"continue": true})。既定が具体モデルの役割のみ
#     updatedInput で tool_input 全体を複製し model だけ差し替えて注入(updatedInput は入力全体を置換する)
#   - 非 Agent/Task ツール・subagent_type 欠落・表に無い役割・表が読めない・壊れた JSON は無出力 exit 0
#     (fail-open。CI windows の固定ペイロード {tool_input:{command,file_path,content}} でも空出力)
# 役割表は tools/generate-adapters.py が下のマーカー間に埋め込む(手で書かない)。
# テスト用: GUARD_SUBAGENT_MODEL_TABLE=<json ファイル> で表を差し替えられる(fail-open の範囲内)。
# JSON 解析は node → python(構造比較を含むため grep フォールバックは持たない=壊れた JSON は無出力)。
input=$(cat)

# 判定ログ(_log.sh。session_id 等は $input から拾う。無ければ無記録=fail-open)
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }

# BEGIN GENERATED: model-policy-role-table
role_table='{"deny_exact":["haiku"],"deny_prefix":["claude-haiku-4-5"],"generated_from":".github/harness/model-policy.yml","retrieved":"2026-09-10","roles":{"reviewer":{"allowed_exact":["inherit","fable","opus","sonnet"],"allowed_prefix":["claude-fable-5-1","claude-opus-5","claude-sonnet-5","claude-fable-5","claude-mythos","claude-opus-4","claude-sonnet-4"],"default":"inherit"},"spec-critic":{"allowed_exact":["inherit","fable","opus","sonnet"],"allowed_prefix":["claude-fable-5-1","claude-opus-5","claude-sonnet-5","claude-fable-5","claude-mythos","claude-opus-4","claude-sonnet-4"],"default":"inherit"},"task-worker":{"allowed_exact":["inherit","fable","opus","sonnet"],"allowed_prefix":["claude-fable-5-1","claude-opus-5","claude-sonnet-5","claude-fable-5","claude-mythos","claude-opus-4","claude-sonnet-4"],"default":"inherit"}}}'
# END GENERATED: model-policy-role-table

if [[ -n "${GUARD_SUBAGENT_MODEL_TABLE:-}" && -r "${GUARD_SUBAGENT_MODEL_TABLE}" ]]; then
  role_table=$(cat "${GUARD_SUBAGENT_MODEL_TABLE}" 2>/dev/null) || exit 0
fi

# 判定器は「1行目 = 種別<TAB>役割<TAB>モデル、2行目以降 = 出力 JSON」を返す(種別: skip/allow/deny/inject)。
# node と python は同じ規則を実装する(sh/ps1 と同じく挙動を揃える)。
verdict=""
if command -v node >/dev/null 2>&1; then
  verdict=$(printf '%s' "$input" | ROLE_TABLE="$role_table" node -e '
try {
  const fs = require("fs");
  const j = JSON.parse(fs.readFileSync(0, "utf8"));
  const t = JSON.parse(process.env.ROLE_TABLE || "{}");
  const out = (s) => process.stdout.write(s);
  const tn = j.tool_name;
  if (tn !== undefined && tn !== null && tn !== "Agent" && tn !== "Task") { out("skip\n"); process.exit(0); }
  const ti = (j.tool_input && typeof j.tool_input === "object" && !Array.isArray(j.tool_input)) ? j.tool_input : null;
  if (!ti) { out("skip\n"); process.exit(0); }
  const role = ti.subagent_type;
  const roles = t.roles || {};
  if (typeof role !== "string" || !Object.prototype.hasOwnProperty.call(roles, role)) { out("skip\n"); process.exit(0); }
  const r = roles[role];
  const fmt = (o) => JSON.stringify(o, null, 1).replace(/\n\s*/g, " ");
  const model = ti.model;
  if (model === undefined || model === null || model === "") {
    const d = r["default"];
    if (!d || d === "inherit") { out("allow\t" + role + "\t\n"); process.exit(0); }
    const u = Object.assign({}, ti, { model: d });
    out("inject\t" + role + "\t" + d + "\n" + fmt({ continue: true, hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow", updatedInput: u } }) + "\n");
    process.exit(0);
  }
  const m = String(model);
  const hit = (exact, prefix) => (exact || []).indexOf(m) >= 0 || (prefix || []).some((p) => p && (m === p || m.indexOf(p) === 0));
  let reason = null;
  if (hit(t.deny_exact, t.deny_prefix)) reason = "候補外のモデル";
  else if (!hit(r.allowed_exact, r.allowed_prefix)) reason = "方針に無いモデル名";
  if (reason) {
    const why = "サブエージェント " + role + " への model=" + m + " は役割別モデル方針(.github/harness/model-policy.yml)で許可されていません(" + reason + ")。model を省略して方針の既定(inherit)を使うか、方針を変更して python tools/generate-adapters.py を再実行してください。";
    out("deny\t" + role + "\t" + m + "\n" + fmt({ continue: true, hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: why } }) + "\n");
    process.exit(0);
  }
  out("allow\t" + role + "\t" + m + "\n");
} catch (e) { }
' 2>/dev/null)
fi
if [[ -z "$verdict" ]] && { command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; }; then
  # 解決順は run-python.sh と同じ python→python3(Windows の Store スタブ python3 誤検出回避)
  py=$(command -v python 2>/dev/null || command -v python3)
  verdict=$(printf '%s' "$input" | ROLE_TABLE="$role_table" PYTHONIOENCODING=utf-8 "$py" -c '
import sys, json, os
def out(s):
    sys.stdout.buffer.write(s.encode("utf-8"))
try:
    j = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    t = json.loads(os.environ.get("ROLE_TABLE") or "{}")
    tn = j.get("tool_name") if isinstance(j, dict) else None
    if not isinstance(j, dict) or (tn is not None and tn not in ("Agent", "Task")):
        out("skip\n"); sys.exit(0)
    ti = j.get("tool_input")
    if not isinstance(ti, dict):
        out("skip\n"); sys.exit(0)
    role = ti.get("subagent_type")
    roles = t.get("roles") or {}
    if not isinstance(role, str) or role not in roles:
        out("skip\n"); sys.exit(0)
    r = roles[role]
    fmt = lambda o: json.dumps(o, ensure_ascii=False)
    model = ti.get("model")
    if model is None or model == "":
        d = r.get("default")
        if not d or d == "inherit":
            out("allow\t" + role + "\t\n"); sys.exit(0)
        u = dict(ti); u["model"] = d
        out("inject\t" + role + "\t" + d + "\n" + fmt({"continue": True, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": u}}) + "\n")
        sys.exit(0)
    m = str(model)
    def hit(exact, prefix):
        return m in (exact or []) or any(p and (m == p or m.startswith(p)) for p in (prefix or []))
    reason = None
    if hit(t.get("deny_exact"), t.get("deny_prefix")):
        reason = "候補外のモデル"
    elif not hit(r.get("allowed_exact"), r.get("allowed_prefix")):
        reason = "方針に無いモデル名"
    if reason:
        why = "サブエージェント " + role + " への model=" + m + " は役割別モデル方針(.github/harness/model-policy.yml)で許可されていません(" + reason + ")。model を省略して方針の既定(inherit)を使うか、方針を変更して python tools/generate-adapters.py を再実行してください。"
        out("deny\t" + role + "\t" + m + "\n" + fmt({"continue": True, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}) + "\n")
        sys.exit(0)
    out("allow\t" + role + "\t" + m + "\n")
except SystemExit:
    raise
except Exception:
    pass
' 2>/dev/null)
fi
verdict=${verdict//$'\r'/}
[[ -z "$verdict" ]] && exit 0
head=${verdict%%$'\n'*}
body=""
[[ "$verdict" == *$'\n'* ]] && body=${verdict#*$'\n'}
IFS=$'\t' read -r kind role model <<<"$head"
case "$kind" in
  allow)
    printf '%s\n' '{"continue": true}' ;;
  deny)
    hook_log deny "${role} model=${model}"
    printf '%s\n' "$body" ;;
  inject)
    hook_log inject "${role} model=${model}"
    printf '%s\n' "$body" ;;
  *)
    exit 0 ;;
esac
