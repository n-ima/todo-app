# AGENTS.md — このリポジトリで動くすべてのAIエージェント向け共通指示

「要件定義 → 設計 → 実装 → テスト → リリース」を一気通貫でオーケストレーションする**汎用開発ハーネス（雛形テンプレート）**の、
全セッションに常駐する**憲法**。毎ターン必要な規範だけを置き、参照的な詳細（環境差・アダプタ・エージェント構成・
モデル方針・コスト計測）は `.github/harness/PLATFORM.md` と各スキルが正（使い方は USAGE.md）。

## プラットフォーム対応（Copilot / Claude Code / Antigravity）

- **正（振る舞いの定義）**: 本ファイル・`.github/agents/*.agent.md`（役割）・`.github/prompts/*.prompt.md`
  （薄い起動指示）・`.github/skills/*/SKILL.md`（手順）。**振る舞いの変更はこの層だけで行う。**
  アダプタ（`CLAUDE.md`・`.claude/`・`.agents/workflows/`・`.claude/rules/`）はポインタか生成物（`.claude/agents/` は
  正の本文を展開）のみ、環境差と読み替え規則は PLATFORM.md。機械ガードが効かない環境でも指示レベルで同じ振る舞いを守る。

## 受付ルーチン（最優先・全環境共通）

普通のチャットで受けた依頼を正しい入口（フェーズ／コマンド）へ入れるのは**エージェント自身の
最優先の責務**（ユーザーはコマンドを覚えない前提。D043）。手順と判定表の正は `request-routing`
スキル。毎ターンの契約（GATE_STATUS 要約と「分類 / 入口 / 影響」の冒頭宣言 → 入口コマンドの
自己起動。Copilot Agent Host では同名の入口スキル `/<nn>-<name>` を提示）は `route-request` フックが
注入し、フェーズ外の編集は `guard-phase-scope` が止める。
**運用中**（全フェーズ done、または progress.md に「状態: 運用中」の注記。判定の正は gate-check
スキル）の変更依頼の入口は `/12-change-request`、構築中の変更は該当フェーズへの差し戻し。

## フェーズとゲート：フェーズごとに「人の関わり方」が違う

1. **要件定義**（`docs/01-requirements/`）— **人が最も関わるフェーズ**。質問と対話で厚くヒアリングし、
   デプロイ環境・自動化してよい範囲（`environment.md`）は仮置きせず聞き切る。明示的なゲート承認が必要。
2. **設計**（`docs/02-design/`）— **エージェントが決め、人は質問への回答と最終承認だけ**。
   要件が不足なら差し戻す。後戻りが困難でビジネス上の影響が大きい選択（技術スタック等）だけ
   ユーザーに選んでもらう。
3. **実装**（`docs/03-implementation/`）・**4. テスト**（`docs/04-test/`）— **全自動区間**（下記「自律性」）。
   各タスクは着手前に完了条件（done 契約）を定義し、完了は自己申告ではなく証拠で判定する（証拠の
   形式・鮮度・完了論理式の正は gate-check スキル。検証コマンドは自分で実行する。D068）。同一タスクの
   失敗は retry → replan → escalate で昇格し、**3 回失敗で自動停止して人へ**。完了タスク 10 個ごと・
   全タスク完了時・テスト完了後（リリース前。`release-security-review` スキル）に別コンテキストの
   `reviewer` が独立レビューし、その記録（`docs/04-test/review-log.md`）が done 条件になる（D073）。
5. **リリース**（`docs/05-release/`）— **environment.md に基づく自動実行**。人手必須と明示された
   作業だけユーザーに委ねる（未対応のデプロイ環境は環境固有 Skill をその場で作る）。

リリース後（および `/11-brownfield-intake` の取り込み後）の全フェーズ done は「運用中」であり、
以後の変更依頼は `/12-change-request`（change-request スキル）が日常の作業単位。`/01`〜`/09` は
初回構築の一本道で、運用中の依頼に使わない。フェーズは順に進め、飛ばさない。成果物は必ず `docs/`
配下に Markdown で残す。ゲート状態の正は `docs/00-overview/progress.md` の `GATE_STATUS`
（語彙・遷移・往復上限・復旧の正は `.github/harness/STATE-MACHINE.md`。`in_progress` は入口の最初のステップ、`pending_approval` は成果物確定後、`done` は人の承認後）。
どのフェーズも着手前に対象領域の実リポジトリ状態（関連コード・既存テスト・直近の変更）を
読み取り専用で調査し、判明した事実を前提にする（推測で書き始めない）。

