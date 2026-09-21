# STATE-MACHINE.md — フェーズゲートの状態機械（GATE_STATUS の正）

`docs/00-overview/progress.md` 先頭の機械可読ブロック `GATE_STATUS` が、このハーネスの**状態の正**である。
本書はその状態機械の**仕様の正**（語彙・許される遷移・誰がいつ書くか・往復上限・並行更新・復旧手順）で、
gate-check スキル（判定と操作の手順）・各フェーズエージェント・フック（`warn-gate-tamper` /
`warn-stale-gate` / `inject-progress` / `guard-phase-scope`）・`tools/gate_status.py`（機械検査と復旧 CLI）・
`tools/validate-harness.py` (r) は本書に従う（再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15 /
A7-H-5。D067 の「正規遷移」と guard-phase-scope の文言の矛盾を本書で解消）。

## 1. 書式と語彙

```
<!-- GATE_STATUS
requirements: not_started
design: not_started
implementation: not_started
test: not_started
release: not_started
-->
<!-- GATE_COUNTERS
implement_test_loops: 0
-->
```

- **キーは 5 つ固定**（`requirements` / `design` / `implementation` / `test` / `release`。この順が正準）。
  欠落・重複・未知のキー・読めない行は**完全性の破れ**（`validate-harness.py` (r) が ERROR、`warn-stale-gate`
  が progress.md 書込後に警告、`python tools/gate_status.py check` が exit 1）。
- **値は 4 語彙**: `not_started`（未着手）/ `in_progress`（進行中）/ `pending_approval`（ゲート承認待ち）/
  `done`（完了＝人がゲート承認済み）。値は行の**最初のトークン**で、後ろに空白区切りの注記
  （例 `done 2026-09-17` / `in_progress (CR-003)`）を置いてよい（全フックが最初のトークンで判定する。
  承認日の正式な置き場は人間向けのフェーズ表）。
- **`GATE_COUNTERS`**（GATE_STATUS 直下。任意）: `implement_test_loops` は implement↔test の往復回数（非負整数）。
  ブロックもキーも無ければ **0 扱い**（既存プロジェクトの後方互換。3 実プロジェクトの progress.md は 5 キーのみ）。
- 人間向けフェーズ表（`未着手` / `進行中` / `ゲート承認待ち` / `完了`）は GATE_STATUS の鏡であり、常に一致させる
  （復旧手順 6 の `recover --from table` はこの表から再構成する）。

## 2. 許される遷移と、誰がいつ書くか

| 遷移 | 誰が | いつ（どのステップで） |
|---|---|---|
| `not_started → in_progress` | そのフェーズの**入口**（フェーズコマンド `/01`〜`/09` にバインドされたエージェント、`/12-change-request`、`/13-converge`、または orchestrator の機械的更新） | **入口の最初のステップ**（着手前の読み取り調査の直後。成果物を書く前）。これは**正規の遷移**であり、`warn-gate-tamper` の in_progress 警告は「入口を経た遷移か」の確認を促す非ブロッキング通知（D067 の実測どおり進行を阻害しない）。**入口を経ずに、フェーズ外編集ガードを通すためだけに書き換えるのはゲート改竄**（D063。`guard-phase-scope` の deny 理由文と同じ定義） |
| `in_progress → pending_approval` | そのフェーズのエージェント | **フェーズの最終ステップ**＝成果物が確定し、独立レビュー（requirements / design は `spec-critic`、implementation / test は `reviewer` の `review-log.md` 記録）を終えた直後、**ユーザーに承認を求める前**。全自動区間（implement → test → release の `send: true` ハンドオフ）でも、次フェーズへ渡す前に自フェーズを `pending_approval` にする（`guard-phase-scope` は `pending_approval` も進行中として allow する） |
| `pending_approval → done` | 人（ゲート承認）。エージェントは**ユーザーの明示的な承認発言の後**にだけ書く | ゲート承認時。証拠の当日鮮度の確認は gate-check スキル。全自動区間の implementation / test は release のゲート承認（release.agent.md 手順 8 → orchestrator）でまとめて `done` にしてよい。ライトパスは承認①②で一括（gate-check スキル「ライトパスの GATE_STATUS 運用」） |
| `in_progress → done` | 同上 | 承認発言が成果物確定と同じターンで得られた場合の短絡（許容。`pending_approval` を経るのが正準） |
| `pending_approval → in_progress` | そのフェーズのエージェント | ゲートで差し戻された（承認されず修正を求められた）とき。理由を「未確定事項・申し送り」に 1 行 |
| `done → in_progress`（巻き戻し） | **`/12-change-request`**（change-request スキル手順 3 の該当フェーズ）と **`/13-converge`**（converge スキル手順 3.5）**だけ** | 改修サイクルの開始時。**該当フェーズだけ**を戻し、後続フェーズの `done` はそのまま（全フェーズのやり直しはしない＝AGENTS.md「差分駆動の原則」）。このとき progress.md 本文に「状態: 運用中」の注記があること（brownfield 取り込みと `/09` 完了時に書かれる。無ければ /12 が置く）＝順序矛盾検査の規則 B を免除する根拠 |
| `in_progress → in_progress`（test → implementation の差し戻し） | `test` エージェント（`実装に戻る` ハンドオフ） | implementation を `in_progress` に戻すと同時に **`implement_test_loops` を +1**（下記「往復上限」） |
| `done → not_started` / `pending_approval → not_started` | 使わない | 成果物を捨てる操作は差分駆動の否定。必要なら人が手で戻し、理由を申し送りに残す |

