# Claude Code 用エントリポイント

共通指示の正は AGENTS.md（下でインポート）。ここには振る舞いを書かない
（Claude Code は AGENTS.md を直接読まないため、このファイルが橋渡しをする。`/import` は使わない）。

@AGENTS.md

## Claude Code 固有の対応表（仕組みと根拠は `.github/harness/PLATFORM.md`「Claude Code 固有の運用」）

| 正の層（Copilot） | Claude Code での読み替え |
|---|---|
| `.github/prompts/*.prompt.md`（フェーズ起動） | `.claude/commands/` の同名スラッシュコマンド（生成物。`.agent.md` の役割定義と prompt 本文を読む薄いアダプタ。prompt files は VS Code の既定 Agent Host では読まれない＝CP-2。Copilot 側の入口は同じ prompt files から生成した入口スキル `.github/skills/<nn>-<name>/`（A6-17 / D081）で、Claude Code 側には `.claude/skills/<nn>-*/` のポインタを作らない＝スラッシュ名が `.claude/commands` と衝突する） |
| `runSubagent` | Agent ツール（旧称 Task）で `.claude/agents/` の同名サブエージェント（reviewer / task-worker / spec-critic）を呼ぶ |
| `.github/skills/` / `.github/instructions/*.instructions.md`（`applyTo` のパス限定指示） | `.claude/skills/` のポインタ（生成物。新設は正とポインタの両方＝`skill-authoring` スキル）/ `.claude/rules/<name>.md`（`paths:` の生成物。`tools/generate-adapters.py` 第7節。該当ファイルを読むときだけ載る＝常駐予算の外。手書きの rule は置かない） |
| ハンドオフ `send:false` / `send:true` | 「新しいセッションで該当コマンドを実行」の案内 / 同一セッション内でロールを切り替えて自動継続 |
| `.github/hooks/` | `.claude/settings.json` に同じスクリプトを配線（Windows は Git Bash 必須）＋ `permissions.deny / ask` で二重化（D046） |
| プレーンチャットの受付 | AGENTS.md「受付ルーチン」の入口コマンドを **Skill ツールで自分で起動**する（`.claude/commands/` のファイル名がスキル名。例: `12-change-request`） |
| ハーネス本体の保守（このテンプレート自体の改修） | 人間が `python tools/harness-maintenance.py --on --apply`（保守モード）→ 作業 → `--off --apply` で必ず戻す |
