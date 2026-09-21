#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""役割別モデル/effort 方針(.github/harness/model-policy.yml)の読込・生成・検査。

監査 2026-09-09 §4.5(MP-1〜7 / CC-2 / MS 群)の実装。正は model-policy.yml の 1 ファイルで、
次の全面が生成物(本モジュールが書く。手で編集しない):

- .claude/agents/<role>.md の frontmatter(model / effort / maxTurns / background / memory。
  generate-adapters.py 第2節が書いた name/description/tools はそのまま保つ)
- .github/agents/<role>.agent.md の `model:` 行のみ(`# generated-from: model-policy.yml` マーカー。
  null の役割は行を出さず、`model: auto` は除去する)
- .claude/commands/<cmd>.md の `effort:`(phase_overrides。既定は空 = 無変更)
- .github/copilot/settings.json(Copilot CLI。`_generated_from` キー付き)
- .github/hooks/scripts/guard-subagent-model.{sh,ps1} の埋め込み役割表(JSON)
- PLATFORM.md / README.md / agents.html の <!-- BEGIN GENERATED: model-policy-* --> ブロック

.claude/settings.json の permissions.deny(`Agent(model:…)`)だけは自動では書かず、
--print-deny で算出結果を出し、--apply-deny で人間が明示的に反映する(保守モードの退避
.claude/settings.json.locked が存在する間は拒否する = harness-maintenance --off の復元で消えるため)。

使い方(リポジトリルートで。generate-adapters.py の引数付き起動は全てここへ委譲される):
    python tools/generate-adapters.py                 # 通常モード: 第1〜4節を全部再生成(第4節が本モジュール)
    python tools/generate-adapters.py --check         # 生成物が方針とずれていないか(0=一致 / 1=差分 / 2=エラー)
    python tools/generate-adapters.py --print-deny    # permissions.deny に入れる Agent(model:…) 規則を出力
    python tools/generate-adapters.py --apply-deny    # 上記を .claude/settings.json に追記(.locked があれば拒否)
    python tools/model_policy.py --print-prices       # 単価表(prefix 族フォールバック付き)を JSON で出力
    python tools/model_policy.py --print-role-table   # フックに埋め込む役割表(JSON)
    python tools/model_policy.py --selftest           # 自己テスト

依存: Python 3.x + PyYAML(方針の読込のみ。生成物の読み手(フック・effort-report)は JSON/文字列を読む)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import sys
import tempfile

try:
    import yaml
except ImportError:  # validate 側が案内する(requirements-dev.txt)
    yaml = None

POLICY_REL = ".github/harness/model-policy.yml"
SETTINGS_REL = ".claude/settings.json"
LOCKED_REL = ".claude/settings.json.locked"
COPILOT_SETTINGS_REL = ".github/copilot/settings.json"
HOOK_SH_REL = ".github/hooks/scripts/guard-subagent-model.sh"
HOOK_PS1_REL = ".github/hooks/scripts/guard-subagent-model.ps1"
PLATFORM_REL = ".github/harness/PLATFORM.md"
README_REL = "README.md"
AGENTS_HTML_REL = ".github/harness/agents.html"

CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLI_EFFORTS = ("low", "medium", "high", "xhigh")
CLI_CONTEXT_TIERS = ("default", "long_context", "inherit")
MEMORY_VALUES = ("user", "project", "local")
CLAUDE_KEYS = {"model", "effort", "maxTurns", "background", "memory", "allowed_models"}
VSCODE_KEYS = {"model"}
CLI_KEYS = {"model", "effortLevel", "contextTier", "model_policy"}
CLAUDE_FM_KEYS = ("model", "effort", "maxTurns", "background", "memory")
ROLE_KINDS = ("subagent", "phase", "maintainer")

GH_MARK = "# generated-from: model-policy.yml"
CLAUDE_BODY_NOTE = ("frontmatter の model / effort / maxTurns / background / memory は "
                    "`.github/harness/model-policy.yml` から生成される(手で書かない。"
                    "`python tools/generate-adapters.py` で再生成)。")
HOOK_BEGIN = "# BEGIN GENERATED: model-policy-role-table"
HOOK_END = "# END GENERATED: model-policy-role-table"


class PolicyError(Exception):
    """方針ファイルの構造エラー(生成を止める)。"""


# ---------------------------------------------------------------- 読込・参照

def load_policy(root):
    path = os.path.join(root, POLICY_REL)
    if yaml is None:
        raise PolicyError("PyYAML が無い(pip install -r requirements-dev.txt)")
    if not os.path.exists(path):
        raise PolicyError(f"{POLICY_REL} が存在しない")
    try:
        data = yaml.safe_load(open(path, encoding="utf-8-sig").read())
    except Exception as e:  # noqa: BLE001
        raise PolicyError(f"{POLICY_REL}: YAML を読めない: {e}")
    if not isinstance(data, dict):
        raise PolicyError(f"{POLICY_REL}: トップレベルがマップではない")
    if data.get("version") != 1:
        raise PolicyError(f"{POLICY_REL}: version は 1 のみ対応(実際: {data.get('version')!r})")
    for key in ("retrieved", "models", "roles", "enforcement"):
        if key not in data:
            raise PolicyError(f"{POLICY_REL}: 必須キー {key} が無い")
    if not isinstance(data["models"], dict) or not isinstance(data["roles"], dict):
        raise PolicyError(f"{POLICY_REL}: models / roles はマップ")
    return data


def models(p):
    return p.get("models") or {}


def legacy_models(p):
    return p.get("legacy_models") or {}


def roles(p):
    return p.get("roles") or {}


def enforcement_cc(p):
    return (p.get("enforcement") or {}).get("claude_code") or {}


def claude_alias(p, key):
    return (models(p)[key].get("claude_code") or {}).get("alias")


def claude_id(p, key):
    return (models(p)[key].get("claude_code") or {}).get("id")


def copilot_display(p, key):
    return models(p)[key].get("copilot_display")


def copilot_cli_id(p, key):
    return models(p)[key].get("copilot_cli_id")


def deny_keys(p):
    keys = enforcement_cc(p).get("deny_agent_model") or []
    for k in keys:
        if k not in models(p):
            raise PolicyError(f"enforcement.claude_code.deny_agent_model: 未知のモデルキー {k!r}")
    return list(keys)


