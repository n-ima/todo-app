# copilot-sdlc-harness — AI開発ハーネス（Copilot / Claude Code 対応・Antigravity アダプタ同梱）

[![harness-ci](https://github.com/n-ima/copilot-sdlc-harness/actions/workflows/harness-ci.yml/badge.svg)](https://github.com/n-ima/copilot-sdlc-harness/actions/workflows/harness-ci.yml)

「要件定義 → 設計 → 実装 → テスト → リリース」を一気通貫でオーケストレーションするための
**汎用テンプレート** です。特定のアプリのコードは含みません。新しいアプリを作るたびに、
このリポジトリをテンプレートとして使い、要件メモを書くところから始めます。

> **用語の約束**: 本文で単に「ハーネス」と書くときは、この copilot-sdlc-harness
> 自身を指します。ツール分類としての開発ハーネス一般や比較対象を指すときは
> 「ハーネス一般」「他ハーネス」のように必ず修飾して書き分けます。
> その他の用語（done契約・ゲート・bare・pass^k 等）は
> **[.github/harness/GLOSSARY.md](.github/harness/GLOSSARY.md)** にまとめています。

> **このREADMEはハーネス（テンプレート）自体の説明です。** テンプレートから作られた
> プロジェクトでは、`/00-start-project` 実行時にルートREADMEがアプリ用スタブに
> 置き換えられ、リリース時に正式なアプリのREADMEになります（リポジトリを開いたとき
> 最初にアプリの説明が見えるようにするため）。ハーネスの使い方文書は
> [.github/harness/](.github/harness/) に置かれ、プロジェクトでも残ります。

## このハーネスで何が良くなるか（実測ベース）

「AIに開発させると何が壊れるか」という失敗モードごとに、指示文ではなく機構
（フック・検証ツール・台帳）を対応させています。結果として:

- **曖昧な要件で「静かに間違った仕様が本番化する」のを止める** — 締め日の矛盾を仕込んだ
  課題で、素の Claude Code は気づかず実装（実測 0/2 失敗）、本ハーネスは要件ゲートで
  検出・記録して実装（2/2 成功）。**参考値**（n=2・装置 v1・予算非対等 bare $40 / harness
  $60・全走行 Claude Code 2.1.201＝比較条件不成立。再監査 2026-08-31 H-8、2026-09-09
  EV-3/EV-10/CC-1。装置 v2 で n=5 をやり直す＝A6-16）。
- **「完了しました」を証拠でしか認めない（done契約）** — 完了マークに検証コマンドと実行痕跡を
  必須化し、独立レビュアーが証拠と実体の食い違いまで照合（テスト証拠のすり替え改竄を検出。
  公式 `/code-review` は見逃した）。
- **運用中の変更が変更管理フローに機械的に乗る** — 素チャットの「◯◯を追加して」から、
  人手ゼロで台帳起票→ゲート→実装→テスト→記録まで自走。
- **Copilot と Claude Code の両方で実機動作**（version 1.2.0。Claude Code は 2026-09-21 に 2.1.278 で確認、Copilot の確認は 1.1.0＝2026-08-30 のまま）。自己整合はフック自己テスト
  （sh / ps1。件数は下の生成行）と構成整合検査（validate-harness）で機械維持
  （2 OS の CI は 2026-08-11 の導入以来赤のままだったが、2026-09-10 に初めて全ジョブ緑
  （[run 34406967789](https://github.com/n-ima/copilot-sdlc-harness/actions/runs/34406967789)）。CI 緑を確認するまで「検証済み」「全PASS」は
  書かない＝再監査 2026-09-09 RG-12 / D079。詳細は「現在地とロードマップ」節）。
<!-- BEGIN GENERATED: selftest-counts -->
- **機械的フック 25 本（+opt-in 1）・フック自己テスト selftest.sh 404 / selftest.ps1 376 ケース**（件数は check 行の静的計数＝生成物。pass/fail の正は各 selftest の実行結果であり、CI 緑を確認するまで「全PASS」は書かない）。
<!-- END GENERATED: selftest-counts -->

**他ハーネス・公式ビルトインとの優位／同等／劣位（未検証は未検証と明記）は
[.github/harness/COMPARISON.md](.github/harness/COMPARISON.md) に実測根拠つきで整理**しています
（HTML版: [COMPARISON.html](.github/harness/COMPARISON.html)）。

## 対応プラットフォーム

振る舞いの正は1か所（`AGENTS.md` + `.github/`）に置き、各環境には薄いアダプタだけを
置いているため、**どの環境で使っても同じフェーズゲート・同じ振る舞い**になるよう
設計しています（実機で通し検証済みなのは Copilot / Claude Code の2環境。
Antigravity はアダプタ同梱のみで検証は凍結中＝D057）。

| 機能 | GitHub Copilot (VS Code) | Claude Code | Antigravity |
|---|---|---|---|
| 共通指示 | AGENTS.md（直接） | CLAUDE.md → AGENTS.md | AGENTS.md（直接） |
| フェーズ起動 | `/03-design-architecture` 等（Agent Host: 入口スキル `.github/skills/<nn>-<name>/`＝prompts からの生成物 / Local ハーネス: prompts） | 同名コマンド（`.claude/commands/`） | 同名ワークフロー（`.agents/workflows/`） |
| サブエージェント | runSubagent | Agent ツール（旧称 Task。`.claude/agents/`） | Agent Managerの別会話 |
| スキル | `.github/skills/` | `.claude/skills/`（正へのポインタ） | 正のファイルを直接参照 |
| フック（機械的ガード） | ✓ `.github/hooks/` | ✓✓ hooks（同一スクリプト共用）+ `permissions.deny` | ✗ IDEのDeny List（GUI）で代替 |
| ハンドオフボタン | ✓ | ✗（コマンド案内に統一） | ✗（コマンド案内に統一） |

どの環境でも入口は同じです: **`/00-start-project` を実行**（Copilot は Agent Host（既定）なら
入口スキル・Local ハーネスならプロンプト、Claude Code はスラッシュコマンド、Antigravity は
ワークフローとして同名で存在します）。

**対応度の目安**: Copilot＝フル（主環境・ハンドオフ含む。実機検証手順は
[COPILOT-E2E.md](.github/harness/COPILOT-E2E.md)） ／ Claude Code＝フル同等
（機械的ガードはむしろ最強: hooks+permissions.denyの二重。E2E実測済み） ／
Antigravity＝**現状維持（凍結）**: アダプタ（.agents/workflows/）は同梱を継続するが、
以後の検証・拡張の対象は Copilot / Claude Code の2環境とする（D057）。
機械的ガードはGUIのDeny Listへの手動登録で代替（IDEがプロジェクト内スクリプト
フックを読まないため）。

> 2026年8月時点のGitHub Copilot / VS Code / Claude Codeの最新仕様（Custom Agents,
> Agent Skills, Agent Hooks, Agent Plugins, AGENTS.md）に基づいて構築しています
> （最終検証日: 2026-08-12）。**Agent Hooks は Preview 機能**であり仕様が変わる可能性が
> あります。**Agent Plugins は 1.0 が GA**（2026-08-31。VS Code / Copilot CLI / SDK）で、
> `plugin.json` はその 1.0 スキーマに準拠させています（実インストールは未検証。
> 「再利用パッケージとしての配布」節）。実機の通し動作は 2026-08-30 に Copilot（VS Code・6セッション）と Claude Code（ヘッドレス・回帰確認含む）の両経路で確認しています（version 1.1.0）。

> 🎨 **はじめての方へ**: [.github/harness/overview.html](.github/harness/overview.html) を
> ブラウザで開くと、このハーネスの全体像・特徴・仕組みが図解つきで分かります
> （ダブルクリックで開ける自己完結型HTML）。ページ上部のナビから
> **エージェント一覧・スキル一覧・コマンド一覧・ガードレール解説**の各ページにも移動できます。
> 実際に手を動かした時に何が起きるかを具体例で追った使い方ガイドは
> **[.github/harness/USAGE.md](.github/harness/USAGE.md)** を参照してください。
> このREADMEは全体構造の説明です。
> ハーネス自体の設計判断の背景・根拠は **[DECISIONS.md](DECISIONS.md)** に記録しています。
> ハーネスを改修する際は、同じ議論を繰り返したり直したバグを再導入したりしないよう、
> 変更前に必ず目を通してください。

## 前提

- **共通**: git
- **Copilot**: VS Code + GitHub Copilot Chat 拡張機能（Custom Agents / Agent Skills 対応バージョン）
- **Claude Code**: Git Bash（Windowsでのフック実行に必要）・python 推奨
- **Antigravity**: 特記なし

## 適用範囲（使うべき場面・使うべきでない場面）

- **使う**: 本番運用・長期保守するアプリ、複数人での開発、監査・トレーサビリティ要件のある開発。
- **大規模（US30超・複数サブシステム・複数チーム）も対応**: 単一パイプラインのままではなく、
  システム層＋サブシステム層の2層に分割して使う
  （[large-scale-development](.github/skills/large-scale-development/SKILL.md) スキル。
  サブシステム分割とインターフェース契約は人が承認、サブシステムの内側はAIに任せる）。
- **既存コードベース（brownfield）も対応**: `/11-brownfield-intake` で実装済みコードから
  as-is要件・アーキテクチャを逆起こしし、整合検証を経て差分駆動の改修サイクルに接続する
  （[brownfield-intake](.github/skills/brownfield-intake/SKILL.md) スキル）。
- **使わない**: 使い捨てスクリプト、実験、PoC（素のCopilotに直接頼む方が速い）。

このハーネスはフェーズゲート・独立レビュー・文書化を強制する分、フルパイプラインは
小さな作業には過剰です。**小規模な新規開発にはライトパス（`fast-track` スキル。
要件+設計を1往復に集約・承認2回・品質バーは維持）が既定で提案されます**（D054 の
実測が根拠。詳細は USAGE.md §9）。使い捨てスクリプト・実験・PoC は引き続き対象外です。

## クイックスタート

1. `requirements/memo.md` に、作りたいアプリについて自由に書く（雑なメモでよい）。
2. Copilot Chat の入力欄で `/00-start-project` を実行する
   （このプロンプトは `agent: orchestrator` にバインドされているため、現在どのエージェントを
   選択していても自動的に `orchestrator` として動く。手動でエージェントを切り替える必要はない）。
   → 進捗を確認し、次にやるべきことを提案してくれる。
3. 各フェーズ専属のエージェント（`requirements` → `design` → `implement` → `test` →
   `release`）へ、フェーズの区切りごとに案内される**「新しいチャット + コマンド」**で
   進める（例: 新しいチャットで `/01-requirements-intake` を実行。プロンプトは対応する
   エージェントへ自動的にバインドされるため、手動でエージェントを切り替える必要はない）。
   会話が短い小規模案件なら、応答末尾に出るハンドオフボタンで続行してもよい。

具体的なやり取りの例は [.github/harness/USAGE.md](.github/harness/USAGE.md) を参照してください。

### フェーズごとに人の関わり方が違う（重要）

| フェーズ | 人の役割 |
|---|---|
| 要件定義 | **厚く回答する**。特にデプロイ環境・自動化してよい範囲（`environment.md`）は仮置きせず具体的に答える。ここが要。 |
| 設計 | 質問に答える＋**最終承認1回**。アーキテクチャ・詳細設計はエージェントが決める。 |
| 実装〜テスト | **基本ノータッチ**。ノンストップで自動実行される。真のブロッカー発生時のみ質問が来る。 |
| リリース | **承認と人手必須の作業**に対応。準備（ビルド・dry-run・チェックリスト）は`environment.md`に基づき自動、外部反映（push・タグ・デプロイ・公開）は`reviewer`の独立検証を経た action packet を行ごとに承認してから実行（Hooksが実行時にも「何を・どこへ」を示して確認。承認バイパス下は拒否）。人手必須の作業（支払い、外部ダッシュボードでの承認、ドメイン購入等）は提示に従って実施。 |

要件定義フェーズだけは厚く聞かれる前提で臨んでください。ここで曖昧に答えると、
「自動のはずの実装〜テスト」や「自動のはずのリリース」で必ず詰まります。

## 3層構造：Agents / Skills / Hooks

このハーネスは3つの異なる仕組みを役割分担させています。

| 仕組み | 役割 | 置き場所 |
|---|---|---|
| **Custom Agents** | フェーズごとの人格・ツール権限・引き継ぎ先を定義する「誰が」 | `.github/agents/*.agent.md` |
| **Subagents** | メインの会話を汚さず独立コンテキストで動く補助（例: `reviewer`） | `.github/agents/*.agent.md`（`user-invocable`/`agents`で制御） |
| **Agent Skills** | フェーズ内で使う手順・チェックリストという「どうやるか」の部品 | `.github/skills/*/SKILL.md` |
| **Prompt Files** | フェーズを1手ずつ進める再利用可能なスラッシュコマンド（正のレイヤ。VS Code の Local ハーネス限定で、既定の **Agent Host では読み込まれない**＝再監査 2026-09-09 CP-2。Agent Host では同名の**入口スキル** `.github/skills/<nn>-<name>/SKILL.md`（`tools/generate-adapters.py` が prompt files から生成する user-invocable スキル。役割の設定は本文の指示＝指示層）が入口になる。A6-17。実機確認は未了） | `.github/prompts/*.prompt.md`（Agent Host 入口: `.github/skills/<nn>-<name>/`） |
| **Agent Hooks** | ゲート・セキュリティ運用を機械的に補強する自動実行（Preview） | `.github/hooks/` |

エージェントを薄く保ち、手順の詳細はSkillに逃がすことで、各エージェントファイルが
肥大化しないようにしています。Skillは段階的開示（`name`/`description`だけを常時読み込み、
本文は関連する時だけ読み込む）により、無関係なSkillがコンテキスト・トークンを圧迫しないように
設計されています（詳細は [.github/harness/PLATFORM.md](.github/harness/PLATFORM.md) の「コスト計測の詳細」）。

## フェーズとエージェント／プロンプト対応表

<!-- BEGIN GENERATED: phase-command-table -->
| # | フェーズ | プロンプト | 対応エージェント | 成果物 |
|---|---|---|---|---|
| - | 進捗確認 | `/00-start-project`, `/99-status` | orchestrator | `docs/00-overview/progress.md` |
| 1 | 要件定義（初期） | `/01-requirements-intake` | requirements | 質問リスト |
| 2 | 要件定義（詳細化） | `/02-requirements-deepdive` | requirements（→ `spec-critic`を承認前に1回） | `docs/01-requirements/*.md` |
| 3 | 設計（アーキテクチャ） | `/03-design-architecture` | design | `docs/02-design/architecture.md`, `adr/` |
| 4 | 設計（詳細） | `/04-design-detailed` | design（→ `spec-critic`を承認前に1回） | `docs/02-design/detailed-design/` |
| 5 | 実装計画 | `/05-implementation-plan` | implement | `docs/03-implementation/tasks.md` |
| 6 | 実装 | `/06-implement-task` | implement（→ `task-worker`をタスクごとに、`reviewer`を完了タスク10個ごと・完了時にsubagent呼び出し） | ソースコード + テスト |
| 7 | テスト計画 | `/07-test-plan` | test | `docs/04-test/test-plan.md` |
| 8 | テスト実行 | `/08-test-execute` | test（→ `reviewer`をsubagent呼び出し） | `docs/04-test/test-report.md` |
| 9 | リリース | `/09-release-checklist` | release | `docs/05-release/release-checklist.md`, `CHANGELOG.md` |
| 10 | 振り返り | `/10-retrospective` | orchestrator | `docs/06-retrospective/retrospective.md` |
| 11 | 既存コードベース導入 | `/11-brownfield-intake` | change | as-is逆起こしdocs一式 |
| 12 | **変更請求（運用中の日常）** | `/12-change-request` | change（→ `task-worker` / `spec-critic` / `reviewer`） | `docs/00-overview/change-requests.md` + 差分更新されたdocs |
| 13 | 収束監査（運用中の定期棚卸し） | `/13-converge` | change | docs⇔実態の乖離の棚卸しと起票（対処は `/06`・`/12` の通常フロー） |
| - | 還流適用（**本体リポジトリ専用**） | `/90-apply-retrospective` | harness-maintainer | 改善提案の本体適用 + `DECISIONS.md` 追記 |
| - | 逆同期（本体の改善をプロジェクトへ） | `/91-sync-from-harness` | orchestrator | ハーネスコピーの更新（`tools/sync-harness.py` と併用） |
| - | 使い方ヘルプ | `/98-harness-help` | orchestrator | （次の一手の案内） |
<!-- END GENERATED: phase-command-table -->

`/01`〜`/09` は**初回構築の一本道**です。リリース後、および既存アプリを `/11` で
取り込んだ後（＝全フェーズが `done` の「運用中」状態）の日常の作業単位は `/12` になります。
運用が続いたら、定期またはCR数件ごとに `/13-converge`（収束監査。運用中の
docs⇔実態の乖離を棚卸しする）で spec とコードの整合を回復します。

### コマンドは覚えなくてよい（受付ルーチン）

**ユーザーは普通のチャットで「◯◯したい」と言うだけでよく、どのコマンドに入るかは
エージェントが決めます。** ハーネス適用済みプロジェクト（`docs/00-overview/progress.md`
がある）では、エージェントは依頼を受けた時点で `request-routing` スキルに従い、

1. `GATE_STATUS` を読んで現在地を確認し、
2. 依頼を分類し、
3. **応答の冒頭で「分類 / 入口 / 影響範囲」を宣言してから**、
4. 入口に入ります。Claude Code では該当コマンドを **Skill ツールで自分で起動**し、
   Copilot / Antigravity では実行可能なコマンド名を提示します（実行はユーザー）。

これは飾りではなく、ハーネスが成立するための必須条件です。振る舞いの正は
`.agent.md` にあり、それが読まれるのはコマンド実行時だけなので、
**入口に入らなければハーネスは1ミリも動きません**。ユーザーに
「スラッシュコマンドを使いましょうか？」と聞き返すのは誤りで、その時点で
ハーネスは機能していません（`.github/hooks/scripts/route-request.*` が毎ターン
この受付ルールを注入し（機械注入は Claude Code と既定の Copilot=D060 で gate-hooks.json に
配線。フックが効かない環境では AGENTS.md の指示レベルで担保）、`guard-phase-scope.*`
がフェーズ外の実装を機械的に止めます）。

### 起動経路と等価性

エージェントの起動経路は3つあります: (1) プロンプト実行（`/06-implement-task` 等）、
(2) ハンドオフボタン、(3) `runSubagent`。**どの経路でも同じ動作になるよう、振る舞いの正は
`.agent.md` に置き、プロンプトは薄い起動指示だけにしてあります**（ハンドオフ経由では
`.prompt.md` が読み込まれないため）。経路の違いは会話履歴を引き継ぐか＝コストだけです。

設計フェーズの最終承認（4→5）以降は、フェーズ間の `send: true` ハンドオフにより
実装→テスト→リリースがノンストップでつながる（真のブロッカー・Hooksによる`ask`確認・
`environment.md`で人手指定された作業・`reviewer`が問題を検出した時だけ止まる）。

## レビューは別セッションで（reviewer サブエージェント）

`implement` エージェントは完了タスク10個ごとのチェックポイントと全タスク完了時に、
`test` エージェントは全テスト成功後・リリースへ進む前に、必ず `runSubagent` で
`.github/agents/reviewer.agent.md` を呼び出します（結果は `docs/04-test/review-log.md` に
日時付きで記録され、その記録が実装・テストフェーズの done 条件になります。記録の無い
done は `warn-gate-tamper` フックが警告します）。`reviewer` は実装した本人の続きの
会話ではなく **独立したコンテキスト** で起動され、`edit`/`execute` ツールを持たない
読み取り専用エージェントとして、正しさ・セキュリティ（`.github/skills/release-security-review/SKILL.md`）・
品質をレビューします。これは「実装した本人がそのまま自分のコードを承認してしまう」バイアスを
防ぐための、公式ドキュメントでも推奨されているパターンです。CRITICAL/HIGHの指摘があれば
実装エージェントに自動で差し戻されます。

並列で複数の視点（正しさ用・セキュリティ用・品質用を別々のサブエージェントに分ける等）を
同時に動かす構成も可能ですが、モデル呼び出し回数がその分増えてコストが積み上がるため、
既定では `reviewer` 1回のパスに留めています。

## UIデザインゲート：画面は実装前に「見て」確認する

ブラウザUIを持つアプリでは、設計フェーズで主要画面を**自己完結型HTMLモックアップ**
（`docs/02-design/ui/`、ダブルクリックでブラウザ表示可能）として作成し、
ユーザーが視覚確認してから実装に進みます（`.github/skills/ui-design-mockup/SKILL.md`）。

- 「実装が終わって動かして初めてデザインの問題が分かる」という最も高くつく失敗を、
  設計段階の視覚ゲートで防ぎます。
- **閲覧はブラウザで開くだけなのでトークンコスト・ゼロ**。修正はチャット指示でも
  HTMLの直接編集でも受け付けます。
- コスト配慮: 全画面ではなく主要フロー上の画面＋デザイン基準になる代表画面に絞ります。
  UIの無いアプリ（CLI・APIのみ）ではこのゲート自体をスキップします。
- 承認されたモックアップは画面設計の「正」となり、実装（task-worker）が参照し、
  テスト（Playwrightスクリーンショット）が乖離を検出します。

## 成長ループ：使うたびに賢くなるハーネス

このハーネスは「一度作って終わり」ではなく、**使った経験が次のプロジェクトの入力になる**
仕組みを持っています（詳細は [AGENTS.md](AGENTS.md) の「成長ループ」節と
[.github/skills/harness-retrospective/SKILL.md](.github/skills/harness-retrospective/SKILL.md)）。

1. **教訓ログ（`docs/00-overview/learnings.md`）** — 開発中、エージェントが訂正を受けたり
   同じ失敗を繰り返したりしたら、その場で1行追記される。SessionStartフックが
   このファイルを以後の全セッションに自動注入するため、「言ったのに忘れられる」が起きない。
2. **振り返り（`/10-retrospective`）** — リリース後に実施。摩擦のあった出来事を
   「ハーネス改善 / プロジェクト固有 / 一過性」に分類し、ハーネス改善分は
   対象ファイル・問題・提案・根拠の4点セットの改善提案表にまとめる。
3. **本体への還流** — 改善提案と、開発中に生まれた再利用可能なSkill（`deploy-*`等）を
   このテンプレートリポジトリに適用する（`/90-apply-retrospective` で harness-maintainer
   エージェントが提案を適用し、保護対象のみ人間の保守モード実行を介す。
   `DECISIONS.md` に根拠つきで記録する）。
   以後の新プロジェクトは改善済みの状態から始まる。

## コンテキストロット対策（1タスク1セッションの実現方法）

長い会話ほどモデルの想起精度が落ちる "context rot" という現象がAnthropicの検証で
報告されています。「1タスク1セッション」という経験則はこれへの合理的な対策です。

このハーネスでは、実装フェーズの `implement` エージェントが全タスクを1つの会話で
連続実装するのではなく、**タスクごとに `task-worker` サブエージェントを呼び出して
1タスクずつ独立したコンテキストで実装させます。** `implement` 自身は「次はどのタスクか」を
判断する軽いコーディネーターに徹し、実装の詳細を自分の会話に溜め込みません。
`docs/03-implementation/tasks.md` が唯一の正の状態なので、何らかの理由で会話が
リセットされても、ファイルを見れば同じ場所から再開できます。

この考え方は他のフェーズにも当てはまります。**1つのフェーズ内でも会話が長くなってきたと
感じたら、無理に続けず新しいチャットセッションを開始して `docs/` の関連ファイルを
読み直させる方が、精度の面でも有利です。** ドキュメントに残っている情報は会話をまたいでも
失われません。

## Agent Hooks によるゲート・セキュリティ強制（Preview）

`.github/hooks/gate-hooks.json` と `.github/hooks/security-hooks.json` により、
以下を機械的に補強しています（詳細は [.github/hooks/README.md](.github/hooks/README.md)）。

- `*_template.md` への直接編集を **deny**
- `git push` / `git tag` / `--force` / `reset --hard` / `rm -rf` を **ask**（毎回確認）
- セッション開始時に現在のフェーズゲート状況を自動的にコンテキスト注入
- **ユーザーの依頼のたびに、現在のゲート状況と受付ルーチンのルール
  （分類/入口/影響を冒頭で宣言してから入口に入る）を注入**
  （`route-request`。SessionStart の1回だけだと会話が伸びるほど薄まり、
  ハーネス外の場当たり作業に落ちるため）
- **フェーズ外でのアプリコード編集を deny/ask**（`guard-phase-scope`。どのフェーズも
  `in_progress` でない状態（運用中・未初期化を含む）で `/12-change-request` を経ずに
  コードを触ろうとしたときに止める。「入口に入る」を指示ではなく機械で担保する）
- **記録せずにセッションを終えようとしたら1回だけブロック**（`remind-record`。
  アプリのコードを変更したのに `docs/00-overview/` に何も残っていない状態で
  止まろうとしたとき。成長ループは記録されない限り回らない）
- ハーネス自体の運用ルールに関わる設定ファイルへの編集を **deny**
  （プロンプトインジェクション等による自己権限昇格・ガードレール解除の防止。
  正確な保護対象リストは [AGENTS.md](AGENTS.md) の「セキュリティガードレール」節を
  正とする。`.github/skills/` は動的なSkill追加を許すため原則対象外だが、
  ゲート運用ルールの実体である request-routing / gate-check の中核2スキルは保護対象。D048）
- クラウド認証情報・秘密鍵らしき高確度パターンを **deny**、汎用的な `api_key=...` 等は **ask**

ハーネス本体を保守するとき（このテンプレート自体の改修）は、`permissions.deny` と
`guard-harness-config-edit` フックの**両方**を外す必要があります。
`python tools/harness-maintenance.py --on --apply` が両方をまとめて退避し、
`--off --apply` で戻します（人間が自分のターミナルで実行するツール。
エージェント経由では確認入力に失敗して実行できません）。

ただしPreview機能のため、組織設定や拡張機能のバージョンによっては発火しないことがあります。
その場合でも `AGENTS.md` の指示レベルのルールが効くようにしてあります（二重の安全網）。
さらに `.github/CODEOWNERS`（テンプレート）とブランチ保護を組み合わせることで、
ハーネス自体への変更に人間のレビューを必須にすることを推奨します。

## 再利用パッケージとしての配布（`plugin.json` / `.claude-plugin/plugin.json`）

配布の主経路は archive / intake / 逆同期（`tools/sync-harness.py`。ハーネスのファイルを
プロジェクトへコピーする方式）で、plugin マニフェストはその補助です。マニフェストは
**`plugin.json` の 1 か所が正**で、Claude Code 向けは生成物です（再監査 2026-09-09 CP-7 / A6-23）。

- `plugin.json` — **Agent Plugins 1.0**（2026-08-31 GA。VS Code / Copilot CLI / SDK）の
  マニフェスト。`$schema` と `name` が必須で、トップレベルは version / description / author /
  repository / license / keywords / extensions だけ（それ以外は不可）。版数の正でもある
  （CHANGELOG 規約）。ハーネス固有の情報（agents / hooks / commands の位置、生成物の一覧）は
  `extensions` の逆ドメイン名前空間（`com.github.copilot` / `io.github.n-ima.copilot-sdlc-harness`）に置く。
  1.0 の portable 部品（`skills/`・`mcp.json`）は plugin ルート直下の固定位置だが、本ハーネスの
  skills は `.github/skills/` が正のため、その固定位置には置いていない。
- `.claude-plugin/plugin.json` — **Claude Code plugin** マニフェスト（生成物。
  `python tools/generate-adapters.py` 第6節が `plugin.json` から生成し、`--check` で差分検査）。
  `skills` は `./.github/skills/`、`commands` は `./.claude/commands/`、`agents` は
  `./.claude/agents/*.md` の配列、`hooks` は `./.claude-plugin/hooks.json`（`.claude/settings.json`
  の hooks を `${CLAUDE_PLUGIN_ROOT}` 相対に写した生成物）を指す。
- 検査: `python tools/validate-harness.py`（`plugin.json` のスキーマ・必須キー、`.claude-plugin/plugin.json`
  のパスが `./` 始まりで実在、生成物の鮮度）と `claude plugin validate .`
  （開発機 2.1.201 で exit 0。`--strict` は plugin ルートに CLAUDE.md がある構成では警告で必ず赤になる）。

導入手順（いずれも**実インストールは未検証**。手順は各公式 docs の記載）:

- Claude Code: `claude --plugin-dir <このリポジトリのパス>` でセッション限定ロード
  （marketplace 登録は未整備）。**このリポジトリ（またはテンプレコピー）を直接開くときは使わない**
  （`.claude/settings.json` が同じフックを配線しているため二重発火する。plugin の hooks は
  plugin 経由の配布時だけの配線）。
- VS Code: コマンドパレット「Chat: Install Plugin From Source」に Git リポジトリ URL、
  または拡張機能ビューの `@agentPlugins`。Copilot CLI で導入した plugin（`~/.copilot/installed-plugins/`）
  は VS Code からも見える。
- 制約: `.claude/commands|agents` のポインタは「作業ディレクトリにハーネスのコピーがある」前提で
  書かれており、別リポジトリへ plugin として入れた場合は参照先が無い（`${CLAUDE_PLUGIN_ROOT}`
  への書き換えは未着手）。組織ポリシーで plugin が無効化されている場合もある。

## ディレクトリ構成

```
AGENTS.md                     … 全AIエージェント共通の唯一の指示ファイル（正）
CLAUDE.md                      … Claude Code用エントリポイント（AGENTS.mdをインポート）
DECISIONS.md                   … ハーネス自体の設計判断ログ（改修前に必読）
plugin.json                    … Agent Plugins 1.0 マニフェスト（配布用・版数の正。ハーネス固有情報は extensions）
.claude-plugin/                … Claude Code plugin マニフェスト（plugin.json からの生成物）
.claude/                       … Claude Code用アダプタ（commands/agents/skills=正へのポインタ、settings.json=hooks配線）
.agents/                       … Antigravity用アダプタ（workflows=正へのポインタ）
.github/
  agents/                       … フェーズごとのCustom Agent（旧chatmode）+ reviewer(subagent)
  skills/                       … フェーズで使う手順・チェックリスト（Agent Skills）
                                    release-security-review, skill-authoring 等を含む
  prompts/                      … フェーズを進めるプロンプトファイル
  hooks/                        … ゲート・セキュリティを機械的に補強するフック（Preview）
  workflows/                    … ハーネス自身の整合性CI（validate + フックselftest + eval）
  instructions/                 … パス限定の追加指示（例: docs/** 向け）
  harness/                      … ハーネス自体の使い方文書（USAGE.md・overview.html。
                                    ルートREADMEがアプリ用になってもここに残る）
  CODEOWNERS                    … ハーネス設定への人間レビュー強制のテンプレート
tools/                         … 保守・検証ツール（validate-harness / generate-adapters /
                                    harness-maintenance / sync-harness / intake-app /
                                    effort-report / golden-eval / doctor / freshness-lint /
                                    release-tag / host-canary）
requirements/
  memo.md                       … ユーザーが最初に書く生の要件メモ
docs/
  00-overview/                    … 進捗ダッシュボード（GATE_STATUS）・learnings・変更請求台帳
  01-requirements/                … 要件定義書・非機能要件・用語集・environment.md（デプロイ環境/自動化境界）
  02-design/                      … アーキテクチャ設計・ADR・詳細設計
  03-implementation/              … 実装タスクリスト（task-workerサブエージェントが1タスクずつ実装）
  04-test/                        … テスト計画・テスト結果
  05-release/                     … リリースチェックリスト・CHANGELOG
  06-retrospective/               … 振り返り・effort-report（トークン効率）
```

## 設計方針

- **フェーズごとに人の関わり方を変える**: 要件定義は厚いヒアリング、設計はエージェント主導＋
  最終承認、実装〜テストは全自動、リリースは準備が自動・外部反映は独立検証と人の承認を経て実行＋人手必須部分は人。
  一律に「毎回確認」でも一律に「全部自動」でもなく、フェーズの性質に応じて変える。
- **要件定義がすべての前提**: 特にデプロイ環境・自動化境界（`environment.md`）は
  要件定義フェーズでしか正しく聞き出せない。ここを曖昧にしたまま進めると、
  後工程の自動化が必ず破綻するため、他のどの項目より優先して具体化する。
- **技術スタック非依存**: このハーネス自体は特定の言語・フレームワークに依存しない。
  技術選定は設計フェーズでトレードオフを提示した上でユーザーが決める。
- **エージェントを薄く、手順はSkillへ**: 各エージェントファイルは人格・権限・引き継ぎ先だけを持ち、
  詳細な手順・チェックリストはAgent Skillsに分離する。
- **環境固有Skillは動的に育てる**: `.github/skills/deploy-<environment>/` はハーネスに
  事前収録せず、実際にリリースする環境が判明した時点で `skill-authoring` スキルを使って
  その場で作成する。2回目以降の同じ環境へのリリースはそのSkillにより自動化される。
- **クロスエージェント対応**: `AGENTS.md` を単一の正として、VS Code Copilot以外
  （Copilot cloud agent, Claude Code等）でも同じルールが自然に効くようにする。
- **破壊的操作は都度確認**: push・タグ付け・force系操作・本番デプロイ等は
  `environment.md`の分類に関わらず必ず事前確認する（Hooksによる機械的補強あり）。
- **レビューは別セッション（サブエージェント）で**: 実装した本人がそのまま自己承認しないよう、
  独立コンテキストの `reviewer` サブエージェントが正しさ・セキュリティ・品質を確認してから
  リリースに進む。レビュワーは「合格」をデフォルトにせず反証を試みる懐疑チューニング。
- **レビューの配置は「タスク単位は機械、区切りで意味」**: 実装工程の品質はタスク単位の
  機械的検証（done契約・テスト）で守り、意味的な独立レビュー（`reviewer`）は
  完了タスク10個ごとのチェックポイントと実装・テストの出口（全タスク完了時・テスト完了後）に
  置き、その記録（`docs/04-test/review-log.md`）を done 条件にする。
  「完了は自然言語の主張ではなく決定論的検証に束縛する」「テストで判定できることを
  LLMに判定させない」というAnthropic公式が示すハーネス一般の設計指針と、クリーンコンテキストの
  独立レビューほど精度が高いという業界の実証に基づく配置（タスクごとのLLMレビューは
  コスト増と過剰ゲートを招くため既定にしない。重要案件では明示要求で追加可能。
  出口1回だけの配置では、実運用で `reviewer` が一度も起動しないまま実装が完了した実例が
  あったため、区切りごとの記録つき呼び出しに改めた）。
- **完了は宣言ではなく証拠で判定（done契約）**: 各タスクは着手前に完了条件
  （検証コマンド＋実行時確認）を定義し、その証拠をもってのみ完了にする。
  同一タスクの失敗は retry → replan → escalate の3状態で昇格し、3回失敗で
  自動停止して人にエスカレーションする（無限ループでトークンを浪費しない）。
- **ハーネス自身も検査対象**: 構造検証（validate-harness）・フック自己テスト（件数は
  `selftest.sh` の実行結果が正）・
  Golden Eval（完了宣言と成果物実態の突き合わせ）を本体CIで実行する。
  「機械的強制を説く本体が自分は手動検査」という矛盾を持たない。
- **足すだけでなく引く（de-scaffolding）**: 振り返りでは「足りないガード」と同じ真剣さで
  「モデルの向上で不要になったガード」を問い、陳腐化した足場は削除する。
- **セキュリティは指示だけでなくHooksでも担保**: 最小権限のツール割り当て、
  ハーネス自体の設定への自動編集禁止、シークレットのハードコード検知をHooksで機械的に強制する。
- **役割別モデル/effort 方針を単一ソースから生成して機械強制**: 方針の正は
  `.github/harness/model-policy.yml` の1ファイル（既定は全役割 `inherit`＝メイン会話のモデルを継承。
  判定役 reviewer / spec-critic と上流 requirements / design は effort high を下限に固定し、
  `task-worker` の軽量化は A/B 実測後にのみ）。frontmatter・`permissions.deny`・Copilot CLI 設定・
  下表はそこから生成し、Claude Code では deny（呼出時パラメータの literal）／PreToolUse フック
  （役割表と照合）／PostToolUse の実行モデル記録の三層で守る。
  `model: auto` は Copilot の frontmatter 仕様に無いため使わない（コストを抑えるならピッカーで Auto を選ぶ）。
  サブエージェントの並列多用や、無関係なSkillの読み込みでコストを浪費しない。

<!-- BEGIN GENERATED: model-policy-readme -->
| 役割 | Claude Code model / effort | Copilot VS Code | Copilot CLI effortLevel | 下限固定 |
|---|---|---|---|---|
| `orchestrator` | inherit / inherit | ピッカー継承 | 既定 | — |
| `requirements` | inherit / high | ピッカー継承 | 既定 | never_low |
| `design` | inherit / high | ピッカー継承 | 既定 | never_low |
| `implement` | inherit / inherit | ピッカー継承 | 既定 | — |
| `test` | inherit / inherit | ピッカー継承 | 既定 | — |
| `release` | inherit / inherit | ピッカー継承 | 既定 | — |
| `change` | inherit / inherit | ピッカー継承 | 既定 | — |
| `harness-maintainer` | inherit / inherit | ピッカー継承 | 既定 | — |
| `reviewer` | inherit / high | ピッカー継承 | high | never_low |
| `spec-critic` | inherit / high | ピッカー継承 | high | never_low |
| `task-worker` | inherit / inherit | ピッカー継承 | 既定 | — |

（`.github/harness/model-policy.yml`（取得 2026-09-10）から生成。`inherit` = メイン会話のモデル/effort を継承。上書きの優先順位・劣化モード・単価表は `.github/harness/PLATFORM.md`「モデル/effort の方針」）
<!-- END GENERATED: model-policy-readme -->

詳細な運用ルールは [AGENTS.md](AGENTS.md) を参照してください（特にセキュリティガードレール・
コスト方針・サブエージェント活用方針の節）。

## 既知の制約・要検証事項

- Custom Agents / Agent Skills / Agent Hooks / Agent Plugins は執筆時点でいずれも
  比較的新しい機能（一部Preview）であり、実際にVS Code上で動かして
  フォーマットのズレがないか確認することを推奨します。
- Hooksのstdin/stdoutペイロード形状は公式一次情報から確認した範囲での実装であり、
  実環境での挙動確認・調整が必要な場合があります（[.github/hooks/README.md](.github/hooks/README.md)参照）。
- VS Code Copilot の **Local ハーネス**は `.claude/settings.json` のフック定義を opt-in で
  しか読まず、既定では二重発火しません（D058 実機確認）。一方、現在既定の **Agent Host** と
  Copilot CLI は `.claude/settings.json`・`.claude/skills`・`CLAUDE.md` も既定で走査し、
  同一ガードを重複排除なし・直列で実行します（再監査 2026-09-09 CP-3）。本ハーネスは
  `.vscode/settings.json` の `chat.hookFilesLocations` / `chat.useClaudeMdFile: false` で
  読込元を固定して抑止します（Agent Host での実機確認は未了。ガードは冪等のため二重でも
  実害は警告・ログの重複に留まります）。
- **2026-08-31 の独立再監査（41エージェント・27観点）で 177 件の指摘**を受けています
  （[audits/external-reaudit-2026-08-31.md](audits/external-reaudit-2026-08-31.md)）。
  うち CRITICAL 2 件（本体 CI が依存宣言の欠落で導入以来一度も成功していない／
  設定ガードがインタプリタ経由の書き込みを検知しない）は監査者が実測再現済みで、
  下記ロードマップの P0 として最優先で修正します。個別プロジェクトのアプリ開発機能
  （フェーズゲート・done契約・変更管理）はローカル検査・実機 E2E で検証済みのまま
  利用できます。

## 現在地とロードマップ（2026-08-31 時点）

**現在地**: version 1.2.0・設計決定は [DECISIONS.md](DECISIONS.md)（件数は COMPARISON §6 の生成行）・Copilot / Claude Code の
2環境で実機 E2E 完走・主戦場 A/B（曖昧要件+変更要求+回帰）で素の Claude Code に
2/2 vs 0/2（参考値: n=2・装置 v1・比較条件不成立。v2 で n=5 をやり直す＝A6-16）・
公式ビルトインとの使い分けを実測で確定（セキュリティ検出は委譲、
独立レビューは維持）。独立監査は4回実施し、最新の再監査が「applied 記録と実適用の乖離」
という構造欠陥を指摘したため、以下の順で消化します（全指摘の台帳管理は
[audits/PROPOSALS.md](audits/PROPOSALS.md)）。

1. **P0 地固め（2026-09-10 に主要項目を適用＝D072 / D079 / D080。CI は初めて全ジョブ緑 run 34406967789。残: 開発機の版更新）** — CI を実際に緑化し「緑を確認するまで applied と
   記録しない」規律を導入／設定ガードのインタプリタ・git plumbing・junction 経由の
   書き込みバイパスを封鎖／保護域ガードの入力フィールド網羅（uri・notebook・パッチ経由）／
   Windows 側 fail-open（バッククォート行継続・8.3 短縮名）の修正。
2. **P1 構造的品質保証（「N面のうち1面だけ直る」の根絶）** — 説明用 HTML 6本と主要な
   数値言及を `tools/gen-docs.py` の生成対象に拡張し、validate で一次データ実数との
   突合を必須化／秘密検知パターンの現代化（npm token・JWT・Azure）／brownfield 導入
   直後の誤検知解消／太くなったプロンプト（/99・/07・/09・/11）の薄化。
3. **P2 世界最高の証明・最新機能追従** — 評価装置の対等化（アーム共通予算・model 記録・
   トラジェクトリ保存）の上で A/B を統計的に成立する形でやり直し（n=5・完全分離で
   Fisher 両側 p=0.008＝再監査 2026-09-09 §6・pass^k＝k回
   走らせて k 回とも成功したときだけ成功と数える指標・他ハーネス Spec Kit / OMC との直接比較）／常駐指示の 200 行未満化（`.claude/rules/`
   への分担）／委譲エンフォーサ（A3-8。モデル方針は deny／PreToolUse(Agent) の役割表照合／PostToolUse の
   実行モデル記録の三層で実装済み＝A6-10。`SubagentStart` はブロック不可・model 無しのため
   使わない。残るは agents 最小権限の機械強制）／プラグイン配布の
   標準スキーマ準拠／long-running agent 公式パターンの E2E への適用。
4. **P3 検証の空白の解消** — Copilot 実機フック発火の回帰検証・フックレイテンシ計測・
   git 履歴のシークレットスキャン・HTML 実描画/アクセシビリティ検収など。

## 位置づけ

本ハーネスは spec-as-source（spec だけを編集し、コードは常に spec から再生成する）では
なく **spec-anchored** です。spec（docs/）を錨としつつコードも第一級の成果物として扱い、
フェーズゲートと収束ループ（`/12-change-request` の差分駆動、`/13-converge` の定期監査）で
spec とコードを相互に整合させ続けます。

## ライセンス

MIT License（[LICENSE](LICENSE) 参照）。
