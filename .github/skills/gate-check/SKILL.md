---
name: gate-check
description: docs/00-overview/progress.md の状態を読み書きしてフェーズゲート(未着手/進行中/ゲート承認待ち/完了)を判定・更新する手順。オーケストレーターや各フェーズエージェントが進捗確認・更新するときに使う。
---

# ゲート判定スキル

## 状態の正

`docs/00-overview/progress.md` の先頭にある機械可読ブロックが正。人間向けの表と
必ず一致させる。

```
<!-- GATE_STATUS
requirements: not_started | in_progress | pending_approval | done
design: not_started | in_progress | pending_approval | done
implementation: not_started | in_progress | pending_approval | done
test: not_started | in_progress | pending_approval | done
release: not_started | in_progress | pending_approval | done
-->
<!-- GATE_COUNTERS
implement_test_loops: 0
-->
```

**状態機械の仕様の正は `.github/harness/STATE-MACHINE.md`**（語彙・許される遷移・誰がいつ書くか・
implement↔test の往復上限・並行更新・復旧手順）。本スキルはその判定と操作の手順。キーは 5 つ固定、
値は 4 語彙（行の最初のトークン。後ろの注記は許容）、`GATE_COUNTERS`（往復回数。無ければ 0）は
GATE_STATUS 直下。完全性（キー欠落・語彙外の値）と順序矛盾は `python tools/gate_status.py check` が検査し、
`warn-stale-gate` フックが progress.md 書込後に同じ規則で警告、`tools/validate-harness.py` (r) も同じ検査を行う。

## 判定手順

1. `docs/00-overview/progress.md` が無ければ `progress_template.md` から作成する。
2. 各フェーズについて、対応する成果物ファイルの有無から実態を推測する。
   - requirements: `docs/01-requirements/requirements.md` が無ければ `not_started`。
     あれば `in_progress`。「未確定事項」欄が空でユーザー承認を得ていれば `done`。
   - design: `docs/02-design/architecture.md`
   - implementation: `docs/03-implementation/tasks.md`（全チェックボックス完了で`done`。
     ただし `👤` 印の**人手必須タスク**は、ドラフトの出力ではなく**実体の配置・実行が
     確認できて**初めて完了となる。人手必須タスクの残件が1件でもあれば `done` にしない（D039）。
     **さらに独立レビュー（`reviewer`）の実施記録が1件以上あること**:
     `docs/04-test/review-log.md` の日時付きエントリ（`review_log_template.md` の形式。
     最新エントリの日付が `tasks.md` の最新の完了証拠と同日以降）、または
     `docs/04-test/security-review-report.md`。記録の無い `done` は `warn-gate-tamper`
     フックが「独立レビューの記録がありません」と警告し、`golden-eval` が WARN する
     （D073。実装フェーズ内の呼び出し点は implement.agent.md 手順5・6）
   - test: `docs/04-test/test-report.md`（「独立レビュー」節に結論があり、対応する記録が
     `review-log.md` または `security-review-report.md` にあること。機械検査は implementation と同じ）
   - release: `docs/05-release/release-checklist.md`（外部反映の action packet の全行に承認欄と
     実行結果が埋まり、`reviewer` の release 検証エントリが `review-log.md` にあること。
     release.agent.md「計画→独立検証→承認→実行」）
3. GATE_STATUSブロックと実態がずれていれば、ユーザーに更新してよいか確認してから書き換える
   （エージェントが黙って `done` にしない。ユーザーの明示的な承認発言があって初めて `done` にする）。
4. `.github/hooks/scripts/` のゲート系フックはこのGATE_STATUSブロックを直接パースするため、
   フォーマット（インデント・キー名）を崩さない。書換は `python tools/gate_status.py set <phase> <value>`
   （一時ファイル→rename の原子的書換＋遷移ログ 1 行。書換後の完全性を検査してから書く。`done` は
   `--evidence` で証拠 3 点セットが必須＝`guard-done-evidence` と同じ規則を CLI 経路でも課す）を推奨する。
   手で書いたときは `python tools/gate_status.py check` で完全性と順序矛盾を確認する。

## 遷移の責務と往復上限（要約。正は STATE-MACHINE.md §2・§3）

- **`in_progress` は入口の最初のステップで書く**（フェーズコマンドにバインドされたエージェント・`/12`・`/13`・
  orchestrator の機械的更新。着手前の読み取り調査の直後、成果物を書く前）。これは正規の遷移であり
  `warn-gate-tamper` の in_progress 警告は確認を促す通知（D067）。入口を経ずにフェーズ外編集ガードを通すためだけに
  書き換えるのはゲート改竄（D063）。
