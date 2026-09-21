"""ハーネスの構成整合性を機械検査する。

チェック内容:
- agents/prompts/skills の frontmatter (description必須、tools alias、agents許可リスト、
  handoffs の参照先、prompt の agent: バインディング、skill name/description 規則)
- hooks JSON のパースと参照スクリプトの実在
- マルチプラットフォームアダプタ (.claude/commands|agents|skills、.agents/workflows) の
  欠落・description乖離 (乖離があれば tools/generate-adapters.py で再生成する)
  - Copilot Agent Host 向け入口スキル (.github/skills/<nn>-<name>/。prompts からの生成物。A6-17 / CP-2)
    は .claude/skills アダプタの対象外 (作ると /<nn>-… が .claude/commands と衝突するため存在すれば ERROR)。
    生成物の鮮度・孤児・未生成は (l) で検査 (判定と生成の正は tools/copilot_entry_skills.py)
- 伝播整合(N面鏡対策):
  - .claude 側アダプタの孤児ポインタ (正の .github/skills|agents が実在するか)
  - 3面照合: .claude/settings.json の permissions.deny と
    guard-harness-config-edit.sh の protected_pattern と
    guard-harness-config-edit.ps1 の $protectedPattern の3層に主要保護パスが揃っているか
    (パターン抽出に失敗した層は全文フォールバックせず WARN でスキップを明示)
  - README/.github/harness/*.html/.github/harness/*.md のフック自己テスト件数ハードコード
    (「Nケース」「N件」「sh N / ps1 M」「N/M(sh/ps1)」形)が selftest.sh / selftest.ps1 の
    静的計数と一致するか (再監査 2026-09-09 RG-12 で走査対象と形を拡張)
  - prompts の「ステップN」参照がバインド先 .agent.md の番号付き項目の範囲内か
    (バインド先に番号付き項目が無くステップ参照がある場合は INFO で照合不能を明示)
  - audits/PROPOSALS.md の状態列 (applied/partial/deferred/rejected/open) と open 件数のINFO報告
  - audits/*.md 先頭の <!-- proposals: … --> 宣言 ID 集合が PROPOSALS.md の ID 集合に含まれるか
    (登録完全性。既定は WARN、--ledger-strict で ERROR。再監査 2026-09-09 OP-1/RG-14)
  - .vscode/settings.json の不変条件 (chat.hookFilesLocations で .claude/settings.json を
    読まない・chat.useClaudeMdFile=false・chat.agentSkillsLocations で .claude/skills を読まない。
    Agent Host / Copilot CLI の二重読込対策。再監査 2026-09-09 CP-3)
  - DECISIONS.md ヘッダ「最終更新」の D 番号が本文最大の D 番号と一致するか
  - tools/gen-docs.py --check が差分ゼロか (説明文書の生成ブロックが一次データと一致。A3-4)
  - AGENTS.md の常時ロード予算 (25KB 超で WARN、30KB 超で ERROR。A3-5)
  - model-policy 整合 (.github/harness/model-policy.yml と生成物・各面の一致。許容値集合・
    model: auto の全文 grep・プラン行列・廃止 30 日前・親≧子ティア仮説・専用フィールド混入・
    min_version・役割集合・retrieved 鮮度・deny の一致・保護 5 面。WARN 既定、
    --model-policy-strict で ERROR。監査 2026-09-09 §4.5)
  - plugin マニフェスト (plugin.json が Agent Plugins 1.0 スキーマ($schema・name 必須・許容キーのみ・
    semver)に従うか、.claude-plugin/plugin.json の name/version 一致とパスが ./ 始まりで実在するか、
    生成物(.claude-plugin/plugin.json・.claude-plugin/hooks.json)の鮮度 ((m)。再監査 2026-09-09 CP-7 / A6-23)
  - 浮動参照 (`<pkg>@latest` / `<owner>/<repo>@main` / `<image>:latest` 形) が設定・スキル・文書・
    ワークフロー・ツールに無いか (本体は ERROR・配布先は WARN。codex 監査 2026-08-31 H-02 / IA-20260831-04)
  - リリースの不変性 (本体のみ): plugin.json の version が CHANGELOG の版見出しにあるか (ERROR)、
    各版に annotated tag vX.Y.Z があるか (WARN。codex H-07 / IA-20260831-09 / A7-M-1)
  - GitHub Actions の harden: uses の 40 桁 SHA 固定・permissions・timeout-minutes・concurrency・
    checkout の persist-credentials: false (本体 CI は ERROR、他は WARN。codex H-06 / IA-20260831-08)
  - 外部 Skill / プラグイン / MCP のロック (.github/harness/external-lock.json。指示層の外部参照(npx/uvx の
    パッケージ・owner/repo)がロックに無い・浮動版(@latest 等)・ロックと違う版・版なしは ERROR、local patch の
    ダイジェストずれは WARN ((y)。判定の正は tools/external_lock.py。codex 監査 2026-08-31 H-09 / IA-20260831-11 / IA-20260831-04)
  - 判定ログの欄順: _log.py の FIELD_ORDER・_log.sh の printf 書式・_log.ps1 の行組立の 3 系統が同じ欄順で末尾が
    duration_ms / host か ((k-4)。ERROR。第7波 R-09 / A2-7)
  - Claude Code サブエージェントの本文展開: .claude/agents/<role>.md が .github/agents/<role>.agent.md の本文展開と
    一致するか (鏡割れ・未生成は ERROR。(z)。判定と生成の正は tools/subagent_adapters.py。第7波 A5-6)
  - ポインタ型アダプタ (.claude/commands|skills・.agents/workflows) の本文ドリフト ((s)。generate-adapters.py
    --check-adapters の exit code。ERROR。.claude/agents は (z) が見る。A7-M-6)
  - ビルトイン依存の台帳 builtin-dependencies.json の構造と used_by の実在・言及 ((t)。A5-8)
  - CHANGELOG の版と annotated tag の対応 ((u)。tools/release-tag.py --check の INFO 転記。A7-M-1)
  - harness-ci.yml の claude 版固定 = platform-requirements.json の verified_on_ci ((v)。WARN。A3-6)
  - WARN→ERROR の昇格 (A7-M-6): (b) 3面照合の欠落は本体で ERROR(配布先は WARN)、(k)/(k-2)/(k-3) の共通ライブラリ配線と
    8.3 展開の欠落、アダプタの description 乖離。昇格しないもの: (c)/(c-2) 件数ハードコード(文言の問題。生成へ移す途中)、
    (f) DECISIONS ヘッダ(freshness-lint が併せて見る)、(j) model-policy((j-1) retrieved 30 日超のように時間で赤になる検査を
    含むため --model-policy-strict で明示)。

使い方(リポジトリルートで):
    python tools/validate-harness.py
    python tools/validate-harness.py --ledger-strict   # 台帳の登録漏れを ERROR にする

終了コード: エラーがあれば1、なければ0。
依存: Python 3.x + PyYAML (pip install pyyaml)
"""
import re
import subprocess
import sys
import json
import glob
import os

import yaml

# Windows コンソール/サブプロセス(cp932)で INFO/WARN の日本語が化けないよう UTF-8 に固定する
# (再監査 2026-09-09 RG-15。gen-docs.py / effort-report.py と同じ処理)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = []
warnings = []
infos = []
# --ledger-strict: 台帳の登録漏れ((e-2) 検査)を WARN でなく ERROR にする。
# 統合担当が PROPOSALS.md を書き終えるまでは既定 WARN(未登録が既知のため CI を赤にしない)
LEDGER_STRICT = "--ledger-strict" in sys.argv[1:]

# 本体判定(D086 追記 2026-09-17): 目印を「DECISIONS.md の有無」から、フック(inject-progress の H-11)と
# tools/doctor.py の is_body と同じ規則「DECISIONS.md か .github/harness/USAGE.md があり、requirements/memo.md が
# テンプレのまま」に揃える。テンプレート複製由来の DECISIONS.md が残った実プロジェクト(3 実プロジェクトの同期で顕在化:
# gen-docs.py 不在 ERROR・アプリ README のマーカー欠落 ERROR・古い DECISIONS.md との決定数不一致 WARN)を本体扱いしない。
# 本体専用の検査: (c-2) の決定数、(f) DECISIONS ヘッダ、(g) gen-docs、(j-6) README の `model: auto` 走査、
# (j-11) README の model-policy 対照表(model_policy.targets 側で除外)。
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import doctor as _doctor_body  # noqa: E402
    IS_BODY = bool(_doctor_body.is_body(ROOT))
except Exception:  # noqa: BLE001
    IS_BODY = os.path.exists(os.path.join(ROOT, "DECISIONS.md"))
if not IS_BODY:
    infos.append("配布先として検査(requirements/memo.md に実内容あり、または DECISIONS.md / USAGE.md 無し): "
                 "本体専用の検査 (c-2) 決定数 / (f) / (g) / (j-6)(j-11) の README は対象外")


