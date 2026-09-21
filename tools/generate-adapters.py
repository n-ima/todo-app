"""Copilot正レイヤ(.github/)からClaude Code/Antigravity用の薄いアダプタを再生成する。

アダプタは振る舞いを持たず、正のファイルへのポインタのみ(等価性ルールのプラットフォーム拡張)。
冪等: 再実行すると全アダプタを上書き再生成する。

使い方(リポジトリルートで):
    python tools/generate-adapters.py

いつ実行するか:
- .github/prompts/ にプロンプトを追加・改名した / description を変えたとき
- .github/skills/ にスキルを追加した / description を変えたとき
- .github/agents/ のサブエージェント(reviewer/spec-critic/task-worker)の description か本文を変えたとき
  (第2節 = tools/subagent_adapters.py。.claude/agents/<role>.md は正の本文を展開した生成物。A5-6)
- .github/harness/model-policy.yml(役割別モデル/effort 方針)を変えたとき(第4節。
  --check で生成物の差分検査、--print-deny / --apply-deny で permissions.deny の Agent(model:…) 規則。
  引数付きの起動は tools/model_policy.py に委譲されアダプタは書かない)
- .github/prompts/ を変えたときは Copilot Agent Host 向けの入口スキル
  .github/skills/<nn>-<name>/SKILL.md も再生成される(第5節 = tools/copilot_entry_skills.py。
  Agent Host は prompt files を読まないため。--check は第4〜6節すべての鮮度を返す。
  Claude Code 側の .claude/skills/<nn>-*/ ポインタは作らない=.claude/commands とスラッシュ名が衝突する)
- plugin.json(Agent Plugins 1.0 マニフェスト。名前・版・説明)や .claude/settings.json の hooks を
  変えたとき(第6節。.claude-plugin/plugin.json と .claude-plugin/hooks.json を再生成する。
  --check は第4〜6節すべての差分を検査する。tools/plugin_manifests.py)
- .github/instructions/<name>.instructions.md(Copilot のパス限定指示。applyTo:)を追加・変更したとき
  (第7節。Claude Code のパス限定ルール .claude/rules/<name>.md(paths:)を生成する。tools/path_rules.py。
  --check は第4〜7節すべての差分を検査する。A6-19b(4) / A7-M-8)
- --check は第1・3節のポインタ型アダプタ(.claude/commands・.claude/skills・.agents/workflows)の本文ドリフトも
  検査する(生成結果と現状の比較。書かない。第2節 .claude/agents は正の本文展開で tools/subagent_adapters.py の
  cmd_check と validate (z) が見る)。--check-adapters は第1・3節だけ(validate-harness (s) が呼ぶ。A7-M-6)

実行後は tools/validate-harness.py で乖離が無いことを確認する。
依存: Python 3.x + PyYAML (pip install pyyaml)
"""
import re
import os
import sys
import glob

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows コンソール(cp932)でも STALE 行の日本語が化けないよう UTF-8 に固定する(再監査 2026-09-09 RG-15。gen-docs.py と同じ)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# ---- 0. model-policy 専用モード(第4節の検査・deny 出力。アダプタは書かない) ----
# --check / --print-deny / --apply-deny / --print-prices / --print-role-table / --selftest は
# tools/model_policy.py に委譲して終了する(「検査」が冪等な再生成でも書込を伴うのは誤りのため)。
# 引数なし(通常モード)は従来どおり第1〜3節に加えて末尾の第4〜7節を実行する。
# 第1・3節(ポインタ型アダプタ .claude/commands・.claude/skills・.agents/workflows)の鮮度検査(A7-M-6「アダプタ＝ポインタのみ」の
# 本文ドリフト。再監査 2026-08-31 §6 CI・復旧): --check は第4〜7節の検査に続けて第1・3節を比較モード
# (write() が書かずに差分だけ数える)で通す。--check-adapters は第1・3節だけ(tools/validate-harness.py (s) が呼ぶ。
# 第2節 .claude/agents は本文展開で subagent_adapters.cmd_check / validate (z)、第4〜7節は validate が (j)/(l)/(m)/(x) で見る)。
# 生成ロジックはこの 1 本だけ(比較用の複製を持たない)。
CHECK_ONLY = False
_CHECK_RC = 0
if len(sys.argv) > 1 and sys.argv[1] == "--check-adapters":
    CHECK_ONLY = True
