---
paths:
  - "docs/**"
---
<!-- generated from .github/instructions/docs.instructions.md by tools/generate-adapters.py 第7節 (tools/path_rules.py。Claude Code のパス限定ルール。手で編集しない。正は applyTo 側。再生成: python tools/generate-adapters.py) -->

<!-- ドキュメント規約の正(AGENTS.md「ドキュメント規約」は 1 行の参照だけ。Copilot は applyTo で、Claude Code は
     生成物 .claude/rules/docs.md(paths:)で、docs/ 配下のファイルを扱うときだけ読む=常駐予算の外。
     第3波 A6-19b(4) / A7-M-8。フックが無い環境(Antigravity)は AGENTS.md の参照行から辿る) -->

- `docs/` 配下の実体ファイルは、対応する `*_template.md` の見出し構成を維持する。見出しを削らない。
- テンプレートファイル自体（`*_template.md`）は編集しない。コピーして実体ファイルを作る
  （テンプレート名は snake_case、実体名は docs 内の指示に従う。例: `test_plan_template.md` → `test-plan.md`。
  直接編集は `guard-template-edit` が deny する）。
- 表形式の項目は空欄のまま残さず、「該当なし」等を明示する。
- Mermaid図は必ずシンタックスが有効か確認する（レンダリングエラーを残さない）。
- 不可視文字・文字化け（NUL / 本文中BOM / ハングル / ZWSP / NBSP / 双方向制御文字 等）を混入させない
  （書き込み後に `check-doc-chars` が警告する。混入したら除去する）。