def read_frontmatter(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not m:
        errors.append(f"{path}: frontmatter not found or malformed")
        return None, text
    try:
        fm = yaml.safe_load(m.group(1))
    except Exception as e:
        errors.append(f"{path}: YAML parse error: {e}")
        return None, text
    return fm, text[m.end():]


VALID_TOOL_ALIASES = {"execute", "read", "edit", "search", "agent", "web", "todo", "playwright"}

agent_files = glob.glob(os.path.join(ROOT, ".github", "agents", "*.agent.md"))
agent_names = {os.path.basename(f).replace(".agent.md", "") for f in agent_files}

for f in agent_files:
    fm, _ = read_frontmatter(f)
    if fm is None:
        continue
    if "description" not in fm:
        errors.append(f"{f}: missing 'description'")
    tools = fm.get("tools", [])
    for t in tools:
        if t not in VALID_TOOL_ALIASES:
            errors.append(f"{f}: unknown tool alias '{t}'")
    if "agents" in fm:
        allowed = fm["agents"]
        if allowed == "*":
            if "agent" not in tools:
                errors.append(f"{f}: agents is '*' but tools missing 'agent' alias")
        elif isinstance(allowed, list):
            for a in allowed:
                if a not in agent_names:
                    errors.append(f"{f}: agents whitelist references unknown agent '{a}'")
            if allowed and "agent" not in tools:
                errors.append(f"{f}: has agents whitelist {allowed} but tools missing 'agent' alias")
        else:
            errors.append(f"{f}: 'agents' must be a list or '*', got {type(allowed).__name__} ({allowed!r})")
    else:
        warnings.append(f"{f}: no 'agents' field (consider explicit least-privilege)")
    for h in fm.get("handoffs", []) or []:
        if h.get("agent") not in agent_names:
            errors.append(f"{f}: handoff target '{h.get('agent')}' does not exist")
        for req in ("label", "prompt"):
            if req not in h:
                errors.append(f"{f}: handoff missing '{req}'")
        if "send" not in h:
            warnings.append(f"{f}: handoff to '{h.get('agent')}' missing explicit 'send'")

prompt_files = glob.glob(os.path.join(ROOT, ".github", "prompts", "*.prompt.md"))
prompt_fms = {}  # base名 -> frontmatter(アダプタとの description 乖離検査で再利用)
prompt_step_refs = []  # (path, agent名, body)(「ステップN」参照の範囲検査で再利用)
for f in prompt_files:
    fm, body = read_frontmatter(f)
    if fm is None:
        continue
    prompt_fms[os.path.basename(f).replace(".prompt.md", "")] = fm
    if "description" not in fm:
        errors.append(f"{f}: missing 'description'")
    body_lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(body_lines) > 25:
        warnings.append(f"{f}: body has {len(body_lines)} non-empty lines (>25): "
                        "プロンプトが太い可能性（手順の正は .agent.md / スキルへ）")
    agent = fm.get("agent")
    if not agent:
        errors.append(f"{f}: missing 'agent' binding")
    elif agent not in agent_names:
        errors.append(f"{f}: agent binding '{agent}' does not exist")
    else:
        prompt_step_refs.append((f, agent, body))
    if "mode" in fm:
        warnings.append(f"{f}: legacy 'mode' field present")
    if "tools" in fm:
        warnings.append(f"{f}: redundant 'tools' field (inherited from bound agent)")

skill_files = glob.glob(os.path.join(ROOT, ".github", "skills", "*", "SKILL.md"))
for f in skill_files:
    fm, _ = read_frontmatter(f)
    if fm is None:
        continue
    folder = os.path.basename(os.path.dirname(f))
    name = fm.get("name")
    if name != folder:
        errors.append(f"{f}: name '{name}' != folder '{folder}'")
    if not name or not re.match(r"^[a-z0-9-]{1,64}$", name):
        errors.append(f"{f}: invalid name '{name}'")
    desc = fm.get("description", "")
    if not desc:
        errors.append(f"{f}: missing description")
    elif len(desc) > 1024:
        errors.append(f"{f}: description exceeds 1024 chars")

json_files = [
    os.path.join(ROOT, "plugin.json"),
    os.path.join(ROOT, ".github", "hooks", "gate-hooks.json"),
    os.path.join(ROOT, ".github", "hooks", "security-hooks.json"),
    os.path.join(ROOT, ".vscode", "settings.json"),
    os.path.join(ROOT, ".claude", "settings.json"),
]
for f in json_files:
    try:
        text = open(f, encoding="utf-8").read()
        stripped = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
        json.loads(stripped)
    except Exception as e:
        errors.append(f"{f}: JSON parse error: {e}")

for hooks_file in [os.path.join(ROOT, ".github", "hooks", "gate-hooks.json"),
                   os.path.join(ROOT, ".github", "hooks", "security-hooks.json"),
                   os.path.join(ROOT, ".claude", "settings.json")]:
    try:
        text = open(hooks_file, encoding="utf-8").read()
        stripped = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
        data = json.loads(stripped)
    except Exception:
        continue
    blob = json.dumps(data)
    for m in re.finditer(r"\.github/hooks/scripts/[\w.-]+", blob):
        p = os.path.join(ROOT, m.group(0).replace("/", os.sep))
        if not os.path.exists(p):
            errors.append(f"{hooks_file}: referenced script missing: {m.group(0)}")

# ---- multi-platform adapters
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import copilot_entry_skills as _ces  # noqa: E402
except Exception as e:  # noqa: BLE001
    _ces = None
    errors.append(f"tools/copilot_entry_skills.py を import できない: {e}")


def is_entry_skill(name, fm):
    """Copilot Agent Host 向け入口スキル(.github/skills/<nn>-<name>/。prompts からの生成物。A6-17 / CP-2)。
    判定の正は tools/copilot_entry_skills.py(名前が ^\\d\\d- か frontmatter に harness-entry: copilot)。"""
    if _ces is not None:
        return _ces.is_entry_skill(name, fm)
    return bool(re.match(r"^\d\d-", name or "")) or fm.get("harness-entry") == "copilot" \
        or (fm.get("metadata") or {}).get("harness-entry") == "copilot"


for sf in skill_files:
    fm, _ = read_frontmatter(sf)
    if fm is None:
        continue
    name = fm.get("name")
    adapter = os.path.join(ROOT, ".claude", "skills", name, "SKILL.md")
    if is_entry_skill(name, fm):
        # 入口スキルは Copilot 専用の生成物: Claude Code 側は .claude/commands/<nn>-*.md が同じ正を参照するため
        # .claude/skills ポインタを作らない(作ると /<nn>-… のスラッシュ名が commands と衝突する)
        if os.path.isdir(os.path.join(ROOT, ".claude", "skills", name)):
            errors.append(f".claude/skills/{name}/: Copilot 入口スキルのポインタは作らない"
                          f"(/{name} が .claude/commands/{name}.md と衝突する。ディレクトリを削除する)")
        continue
    if not os.path.exists(adapter):
        errors.append(f".claude/skills/{name}/SKILL.md: adapter missing (run tools/generate-adapters.py)")
    else:
        afm, _ = read_frontmatter(adapter)
        if afm and afm.get("description") != fm.get("description"):
            # ERROR に昇格(A7-M-6): アダプタ=ポインタのみ の不変条件。description の乖離は生成し忘れ=鏡割れ
            # (本文のドリフトは (s) が generate-adapters.py --check-adapters で見る)
            errors.append(f".claude/skills/{name}: description differs (run tools/generate-adapters.py)")

for pf in prompt_files:
    base = os.path.basename(pf).replace(".prompt.md", "")
    for adapter in [os.path.join(ROOT, ".claude", "commands", f"{base}.md"),
                    os.path.join(ROOT, ".agents", "workflows", f"{base}.md")]:
        if not os.path.exists(adapter):
            errors.append(f"{os.path.relpath(adapter, ROOT)}: adapter missing (run tools/generate-adapters.py)")
        else:
            afm, _ = read_frontmatter(adapter)
            pfm = prompt_fms.get(base)
            if afm and pfm and afm.get("description") != pfm.get("description"):
                errors.append(f"{os.path.relpath(adapter, ROOT)}: description differs "
                              "(run tools/generate-adapters.py)")  # ERROR 昇格(A7-M-6。上と同じ理由)

# 逆方向: 正(.github/prompts/*.prompt.md)の無い孤児アダプタを検出する
# (プロンプトを削除・改名したのにアダプタが残ると、存在しないコマンドが露出し続ける)
prompt_bases = {os.path.basename(f).replace(".prompt.md", "") for f in prompt_files}
for adapter_dir in [os.path.join(ROOT, ".claude", "commands"),
                    os.path.join(ROOT, ".agents", "workflows")]:
    for af in glob.glob(os.path.join(adapter_dir, "*.md")):
        base = os.path.basename(af)[:-len(".md")]
        if base not in prompt_bases:
            errors.append(f"{os.path.relpath(af, ROOT)}: 孤児アダプタ (対応する "
                          f".github/prompts/{base}.prompt.md が存在しない。本体側で削除/"
                          "改名したならこのアダプタも削除する)")

for sub in ["reviewer", "spec-critic", "task-worker"]:
    if not os.path.exists(os.path.join(ROOT, ".claude", "agents", f"{sub}.md")):
        errors.append(f".claude/agents/{sub}.md: subagent adapter missing")

# ---- 伝播整合(N面鏡対策): 同じ事実を書いた複数の面が食い違っていないかの機械検査

# (a) 孤児検査の .claude 側: アダプタに対応する正(.github)が実在するか
# (正を削除・改名したのにアダプタが残ると、存在しないスキル/エージェントが露出し続ける)
for af in glob.glob(os.path.join(ROOT, ".claude", "skills", "*", "SKILL.md")):
    name = os.path.basename(os.path.dirname(af))
    if not os.path.isdir(os.path.join(ROOT, ".github", "skills", name)):
        errors.append(f".claude/skills/{name}/SKILL.md: 孤児ポインタ (正の .github/skills/{name}/ が"
                      "存在しない。本体側で削除/改名したならこのアダプタも削除する)")
for af in glob.glob(os.path.join(ROOT, ".claude", "agents", "*.md")):
    name = os.path.basename(af)[:-len(".md")]
    if not os.path.exists(os.path.join(ROOT, ".github", "agents", f"{name}.agent.md")):
        errors.append(f".claude/agents/{name}.md: 孤児ポインタ (正の .github/agents/{name}.agent.md が"
                      "存在しない。本体側で削除/改名したならこのアダプタも削除する)")

# (b) 3面照合: 実deny集合(.claude/settings.json)・guard-harness-config-edit.sh の
# protected_pattern・guard-harness-config-edit.ps1 の $protectedPattern の3層に、
# 主要保護パスのチェックリストがすべて含まれているか。
# 一部だけ更新して保護に穴が開く(または一部だけ残って挙動が食い違う)のを検出する。
CROSS_ITEMS = [
    # (表示名, deny側で探す部分文字列, guard側で探す正規表現)
    (".github/agents/", ".github/agents/", r"\\?\.github/agents/"),
    (".github/hooks/", ".github/hooks/", r"\\?\.github/hooks/"),
    (".github/workflows/", ".github/workflows/", r"\\?\.github/workflows/"),
    (".github/prompts/", ".github/prompts/", r"\\?\.github/prompts/"),
    ("AGENTS.md", "AGENTS.md", r"AGENTS\\?\.md"),
    ("CLAUDE.md", "CLAUDE.md", r"CLAUDE\\?\.md"),
    ("plugin.json", "plugin.json", r"plugin\\?\.json"),
    (".claude/settings.local.json", ".claude/settings.local.json",
     r"settings(\(\\\.local\)\?|\\?\.local)\\?\.json"),
    (".github/harness/PLATFORM.md", "PLATFORM.md", r"PLATFORM\\?\.md"),
    (".github/instructions/", ".github/instructions", r"\\?\.github/instructions"),
    ("request-routing スキル", "request-routing", r"request-routing"),
    ("gate-check スキル", "gate-check", r"gate-check"),
    # 第5回(2026-09-09 再監査 SC-1/SC-6/MP-3): 常駐ロード面の新経路・Copilot CLI 設定・常駐指示・
    # 役割別モデル方針の正。deny / guard sh / guard ps1 / CODEOWNERS の4面同時に足す
    (".claude/rules/", ".claude/rules/", r"\\?\.claude/rules/"),
    (".github/copilot/", ".github/copilot/", r"\\?\.github/copilot/"),
    (".github/copilot-instructions.md", ".github/copilot-instructions.md", r"copilot-instructions\\?\.md"),
    ("CLAUDE.local.md", "CLAUDE.local.md", r"CLAUDE\\?\.local\\?\.md"),
    ("GEMINI.md", "GEMINI.md", r"GEMINI\\?\.md"),
    (".github/harness/model-policy.yml", "model-policy.yml", r"model-policy\\?\.yml"),
    # 第7波(2026-09-17 IA-20260831-11): 外部 Skill / MCP のロック(guard sh/ps1 の protected_pattern / prot_files・deny・CODEOWNERS)
    (".github/harness/external-lock.json", "external-lock.json", r"external-lock\\?\.json"),
]
deny_blob = None
try:
    _txt = open(os.path.join(ROOT, ".claude", "settings.json"), encoding="utf-8").read()
    _data = json.loads(re.sub(r"^\s*//.*$", "", _txt, flags=re.MULTILINE))
    deny_blob = "\n".join(_data.get("permissions", {}).get("deny", []))
except Exception:
    pass
# 抽出失敗時に全文へフォールバックしない: コメント等のパターン外テキストに一致して
# 「揃っている」と誤認し、実際の保護の穴を見逃すため。抽出できなければ WARN で明示する。
guard_sh_pattern = None
guard_sh = os.path.join(ROOT, ".github", "hooks", "scripts", "guard-harness-config-edit.sh")
try:
    _gtxt = open(guard_sh, encoding="utf-8").read()
    _m = re.search(r"protected_pattern='([^']*)'", _gtxt)
    if _m:
        guard_sh_pattern = _m.group(1)
except Exception:
    pass
guard_ps1_pattern = None
guard_ps1 = os.path.join(ROOT, ".github", "hooks", "scripts", "guard-harness-config-edit.ps1")
try:
    _ptxt = open(guard_ps1, encoding="utf-8").read()
    _pm = re.search(r"\$protectedPattern\s*=\s*'([^']*)'", _ptxt)
    if _pm:
        # .ps1 はパス区切りを [\\/] の文字クラスで書くため、/ に正規化してから
        # .sh と同じ CROSS_ITEMS の正規表現で照合する
        guard_ps1_pattern = _pm.group(1).replace(r"[\\/]", "/")
except Exception:
    pass
# 昇格(A7-M-6。再監査 2026-08-31 §6「validate の警告は exit 0 のため中核不変条件の破れが CI を緑のまま通過」):
# 3 面のどれか 1 つに保護パスが無い=保護の穴。本体では ERROR(CI が赤になる)。配布先は .claude/settings.json が
# REVIEW_FILES(無言上書きしないため /91 の手動マージまで古いまま)なので従来どおり WARN。抽出失敗のスキップは判断不能なので WARN のまま
_cross_sink = errors if IS_BODY else warnings
if deny_blob is None:
    warnings.append("3面照合: .claude/settings.json の permissions.deny を読めないため照合をスキップ")
else:
    for label, deny_probe, _guard_re in CROSS_ITEMS:
        if deny_probe not in deny_blob:
            _cross_sink.append(f"3面照合: {label} が .claude/settings.json の permissions.deny に見当たらない")
for _guard_name, _guard_pattern in [("guard-harness-config-edit.sh", guard_sh_pattern),
                                    ("guard-harness-config-edit.ps1", guard_ps1_pattern)]:
    if _guard_pattern is None:
        warnings.append(f"3面照合: {_guard_name} から保護パターンを抽出できないため照合をスキップ"
                        "(スクリプトの変数定義とこの検査の抽出正規表現を確認)")
        continue
    for label, _deny_probe, guard_re in CROSS_ITEMS:
        if not re.search(guard_re, _guard_pattern):
            _cross_sink.append(f"3面照合: {label} が {_guard_name} の保護パターンに見当たらない")

# (c) 件数ハードコード検出: README/.github/harness/*.html/.github/harness/*.md の
# 「フック自己テスト N ケース」「selftest N件」「sh N / ps1 M」「N/M(sh/ps1)」が
# selftest.sh / selftest.ps1 の静的 check 数の近似(^check 行 + 手動判定ブロック数)からずれていないか。
# 数値の言及が無ければ OK(件数を明記しない書き方も許容する)。
# 走査対象と形は再監査 2026-09-09 RG-12(COMPARISON.md:263 の「selftest 15件」が実測 18 でも
# 未検出だった)で拡張。数え方は tools/gen-docs.py の count_selftest_cases /
# count_selftest_ps1_cases と同一に保つこと。
selftest_sh = os.path.join(ROOT, ".github", "hooks", "scripts", "selftest.sh")
selftest_ps1 = os.path.join(ROOT, ".github", "hooks", "scripts", "selftest.ps1")
if os.path.exists(selftest_sh):
    _st = open(selftest_sh, encoding="utf-8").read()
    _check_calls = len(re.findall(r"^check ", _st, re.M))
    # check() 関数内の1回を除いた pass カウンタ増分 = check() を経由しない手動判定ブロック数の近似
    _manual = max(0, len(re.findall(r"pass=\$\(\(pass\+1\)\)", _st)) - 1)
    _approx = _check_calls + _manual
    _approx_ps1 = None
    if os.path.exists(selftest_ps1):
        _pst = open(selftest_ps1, encoding="utf-8-sig").read()
        _approx_ps1 = (len(re.findall(r"^Check ", _pst, re.M))
                       + max(0, len(re.findall(r"\$script:pass\+\+", _pst)) - 1))
    _known = {_approx} | ({_approx_ps1} if _approx_ps1 is not None else set())
    _docs_c = ([os.path.join(ROOT, "README.md")]
               + sorted(glob.glob(os.path.join(ROOT, ".github", "harness", "*.html")))
               + sorted(glob.glob(os.path.join(ROOT, ".github", "harness", "*.md"))))
    for doc in _docs_c:
        if not os.path.exists(doc):
            continue
        for ln_no, line in enumerate(open(doc, encoding="utf-8").read().splitlines(), 1):
            if "自己テスト" not in line and "selftest" not in line.lower():
                continue
            _rel = os.path.relpath(doc, ROOT)
            # 「自己テスト96ケース」「selftest 15件」「selftest.sh 96件」形(語の直後の件数のみ)
            for m in re.finditer(r"(?:selftest(?:\.sh|\.ps1)?|自己テスト)\s*[（(]?\s*(\d+)\s*(?:ケース|件)", line):
                if int(m.group(1)) not in _known:
                    warnings.append(f"{_rel}:{ln_no}: フック自己テスト件数の"
                                    f"ハードコード {m.group(1)} が selftest.sh {_approx} / selftest.ps1 "
                                    f"{_approx_ps1} の静的計数と不一致(生成ブロックへ移すか、件数の明記をやめる。"
                                    "python 系ツールの selftest 件数は文書に書かない)")
            # 「sh 96 / ps1 73」「selftest.sh 96 / selftest.ps1 73」「96/73（sh/ps1」形
            _pairs = [(m.group(1), m.group(2)) for m in
                      re.finditer(r"sh\s*(\d+)\s*/\s*(?:selftest\.)?ps1\s*(\d+)", line)]
            _pairs += [(m.group(1), m.group(2)) for m in
                       re.finditer(r"(\d+)\s*/\s*(\d+)\s*[（(]\s*sh\s*/\s*ps1", line)]
            for _sh_n, _ps_n in _pairs:
                if int(_sh_n) != _approx or (_approx_ps1 is not None and int(_ps_n) != _approx_ps1):
                    warnings.append(f"{_rel}:{ln_no}: フック自己テスト件数の"
                                    f"ハードコード sh {_sh_n} / ps1 {_ps_n} が静的計数 sh {_approx} / "
                                    f"ps1 {_approx_ps1} と不一致(生成ブロックへ移すか、件数の明記をやめる)")

# (c-2) 件数ハードコード検出の拡張(A3-4b / A7-A1-2): スキル数・コマンド数・エージェント数・決定数が
# 説明文書に手で書かれていないか。一次データは .github/skills|prompts|agents のファイル数と
# DECISIONS.md の ^## D 見出し(件数と最大番号)。生成ブロック(gen-docs)の中の数値は一次データと
# 同じ値になるので検出されない=「生成ブロックへ移すか、件数の明記をやめる」の二択に倒す。
# 日本語形「N 本のスキル」「スキル N 本」「N のスキル」「決定 NN 件」「NN 件の決定」「D001〜D0NN」
# 「DECISIONS.md・NN件」と、README.en 向けの英語形「N skills」「N (slash) commands」「N design decisions」。
_n_skills = len(skill_files)
_n_cmds = len(prompt_files)
_n_agents = len(agent_files)
_dec_n = _dec_max = None
_dec_path = os.path.join(ROOT, "DECISIONS.md")
if IS_BODY and os.path.exists(_dec_path):  # 配布先に残った古い DECISIONS.md は一次データにしない
    _dec_ids = [int(n) for n in re.findall(r"^## D(\d+)", open(_dec_path, encoding="utf-8-sig").read(), re.M)]
    _dec_n, _dec_max = len(_dec_ids), (max(_dec_ids) if _dec_ids else 0)
_COUNT_PATTERNS = [
    # (表示名, 正の値, 正規表現。group(1) が数値)
    ("スキル数", _n_skills, r"(\d+)\s*(?:本|個|つ)?の(?:Agent )?スキル"),
    ("スキル数", _n_skills, r"スキル\s*(\d+)\s*本"),
    ("スキル数", _n_skills, r"\b(\d+)\s+(?:Agent\s+)?[Ss]kills\b"),
    ("コマンド数", _n_cmds, r"(\d+)\s*(?:本|個|つ)?の(?:スラッシュ)?コマンド"),
    ("コマンド数", _n_cmds, r"コマンド\s*(\d+)\s*本"),
    ("コマンド数", _n_cmds, r"\b(\d+)\s+(?:slash\s+)?commands\b"),
    ("エージェント数", _n_agents, r"(\d+)\s*(?:本|個|つ|人)?の(?:専属)?エージェント(?!経路)"),
    # 英語の「N agents」は監査の「41 agents」(監査エージェント数)と衝突するため対象外(README.en は日本語版の翻訳で件数の正は同じ)
    ("決定数", _dec_n, r"(?:設計)?決定\s*(\d+)\s*件"),
    ("決定数", _dec_n, r"(\d+)\s*件の(?:設計)?決定"),
    ("決定数", _dec_n, r"DECISIONS\.md\s*[・,、]\s*(\d+)\s*件"),
    ("決定数", _dec_n, r"\b(\d+)\s+(?:design\s+)?decisions\b"),
    ("決定の最大番号", _dec_max, r"D001\s*[〜～~\-–]\s*D(\d+)"),
]
_docs_c2 = ([os.path.join(ROOT, n) for n in ("README.md", "README.en.md", "CLAUDE.md", "AGENTS.md")]
            + sorted(glob.glob(os.path.join(ROOT, ".github", "harness", "*.html")))
            + sorted(glob.glob(os.path.join(ROOT, ".github", "harness", "*.md"))))
for doc in _docs_c2:
    if not os.path.exists(doc):
        continue
    _rel = os.path.relpath(doc, ROOT).replace(os.sep, "/")
    for ln_no, line in enumerate(open(doc, encoding="utf-8-sig").read().splitlines(), 1):
        for _label, _truth, _pat in _COUNT_PATTERNS:
            if _truth is None:
                continue
            for m in re.finditer(_pat, line):
                if int(m.group(1)) != _truth:
                    warnings.append(f"{_rel}:{ln_no}: {_label}のハードコード {m.group(1)} が一次データ {_truth} と不一致"
                                    "(gen-docs の生成ブロックへ移すか、件数の明記をやめる。A3-4b)")

# (d) ステップ番号整合: prompts の「ステップN」参照が、バインド先 .agent.md の
# 番号付き項目の範囲内か(改番後にプロンプト側の参照だけ残るずれを検出する)
_agent_max_cache = {}


def agent_numbered_max(agent):
    if agent not in _agent_max_cache:
        path = os.path.join(ROOT, ".github", "agents", f"{agent}.agent.md")
        try:
            text = open(path, encoding="utf-8").read()
            nums = [int(n) for n in re.findall(r"^\s{0,3}(\d+)(?:\.\d+)*\.\s", text, re.M)]
            _agent_max_cache[agent] = max(nums) if nums else None
        except Exception:
            _agent_max_cache[agent] = None
    return _agent_max_cache[agent]


for pf, agent, body in prompt_step_refs:
    refs = []
    for m in re.finditer(r"ステップ\s*(\d+)(?:\s*[〜~～\-]\s*(\d+))?", body):
        refs.append(int(m.group(1)))
        if m.group(2):
            refs.append(int(m.group(2)))
    if not refs:
        continue
    max_step = agent_numbered_max(agent)
    if max_step is None:
        # 沈黙スキップにしない: 参照だけあって照合できない状態(改番ずれを検出できない)を
        # INFO で可視化する(第3回監査)
        infos.append(f"{pf}: 「ステップN」参照があるが、バインド先 {agent}.agent.md に"
                     "番号付き項目が無いため範囲照合ができない(照合不能)")
        continue
    for n in refs:
        if n > max_step:
            warnings.append(f"{pf}: 「ステップ{n}」参照がバインド先 {agent}.agent.md の"
                            f"番号付き項目の範囲(最大 {max_step})を超えている")

# (e) 提案台帳検査: audits/PROPOSALS.md の状態列の語彙と open 件数(台帳が無ければ何もしない)
proposals_md = os.path.join(ROOT, "audits", "PROPOSALS.md")
proposal_ids = set()  # 台帳に登録済みの提案 ID(ID 列の値。(e-2) の突合で使う)
if os.path.exists(proposals_md):
    # partial: applied と記録したが機械ゲートとしては未完成(再監査 2026-09-09 RG-13 で新設。
    # 残作業は子 ID(例 A3-4b)で起票する)
    VALID_STATES = ("applied", "partial", "deferred", "rejected", "open")
    state_col = None
    id_col = None
    reg_col = upd_col = None  # 登録日 / 状態更新日(OP-4。2026-09-10 追加の 2 列)
    total = open_count = 0
    _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _date_bad = 0
    for ln_no, line in enumerate(open(proposals_md, encoding="utf-8").read().splitlines(), 1):
        if not line.strip().startswith("|"):
            state_col = None  # テーブルを抜けたら列位置をリセット(複数テーブル対応)
            id_col = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if state_col is None:
            if any("状態" in c for c in cells):
                state_col = next(i for i, c in enumerate(cells) if "状態" in c and "更新" not in c)
                id_col = next((i for i, c in enumerate(cells) if c.upper() == "ID"), 0)
                reg_col = next((i for i, c in enumerate(cells) if "登録日" in c), None)
                upd_col = next((i for i, c in enumerate(cells) if "状態更新日" in c), None)
                if reg_col is None or upd_col is None:
                    warnings.append(f"audits/PROPOSALS.md:{ln_no}: 表に 登録日 / 状態更新日 の列が無い"
                                    "(OP-4: 滞留日数・監査→適用リードタイムの KPI を effort-report --kpi が出せない)")
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue  # 区切り行
        if state_col >= len(cells):
            continue
        total += 1
        if reg_col is not None and upd_col is not None and max(reg_col, upd_col) < len(cells):
            _reg, _upd = cells[reg_col], cells[upd_col]
            if not _date_re.match(_reg) or not _date_re.match(_upd):
                _date_bad += 1
                if _date_bad <= 5:
                    warnings.append(f"audits/PROPOSALS.md:{ln_no}: 登録日 '{_reg}' / 状態更新日 '{_upd}' が YYYY-MM-DD でない")
            elif _upd < _reg:
                warnings.append(f"audits/PROPOSALS.md:{ln_no}: 状態更新日 {_upd} が登録日 {_reg} より前")
        if id_col is not None and id_col < len(cells):
            _idm = re.match(r"\**([A-Za-z][A-Za-z0-9]*-[A-Za-z0-9-]+?)\**$", cells[id_col].strip())
            if _idm:
                proposal_ids.add(_idm.group(1))
        token = cells[state_col].lower()
        state = next((s for s in VALID_STATES if token == s or token.startswith(s)), None)
        if state is None:
            warnings.append(f"audits/PROPOSALS.md:{ln_no}: 状態列 '{cells[state_col]}' が "
                            "applied/partial/deferred/rejected/open のいずれでもない")
        elif state == "open":
            open_count += 1
    if total:
        infos.append(f"audits/PROPOSALS.md: 提案 {total} 件(うち open {open_count} 件)")
    else:
        warnings.append("audits/PROPOSALS.md: 状態列を持つ表の行を検出できない(台帳の書式を確認)")

# (e-2) 登録完全性: 各監査 md の先頭付近の <!-- proposals: … --> 宣言(監査が起票した ID の集合)が
# PROPOSALS.md の ID 集合に含まれるか。監査→台帳のパイプライン入口の詰まり
# (再監査 2026-08-31・codex 監査ののべ 200 超が台帳に 0 参照。再監査 2026-09-09 OP-1/RG-14)を
# 機械検出する。宣言の書式: `<!-- proposals: A6-1..24, A7-H-1..11, IA-20260831-01..15, R-01..09 -->`
# (カンマ区切り。`X..N` は X 末尾の数字から N までの範囲展開。ゼロ埋め幅は X に従う)。
# 宣言が無い監査 md は対象外(過去監査は遡及登録の対象外。新規監査は宣言を必須にする運用)。


def expand_proposal_decl(decl):
    ids = []
    for tok in re.split(r"\s*,\s*", decl.strip()):
        if not tok:
            continue
        m = re.fullmatch(r"([A-Za-z][A-Za-z0-9-]*?-?)(\d+)\.\.(\d+)", tok)
        if m:
            prefix, start, end = m.group(1), m.group(2), m.group(3)
            width = len(start)
            for n in range(int(start), int(end) + 1):
                ids.append(f"{prefix}{n:0{width}d}")
        else:
            ids.append(tok)
    return ids


for _audit in sorted(glob.glob(os.path.join(ROOT, "audits", "*.md"))):
    if os.path.basename(_audit) == "PROPOSALS.md":
        continue
    try:
        _head = open(_audit, encoding="utf-8-sig").read(4000)
    except Exception:
        continue
    _dm = re.search(r"<!--\s*proposals:\s*(.*?)\s*-->", _head, re.S)
    if not _dm:
        continue
    _declared = expand_proposal_decl(_dm.group(1))
    _missing = [i for i in _declared if i not in proposal_ids]
    _rel = os.path.relpath(_audit, ROOT)
    if not proposal_ids:
        _msg = (f"{_rel}: 宣言 {len(_declared)} 件の提案 ID が PROPOSALS.md に 1 件も登録されていない"
                "(台帳の ID 列を読めないか、監査分が未登録)")
    elif _missing:
        _msg = (f"{_rel}: 宣言 {len(_declared)} 件のうち {len(_missing)} 件が PROPOSALS.md に未登録: "
                + ", ".join(_missing[:8]) + (" …" if len(_missing) > 8 else ""))
    else:
        infos.append(f"{_rel}: 宣言 {len(_declared)} 件はすべて PROPOSALS.md に登録済み")
        continue
    (errors if LEDGER_STRICT else warnings).append(_msg)

# (e-3) .vscode/settings.json の不変条件(再監査 2026-09-09 CP-3): VS Code Agent Host と
# Copilot CLI は既定で `.claude/settings.json`・`.claude/skills`・`CLAUDE.md` も走査し、
# 重複排除なしで同一ガードを直列実行する。Local ハーネスの opt-in 門(D058)は Agent Host には
# 無いため、ワークスペース設定で読込元を明示して二重読込を抑止する。
vscode_settings = os.path.join(ROOT, ".vscode", "settings.json")
if os.path.exists(vscode_settings):
    try:
        _vs = json.loads(re.sub(r"^\s*//.*$", "",
                                open(vscode_settings, encoding="utf-8-sig").read(), flags=re.MULTILINE))
    except Exception:
        _vs = None
    if isinstance(_vs, dict):
        _hooks_loc = _vs.get("chat.hookFilesLocations")
        if not isinstance(_hooks_loc, dict):
            errors.append(".vscode/settings.json: chat.hookFilesLocations が無い"
                          "(Agent Host / Copilot CLI が .claude/settings.json のフックを重複読込する。CP-3)")
        else:
            if _hooks_loc.get(".github/hooks") is not True:
                errors.append(".vscode/settings.json: chat.hookFilesLocations['.github/hooks'] が true でない")
            for _p in (".claude/settings.json", ".claude/settings.local.json"):
                if _hooks_loc.get(_p) is not False:
                    errors.append(f".vscode/settings.json: chat.hookFilesLocations['{_p}'] が false でない"
                                  "(同一ガードの二重発火・直列実行を抑止できない。CP-3)")
        if _vs.get("chat.useClaudeMdFile") is not False:
            errors.append(".vscode/settings.json: chat.useClaudeMdFile が false でない"
                          "(CLAUDE.md → @AGENTS.md と AGENTS.md の二重読込。CP-3)")
        if (_vs.get("chat.agentSkillsLocations") or {}).get(".claude/skills") is not False:
            errors.append(".vscode/settings.json: chat.agentSkillsLocations['.claude/skills'] が false でない"
                          "(.claude/skills ポインタと .github/skills 正の二重読込)")

# (f) DECISIONS 鮮度: ヘッダ「最終更新: …(DNNN)」の D 番号が本文最大の D 番号と一致するか
decisions_md = os.path.join(ROOT, "DECISIONS.md")
if IS_BODY and os.path.exists(decisions_md):
    _dtext = open(decisions_md, encoding="utf-8").read()
    _hm = re.search(r"最終更新[::].*?D(\d+)", _dtext[:2000])
    _body_ids = [int(n) for n in re.findall(r"^## D(\d+)", _dtext, re.M)]
    if _hm is None:
        warnings.append("DECISIONS.md: ヘッダに「最終更新: …(DNNN)」表記が見当たらない")
    elif _body_ids and int(_hm.group(1)) != max(_body_ids):
        warnings.append(f"DECISIONS.md: ヘッダの最終更新 D{int(_hm.group(1)):03d} が"
                        f"本文最大の D{max(_body_ids):03d} と一致しない(ヘッダの更新漏れ)")

# (g) 生成物鮮度: 説明文書の生成ブロック(README の対応表・overview.html の統計タイル・
# .github/harness/README.md の件数文言)を一次データから再生成しても差分が出ないか(A3-4)。
# gen-docs を import せず subprocess で呼ぶ(生成ロジックの正は gen-docs 側に1つだけ置き、
# この検査は「実行して exit code を見る」以上の知識を持たない)
gen_docs = os.path.join(ROOT, "tools", "gen-docs.py")
# 配布先では対象外(2026-09-10 A7-H-9/RG-10 の解消): gen-docs.py は本体専用(三重台帳 .gitattributes /
# sync EXCLUDE_FILES / intake TEMPLATE_EXCLUDE_REL で配布除外)で、生成先の README 対応表もプロジェクトでは
# アプリ README に置き換わる。本体の目印は DECISIONS.md(clone とテンプレート複製にだけあり、ZIP / sync /
# intake 経路には無い=gen-docs.py が届く経路と同じ集合。テンプレート複製は /00 で DECISIONS.md を消す)。
# 2026-09-17 追記: 目印は IS_BODY(doctor.is_body と同規則。テンプレ複製由来の DECISIONS.md が残る実プロジェクトを除外)
_body_signature = IS_BODY
if not _body_signature:
    infos.append("tools/gen-docs.py --check: 配布先(本体判定 IS_BODY=False)のため対象外"
                 + "(tools/gen-docs.py が残っているが本体専用。消してよい)" * bool(os.path.exists(gen_docs)))
elif os.path.exists(gen_docs):
    try:
        _proc = subprocess.run(
            [sys.executable, gen_docs, "--check"],
            capture_output=True, cwd=ROOT, timeout=120,
            encoding="utf-8", errors="replace")
        if _proc.returncode != 0:
            _detail = " / ".join((_proc.stdout or _proc.stderr or "").strip().splitlines()[:5])
            errors.append("tools/gen-docs.py --check: 生成文書が一次データとずれている"
                          f"(python tools/gen-docs.py --apply で再生成): {_detail}")
    except Exception as e:
        errors.append(f"tools/gen-docs.py --check: 実行失敗: {e}")
else:
    errors.append("tools/gen-docs.py が存在しない(本体リポジトリ(IS_BODY=True)なのに説明文書の生成物化検査ができない)")

# (h) 常時ロード予算ゲート: AGENTS.md のバイト数。全セッションに常時ロードされる憲法は
# D050(規範の憲法化)により1行ずつ複利的に太る圧力を受け続ける(実測 24.9→26.4KB。
# 第3回監査 A3-5)。閾値超過を機械検出し、追記より圧縮・PLATFORM.md 等への分離を促す
agents_md = os.path.join(ROOT, "AGENTS.md")
if os.path.exists(agents_md):
    _size = os.path.getsize(agents_md)
    if _size > 30 * 1024:
        errors.append(f"AGENTS.md: {_size} bytes が常時ロード予算 30KB を超過"
                      "(追記をやめ、圧縮または PLATFORM.md / スキルへの分離が必須)")
    elif _size > 25 * 1024:
        warnings.append(f"AGENTS.md: {_size} bytes が常時ロード予算の注意水準 25KB を超過"
                        "(30KB でエラーになる。次の改修で圧縮を検討)")
else:
    errors.append("AGENTS.md が存在しない")

# (i) 常駐指示ロードの実測(再監査 CC-9 / A5-5): InstructionsLoaded フック
# (log-instructions-loaded.py)が .github/hooks/logs/instructions-loaded.jsonl に残した
# 記録から、直近セッションの総ロード行数を INFO 表示する。(h) の静的バイト数と違い、
# nested CLAUDE.md / .claude/rules / @include / 圧縮後の再ロードまで含む「実際に載った量」。
# 常駐(load_reason=session_start)の合計が 200 行を超えたら WARN(A5-5 の予算)。
# ログはローカル専用(.gitignore)なので、無ければ何も言わない(CI では常に無い)。
_il_log = os.path.join(ROOT, ".github", "hooks", "logs", "instructions-loaded.jsonl")
if os.path.exists(_il_log):
    _il_rows = []
    try:
        for _ln in open(_il_log, encoding="utf-8", errors="replace"):
            _ln = _ln.strip()
            if not _ln:
                continue
            try:
                _r = json.loads(_ln)
            except Exception:
                continue
            if isinstance(_r, dict) and _r.get("session_id"):
                _il_rows.append(_r)
    except Exception:
        _il_rows = []
    if _il_rows:
        _last_sid = _il_rows[-1]["session_id"]
        _sess = [r for r in _il_rows if r.get("session_id") == _last_sid]
        # 常駐 = session_start + include(`@AGENTS.md` の展開。公式: imported files load at launch)。
        # 第3波(2026-09-17)の実測: matcher が session_start|compact の間は CLAUDE.md 18 行だけが記録され、
        # AGENTS.md 192 行が漏れていた(matcher に include を追加=統合コミットの断片)。include 行が無い記録は
        # その旨を INFO に出す(matcher 未更新の目印)。
        _RESIDENT_REASONS = ("session_start", "include")
        _resident = [r for r in _sess if r.get("load_reason") in _RESIDENT_REASONS]
        _res_lines = sum(int(r.get("lines") or 0) for r in _resident)
        _res_bytes = sum(int(r.get("bytes") or 0) for r in _resident)
        _other = [r for r in _sess if r.get("load_reason") not in _RESIDENT_REASONS]
        _oth_lines = sum(int(r.get("lines") or 0) for r in _other)
        _files = ", ".join(f"{r.get('file_path')}({r.get('load_reason')})" for r in _resident) or "(なし)"
        _no_include = "" if any(r.get("load_reason") == "include" for r in _resident) else \
            "。include 行なし=@AGENTS.md の展開が未記録(InstructionsLoaded の matcher に include が無いか、@import 無し)"
        infos.append(f"常駐指示の実測(直近セッション {_last_sid[:8]}…, {_sess[-1].get('ts', '')[:19]}): "
                     f"session_start+include {len(_resident)} ファイル {_res_lines} 行 / {_res_bytes} bytes "
                     f"[{_files}]; 遅延・再ロード {len(_other)} 件 {_oth_lines} 行{_no_include}")
        if _res_lines > 200:
            warnings.append(f"常駐指示の実測 {_res_lines} 行が予算 200 行(A5-5)を超過"
                            "(AGENTS.md / CLAUDE.md / .claude/rules の圧縮・PLATFORM.md やスキルへの分離を検討)")

# (h-2) 常駐指示のトークン推定ゲート(A3-5b。再監査 2026-09-09 RG-13: A3-5 はバイト数のみだった):
# AGENTS.md + CLAUDE.md(静的。(i) の実測があれば session_start のバイト数で校正)+ route-request の
# 毎依頼注入文(スクリプト内の ctx リテラルの最長分岐)+ inject-progress の注入上限
# (tools/usage-config.json の inject_progress_max_chars)を、文字種別の係数でトークンに換算する。
# 係数(公称の経験則。正は usage-config.json): 日本語(CJK・かな・全角記号)1 文字 = 1.0〜1.3 トークン、
# ASCII 4 文字 = 1 トークン。範囲の上限が resident_tokens_warn を超えたら WARN、それ以外は INFO。
# トークナイザ実測ではない(Claude のトークナイザは非公開)ため、値は「桁と傾向」を見る用途に限る。
_uc_path = os.path.join(ROOT, "tools", "usage-config.json")
try:
    _uc = json.load(open(_uc_path, encoding="utf-8-sig"))
except Exception:
    _uc = {}
_tok_warn = int(_uc.get("resident_tokens_warn") or 20000)
_tok_target = int(_uc.get("resident_tokens_target") or 12000)
_cjk_lo, _cjk_hi = (list(_uc.get("resident_token_per_cjk_char") or [1.0, 1.3]) + [1.0, 1.3])[:2]
_ascii_per_tok = float(_uc.get("resident_ascii_chars_per_token") or 4)
_inj_max_chars = int(_uc.get("inject_progress_max_chars") or 8700)


def _token_range(text):
    """(下限, 上限, CJK 文字数, ASCII 文字数)。CJK は U+3000 以降の全角域、それ以外の非空白は ASCII 扱い。"""
    cjk = sum(1 for ch in text if ord(ch) >= 0x3000)
    other = sum(1 for ch in text if ord(ch) < 0x3000 and not ch.isspace())
    return (cjk * _cjk_lo + other / _ascii_per_tok, cjk * _cjk_hi + other / _ascii_per_tok, cjk, other)


_resident_parts = []  # (名前, 文字列)
for _name in ("AGENTS.md", "CLAUDE.md"):
    _p = os.path.join(ROOT, _name)
    if os.path.exists(_p):
        _resident_parts.append((_name, open(_p, encoding="utf-8-sig").read()))
_rr = os.path.join(ROOT, ".github", "hooks", "scripts", "route-request.sh")
if os.path.exists(_rr):
    # 分岐ごとの注入文(ctx="…" / ctx="${ctx}…" のリテラル)のうち最長のものを毎依頼分として数える
    _lits = [m.group(1) for m in re.finditer(r'ctx="(?:\$\{ctx\})?([^"]{40,})"', open(_rr, encoding="utf-8").read())]
    if _lits:
        _resident_parts.append(("route-request 注入文(最長分岐・毎依頼)", max(_lits, key=len)))
_resident_parts.append(("inject-progress 注入上限(usage-config.json。inject-progress.* が実際に打ち切る値=第3波)", "あ" * int(_inj_max_chars * 0.7) + "a" * int(_inj_max_chars * 0.3)))
_lo = _hi = 0.0
_detail = []
for _name, _text in _resident_parts:
    _l, _h, _c, _o = _token_range(_text)
    _lo += _l
    _hi += _h
    _detail.append(f"{_name} {_c + _o} 文字≈{int(_l)}〜{int(_h)}")
_calib = ""
if os.path.exists(_il_log) and _il_rows:
    _meas_bytes = sum(int(r.get("bytes") or 0) for r in _resident if str(r.get("file_path", "")).endswith(("AGENTS.md", "CLAUDE.md")))
    _static_bytes = sum(len(t.encode("utf-8")) for n, t in _resident_parts if n in ("AGENTS.md", "CLAUDE.md"))
    if _meas_bytes and _static_bytes:
        _ratio = _meas_bytes / _static_bytes
        _lo *= _ratio
        _hi *= _ratio
        _calib = f"。実測校正: instructions-loaded の session_start バイト数 {_meas_bytes} / 静的 {_static_bytes} = ×{_ratio:.2f}"
_msg = (f"常駐指示のトークン推定: 約 {int(_lo):,}〜{int(_hi):,} トークン(注意水準 {_tok_warn:,}・目標 {_tok_target:,}"
        f"{'=目標超過 ' + format(int(_hi - _tok_target), ',') if _hi > _tok_target else ''}。係数 CJK {_cjk_lo}〜{_cjk_hi}/文字・"
        f"ASCII {_ascii_per_tok:g} 文字/トークン。内訳: " + " / ".join(_detail) + _calib + ")")
if _hi > _tok_warn:
    warnings.append(_msg + "(上限が resident_tokens_warn 超。AGENTS.md / CLAUDE.md の圧縮・注入文の短縮を検討。A3-5b/A5-5)")
else:
    infos.append(_msg)

# (h-3) 規範行の由来監査(A3-5 の未実装分。第3回監査 A3-5 / 再監査 RG-13): AGENTS.md の「必ず / 禁止 / しない」を
# 含む規範行のうち、(D0xx) 参照も実失敗の言及(実測・実例・事故・失敗・監査・実害・再発)も無い行を INFO で列挙する。
# 強制はしない(規範の削除・圧縮は人間の判断。指示層の棚卸しは A6-19 / w2-instr)。由来の無い規範は
# 「なぜあるのか」を説明できないため、圧縮候補の一覧として使う。
_agents_md = os.path.join(ROOT, "AGENTS.md")
if os.path.exists(_agents_md):
    _norm_re = re.compile(r"必ず|禁止|しない")
    _origin_re = re.compile(r"D0\d\d|D\d{3}|実測|実例|事故|失敗|監査|実害|再発|実機|実運用|Issue|CVE")
    _unsourced = []
    for _ln_no, _line in enumerate(open(_agents_md, encoding="utf-8-sig").read().splitlines(), 1):
        if _norm_re.search(_line) and not _origin_re.search(_line):
            _unsourced.append((_ln_no, _line.strip()))
    if _unsourced:
        infos.append(f"AGENTS.md: 由来(D 番号・実失敗)の無い規範行 {len(_unsourced)} 件(圧縮候補。強制なし。A3-5b): "
                     + " / ".join(f"L{n}「{t[:40]}{'…' if len(t) > 40 else ''}」" for n, t in _unsourced[:12])
                     + (" …" if len(_unsourced) > 12 else ""))
    else:
        infos.append("AGENTS.md: 由来(D 番号・実失敗)の無い規範行 0 件")
# (j) model-policy 整合(監査 2026-09-09 §4.5 の validate 検査。設計文書では「(f) model-policy 整合」):
# 役割別モデル/effort 方針(.github/harness/model-policy.yml)と生成物・各面の一致を検査する。
# 既定は WARN(CI 連続緑 1 サイクル後に ERROR へ昇格する二段導入)。--model-policy-strict で ERROR。
# 例外(常に ERROR): `model: auto` の全文 grep(Copilot の frontmatter 仕様外の記述)と生成物の鮮度(鏡割れ)。
MODEL_POLICY_STRICT = "--model-policy-strict" in sys.argv[1:]


def _mp(msg):
    (errors if MODEL_POLICY_STRICT else warnings).append("model-policy: " + msg)


sys.path.insert(0, os.path.join(ROOT, "tools"))
_mpmod = None
_policy = None
try:
    import model_policy as _mpmod  # noqa: E402
except Exception as e:  # noqa: BLE001
    errors.append(f"tools/model_policy.py を import できない: {e}")
if _mpmod is not None:
    try:
        _policy = _mpmod.load_policy(ROOT)
    except _mpmod.PolicyError as e:
        errors.append(f"model-policy: {e}")
if _policy is not None:
    import datetime as _dt
    _pm = _mpmod.models(_policy)
    _pl = _mpmod.legacy_models(_policy)
    _pe = _policy.get("external_models") or {}
    _roles = _mpmod.roles(_policy)
    _aliases = {_mpmod.claude_alias(_policy, k) for k in _pm if _mpmod.claude_alias(_policy, k)}
    _ids = {_mpmod.claude_id(_policy, k) for k in _pm if _mpmod.claude_id(_policy, k)}
    _displays = {_mpmod.copilot_display(_policy, k) for k in _pm if _mpmod.copilot_display(_policy, k)}
    _today = _dt.date.today()

    def _as_date(v):
        return v if isinstance(v, _dt.date) else _dt.date.fromisoformat(str(v))

    def _vs_keys(r):
        v = (r.get("copilot_vscode") or {}).get("model")
        return [v] if isinstance(v, str) else list(v or [])

    # (j-1) retrieved 鮮度
    try:
        if (_today - _as_date(_policy.get("retrieved"))).days > 30:
            _mp(f"retrieved {_policy.get('retrieved')} が 30 日超(可用性・価格・廃止日・版条件を再取得する)")
    except Exception:  # noqa: BLE001
        _mp("retrieved が日付として読めない")
    # (j-2) 許容値集合(ホスト別)・専用フィールドの混入・never_low・effort 非対応モデル
    for _role, _r in _roles.items():
        if _r.get("kind") not in _mpmod.ROLE_KINDS:
            _mp(f"roles.{_role}.kind {_r.get('kind')!r} は {_mpmod.ROLE_KINDS} のいずれか")
        _cc = _r.get("claude_code") or {}
        _extra = set(_cc) - _mpmod.CLAUDE_KEYS
        if _extra:
            _mp(f"roles.{_role}.claude_code に未知のキー {sorted(_extra)}(Copilot 専用フィールドの混入?)")
        try:
            _mpmod.normalize_claude_model(_policy, _cc.get("model"))
        except _mpmod.PolicyError as e:
            _mp(f"roles.{_role}: {e}")
        _eff = _cc.get("effort")
        if _eff not in (None, "inherit") and _eff not in _mpmod.CLAUDE_EFFORTS:
            _mp(f"roles.{_role}.claude_code.effort {_eff!r} は inherit / {_mpmod.CLAUDE_EFFORTS} のいずれか")
        if _cc.get("memory") is not None and _cc.get("memory") not in _mpmod.MEMORY_VALUES:
            _mp(f"roles.{_role}.claude_code.memory {_cc.get('memory')!r} は {_mpmod.MEMORY_VALUES} のいずれか")
        if _cc.get("maxTurns") is not None and (not isinstance(_cc["maxTurns"], int) or _cc["maxTurns"] <= 0):
            _mp(f"roles.{_role}.claude_code.maxTurns は正の整数")
        if _cc.get("background") is not None and not isinstance(_cc["background"], bool):
            _mp(f"roles.{_role}.claude_code.background は真偽値")
        if _r.get("never_low") and _eff not in ("high", "xhigh", "max"):
            _mp(f"roles.{_role}: never_low なのに claude_code.effort が {_eff!r}(high 以上を下限にする)")
        _vs = _r.get("copilot_vscode") or {}
        _extra = set(_vs) - _mpmod.VSCODE_KEYS
        if _extra:
            _mp(f"roles.{_role}.copilot_vscode に未知のキー {sorted(_extra)}(Claude 専用フィールドの混入?)")
        for _k in _vs_keys(_r):
            if _k not in _pm:
                _mp(f"roles.{_role}.copilot_vscode.model: 未知のモデルキー {_k!r}")
        _cli = _r.get("copilot_cli") or {}
        _extra = set(_cli) - _mpmod.CLI_KEYS
        if _extra:
            _mp(f"roles.{_role}.copilot_cli に未知のキー {sorted(_extra)}")
        if _cli.get("effortLevel") is not None and _cli["effortLevel"] not in _mpmod.CLI_EFFORTS:
            _mp(f"roles.{_role}.copilot_cli.effortLevel {_cli['effortLevel']!r} は {_mpmod.CLI_EFFORTS}(max 無し)")
        if _cli.get("contextTier") is not None and _cli["contextTier"] not in _mpmod.CLI_CONTEXT_TIERS:
            _mp(f"roles.{_role}.copilot_cli.contextTier {_cli['contextTier']!r} は {_mpmod.CLI_CONTEXT_TIERS}")
        if _cli.get("model") is not None and (not isinstance(_cli["model"], str) or _cli["model"] not in _pm):
            _mp(f"roles.{_role}.copilot_cli.model は単一のモデルキー(配列不可。配列は .agent.md 側)")
        _assigned = list(_vs_keys(_r))
        if _cli.get("model") in _pm:
            _assigned.append(_cli["model"])
        if _cc.get("model") in _pm:
            _assigned.append(_cc["model"])
        for _k in _assigned:
            if _pm[_k].get("effort_supported") is False and (_eff not in (None, "inherit") or _cli.get("effortLevel")):
                _mp(f"roles.{_role}: effort 非対応モデル {_k} に effort / effortLevel が付いている(無視される)")
        if _r.get("kind") != "subagent" and any(_cc.get(x) is not None for x in ("maxTurns", "background", "memory")):
            _mp(f"roles.{_role}: kind {_r.get('kind')} は .claude/agents に生成先が無いため maxTurns/background/memory は効かない")
    # (j-3) 役割集合の一致(policy.roles = .github/agents の basename、kind: subagent = .claude/agents の実体 3 本)
    if set(_roles) != set(agent_names):
        _mp(f"policy.roles と .github/agents の集合がずれている(policy のみ {sorted(set(_roles) - set(agent_names))} / "
            f"agents のみ {sorted(set(agent_names) - set(_roles))})")
    _sub_roles = {n for n, v in _roles.items() if v.get("kind") == "subagent"}
    _claude_agents = {os.path.basename(f)[:-len(".md")] for f in glob.glob(os.path.join(ROOT, ".claude", "agents", "*.md"))}
    if _sub_roles != _claude_agents:
        _mp(f"kind: subagent の役割 {sorted(_sub_roles)} と .claude/agents の実体 {sorted(_claude_agents)} がずれている"
            "(他の役割は生成先なし=文書のみ)")
    # (j-4) .github/agents の frontmatter: model は表示名(配列可)、auto は常に ERROR、Claude 専用キーの混入、生成マーカー
    for _f in agent_files:
        _fm, _ = read_frontmatter(_f)
        if _fm is None:
            continue
        _mv = _fm.get("model")
        if _mv is not None:
            for _v in (_mv if isinstance(_mv, list) else [_mv]):
                if _v == "auto":
                    errors.append(f"model-policy: {_f}: model: auto は Copilot の frontmatter 仕様に無い(generate-adapters で除去)")
                elif _v not in _displays:
                    _mp(f"{_f}: model {_v!r} が policy.models[*].copilot_display に無い")
        for _k in ("effort", "maxTurns", "background", "memory"):
            if _k in _fm:
                _mp(f"{_f}: Claude Code 専用フィールド {_k} が Copilot の正レイヤに混入")
        if _mpmod.GH_MARK not in open(_f, encoding="utf-8").read():
            _mp(f"{_f}: model 行の生成マーカー(# generated-from: model-policy.yml)が無い(python tools/generate-adapters.py)")
        _allowed = _fm.get("agents")
        if isinstance(_allowed, list) and os.path.basename(_f)[:-len(".agent.md")] in _roles:
            _inv = (_roles[os.path.basename(_f)[:-len(".agent.md")]].get("invokes") or [])
            if set(_inv) != set(_allowed):
                _mp(f"{_f}: policy の invokes {sorted(_inv)} が frontmatter の agents {sorted(_allowed)} と一致しない")
    # (j-5) .claude/agents の frontmatter: Copilot 専用キーの混入、model / effort の許容値
    for _af in glob.glob(os.path.join(ROOT, ".claude", "agents", "*.md")):
        _fm, _ = read_frontmatter(_af)
        if _fm is None:
            continue
        for _k in ("handoffs", "agents", "user-invocable", "disable-model-invocation", "model-policy"):
            if _k in _fm:
                _mp(f"{_af}: Copilot 専用フィールド {_k} が Claude Code アダプタに混入")
        _mv = _fm.get("model")
        if _mv is not None and _mv != "inherit" and _mv not in _aliases and _mv not in _ids:
            _mp(f"{_af}: model {_mv!r} は inherit / alias / フル ID(policy.models)のいずれでもない")
        if _fm.get("effort") is not None and _fm.get("effort") not in _mpmod.CLAUDE_EFFORTS:
            _mp(f"{_af}: effort {_fm.get('effort')!r} は {_mpmod.CLAUDE_EFFORTS} のいずれか")
    # (j-6) `model: auto` の全文 grep(frontmatter だけでなく本文も。audits/・DECISIONS.md・CHANGELOG.md は履歴なので除外)
    _auto_re = re.compile(r"model\s*:\s*`?auto`?", re.I)
    # 説明文(「`model: auto` は仕様に無いため使わない」等)は除外する: バッククォート/<code> で引用され、
    # かつ同じ行に否定・撤回の語があるもの。裸の出現(frontmatter・ピル等)は常に ERROR
    _auto_quoted = re.compile(r"(`|<code>)\s*model\s*:\s*auto\s*(`|</code>)", re.I)
    _auto_neg = ("使わない", "使いません", "仕様に無い", "仕様外", "除去", "書かない", "supersede", "廃止")
    # README は本体でだけ走査する(配布先の README はアプリの README)
    _scan = [os.path.join(ROOT, n) for n in ((("README.md", "README.en.md") if IS_BODY else ()) + ("CLAUDE.md", "AGENTS.md"))]
    for _pat in ((".github", "agents", "*.md"), (".github", "harness", "*.md"), (".github", "harness", "*.html"),
                 (".github", "skills", "*", "SKILL.md"), (".github", "prompts", "*.md"), (".github", "instructions", "*.md"),
                 (".claude", "agents", "*.md"), (".claude", "commands", "*.md"), (".claude", "skills", "*", "SKILL.md")):
        _scan += glob.glob(os.path.join(ROOT, *_pat))
    for _doc in _scan:
        if not os.path.exists(_doc):
            continue
        _rel = os.path.relpath(_doc, ROOT).replace(os.sep, "/")
        for _ln_no, _line in enumerate(open(_doc, encoding="utf-8").read().splitlines(), 1):
            if _auto_re.search(_line):
                if _auto_quoted.search(_line) and any(n in _line for n in _auto_neg):
                    continue
                if _rel in ("AGENTS.md", "CLAUDE.md"):
                    warnings.append(f"model-policy: {_rel}:{_ln_no}: `model: auto` の言及が残っている(保守モードで人間が編集する保護ファイル)")
                else:
                    errors.append(f"model-policy: {_rel}:{_ln_no}: `model: auto` の言及(Copilot の frontmatter 仕様外)。方針の正は model-policy.yml")
    # (j-7) プラン行列(全プランで 1 つ以上 true。役割の配列は各プランで使えるモデルを 1 つ以上含む)
    for _k, _m in _pm.items():
        _plans = _m.get("copilot_plans") or {}
        if not any(bool(v) for v in _plans.values()):
            _mp(f"models.{_k}: copilot_plans がどのプランでも true でない")
        for _pk, _pv in _plans.items():
            if not isinstance(_pv, bool):
                _mp(f"models.{_k}.copilot_plans.{_pk} は真偽値(管理者ポリシーの注記は admin_policy_note へ)")
    for _role, _r in _roles.items():
        _vk = _vs_keys(_r)
        if not _vk:
            continue
        _plan_names = set()
        for _k in _vk:
            _plan_names |= set(_pm.get(_k, {}).get("copilot_plans") or {})
        for _pn in sorted(_plan_names):
            if not any((_pm.get(_k, {}).get("copilot_plans") or {}).get(_pn) for _k in _vk):
                _mp(f"roles.{_role}.copilot_vscode.model: プラン {_pn} で使えるモデルが配列に無い(plan_fallbacks を足す)")
    # (j-8) 廃止 30 日前: deprecated_on <= 今日+30 のモデルを役割・fallback が参照していれば WARN
    for _k, _m in list(_pm.items()) + list(_pl.items()):
        if not _m.get("deprecated_on"):
            continue
        try:
            _dd = _as_date(_m["deprecated_on"])
        except Exception:  # noqa: BLE001
            _mp(f"models.{_k}.deprecated_on が日付として読めない")
            continue
        if _dd > _today + _dt.timedelta(days=30):
            continue
        _used = []
        for _role, _r in _roles.items():
            _pf = _r.get("plan_fallbacks") or {}
            _pf_keys = [x for v in (_pf.values() if isinstance(_pf, dict) else []) for x in (v or [])]
            if _k in _vs_keys(_r) or (_r.get("copilot_cli") or {}).get("model") == _k \
                    or (_r.get("claude_code") or {}).get("model") == _k or _k in _pf_keys:
                _used.append(_role)
        if _used:
            _mp(f"models.{_k}: 廃止 {_dd} まで 30 日以内なのに役割 {_used} が参照している")
        else:
            infos.append(f"model-policy: {_k} は {_dd} に廃止(役割からの参照なし。deny/単価表のみ)")
    # (j-9) 親≧子ティア(仮説。verified_on が null の間は「未検証の仮説」と明記)
    _toh = _policy.get("tier_order_hypothesis") or {}
    _order = list(_toh.get("order") or [])
    _known = set(_pm) | set(_pl) | set(_pe)
    for _k in _order:
        if _k not in _known:
            _mp(f"tier_order_hypothesis.order: 未知のキー {_k}(models / legacy_models / external_models に登録する)")
    _tier_label = "未検証の仮説" if not _toh.get("verified_on") else f"verified {_toh.get('verified_on')}"
    for _role, _r in _roles.items():
        _pk = next(iter(_vs_keys(_r)), None)
        if _pk is None or _pk not in _order:
            continue
        for _child in _r.get("invokes") or []:
            _ck = next(iter(_vs_keys(_roles.get(_child) or {})), None)
            if _ck is None or _ck not in _order:
                continue
            if _order.index(_ck) < _order.index(_pk):
                _mp(f"roles.{_role} → {_child}: 子 {_ck} が親 {_pk} より上位ティア({_tier_label}。"
                    "Copilot VS Code は起動拒否・CLI は無言降格)")
    # (j-10) min_version: verified は docs のキー集合内、版は数字とドット
    for _host, _h in ((_policy.get("min_version") or {})).items():
        _docs = (_h or {}).get("docs") or {}
        for _k, _v in list(_docs.items()) + list(((_h or {}).get("verified") or {}).items()):
            if not re.fullmatch(r"\d+(\.\d+)+", str(_v)):
                _mp(f"min_version.{_host}: {_k} = {_v!r} は版番号の形ではない")
        for _k in ((_h or {}).get("verified") or {}):
            if _k not in _docs and _k != "effort_frontmatter":
                _mp(f"min_version.{_host}.verified の {_k} は docs 側に無いキー")
    # (j-11) 生成物の鮮度(python tools/generate-adapters.py --check 相当。鏡割れは常に ERROR)
    try:
        for _rel in _mpmod.stale_targets(ROOT, _policy):
            errors.append(f"model-policy: 生成物 {_rel} が方針とずれている(python tools/generate-adapters.py で再生成)")
    except _mpmod.PolicyError as e:
        errors.append(f"model-policy: {e}")
    # (j-12) .claude/settings.json: permissions.deny が --print-deny と一致、guard-subagent-model の配線
    _missing, _extra = _mpmod.settings_deny_status(ROOT, _policy)
    if _missing is None:
        warnings.append("model-policy: .claude/settings.json を読めないため deny 照合をスキップ")
    else:
        if _missing:
            _mp(f".claude/settings.json の permissions.deny に未反映: {_missing}"
                "(python tools/generate-adapters.py --print-deny / --apply-deny)")
        if _extra:
            _mp(f".claude/settings.json に方針に無い Agent(model:…) deny: {_extra}")
    try:
        _stxt = open(os.path.join(ROOT, ".claude", "settings.json"), encoding="utf-8").read()
        if "guard-subagent-model" not in _stxt:
            _mp("guard-subagent-model が .claude/settings.json の PreToolUse(matcher Agent|Task)に未配線(三層強制の第2層)")
    except Exception:  # noqa: BLE001
        pass
    # (j-13) .github/copilot/settings.json: JSON・_generated_from・キー集合・effortLevel の許容値
    _cs = os.path.join(ROOT, ".github", "copilot", "settings.json")
    if os.path.exists(_cs):
        try:
            _csd = json.loads(open(_cs, encoding="utf-8-sig").read())
            if _csd.get("_generated_from") != _mpmod.POLICY_REL:
                _mp(".github/copilot/settings.json に _generated_from が無い(手書き?)")
            for _k in set(_csd) - {"_generated_from", "_generated_note", "model", "effortLevel", "contextTier", "subagents"}:
                _mp(f".github/copilot/settings.json: 未知のトップレベルキー {_k}")
            for _name, _ent in ((_csd.get("subagents") or {}).get("agents") or {}).items():
                for _k in set(_ent) - {"model", "effortLevel", "contextTier"}:
                    _mp(f".github/copilot/settings.json: subagents.agents.{_name} の未知のキー {_k}")
                if _ent.get("effortLevel") not in (None,) + tuple(_mpmod.CLI_EFFORTS):
                    _mp(f".github/copilot/settings.json: subagents.agents.{_name}.effortLevel {_ent.get('effortLevel')!r} は {_mpmod.CLI_EFFORTS}")
        except Exception as e:  # noqa: BLE001
            errors.append(f".github/copilot/settings.json: JSON parse error: {e}")
    # (j-14) 保護・配布 5 面: model-policy.yml と .github/copilot/ が deny・guard sh/ps1・CODEOWNERS・SYNC_GLOBS/REVIEW_FILES・.gitignore にあるか
    _faces = []
    if deny_blob is not None:
        _faces.append(("permissions.deny", deny_blob, "model-policy.yml", ".github/copilot/"))
    if guard_sh_pattern is not None:
        _faces.append(("guard-harness-config-edit.sh", guard_sh_pattern, "model-policy", "copilot"))
    if guard_ps1_pattern is not None:
        _faces.append(("guard-harness-config-edit.ps1", guard_ps1_pattern, "model-policy", "copilot"))
    for _name, _rel in (("CODEOWNERS", os.path.join(".github", "CODEOWNERS")), ("sync-harness.py SYNC_GLOBS/REVIEW_FILES", os.path.join("tools", "sync-harness.py"))):
        try:
            _faces.append((_name, open(os.path.join(ROOT, _rel), encoding="utf-8").read(), "model-policy.yml", ".github/copilot/"))
        except Exception:  # noqa: BLE001
            pass
    for _name, _blob, _p1, _p2 in _faces:
        if _p1 not in _blob:
            _mp(f"保護5面: .github/harness/model-policy.yml が {_name} に見当たらない")
        if _p2 not in _blob:
            _mp(f"保護5面: .github/copilot/ が {_name} に見当たらない")
    try:
        if ".github/copilot/settings.local.json" not in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read():
            _mp(".gitignore に .github/copilot/settings.local.json が無い(公式: ローカル上書きは gitignore へ)")
    except Exception:  # noqa: BLE001
        pass
    infos.append(f"model-policy: roles {len(_roles)} / models {len(_pm)} / deny 規則 {len(_mpmod.deny_rules(_policy))} 件"
                 f"(検査は {'ERROR' if MODEL_POLICY_STRICT else 'WARN'} 既定)")

# (k) 共通ライブラリ配線(D072 / 第5回 RG-4・CP-1): パス候補の網羅収集と読み取り系ツール除外の
# 正は _paths.sh / _paths.ps1 の1箇所。各ガードがそれを source(ドットソース)していない=
# 単一フィールド判定に退行している(uri/notebook_path/apply_patch 経由の素通り)を検出する。
_paths_users = ["guard-harness-config-edit", "guard-template-edit", "guard-secret-leak",
                "warn-gate-tamper", "warn-stale-gate", "check-doc-chars",
                "guard-phase-scope",  # phase-scope は第6波(A6-20)で複製を _paths に差し替え
                "guard-done-evidence", "guard-external-effect"]  # 第2波 w2-supply(A2-4b / IA-20260831-01)の新規 2 本
_scripts_dir = os.path.join(ROOT, ".github", "hooks", "scripts")
for _lib, _ext, _needle in (("_paths.sh", ".sh", "/_paths.sh"), ("_paths.ps1", ".ps1", "_paths.ps1")):
    if not os.path.exists(os.path.join(_scripts_dir, _lib)):
        errors.append(f".github/hooks/scripts/{_lib} が存在しない(ガードのパス候補収集・読取除外の正)")
        continue
    for _g in _paths_users:
        _gp = os.path.join(_scripts_dir, _g + _ext)
        if not os.path.exists(_gp):
            continue
        try:
            if _needle not in open(_gp, encoding="utf-8-sig").read():
                # ERROR 昇格(A7-M-6): 共通ライブラリを source しないガードは単一フィールド判定に退行し素通りが再発する
                errors.append(f".github/hooks/scripts/{_g}{_ext}: {_lib} を source していない"
                              "(uri/notebook_path/apply_patch 経由の判定と読取除外が退行する。D072)")
        except Exception:
            pass
# (k-2) 8.3 短縮名の展開(H-10/RG-7 の横展開): 保護ディレクトリ名そのもの(.github→GITHUB~1、
# *_template.md→REQUIR~1.MD)が短縮名で渡ると字面照合が外れる。deny 型ガード 2 本が比較前に
# _paths の展開関数(sh: expand_candidate_83_into / ps1: ConvertTo-LongPathCandidate)を呼んでいるかを見る。
for _g in ("guard-harness-config-edit", "guard-template-edit"):
    for _ext, _fn in ((".sh", "expand_candidate_83_into"), (".ps1", "ConvertTo-LongPathCandidate")):
        _gp = os.path.join(_scripts_dir, _g + _ext)
        if not os.path.exists(_gp):
            continue
        try:
            if _fn not in open(_gp, encoding="utf-8-sig").read():
                errors.append(f".github/hooks/scripts/{_g}{_ext}: 8.3 短縮名の展開({_fn})を比較前に通していない"
                              "(GITHUB~1/hooks 等の短縮名パスが保護判定を素通りする。H-10/RG-7。ERROR 昇格=A7-M-6)")
        except Exception:
            pass

# (k-3) 判定ログの共通化(A6-20 / RC-5 / RC-11): 書式(1 行 JSONL)・redaction・置き場の正は _log.sh / _log.ps1 /
# _log.py と .github/harness/privacy-patterns.json の 1 か所。scripts/ 内に旧 TSV(hook-decisions.log)を直接参照する箇所や
# 自前の hook_log / Write-HookLog 実装が残っていれば ERROR(旧 TSV の読み手としての後方互換は _log.py だけが持つ)。
# hook_log / Write-HookLog を呼ぶのに _log を source / import していないスクリプトは WARN(無記録で動くため
# 「判定ログが空」が壊れているのか発火しなかったのか切り分けられなくなる)。privacy-patterns.json は 3 系統が
# 読むため JSON 妥当性・1 エントリ 1 行(sh の行読みフォールバックの前提)・各 pattern のコンパイル可否を検査する。
for _lib in ("_log.sh", "_log.ps1", "_log.py"):
    if not os.path.exists(os.path.join(_scripts_dir, _lib)):
        errors.append(f".github/hooks/scripts/{_lib} が存在しない(判定ログ JSONL の共通実装。A6-20)")
# redaction の正は .github/harness/privacy-patterns.json(2026-09-21 に .github/hooks/scripts/ から移動。Copilot CLI が
# .github/hooks/**/*.json をすべてフック設定として読み「hooks must be an object」を出すため。D097)
_pp_path = os.path.join(ROOT, ".github", "harness", "privacy-patterns.json")
if not os.path.exists(_pp_path):
    errors.append(".github/harness/privacy-patterns.json が存在しない(判定ログ redaction / 欄分類の正。A6-20)")
if os.path.exists(os.path.join(_scripts_dir, "_privacy-patterns.json")):
    errors.append(".github/hooks/scripts/_privacy-patterns.json が残っている(.github/harness/ へ移動済み。Copilot CLI がフック設定と誤認する。D097)")
if os.path.exists(os.path.join(ROOT, ".github", "hooks", "plugin-hooks.json")):
    errors.append(".claude-plugin/hooks.json が残っている(.claude-plugin/hooks.json へ移動済み。Copilot CLI が読んで fail-closed になる。D097)")
if os.path.exists(_pp_path):
    try:
        _pp_text = open(_pp_path, encoding="utf-8-sig").read()
        _pp = json.loads(_pp_text)
        _pp_entries = _pp.get("redact") if isinstance(_pp, dict) else None
        if not isinstance(_pp_entries, list) or not _pp_entries:
            errors.append(".github/harness/privacy-patterns.json: redact が空(判定ログの redaction が無効になる)")
        else:
            _pp_lines = len(re.findall(r'^\s*\{[^\n]*"pattern"[^\n]*\}\s*,?\s*$', _pp_text, re.M))
            if _pp_lines != len(_pp_entries):
                errors.append(".github/harness/privacy-patterns.json: 1 エントリ 1 行の規約違反"
                              f"(エントリ {len(_pp_entries)} / 1 行のエントリ {_pp_lines}。sh の行読みフォールバックが拾えない)")
            for _e in _pp_entries:
                _pat = _e.get("pattern") if isinstance(_e, dict) else None
                if not isinstance(_pat, str) or not _pat:
                    errors.append(".github/harness/privacy-patterns.json: pattern が空か文字列でないエントリがある")
                    continue
                try:
                    re.compile(_pat)
                except re.error as _ex:
                    errors.append(f".github/harness/privacy-patterns.json: pattern {_pat!r} が正規表現として不正({_ex})")
                if re.search(r"\[sdwSDW]|\[\[:", _pat):
                    warnings.append(f".github/harness/privacy-patterns.json: pattern {_pat!r} に \\s \\d \\w / [[:class:]] が"
                                    "含まれる(bash と .NET/Python で解釈が割れる。明示の文字クラスで書く)")
    except Exception as _ex:
        errors.append(f".github/harness/privacy-patterns.json: JSON として読めない({_ex})")
for _sp in sorted(glob.glob(os.path.join(_scripts_dir, "*"))):
    _name = os.path.basename(_sp)
    if not re.search(r"\.(sh|ps1|py)$", _name) or _name.startswith("_log.") or _name.startswith("selftest."):
        continue
    try:
        _src = open(_sp, encoding="utf-8-sig", errors="replace").read()
    except Exception:
        continue
    _rel = f".github/hooks/scripts/{_name}"
    if "hook-decisions.log" in _src:
        errors.append(f"{_rel}: 旧 TSV 判定ログ(hook-decisions.log)を直接参照している"
                      "(書式・置き場の正は _log.* の hook-decisions.jsonl。A6-20)")
    if _name.endswith(".sh"):
        if re.search(r"^hook_log\(\)\s*\{", _src, re.M):
            errors.append(f"{_rel}: 自前の hook_log 実装が残っている(_log.sh を source する。A6-20)")
        if re.search(r"^\s*hook_log\s+\S", _src, re.M) and "/_log.sh" not in _src:
            errors.append(f"{_rel}: hook_log を呼ぶのに _log.sh を source していない(無記録になる。ERROR 昇格=A7-M-6)")
    elif _name.endswith(".ps1"):
        if re.search(r"^function Write-HookLog\b", _src, re.M):
            errors.append(f"{_rel}: 自前の Write-HookLog 実装が残っている(_log.ps1 をドットソースする。A6-20)")
        if re.search(r"^\s*Write-HookLog\s+\S", _src, re.M) and "_log.ps1" not in _src:
            errors.append(f"{_rel}: Write-HookLog を呼ぶのに _log.ps1 をドットソースしていない(無記録になる。ERROR 昇格=A7-M-6)")
    else:
        if re.search(r"^def hook_log\(", _src, re.M) and "import _log" not in _src:
            errors.append(f"{_rel}: 自前の hook_log 実装が残っている(_log.py を import する。A6-20)")

# (k-4) 判定ログの欄順(R-09 / A2-7): 3 系統の writer(_log.py の FIELD_ORDER・_log.sh の printf 書式・_log.ps1 の行組立)の
# 欄名と順序が一致し、末尾が duration_ms / host であること(欄は末尾にだけ足す=旧行を欠落 null で読める後方互換の前提。
# 読み手 tools/hook-metrics.py・受領書の hooks 欄・selftest 両系の canary が同じ欄順を前提にする)。
_lo_py = _lo_sh = _lo_ps = None
try:
    _m = re.search(r"^FIELD_ORDER\s*=\s*\((.*?)\)", open(os.path.join(_scripts_dir, "_log.py"), encoding="utf-8-sig").read(), re.S | re.M)
    _lo_py = re.findall(r'"([a-z_]+)"', _m.group(1)) if _m else None
    _m = re.search(r"printf '(\{\"ts\".*?\})\\n'", open(os.path.join(_scripts_dir, "_log.sh"), encoding="utf-8-sig").read())
    _lo_sh = re.findall(r'"([a-z_]+)":', _m.group(1)) if _m else None
    _m = re.search(r"\$line = '\{\"ts\".*?\"\}'", open(os.path.join(_scripts_dir, "_log.ps1"), encoding="utf-8-sig").read(), re.S)
    _lo_ps = re.findall(r'"([a-z_]+)":', _m.group(0)) if _m else None
except Exception as _ex:  # noqa: BLE001
    warnings.append(f"判定ログの欄順を 3 系統から読めない({_ex})")
if _lo_py and _lo_sh and _lo_ps:
    if not (_lo_py == _lo_sh == _lo_ps):
        errors.append(f"判定ログの欄順が 3 系統で一致しない(py {_lo_py} / sh {_lo_sh} / ps1 {_lo_ps}。欄は末尾にだけ足す。R-09 / A2-7)")
    elif _lo_py[-2:] != ["duration_ms", "host"]:
        errors.append(f"判定ログの欄順の末尾が duration_ms / host でない({_lo_py}。R-09 / A2-7)")
    else:
        infos.append(f"判定ログの欄順: 3 系統一致 {len(_lo_py)} 欄({', '.join(_lo_py)})")
elif _lo_py is not None or _lo_sh is not None or _lo_ps is not None:
    warnings.append(f"判定ログの欄順を読めない系統がある(py {_lo_py} / sh {_lo_sh} / ps1 {_lo_ps})")

# (l) Copilot 入口スキルの鮮度(再監査 2026-09-09 CP-2 / A6-17): Agent Host は prompt files を読まないため、
# .github/skills/<nn>-<name>/SKILL.md(prompts + agent バインドを参照する薄い user-invocable スキル)を
# tools/generate-adapters.py 第5節が生成する。正(prompt / agent の handoffs)を変えて再生成し忘れた鏡割れ、
# prompt を削除・改名したのに残った孤児、未生成、.claude/skills 側の禁止ポインタを ERROR にする
# (生成ロジックは copilot_entry_skills 側に 1 つだけ置き、ここは status() の結果を見るだけ)。
if _ces is not None:
    try:
        _stale, _orphans, _missing, _conflicts = _ces.status(ROOT)
        for _rel in _stale:
            errors.append(f"{_rel}: Copilot 入口スキルが正(prompt / agent)とずれている(python tools/generate-adapters.py で再生成)")
        for _rel in _missing:
            errors.append(f"{_rel}: Copilot 入口スキルが未生成(python tools/generate-adapters.py)")
        for _name in _orphans:
            errors.append(f".github/skills/{_name}/: 孤児の入口スキル(対応する .github/prompts/{_name}.prompt.md が無い。"
                          "python tools/generate-adapters.py で削除される)")
        for _name in _conflicts:
            errors.append(f".claude/skills/{_name}/: Copilot 入口スキルのポインタは作らない(/{_name} が .claude/commands と衝突)")
        if not (_stale or _missing or _orphans or _conflicts):
            infos.append(f"Copilot 入口スキル: {len(_ces.targets(ROOT))} 件が prompts と一致(.claude/skills ポインタなし)")
    except _ces.EntryError as e:
        errors.append(f"copilot-entry-skills: {e}")

# (m) plugin マニフェスト(再監査 2026-09-09 CP-7 / RG-17 / A6-23): 正は plugin.json(Agent Plugins 1.0)、
# .claude-plugin/plugin.json と .claude-plugin/hooks.json は tools/plugin_manifests.py の生成物。
# - plugin.json: $schema 固定・name 必須(小文字英数字とハイフン/ドット)・トップレベルは 1.0 の 10 キーのみ・
#   version は semver・description に「Antigravity対応」(R-08)が無い → 違反は ERROR
# - .claude-plugin/plugin.json: name 必須・name/version が plugin.json と一致・パス系(skills/commands/agents/hooks 等)は
#   ./ 始まりで参照先が実在 → 違反は ERROR
# - 生成物の鮮度(python tools/generate-adapters.py --check 相当)は鏡割れとして常に ERROR
# - 保護・配布面: .claude-plugin/ が permissions.deny・CODEOWNERS・SYNC_GLOBS にあるか(WARN)
_pmmod = None
try:
    import plugin_manifests as _pmmod  # noqa: E402
except Exception as e:  # noqa: BLE001
    errors.append(f"tools/plugin_manifests.py を import できない: {e}")
if _pmmod is not None:
    _pm_source = None
    _pm_path = os.path.join(ROOT, _pmmod.SOURCE_REL)
    if not os.path.exists(_pm_path):
        errors.append(f"{_pmmod.SOURCE_REL} が無い(plugin マニフェストの正・版数の正)")
    else:
        try:
            _pm_source = json.loads(open(_pm_path, encoding="utf-8-sig").read())
        except Exception as e:  # noqa: BLE001
            errors.append(f"{_pmmod.SOURCE_REL}: JSON parse error: {e}")
    if _pm_source is not None:
        for _p in _pmmod.validate_source(_pm_source):
            errors.append(f"{_pmmod.SOURCE_REL}: {_p}(Agent Plugins 1.0 スキーマ)")
        for _p in _pmmod.validate_claude_manifest(ROOT, _pm_source):
            errors.append(_p)
        try:
            for _rel in _pmmod.stale_targets(ROOT, _pm_source):
                errors.append(f"{_rel}: plugin.json / .claude/settings.json から再生成すると差分が出る"
                              "(python tools/generate-adapters.py で再生成)")
        except _pmmod.ManifestError as e:
            errors.append(f"plugin-manifests: {e}")
        if deny_blob is not None and ".claude-plugin/" not in deny_blob:
            warnings.append(".claude/settings.json の permissions.deny に .claude-plugin/** が無い"
                            "(Claude Code plugin マニフェストの保護面。guard sh/ps1 は plugin\\.json$ で一致する)")
        for _name, _rel in (("CODEOWNERS", os.path.join(".github", "CODEOWNERS")),
                            ("sync-harness.py SYNC_GLOBS", os.path.join("tools", "sync-harness.py"))):
            try:
                if ".claude-plugin/" not in open(os.path.join(ROOT, _rel), encoding="utf-8").read():
                    warnings.append(f"{_name} に .claude-plugin/ が見当たらない(plugin マニフェストの保護・配布面)")
            except Exception:  # noqa: BLE001
                pass
        infos.append(f"plugin-manifests: {_pmmod.SOURCE_REL} name={_pm_source.get('name')} version={_pm_source.get('version')} "
                     f"/ 生成物 {_pmmod.CLAUDE_MANIFEST_REL}, {_pmmod.PLUGIN_HOOKS_REL}")

# (n) ホスト版要求(再監査 2026-09-09 CC-1 / OP-2 / A6-8、第3回 A3-3): .github/harness/platform-requirements.json の
# 構造・自己整合(load が検査)と、その鏡の一致(model-policy.yml の min_version を PyYAML 無しで読む regex 読み=
# doctor / gen-docs が使う経路と yaml の一致、tools/usage-config.json の host_min_version = claude_code.min)、
# claude --version の照合。環境検査の正は tools/doctor.py(min 未満 FAIL / required 未満 WARN)で、validate は
# CI(claude 不在)を赤にしないよう最低版未満を WARN、未導入・要求版未満は INFO に留める。
try:
    import platform_requirements as _preq  # noqa: E402
    _req = _preq.load(ROOT)
except Exception as e:  # noqa: BLE001
    _req = None
    errors.append(f".github/harness/platform-requirements.json: {e}")
if _req is not None:
    _cc = _req["claude_code"]
    if _policy is not None:
        _yaml_docs = {k: str(v) for k, v in ((((_policy.get("min_version") or {}).get("claude_code") or {}).get("docs") or {}).items())}
        _rx_docs = _preq.regex_policy_min_versions(ROOT)
        if _yaml_docs != _rx_docs:
            warnings.append("platform-requirements: model-policy.yml の min_version.claude_code.docs を regex で読んだ結果が yaml と一致しない"
                            f"(regex {_rx_docs} / yaml {_yaml_docs}。doctor / gen-docs は regex 読みに依存する。2 スペース字下げ・引用文字列の書式に戻す)")
    try:
        _ucfg = json.load(open(os.path.join(ROOT, "tools", "usage-config.json"), encoding="utf-8-sig"))
        if str(_ucfg.get("host_min_version")) != str(_cc["min"]):
            warnings.append(f"platform-requirements: tools/usage-config.json の host_min_version {_ucfg.get('host_min_version')!r} が "
                            f"claude_code.min {_cc['min']!r} と一致しない(正は platform-requirements.json。受領書の host_below_min_version 判定がずれる)")
    except Exception:  # noqa: BLE001
        pass
    try:
        import doctor as _doctor  # noqa: E402
        _lv, _msg = _doctor.claude_version_status(ROOT)
    except Exception as e:  # noqa: BLE001
        _lv, _msg = "missing", f"tools/doctor.py を実行できない: {e}"
    if _lv == "fail":
        warnings.append(f"claude --version: {_msg}(最低版未満。環境検査の正は python tools/doctor.py。CI では claude 不在のため対象外)")
    else:
        infos.append(f"claude --version: {_msg}")
    infos.append(f"platform-requirements: claude_code min {_cc['min']} / required {_cc['required']} / e2e min_cli {_cc['e2e']['min_cli']} "
                 f"/ features {len(_cc['features'])} 件(as_of {_req.get('as_of')})")

# (o) 浮動参照の禁止(codex 監査 2026-08-31 H-02 / IA-20260831-04): 設定・スキル・文書・ワークフロー・ツールに
# 「<パッケージ>@latest|next|canary」「<owner>/<repo>@main|master」「<イメージ>:latest」の形の浮動参照が
# あれば ERROR。「監査した版」と「実行される版」を結び付けない参照はサプライチェーン統制(AGENTS.md /
# skill-authoring スキル)に反する。固定と更新の手順は SECURITY.md「外部依存の固定と更新」。名前を伴わない
# 散文の言及(`@latest` 単独)は対象外。履歴(audits/・DECISIONS.md・CHANGELOG.md)と logs/ は走査しない。
# 本体(IS_BODY)は ERROR、配布先プロジェクト(アプリ側の docs/ に `<image>:latest` 等が書かれ得る)は WARN。
_float_re = re.compile(
    r"(?<![\w/.@-])(?:@?[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+@(?:latest|next|canary|main|master)(?![\w.-])"
    r"|(?<![\w/.:@-])[A-Za-z0-9][A-Za-z0-9_./-]*:latest(?![\w.-])")
_float_globs = ["README.md", "README.en.md", "plugin.json", ".mcp.json", "docs/**/*.md",
                ".github/**/*.md", ".github/**/*.json", ".github/**/*.yml", ".github/**/*.yaml",
                ".github/**/*.example", ".github/**/*.html", ".github/hooks/scripts/*",
                ".vscode/*.json", ".claude/**/*.md", ".claude/**/*.json", ".claude-plugin/*.json",
                ".agents/**/*.md", "tools/*.py"]
_float_seen = set()
for _pat in _float_globs:
    for _fp in sorted(glob.glob(os.path.join(ROOT, _pat), recursive=True)):
        _relp = os.path.relpath(_fp, ROOT).replace(os.sep, "/")
        if (_relp in _float_seen or not os.path.isfile(_fp) or "/logs/" in _relp
                or "node_modules" in _relp or "__pycache__" in _relp):
            continue
        _float_seen.add(_relp)
        try:
            _flines = open(_fp, encoding="utf-8-sig", errors="replace").read().splitlines()
        except Exception:
            continue
        for _ln_no, _line in enumerate(_flines, 1):
            _fm = _float_re.search(_line)
            if _fm:
                (errors if IS_BODY else warnings).append(f"{_relp}:{_ln_no}: 浮動参照 '{_fm.group(0)}'(版・commit SHA・integrity で固定する。"
                              "SECURITY.md「外部依存の固定と更新」。codex H-02 / IA-20260831-04)")

# (p) リリースの不変性(codex 監査 2026-08-31 H-07 / IA-20260831-09、再監査 2026-08-31 A7-M-1)。本体リポジトリ
# (DECISIONS.md がある)だけが対象: plugin.json の version が CHANGELOG.md の版見出し `## [X.Y.Z]` に無ければ
# ERROR(版数の正は plugin.json、CHANGELOG はその要約=CHANGELOG 規約)。CHANGELOG の各版に annotated tag
# vX.Y.Z が無い・lightweight なら WARN(タグ付与は人間の作業。手順は SECURITY.md「リリースの不変性」)。
# git 不在・非リポジトリは INFO で照合不能を明示する。配布先プロジェクトの CHANGELOG はアプリのものなので見ない。
_changelog = os.path.join(ROOT, "CHANGELOG.md")
_plugin_j = os.path.join(ROOT, "plugin.json")
if IS_BODY and os.path.exists(_changelog) and os.path.exists(_plugin_j):  # 目印は IS_BODY(D086 追記 2。配布先の CHANGELOG はアプリのもの)
    _cl_versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", open(_changelog, encoding="utf-8").read(), re.M)
    try:
        _pv = json.loads(open(_plugin_j, encoding="utf-8").read()).get("version")
    except Exception:
        _pv = None
    if _pv and _cl_versions and _pv not in _cl_versions:
        errors.append(f"plugin.json version {_pv} が CHANGELOG.md の版見出しに無い"
                      "(版数の正は plugin.json。両方を同時に更新する=CHANGELOG 規約)")
    _tag_types = None
    try:
        _tp = subprocess.run(["git", "for-each-ref", "refs/tags", "--format=%(refname:short) %(objecttype)"],
                             capture_output=True, cwd=ROOT, timeout=30, encoding="utf-8", errors="replace")
        if _tp.returncode == 0:
            _tag_types = {}
            for _tl in _tp.stdout.splitlines():
                _tparts = _tl.split()
                if _tparts:
                    _tag_types[_tparts[0]] = _tparts[1] if len(_tparts) > 1 else ""
    except Exception:
        _tag_types = None
    if _tag_types is None:
        infos.append("release: git タグを照合できない(git 不在または非リポジトリ)")
    else:
        _no_tag = [v for v in _cl_versions if ("v" + v) not in _tag_types]
        _light = [v for v in _cl_versions if _tag_types.get("v" + v) == "commit"]
        if _no_tag:
            warnings.append("release: CHANGELOG の版に git tag が無い: " + ", ".join("v" + v for v in _no_tag)
                            + "(人間の作業: git tag -a vX.Y.Z <その版のコミット> -m vX.Y.Z && git push origin vX.Y.Z。"
                            "SECURITY.md「リリースの不変性」。A7-M-1 / IA-20260831-09)")
        if _light:
            warnings.append("release: lightweight タグ(annotated でない。--follow-tags で送信されない): "
                            + ", ".join("v" + v for v in _light))

# (q) GitHub Actions の harden(codex 監査 2026-08-31 H-06 / IA-20260831-08): .github/workflows/*.yml(.yaml、
# *.example 含む)を yaml.safe_load し、(1) 全 `uses:` が 40 桁の commit SHA 固定、(2) workflow または各 job に
# permissions、(3) 各 job に timeout-minutes、(4) push / pull_request で走る workflow に concurrency、
# (5) actions/checkout に persist-credentials: false、を検査する。本体 CI(harness-ci.yml)は ERROR、それ以外
# (プロジェクトが skill-authoring で生成した CI や .example)は WARN(プロジェクトの CI を壊さない)。
_wf_files = sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
                   + glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yaml"))
                   + glob.glob(os.path.join(ROOT, ".github", "workflows", "*.example")))