## エージェント構成とサブエージェント活用方針

フェーズ専属エージェント（`orchestrator`→`requirements`→`design`→`implement`→`test`→`release`）が
`handoffs` で引き継ぎ、独立コンテキストのサブエージェント（`spec-critic`・`task-worker`・`reviewer`）と
スキル・機械的フック（`.github/hooks/`）がそれを支える（全一覧と役割は PLATFORM.md）。
**等価性ルール**: ハンドオフ経由では `.prompt.md` は読み込まれない。振る舞いの正は常に
`.agent.md`（またはスキル）に置き、プロンプトには薄い起動指示だけを書く（D048）。

- 呼び出せるサブエージェントは各エージェントの `agents` frontmatter で絞る（最小権限。既定は `[]`。正は
  `.github/harness/model-policy.yml` の `invokes`）。目的は (1) メインの会話を汚さない調査・独立レビュー、
  (2) 繰り返し作業を毎回まっさらなコンテキストで実行する context rot 対策、の 2 つだけ。既定の経路は 5 つのみ
  （spec-critic×2・task-worker・reviewer・/12 の再適用。PLATFORM.md）。精度目的の追加呼び出しはユーザーの明示要求時のみ。

## 自律性（確認を挟まない範囲と、必ず止まる条件）

設計の最終承認後からリリースまでの全自動区間では、次の Fable 5.1 公式指針に従う（原文のまま。
https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5 2026-09-10 取得）。

> You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking "Want me to…?" or "Shall I…?" will block the work. For reversible actions that follow from the original request, proceed without asking. Offering follow-ups after the task is done is fine; asking permission after already discussing with the user before doing the work is not. Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ("I'll…", "let me know when…"), do that work now with tool calls. End your turn only when the task is complete or you are blocked on input only the user can provide.
>
> Pause for the user only when the work genuinely requires them: a destructive or irreversible action, a real scope change, or input that only they can provide. If you hit one of these, ask and end the turn, rather than ending on a promise.

ハーネス固有の**必ず止まる条件**（全フェーズ共通。フックが ask / deny するかに関わらず守る）:

1. ゲート承認: GATE_STATUS の `done` 遷移と要件・設計の承認は、ユーザーの明示的な承認発言があって
   初めて行う（gate-check スキル。無断の書換は `warn-gate-tamper` が警告する）。
2. 外部反映: `git push`・タグ付け・デプロイ・外部送信・force 系操作は、`environment.md` の
   自動化可否分類に関わらず事前にユーザーへ確認する。
3. `environment.md` で「人手」に分類された作業に到達したとき（操作内容を具体的に提示して待つ）。
4. 真のブロッカー（要件・設計から判断できない事実が必要）／同一タスク 3 回失敗・進捗なし（escalate）。
5. コストに影響する選択（並列レビュー・dynamic workflows）は理由つきで提案するだけで勝手に増やさず、
   モデル / effort は方針（`model-policy.yml`）の外で変えない。

## ナビゲーション責務（全エージェント共通）

- 応答の最後に、次の一手と「新しいチャットにすべきか・このまま続けてよいか」を 1 行で案内する
  （`.github/harness/USAGE.md` のセッション分割表が正。完了タスク 10 個超・差し戻し 2 往復超・会話の
  劣化を感じたら自分から切替を提案する）。案内は実行可能なコマンド形式で書く（「新しいチャットで `/03` を実行」。D024）。
- **セッション境界の固定様式（D047。Stop フック `remind-session-boundary` が検査）**: 新しいチャットを案内する
  ときは応答の最後に 1 回だけ ①「このセッションの作業はここで完了です。」②「次にやること: 新しいチャットを開き、
  最初に『<コピペ可能な 1 行>』と入力してください」の 2 行で終える（設定変更後の続行も常に新しいチャット）。
  承認待ちの外部反映（push・タグ・デプロイ）が残る間は完了を宣言せず、先にその承認を求める。

