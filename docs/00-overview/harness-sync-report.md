# ハーネス逆同期レポート

- 実行モード: **apply** / 日時: 2026-09-23 16:54
- 本体: `D:\vscode-worspace\CreateAppl`(**D098** まで) → プロジェクト: `D:\vscode-worspace\todo-app`
- 配布元の同一性: source_commit `2cee3e7974bb7b8c1e302115419e1ea273622906` / archive_sha256 `8f53e70d7d8378791aecdc0d2c7bec8c0bab3ea0871bf46aa3177f99af289f86`(`python tools/sync-harness.py --verify` で照合)
- 追加: 1 / 更新: 21 / 要レビュー: 1 / 変更なし: 238
- 旧生成物の掃除: 0。この一覧(本体で置き場を移した生成物)以外は削除しない=本体側で廃止されたファイルの掃除は手動で行う。

## 追加(適用済み)
- `tools/questionnaire.py`

## 更新(適用済み)
- `.agents/workflows/01-requirements-intake.md`
- `.claude/agents/spec-critic.md`
- `.claude/commands/01-requirements-intake.md`
- `.claude/skills/requirements-elicitation/SKILL.md`
- `.github/agents/requirements.agent.md`
- `.github/agents/spec-critic.agent.md`
- `.github/harness/COMPARISON.html`
- `.github/harness/COMPARISON.md`
- `.github/harness/PLATFORM.md`
- `.github/harness/USAGE.md`
- `.github/harness/commands.html`
- `.github/harness/skills.html`
- `.github/prompts/01-requirements-intake.prompt.md`
- `.github/prompts/02-requirements-deepdive.prompt.md`
- `.github/skills/01-requirements-intake/SKILL.md`
- `.github/skills/fast-track/SKILL.md`
- `.github/skills/harness-retrospective/SKILL.md`
- `.github/skills/requirements-elicitation/SKILL.md`
- `docs/00-overview/README.md`
- `docs/01-requirements/requirements_template.md`
- `docs/06-retrospective/retrospective_template.md`

## 要レビュー(自動上書きしない。差分を確認し手動でマージする)
- `README.md`
  - 混在ファイル(deploy-* の固有値表など)は、汎用部分の変更だけを手で取り込む。

## 未追跡のため配布しない(git ls-files 照合。本体でコミットしてから同期する。A7-M-1 / RG-14)
- なし

## 次の手順
1. 検証: `bash .github/hooks/scripts/selftest.sh` と `python tools/validate-harness.py` をプロジェクト側で実行し、全PASS/エラー0を確認する。
2. `docs/00-overview/progress.md` の申し送りを「ハーネス同期: D098 まで適用済み」に更新し、還流待ちマーカー・learnings.md の暫定運用行を消す(/91-sync-from-harness の完了処理)。