for _wf in _wf_files:
    _wrel = os.path.relpath(_wf, ROOT).replace(os.sep, "/")
    # 本体の harness-ci.yml だけ ERROR。配布先に残った古い harness-ci.yml(テンプレ複製由来)は WARN
    # (2026-09-21 の 3 実プロジェクト同期で ERROR 6 件が出た。プロジェクトの CI を赤にしない)
    _sink = errors if (IS_BODY and os.path.basename(_wf) == "harness-ci.yml") else warnings
    try:
        _wdoc = yaml.safe_load(open(_wf, encoding="utf-8").read())
    except Exception as e:
        _sink.append(f"{_wrel}: YAML を解析できない: {e}")
        continue
    if not isinstance(_wdoc, dict):
        _sink.append(f"{_wrel}: workflow の形(マッピング)でない")
        continue
    _jobs = _wdoc.get("jobs") or {}
    _on = _wdoc.get("on", _wdoc.get(True))  # PyYAML は `on:` を真偽値 True のキーに解釈する
    if isinstance(_on, dict):
        _on_keys = set(_on.keys())
    elif isinstance(_on, str):
        _on_keys = {_on}
    else:
        _on_keys = set(str(k) for k in (_on or []))
    _problems = []
    if "permissions" not in _wdoc and not all(isinstance(j, dict) and "permissions" in j for j in _jobs.values()):
        _problems.append("permissions が workflow にも全 job にも無い(GITHUB_TOKEN の最小権限: permissions: contents: read)")
    if ("concurrency" not in _wdoc and ({"push", "pull_request"} & _on_keys)
            and not all(isinstance(j, dict) and "concurrency" in j for j in _jobs.values())):
        _problems.append("concurrency が無い(同一 ref の重複 run を cancel-in-progress で取り消す)")
    for _jn, _job in _jobs.items():
        if not isinstance(_job, dict):
            continue
        if "timeout-minutes" not in _job:
            _problems.append(f"job {_jn}: timeout-minutes が無い")
        for _st in _job.get("steps") or []:
            if not isinstance(_st, dict) or not _st.get("uses"):
                continue
            _uses = str(_st["uses"])
            if _uses.startswith("./") or _uses.startswith("docker://"):
                continue
            if not re.search(r"@[0-9a-f]{40}$", _uses):
                _problems.append(f"job {_jn}: uses {_uses} が 40 桁 commit SHA で固定されていない(タグ・ブランチは可変参照)")
            if _uses.startswith("actions/checkout@"):
                _with = _st.get("with") or {}
                if _with.get("persist-credentials", True) is not False:
                    _problems.append(f"job {_jn}: actions/checkout に persist-credentials: false が無い(資格情報を作業ツリーに残さない)")
    for _pb in _problems:
        _sink.append(f"{_wrel}: {_pb}(codex H-06 / IA-20260831-08)")