elif len(sys.argv) > 1:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import model_policy  # noqa: E402
    rc = model_policy.main(sys.argv[1:])
    # --check / --selftest は第5節(Copilot 入口スキル。tools/copilot_entry_skills.py)と
    # 第6節(plugin manifests。tools/plugin_manifests.py)にも掛ける
    # (CI の `generate-adapters.py --check` 1 本で第4〜6節すべての鮮度を見る。終了コードは大きい方=0/1/2 の意味は第4節と同じ)
    if sys.argv[1] in ("--check", "--selftest"):
        import copilot_entry_skills  # noqa: E402
        import plugin_manifests  # noqa: E402
        import subagent_adapters  # noqa: E402
        import path_rules  # noqa: E402  第7節(パス限定ルール。第3波 A6-19b(4))
        if sys.argv[1] == "--check":
            rc = max(rc, copilot_entry_skills.cmd_check(ROOT), plugin_manifests.cmd_check(ROOT), subagent_adapters.cmd_check(ROOT),
                     path_rules.cmd_check(ROOT))
        else:
            rc = max(rc, copilot_entry_skills.selftest(), plugin_manifests.selftest(), subagent_adapters.selftest(), path_rules.selftest())
    if sys.argv[1] == "--check":
        CHECK_ONLY, _CHECK_RC = True, rc  # 第1・3節も比較モードで通してから終了する(第4節の手前の finish_check)
    else:
        sys.exit(rc)


def read_frontmatter(path):
    # utf-8-sig: エディタが BOM 付きで保存しても frontmatter の先頭一致が壊れないようにする
    text = open(path, encoding="utf-8-sig").read()
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    fm = yaml.safe_load(m.group(1)) if m else None
    if fm is None:
        sys.exit(f"ERROR: {os.path.relpath(path, ROOT)}: frontmatter を読めません"
                 "(先頭に --- で囲まれた YAML が必要)")
    return fm


_CHECK_STALE = []   # 比較モードで差分(未生成を含む)があったアダプタの相対パス
_CHECK_SEEN = 0     # 比較モードで見たアダプタ数
_CHECK_POLICY = {}  # 比較モードで 1 回だけ読む model-policy(第4節が第1節の出力に差し込む行を再現するため)


def _policy_transform(rel, content):
    """第4節(tools/model_policy.py)が第1節の出力へ差し込む行(.claude/commands の phase_overrides の effort)を比較前に
    同じ関数で適用する。最終状態の正は「第1・3節の出力 → 第4節の変換」(.claude/agents は第2節=本文展開の生成物で、
    第4節の model/effort 差し込みまで含めた鮮度は tools/subagent_adapters.py の status() / validate (z) が見る)。"""
    if not rel.startswith(".claude/commands/"):
        return content
    if "p" not in _CHECK_POLICY:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import model_policy as _mp  # noqa: E402
            _CHECK_POLICY["p"] = _mp.load_policy(ROOT)
            _CHECK_POLICY["mod"] = _mp
        except Exception as e:  # noqa: BLE001
            print(f"WARN: model-policy を読めないため .claude/commands は第4節の変換なしで比較する: {e}")
            _CHECK_POLICY["p"] = None
    p, mp = _CHECK_POLICY.get("p"), _CHECK_POLICY.get("mod")
    if p is None:
        return content
    name = os.path.basename(rel)[:-3]
    ov = (p.get("phase_overrides") or {}).get(name)
    return mp.render_command(content, ov.get("effort") if isinstance(ov, dict) else None, rel)


def _check_target(path, content):
    """比較モード: 生成結果と現状を EOL 差を無視して比べる(model_policy の _same_text と同じ意味論)。書かない。"""
    global _CHECK_SEEN
    _CHECK_SEEN += 1
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    content = _policy_transform(rel, content).replace("\r\n", "\n")
    try:
        cur = open(path, encoding="utf-8-sig", newline="").read().replace("\r\n", "\n")
    except OSError:
        cur = None
    if cur is None:
        _CHECK_STALE.append(rel)
        print(f"MISSING: {rel} (python tools/generate-adapters.py で生成)")
    elif cur != content:
        _CHECK_STALE.append(rel)
        print(f"STALE: {rel} (ポインタ型アダプタが正とずれている。python tools/generate-adapters.py で再生成。A7-M-6)")


