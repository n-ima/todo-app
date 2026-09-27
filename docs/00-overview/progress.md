<!-- GATE_STATUS
requirements: done 証拠: `python tools/questionnaire.py check requirements/questionnaire.md` → ERROR 0 / 必須未回答 0。ユーザーが 2026-09-26 に §7 を確認し承認（A-020 は (a)） (2026-09-26 21:38)
design: done 証拠: `python tools/trace-check.py .` → orphan 0 / NG は既知の誤検知 NFR-010 のみ。spec-critic MAJOR 6・MINOR 15 全件反映。ユーザーが 2026-09-27 にモックアップ 4 枚を確認し設計ゲートを承認 (2026-09-27 09:00)
implementation: in_progress
test: not_started
release: not_started
-->
<!-- GATE_COUNTERS
implement_test_loops: 0
-->

# 進捗ダッシュボード

最終更新日: 2026-09-26

上のコメントブロック（GATE_STATUS）が正の状態。`.github/hooks/` のフックや
`.github/skills/gate-check/SKILL.md` はこのブロックを直接パースするため、
書き換えるときはキー名・インデントを崩さないこと（5 キー固定・値は 4 語彙。
語彙・遷移・往復上限・復旧手順の正は `.github/harness/STATE-MACHINE.md`、機械検査は
`python tools/gate_status.py check`、書換は `python tools/gate_status.py set <phase> <value>`
が一時ファイル→rename で行う）。直下の `GATE_COUNTERS` は implement↔test の往復回数
（`test` が実装に差し戻すたびに +1、`test: done` で 0。無ければ 0 扱い）。

| フェーズ | 状態 | ゲート承認日 | 備考 |
|---|---|---|---|
| 要件定義 | 完了 | 2026-09-26 | /02 完了: 質問票 84 問（第 1 回 76・第 2 回 8）全問回答済み。requirements.md（US 22 件・A 台帳 A-001〜A-024）・nfr.md・glossary.md・environment.md を作成し、spec-critic 2 回（MAJOR 9＋5）を反映済み。2026-09-26 ユーザー承認（A-020 は (a)） |
| 設計 | 完了 | 2026-09-27 | /03 完了（2026-09-26）: 技術スタックは .NET（ユーザー選択。版は LTS の .NET 10）。architecture.md・ADR 0001〜0006 を作成。/04 完了（2026-09-26）: detailed-design/DD-01〜DD-12・ui/ モックアップ 4 枚・design-tokens.md（コントラスト実測）・実装規約スキル dotnet-conventions。spec-critic 1 回（MAJOR 6・MINOR 15）を全件反映。2026-09-27 ユーザーがモックアップ 4 枚を確認し承認 |
| 実装 | 未着手 | - | 該当なし |
| テスト | 未着手 | - | 該当なし |
| リリース | 未着手 | - | 該当なし |

状態は次のいずれか: `未着手`(not_started) / `進行中`(in_progress) / `ゲート承認待ち`(pending_approval) / `完了`(done)

## 未確定事項・申し送り

- （フェーズ間で持ち越す未解決の疑問点や前提をここに記録する）
- Q-03-12 は「(A) 状態の並びの定義」で確定（2026-09-23）。承認フロー・自動化ルールはスコープ外
- 第 1 回 m5（2 論点混在）の確認結果: Q-01-3・Q-03-1・Q-03-5 は問題なし。Q-06-1 は「開発に使える時間」が未回答（requirements.md A-011）。Q-11-4 は Q-11-3 と食い違い（第 2 回 Q-11-6）
- 要件承認済み（2026-09-26）。設計開始前に人が実機確認: A-017（GitHub Secret scanning / CodeQL の可否）・A-022（退避先サーバーの稼働時間）・A-023（Windows 11 のエディション・ファイアウォール・同時接続数上限）
- 設計 /03（2026-09-26）: A-017・A-022・A-023 はユーザーの了承により未確認のまま設計を進めた（architecture.md §10 で対策。リリース前に人が確認）。`python tools/trace-check.py .` の NG「NFR-010 dangling」は、ツールが要件 ID を requirements.md 本文からしか拾わないための誤検知（NFR-010 は nfr.md に実在）。振り返りでハーネス改善候補にする
- 設計 /04（2026-09-26）の申し送り: (1) 一覧の描画は最大 3,000 行。15,000 件規模では既定表示が 2 段目までの展開になる想定（US-005 の「3 段目まで」は上限の範囲で守る。DD-06 §3）。(2) Install のサービス SID による ACL 付与は Windows 11 実機での確認が必要（DD-10 §2）。(3) CI ワークフロー `.github/workflows/ci.yml` は実装フェーズの最初のタスクで作る（DD-10 §7）
- ハーネス同期: D098 まで適用済み（2026-09-23、source_commit 2cee3e7。`docs/00-overview/harness-origin.md`）

申し送りは**進行中のサイクル分のみ**を本文に置く。リリースのゲート承認時に、
完了した版のサイクル分を `docs/00-overview/archive/progress-v<版>.md` へ退避する
（GATE_STATUS ブロック・フェーズ表は本文に残す。append-only のまま積み上がると
このファイルが1回の Read に収まらなくなる。2巡で879行に達した実例あり）。