def normalize_claude_model(p, value):
    """roles.*.claude_code.model の値(inherit / モデルキー / alias / フル ID)を frontmatter 用の文字列にする。
    モデルキーはフル ID に展開する(availableModels の literal 一致に強いため。設計: フル ID 推奨)。"""
    if value in (None, "", "inherit"):
        return "inherit"
    if value in models(p):
        return claude_id(p, value)
    for k in models(p):
        if value in (claude_alias(p, k), claude_id(p, k)):
            return value
    raise PolicyError(f"claude_code.model の値 {value!r} は inherit / モデルキー / alias / フル ID のいずれでもない")


def role_allowed_keys(p, role):
    cc = roles(p)[role].get("claude_code") or {}
    allowed = cc.get("allowed_models")
    if allowed is None:
        denied = set(deny_keys(p))
        return [k for k in models(p) if k not in denied]
    for k in allowed:
        if k not in models(p):
            raise PolicyError(f"roles.{role}.claude_code.allowed_models: 未知のモデルキー {k!r}")
    return list(allowed)


def deny_rules(p):
    """permissions.deny に入れる規則。呼出時の model パラメータの literal 一致のみに効く(公式)。"""
    rules = []
    wildcard = bool(enforcement_cc(p).get("deny_wildcard"))
    for k in deny_keys(p):
        alias, full = claude_alias(p, k), claude_id(p, k)
        if alias:
            rules.append(f"Agent(model:{alias})")
        if full:
            rules.append(f"Agent(model:{full})")
            if wildcard:
                rules.append(f"Agent(model:{full}*)")
    return rules


def role_table(p):
    """guard-subagent-model.{sh,ps1} に埋め込む役割表。kind: subagent の役割のみ(Agent ツールで
    呼ばれるのはこれだけ。表に無い subagent_type はフックが素通し=fail-open)。"""
    deny_exact, deny_prefix = [], []
    for k in deny_keys(p):
        if claude_alias(p, k):
            deny_exact.append(claude_alias(p, k))
        if claude_id(p, k):
            deny_prefix.append(claude_id(p, k))
    legacy_prefixes = sorted({pf for m in legacy_models(p).values() for pf in (m.get("price_prefixes") or [])})
    table_roles = {}
    for role, r in roles(p).items():
        if r.get("kind") != "subagent":
            continue
        cc = r.get("claude_code") or {}
        allowed = role_allowed_keys(p, role)
        exact = ["inherit"] + [claude_alias(p, k) for k in allowed if claude_alias(p, k)]
        prefix = [claude_id(p, k) for k in allowed if claude_id(p, k)] + legacy_prefixes
        table_roles[role] = {
            "default": normalize_claude_model(p, cc.get("model")),
            "allowed_exact": exact,
            "allowed_prefix": prefix,
        }
    return {
        "generated_from": POLICY_REL,
        "retrieved": str(p.get("retrieved")),
        "deny_exact": deny_exact,
        "deny_prefix": deny_prefix,
        "roles": table_roles,
    }