- **`pending_approval` はフェーズの最終ステップで書く**（成果物が確定し独立レビューを終えた直後、ユーザーに承認を
  求める前。全自動区間でも次フェーズへ渡す前に自フェーズを `pending_approval` にする）。
- **`done` は人の承認発言の後にだけ書く**（本スキル手順 3。全自動区間の implementation / test は release のゲート承認で
  まとめてよい。ライトパスは承認①②で一括）。
- **巻き戻し（`done → in_progress`）は `/12-change-request` と `/13-converge` 経由だけ**。該当フェーズだけを戻し、
  後続の `done` はそのまま（progress.md に「状態: 運用中」の注記があること＝順序矛盾検査の規則 B の免除根拠）。
- **implement ↔ test の往復上限**: `test` が `実装に戻る` で implementation を `in_progress` に戻すたびに
  `GATE_COUNTERS` の `implement_test_loops` を +1（`python tools/gate_status.py bump-loop`）。上限
  （`tools/usage-config.json` の `implement_test_loop_max`、既定 3）を超えたら自動の差し戻しをやめ、
  `/13-converge` か人の判断に上げる（`warn-gate-tamper` と `validate` (r) が WARN）。`test: done` で 0 に戻す。
- **復旧**（GATE_STATUS が壊れた・lock が残った・遷移ログがずれた）: STATE-MACHINE.md §6 の手順
  （`python tools/gate_status.py recover --from log|git|table|baseline` / `unlock` / `reconcile`）。

## 完了と停止の条件（全フェーズ共通の論理式）

フェーズやタスクの「完了」は、エージェントの完了宣言ではなく次の条件の全充足で判定する。

```
完了 = 受入条件を満たす AND 定義された機械的検証(テスト・ビルド等)が通る
       AND 必要な成果物が docs/ に存在する AND 人間の承認が必要な箇所は承認済み
```

逆に、次のいずれかに当たったら自律継続をやめて停止・報告する（成功による停止と
失敗による停止は別物であり、両方を明示する）。

```
エスカレーション = 同一失敗の反復(3回) OR 進捗なし OR 要件・設計で判断できない事実が必要
                 OR 破壊的操作・方針境界に到達 OR 完了条件そのものが満たせないと判明
```

**完了マークの規律**: `tasks.md` のチェックボックスと GATE_STATUS は「通すために
書き換える」対象になり得る（Markdownはモデルが編集できてしまう）。完了マークは
必ず完了条件の証拠とセットで付け、`done` への遷移は必ずユーザー承認を経る。
上記完了論理式の「機械的検証が通る」の証拠は、**「再実行可能なコマンド + その出力の
要約 + 実行日時（YYYY-MM-DD HH:MM）」の3点セット**で記録する。実行日時の無い証拠・
古い証拠での `[x]` 化は不可（完了ハルシネーション対策）。
**ゲート承認時は証拠の実行日時を確認し、そのゲート作業内（当日）の日時でなければ
再実行を求める**（過去の実行結果の使い回しでゲートを通さない）。
3点セットの書式は1行に揃える: `` 証拠: `<再実行可能なコマンド>` → <出力の要約> (YYYY-MM-DD HH:MM) ``
（`tasks.md` はタスク行の直下、`progress.md` はフェーズ表の備考欄か申し送りに、承認者と
併記する）。`[x]` を増やす書込・`done` へ遷移させる書込は、**同じ書込の新内容にこの3点が
無ければ `guard-done-evidence` フック（PreToolUse）が拒否する**（機械強制。証拠の真偽は
reviewer 観点5 と `golden-eval` が照合する。人間の直接編集は対象外。A2-4b）。
reviewer は証拠と実体の一致を照合する（reviewer観点5）。
機械的検証は `python tools/golden-eval.py <プロジェクトパス>` でいつでも実行できる
（完了宣言と成果物実態の突き合わせ。テスト・リリース前の自己点検に使う）。

## ライトパスの GATE_STATUS 運用（小規模グリーンフィールド。fast-track スキル使用時）

小規模な新規開発をライトパス（`fast-track` スキル）で進める場合、ゲート承認を
**2回に集約**する。承認回数を束ねるだけで、「`done` への遷移は必ずユーザーの明示的な
承認を経る」原則は変わらない（エージェントが黙って `done` にしない点は同じ）。