# (r) GATE_STATUS の完全性(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15): 語彙・遷移・
# 往復上限・復旧手順の正は .github/harness/STATE-MACHINE.md、機械検査の正は tools/gate_status.py(フック warn-stale-gate /
# warn-gate-tamper / inject-progress の sh/ps1 は同じ規則の鏡で selftest 両系が固定)。本体のテンプレ(progress_template.md)と
# 配布先の実物(progress.md。本体には無い)の両方を検査し、5 キーの欠落・重複・未知キー・読めない行・語彙外の値・GATE_COUNTERS
# の不正は ERROR、順序矛盾(後続が着手済みなのに先行が not_started / 後続が done なのに先行が done でない。「状態: 運用中」の
# 注記があれば後者を免除)と往復上限超(tools/usage-config.json の implement_test_loop_max)は WARN。
# (r-2) 語彙の鏡: GATE_STATUS を直接パースするフック・ツールに 5 キーの語が、完全性検査側に 4 値の語が揃っているか(WARN)。
# (r-3) 本体では仕様書 STATE-MACHINE.md の存在と語彙の記載(ERROR)。
_gs = None
try:
    import gate_status as _gs  # noqa: E402
except Exception as e:  # noqa: BLE001
    errors.append(f"tools/gate_status.py を import できない: {e}")
