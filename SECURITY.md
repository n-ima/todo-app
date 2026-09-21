# SECURITY.md — 脆弱性の報告窓口と対応方針

このハーネス自体の脆弱性（ガードのすり抜け・保護の穴など）を見つけた場合の報告先です。
テンプレートから作られた個別プロジェクトの脆弱性は、そのプロジェクト側の窓口へ報告してください。

## 報告窓口

- **推奨**: GitHub の **Security Advisories**（リポジトリの Security タブ →
  「Report a vulnerability」）から非公開で報告してください。
- **代替**: リポジトリ Issue の private 報告（Private vulnerability reporting が
  有効な場合）。
- 修正前に攻撃手順が公開されるのを避けるため、公開 Issue への詳細記載は
  控えてください。

## 対象（とくに報告を歓迎する領域）

- **ガードフック**（`.github/hooks/`）: 判定のすり抜け
  （エンコーディング差・エスケープ・シェル差・パターン漏れによる素通し）
- **deny 設定**（`.claude/settings.json` 等）: 自己権限昇格・保護対象の書き換え経路
- **配布ツール**（`tools/sync-harness.py`・`tools/intake-app.py`）:
  意図しない上書き・パス脱出・本体誤認・配布元の偽装（`harness-origin.md` の
  `source_commit` / `archive_sha256` と実際の配布物が食い違う経路）
- **外部反映・証拠のガード**（`guard-external-effect.*`・`guard-dangerous-git.*`・
  `guard-done-evidence.*`）: 承認なしにデプロイ・公開・push に到達できる経路、
  証拠なしの `done` / `[x]` 書込が通る経路（承認バイパス下で deny にならない場合を含む）

## 対応方針

1. **確認**: 報告内容を実測で再現する（再現手順・環境が添えてあると早く進みます）。
2. **修正**: ガードの修正には selftest の回帰ケースを併設する。
3. **記録**: 判断と根拠を `DECISIONS.md` に D 記録として残す。

なお、このハーネスの機械的ガードは「エージェントの事故の抑止」が目的であり、
悪意ある人間に対する防壁ではありません。その前提を超える期待はしないでください。

## サポート対象の版（codex 監査 2026-08-31 R-07）

版数の正は `plugin.json` の `version`（`CHANGELOG.md` はその要約）。

| 版 | 状態 | 修正の提供 |
|---|---|---|
| 1.2.x（現行） | サポート中 | セキュリティ修正・機能修正とも `main` に取り込み、次の版で提供 |
| 1.1.x | セキュリティ修正のみ | 1.2.0 の公開（2026-09-21）から 90 日（2026-12-20）まで。修正は 1.2.x への更新で提供（バックポートはしない） |
| 1.0.x | セキュリティ修正のみ | 1.1.0 の公開（2026-08-30）から 90 日（2026-11-28）まで |
| 0.x | 非サポート | 1.2.x へ更新してください |

配布先プロジェクトは `tools/sync-harness.py`（/91）で本体の修正を取り込む。取り込んだ版と本体コミットは
`docs/00-overview/harness-origin.md` の `source_commit` / `archive_sha256` / `synced_at` / `latest_decision` に
記録され、`python tools/sync-harness.py --verify` で本体 HEAD と照合できる。

## 応答の目安（個人メンテナのため SLA ではなく「目安」）

| 段階 | 目安 |
|---|---|
| 受領の返信（acknowledgement） | 5 営業日以内 |
| 重大度の判定と再現（triage） | 14 日以内 |
| 修正の提供 | CRITICAL / HIGH は 30 日以内、MEDIUM / LOW は 90 日以内を目標。間に合わない場合は報告者に状況を共有する |

重大度の語彙は `reviewer` と監査に合わせて CRITICAL / HIGH / MEDIUM / LOW。CRITICAL は「エージェントが承認なしに
外部反映（push・タグ・デプロイ・公開）や保護対象の書き換えに到達できる経路」。

## 公開方針（coordinated disclosure）

- 修正が公開されるまで詳細は非公開（報告者にも公開を控えてもらう）。修正公開後、または受領から **90 日**
  経過後（修正が間に合わない場合）に GitHub Security Advisory として公開する。
- 公開する内容: 影響する版・経路の概要・修正版・回避策・報告者の謝辞（希望者のみ）。
- `CHANGELOG.md` の `### Security` 節と `DECISIONS.md` の D 記録に、判断と根拠を残す。

## 対象外（scope exclusion）

