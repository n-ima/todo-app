---
name: harness-apply-retrospective
description: ハーネス本体リポジトリ専用。個別プロジェクトの振り返り(retrospective*.md)の改善提案表を本体に適用し、DECISIONS.mdに記録する手順。/90-apply-retrospectiveでharness-maintainerエージェントが使う。個別プロジェクトのセッションでは使わない(harness-retrospectiveの還流方法を参照)。
---

# 還流適用スキル（振り返り改善提案の本体への取り込み）

## 前提確認（最初に必ず行う）

1. **本体リポジトリで動いているか確認する**: ルートに `DECISIONS.md` があり、
   `docs/00-overview/progress.md` が存在しない（= テンプレート状態）ことを確認する。
   個別プロジェクトのセッションだった場合はここで中止し、正規フロー
   （本体リポジトリを開いた新しいセッションで `/90-apply-retrospective` を実行）を案内する。
2. 対象の振り返りファイル（`retrospective*.md`）のパスをユーザーから受け取る。
   指定が無ければ確認する（推測で選ばない）。
3. `DECISIONS.md` を通読する（同じ議論の繰り返し・一度直した問題の再導入を防ぐ。
   AGENTS.md「参照」節と同じ理由）。

## 適用手順

1. 振り返りの改善提案表（対象ファイル・現状の問題・提案・根拠の4点セット）を読み、
   各提案を次の2群に仕分ける。
   - **そのまま適用可**: `.github/skills/`（下記の中核2スキルを除く）・`DECISIONS.md`・
     README/USAGE 等（`permissions.deny` の対象外）
   - **保護対象**: `.github/agents|hooks|workflows|prompts/`・`AGENTS.md`・`CLAUDE.md`・
     `plugin.json`・`.claude/settings.local.json`・`docs/**/*_template.md`・
     アダプタ層（`.claude/`・`.agents/`）・中核2スキル（`request-routing` / `gate-check`。
     `.github/skills/` と `.claude/skills/` の両方）
     （prompts は D046、settings.local.json と中核スキルは D048 で保護対象に加わった）
2. 「そのまま適用可」の提案を適用する。提案表に無い変更を混ぜない
   （気づいた別の改善は提案として報告し、勝手に適用しない）。
3. **保護対象**は、エージェントが scratchpad に**検証済みの適用スクリプト**を用意し、
   差分の要約を提示した上で**人間に1コマンドで実行してもらう**（D038 の sync-harness.py と
   同じ「人間がゲート」方式。D039 で確立）。実行時は人間が**保守モード**
   （`python tools/harness-maintenance.py --on --apply`）で deny とフックの両層を
   一時解除してから適用スクリプトを実行し、適用後に `--off --apply` で必ず復元する。
   適用スクリプトは
   ①置換ペアの全一致を検証してから書き込む2段階（1件でも不一致なら何も書かない）
   ②BOM・改行（CRLF/LF）を元ファイルに合わせて保持
   ③フック等のスクリプト類は本番適用の**前に** scratchpad で実機検証
   （selftest 相当）を済ませる、の3点を満たすこと。
   旧方式（`.claude/settings.json` の deny 行の一時解除）は、
   `guard-harness-config-edit` フックが正しく機能するようになった D037 以降、
   **deny 行を外してもフック層が編集を deny するため単独では機能しない**。
   Bash のファイル操作で deny・フックを迂回しない（人間の実行を待つ）。
4. スキルの新設・プロンプトの追加変更があった場合は、アダプタを手書きせず再生成する:
   `python tools/generate-adapters.py` → `python tools/validate-harness.py`（エラー0を確認）。
   エージェントからの実行が権限ガードにブロックされる環境（Claude Code auto モードで
   実測。生成ツールが保護対象パスへ書き込むため）では、この2コマンドをユーザーに
   提示して実行してもらい、結果の出力を確認する。
5. `DECISIONS.md` に D 連番で記録する。含める内容:
   出典（どのプロジェクトのどの振り返りか）・適用した各項目と根拠となった出来事・
   **見送った項目とその理由**・適用手順（保守モード使用の有無）。
   「reviewer で2回以上出た同種指摘」の昇格対象（B-1 運用ルール）の有無も確認して記録する。
6. コミットは既存の命名に合わせ
   （例: `<プロジェクト名>振り返りの還流: 改善提案N件を適用(DXXX)`）、
   ユーザーの確認を得てから行う。push は別途必ず確認する。
7. **完了処理**: 適用元プロジェクトの `docs/00-overview/progress.md` の申し送り
   「ハーネス還流 未適用 N件」を「本体適用済み（DXXX）・逆同期未了」へ更新する
   （このセッションから触れない場合は、更新すべき行をユーザーに提示して依頼する。
   残したままだと次サイクルが「未適用」という誤った前提で動く）。
   **本体に適用しただけではプロジェクト側のハーネスコピーは古いままである点に注意**:
   プロジェクトへの反映は、プロジェクトを開いたセッションで `/91-sync-from-harness`
   （`.github/skills/harness-sync/SKILL.md`）を案内する。マーカーと learnings.md の
   暫定運用行を完全に消すのは逆同期の完了時（/91 の完了処理）。
8. **完了条件の確認**: `audits/PROPOSALS.md`（提案トレーサビリティ台帳）の
   全提案IDについて applied / deferred / rejected を記入したことを確認する
   （deferred / rejected には理由を書く）。リリースを行う場合は、`plugin.json` の
   version 昇格・`CHANGELOG.md` への追記・git tag の作成をユーザーに依頼する。

## アンチパターン

- 個別プロジェクトのセッションから本体を直接編集する（D035。
  直接適用の指示があっても最低1回は確認を促す）。
- 保守モードの復元（`--off --apply`）を確認せずに終える（ガード解除の状態が
  コミットに混入したまま残るのが最悪）。
- DECISIONS.md への記録を省く（記録の無い変更は、次の保守で「なぜこうなっているか」が
  分からず同じ議論を繰り返す）。
- 提案表に無い「ついでの改善」を混ぜる（レビュー不能になる。別提案として報告する）。