if _gs is not None:
    _gs_max = _gs.loop_max(ROOT)
    for _rel in (_gs.TEMPLATE_REL, _gs.GATE_REL):
        _gp = os.path.join(ROOT, _rel)
        if not os.path.exists(_gp):
            continue
        try:
            _gtext = open(_gp, encoding="utf-8-sig", errors="replace").read()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{_rel}: 読めない({e})")
            continue
        _gerr, _gwarn = _gs.check(_gtext, loop_max=_gs_max)
        _grel = _rel.replace(os.sep, "/")
        for _m in _gerr:
            errors.append(f"{_grel}: GATE_STATUS {_m}(復旧は python tools/gate_status.py recover --from log|git|table|baseline。STATE-MACHINE.md)")
        for _m in _gwarn:
            warnings.append(f"{_grel}: GATE_STATUS {_m}")
        if not _gerr and not _gwarn:
            infos.append(f"{_grel}: GATE_STATUS 完全({_gs.compact(_gs.state(_gtext))} / implement_test_loops {_gs.loops(_gtext)} / 上限 {_gs_max})")
    for _rel in _gs.MIRRORS:
        _mp_path = os.path.join(ROOT, _rel)
        if not os.path.exists(_mp_path):
            continue
        try:
            _mtext = open(_mp_path, encoding="utf-8-sig", errors="replace").read()
        except Exception:  # noqa: BLE001
            continue
        _lack = [p for p in _gs.PHASES if p not in _mtext]
        if _lack:
            warnings.append(f"{_rel.replace(os.sep, '/')}: GATE_STATUS の 5 キーのうち {_lack} の語が無い(鏡の退行。正は tools/gate_status.py PHASES)")
    for _rel in _gs.VALUE_MIRRORS:
        _mp_path = os.path.join(ROOT, _rel)
        if not os.path.exists(_mp_path):
            continue
        try:
            _mtext = open(_mp_path, encoding="utf-8-sig", errors="replace").read()
        except Exception:  # noqa: BLE001
            continue
        _lack = [v for v in _gs.VALUES if v not in _mtext]
        if _lack:
            warnings.append(f"{_rel.replace(os.sep, '/')}: 語彙 {_lack} が完全性検査の鏡に無い(正は tools/gate_status.py VALUES)")
    _spec = os.path.join(ROOT, _gs.SPEC_REL)
    if IS_BODY:
        if not os.path.exists(_spec):
            errors.append(f"{_gs.SPEC_REL.replace(os.sep, '/')} が存在しない(状態機械の仕様の正。A7-M-3 / R-04)")
        else:
            try:
                _stext = open(_spec, encoding="utf-8-sig", errors="replace").read()
                _lack = [v for v in _gs.VALUES + _gs.PHASES + _gs.COUNTER_KEYS if v not in _stext]
                if _lack:
                    errors.append(f"{_gs.SPEC_REL.replace(os.sep, '/')}: 語彙 {_lack} の記載が無い(仕様書と gate_status.py の語彙が食い違う)")
            except Exception:  # noqa: BLE001
                pass