def role_table_json(p):
    text = json.dumps(role_table(p), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if "'" in text:
        raise PolicyError("役割表 JSON に単引用符が含まれる(sh/ps1 の単引用符リテラルに埋め込めない)")
    return text


def price_table(p):
    """単価表(prefix 族フォールバック)。最長一致で解決するよう prefix 長の降順に並べる。
    tools/effort-report.py 等の stdlib-only な読み手は --print-prices の JSON を読む。"""
    rows = []
    for key, m in list(models(p).items()) + list(legacy_models(p).items()):
        price = m.get("price_usd_per_mtok") or {}
        for pf in m.get("price_prefixes") or []:
            rows.append({"prefix": pf, "key": key,
                         "input": price.get("input"), "output": price.get("output"),
                         "cache_read": price.get("cache_read"),
                         "cache_write_5m": price.get("cache_write_5m"),
                         "cache_write_1h": price.get("cache_write_1h"),
                         "legacy": key in legacy_models(p)})
    rows.sort(key=lambda r: (-len(r["prefix"]), r["prefix"]))
    return rows


def resolve_price(p, model_id):
    for row in price_table(p):
        if model_id.startswith(row["prefix"]):
            return row
    return None


# ---------------------------------------------------------------- frontmatter 書換

_FM_RE = re.compile(r"^(---\r?\n)(.*?)(\r?\n---\r?\n)(.*)$", re.DOTALL)


def _split_frontmatter(text, rel):
    m = _FM_RE.match(text)
    if not m:
        raise PolicyError(f"{rel}: frontmatter を読めない(先頭に --- で囲まれた YAML が必要)")
    return m.group(1), m.group(2), m.group(3), m.group(4)


def _eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def _yaml_bool(v):
    return "true" if v else "false"


def render_claude_agent(text, p, role, rel=".claude/agents/<role>.md"):
    """generate-adapters.py 第2節が書いた .claude/agents/<role>.md に方針由来の行を差し込む(冪等)。"""
    head, fm, close, body = _split_frontmatter(text, rel)
    eol = _eol_of(text)
    lines = [ln for ln in fm.split(eol)
             if not re.match(r"^(model|effort|maxTurns|background|memory)\s*:", ln)]
    cc = roles(p)[role].get("claude_code") or {}
    new = [f"model: {normalize_claude_model(p, cc.get('model'))}"]
    effort = cc.get("effort")
    if enforcement_cc(p).get("emit_effort_frontmatter", True) and effort not in (None, "inherit"):
        new.append(f"effort: {effort}")
    if cc.get("maxTurns") is not None:
        new.append(f"maxTurns: {int(cc['maxTurns'])}")
    if cc.get("background") is not None:
        new.append(f"background: {_yaml_bool(cc['background'])}")
    if cc.get("memory") is not None:
        new.append(f"memory: {cc['memory']}")
    idx = next((i for i, ln in enumerate(lines) if ln.startswith("tools:")), len(lines) - 1)
    lines[idx + 1:idx + 1] = new
    body_lines = [ln for ln in body.split(eol) if ln.strip() != CLAUDE_BODY_NOTE]
    body2 = eol.join(body_lines).rstrip("\r\n") + eol + eol + CLAUDE_BODY_NOTE + eol
    return head + eol.join(lines) + close + body2


def render_github_agent(text, p, role, rel=".github/agents/<role>.agent.md"):
    """.github/agents/<role>.agent.md の `model:` 行だけを方針から書き換える(冪等)。
    null の役割は行を出さずマーカーだけ残す。既存の `model: auto` は除去する。"""
    head, fm, close, body = _split_frontmatter(text, rel)
    eol = _eol_of(text)
    out, skipping = [], False
    for ln in fm.split(eol):
        if re.match(r"^model\s*:", ln):
            skipping = True
            continue
        if skipping and re.match(r"^\s+-\s", ln):
            continue
        skipping = False
        if ln.startswith(GH_MARK):
            continue
        out.append(ln)
    vs = roles(p)[role].get("copilot_vscode") or {}
    keys = vs.get("model")
    gen = []
    if keys:
        if isinstance(keys, str):
            keys = [keys]
        names = []
        for k in keys:
            if k not in models(p) or not copilot_display(p, k):
                raise PolicyError(f"roles.{role}.copilot_vscode.model: 未知のモデルキー {k!r}(copilot_display が要る)")
            names.append(copilot_display(p, k))
        gen.append(f"{GH_MARK} (表示名配列。先頭から利用可能なものが使われる。手で書かない)")
        gen.append("model: [" + ", ".join(f"'{n}'" for n in names) + "]")
    else:
        gen.append(f"{GH_MARK} (model: 未指定 = ピッカー継承。auto は frontmatter 仕様外のため書かない)")
    # 差し込み位置: agents: 行(とその継続行)の直後 → tools: → description: の順で探す
    pos = None
    for anchor in ("agents:", "tools:", "description:"):
        for i, ln in enumerate(out):
            if ln.startswith(anchor):
                pos = i + 1
                while pos < len(out) and re.match(r"^\s+-\s", out[pos]):
                    pos += 1
                break
        if pos is not None:
            break
    if pos is None:
        pos = len(out)
    out[pos:pos] = gen
    return head + eol.join(out) + close + body


def render_command(text, effort, rel=".claude/commands/<cmd>.md"):
    """phase_overrides: .claude/commands/<cmd>.md の frontmatter に effort を書く(None なら除去)。"""
    head, fm, close, body = _split_frontmatter(text, rel)
    eol = _eol_of(text)
    lines = [ln for ln in fm.split(eol) if not re.match(r"^effort\s*:", ln)]
    if effort:
        if effort not in CLAUDE_EFFORTS:
            raise PolicyError(f"phase_overrides: effort {effort!r} は {CLAUDE_EFFORTS} のいずれか")
        lines.append(f"effort: {effort}")
    return head + eol.join(lines) + close + body


def render_copilot_settings(p):
    """Copilot CLI の .github/copilot/settings.json。キー名は docs.github.com cli-config-dir-reference
    (User settings 表: subagents.agents.<name> = {model, effortLevel, contextTier})に準拠。"""
    out = {
        "_generated_from": POLICY_REL,
        "_generated_note": ("python tools/generate-adapters.py が生成(手で編集しない)。session model は "
                            "policy.session.copilot_cli_pin が null の間は出力しない。Repository settings は "
                            "trusted なディレクトリでのみ効く。ローカル上書きは settings.local.json(gitignore)"),
    }
    pin = (p.get("session") or {}).get("copilot_cli_pin")
    if pin:
        out["model"] = copilot_cli_id(p, pin) if pin in models(p) else str(pin)
    agents = {}
    for role, r in roles(p).items():
        if r.get("kind") != "subagent":
            continue
        cli = r.get("copilot_cli") or {}
        entry = {}
        if cli.get("model"):
            mk = cli["model"]
            entry["model"] = copilot_cli_id(p, mk) if mk in models(p) else str(mk)
        if cli.get("effortLevel"):
            entry["effortLevel"] = str(cli["effortLevel"])
        if cli.get("contextTier"):
            entry["contextTier"] = str(cli["contextTier"])
        if entry:
            agents[role] = entry
    if agents:
        out["subagents"] = {"agents": agents}
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def render_hook_table_line(p, kind):
    js = role_table_json(p)
    if kind == "sh":
        return f"role_table='{js}'"
    return f"$roleTableJson = '{js}'"


# ---------------------------------------------------------------- 文書ブロック

def _cc_cell(p, r):
    cc = r.get("claude_code") or {}
    return f"{normalize_claude_model(p, cc.get('model'))} / {cc.get('effort') or 'inherit'}"


def _vs_cell(p, r):
    keys = (r.get("copilot_vscode") or {}).get("model")
    if not keys:
        return "ピッカー継承"
    if isinstance(keys, str):
        keys = [keys]
    return " → ".join(copilot_display(p, k) for k in keys)


def _cli_cell(p, r):
    cli = r.get("copilot_cli") or {}
    m = cli.get("model")
    mtxt = (copilot_cli_id(p, m) if m in models(p) else m) if m else "継承"
    return f"{mtxt} / {cli.get('effortLevel') or '既定(medium)'}"


def _target_cell(r):
    kind = r.get("kind")
    if kind == "subagent":
        return "`.claude/agents/`・`.github/copilot/settings.json`"
    return "なし（commands 経由。`phase_overrides` のみ）"


def _extras(p, r):
    cc = r.get("claude_code") or {}
    bits = []
    if cc.get("background") is not None:
        bits.append(f"background={_yaml_bool(cc['background'])}")
    if cc.get("maxTurns") is not None:
        bits.append(f"maxTurns={cc['maxTurns']}")
    if cc.get("memory"):
        bits.append(f"memory={cc['memory']}")
    return "、".join(bits)


def _mv(p, host, key):
    return ((p.get("min_version") or {}).get(host) or {}).get("docs", {}).get(key, "?")


def render_platform_block(p):
    retrieved = p.get("retrieved")
    L = [f"（`python tools/generate-adapters.py` が `{POLICY_REL}`（取得 {retrieved}）から生成。手で編集しない。"
         "検査: `python tools/generate-adapters.py --check` / `python tools/validate-harness.py`）", ""]
    L.append(f"**上書き（方針より強い層）**: `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` の環境では役割別 `model` は"
             f"全て無効（fork と `model: inherit` のスキルを除く。v{_mv(p, 'claude_code', 'subagent_model_force')}+）。"
             "`CLAUDE_CODE_EFFORT_LEVEL` は役割別 `effort` と工程別オーバーレイ（`phase_overrides`）を全て上書きする。")
    L.append("")
    L.append("### 役割×モデル×effort（既定。A/B 前は挙動不変・`ab_evidence` が入るまで固定）")
    L.append("")
    L.append("| 役割 | 種別 | Claude Code model / effort | 生成先 | Copilot VS Code model | Copilot CLI model / effortLevel | 下限固定 | 付帯 | A/B 後の候補 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for role, r in roles(p).items():
        L.append(f"| `{role}` | {r.get('kind')} | {_cc_cell(p, r)} | {_target_cell(r)} | {_vs_cell(p, r)} | "
                 f"{_cli_cell(p, r)} | {'never_low' if r.get('never_low') else '—'} | {_extras(p, r) or '—'} | "
                 f"{r.get('ab_candidate') or '—'} |")
    L.append("")
    denied = ", ".join(f"`{copilot_display(p, k)}`" for k in deny_keys(p)) or "なし"
    L.append(f"候補外（`enforcement.deny_agent_model`）: {denied}。`永久に inherit` ではなく「`ab_evidence` が入るまで既定」"
             "であることに注意（A/B の手順は監査 2026-09-09 §4.3・§6）。")
    L.append("")
    L.append("### 方針と上書きの優先順位表（Claude Code / Copilot VS Code / Copilot CLI）")
    L.append("")
    L.append("| 順位 | Claude Code（サブエージェント model。上が強い） | Copilot VS Code（runSubagent。上が強い） | Copilot CLI（設定は後勝ち = 下が強い） |")
    L.append("|---|---|---|---|")
    L.append(f"| 1 | `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`（全定義無視。v{_mv(p, 'claude_code', 'subagent_model_force')}+） | enterprise 管理既定（未指定時の継承先） | 1. 組込既定 |")
    L.append("| 2 | 呼出時 `model` パラメータ（`guard-subagent-model` が方針と照合） | 呼出時 model 引数 | 2. MDM 管理設定 |")
    L.append(f"| 3 | frontmatter `model`（**方針**・生成物） | `.agent.md` の `model`（**方針**・生成物。配列は CLI {_mv(p, 'copilot_cli', 'agent_model_array')}+） | 3. user settings（`~/.copilot/settings.json`） |")
    L.append(f"| 4 | `CLAUDE_CODE_SUBAGENT_MODEL`（v{_mv(p, 'claude_code', 'resolution_order')}+ は frontmatter より弱い） | ピッカーのモデル（親） | 4. **repo `.github/copilot/settings.json`（方針・生成物。trusted なディレクトリのみ）** |")
    L.append("| 5 | メイン会話のモデル（ユーザースコープ `model`・`/model`） | コストティアガード（親超えは起動拒否） | 5. `.github/copilot/settings.local.json`（gitignore） |")
    L.append("| 6 | — | — | 6. 環境変数 → 7. CLI フラグ |")
    L.append("| effort | `CLAUDE_CODE_EFFORT_LEVEL` > frontmatter `effort`（方針） > `/effort`・settings `effortLevel` > 既定 | **指定不可**（劣化モード） | `subagents.agents.<name>.effortLevel`（low / medium / high / xhigh。方針・生成物） |")
    L.append("")
    L.append("### 劣化モード（ホスト別に効かないもの）")
    L.append("")
    L.append("| ホスト | 効かないもの | 代替・記録 |")
    L.append("|---|---|---|")
    L.append("| Copilot VS Code | 役割別 effort（frontmatter に相当フィールド無し）。`model: auto` は frontmatter 仕様外 | コストを抑えるならピッカーで Auto を選ぶ。実行モデルは応答フッタのホバー（手動転記） |")
    L.append("| Copilot CLI | 親≧子ティアを超える子は**無言で親モデルへ降格**（#2758）。`model-policy: required` は 1.0.83+ 限定 | `--usage-output-file` の per-agent usage を証拠に添付 |")
    L.append("| Copilot cloud agent | custom agent の `model` は**未保証**（生成物から外し SOP のみ） | タスク開始時のピッカー（Auto / Opus 5 / Haiku 4.5）で選ぶ |")
    L.append(f"| Claude Code | availableModels 外の値は無言で代替（family alias は許可される最新版、他は inherited）。開発機 2.1.201 では FORCE（{_mv(p, 'claude_code', 'subagent_model_force')}）・解決順（{_mv(p, 'claude_code', 'resolution_order')}）・Fable 5.1 が未対応 | PostToolUse(Agent).`resolvedModel` / `-p` の `modelUsage` / `/tasks`（{_mv(p, 'claude_code', 'tasks_model_display')}+）を証拠に |")
    L.append("")
    L.append("### 強制の三層（Claude Code。指示より機械）")
    L.append("")
    rules = ", ".join(f"`{r}`" for r in deny_rules(p)) or "（なし）"
    L.append(f"1. `permissions.deny`（{rules}）— **呼出時の `model` パラメータの literal 一致のみ**に効く"
             "（公式: 省略されたパラメータは一致しない・alias はフル ID に一致しない）。frontmatter / inherit 由来には効かない。")
    L.append("2. PreToolUse（matcher `Agent|Task`）`guard-subagent-model.{sh,ps1}` — 役割表（生成物）と照合し、方針外の"
             "明示モデルは deny。省略時は既定が `inherit` の役割は素通し（既定が具体モデルの役割のみ `updatedInput` で"
             "**tool_input 全体を複製して model だけ差し替え**）。非 Agent ツール・`subagent_type` 欠落・表が読めない・壊れた JSON は無出力 exit 0（fail-open）。")
    L.append(f"3. PostToolUse(Agent) / SubagentStop の記録（`{enforcement_cc(p).get('post_tool_use_record')}`。"
             "`logs/usage/<session_id>.subagents.jsonl`）— `resolvedModel` / `modelsUsed` / `status`。**background 役割は "
             "`async_launched` しか取れない**（完了時の `modelsUsed` は前面実行のみ）ため、判定役は `background: false`。"
             "Stop / SubagentStop での block・再実行要求は使わない（fail-open 規律）。done 遷移時の `model_mismatch` 照合は次波（warn-gate-tamper の拡張）。")
    L.append("")
    L.append("### モデル関連機能の最低版（docs 記載版 / 実機検証版）")
    L.append("")
    L.append("| ホスト | 機能 | docs 記載版 | 実機検証版 |")
    L.append("|---|---|---|---|")
    mv = p.get("min_version") or {}
    for host, label in (("claude_code", "Claude Code"), ("copilot_cli", "Copilot CLI")):
        h = mv.get(host) or {}
        verified = h.get("verified") or {}
        for key, ver in (h.get("docs") or {}).items():
            L.append(f"| {label} | `{key}` | {ver} | {verified.get(key) or '未検証'} |")
        ef = h.get("effort_frontmatter")
        if ef:
            L.append(f"| {label} | `effort_frontmatter` | docs: {ef.get('docs') or '版条件なし'} / CHANGELOG: {ef.get('changelog') or '?'}"
                     f"（見解が割れている） | {verified.get('effort_frontmatter') or '未検証'}。確認手順: {ef.get('verify_how') or '—'} |")
    L.append("")
    L.append("### 保護・配布")
    L.append("")
    L.append(f"- `{POLICY_REL}` と `.github/copilot/**` は guard sh/ps1・deny・CROSS_ITEMS・CODEOWNERS・SYNC_GLOBS の保護対象。"
             f"`{POLICY_REL}` は sync の REVIEW_FILES（プロジェクト固有の `plan_fallbacks` / `session.copilot_cli_pin` を守る）。")
    L.append("- `.claude/settings.json` は REVIEW_FILES のため deny・フック配線の差分は配布先で衝突として提示される（`/91` で手動マージ。harness-sync スキル参照）。")
    L.append("- 生成は通常モードで人間が実行する。保守モード（`.claude/settings.json.locked` が存在）中は `--apply-deny` を拒否する"
             "（`harness-maintenance --off` の復元で生成結果が消えるため）。")
    L.append("")
    L.append(f"### 単価表（USD / 1M tokens。取得 {retrieved}。`python tools/model_policy.py --print-prices` が JSON 双子）")
    L.append("")
    L.append("| キー | prefix（最長一致） | input | output | cache read | cache write 5m | cache write 1h | 備考 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for row in price_table(p):
        src = models(p).get(row["key"]) or legacy_models(p).get(row["key"]) or {}
        note = src.get("note") or ""
        if src.get("deprecated_on"):
            note = (note + " " if note else "") + f"廃止 {src['deprecated_on']}"
        L.append(f"| `{row['key']}`{'（legacy）' if row['legacy'] else ''} | `{row['prefix']}` | {row['input']} | {row['output']} | "
                 f"{row['cache_read']} | {row['cache_write_5m']} | {row['cache_write_1h']} | {note} |")
    return "\n".join(L)


def render_readme_block(p):
    L = [f"| 役割 | Claude Code model / effort | Copilot VS Code | Copilot CLI effortLevel | 下限固定 |",
         "|---|---|---|---|---|"]
    for role, r in roles(p).items():
        cli = r.get("copilot_cli") or {}
        L.append(f"| `{role}` | {_cc_cell(p, r)} | {_vs_cell(p, r)} | {cli.get('effortLevel') or '既定'} | "
                 f"{'never_low' if r.get('never_low') else '—'} |")
    L.append("")
    L.append(f"（`{POLICY_REL}`（取得 {p.get('retrieved')}）から生成。`inherit` = メイン会話のモデル/effort を継承。"
             "上書きの優先順位・劣化モード・単価表は `.github/harness/PLATFORM.md`「モデル/effort の方針」）")
    return "\n".join(L)


def render_agents_html_block(p):
    L = ['  <div class="scroll">', "  <table>",
         "    <tr><th>役割</th><th>Claude Code model / effort</th><th>Copilot VS Code</th><th>Copilot CLI effortLevel</th><th>下限固定</th><th>A/B 後の候補</th></tr>"]
    for role, r in roles(p).items():
        cli = r.get("copilot_cli") or {}
        pill = ' <span class="pill sub">never_low</span>' if r.get("never_low") else ""
        L.append(f"    <tr><td><b>{role}</b></td><td><code>{_cc_cell(p, r)}</code></td><td>{_vs_cell(p, r)}</td>"
                 f"<td>{cli.get('effortLevel') or '既定'}</td><td>{'あり' + pill if r.get('never_low') else '—'}</td>"
                 f"<td>{r.get('ab_candidate') or '—'}</td></tr>")
    L.append("  </table>")
    L.append("  </div>")
    L.append(f'  <p class="sec-desc">正は <code>{POLICY_REL}</code>（取得 {p.get("retrieved")}）。'
             "<code>inherit</code> はメイン会話のモデル/effort を継承。<code>model: auto</code> は Copilot の frontmatter 仕様に無いため使わない"
             "（コストを抑えるならピッカーで Auto を選ぶ）。上書きの優先順位・劣化モード・単価表は PLATFORM.md「モデル/effort の方針」。</p>")
    return "\n".join(L)


# ---------------------------------------------------------------- マーカー差し込み

def splice_block(raw, name, generated_lf, rel):
    """<!-- BEGIN GENERATED: name --> / <!-- END GENERATED: name --> の間を置換する(gen-docs.py と同規約)。"""
    begin = f"<!-- BEGIN GENERATED: {name} -->"
    end = f"<!-- END GENERATED: {name} -->"
    if raw.count(begin) != 1 or raw.count(end) != 1:
        raise PolicyError(f"{rel}: マーカー {begin} / {end} がちょうど1組見つからない"
                          f"(BEGIN={raw.count(begin)}, END={raw.count(end)})")
    eol = _eol_of(raw)
    m = re.search(re.escape(begin) + r"\r?\n(.*?)[ \t]*" + re.escape(end), raw, re.DOTALL)
    if not m:
        raise PolicyError(f"{rel}: マーカーの並びが不正(BEGIN の後に END が無い)")
    block = generated_lf.replace("\n", eol) + eol
    return raw[: m.start(1)] + block + raw[m.end(1):]


def splice_hook_table(raw, line, rel):
    if raw.count(HOOK_BEGIN) != 1 or raw.count(HOOK_END) != 1:
        raise PolicyError(f"{rel}: マーカー {HOOK_BEGIN} / {HOOK_END} がちょうど1組見つからない")
    eol = _eol_of(raw)
    m = re.search(re.escape(HOOK_BEGIN) + r"\r?\n(.*?)" + re.escape(HOOK_END), raw, re.DOTALL)
    if not m:
        raise PolicyError(f"{rel}: マーカーの並びが不正")
    return raw[: m.start(1)] + line + eol + raw[m.end(1):]


# ---------------------------------------------------------------- 生成対象の列挙・適用・検査

def _read_raw(path):
    with open(path, "rb") as f:
        data = f.read()
    bom = data.startswith(b"\xef\xbb\xbf")
    return data[3:].decode("utf-8") if bom else data.decode("utf-8"), bom


def _write_raw(path, text, bom):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        if bom:
            f.write(b"\xef\xbb\xbf")
        f.write(text.encode("utf-8"))


def targets(root, p):
    """生成対象を [(rel, new_text, bom)] で返す。存在しない前提ファイルは PolicyError。"""
    out = []
    for role, r in roles(p).items():
        rel = f".claude/agents/{role}.md"
        path = os.path.join(root, rel)
        if r.get("kind") == "subagent":
            if not os.path.exists(path):
                raise PolicyError(f"{rel} が無い(先に generate-adapters.py 第2節が生成する)")
            raw, bom = _read_raw(path)
            out.append((rel, render_claude_agent(raw, p, role, rel), bom))
        rel = f".github/agents/{role}.agent.md"
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            raise PolicyError(f"{rel} が無い(policy.roles と .github/agents の役割集合がずれている)")
        raw, bom = _read_raw(path)
        out.append((rel, render_github_agent(raw, p, role, rel), bom))
    overrides = p.get("phase_overrides") or {}
    for cmd in overrides:
        if not os.path.exists(os.path.join(root, ".claude", "commands", f"{cmd}.md")):
            raise PolicyError(f"phase_overrides.{cmd}: .claude/commands/{cmd}.md が無い(生成先は commands。skills ではない)")
    for path in sorted(glob.glob(os.path.join(root, ".claude", "commands", "*.md"))):
        cmd = os.path.basename(path)[:-3]
        rel = f".claude/commands/{cmd}.md"
        raw, bom = _read_raw(path)
        effort = (overrides.get(cmd) or {}).get("effort") if isinstance(overrides.get(cmd), dict) else None
        out.append((rel, render_command(raw, effort, rel), bom))
    if ((p.get("enforcement") or {}).get("copilot") or {}).get("settings_json", True):
        out.append((COPILOT_SETTINGS_REL, render_copilot_settings(p), False))
    if enforcement_cc(p).get("pre_tool_use_role_table", True):
        for rel, kind in ((HOOK_SH_REL, "sh"), (HOOK_PS1_REL, "ps1")):
            path = os.path.join(root, rel)
            if not os.path.exists(path):
                raise PolicyError(f"{rel} が無い(役割表の埋め込み先)")
            raw, bom = _read_raw(path)
            out.append((rel, splice_hook_table(raw, render_hook_table_line(p, kind), rel), bom))
    blocks = [(PLATFORM_REL, "model-policy-platform", render_platform_block),
              (AGENTS_HTML_REL, "model-policy-agents-html", render_agents_html_block)]
    # README.md の対照表は本体専用。配布先(sync / intake / テンプレ複製から実プロジェクト化したもの)の README は
    # アプリの README に置き換わりマーカーが無いので生成対象にしない(2026-09-17 の 3 実プロジェクト同期で
    # validate (j-11) が ERROR になった。本体判定はフック H-11 / doctor.is_body と同じ規則)
    if _is_body(root):
        blocks.insert(1, (README_REL, "model-policy-readme", render_readme_block))
    for rel, name, gen in blocks:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            raise PolicyError(f"{rel} が無い(対照表の生成先)")
        raw, bom = _read_raw(path)
        out.append((rel, splice_block(raw, name, gen(p), rel), bom))
    return out


def _is_body(root):
    """本体リポジトリか(判定の正は tools/doctor.py の is_body = フックの H-11 と同規則)。
    doctor を読めない環境では従来どおり本体扱い(README を生成対象に含める)。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import doctor as _doctor  # noqa: E402
        return bool(_doctor.is_body(root))
    except Exception:  # noqa: BLE001
        return True


def _same_text(cur, new):
    """改行コードの差(CRLF/LF)だけは一致とみなす。Windows の core.autocrlf=true チェックアウトでは
    作業ツリーが CRLF になるため、バイト比較だと git checkout 直後に必ず「ずれ」になる(統合時に実測)。"""
    if cur is None:
        return False
    return cur.replace("\r\n", "\n") == new.replace("\r\n", "\n")


def stale_targets(root, p):
    stale = []
    for rel, new, _bom in targets(root, p):
        path = os.path.join(root, rel)
        cur = _read_raw(path)[0] if os.path.exists(path) else None
        if not _same_text(cur, new):
            stale.append(rel)
    return stale


def apply_all(root, log=print):
    p = load_policy(root)
    written = 0
    for rel, new, bom in targets(root, p):
        path = os.path.join(root, rel)
        cur = _read_raw(path)[0] if os.path.exists(path) else None
        if not _same_text(cur, new):
            _write_raw(path, new, bom)
            written += 1
            log(rel)
    return written


def settings_deny_status(root, p):
    """(.claude/settings.json の deny に無い規則, 方針に無い Agent(model:…) 規則) を返す。読めなければ (None, None)。"""
    path = os.path.join(root, SETTINGS_REL)
    try:
        data = json.loads(re.sub(r"^\s*//.*$", "", open(path, encoding="utf-8-sig").read(), flags=re.M))
        deny = list((data.get("permissions") or {}).get("deny") or [])
    except Exception:  # noqa: BLE001
        return None, None
    expected = deny_rules(p)
    missing = [r for r in expected if r not in deny]
    extra = [d for d in deny if d.startswith("Agent(model:") and d not in expected]
    return missing, extra


def apply_deny(root):
    """permissions.deny に Agent(model:…) 規則を追記する(保守モード中は拒否)。戻り値: 追記件数。"""
    if os.path.exists(os.path.join(root, LOCKED_REL)):
        raise PolicyError(f"{LOCKED_REL} が存在する(保守モード中)。--off --apply で通常モードへ戻してから実行する"
                          "(保守モード中の settings.json への変更は復元で失われる)")
    p = load_policy(root)
    path = os.path.join(root, SETTINGS_REL)
    raw = open(path, encoding="utf-8-sig").read()
    data = json.loads(raw)
    perms = data.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    added = [r for r in deny_rules(p) if r not in deny]
    if not added:
        return 0
    deny.extend(added)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return len(added)


# ---------------------------------------------------------------- CLI

def cmd_check(root):
    try:
        p = load_policy(root)
        stale = stale_targets(root, p)
    except PolicyError as e:
        print("ERROR:", e)
        return 2
    for rel in stale:
        print(f"STALE: {rel} (python tools/generate-adapters.py で再生成)")
    missing, extra = settings_deny_status(root, p)
    if missing is None:
        print(f"INFO: {SETTINGS_REL} を読めないため deny の照合をスキップ")
    else:
        if missing:
            print(f"INFO: {SETTINGS_REL} の permissions.deny に未反映: {', '.join(missing)}"
                  " (python tools/generate-adapters.py --apply-deny で反映。validate が WARN する)")
        if extra:
            print(f"INFO: 方針に無い Agent(model:…) deny: {', '.join(extra)}")
    n = len(targets(root, p))
    print(f"model-policy --check: {'NG' if stale else 'OK'} (対象 {n} ファイル中 差分 {len(stale)})")
    return 1 if stale else 0


def _selftest_policy():
    """自己テスト用の小さな方針(既定が具体モデルの役割=注入経路を含む)。"""
    return {
        "version": 1, "retrieved": "2026-09-10",
        "models": {
            "opus-5": {"claude_code": {"id": "claude-opus-5", "alias": "opus"}, "copilot_display": "Claude Opus 5",
                       "copilot_cli_id": "claude-opus-5",
                       "price_usd_per_mtok": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10},
                       "price_prefixes": ["claude-opus-5"]},
            "sonnet-5": {"claude_code": {"id": "claude-sonnet-5", "alias": "sonnet"}, "copilot_display": "Claude Sonnet 5",
                         "copilot_cli_id": "claude-sonnet-5",
                         "price_usd_per_mtok": {"input": 2, "output": 10, "cache_read": 0.2, "cache_write_5m": 2.5, "cache_write_1h": 4},
                         "price_prefixes": ["claude-sonnet-5"]},
            "haiku-4.5": {"claude_code": {"id": "claude-haiku-4-5", "alias": "haiku"}, "copilot_display": "Claude Haiku 4.5",
                          "copilot_cli_id": "claude-haiku-4.5",
                          "price_usd_per_mtok": {"input": 1, "output": 5, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2},
                          "price_prefixes": ["claude-haiku-4-5"]},
        },
        "legacy_models": {"sonnet-4": {"price_prefixes": ["claude-sonnet-4"],
                                       "price_usd_per_mtok": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write_5m": 3.75, "cache_write_1h": 6}}},
        "roles": {
            "reviewer": {"kind": "subagent", "claude_code": {"model": "inherit", "effort": "high", "background": False},
                         "copilot_vscode": {"model": ["opus-5", "sonnet-5"]}, "copilot_cli": {"effortLevel": "high"}, "never_low": True},
            "worker-low": {"kind": "subagent", "claude_code": {"model": "sonnet-5", "effort": "low", "maxTurns": 60},
                           "copilot_vscode": {"model": None}, "copilot_cli": {"model": "sonnet-5", "effortLevel": "low"}},
            "implement": {"kind": "phase", "claude_code": {"model": "inherit", "effort": "inherit"},
                          "copilot_vscode": {"model": None}, "copilot_cli": {}},
        },
        "session": {"copilot_cli_pin": None},
        "phase_overrides": {"06-implement-task": {"effort": "medium"}},
        "enforcement": {"claude_code": {"deny_agent_model": ["haiku-4.5"], "deny_wildcard": False}, "copilot": {"settings_json": True}},
    }


def selftest():
    passed = failed = 0

    def expect(cond, name):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS:", name)
        else:
            failed += 1
            print("FAIL:", name)

    p = _selftest_policy()
    # (1) deny 規則: alias とフル ID の両表記、wildcard は既定オフ
    expect(deny_rules(p) == ["Agent(model:haiku)", "Agent(model:claude-haiku-4-5)"], "deny_rules: alias + full id, no wildcard")
    p2 = json.loads(json.dumps(p))
    p2["enforcement"]["claude_code"]["deny_wildcard"] = True
    expect("Agent(model:claude-haiku-4-5*)" in deny_rules(p2), "deny_rules: wildcard opt-in")
    # (2) 役割表: subagent のみ、既定、許可集合(deny を除く)、legacy prefix
    t = role_table(p)
    expect(set(t["roles"]) == {"reviewer", "worker-low"}, "role_table: subagent roles only")
    expect(t["roles"]["reviewer"]["default"] == "inherit" and t["roles"]["worker-low"]["default"] == "claude-sonnet-5",
           "role_table: default normalized (key -> full id)")
    expect("haiku" not in t["roles"]["reviewer"]["allowed_exact"] and "claude-haiku-4-5" not in t["roles"]["reviewer"]["allowed_prefix"],
           "role_table: denied model excluded from allowed")
    expect("claude-sonnet-4" in t["roles"]["reviewer"]["allowed_prefix"] and t["deny_exact"] == ["haiku"], "role_table: legacy prefix allowed, deny_exact")
    expect("'" not in role_table_json(p) and role_table_json(p).isascii(), "role_table_json: ascii, no single quote")
    # (3) .claude/agents frontmatter の差し込み(冪等・手書き行の除去)
    base = "---\nname: reviewer\ndescription: d\ntools: Read, Grep\n---\n\nbody\n"
    r1 = render_claude_agent(base, p, "reviewer")
    expect("model: inherit\neffort: high\nbackground: false\n" in r1 and CLAUDE_BODY_NOTE in r1, "render_claude_agent: policy lines inserted")
    expect(render_claude_agent(r1, p, "reviewer") == r1, "render_claude_agent: idempotent")
    tampered = r1.replace("effort: high", "effort: low")
    expect(render_claude_agent(tampered, p, "reviewer") == r1, "render_claude_agent: hand edit is normalized back")
    r2 = render_claude_agent(base.replace("reviewer", "worker-low"), p, "worker-low")
    expect("model: claude-sonnet-5\neffort: low\nmaxTurns: 60\n" in r2, "render_claude_agent: concrete model / maxTurns")
    p3 = json.loads(json.dumps(p))
    p3["enforcement"]["claude_code"]["emit_effort_frontmatter"] = False
    expect("effort:" not in render_claude_agent(base, p3, "reviewer"), "render_claude_agent: emit_effort_frontmatter=false omits effort")
    # (4) .github/agents の model 行: auto 除去・配列生成・null はマーカーのみ・冪等
    gh = "---\ndescription: 'x'\ntools: ['read']\nagents: []\nmodel: auto\nhandoffs:\n  - agent: a\n    label: 'l'\n---\nbody\n"
    g1 = render_github_agent(gh, p, "implement")
    expect("model: auto" not in g1 and GH_MARK in g1 and "handoffs:" in g1, "render_github_agent: auto removed, marker kept")
    expect(render_github_agent(g1, p, "implement") == g1, "render_github_agent: idempotent")
    g2 = render_github_agent(gh, p, "reviewer")
    expect("model: ['Claude Opus 5', 'Claude Sonnet 5']" in g2 and g2.index(GH_MARK) < g2.index("model: ["), "render_github_agent: display-name array")
    expect(not any(ln.startswith("model:") for ln in render_github_agent(g2, p, "implement").splitlines()),
           "render_github_agent: switching to null removes array")
    # (5) commands の effort
    c = "---\ndescription: 'd'\n---\n\nbody\n"
    c1 = render_command(c, "medium")
    expect("effort: medium" in c1 and render_command(c1, None) == c, "render_command: add and remove effort")
    # (6) copilot settings.json
    cs = json.loads(render_copilot_settings(p))
    expect(cs["_generated_from"] == POLICY_REL and "model" not in cs, "copilot settings: no session pin when null")
    expect(cs["subagents"]["agents"]["reviewer"] == {"effortLevel": "high"}
           and cs["subagents"]["agents"]["worker-low"] == {"model": "claude-sonnet-5", "effortLevel": "low"},
           "copilot settings: subagents.agents entries")
    # (7) 単価解決(最長一致)
    expect(resolve_price(p, "claude-sonnet-5-20260401")["key"] == "sonnet-5" and resolve_price(p, "claude-sonnet-4-6")["key"] == "sonnet-4"
           and resolve_price(p, "gpt-5") is None, "resolve_price: longest prefix, legacy fallback, unknown -> None")
    # (8) マーカー差し込み(CRLF 保存)
    doc = "a\r\n<!-- BEGIN GENERATED: model-policy-readme -->\r\nold\r\n<!-- END GENERATED: model-policy-readme -->\r\nz\r\n"
    s = splice_block(doc, "model-policy-readme", "new1\nnew2", "doc")
    expect(s == "a\r\n<!-- BEGIN GENERATED: model-policy-readme -->\r\nnew1\r\nnew2\r\n<!-- END GENERATED: model-policy-readme -->\r\nz\r\n",
           "splice_block: keeps CRLF")
    hook = "x\n" + HOOK_BEGIN + "\nrole_table='old'\n" + HOOK_END + "\ny\n"
    expect(splice_hook_table(hook, "role_table='new'", "h") == "x\n" + HOOK_BEGIN + "\nrole_table='new'\n" + HOOK_END + "\ny\n",
           "splice_hook_table: replaces table line")
    # (9) apply_deny: 追記と保守モード拒否(一時ディレクトリ)
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".claude"))
        os.makedirs(os.path.join(tmp, ".github", "harness"))
        with open(os.path.join(tmp, POLICY_REL), "w", encoding="utf-8") as f:
            yaml.safe_dump(p, f, allow_unicode=True) if yaml else f.write("")
        with open(os.path.join(tmp, SETTINGS_REL), "w", encoding="utf-8") as f:
            f.write('{\n  "permissions": {\n    "deny": [\n      "Edit(AGENTS.md)"\n    ]\n  }\n}\n')
        if yaml:
            n = apply_deny(tmp)
            data = json.load(open(os.path.join(tmp, SETTINGS_REL), encoding="utf-8"))
            expect(n == 2 and data["permissions"]["deny"] == ["Edit(AGENTS.md)", "Agent(model:haiku)", "Agent(model:claude-haiku-4-5)"],
                   "apply_deny: appends rules after existing deny")
            expect(apply_deny(tmp) == 0, "apply_deny: idempotent")
            open(os.path.join(tmp, LOCKED_REL), "w").close()
            try:
                apply_deny(tmp)
                expect(False, "apply_deny: refuses in maintenance mode (.locked)")
            except PolicyError:
                expect(True, "apply_deny: refuses in maintenance mode (.locked)")
            missing, extra = settings_deny_status(tmp, p)
            expect(missing == [] and extra == [], "settings_deny_status: none missing after apply")
        else:
            print("SKIP: apply_deny (PyYAML なし)")
    # (10) 実方針(リポジトリ)の読込と生成対象の列挙が通る(存在すれば)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(root, POLICY_REL)) and yaml:
        try:
            real = load_policy(root)
            n = len(targets(root, real))
            expect(n >= 3, f"real policy: targets enumerated ({n})")
            expect(set(role_table(real)["roles"]) == {"reviewer", "spec-critic", "task-worker"}, "real policy: 3 subagent roles in table")
        except PolicyError as e:
            expect(False, f"real policy loads: {e}")
    print(f"model_policy selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="役割別モデル方針(model-policy.yml)の生成・検査")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="生成物を書く(generate-adapters.py の第4節と同じ)")
    g.add_argument("--check", action="store_true", help="生成物が方針とずれていないか(0=一致 / 1=差分 / 2=エラー)")
    g.add_argument("--print-deny", action="store_true", help="permissions.deny に入れる Agent(model:…) 規則を出力")
    g.add_argument("--apply-deny", action="store_true", help=".claude/settings.json の permissions.deny に追記(.locked があれば拒否)")
    g.add_argument("--print-prices", action="store_true", help="単価表(prefix 族フォールバック付き)を JSON 出力")
    g.add_argument("--print-role-table", action="store_true", help="フック埋め込み用の役割表(JSON)を出力")
    g.add_argument("--selftest", action="store_true", help="自己テスト")
    args = ap.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.selftest:
            return selftest()
        if args.check:
            return cmd_check(root)
        if args.print_deny:
            p = load_policy(root)
            rules = deny_rules(p)
            print("# permissions.deny に追加する規則(呼出時 model パラメータの literal 一致のみ。"
                  "frontmatter / inherit 由来には効かない)")
            for r in rules:
                print(r)
            print(json.dumps(rules, ensure_ascii=False))
            return 0
        if args.apply_deny:
            n = apply_deny(root)
            print(f"{SETTINGS_REL}: permissions.deny に {n} 件追記" if n else f"{SETTINGS_REL}: 追記なし(既に反映済み)")
            return 0
        if args.print_prices:
            print(json.dumps(price_table(load_policy(root)), ensure_ascii=False, indent=2))
            return 0
        if args.print_role_table:
            print(role_table_json(load_policy(root)))
            return 0
        if args.apply:
            n = apply_all(root, log=lambda rel: print("wrote:", rel))
            print(f"model-policy: {n} ファイル更新")
            return 0
    except PolicyError as e:
        print("ERROR:", e)
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