**13-converge → 06 の遷移**: `/13-converge` は検出・分類・起票までで修正しないが、分類「docs が正」の修正タスクを
`tasks.md` に起票したら、ユーザー承認のうえ `implementation` を `in_progress` に戻して `/06-implement-task` の入口を
開く（運用中は `guard-phase-scope` が in_progress 無しのアプリ編集を deny するため、遷移が無いと /06 が動かない）。
分類「実装が正」は該当上流フェーズ（requirements / design）を `in_progress` に戻して差分更新→再ゲート、
分類「どちらも古い」は CR 起票→ `/12`。消化後は通常どおり `reviewer` 記録→承認→ `done`。

**順序矛盾**（`warn-stale-gate` / `validate` (r) / `gate_status.py check` が WARN）:

- 規則 A: 後続フェーズが着手済み（`not_started` 以外）なのに先行フェーズが `not_started`。常に矛盾。
- 規則 B: 後続フェーズが `done` なのに先行フェーズが `done` でない（例 `test: done` なのに
  `implementation: in_progress`）。構築中は矛盾。「状態: 運用中」の注記があるときは改修サイクル
  （先行だけを戻す）または brownfield 取り込み残余（`test: in_progress`）として**免除**。

## 3. 往復上限（implement ↔ test）

- `test` が `実装に戻る` で implementation を `in_progress` に戻すたびに、`GATE_COUNTERS` の
  `implement_test_loops` を +1 する（`python tools/gate_status.py bump-loop`、または手で +1）。
  `test` が `done` になったら 0 に戻す（`gate_status.py set test done` は自動で 0 にする）。
- **上限は `tools/usage-config.json` の `implement_test_loop_max`（既定 3）**。上限を超えたら自動の差し戻しを
  やめ、`/13-converge`（要件・設計・実装の乖離が往復の原因になっていないかの棚卸し）か人の判断に上げる。
  超過は `warn-gate-tamper`（progress.md 書込後）と `validate` (r) / `gate_status.py check` が WARN する
  （`HARNESS_LOOP_MAX` で一時的に上書き可＝selftest 用）。
- カウンタは会話ではなく progress.md に置く（セッションを跨いでも数え直しにならない。implement.agent.md の
  試行記録と同じ原則）。

## 4. 遷移イベントログ（revision）

- `.github/hooks/logs/gate-transitions.jsonl`（ローカル・git 管理外）に、GATE_STATUS の変化を 1 行ずつ追記する。
  書き手は `_log.sh` / `_log.ps1` / `_log.py` の `gate_log` 系（判定ログ `hook-decisions.jsonl` と同じ共通実装）。
  欄（順序固定）: `ts` / `rev`（通番＝最終行の rev + 1） / `session_id` / `hook_event` / `tool_name` / `script` /
  `source`（`hook` = warn-gate-tamper の PostToolUse、`session-start` = inject-progress の突合、`cli` =
  `gate_status.py set`、`reconcile`、`recover:<元>`） / `before`（初回は null） / `after` / `changed`
  （`phase:前->後` のカンマ連結） / `loops`。
- **前→後の求め方**: PostToolUse は実ファイルが正なので、書込後の progress.md を読み、ログの最終記録の `after` と
  違えば追記する（Write / Edit / パッチ系のどれで書かれても同じ経路。`sed -i` 等フック外の書換や別マシンでの更新は
  次の SessionStart で `inject-progress` が突合して `session-start` 行を足す＝履歴の穴を閉じる）。
- 読み手: `python tools/gate_status.py reconcile`（手動突合）、受領書の GATE 遷移欄は従来どおり baseline との差分
  （本ログは監査用の一次データ）。

## 5. 並行更新（session.lock）

- `inject-progress`（SessionStart）が `.github/hooks/logs/session.lock`（`session_id` / `pid` / `ts` / `epoch` /
  `cwd`）を置く。**別セッションの新しい lock**（`session_lock_stale_minutes`、既定 120 分以内。
  `tools/usage-config.json`。`HARNESS_SESSION_LOCK_STALE_MIN` で上書き）があれば上書きせず「GATE_STATUS の書換は
  片方のセッションだけで」を注入し、`warn-gate-tamper` は他セッションの lock がある状態での progress.md 書込に
  警告を添える（**block はしない**＝人が意図して 2 セッションを使う場合を止めない。fail-open）。stale な lock は
  無視して取得する（クラッシュした前セッションの lock 残りを人が消す必要はない。消すなら
  `python tools/gate_status.py unlock`）。