# (y) 外部 Skill / プラグイン / MCP のロック(codex 監査 2026-08-31 H-09 / IA-20260831-11、H-02 / IA-20260831-04):
# (o) の浮動参照検査(w2-supply。設定・文書・ワークフロー全面)は「浮動か」だけを見る。本検査はそれに加えて指示層の
# 外部参照が「ロックに登録され、ロックの版と一致するか」を見る(同じ @latest は (o) と (y) の両方が指摘する)。
# 取り込んだ外部物は .github/harness/external-lock.json に name / source / revision or version / sha256 / license /
# local_patch を持つ。指示層(.github/skills|agents|prompts|instructions・.claude/*・.agents/*・mcp.json・plugin.json)の
# 外部参照(npx / uvx のパッケージ、`owner/repo`、github.com URL)がロックに無い・浮動版(@latest / @main 等)・
# ロックと違う版・版なしは ERROR、local patch のダイジェストずれは WARN(python tools/external_lock.py --refresh)。
# 判定の正は tools/external_lock.py(標準ライブラリのみ。配布先でも動く。本体・配布先とも対象=AGENTS.md の
# 「外部 Skill・プラグイン・MCP は出所確認・版固定・導入前レビュー」はプロジェクトにも適用される)。
try:
    import external_lock as _xlock  # noqa: E402
    _xl_err, _xl_warn, _xl_info = _xlock.check(ROOT)
    # 本体は ERROR。配布先(IS_BODY=False)はプロジェクト固有スキルの外部参照が未登録でも WARN に留める
    # ((o) 浮動参照と同じ分担。2026-09-21 の 3 実プロジェクト同期で team-operations-hub が ERROR になった)
    (errors if IS_BODY else warnings).extend(_xl_err)
    warnings.extend(_xl_warn)
    infos.extend(_xl_info)
