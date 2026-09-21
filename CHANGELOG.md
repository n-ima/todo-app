# Changelog

このハーネス自体の変更履歴。書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に従う。

規約:

- **版数は `plugin.json` の `version` が正**。本ファイルはその要約であり、版数を
  変えるときは必ず両方を更新する。
- **D番号は `DECISIONS.md` の決定単位**。各変更の詳細・根拠・捨てた選択肢は
  対応する D 記録を参照。
- 版付けは [Semantic Versioning](https://semver.org/lang/ja/) に従う。
  **1.0.0 は Copilot 経路の E2E 実証後**に付与する（Antigravity は D057 で凍結・対象外）
  （Claude Code 経路は D049 で実証済み）。

## [Unreleased]

- 2026-09-21: annotated tag 5 本（v0.1.0 / v0.9.0 / v1.0.0 / v1.1.0 / v1.2.0）を付与し push。validate --ledger-strict が導入以来初めて ERROR 0 / WARN 0（A7-M-1 applied）。
- **Copilot CLI 1.0.86 の実機確認（D097。COPILOT-E2E §2 の初回機械実施）で見つかった 4 点を修正**（fe81c93 / 4fabe43）: (1) Copilot CLI は `.github/hooks/**/*.json` をすべてフック設定として読むため、`plugin-hooks.json` を `.claude-plugin/hooks.json` へ、`_privacy-patterns.json` を `.github/harness/privacy-patterns.json` へ移動（旧パスの残存は validate (k-3) が ERROR）。(2) host 判定は環境変数 `COPILOT_CLI` を最優先（Copilot CLI は Claude 互換の snake_case ペイロードを渡す）。(3) Copilot の書込ツールの項目名 `file_text` / `new_str` / `old_str` を共通ライブラリと証拠ゲートに追加（これが無いと証拠なしの `[x]` が素通りした）。(4) `sync-harness` は旧生成物 2 件だけ `--apply` で掃除する。修正後の実機: 7 項目すべて設計どおり（deny 5・ask 2）、フックエラー 0。selftest sh 404 / ps1 376。

## [1.2.0] - 2026-09-21

第 2 版の監査（2026-08-31・codex・2026-09-09）で出た指摘の実装を 3 波に分けて取り込んだ版（D072〜D096）。
配布先での動作は 2026-09-21 に Claude Code 2.1.278 の実セッション（ChronoLines）で受領書・判定ログ・状態機械の記録まで確認。
Copilot 側の実機確認は 1.1.0（2026-08-30）のまま。

- **CI が初めて全ジョブ緑**（2026-09-10、run 34406967789 = 6d7b834。2026-08-11 の導入以来 30 回連続失敗していた）。D072〜D080 に run URL を記録し、台帳の実装完了行を applied へ（3 点規則）。

統合時点（2026-09-10）のローカル検証: validate ERROR 0 / WARN 0（`--ledger-strict` ERROR 0）、
selftest sh 141 / ps1 114（件数は静的計数。pass/fail の正は各 selftest の実行結果）。
CI 緑の run URL は DECISIONS の各 D 記録に記入するまで未確認（D079 の規範により
本ファイルにも「全PASS」「検証済み」は書かない）。

第2波の統合（2026-09-14、D081〜D084）時点のローカル検証: validate `--ledger-strict` ERROR 0 / WARN 0、
selftest sh 247 / ps1 220（静的計数と実行件数が一致。フックは第2波で無変更）、gen-docs --check 差分 0
（10 ファイル 24 ブロック）、generate-adapters --check 差分 0（model-policy 38 / 入口スキル 18 / plugin
マニフェスト 2）、各 `--selftest` 24 本と py3.11 compileall・CI validate-windows 相当が exit 0。第2波分（統合 A）の
CI 緑 run URL: run 35160441445（bce39f0、2026-09-17。validate / validate-windows とも成功。台帳の A3-4b / A3-5b / A5-7 /
A7-A1-1〜4 / A7-M-4 と親 A3-4 / A3-5 を applied へ）。

第2波の統合 B（2026-09-17、D085〜D086: w2-hooks 0c2c220・w2-doctor 39da2da）時点のローカル検証: validate `--ledger-strict` ERROR 0 / WARN 1（開発機 claude 2.1.201 < min 2.1.217 の validate (n)。CI では INFO）、
selftest sh 267 / ps1 239（静的計数と実行件数が一致）、gen-docs --check 差分 0（11 ファイル）、generate-adapters --check 差分 0
（38 / 18 / 2）、python `--selftest` 26 本と py3.11 compileall が exit 0、doctor（開発機）pass 9 / fail 1（claude 2.1.201 < 2.1.217）。
統合 B 分の CI 緑 run URL: run 35162036724（be7f62d。最初の run 35161576449 は doctor selftest の trust ケースが ubuntu で
FAIL → norm_path 修正）。台帳 A6-20 / A3-3 を applied へ。本体判定修正 7a7027e の CI 緑 run 35162514385 で A7-H-9 / A6-15 も applied。

第2波の統合 C（2026-09-17、D087: w2-supply 38c956f〜c55df87）時点のローカル検証: validate `--ledger-strict` ERROR 0 / WARN 2（開発機 claude 2.1.201 < min 2.1.217 の (n)、CHANGELOG 4 版に git tag が無い (p)＝人間の作業として WARN）、selftest sh 326 / ps1 298（静的計数と実行件数が一致）、gen-docs --check 差分 0（11 ファイル）、generate-adapters --check 差分 0（38 / 18 / 2）、sync-harness / doctor / intake-app / model_policy / plugin_manifests / copilot_entry_skills / platform_requirements / _log / session-receipt の `--selftest` と py3.11 compileall が exit 0、全 .ps1 16 本の固定ペイロード実行はクラッシュ 0。統合 C 分の CI 緑 run URL: run 35165448173（c313540）。台帳 IA-20260831-01/02/04/08 / R-02 / R-07 / A2-4b / A2-4 を applied へ。

第3波の統合 D-1（2026-09-21、D088〜D093: w3-state 0ad386c〜1e18357 を 0cd5ede、w3-privacy 1c6719b〜c2ef3db を 35c8029、w3-instr2 3022738〜e7a941a を e16ba0a でマージし断片適用 8ac3b13）時点のローカル検証（Windows 11 / Git Bash / PowerShell 5.1 / python 3.12、3.11 は compileall と新規ツールの --selftest）: validate `--ledger-strict` ERROR 0 / WARN 2（開発機 claude 2.1.201 < min 2.1.217 の (n)、CHANGELOG 4 版に git tag が無い (p)＝人間の作業）、selftest sh 396 / ps1 368（静的計数と実行件数が一致。326+16+43+11 / 298+16+43+11）、gen-docs --check 差分 0（11 ファイル）、generate-adapters --check 差分 0（model-policy 38 / 入口スキル 18 / plugin マニフェスト 2 / path-rules 1）、python `--selftest` 32 本（tools 17・hooks 13・evaluation の check 2）と py3.11 compileall・`python -W error -m compileall` が exit 0、全 .ps1 16 本の固定ペイロード実行はクラッシュ 0・不正 JSON 0（json 12 / empty 4）、doctor --no-probe（開発機）pass 9 / warn 0 / fail 1（claude 版）/ info 4、harness-ci.yml の yaml.safe_load OK。フックの実機発火（Claude Code 2.1.201）は未検証。validate の節記号は state (r)(r-2)(r-3)・privacy (y)・instr2 (w)(x)（(s)(t)(u)(v) は w3-hygiene、(z) は w3-measure2 用に空け、両ブランチは未統合）。統合 D-1 分の CI 緑 run URL: run 35558650414（b37f9c8）。台帳 A7-M-3 / R-04 / IA-20260831-15 / A7-H-5 / A7-G-3 / IA-20260831-03 / R-01 / A6-19b / A7-M-8 / A7-H-7 / A5-5 / A6-19 / A6-24 を applied へ。

第3波の統合 D-2（2026-09-21、D094〜D095: w3-measure2 6cce9d4〜8ecd17d を fdd14ff でマージし断片適用 8e965a8）時点のローカル検証（Windows 11 / Git Bash / PowerShell 5.1 / python 3.12、3.11 は compileall と全 --selftest）: validate `--ledger-strict` ERROR 0 / WARN 2（開発機 claude 2.1.201 < min 2.1.217 の (n)、CHANGELOG 4 版に git tag が無い (p)＝人間の作業）、selftest sh 402 / ps1 374（PASS 行の計数と集計行が一致。396+6 / 368+6。第7波の 6 ケースは両系とも同じ期待値）、gen-docs --check 差分 0（11 ファイル）、generate-adapters --check 差分 0（model-policy 38 / 入口スキル 18 / plugin マニフェスト 2 / subagent-adapters 3 / path-rules 1）、python `--selftest` 35 本（tools 19・hooks 14・evaluation の check 2）が 3.12 と 3.11 の両方で exit 0、py3.11 compileall・`python -W error -m compileall` が exit 0、全 .ps1 16 本（フック 14 + 補助ライブラリ 2）の固定ペイロード実行はクラッシュ 0・不正 JSON 0（json 12 / empty 4）、doctor --no-probe（開発機）pass 9 / warn 0 / fail 1（claude 版）/ info 4、harness-ci.yml の yaml.safe_load OK（validate ジョブ 35 ステップ）、validate の負例（.github/agents/task-worker.agent.md への 1 行追記で (z) が ERROR）を確認して復元。判定ログの 3 系統（`_log.py` FIELD_ORDER / `_log.sh` printf / `_log.ps1` 行組立）は 10 欄で一致（validate (k-4) INFO）。フックの実機発火（Claude Code 2.1.201）と duration_ms の実機分布は未検証。validate の節記号は measure2 の (k-4) を (k-3) の直後、サブエージェント本文展開の検査を (z)（measure2 側の (r) を改番。(s)(t)(u)(v) は w3-hygiene 用に空き、未統合）。settings.json の hooks `if` 断片は提示のみで未適用。統合 D-2 分の CI 緑 run URL: run 35561003426（a7de8ac）。台帳 R-09 / A2-7 / A2-1 / A5-6 を applied へ。A5-4 は rejected のまま）。

第3波の統合 D-3（2026-09-21、D096: w3-hygiene a74478f〜26926c6 を c9f9ea8 でマージし断片適用 c5fb326）時点のローカル検証（Windows 11 / Git Bash / PowerShell 5.1 / python 3.12、3.11 は compileall と全 --selftest）: validate `--ledger-strict` ERROR 0 / WARN 2（開発機 claude 2.1.201 < min 2.1.217 の (n)、CHANGELOG 4 版に git tag が無い (p)＝人間の作業。hygiene の WARN→ERROR 昇格（(b) 本体の 3 面照合・(k)/(k-2)/(k-3) の配線・アダプタ description）は main の現状で新しい ERROR を出さない。INFO: (s) ポインタ型アダプタ 58 本が生成結果と一致、(t) builtin-dependencies.json 依存 9 件、(u) release-tag versions 4 / ok 0 / missing 4 / unresolved 0、(v) harness-ci.yml の版固定 2.1.267 = verified_on_ci）、validate の負例（`.claude/skills/converge/SKILL.md` のポインタに 1 行追記で (s) が ERROR 1「STALE」）を確認して復元、selftest sh 402 / ps1 374（PASS 行の計数と集計行が一致、FAIL 0 / SKIP 0。本波でシェルケースは増えていない＝D-2 と同数）、gen-docs --check 差分 0（12 ファイル＝DECISIONS.md の `decisions-index` を含む）、generate-adapters --check 差分 0（model-policy 38 / 入口スキル 18 / plugin マニフェスト 2 / subagent-adapters 3 / path-rules 1 / pointer-adapters 58＝.claude/commands 18・.agents/workflows 18・.claude/skills 22）、python `--selftest` 38 本（tools 22・hooks 14・evaluation の check 2。freshness-lint / release-tag / host-canary を含む）が 3.12 と 3.11 の両方で exit 0、py3.11 compileall・`python -W error -m compileall` が exit 0、全 .ps1 フック 14 本（selftest.ps1 と補助ライブラリ _log.ps1 / _paths.ps1 を除く）に CI と同じ固定ペイロード `{"tool_input":{"command":"git status","file_path":"src/app.ts","content":"hello"}}` を stdin 投入 → クラッシュ 0・不正 JSON 0（json 12 / empty 2）、doctor --no-probe（開発機）pass 9 / warn 0 / fail 1（claude 版）/ info 4 / skip 0、実走 freshness-lint WARN 0 / INFO 4（67 ファイル、as_of 2026-09-21、git 日付 yes）、host-canary（開発機 2.1.201）pass 8 / warn 1（host-version 2.1.201 < verified_on_ci 2.1.267）/ info 4 / skip 0・ci-pin 2.1.267 = verified_on_ci（exit 0）、release-tag --check versions 4 / ok 0 / missing 4 / mismatch 0 / lightweight 0 / unresolved 0（1.1.0→9629387 / 1.0.0→23baaae / 0.9.0→3672c83 / 0.1.0→0364686。tag 付与は人の作業。exit 0）、eval-report --report --check OK（手動 E2E 節を含む）、harness-ci.yml の yaml.safe_load OK（validate ジョブ 39 ステップ、全ステップ名に「: 」なし）。validate の節記号は hygiene の (s)(t)(u)(v) をそのまま（main の (o)(p)(q)(r)(r-2)(r-3)(y)(w)(x)(z) の後ろ）。未検証: CI（claude 2.1.267）での host-canary の実走と `--init-only` の cli-help 表示、フックの実機発火（Claude Code 2.1.201）、CI 緑 run URL（push は行わない）。
統合 D-3 分の CI 緑 run URL: run 35563449376（305c274）。台帳 A3-6 / A5-8 / A7-M-6 / R-06 を applied へ。

### Added

- 衛生・鮮度・カナリア（第3波 w3-hygiene a74478f〜26926c6 を c9f9ea8 でマージ、断片適用 c5fb326。D096。A7-M-6 / A7-M-1 / R-06 / A5-8 / A3-6）: `tools/freshness-lint.py`（説明文書の「最終更新（D0NN）」「時点」「version」「D0NN まで」と git 最終変更日・plugin.json・DECISIONS 最大 D 番号・基準日の乖離を WARN。--fix なし。履歴文書はヘッダのみ）、`tools/release-tag.py`（CHANGELOG の版・日付と plugin.json の version 導入コミットから annotated tag のコマンド列を出す。実行は人。shallow clone は unresolved）、`tools/host-canary.py` と `.github/harness/builtin-dependencies.json`（D070 で委譲した /security-review・security-guidance・Action、D071/D068/D069 の対照、CI の CLI 前提 9 件を cli-help / plugin-list / bundle-string / file-exists で確認。WARN/INFO のみ。`platform-requirements.json` の `verified_on_ci`=2.1.267 と `claude --version` の差で Day-0 儀式を案内）、PLATFORM.md「衛生・鮮度の検査」「新モデル / 新ホスト版の Day-0 再ベンチ儀式」、gen-docs の `decisions-index`（DECISIONS.md の索引。任意ブロック）、`generate-adapters.py --check` の第1・3節（ポインタ型アダプタ 58 本＝.claude/commands 18・.agents/workflows 18・.claude/skills 22。.claude/agents は D095 の本文展開で validate (z) が見る）の本文ドリフト検査と `--check-adapters`、手動 E2E（Copilot 経路）の機械可読記録 `evaluation/manual/copilot-e2e_template.json` → `evaluation/results/manual/*.json` → REPORT.md「手動 E2E」節（2026-08-30 分を転記）、intake-app の未追跡件数警告。
- **第3波 measure2（フック計測の継続化と残りの評価項目。R-09 / A2-7 / A2-1 / A5-4 / A5-6 / CC-6。D094〜D095。w3-measure2 6cce9d4 / merge 8ecd17d を fdd14ff でマージ、断片適用 8e965a8）**:
  - 判定ログ `hook-decisions.jsonl` に `duration_ms`（スクリプト内の経過。プロセス起動分は含まない）と `host`（claude-code / copilot / unknown の近似）を末尾 2 欄で追加（`_log.sh` / `_log.ps1` / `_log.py`。旧行は欠落 null で読める後方互換。validate (k-4) が 3 系統の欄順一致を検査。R-09 / A2-7）
  - `tools/hook-metrics.py` 新設: script 別 P50/P95・decision 分布・host 別発火数・false ask/deny 近似を集計し `tools/usage-config.json` の `hook_slo` 超過を WARN（`--strict` / `--json` / `--kpi-line`）。受領書の hooks 欄と `effort-report --kpi` に同じ 1 行（R-09 / A2-7）
  - 事故的停止の記録を目標達成評価に接続（A2-1）: `watchdog-continue.py` が上限到達・max_tokens で `in_progress` のまま止まると `logs/watchdog-stop-<sid>.json` を記録し、`_log.py` の `iter_stop_records` が `abnormal-stop-*`（StopFailure）と共通形で読む。`golden-eval.py` は「事故的停止で終わったセッション中の done 宣言」を達成扱いにしない（NG）、`e2e-run.py` は verdict `DNF_ABNORMAL_STOP` を追加
  - `.claude/agents/*.md` を `.github/agents/*.agent.md` の本文展開に変更（`tools/subagent_adapters.py`。generate-adapters 第2節・`--check`・validate (z) の鮮度検査。A5-6）。プランモードは採らない（PLATFORM 委譲表。A5-4）。hooks `if` は公式確認のうえ guard 系には適用せず、docs 限定の PostToolUse 警告 3 本への候補断片のみ提示（`platform-requirements.json` features.hooks_if。CC-6）
  - selftest.sh +6（第7波）、selftest.ps1 +6、`watchdog-continue.py --selftest` 追加
- プライバシー境界と供給網・書込経路の残り（第3波 w3-privacy 1c6719b / merge c2ef3db。D088）: (1) `draft-learnings.py`（Stop）は `docs/00-overview/learnings-pending.md` を書かず、`.github/hooks/logs/learnings-draft.local.md`（gitignore 済み＝local 欄）に `_privacy-patterns.json` の redaction を通した先頭 60 字だけを残し、`tools/usage-config.json` の `learnings_draft_rotate_days`（30）/ `learnings_draft_excerpt_chars`（60。0 で抜粋なし）で回転（IA-20260831-03 / codex H-01）。`learnings.md` への転記は人か `/10-retrospective`（harness-retrospective スキル）の明示操作だけ。(2) 外部 Skill / MCP のロック `.github/harness/external-lock.json`（awesome-copilot security-review: revision 7e375ea・MIT・local patch のダイジェスト、`@playwright/mcp` 0.0.80: dist.integrity・Apache-2.0）と `tools/external_lock.py`（指示層の外部参照を走査し、未登録・浮動版・版違い・版なしは ERROR、local digest のずれは WARN。`--refresh` / `--selftest`）、validate (y)（IA-20260831-11 / codex H-09）。ロックは guard sh/ps1 の保護対象（deny・CROSS_ITEMS は統合コミット）。(3) R-01 の信頼境界を PLATFORM.md「セキュリティの層構造」に明文化（第 1 防衛線＝フック、第 2＝ConfigChange、境界＝OS sandbox / read-only mount / branch protection＋CODEOWNERS required review＝IA-05 は人が GitHub 設定で有効化）。`guard-harness-config-edit` sh/ps1 に reparse point 作成（`ln -s` / `mklink` / `New-Item -ItemType SymbolicLink|Junction|HardLink` / `CreateSymbolicLink` / `os.symlink` / `fs.symlink`）の ask（保護パス不在でも）と git plumbing の専用タグ、`guard-dangerous-git` sh/ps1 に discard-all 形（`git checkout -- .` / `git restore .` / `:/` 等）の ask。(4) skills 配下の権限系 frontmatter は公式仕様（Claude Code skills / VS Code agent-skills / agentskills.io）から列挙したキー集合（allowed-tools / tools / permissions / permission-mode / hooks / context / agent / shell / mcp / mcp-servers、disable-model-invocation、user-invocable、disallowed-tools）で「拡張だけ ask、縮小・同値・既定値の明示は allow」（ディスク上の現ファイルを旧内容、`old_string` を置換元に比較。`_paths.sh` の 1 回解析 API に 10 欄目 `old_text` = `collect_old_text`、`_paths.ps1` に `Get-OldText`）。`guard-config-change.py` も同じ規則 `privileged_expansion` で HEAD 比較 block（A7-G-3 / SC-2）。selftest sh 369 / ps1 341（静的計数。pass/fail の正は各 selftest の実行結果）。
- 状態機械の堅牢化（第3波 w3-state 0ad386c〜1e18357。D089）: フェーズゲートの状態機械の仕様を `.github/harness/STATE-MACHINE.md` に一本化（5 キー固定・4 語彙・値は行の最初のトークン、`in_progress` は入口の最初のステップ／`pending_approval` はフェーズの最終ステップ／`done` は人の承認後、巻き戻しは `/12`・`/13` 経由だけ、13-converge→06 の遷移、implement↔test の往復上限＝`GATE_COUNTERS` の `implement_test_loops` と `tools/usage-config.json` の `implement_test_loop_max`（既定 3）、遷移ログ・session.lock・復旧手順）。`tools/gate_status.py`（check / set＝一時ファイル→rename の原子的書換＋遷移ログ・`done` は `--evidence` 必須 / bump-loop / recover --from log|git|table|baseline / reconcile / unlock / --selftest）、`validate-harness.py` (r)（本体のテンプレと配布先の実物の完全性 ERROR・順序矛盾 WARN・語彙の鏡）、`warn-stale-gate` の完全性・順序矛盾の警告（注記つき `done 2026-08-01` の sh 全損も修正）、`warn-gate-tamper` の遷移ログ 1 行（`logs/gate-transitions.jsonl`。rev 通番・session_id・前→後・loops）と別セッション lock・往復上限の警告、`inject-progress` の session.lock（stale 120 分）と遷移ログの突合、`guard-phase-scope` の deny 文言を D067 に揃える（A7-H-5）。既存 progress.md は GATE_COUNTERS 無しを 0 扱い（後方互換。3 実プロジェクトの check は完全）。selftest sh 342 / ps1 314（静的計数。pass/fail の正は各 selftest の実行結果）。
- **第3波 instr2（指示層棚卸しの残りとコンテキスト経済。A6-19b (1)(3)(4) / A7-M-8 / A7-H-7。D090〜D093）**: Stop フック `remind-session-boundary.py`（D047 の 2 行様式を `last_assistant_message` で機械検査し warn のみ。陽性 2 種＝案内の後ろの現セッション向け指示 / push・tag が ask 未承認のまま完了宣言）、`check-doc-chars` に ZWSP / NBSP / 双方向制御文字（U+202A〜202E・U+2066〜2069）、`inject-progress` の注入上限 `inject_progress_max_chars`（usage-config.json。env `HARNESS_INJECT_MAX_CHARS` 優先）、パス限定ルール `.claude/rules/`（`.github/instructions/*.instructions.md` の applyTo → paths の生成物。generate-adapters 第7節 `tools/path_rules.py`、validate (x)）、ハーネス名の正を `plugin.json` の `name`（copilot-sdlc-harness）に一本化（validate (w)）、validate (i) の常駐実測に `include`（@AGENTS.md の展開）を合算、/07 /09 の prompt を薄い参照形に・/11 を `change` にバインド、逸話 10 箇所の D 番号化。ローカル検証（2026-09-17、統合 C 取り込み後）: validate `--ledger-strict` ERROR 0 / WARN 2（開発機 claude 版・git tag 未作成＝main と同じ）、selftest sh 337 / ps1 309（静的計数と実行件数が一致）、gen-docs --check 差分 0、generate-adapters --check 差分 0（4 節）。CI 緑 run URL は未記入
- 供給網・リリース権限・証拠ゲート（第2波 w2-supply。D087）: `release` を「計画→独立検証（`reviewer` の release 検証）→承認→実行」に分離（codex 監査 2026-08-31 C-01 / C-02 = IA-20260831-01 / 02。`release` から `web` を外し `reviewer` を invokes に、`release-checklist.md` に外部反映の action packet（操作・対象・引数・コミット SHA・期限・承認・実行結果）、environment_template に「自動＝準備まで。外部反映は分類に関わらず exact-action 承認」の 1 段落、PLATFORM.md「リリースの権限分離（Rule of Two）」、skill-authoring に fresh-session 導入審査）。新規 `guard-external-effect.sh/.ps1`（IaC の apply / destroy・kubectl / helm・クラウド / PaaS CLI・コンテナレジストリ push・パッケージ公開・gh / glab の書込・HTTP の書込メソッド・ssh / scp / rsync・本番マーカー付き migrate・MCP の書込系ツール名を「何を・どこへ」を示す ask。plan / dry-run / 読み取り / ループバック宛は allow。承認バイパス下＝payload の `permission_mode` が bypass 系、または `HARNESS_EXTERNAL_EFFECT_MODE=deny` は deny。49 セグメント以上は評価前に ask）、`guard-dangerous-git` の理由文に「何を」とバイパス deny。新規 `guard-done-evidence.sh/.ps1`（progress.md の done 遷移 / tasks.md の `[x]` 追加を含む書込の新内容に証拠 3 点セット＝コマンド・出力要約・YYYY-MM-DD HH:MM が無ければ deny。人間の直接編集は対象外。A2-4b）。`@playwright/mcp@latest` → `0.0.80`（dist.integrity 記録）と validate (o) 浮動参照検査（IA-20260831-04）、harness-ci.yml / .example の harden（40 桁 SHA・`permissions: contents: read`・concurrency・timeout-minutes・persist-credentials: false）と validate (q)（IA-20260831-08）、validate (p) リリースの不変性（plugin.json の版が CHANGELOG 見出しに無ければ ERROR、annotated tag 無しは WARN）と sync-harness の `source_url` / `source_commit[-dirty]` / `archive_sha256` 記録・`--verify`（0 / 1 / 2）・`--allow-dirty`・未追跡ファイルの非配布（IA-20260831-09 / A7-M-1）、SECURITY.md にサポート版・応答の目安・公開方針・対象外・謝辞・外部依存の固定・リリースの不変性（R-07 / IA-04 / IA-09）、R-02 の selftest（壊れた JSON + 保護対象パス）。selftest sh 326 / ps1 298（静的計数。pass/fail の正は各 selftest の実行結果）。
- フック基盤の共通化（第2波 w2-hooks 72e537e。D085）: 判定ログを 1 行 JSONL `logs/hook-decisions.jsonl` に一本化（`_log.sh/.ps1/.py`・redaction と欄分類の正 `_privacy-patterns.json`。旧 4 列 TSV は書かず、読み手 `_log.py` だけが両形式を読む）、`_paths.sh` の 1 回解析 API `parse_hook_input`（guard-phase-scope の複製を共通関数に差し替え。Windows 実測 5 回平均で phase-scope 1,172→491ms・harness-config-edit(Bash) 1,328→651ms・template-edit 681→410ms・gate-tamper 1,000→634ms）、python フック 6 本の stdin UTF-8 明示復号、done 契約の `model_mismatch` 警告（warn-gate-tamper。D077 三層強制の done 契約側）、SubagentStop の verdict 強制 `guard-subagent-output.py`（判定役の重大度トークン欠落を有界 2 回まで block。配線は settings.json）、PreModelSwitch の `--mode ask` / `model_switch_policy`（既定 log は挙動不変）、受領書の hooks 欄と review-log 日時付きエントリ計数、validate (k-3)。selftest sh 262 / ps1 234（静的計数。pass/fail の正は各 selftest の実行結果）。
- doctor（A3-3 / A6-24 / A6-8 / A6-15、D086）: `python tools/doctor.py` が Claude Code の版（`.github/harness/platform-requirements.json` の min 2.1.217 未満は FAIL / required 2.1.257 未満は WARN）、python / PyYAML / git / Git Bash（WSL の bash が先に解決されると FAIL）/ node / jq、`.claude/settings.json` の配線とインタプリタ解決、`.sh` の LF と `.ps1` の BOM、statusLine のモック実行テスト、ワークスペース信頼の承認記録、配布鮮度（`docs/00-overview/harness-origin.md` の `latest_decision` と本体 DECISIONS.md の最新 D 番号の差）、`logs/usage` の受領書、Windows の 8.3 生成状態を pass / warn / fail の表（ASCII 主体）で出す。`--json` / `--strict` / `--no-probe` / `--write-origin [--harness <本体>] [--force]` / `--selftest`。環境側の検査の正はここ、構造側は従来どおり `tools/validate-harness.py`。
- ホスト版要求の機械可読な正 `.github/harness/platform-requirements.json`（読込・比較・描画は `tools/platform_requirements.py`、標準ライブラリのみ）: PLATFORM.md「最低安全バージョン表」を `gen-docs.py` の生成ブロック `platform-requirements` に、`tools/e2e-run.py` の起動門定数 5 つを JSON 読みに、`tools/validate-harness.py` (n) が JSON の構造・自己整合、model-policy.yml `min_version` の regex 読みと yaml の一致、`tools/usage-config.json` の `host_min_version` との一致、`claude --version`（最低版未満は WARN。CI の claude 不在は INFO）を検査。正の分担（1 行）: ホストに要求する版とホスト機能の導入版は JSON、モデル関連機能の導入版は model-policy.yml `min_version`（JSON は名前で参照するだけ）。
- 配布鮮度（再監査 2026-09-09 RD-4 / A6-15）: `tools/sync-harness.py` / `tools/intake-app.py` の `--apply` と `doctor --write-origin` が同一書式の `docs/00-overview/harness-origin.md`（`path` / `version` / `synced` に加え `source_url` / `source_commit` / `archive_sha256` / `synced_at` / `latest_decision` / `route`。フィールド名は w2-supply と共通）を書く。GitHub テンプレート / ZIP 経路は `/00-start-project` の初期化で `doctor --write-origin` を案内。`inject-progress.sh/.ps1` は SessionStart で本体（`path:` の DECISIONS.md、無ければ CHANGELOG.md）より `HARNESS_STALE_GENERATIONS`（既定 1）世代以上古ければ「/91-sync-from-harness を先に実行」を 1 行注入（本体自身・到達不能・D 番号不明は注入しない＝fail-open。selftest 両系に 5 ケースずつ）。
- 作業ノートパッド（D065、A3-10）: `docs/00-overview/notepad.md` に書かれた未確定の
  途中メモを inject-progress が SessionStart/PreCompact の両方で再注入（先頭2KB、
  UTF-8 バイト基準で sh/ps1 統一）。コンテキスト圧縮・セッション切替で途中状態が
  失われる穴を塞ぐ
- 独立レビューを done 条件へ（D073、A6-14 / RD-2）: `implement` が完了タスク10個ごとの
  チェックポイントと全タスク完了時に `reviewer` を起動し、結果を `docs/04-test/review-log.md`
  （新テンプレ `review_log_template.md`。日時・フェーズ・対象・verdict・根拠 file:line）に記録。
  記録の無い implementation / test の done は `warn-gate-tamper`（sh/ps1）が
  「独立レビューの記録がありません」を警告し、`golden-eval` が WARN する。`reviewer` に記録の
  出力契約と証拠拘束文（読んでいない file:line を書かない。PF-11）を追加。gate-check の
  done 条件・fast-track/change-request の手順・README/USAGE/PLATFORM/HTML 鏡・AGENTS.md を整合
- hooks: 未配線だった Claude Code イベントの記録フック 3 本を追加（D074、再監査 CC-9 / CC-8）。
  `mark-abnormal-stop.py`（StopFailure: 停止理由と GATE_STATUS スナップショット・HEAD sha を
  `logs/abnormal-stop-<session_id>.json` に保存。次回 SessionStart で `inject-progress`（sh/ps1）が
  24 時間以内の記録から「前回は異常終了」を注入。PreCompact では注入しない）、
  `log-instructions-loaded.py`（InstructionsLoaded `session_start|compact`: 常駐指示のロード行数・
  バイト数を `logs/instructions-loaded.jsonl` に記録。`validate-harness.py` (i) が直近セッションの
  総ロード行数を INFO 表示し 200 行超で WARN＝A5-5 の実測経路）、`log-model-switch.py`
  （PreModelSwitch/PostModelSwitch: from/to を `logs/model-switch.jsonl` に記録。block しない）。
  3 本とも `--selftest` を持ち、selftest 両系に起動テスト・記録テスト・異常終了注入の回帰を追加
- 計測と会話ごとの受領書（D075 / D076、再監査 §5 / A6-6・A6-7・A6-12・A6-13）:
  `tools/session-receipt.py`（受領書の集計・レンダリング・トリガ判定の唯一の正。累計トークンは
  transcript（main/サブエージェント別・解析率付き）、host 推定 $・文脈%・行数は statusline JSON のみ、
  tests の pass は golden-eval / check.py の決定論検証のみ、review_less_done は同フェーズ累計）、
  `tools/prices.json`（as_of 2026-09-09。fable-5-1 cache read 0.025x・sonnet-5 $2/$10・最長前方一致・
  family fallback。list 価格の推定で請求額ではない）と `tools/usage-config.json`（閾値の唯一の正。
  context_warn_pct=40 は USAGE.md 分割表と同一値で文言は gen-docs 生成）、フック `log-effort.py` v2
  （Stop=受領書 draft・区切りのときだけ systemMessage 3 行・block なし / SessionEnd=確定 rename）、
  `statusline.py`（表示+永続化。`.claude/settings.json` の `statusLine` に既定配線。
  `settings.local.json` で上書き可・`--chain` で既存表示を連結。ps1 鏡なし）、`log-subagent.py`
  （SubagentStop / PostToolUse(`Agent|Task`)・tool_name 自己フィルタ）、`session-baseline.py`
  （SessionStart startup/clear/resume/fork のみ・上書きしない）、`harness-stats` スキル
  （user-invocable / disable-model-invocation）、`tools/effort-report.py` v2（logs/usage の受領書を
  集計、`docs/06-retrospective/baselines.json` 生成、`--migrate` で旧 CSV 取込、`--snapshot` 月次
  スナップショット、匿名化既定。実 3 プロジェクトの CSV（一時コピー）で 11 セッション $300.74・
  p50 $9.65・p90 $60.71・max $121.13＝監査 RD-5 と一致）
- 役割別モデル/effort 方針の単一ソース（D077、A6-10 / A6-3 / A3-8。監査 2026-09-09 §4.5）:
  `.github/harness/model-policy.yml` v1 を正とし、`.claude/agents` の frontmatter
  （model / effort / background）・`.github/agents` の `model:` 行・`.github/copilot/settings.json`・
  `guard-subagent-model` の役割表・PLATFORM / README / agents.html の対照表を
  `tools/generate-adapters.py`（第4節 = `tools/model_policy.py`）から生成。`--check` /
  `--print-deny` / `--apply-deny`（保守モード中は拒否）/ `--print-prices`。既定は全役割 `inherit`、
  判定役（reviewer / spec-critic）と上流（requirements / design）は effort high + never_low、
  haiku-4.5 は候補外。A/B 前は挙動不変。PreToolUse（matcher `Agent|Task`）
  `guard-subagent-model.{sh,ps1}`: 方針外の明示 model を deny、省略時は既定 inherit の役割は素通し、
  具体既定の役割のみ updatedInput で model だけ差替。fail-open。selftest 両系に 14 ケース。
  validate-harness (j) model-policy 整合（許容値集合・`model: auto` の全文 grep・プラン行列・
  廃止 30 日前・親≧子ティア・min_version・役割集合・生成物鮮度・deny の一致・保護 5 面。
  WARN 既定、`--model-policy-strict` で ERROR）
- 評価装置 v2（D078、A6-16 / A6-9 / A6-8 / A3-1 / A3-2）: `tools/e2e-run.py` を schema v2 化
  （verdict 9 種・`valid_for_comparison`・`invalid_reasons[]`・固定条件 C（`--model`/`--effort` 必須・
  CLI ≥ 2.1.251 の起動門・系列専用 `CLAUDE_CONFIG_DIR`・condition_hash）・督促の対称化・
  workdir/transcript の退避）、`tools/eval-report.py` 新設（装置 v1 結果 10 本の backfill・妥当性検査・
  `evaluation/REPORT.md` 生成・Fisher/Wilson）、expense-webapp `check.py` の `contradiction_resolved` を
  「同一文内に決定動詞+単一の締め日」に強化（負例 7/正例 6）、`evaluation/arms/*.json`・
  `evaluation/experiments/S1〜S5`（事前登録 draft。n=5/アーム・Fisher 両側）、配布除外の三重台帳
  （.gitattributes / sync EXCLUDE_FILES / intake TEMPLATE_EXCLUDE_REL）の三者一致を selftest で検査。
  GLOSSARY に verdict 等の定義、CONTRIBUTING に selftest 4 本
- `.claude/settings.json`（統合。D074/D075/D077）: `statusLine`（statusline.py）、
  PreToolUse `Agent|Task` → guard-subagent-model、PostToolUse `Agent|Task` と SubagentStop →
  log-subagent.py、SessionStart に session-baseline.py、SessionEnd の log-effort timeout 30→10、
  deny に `.github/harness/model-policy.yml` / `.github/copilot/**` の Edit/Write と
  `Agent(model:haiku)` / `Agent(model:claude-haiku-4-5)`。CC-6 の hooks `if` は提案のみ（未適用）
- CI（harness-ci.yml の validate ジョブ）: model-policy の生成物検査と `model_policy.py --selftest`、
  受領書・計測ツール 5 本の selftest、Stop フック（remind-record / draft-learnings）と記録フック 3 本の
  selftest、評価装置 v2 の 5 ステップ（e2e-run / check.py ×2 / eval-report / REPORT 鮮度）を追加。
  構造検証を `--ledger-strict`（台帳の登録漏れは ERROR）に切替。ステップ名に「: 」を含めない
- Copilot Agent Host 向け入口スキル（A6-17 / 再監査 2026-09-09 CP-2。D081）: Agent Host は
  prompt files を読まないため、`tools/generate-adapters.py` 第5節（`tools/copilot_entry_skills.py`）が
  `.github/prompts/*.prompt.md` + 担当 `.agent.md` から `.github/skills/<nn>-<name>/SKILL.md`
  （user-invocable / disable-model-invocation: false / metadata.harness-entry: copilot。本文は
  役割設定→prompt 本文実行→ハンドオフ読み替えの薄いアダプタ）を生成する（18 本。正は prompt files
  のまま。Claude Code 側の `.claude/skills` ポインタは作らない＝第3節で除外）。`generate-adapters --check` と
  `validate-harness.py` (l) が鮮度・孤児・未生成・ポインタ衝突を検査し、gen-docs のスキル数（手順部品）
  からは除外。PLATFORM「Copilot Agent Host の入口」/ USAGE §0 / COPILOT-E2E 1-4・1-5 / commands.html /
  README / GLOSSARY / skill-authoring / CLAUDE.md / AGENTS.md 受付ルーチン / request-routing を整合。
  CI に `copilot_entry_skills.py --selftest`。Agent Host 実機は未確認
- plugin マニフェストの同一ソース生成（D083、A6-23 / CP-7 / A2-2 / IA-20260831-10 / RG-17）:
  `plugin.json`（Agent Plugins 1.0。版数の正）を正に、`.claude-plugin/plugin.json`（Claude Code plugin
  マニフェスト。skills ./.github/skills/・commands ./.claude/commands/・agents は ./.claude/agents/*.md の
  配列・hooks ./.github/hooks/plugin-hooks.json）と `.github/hooks/plugin-hooks.json`（`.claude/settings.json`
  の hooks を `${CLAUDE_PLUGIN_ROOT}` 相対に写したもの。plugin 経由の配布時のみ）を
  `python tools/generate-adapters.py` 第6節（`tools/plugin_manifests.py`）が生成。`--check` は第4〜6節を
  すべて検査。`validate-harness.py` (m) が plugin.json のスキーマ（$schema・name 必須・許容キーのみ・
  semver・「Antigravity対応」不可=R-08）、`.claude-plugin/plugin.json` の name/version 一致とパスが ./ 始まりで
  実在、生成物の鮮度を検査。`.claude-plugin/` を CODEOWNERS と sync-harness SYNC_GLOBS と
  `.claude/settings.json` の permissions.deny に追加。CI に `plugin_manifests.py --selftest`・
  `claude plugin validate .`（claude-code@2.1.267 を版固定で導入）・`claude --init-only`（continue-on-error・
  timeout 5 分）。README「再利用パッケージとしての配布」、PLATFORM.md「配布経路と plugin マニフェスト」、hooks/README
  「plugin 経由の配布時の配線」、COPILOT-E2E §7（実インストール未実施）を追加
- 鏡の生成物化の完遂(A3-4b / A5-7 / A7-A1-1〜4。D084): `tools/gen-docs.py` の生成対象を 7 ファイル 11 ブロック →
  10 ファイル 24 ブロックへ拡張。agents.html(hero 件数・フェーズ専属表・サブエージェント表)・skills.html(hero 件数・
  カテゴリ別表。`^NN-` の入口スキルは「ハーネス入口」に自動分離)・commands.html(hero 件数・カテゴリ別表)の一覧本文を
  `.github/agents|skills|prompts` の frontmatter から生成、USAGE.md のセッション分割表(`usage-session-table`)、
  COMPARISON.md/html の決定数(`decision-count`。DECISIONS.md の `## D` 静的計数)と A/B 実測表(`comparison-measured`。
  `evaluation/REPORT.md` からの機械転記。比較可能 0 本の間は優位・費用比を生成しない)。`gen-docs.py --selftest` 新設
  （CI に追加）
- `tools/validate-harness.py`（D084）: (c-2) スキル数・コマンド数・エージェント数・決定数・「D001〜D0NN」のハードコード検出、
  (e) PROPOSALS の 登録日 / 状態更新日 列の存在・日付形式・順序検査、(h-2) 常駐指示のトークン推定(AGENTS.md + CLAUDE.md +
  route-request 注入文 + inject-progress 上限。係数 CJK 1.0〜1.3/文字・ASCII 4 文字/トークン、`instructions-loaded.jsonl`
  があれば実測バイトで校正。閾値は `tools/usage-config.json` の `resident_tokens_warn`=20,000 / `resident_tokens_target`=12,000)、
  (h-3) AGENTS.md の由来(D 番号・実失敗)無し規範行の INFO 列挙(A3-5b。強制なし)。stdout/stderr を UTF-8 固定(RG-15。
  log-effort.py の selftest 経路も)
- `audits/PROPOSALS.md` に 登録日 / 状態更新日 の 2 列を追加(OP-4。D084。状態列は不変。初期値は出典日 / 2026-09-10 統合分は
  2026-09-10、状態更新日は git blame)。`tools/effort-report.py` に「ハーネス自己改善 KPI」節(未適用の最古滞留日数・
  監査→適用の中央リードタイム)と `--kpi`（CI に表示のみで追加）

### Changed

- 配布先（実プロジェクト）で validate が誤って赤になる 3 点を修正（D088 追記 1 / D096 追記 1。2026-09-21 の 3 実プロジェクト同期で実測）: (q) Actions harden は本体の harness-ci.yml だけ ERROR で配布先は WARN、(t) builtin-dependencies.json の used_by 実在・言及は本体のみ、(y) 外部ロックの未登録参照は配布先では WARN。`external_lock.py` は `client/dist` 形の相対パスを外部参照と誤認しないよう 2 段目のディレクトリ名も除外。
- `tools/validate-harness.py`（D096。A7-M-6）: (b) 3面照合の欠落を本体で ERROR（配布先は WARN）、(k)/(k-2)/(k-3) の共通ライブラリ配線・8.3 展開・_log source の欠落と、アダプタの description 乖離を ERROR に昇格。新検査 (s) ポインタ型アダプタの本文ドリフト（ERROR）、(t) builtin-dependencies.json の構造と used_by、(u) release-tag --check の INFO、(v) harness-ci.yml の claude 版固定 = verified_on_ci（WARN）。(c)/(c-2)・(f)・(j) は昇格しない（理由は docstring）。
- validate-harness の本体判定を「DECISIONS.md の有無」からフック H-11 / doctor.is_body と同じ規則（DECISIONS.md か USAGE.md があり、かつ requirements/memo.md がテンプレのまま）へ（D086 追記 2）。テンプレート複製由来の古い DECISIONS.md が残る実プロジェクトで gen-docs.py 不在 ERROR・アプリ README のマーカー欠落 ERROR・決定数不一致 WARN が出ていた（3 実プロジェクトの同期で実測）。`model_policy.targets` も配布先では README.md を生成対象から外す。
- doctor: workspace-trust はフラグ false でもフック判定ログがあれば pass（デスクトップアプリは false のまま適用される。D086 追記 3）、`norm_path` の posix での backslash キー照合を修正（D086 追記 1）。
- 3 実プロジェクト（ChronoLines / team-operations-hub / vision-bridge）へ本体 D086 を同期（A6-15。sync --apply、要レビューの手動マージ、effort-log.csv の受領書移行。プロジェクト側は未コミット＝利用者レビュー。D086 追記 4）。
- 配布先で `validate-harness.py` が `gen-docs.py` 不在を ERROR にしていた不整合（A7-H-9 / RG-10）: `tools/gen-docs.py` を `.gitattributes` の export-ignore にも載せて配布除外の三重台帳（.gitattributes / sync EXCLUDE_FILES / intake TEMPLATE_EXCLUDE_REL）を完全一致にし（`LEDGER_KNOWN_EXCEPTIONS` を空に）、validate は DECISIONS.md の無い配布先では gen-docs 検査を対象外（INFO）にする。USAGE.md のテンプレート経路の削除手順に `tools/gen-docs.py tools/e2e-run.py tools/eval-report.py evaluation/` を追加。
- 文書: USAGE.md「導入直後の確認（4 経路共通・1 分）」とトラブルシューティング、harness-sync スキル / `/91` プロンプト（sync 後に doctor）、harness-guide・brownfield-intake スキル、orchestrator の `/00` 初期化案内、hooks README の inject-progress 行、docs/00-overview/README.md（harness-origin.md の説明）、README.md の tools 一覧。
- 台帳規則と品質主張の規範（D079、A6-4 / A6-5 / A6-8 / A6-17 / OP-1 / RG-12 / RG-13 / RG-14 / EV-3 / CP-3）:
  **「全PASS」「検証済み」刻印の除去**（`tools/gen-docs.py` のテンプレから「全PASS」
  「2環境で実機検証済み」を除去し件数のみの中立表記へ。selftest.ps1 の Check 行を数える
  `count_selftest_ps1_cases` を追加し、README / COMPARISON.md / COMPARISON.html / overview.html /
  guardrails.html（×3）/ harness README の手書き件数を生成ブロック `selftest-counts` /
  `hook-count` へ移行（生成対象 3→7 ファイル）。同一ファイル複数ブロックで `--check` が常に STALE に
  なる不具合も修正。python 系ツールの selftest 件数は文書から除去。selftest 両系は 1 ケース
  1 増分行の規約＝統合時に selftest.ps1 の `foreach` 3 か所を展開し静的計数と実行件数を一致）。
  **validate-harness.py**: (c) 件数照合の走査対象に `.github/harness/*.md` と「N件」「sh N / ps1 M」
  「N/M(sh/ps1)」形、(e) 状態語彙に `partial`、(e-2) `audits/*.md` 先頭の `<!-- proposals: … -->`
  宣言 ⊆ PROPOSALS.md の検査（既定 WARN、`--ledger-strict` で ERROR）、(e-3) `.vscode/settings.json`
  の不変条件。**台帳**: 採番規則（1 監査＝1 接頭辞・出典ファイル名から機械決定）・applied 3 点
  （D 番号＋検証コマンド＋CI 緑 run URL）・`partial` 状態を明文化し、A6-1..24 / A7 群 32 件 /
  IA-20260831-01..15 / R-01..09 / 子 ID A2-4b・A3-4b・A3-5b を登録、A2-4/A3-4/A3-5 を partial に
  差し戻し。**評価主張の訂正**（EV-3・§6）: 7 面の「本ハーネス（完走）PASS $45.47」を
  DNF_BUDGET_TOTAL の参考値へ、「2/2 vs 0/2」を参考値（n=2・装置 v1・比較条件不成立）へ、
  「n≥6」を n=5（完全分離で Fisher 両側 p=0.008）へ、費用比（4.9倍／7.7倍／14倍／46%減／29%減）を
  実額表記へ。**PLATFORM.md**: 最低安全版表に 2.1.232 / 2.1.239 / 2.1.251 / 2.1.257 / 2.1.259 と
  StopFailure/InstructionsLoaded の発火下限、要求版 2.1.257 以上。「フックの読込元・実行意味論の
  環境差」表。D058「二重発火なし」に Local ハーネス限定の但し書き。「Task ツール」→
  「Agent ツール（旧称 Task）」。「/import は使わない」。「Stop フックの限界（CC-14）」。
  **Copilot Agent Host**: `.vscode/settings.json` に `chat.hookFilesLocations`（`.github/hooks` のみ）と
  `chat.useClaudeMdFile: false`。README / plugin.json / CLAUDE.md に「prompt files は Agent Host では
  読み込まれない」の注記。**GLOSSARY.md**: 「Agent ツール」「Agent Host / Local ハーネス」
  「ConfigChange」「基準線」。**request-routing スキル**（CC-5）: TodoWrite 前提を一般化
- モデル方針の supersede（D077）: D007（Copilot `model: auto`）と D040 決定5（task-worker の
  Sonnet 化・旧解決順・旧単価）を supersede。`model: auto` を全面から除去（.agent.md 5 本の
  frontmatter・implement / test 本文・USAGE・overview・README・COMPARISON・agents.html・
  harness-guide・AGENTS.md）。harness-guide の解決順を v2.1.251（呼出時 > frontmatter >
  `CLAUDE_CODE_SUBAGENT_MODEL` > メイン）と `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` に訂正。
  PLATFORM.md に「モデル/effort の方針」節（役割表・優先順位表・劣化モード・三層強制・最低版・
  単価表。生成ブロック）を新設し「deny が守るのは Edit/Write のみ」を訂正（CLAUDE.md も同旨）。
  保護 5 面に `.github/harness/model-policy.yml`（sync は REVIEW_FILES）と `.github/copilot/**` を追加
  （guard-harness-config-edit sh/ps1・CROSS_ITEMS・CODEOWNERS・SYNC_GLOBS・.gitignore）
- 計測の一次データ（D075）: `.github/hooks/logs/usage/`（gitignore・90 日ローテーション）に一本化。
  `docs/00-overview/effort-log.csv`（D040）は廃止し移行期間中 ignore（RC-4）。PLATFORM.md
  「コスト計測の詳細」を statusline 経路・出所タグ・ホスト別可用性行列・劣化モードに書き換え。
  `/99-status` は progress.md を作成しない（A7-H-6）。費用欄を追加（受領書 → 無ければ effort-report）。
  AGENTS.md のコスト節は方針参照 1 項目と受領書の 1 行に置換（24,334B）
- 評価の記録・報告規範（D078）: 除外は「装置欠陥」か「事前登録条件」のみ、results/ は削除しない、
  費用は範囲で報告し比を作らない。evaluation/README を装置 v2 に全面改訂（旧「k=3 推奨」
  「n≥6」を撤回）。USAGE.md にテンプレート経路の必須手順（クローン直後に DECISIONS.md / audits/
  等を削除）と本体判定の機械条件（D072）、statusline の上書き方法を追記
- reviewer は維持を実測で確定（D071、A5-9）: ビルトイン /code-review との頭合わせで
  一般バグは同等（4-5/5）だが、done契約照合（tasks.md の完了証拠と実体の突合）は
  reviewer のみが検出（5/5 vs 4/5）。ハーネス固有の文脈検査に優位があり、D070 の
  security（検出力同等→委譲）とは逆に維持が最適と測定で確定。委譲表の reviewer 行を
  実測根拠つきに更新
- セキュリティの役割再定義（D070）: 「検出は継続更新されるビルトインに委譲・ハーネスは
  リリースゲート統合のみ所有」へ。自前 security-review スキルを release-security-review に
  改名してビルトイン /security-review の同名遮蔽を解除（D005/D052 の遮蔽判断を撤回）。
  仕込み脆弱性7種の頭合わせ実測でビルトイン7/7・自前7/7=検出力同等を確認し、測定で委譲を
  確定。委譲表に優位の根拠(実測/論拠/未評価)と再評価トリガの2列を追加。warn-gate-tamper に
  レポート無き release done の警告を追加
- Autopilot 運用規範（D068、A5-3）: VS Code の Autopilot(プレビュー) が人手ゲート・
  証拠待ちを「仮定」で自己続行し、テスト未実行のまま done 遷移する実測（Assumed
  tests passed）に基づき、運用規範を PLATFORM に記録（実行権のあるエージェント限定・
  人手ゲート区間では不使用・deny は有効）。change-request スキルに Copilot 時の
  implement ハンドオフを明文化。watchdog-continue は opt-in 維持を確定
- フェーズ外編集ガードの構造刷新（D066）: 第5回敵対的検証（29指摘・critical 2）を受け、
  子プロセス再帰を全廃し「候補のアプリスコープ判定→状態判定1回」の同一プロセス構造へ。
  `../` トラバーサル・%エンコード・Windows file URI・9件目切り詰め・別フィールド名
  パッチの各バイパスを封鎖し、候補過多とパス不明の編集系ツールを fail-closed 化。
  warn-gate-tamper は一括/パッチ経由のゲート変更と pending_approval 遷移も検知。
  攻撃リプレイを selftest に恒久化（sh 94 / ps1 71）
- 指示層の棚卸し（D082。A6-19 / A5-5 / IA-20260831-14 / R-05 の一部。再監査 2026-09-09 PF-1〜7/10〜13）: AGENTS.md を
  24,334B/298 行→18,390B/191 行、CLAUDE.md を 43→18 行の対応表だけに（常駐 341→209 行。統合の断片適用後は
  AGENTS.md 18,339B/192 行＝常駐 210 行。仕組みの説明は PLATFORM.md「Claude Code 固有の運用」へ）。フックで機械強制済みの
  禁止事項（保護ファイル一覧・テンプレ編集・push/tag・秘密情報・不可視文字）は本文から削り `.github/hooks/README.md` への
  参照に。「確認を挟まず自走する」の分散文言を Fable 5.1 公式 autonomy ブロック（原文）1回＋「必ず止まる条件」5行に、
  コーディング規約を公式スコープ段落＋ハーネス固有2項（Edit 部分編集・タスク単位コミット）に置換。implement / task-worker の
  完了証拠の主経路を返答テキストから `tasks.md` の差分＋PostToolUse ログ（`logs/usage/<sid>.subagents.jsonl`）＋recap
  1段落へ。強いモデル切替勧告 7 箇所を model-policy.yml 参照に、指示層の逸話 47→10 箇所を「(D0xx)」参照に。spec-critic に
  証拠拘束文、harness-retrospective に「定期棚卸し（削除→失敗したものだけ復帰）」節（Boris Cherny の 6 か月ルール、出典つき）。
  「Task ツール」表記を「Agent ツール（旧称 Task）」に統一（generate-adapters.py・README・PLATFORM。CC-3）。残作業は A6-19b
- `plugin.json` を Agent Plugins 1.0 スキーマに書き換え（D083。旧 agents/skills/hooks/commands/commands_note は
  additionalProperties=false のため `extensions` の逆ドメイン名前空間 `com.github.copilot` /
  `io.github.n-ima.copilot-sdlc-harness` へ移動。description の「Antigravity対応」を
  「Antigravity はアダプタ同梱・未検証」に。`com.github.copilot.note` に Agent Host の入口スキル（D081）を明記）。
  README の「Agent Plugins は Preview」を「Agent Plugins 1.0 GA（2026-08-31）。実インストールは未検証」に
- docs テンプレ(A7-M-4。D084): `docs/00-overview/README.md` の成果物索引(environment.md / interfaces/ / ui/ /
  security-review-report.md / change-requests.md 等)とトレーサビリティ ID 体系表、nfr テンプレの `NFR-nnn` ID 列、
  requirements / test_plan / architecture テンプレの ID 参照、change-requests テンプレの ID 例(`T-` / `TC-` → `TASK-` / `UT-`)。
  README / overview / COMPARISON の決定数ハードコードを生成ブロックまたは非数値表現へ。PLATFORM.md「鏡の生成物」節、
  hooks README の件数規約
- `tools/generate-adapters.py`（統合 2026-09-14）: 第0節の `--check` / `--selftest` は第4節（model-policy）・第5節（入口スキル）・
  第6節（plugin マニフェスト）をすべて回し終了コードは大きい方。第3節は入口スキルに `.claude/skills` ポインタを作らない
  （w2-skills 時点の見落としを統合で修正。全再生成で未追跡ディレクトリが出ないことを確認）。CI ステップ名に「: 」を含めない

### 変更（w1-guards: 設定・ファイル書込系ガードの封鎖。D080。再監査 2026-09-09 RG-1/SC-4/RG-3/RG-4/RG-5/CP-1/SC-1/SC-2/SC-6/MP-3/CC-10/SC-3/H-10/SC-5）

- `guard-harness-config-edit` sh/ps1: コマンド検査を固定 allowlist から反転判定へ（保護パスを含むコマンドは全セグメントの
  先頭語が読み取り専用語で、リダイレクト・変数代入・書き込み語を含まないときだけ allow、それ以外は ask）。python -c /
  node -e / git apply|checkout|restore|read-tree|update-index|stash pop / dd / install / mklink / New-Item Junction / ln -s /
  perl -pi / [IO.File]:: / 変数間接 / ヒアドキュメント / Copy-Item / デコードパイプ / -EncodedCommand / iex / xargs /
  find -delete / awk の全ベクタが ask。行継続 3 種（バックスラッシュ・バッククォート・キャレット）を畳む（H-1）
- `guard-config-change.py`（ConfigChange 第2防衛線）を新設し `.claude/settings.json` に配線（matcher
  `project_settings|local_settings|skills`）: 保守モード外で `.claude/settings.json` が git HEAD と異なれば block、
  `.claude/skills/` は中核 2 スキルの変更・hooks/hooks.json・.claude-plugin/plugin.json の出現・権限系 frontmatter の
  新規追加を block（動的追加は allow）。fail-open。`--selftest` 21 検査
- `_paths.sh` / `_paths.ps1`（D080 決定2）: パス候補の網羅収集・読み取り系ツール名の除外（CP-1）・新内容の収集・8.3 短縮名
  の展開（`win_longpath` / `ConvertTo-LongPath`）を共通化し、deny 型ガード 6 本が source。validate (k)/(k-2) が配線を検査
- 保護面を拡張: `.claude/rules/`、`.github/copilot/`、`.github/copilot-instructions.md`、`CLAUDE.local.md`、`GEMINI.md`、
  `.github/harness/model-policy.yml`（guard sh/ps1・validate CROSS_ITEMS・CODEOWNERS・permissions.deny）。skills 配下の
  hooks/hooks.json と権限系 frontmatter の書込は ask（SC-2）
- `guard-secret-leak`: npm_ / JWT / Azure AccountKey / SAS sig= / OpenAI / GitLab / Hugging Face / PyPI / SendGrid /
  DigitalOcean / Google OAuth / AWS 一時鍵 / Slack App / PGP を高確度に追加、汎用パターンのクォート必須を撤廃（H-3）
- 実行時間の担保（SC-5）: config-edit.sh の照合を bash 組み込みに置換（Windows 実測: 8 セグメント 3.9s→1.1s、
  32 セグメント 12.0s→1.4s。timeout 5s 超は fail-open で素通りしていた）。33 セグメント超は評価前に ask（fail-closed）。
  8.3 短縮名はコマンド内トークンも含め長形式に展開してから照合（H-10 の横展開）
- selftest 両系に +106 ケースずつ（件数の正は selftest の実行結果。CI 緑を確認するまで「全PASS」は書かない）

## [1.1.0] - 2026-08-30

リリース時点の検証: selftest sh 82 / ps1 59 全PASS（件数の正は selftest 実行結果）。

### Added

- route-request.ps1: 受付ルーチンの PowerShell 版を gate-hooks.json の
  UserPromptSubmit に配線し、既定の Copilot にも毎依頼で機械注入（D060、A5-1）。
  注入文は .sh とバイト同一（全分岐+注記つき値で実測）・selftest sh 79 / ps1 56。
  第4回敵対的検証(24指摘)を反映: GATE_STATUS 抽出の線形化と入力上限(ReDoS・
  注入爆弾対策)・キー初出のみ/値64字の仕様統一・本体リポジトリでの出力契約統一
  (無出力→最小JSON)
- 宣言素通り対策の機械化（D061）: 運用中の注入文に「提示したターンでは編集せず停止」を
  追加し、フェーズ外ガードの確認文を「スキップ+/12 指示」推奨に再設計（実機で
  指示層2連敗を確認したため、強制力を機械層へ移設）
- フェーズ外編集の deny 格上げ（D062）: 運用中・着手前のアプリコード編集を ask から
  deny に変更（実機3連敗+ホスト承認記憶による ask 消音の観測が根拠。deny の理由文が
  エージェントに /12 経由を機械的に要求する。緊急経路は人間の直接編集。未初期化は
  ask のまま）
- 複数ファイル/パッチ系ツールの素通り穴を封鎖（D063）: 単一の file_path を持たない
  一括編集・apply_patch 系のペイロードで guard-phase-scope がパス欠落の fail-open に
  落ちていた（実機で app/memo.py の編集が無記録で素通り。判定ログで証明）。
  tool_input 全体から path 系フィールドとパッチ内 File マーカーを再帰収集し、
  候補ごとに自己再帰で評価して最悪判定（deny > ask > allow）を採用
- in_progress 自書き換え迂回への対処（D063）: deny 理由文で明示的に禁止し、
  warn-gate-tamper が in_progress 遷移を警告（入口コマンド経由か・CR記録があるかの確認）

## [1.0.0] - 2026-08-30

Copilot 経路の実機 E2E 完走（COPILOT-E2E.md 全項目・A2-6 applied）により昇版（D058/D059）。
0.9.0 以降の変更（D053〜D059）を含む。

### Added

- ライトパス（fast-track スキル）: 小規模グリーンフィールドの軽量経路（承認2回・
  品質バー維持）。実測でフルパス比 46% コスト減（D055、A4-1）
- 測定基盤: tools/e2e-run.py + evaluation/（bare との A/B・pass^k・枯渇検知。
  配布物からは除外）（D054〜D056）
- 鏡の生成物化: tools/gen-docs.py（README 表・overview 統計を生成、--check を
  validate と CI に配線）（D054）
- COPILOT-E2E.md: Copilot 経路の実機検証チェックリスト（D057）
- GATE_STATUS 形式リント: progress.md 編集後に正準 `<!-- GATE_STATUS -->` ブロックが
  無ければ警告（warn-gate-tamper .sh/.ps1）（D059）
- guard-phase-scope に読み取り系ツールの除外（ツール名の二段フィルタ。警報疲れ→
  包括許可→ガード無効化の連鎖を遮断）（D059）
- 本体判定の memo プリスティン機械判定（D058）
- selftest: sh 64→75 / ps1 41→50

### Changed

- fast-track: Copilot での implement ハンドオフ・実行責務・タスク単位コミット・
  done 遷移時期を明文化（D059）
- request-routing: 自分で起動できない環境では「提示して停止」を明文化（D059）
- orchestrator: progress.md はテンプレをそのままコピー（自作禁止）（D059）
- PLATFORM: 二重発火は既定で非発生（VS Code は Claude フックを opt-in 提案）と
  「すべて許可」の運用注意を実測反映（D058/D059）
- Antigravity を凍結（現状維持。検証・拡張の対象は Copilot / Claude Code の2環境）（D057）
- e2e-run に --fresh-on-command（セッション分割教義どおりの走らせ方で $69→$49、
  29% コスト減を実測）（D057）

### Fixed

- D053 の本体判定過剰修正により、テンプレ由来の新規プロジェクトが本体誤検知され
  /00 が拒否される回帰（D058）
- D052 適用の交差リグレッション（/91 の LICENSE 無言上書き・trace-check の全角 ID
  盲点・converge の終了コード矛盾・メタ4ファイルの配布混入等）（D053）

## [0.9.0] - 2026-08-22

### Changed

- 受付ルーチン契約の一本化・運用中判定の正準化・プロンプト4本の薄化と
  ハンドオフ send 意味論の二分（外部監査 2026-08-19 の適用。D048）
- `AGENTS.md` の憲法化: 39.9KB→24.9KB。参照的詳細を
  `.github/harness/PLATFORM.md` へ分離（D050）
- 第2回監査の全面適用: 配布規律の整備（LICENSE / SECURITY / CONTRIBUTING /
  本 CHANGELOG / 提案台帳 `audits/PROPOSALS.md`）ほかロードマップ一式（D052）

### Added

- E2E 通し実行の初回完走（Claude Code 経路。/00→要件→設計→実装→テスト→
  リリース→運用中→/12 まで実走行）と、実測で見つかった3件の即時修正（D049）
- `tools/sync-harness.py`・`tools/intake-app.py` の selftest、docs 乖離後の
  再収束入口 `/13-converge`（D052）
- claude-code-security-review の opt-in テンプレート
  （`.github/workflows/claude-security-review.yml.example`。D052）

### Fixed

- 注入系の静かな死（JSON エスケープ・エンコーディング）と自己権限昇格ガードの
  穴4系統の封鎖（D048）
- マージ前再チェック: 角度を変えた再検証で検出した交差リグレッション・
  実測バグ30件超の修正（.ps1 エンコーディング固定・%b 教訓破壊・
  looks_like_harness 判定ほか。D051）

## [0.1.0] - 2026-07-02

- 初期構築（D001-D047）: フェーズゲート付き SDLC オーケストレーション・
  独立レビュー（spec-critic / reviewer）・機械的ガード（hooks + deny）・
  成長ループ（learnings / retrospective）・3環境対応
  （Copilot / Claude Code / Antigravity）・配布/導入ツール（sync / intake）。
- 版数 0.1.0 のまま継続改修していた期間（2026-07-02〜2026-08-19）の総括。
  個々の経緯は DECISIONS.md の D001〜D047 が正。