def finish_check(rc_before):
    """比較モードの集計と終了コード(0/1。第4〜7節の rc と大きい方)。"""
    bad = len(_CHECK_STALE)
    print(f"pointer-adapters --check: {'NG' if bad else 'OK'} (対象 {_CHECK_SEEN} ファイル中 差分 {bad})")
    return max(rc_before, 1 if bad else 0)


def write(path, content):
    if CHECK_ONLY:
        _check_target(path, content)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote:", os.path.relpath(path, ROOT))


def handoff_guidance(handoffs, session_word, command_word):
    """バインド先エージェントの handoffs を、ハンドオフボタンが無い環境向けの案内文に変換する。

    send: false → 従来どおり「新しい<セッション>で /<コマンド> を実行」の案内。
    send: true  → 同一セッション内で次エージェントの役割に切り替えて自動継続
                  (ノンストップ設計を環境差で失わないため)。
    """
    sf = [h.get("label", h.get("agent", "")) for h in handoffs if not h.get("send")]
    st = [h.get("label", h.get("agent", "")) for h in handoffs if h.get("send")]
    if not st:
        return (f"ハンドオフボタンは存在しないため、フェーズ移行の案内は\n"
                f"   「新しい{session_word}で /<{command_word}> を実行」の形にしてください。")
    lines = ["ハンドオフボタンは存在しないため、ハンドオフは次のように読み替えてください。"]
    if sf:
        lines.append(f"   - `send: false` のハンドオフ({' / '.join(sf)}): フェーズ移行の案内を")
        lines.append(f"     「新しい{session_word}で /<{command_word}> を実行」の形にしてください。")
    lines.append(f"   - `send: true` のハンドオフ({' / '.join(st)}): このフェーズ完了後は止まらず、")
    lines.append(f"     同一{session_word}内で次エージェント(/<{command_word}> 相当)の役割に")
    lines.append(f"     切り替えて自動継続してください。新しい{session_word}の案内はしません")
    lines.append("     (会話が既に長い場合のみ `.github/harness/USAGE.md` のセッション分割表に従う)。")
    return "\n".join(lines)


# ---- 1. Claude Code commands (.claude/commands/) & Antigravity workflows (.agents/workflows/)
prompt_files = sorted(glob.glob(os.path.join(ROOT, ".github", "prompts", "*.prompt.md")))
for pf in prompt_files:
    fm = read_frontmatter(pf)
    base = os.path.basename(pf).replace(".prompt.md", "")
    agent = fm.get("agent")
    if not agent:
        sys.exit(f"ERROR: {os.path.relpath(pf, ROOT)}: frontmatter に 'agent' がありません"
                 "(プロンプトは必ずエージェントにバインドする)")
    desc = fm["description"]
    prompt_rel = f".github/prompts/{os.path.basename(pf)}"
    agent_rel = f".github/agents/{agent}.agent.md"
    agent_fm = read_frontmatter(os.path.join(ROOT, ".github", "agents", f"{agent}.agent.md"))
    handoffs = agent_fm.get("handoffs") or []
    cmd_handoff_note = handoff_guidance(handoffs, "セッション", "コマンド名")
    wf_handoff_note = handoff_guidance(handoffs, "エージェント会話", "ワークフロー名")

    cmd = f"""---
description: '{desc}'
---

このコマンドは薄いアダプタです。振る舞いの正は参照先にあります。

1. `{agent_rel}` を読み、その役割定義に従ってこの会話のロールを設定してください。
2. その上で `{prompt_rel}` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、Claude Code では **Agent ツール(旧称 Task)**で
   `.claude/agents/` の同名サブエージェント(reviewer / task-worker / spec-critic)を
   呼ぶことに読み替えてください。{cmd_handoff_note}
"""
    write(os.path.join(ROOT, ".claude", "commands", f"{base}.md"), cmd)

    wf = f"""---
description: '{desc}'
---

このワークフローは薄いアダプタです。振る舞いの正は参照先にあります。

1. `{agent_rel}` を読み、その役割定義に従って振る舞ってください。
2. その上で `{prompt_rel}` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent`(独立コンテキストでのレビュー・実装分離)は、
   Antigravity では **Agent Manager で別のエージェント会話として実行**し、
   結果を受け取って続行することに読み替えてください(同一会話で続ける場合は、
   独立性が失われることをユーザーに伝えたうえで行うこと)。
   {wf_handoff_note}
4. フック(機械的ガードレール)はこの環境では発火しません(Antigravity IDEはプロジェクト内の
   スクリプトフックを読まない・実機検証済みの知見)。AGENTS.md の指示レベルのルール
   (テンプレート直接編集の禁止・push/tag等の事前確認・シークレット非記載)を自分の判断で
   厳守してください。機械的な保護が必要な場合、ユーザーに IDE の Deny List
   (Settings → Permissions → Advanced)への危険コマンド登録を案内してください。
"""
    write(os.path.join(ROOT, ".agents", "workflows", f"{base}.md"), wf)