- `session_id` が取れないホスト（VS Code の SessionStart ペイロードに `session_id` が載るかは**未検証**）では lock を
  扱わない（無記録・無警告）。
- **原子的な書換（推奨手順）**: GATE_STATUS を書き換えるときは「一時ファイルに全文を書いてから rename」する。
  `python tools/gate_status.py set <phase> <value>` はこの手順で書き、遷移ログにも 1 行残す。`done` への遷移は
  `--evidence '`<再実行可能なコマンド>` → <出力の要約> (YYYY-MM-DD HH:MM)'` が必須で、証拠は値の注記に残る
  （Bash 経由の書込は PreToolUse の `guard-done-evidence` を通らないため、CLI が同じ 3 点セットの規則を課す＝A2-4b を
  CLI 経路でも守る。人の承認発言を得てから実行する）。ホストの Edit / Write
  ツールは in-place 書込のため**フックでは強制しない**（PostToolUse は書かれた後にしか動けず、PreToolUse で
  progress.md の直接編集を deny すると正規の入口や人の緊急編集まで止まる。強制ではなく「途中失敗は復旧手順で
  戻せる」設計にする）。

## 6. 復旧手順（recovery drill）

途中失敗（クラッシュ・電源断・同時更新・手編集ミス）からの復旧は次の順。検出→復旧→検査を `selftest` 両系
（sh / ps1）と `python tools/gate_status.py --selftest` が機械化している（壊れた GATE_STATUS → 検出 → 復旧 →
検査が通る）。

1. **検出**: `python tools/gate_status.py check`（exit 1 = 完全性の破れ、WARN = 順序矛盾・往復上限超）。
   `python tools/validate-harness.py` (r) も同じ規則で ERROR / WARN を出し、progress.md を書いた直後は
   `warn-stale-gate` が同じ内容を 1 行で警告する。SessionStart の `inject-progress` は前回の異常終了
   （`abnormal-stop-*.json`）と別セッションの lock を注入する。
2. **GATE_STATUS が壊れている**（キー欠落・語彙外の値・ブロック喪失）: 復旧元を次の優先順で選び
   `python tools/gate_status.py recover --from <元>` を実行する。
   - `log`: 遷移ログの最終記録（このマシンで最後に観測された状態。最も新しい）
   - `git`: `git show HEAD:docs/00-overview/progress.md`（最後にコミットされた状態）
   - `table`: progress.md の人間向けフェーズ表（未着手 / 進行中 / ゲート承認待ち / 完了）
   - `baseline`: 最新の `logs/usage/<sid>.baseline.json`（セッション開始時点のスナップショット）
   復旧後は `check` が exit 0 になることを確認し、復旧の事実を「未確定事項・申し送り」に 1 行残す
   （復旧も遷移ログに `recover:<元>` として残る）。
3. **lock が残っている**（前セッションが異常終了）: 既定では stale（120 分）で自動的に無視される。すぐ消すなら
   `python tools/gate_status.py unlock`（stale でなければ `--force`。別セッションが本当に動いていないことを確認）。
4. **遷移ログが実物とずれている**（フック外の書換・別マシン）: `python tools/gate_status.py reconcile` が最終記録と
   実物を突き合わせて 1 行追記する（次の SessionStart でも自動で同じことが起きる）。
5. **往復上限超**: カウンタを消さない。`/13-converge` か人の判断で原因を潰してから、`gate_status.py set test done`
   （サイクル完了）で 0 に戻る。
6. 人間向けフェーズ表と GATE_STATUS が食い違う: GATE_STATUS が正。表を直す（表から復旧したときは逆）。

## 7. 機械検査の配置（正は 1 か所、鏡は selftest で固定）

| 検査 | どこで | 何を |
|---|---|---|
| 完全性（ERROR）・順序矛盾（WARN）・往復上限（WARN） | `tools/gate_status.py check` / `validate-harness.py` (r)（本体のテンプレ `progress_template.md` と配布先の実物 `progress.md`） | 本書 §1・§2・§3 |
| 同上の警告（書込直後・非ブロッキング） | `warn-stale-gate.sh/.ps1`（progress.md 自身の編集） | bash / PowerShell の鏡。selftest 両系 |
| 遷移ログ・lock 警告・往復上限警告 | `warn-gate-tamper.sh/.ps1`（PostToolUse） | §4・§5・§3 |
| lock の取得・突合 | `inject-progress.sh/.ps1`（SessionStart） | §5・§4 |
| 語彙の鏡 | `validate-harness.py` (r-2) | GATE_STATUS を直接パースするフック・ツールに 5 キーの語が揃っているか、語彙検査側に 4 値が揃っているか |
| 復旧ドリル | `selftest.sh` / `selftest.ps1` / `gate_status.py --selftest` | 壊す → 検出 → 復旧 → 検査が通る |