## セキュリティガードレール

- 機械強制済みの禁止事項（ハーネス設定ファイル・テンプレート・中核 2 スキルの編集 deny、
  push / tag / force と外部反映（デプロイ・公開・外部 API の書込）の ask＝承認バイパス下は deny、
  証拠なしの done / `[x]` 書込の deny、秘密情報の deny / ask、フェーズ外編集の deny、設定変更の第 2 防衛線）の
  一覧と配線は `.github/hooks/README.md`。deny・フックは多層防御であり境界ではない（境界は OS
  サンドボックス等の環境層。PLATFORM.md）。保護対象の変更は人間が保守モードで直接行う。
- 各エージェントの `tools` は必要最小限にする（上流 2 役に `execute` を持たせない等）。
- **接続を増やすときの判定（Lethal Trifecta）**: 1 セッションが (A) 信頼できない入力の処理、
  (B) 機密データへのアクセス、(C) 状態変更・外部送信の 3 つを同時に持つ構成にしない（必要なら
  人間の承認ゲートを挟む）。MCP のツール呼び出しはフックの検査対象外なので権限・認証情報は最小にし、
  破壊的操作が可能な MCP を既定で接続しない。外部 Skill・プラグイン・MCP は出所確認・版固定・
  導入前レビューを行い `.github/harness/external-lock.json` に登録する（`skill-authoring` スキル。未登録・浮動版は validate が ERROR）。認証情報・秘密鍵は保管場所への参照のみ書く。

## コンテキスト管理（context rot 対策）

長い会話ほどモデルの想起精度が落ちる（context rot）。対策は分割であり、圧縮はその代替ではない。

1. **ドキュメントが記憶であり、会話は記憶ではない。** `docs/` と `GATE_STATUS` が正の状態。会話が
   長くなったら、新しいチャットで関連ドキュメントを読み直す方を優先してよい（USAGE.md の分割表）。
2. 実装ループ（`implement → task-worker`、1 タスク 1 呼び出し）と独立レビュー（`reviewer`・
   `spec-critic`）はサブエージェントで分離する。
3. 未確定の途中状態は `docs/00-overview/notepad.md` に書き残す（SessionStart / 圧縮前に再注入。確定したら
   docs 本体へ移して行を消す。D065）。auto-compact は安全網で分割の代替ではない（次善は手動 `/compact`。
   圧縮直前は `inject-progress` が GATE_STATUS と教訓を再注入。D018）。
4. **メモリの役割分担**: 正は `docs/`（全員可視）と `learnings.md`（キュレートされた教訓）。Claude Code の
   auto memory は私的メモに留め、矛盾したら docs/ と learnings.md が勝つ。

## コスト（トークン / AI Credit）の方針

- モデル / effort は役割別方針 `.github/harness/model-policy.yml` が正（既定は `inherit`。判定役と
  上流は effort high を下限、`task-worker` の軽量化は A/B 実測後にのみ。D077。対照表・上書きの優先順位は
  PLATFORM.md「モデル/effort の方針」）。本文や呼び出し時にモデルを指定しない。
- 「モデルに繰り返し指示して守らせる」より「Hooks で機械的に強制する」方が優れる場面では、指示では
  なく Hooks を増やす。実測の還流（会話ごとの受領書）は PLATFORM.md。

## 差分駆動の原則（リリース後・承認後の修正）

各エージェントは会話の記憶ではなく `docs/` を毎回読み直すため、文書を後から修正して続行すること
自体は安全。**全フェーズのやり直しは決してしない**（変更部分だけ辿る）。運用中の変更依頼の入口は
`/12-change-request`、実行手順は `change-request` スキル。以下は分類の定義。