except Exception as e:  # noqa: BLE001
    errors.append(f"tools/external_lock.py を実行できない: {e}")

# (w) ハーネス名の表記ゆれ(第3波 A7-M-8。再監査 2026-08-31 §6「ハーネス名が 3 通り」): 正は plugin.json の name
# (Agent Plugins 1.0 の必須キー。.claude-plugin/plugin.json は生成物として一致を (m) が検査)。説明文書
# (README / README.en / .github/harness/*.md|*.html / USAGE / GLOSSARY / COMPARISON / COPILOT-E2E / PLATFORM /
# AGENTS.md / CLAUDE.md / agents / prompts / skills)に旧名・別名が残っていれば ERROR、README.md の見出し 1 行目と
# 各 html の <title> に正の名前が無ければ WARN。履歴文書(DECISIONS.md / CHANGELOG.md / audits/)と GitHub の
# リポジトリ URL・plugin.json の名前空間キーは対象外(旧名は履歴として残る)。本体でだけ走る(配布先の README は
# アプリの README)。
_LEGACY_HARNESS_NAMES = ("app-dev-harness", "CreateAppl")
if IS_BODY:
    _canon = None
    try:
        _canon = json.loads(open(os.path.join(ROOT, "plugin.json"), encoding="utf-8-sig").read()).get("name")
    except Exception:  # noqa: BLE001
        pass
    if not _canon:
        warnings.append("harness-name: plugin.json の name を読めないため表記ゆれ検査をスキップ")
    else:
        _name_docs = [os.path.join(ROOT, n) for n in ("README.md", "README.en.md", "AGENTS.md", "CLAUDE.md")]
        for _pat in ((".github", "harness", "*.md"), (".github", "harness", "*.html"), (".github", "agents", "*.md"),
                     (".github", "prompts", "*.md"), (".github", "skills", "*", "SKILL.md"), (".github", "instructions", "*.md")):
            _name_docs += sorted(glob.glob(os.path.join(ROOT, *_pat)))
        _legacy_hits = 0
        for _doc in _name_docs:
            if not os.path.exists(_doc):
                continue
            _rel = os.path.relpath(_doc, ROOT).replace(os.sep, "/")
            try:
                _lines = open(_doc, encoding="utf-8-sig", errors="replace").read().splitlines()
            except Exception:  # noqa: BLE001
                continue
            for _ln_no, _line in enumerate(_lines, 1):
                for _legacy in _LEGACY_HARNESS_NAMES:
                    if _legacy == _canon or _legacy not in _line:
                        continue
                    _legacy_hits += 1
                    if _legacy_hits <= 12:
                        errors.append(f"harness-name: {_rel}:{_ln_no}: 旧名 {_legacy!r} が残っている(正は plugin.json の name={_canon!r}。"
                                      "履歴文書以外は正の名前に揃える)")
            if _rel == "README.md" and _lines and _canon not in _lines[0]:
                warnings.append(f"harness-name: README.md の 1 行目に正の名前 {_canon!r} が無い")
            if _rel.endswith(".html"):
                _tm = re.search(r"<title>([^<]*)</title>", "\n".join(_lines[:20]))
                if _tm and _canon not in _tm.group(1):
                    warnings.append(f"harness-name: {_rel} の <title> に正の名前 {_canon!r} が無い({_tm.group(1)!r})")
        if _legacy_hits > 12:
            errors.append(f"harness-name: 旧名の残存 計 {_legacy_hits} 件(先頭 12 件のみ表示)")
        infos.append(f"harness-name: 正 {_canon!r}(plugin.json)。走査 {len(_name_docs)} ファイル、旧名の残存 {_legacy_hits} 件")