# ---- 2. Claude Code subagents (.claude/agents/): .github/agents/<role>.agent.md の本文展開(A5-6)。
# 公式: サブエージェントの本文は起動時にだけ読み込まれ、常駐するのは description だけ。旧ポインタ方式(「.github/agents を
# 読め」の 5 行)は常駐削減にならず Read 1 往復と読み飛ばしの余地だけを残していた。生成・検査の正は tools/subagent_adapters.py
# (model / effort 等の frontmatter は第4節 model-policy が差し込む。鮮度は --check と validate (z))。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subagent_adapters  # noqa: E402
if not CHECK_ONLY:  # 比較モード(--check / --check-adapters)では書かない。本節の鮮度は cmd_check(--check)と validate (z) が見る
    try:
        subagent_adapters.apply_all(ROOT, log=lambda rel: print("wrote:", rel))
    except subagent_adapters.AdapterError as e:
        sys.exit(f"ERROR: subagent-adapters: {e}")

# ---- 3. Claude Code skill adapters (.claude/skills/)
skill_files = sorted(glob.glob(os.path.join(ROOT, ".github", "skills", "*", "SKILL.md")))
# Copilot Agent Host 向けの入口スキル(.github/skills/<nn>-<name>/。第5節の生成物)にはポインタを作らない
# (/<nn>-… のスラッシュ名が第1節の .claude/commands/<nn>-*.md と衝突する。判定の正は tools/copilot_entry_skills.py。
# 作ってしまうと validate-harness.py (l) がポインタ衝突として ERROR にする)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import copilot_entry_skills  # noqa: E402
# 正の frontmatter にあれば Claude Code アダプタへそのまま転写するキー(監査 §5.3-4: harness-stats の
# user-invocable / disable-model-invocation は name/description しか通さない生成器で消えていた)
SKILL_PASSTHROUGH_KEYS = ("user-invocable", "disable-model-invocation", "allowed-tools", "shell")


def skill_frontmatter_extra(fm):
    lines = []
    for key in SKILL_PASSTHROUGH_KEYS:
        if key in fm:
            v = fm[key]
            lines.append(f"{key}: {'true' if v is True else 'false' if v is False else v}")
    return ("\n" + "\n".join(lines)) if lines else ""


for sf in skill_files:
    fm = read_frontmatter(sf)
    name = fm["name"]
    if copilot_entry_skills.is_entry_skill(name, fm):
        continue
    desc = fm["description"]
    src_text = open(sf, encoding="utf-8-sig").read()
    src_body = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", src_text, count=1, flags=re.DOTALL)
    if re.search(r"^!`[^`\n]+`[ \t]*$", src_body, re.M):
        # 行頭の動的コンテキスト注入(!`cmd` の単独行)はスキル本文にある時だけ起動時に実行されるため、
        # ポインタではなく本文を写す(正は .github 側。再生成で追従する)。地の文中の !` は対象外
        pointer = (f"<!-- generated from .github/skills/{name}/SKILL.md (本文に !`cmd` を含むため本文を転写。"
                   f"編集は正の側で行い python tools/generate-adapters.py で再生成する) -->\n\n{src_body.strip()}\n")
    else:
        pointer = (f"このスキルの本文の正は `.github/skills/{name}/SKILL.md` です。\n"
                   "それを読み、その手順・チェックリストに従ってください(このファイルはClaude Codeが\n"
                   "`.claude/skills/` しか探索しないために置いてある薄いポインタです)。\n")
    body = f"""---
name: {name}
description: {desc}{skill_frontmatter_extra(fm)}
---

{pointer}"""
    write(os.path.join(ROOT, ".claude", "skills", name, "SKILL.md"), body)