- **開始時**: `progress.md` の「未確定事項・申し送り」に
  「ライトパス使用中（fast-track。承認は2回: ①要件+設計 ②リリース前）」と1行注記する。
  この注記が、個別ゲートを経ない `done` 遷移を `warn-gate-tamper` 等の監査で説明する根拠になる。
- **承認①（要件+設計まとめて）**: ユーザーの承認1回で requirements と design を
  **一括で** `done` に遷移してよい（成果物の存在確認は通常どおり:
  簡易記入の `requirements.md` と `architecture.md` が実在すること）。
- **承認②（リリース前確認）**: 証拠3点セットの完備と reviewer レビューの完了
  （`review-log.md` の日時付きエントリまたは `security-review-report.md` への記録。
  **ライトパスでも reviewer 1回は省略できない**＝fast-track スキルのアンチパターン）を前提に、
  ユーザーの承認1回で implementation / test / release を一括で `done` に遷移してよい。
  証拠の当日鮮度チェックは通常どおり行う。あわせて注記を「ライトパス完了」に更新する。
  ライトパスはタスク10個超で昇格するため、implement.agent.md の「完了タスク10個ごとの
  チェックポイントレビュー」は発生せず、この承認②前の1回が implementation / test 両方の
  done 条件を満たす。
- **フルパスへの昇格時**（タスク10超・要件変更・複数機能化）: 注記を
  「ライトパスからフルパスへ昇格（日付・理由）」に更新し、以後は通常の個別ゲート運用に戻る。

## 運用中の扱い（判定の正）

**運用中 = GATE_STATUS の5フェーズが全て `done`、または `progress.md` に
「状態: 運用中」の注記がある。** これは「プロジェクト完了」ではなく **「運用中」** であり、
リリース済みプロジェクトと、`/11-brownfield-intake` で取り込んだ既存アプリの
初期状態の両方に当たる。`/11-brownfield-intake` は既存テストの状態によっては
test = `in_progress` のまま運用中注記を書くことがあり、その場合も運用中として扱う。

**この状態でユーザーから依頼を受けたときの入口は `/12-change-request`。**
「全部 done なので次にやることはありません」と答えて終わらせない（入口が示されないと、
エージェントはハーネス外の場当たり作業に落ちる。D043）。

## 改修サイクル（リリース後の修正時）

手順の正は `change-request` スキル（`.github/skills/change-request/SKILL.md`）。
以下はゲート操作の部分だけを示す。AGENTS.md「差分駆動の原則」の4分類に基づき、
改修の起点に応じて該当フェーズを `in_progress` へ戻す。

- 要件・設計の変更を伴う改修 → 該当する上流フェーズ（requirements または design）から
- 変更を伴わないバグ修正 → implementation から
- 戻すのは**該当フェーズ以降のみ**（全フェーズのやり直しはしない）
- 改修理由を `progress.md` の「未確定事項・申し送り」に1行記録する
- リリース直前の**小規模な要件追加**は、AGENTS.md「差分駆動の原則」5. の条件
  （影響範囲が閉じていることの明示・`spec-critic` 省略はユーザーの明示承認・
  省略した手続きの progress.md への記録）を満たす場合に限り、
  複数フェーズを1セッションで通してよい

## リリース承認時の後処理（progress.md の肥大化対策）

リリースのゲート承認時（`release` を `done` にするとき）、`progress.md` の
「未確定事項・申し送り」から**完了した版のサイクル分**を
`docs/00-overview/archive/progress-v<版>.md` へ退避する
（GATE_STATUS ブロック・フェーズ表・進行中の申し送りは本文に残す）。
申し送りが append-only のまま積み上がると、オーケストレーターが最初に読むファイルが
1回の Read に収まらなくなる（D039）。

## 横断整合監査（ユーザーが「整合チェック」「監査」を求めたとき）

フェーズ判定は個々の成果物の有無を見るが、この監査は**成果物間の食い違い**を検出する。
改修が数回重なった後に特に効く。この監査の独立した入口として `/13-converge` がある。
以下を突き合わせ、食い違いを表で報告する（修正はしない）。

1. `requirements.md` の要求ID ↔ 設計のトレーサビリティ表（対応漏れ）
2. `architecture.md` / 詳細設計 ↔ `tasks.md` のタスク
   （設計にない実装・実装されない設計）
3. `tasks.md` ↔ テスト計画/レポートのケース対応
4. 文書中の実装ファイル・テストへの参照 ↔ 実在するか（削除・リネーム漏れ）
5. GATE_STATUS ↔ 各成果物の実態
6. 「実装乖離あり」注記 ↔ 解消期限切れがないか