# (x) パス限定ルールの鮮度(第3波 A6-19b(4) / A7-M-8): .claude/rules/<name>.md は .github/instructions/<name>.instructions.md
# (Copilot の applyTo)から tools/generate-adapters.py 第7節(tools/path_rules.py)が生成する。鏡割れ・未生成・
# 正の無い孤児(手書き rule)は ERROR、paths 無し(applyTo "**"=常駐相当)は WARN(常駐 200 行予算に入るため)。
try:
    import path_rules as _prmod  # noqa: E402
except Exception as e:  # noqa: BLE001
    _prmod = None
    errors.append(f"tools/path_rules.py を import できない: {e}")
if _prmod is not None:
    try:
        _pr_stale, _pr_missing, _pr_orphans, _pr_resident = _prmod.status(ROOT)
        for _rel in _pr_stale:
            errors.append(f"{_rel}: パス限定ルールが正(.github/instructions)とずれている(python tools/generate-adapters.py で再生成)")
        for _rel in _pr_missing:
            errors.append(f"{_rel}: パス限定ルールが未生成(python tools/generate-adapters.py)")
        for _rel in _pr_orphans:
            errors.append(f"{_rel}: 正の無い .claude/rules(手書きの rule は置かない。正は .github/instructions/<name>.instructions.md に移して再生成)")
        for _rel in _pr_resident:
            warnings.append(f"{_rel}: applyTo が全ファイル(paths 無し)のため起動時に常駐する(常駐 200 行予算に入る。パスを絞るか AGENTS.md へ)")
        if not (_pr_stale or _pr_missing or _pr_orphans):
            infos.append(f"path-rules: .claude/rules {len(_prmod.targets(ROOT))} 件が .github/instructions と一致(常駐相当 {len(_pr_resident)})")
    except _prmod.RuleError as e:
        errors.append(f"path-rules: {e}")

# (z) Claude Code サブエージェントの本文展開(A5-6): .claude/agents/<role>.md は .github/agents/<role>.agent.md の本文を
# 展開した生成物(tools/subagent_adapters.py)。正を直して再生成し忘れた鏡割れ・未生成を ERROR にする
# (frontmatter の description 乖離は上のアダプタ検査、model / effort 行は (j) model-policy が見る。本文の鮮度はここだけ)。
try:
    import subagent_adapters as _sa  # noqa: E402
    _sa_stale, _sa_missing = _sa.status(ROOT)
    for _rel in _sa_stale:
        errors.append(f"{_rel}: サブエージェント本文が正(.github/agents)とずれている(python tools/generate-adapters.py で再生成。A5-6)")
    for _rel in _sa_missing:
        errors.append(f"{_rel}: サブエージェントが未生成(python tools/generate-adapters.py)")
    if not (_sa_stale or _sa_missing):
        infos.append(f"subagent-adapters: {len(_sa.SUBAGENT_TOOLS)} 件の .claude/agents が正の本文展開と一致(A5-6)")
except Exception as e:  # noqa: BLE001
    errors.append(f"tools/subagent_adapters.py: {e}")

# (s) ポインタ型アダプタの本文ドリフト(A7-M-6。再監査 2026-08-31 §6 CI・復旧「アダプタ＝ポインタのみ の不変条件の本文ドリフト
# (description の一致しか見ない)を CI が検査できない」): .claude/commands|skills・.agents/workflows を
# tools/generate-adapters.py の第1・3節と同じ生成ロジックで再生成し、現状と比べる(比較モード --check-adapters。書かない。
# .claude/agents は第2節=正の本文展開(tools/subagent_adapters.py)で (z) が見るため対象外)。
# 差分・未生成は鏡割れとして ERROR。生成ロジックは generate-adapters.py の 1 本だけ(この検査は exit code を見る以上の知識を持たない)。
_gen_adapters = os.path.join(ROOT, "tools", "generate-adapters.py")
if os.path.exists(_gen_adapters):
    try:
        _proc = subprocess.run([sys.executable, _gen_adapters, "--check-adapters"], capture_output=True, cwd=ROOT,
                               timeout=180, encoding="utf-8", errors="replace")
        _lines = [ln for ln in (_proc.stdout or "").splitlines() if ln.startswith(("STALE:", "MISSING:"))]
        if _proc.returncode != 0:
            errors.append("generate-adapters.py --check-adapters: ポインタ型アダプタが正とずれている"
                          "(python tools/generate-adapters.py で再生成): " + " / ".join(_lines[:6])
                          + (" …" if len(_lines) > 6 else "") + ("" if _lines else (_proc.stderr or "").strip()[-300:]))
        else:
            infos.append("ポインタ型アダプタ(.claude/commands|skills・.agents/workflows): 生成結果と一致(--check-adapters。.claude/agents の本文展開は (z))")
    except Exception as e:  # noqa: BLE001
        errors.append(f"generate-adapters.py --check-adapters: 実行失敗: {e}")
else:
    warnings.append("tools/generate-adapters.py が存在しない(ポインタ型アダプタの本文ドリフト検査ができない)")

# (t) ビルトイン依存の台帳(A5-8。D070「検出は委譲・統合を所有」): .github/harness/builtin-dependencies.json の構造と、
# used_by の各ファイルが実在し id を言及しているか(委譲先の増減に台帳が追従しているか)。存在確認そのもの(claude を起動)は
# tools/host-canary.py(CI の claude 導入後ステップ。WARN=赤にしない)。
_bd_path = os.path.join(ROOT, ".github", "harness", "builtin-dependencies.json")
if os.path.exists(_bd_path):
    try:
        _bd = json.load(open(_bd_path, encoding="utf-8-sig"))
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("host_canary", os.path.join(ROOT, "tools", "host-canary.py"))
            _hc = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_hc)
            for _p in _hc.validate_manifest(_bd):
                errors.append(f".github/harness/builtin-dependencies.json: {_p}")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"tools/host-canary.py を読めないため builtin-dependencies.json の構造検査をスキップ: {e}")
        _n_dep = 0
        for _dep in (_bd.get("dependencies") or []):
            _n_dep += 1
            _needle = str(_dep.get("id") or "")
            # used_by の実在・言及は本体でだけ見る(配布先の README はアプリの README、evaluation/ と本体 CI は非配布。
            # 2026-09-21 の 3 実プロジェクト同期で WARN 4 件の誤検出)
            for _rel in (_dep.get("used_by") or []) if IS_BODY else []:
                _p = os.path.join(ROOT, _rel.replace("/", os.sep))
                if not os.path.exists(_p):
                    warnings.append(f"builtin-dependencies.json: {_dep.get('id')} の used_by {_rel} が存在しない(台帳の更新漏れ)")
                    continue
                try:
                    if _needle and _needle not in open(_p, encoding="utf-8-sig", errors="replace").read():
                        warnings.append(f"builtin-dependencies.json: {_rel} に {_needle!r} の言及が無い"
                                        "(委譲先を外したなら台帳からも外す。A5-8)")
                except Exception:  # noqa: BLE001
                    pass
        infos.append(f"builtin-dependencies.json: 依存 {_n_dep} 件(存在確認は python tools/host-canary.py。as_of {_bd.get('as_of')})")
    except Exception as e:  # noqa: BLE001
        errors.append(f".github/harness/builtin-dependencies.json: JSON として読めない({e})")
elif IS_BODY:
    warnings.append(".github/harness/builtin-dependencies.json が無い(D070 で委譲したビルトインの台帳。A5-8)")

# (u) リリース tag の整合(A7-M-1。再監査 2026-08-31 §6「CHANGELOG 4 版に対応する git tag が 1 つも無い」/ 2026-09-09 OP-5):
# CHANGELOG.md の `## [x.y.z] - 日付` と annotated tag の対応を tools/release-tag.py --check で見て INFO に転記する。
# INFO に留める理由: tag 無し自体の WARN は (p)(リリースの不変性。D087)が出す。本検査は「どのコミットに付けるか」の解決結果を
# 添える補助。CI の actions/checkout は fetch-tags: true でも depth 1 で履歴が無く、版の導入コミットを解決できない
# (unresolved)ため、解決は完全 clone の本体で行う(配布先に CHANGELOG は無い=対象外)。
_rt = os.path.join(ROOT, "tools", "release-tag.py")
if IS_BODY and os.path.exists(_rt) and os.path.exists(os.path.join(ROOT, "CHANGELOG.md")):
    try:
        _proc = subprocess.run([sys.executable, _rt, "--check"], capture_output=True, cwd=ROOT, timeout=120,
                               encoding="utf-8", errors="replace")
        _sum = next((ln for ln in reversed((_proc.stdout or "").splitlines()) if ln.startswith("release-tag --check:")), None)
        _missing = [ln.split("|")[1].strip() for ln in (_proc.stdout or "").splitlines()
                    if ln.startswith("| ") and ln.rstrip().endswith("| missing |")]
        infos.append((_sum or "release-tag --check: 集計行なし") + ("(tag が無い版: " + ", ".join(_missing)
                     + "。python tools/release-tag.py がコマンド列を出す。人が実行)" if _missing else ""))
    except Exception as e:  # noqa: BLE001
        warnings.append(f"tools/release-tag.py --check: 実行失敗: {e}")

# (v) CI の claude 版固定は platform-requirements.json の verified_on_ci の鏡(A3-6 ホスト機能吸収カナリア):
# harness-ci.yml の `@anthropic-ai/claude-code@x.y.z` が verified_on_ci(CI 緑を確認した版)と違えば WARN
# (pin を上げたら CI 緑を確認してから JSON を更新する=Day-0 儀式。PLATFORM.md)。実機版との比較は host-canary。
if _req is not None and IS_BODY:
    _ci_yml = os.path.join(ROOT, ".github", "workflows", "harness-ci.yml")
    _v_ci = _req["claude_code"].get("verified_on_ci")
    if os.path.exists(_ci_yml):
        _pm_ci = re.search(r"@anthropic-ai/claude-code@(\d+\.\d+\.\d+)", open(_ci_yml, encoding="utf-8-sig").read())
        if _pm_ci and _v_ci and _pm_ci.group(1) != str(_v_ci):
            warnings.append(f"platform-requirements: harness-ci.yml の claude 版固定 {_pm_ci.group(1)} が verified_on_ci {_v_ci} と違う"
                            "(CI 緑を確認したら verified_on_ci を更新し、Day-0 再ベンチ儀式(PLATFORM.md)を実施する。A3-6)")
        elif _pm_ci and _v_ci:
            infos.append(f"platform-requirements: harness-ci.yml の claude 版固定 {_pm_ci.group(1)} = verified_on_ci")
        elif not _v_ci:
            infos.append("platform-requirements: verified_on_ci が未設定(CI 緑を確認した導入版を記録すると host-canary が版差を出せる)")

print("=== ERRORS ===")
for e in errors:
    print("ERROR:", e)
print(f"total errors: {len(errors)}")
print("=== WARNINGS ===")
for w in warnings:
    print("WARN:", w)
print(f"total warnings: {len(warnings)}")
if infos:
    print("=== INFO ===")
    for i in infos:
        print("INFO:", i)
sys.exit(1 if errors else 0)