# 比較モード(--check / --check-adapters)は第1・3節の差分集計で終了する(第2節と第4〜7節は書込を伴う apply のため通さない)
if CHECK_ONLY:
    sys.exit(finish_check(_CHECK_RC))

# ---- 4. model-policy 展開 (.github/harness/model-policy.yml → 役割別 model/effort の鏡。監査 2026-09-09 §4.5)
# .claude/agents の frontmatter(model/effort/maxTurns/background/memory)、.github/agents の model: 行、
# .claude/commands の effort(phase_overrides)、.github/copilot/settings.json、guard-subagent-model の役割表、
# PLATFORM.md / README.md / agents.html の対照表。permissions.deny だけは --print-deny / --apply-deny(明示)。
# 通常モードで人間が実行する(保守モードの退避 .locked 中は --apply-deny を拒否する)。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_policy  # noqa: E402
try:
    model_policy.apply_all(ROOT, log=lambda rel: print("wrote:", rel))
except model_policy.PolicyError as e:
    sys.exit(f"ERROR: model-policy: {e}")

# ---- 5. Copilot Agent Host 向け入口スキル (.github/skills/<nn>-<name>/SKILL.md。再監査 2026-09-09 CP-2 / A6-17)
# Agent Host は prompt files(.github/prompts)を読み込まないため、正(prompt + agent バインド)を参照するだけの
# 薄い user-invocable スキルを prompt ごとに生成する(生成・判定の正は tools/copilot_entry_skills.py)。
# Claude Code 側の .claude/skills/<nn>-*/ ポインタは作らない(第1節の .claude/commands/<nn>-*.md とスラッシュ名が衝突)。
import copilot_entry_skills  # noqa: E402
try:
    copilot_entry_skills.apply_all(ROOT, log=lambda rel: print("wrote:", rel))
except copilot_entry_skills.EntryError as e:
    sys.exit(f"ERROR: copilot-entry-skills: {e}")

# ---- 6. plugin manifests (plugin.json → .claude-plugin/plugin.json / .claude-plugin/hooks.json。
# 再監査 2026-09-09 CP-7 / A6-23)
# 正は Agent Plugins 1.0 準拠の plugin.json(版数の正でもある)。Claude Code plugin マニフェストと
# plugin 形式の hooks(.claude/settings.json の hooks を ${CLAUDE_PLUGIN_ROOT} 相対に写したもの)は生成物。
import plugin_manifests  # noqa: E402
try:
    plugin_manifests.apply_all(ROOT, log=lambda rel: print("wrote:", rel))
except plugin_manifests.ManifestError as e:
    sys.exit(f"ERROR: plugin-manifests: {e}")

# ---- 7. パス限定ルール (.github/instructions/<name>.instructions.md → .claude/rules/<name>.md。
# 第3波 A6-19b(4) / A7-M-8。再監査 2026-08-31 §6「.claude/rules/ 未導入」)
# 正は Copilot の applyTo(該当ファイルを扱うときだけ読まれる)。Claude Code の同じ仕組み paths: に写し、
# 該当ファイルを読むときだけ載る=AGENTS.md の常駐 200 行予算の外に出せる規範の置き場。手書きの rule は置かない
# (生成器・判定の正は tools/path_rules.py。鮮度・孤児は validate (x))。
import path_rules  # noqa: E402
try:
    path_rules.apply_all(ROOT, log=lambda rel: print("wrote:", rel))
except path_rules.RuleError as e:
    sys.exit(f"ERROR: path-rules: {e}")

print("done")