- **悪意ある人間に対する防壁ではない**: 機械的ガードは「エージェントの事故の抑止」が目的。パースに失敗した
  payload でも保護対象パスを含む書込は grep フォールバックで deny / ask 側に落ちる（selftest で固定。codex R-02）が、
  パス系フィールド名が未知の形（`fileName` 等）や interpreter 内の書込（`python -c` の中の open()）は文字列検査では
  見えない。境界は OS sandbox・`permissions.deny`・Git 側保護（PLATFORM.md「セキュリティの層構造」）。
- テンプレートから作られた**個別プロジェクトのアプリコード**の脆弱性（そのプロジェクトの窓口へ）。
- ホスト（VS Code / Copilot / Claude Code）自体の不具合（各ベンダーへ。本ハーネスは劣化モード表で影響を明記する）。
- `chat.useCustomAgentHooks` 等が組織設定で無効化された環境でフックが発火しないこと（設計上の前提。PLATFORM.md）。
- 評価装置（`evaluation/`・`tools/e2e-run.py`）の結果の解釈。

## 謝辞（acknowledgements）

有効な報告をくださった方は、希望に応じて Security Advisory と `CHANGELOG.md` の該当版に氏名または
ハンドルを記載する（匿名希望も可）。金銭的な報奨（bug bounty）はない。

## 外部依存の固定と更新（サプライチェーン。codex 監査 2026-08-31 H-02 / IA-20260831-04）

- **浮動参照の禁止**: `npx <pkg>@latest`・`@next`・`@canary`、`uses: <owner>/<repo>@main`、コンテナの `<image>:latest`
  は「監査した版」と「実行される版」を結び付けないため使わない。`python tools/validate-harness.py` (o) が設定・
  スキル・文書・ワークフロー・ツールを走査して ERROR にする（配布先プロジェクトでは WARN）（名前を伴わない散文の言及は対象外）。
- **固定の単位**: npm / MCP は版番号（`@playwright/mcp@0.0.80`）＋ lockfile の `integrity`（`npm i -D <pkg>@<版>` で
  `package-lock.json` に載せる。test-case-design スキルの MCP 設定が実例）。GitHub Actions は 40 桁の commit SHA
  （コメントに版。`.github/workflows/harness-ci.yml` が実例）。外部 Skill は取得元のコミットハッシュ / タグを
  SKILL.md の出典に記録し、レビュー済みの印「レビュー: 誰が・YYYY-MM-DD」を残す（skill-authoring スキル）。
- **更新の手順**: Dependabot / Renovate の PR（または手動 PR）で、差分・release notes・safe trial（使い捨て環境で
  1 回実行）・ロールバック手順を確認してから取り込む。手で `@latest` に戻さない。
- **CI の権限**: `harness-ci.yml` は `permissions: contents: read`・job ごとの `timeout-minutes`・`concurrency`・
  checkout の `persist-credentials: false` を持つ（validate (q) が検査。本体 CI は ERROR、プロジェクトが
  生成した CI は WARN）。

## リリースの不変性（codex 監査 2026-08-31 H-07 / IA-20260831-09、再監査 2026-08-31 A7-M-1）

- 本体の各版（`CHANGELOG.md` の `## [X.Y.Z]`）には annotated tag `vX.Y.Z` を付ける（人間の作業）:
  `git tag -a vX.Y.Z <その版のコミット> -m "vX.Y.Z" && git push origin vX.Y.Z`。validate (p) がタグの無い版と
  lightweight タグを WARN で列挙する（2026-09-14 時点: 0.1.0 / 0.9.0 / 1.0.0 / 1.1.0 の 4 版ともタグ無し）。
- 配布先には `tools/sync-harness.py` が `harness-origin.md` に `source_commit`（本体 HEAD。未コミットの変更が
  あれば `-dirty`）と `archive_sha256`（`git archive --format=tar HEAD` の SHA-256）を記録し、未コミットの本体からの
  `--apply` を既定で拒否（`--allow-dirty` で明示）、未追跡ファイルを配布しない（`git ls-files` 照合）。
  `python tools/sync-harness.py --verify` で記録と本体 HEAD を照合する（0 = 一致 / 1 = 不一致 / 2 = 照合不能）。
- 署名付き immutable release と artifact attestation（GitHub の Immutable releases / Artifact attestations）は
  設計のみ（open）: GitHub Release を発行するようになった時点で `gh release create` を attestation と組み合わせ、
  consumer 側（sync-harness）で `gh attestation verify` を呼ぶ。それまでは commit SHA と archive の SHA-256 が
  同一性の根拠であり、署名による発行元の証明は無い。