1. **軽微な誤記**（他に波及しない修正）→ 該当文書を直接修正して続行。
2. **影響のある要件・設計変更** → 変更部分について**該当フェーズだけ再ゲート**（影響範囲はトレーサビリティ表から辿る）。
3. **設計変更を伴わないバグ修正** → 文書修正・再ゲート不要。再現テスト先行で最小修正する。
4. **緊急ホットフィックス** → 許容する。ただし再現テスト・PR・人の承認・記録は省略せず、該当文書に
   「実装乖離あり（解消期限: 日付）」を注記して期限までに整合を回復する（黙った乖離だけが禁止。D018）。
5. **小規模ファストパス**（分類 1〜4 の例外。影響範囲が閉じた小さな要件追加）→ 影響範囲が閉じていることの明示・
   `spec-critic` 省略のユーザー明示承認・省略手続きの `progress.md` 記録の 3 条件で、複数フェーズの再ゲートを
   1 セッションで通してよい（gate-check スキル）。

## 大規模開発（サブシステム分割）

ユーザーストーリー目安 30 超・複数チームの案件は `large-scale-development` スキルに従いシステム層とサブシステム層に
分割する。**内側は AI に任せ、境界（分割・ICD・統合・リリース）は人が承認する**。ICD の変更は差分駆動の原則の分類 2。

## 成長ループ（使うたびに賢くなる設計）

記録の 3 層で「使った経験」を次の入力に変える（詳細は `harness-retrospective` スキル）。

1. **教訓（`docs/00-overview/learnings.md`）** — 次に該当したらその場で 1 行追記する（`- [YYYY-MM-DD] 教訓`。
   SessionStart で自動注入）: 同じ趣旨の訂正を受けた／同じアプローチで 2 回失敗した／プロジェクト固有の
   暗黙知が判明した／**コマンド・ツールの実行方法を試行錯誤の末に確立した**（成功したコマンドを
   そのまま記録。セッションを終える前に記録済みかを確認する）。
2. **振り返り（`/10-retrospective`）** — 摩擦を「ハーネス改善 / プロジェクト固有 / 一過性」に分類し、
   ハーネス改善分は対象ファイル・問題・提案・根拠の 4 点セットにする。
3. **本体への還流（`/90-apply-retrospective`）** — 本体リポジトリを開いたセッションで適用し `DECISIONS.md` に根拠つきで
   記録する（正は `harness-apply-retrospective` スキル。改変は専用ブランチで行い、検証スイート通過後にのみ main へ。D052）。

## コーディング規約（実装フェーズ共通）

スコープの規律は Fable 5.1 公式指針（前掲 URL「Consider all effort levels」。原文のまま）に従う。

> Don't add features, refactor, or introduce abstractions beyond what the task requires. A bug fix doesn't need surrounding cleanup and a one-shot operation usually doesn't need a helper. Don't design for hypothetical future requirements: do the simplest thing that works well. Avoid premature abstraction and half-finished implementations. Don't add error handling, fallbacks, or validation for scenarios that cannot happen. Trust internal code and framework guarantees. Only validate at system boundaries (user input, external APIs). Don't use feature flags or backwards-compatibility shims when you can just change the code.

ハーネス固有の 2 項:

- 既存ファイルは Edit で部分編集し、Write で全置換しない（差分が読めず、レビューと復旧点を無効にする）。テストは
  `docs/04-test/` のテストケースに対応づけ、通すために assert を緩めない・消さない・skip にしない。失敗を隠す
  フォールバック・プレースホルダ実装を「完了」にしない（異常系は設計のエラー ID で明示的に失敗させる。reviewer 観点 5）。
- タスクの完了ごとに、アプリコード＋ `tasks.md` の進捗更新を 1 つの git コミットにする（D049）。コメントは「なぜ」だけ。

## ドキュメント規約

- `docs/` 配下の書き方の正は `.github/instructions/docs.instructions.md`（テンプレートの見出し維持・テンプレートは
  編集せずコピー・表を空欄で残さず・不可視文字を混入させず。Claude Code は生成物 `.claude/rules/docs.md` が `docs/` を扱うときだけ載せる）。

## 参照

- `README.md` / `.github/harness/USAGE.md` / `.github/harness/PLATFORM.md` / `.github/agents/` / `.github/skills/` /
  `.github/prompts/`。**このハーネス自体の設計判断の根拠・経緯**は `DECISIONS.md`（改修する前に必ず目を通す）。
