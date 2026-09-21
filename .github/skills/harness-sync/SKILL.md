---
name: harness-sync
description: ハーネス本体で適用済みの改善(DECISIONS.mdのD番号)を個別プロジェクトのハーネスコピーへ逆同期する手順。/91-sync-from-harnessでプロジェクト側から使う。tools/sync-harness.pyのdry-run→レポート確認→人間が--apply→検証→マーカー更新の流れ。本体還流(/90)の後工程。
---

# ハーネス逆同期スキル（本体の改善をプロジェクトへ反映する）

## 位置づけ（成長ループの3辺目）

1. `/10-retrospective`（プロジェクト）: 改善提案を作る
2. `/90-apply-retrospective`（本体リポジトリ）: 本体へ適用し DECISIONS.md に記録する
3. **`/91-sync-from-harness`（プロジェクト。このスキル）**: 本体の適用結果を
   プロジェクトのハーネスコピーへ反映する

本体に適用しただけでは、プロジェクト側のフック・エージェント定義・スキルは
**古いコピーのまま**である（ガードレールの修正ほどこの取り残しが危険）。
この逆同期が済んで初めて還流は完了する。

## 役割分担（なぜ人間がコマンドを実行するのか）

プロジェクト側にも本体と同じ保護（`permissions.deny` + フック）があるため、
エージェントがフックやエージェント定義を直接編集すると deny 一時解除の往復が
毎回発生する。`tools/sync-harness.py` は**人間が1コマンドで実行する**ことで
この儀式を丸ごと不要にする（人間がゲートである原則はそのまま。エージェントは
レポートの読解・差分の説明・docs の更新を担う）。

## 手順

1. **本体リポジトリのパスを決める**: `docs/00-overview/harness-origin.md` の
   `HARNESS_ORIGIN` ブロックに前回の本体パスが自動記録されている（intake-app.py /
   sync-harness.py の `--apply` が書く）。**記録があればそれを既定とし、ユーザーに
   確認だけ取る**（パスが実在しない・本体を移動した場合のみ聞き直す）。
   記録が無い場合はユーザーに聞く（推測で選ばない）。
2. **dry-run を実行する**（書き込みなし。環境が許せばエージェントが実行してよい。
   ブロックされる場合はコマンドをユーザーに提示する）:
   ```
   python tools/sync-harness.py
   ```
   （`--harness` 省略時は harness-origin.md の記録が使われる。記録が無い/上書きしたい
   場合は `--harness <本体のパス>` を明示する）
   結果は `docs/00-overview/harness-sync-report.md` に書き出されるので、
   エージェントはこれを読んで「追加/更新/要レビュー」を要約して提示する。
   本体側から実行する場合は `--project <プロジェクトのパス>` を使う（方向は常に本体→プロジェクト）。
3. **`--apply` は必ず人間が実行する**（保護対象ファイルへの書き込みを含むため）:
   ```
   python tools/sync-harness.py --apply
   ```
4. **要レビューのファイルを手動マージする**: `deploy-*` スキル（プロジェクト固有値表を
   持つ）や README 等は自動上書きされない。エージェントが両方の差分を読み、
   **汎用部分の変更だけ**を取り込む編集案を提示する（固有値表・プロジェクト固有の
   記述は保持する）。スキルは deny 対象外なのでエージェントが編集できる。
   `.claude/settings.json` も要レビューのため、本体側で追加された `permissions.deny` の
   `Agent(model:…)` 行や PreToolUse（`Agent|Task`）の `guard-subagent-model` 配線は自動反映
   されず衝突として出る。差分を読んで本体側の deny 行・hooks 配線を取り込む（人間が
   保守モードで編集。`python tools/generate-adapters.py --print-deny` で期待値を再計算できる）。
   `.github/harness/model-policy.yml` も要レビュー（プロジェクト固有の `plan_fallbacks` /
   `session.copilot_cli_pin` を守るため）で、取り込み後は `python tools/generate-adapters.py`
   で鏡（frontmatter・Copilot CLI 設定・対照表）を再生成する。
5. **検証**: 次を実行し、全PASS・エラー0を確認する（フックは壊れていても
   静かに通るため、同期後の selftest は省略しない）。
   ```
   bash .github/hooks/scripts/selftest.sh
   python tools/validate-harness.py
   python tools/doctor.py
   ```
   `doctor` は同期後の環境側の確認（配線・statusLine の実行・配布鮮度）。`distribution` 行が
   `copy at Dxxx = harness Dxxx` の PASS になっていれば `harness-origin.md` の更新まで完了している
   （`--apply` が `latest_decision` / `source_commit` / `synced_at` を書き直す）。WARN のまま
   なら手動マージ漏れか本体パスの不一致を疑う。
6. **完了処理（エージェントが行う）**:
   - `docs/00-overview/progress.md` の申し送りに「**ハーネス同期: DXXX まで適用済み**
     （YYYY-MM-DD、harness-sync-report.md 参照）」を記録し、還流待ちマーカー
     （「ハーネス還流 未適用/逆同期未了」）を消す。
   - `learnings.md` の「本体修正が還流されるまでの前提」の暫定運用行を消す
     （または解消済みと注記する）。
   - `harness-sync-report.md` はコミットに含めて記録として残す。

## 注意

- **このツールは削除をしない**。本体側で廃止されたファイルはレポートに現れないため、
  DECISIONS.md の該当エントリに廃止の記載があれば手動で消す。
- プロジェクトが独自に作ったスキル（`stack-conventions`、`.claude/skills/` の
  対応ポインタ等）は本体に存在しないため触れられない（安全側の設計）。
- 同期対象・要レビューの分類は `tools/sync-harness.py` 冒頭のマニフェストが正。
  分類を変えたくなったら本体リポジトリ側で変更する（プロジェクト側のコピーを
  直接書き換えない）。
