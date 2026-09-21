# ハーネス総点検レポート（2026-08-11）

前回の鮮度監査（D030・2026-07-06）以降の世の中の変化と、D031〜D045で拡張された現行構成を
突き合わせた総点検。調査は4系統を並列実施した:
(1) Claude Code 公式（docs/changelog/engineeringブログ）、(2) GitHub Copilot 公式
（docs.github.com / VS Code docs / changelog）、(3) コミュニティのハーネス・
ループエンジニアリング動向（Ralph系・Spec Kit/Kiro/BMAD/OpenSpec・著名実践者）、
(4) 本体リポジトリの内部整合性監査（読み取り専用。validate-harness.py 実行結果: 0エラー）。

このレポートは `/90-apply-retrospective` の改善提案表（対象・問題・提案・根拠の4点セット）
形式に準拠しており、そのまま還流適用の入力にできる。

---

## 1. 総評

**アーキテクチャの根幹は2026年8月時点の実証済みベストプラクティスと一致しており、
複数の領域で公開されているどのハーネスよりも先行している。**
特に「成長ループの本体還流」「差分駆動の変更管理」「上流独立レビュー」は公開等価物が無いか、
今年になって外部で実証された設計を先取りしている。

一方で、(a) 機械ガードの実装穴（PowerShellツール迂回等）、(b) 本体自身のCI不在、
(c) Copilot側の大変化（フックイベント拡充・課金と計測の変化・prompt files の戦略的廃止方向）
への未追随、(d) タスク単位の実行時検証（2026年に確立した evaluator/done契約パターン）の
不在、が「世界最高」を名乗る上での要修正点として特定された。

---

## 2. 明確な優位点（外部比較で裏付けられたもの）

| # | 優位点 | 外部比較・裏付け |
|---|---|---|
| S1 | **成長ループ3層 + 本体還流 + D番号決定ログ**（learnings自動注入 → /10振り返り → /90本体適用 → /91逆同期） | 公開等価物なし。最接近は Every の compound engineering（教訓の書き戻しステップ）だが、「テンプレート本体へ還流し新プロジェクトが改善済みから始まる」層と、根拠つき決定ログ（DECISIONS.md）を持つ公開ハーネスは無い。本ハーネスの最大の差別化要素 |
| S2 | **差分駆動の変更管理（/12 + トレーサビリティ再ゲート + 台帳）** | Spec Kit は greenfield 偏重・反復に弱いと批判されている（Scott Logic 2025-11）。delta spec を持つ OpenSpec と同等以上で、「該当フェーズだけ再ゲート」まで規定するものは公開に無い |
| S3 | **フレッシュコンテキスト独立レビュー（spec-critic×2 + reviewer）** | Cognition「Multi-Agents: What's Actually Working」(2026-04): クリーンコンテキストのレビューエージェントは PR あたり約2バグ・58%が重大を検出し、**共有コンテキストが無い方が精度が高い**と実証。本ハーネスの設計そのもの。上流（要件・設計）への配置はさらに費用対効果が高く、公開ハーネスでは希少 |
| S4 | **単一スレッド書き込み + 文書媒介**（並列書き込みスウォームを採らない） | 2026年の最も決着した論点。Anthropic/Cognition/LangChain が「オーケストレータ+隔離サブエージェント、書き込みは単線」に収束。D034 の判断は外部で追認された |
| S5 | **hooks-over-instructions + 劣化モードの正直な文書化** | Claude Code 公式 best practices の現行ドクトリン「強制すべきルールはフック/permissions、知識はスキル、常時指示は短い CLAUDE.md」と完全一致。3環境の強度差を明文化する公開例は無い |
| S6 | **docs-as-memory + セッション分割表** | Anthropic harness design (2026-03) の「compaction よりフルコンテキストリセット」知見と一致。HumanLayer の実測（コンテキスト40%超で想起劣化=ダムゾーン）が分割ルールの定量的裏付けになる |
| S7 | **mutation-verification スキル** | Meta ACH（FSE 2025、Messenger/WhatsApp/Instagram に実運用）で実証された手法。テンプレートとして持つ公開ハーネスはほぼ無い |
| S8 | **effort-log トークン計測** | 「ハーネス自体を測定変数として扱う」は 2026 年の新潮流（Anthropic の infra-noise 論文）。個人規模で工程・エージェント・モデル別計測を実装済みの公開例は無い |
| S9 | **EARS + requirements/design/tasks 三点セット** | Kiro（GA 2026-03、EARS採用）が業界標準化した spec の形と同型。D015 の先行採用が正解だったことが確定 |
| S10 | **受付ルーチン（UserPromptSubmit毎ターン注入）+ 入口の自動起動** | 「ユーザーはコマンドを覚えない前提」を機械的に強制する公開ハーネスは無い（Spec Kit も BMAD もコマンド暗記前提） |
| S11 | **brownfield intake + 逆同期** | 実務の多数派（既存コードベース）対応。Spec Kit /speckit.converge と同発想を D031 で先行実装済み |

---

## 3. 改善提案表

凡例: 保護=適用に人間の保守モード（`tools/harness-maintenance.py --on --apply`）または
適用スクリプト実行が必要。非保護=エージェントが直接適用可。

### P0: 機械ガードの実穴・本体整合性（安全性。最優先）

| # | 対象ファイル | 現状の問題 | 提案 | 根拠 |
|---|---|---|---|---|
| P0-1 | `.claude/settings.json`（保護） | PreToolUse の Bash マッチャーと `permissions.ask`（`Bash(git push:*)` 等）が **PowerShell ツールを対象にしていない**。この開発機で実際に PowerShell ツールが露出しており、`git push`・シークレット混入コマンドがガード・ask とも素通りする | マッチャーを `Bash\|PowerShell` に拡張し、ask に `PowerShell(git push:*)` 等の対を追加。ガードスクリプトは tool_input.command を同様に検査（形式は同じ） | 内部監査C-1。「Claude Code が3環境で最強」という AGENTS.md の主張の根幹に関わる穴 |
| P0-2 | `.github/workflows/`（保護・新設） | 本体に CI が無く、`validate-harness.py`（0エラー確認済み）と `selftest.sh`・`log-effort.py --selftest` が手動実行頼み。「指示よりフックで機械的に強制」を説くハーネス自身の整合性が人間の記憶頼み | `harness-ci.yml` を新設し push/PR で 3 つの自己検査を実行 | 内部監査G-1。ドッグフーディング不在は思想との自己矛盾 |
| P0-3 | `.github/hooks/scripts/guard-dangerous-git.*`（保護） | `git -C <path> push`・`git --git-dir=... push`・`rm -fr`・`rm -r -f` がパターンを迂回 | パターン追加（bash/ps1 両系統）。selftest.sh に回帰ケース追加 | 内部監査C-8 |
| P0-4 | `.claude/settings.json`・`remind-record.py`（保護） | NotebookEdit がガードマッチャーと deny の対象外。remind-record も `notebook_path` を読まない | マッチャーに NotebookEdit 追加（または deny でツール自体を塞ぐ）、remind-record に notebook_path 対応 | 内部監査C-3 |
| P0-5 | `.claude/settings.json`（保護） | Stop/SessionEnd フックが `python` 直書き。素の Linux/macOS では remind-record と effort 計測が**無言で全滅**する（fail-open） | `python3` フォールバック付きの起動（例: シェルラッパ or `python3 ... \|\| python ...`） | 内部監査C-10。Windows 以外への展開時の実害 |
| P0-6 | `.github/hooks/scripts/warn-stale-gate.*`（保護） | AGENTS.md は「ゲート承認済み文書の編集で警告」と主張するが、実装は5ファイルのみ監視。nfr.md・environment.md・detailed-design/・ADR・ICD の編集は警告されない | 監視対象をフェーズ配下のパターンに拡大（フェーズ→パスプレフィクス対応にする） | 内部監査C-2。主張と実装の乖離 |
| P0-7 | `.github/hooks/scripts/guard-phase-scope.*`（保護） | 除外パターンが緩い部分文字列一致（`*docs/*`・`*tools/*`・`*README.md`）のため、`app/src/tools/x.ts` 等のアプリコードがフェーズガードを素通り | 除外をリポジトリルート相対の前方一致に厳密化 | 内部監査C-4 |
| P0-8 | `.vscode/settings.json`（保護）ほか | VS Code Copilot が `.claude/settings.json` のフックを**ネイティブ解釈**するようになった（公式docs 2026-08-05時点）。`.github/hooks/` の同一スクリプトと**二重発火**する恐れ | 実機で二重発火を検証し、発火するなら片系を無効化する設定を追加（スキル二重読込を D030 で塞いだのと同型の対応） | Copilot公式調査。未検証のまま放置すると警告2重化・ログ2重記録 |
| P0-9 | `.claude/settings.json` または `guard-harness-config-edit`（保護） | `.github/prompts/**` が deny・フックのどちらでも保護されていない。プロンプトは正レイヤ（起動指示）でありインジェクションで書き換え可能 | 保護対象に追加（P3-1 の prompts→skills 移行方針と併せて決定） | 内部監査C-7。スキル除外は文書化された意図だがプロンプト除外は無記載 |

### P1: 鮮度更新（外部変化への追随。記述が事実と乖離）

| # | 対象ファイル | 現状の問題 | 提案 | 根拠 |
|---|---|---|---|---|
| P1-1 | `AGENTS.md` ガードレール強度差の節（保護） | 「Copilot には UserPromptSubmit 相当が無い」「Stop 相当も無い」が**古い**。VS Code Copilot は現在8イベント（SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/PreCompact/SubagentStart/SubagentStop/Stop）をサポート（Preview） | 節を書き換え、`.github/hooks/` に route-request / remind-record 相当を配線して Copilot でも受付・記録を機械化する | Copilot公式調査（VS Code hooks docs 2026-08-05）。「Claude Code のみが最強」の前提が変化 |
| P1-2 | `AGENTS.md` コスト節・`DECISIONS.md` D040 補記（保護/非保護） | Copilot の課金は 2026-06-01 に premium request 廃止・**AI Credits（トークン従量）へ移行**済みで、セッション別トークン/コストの可視化も実装済み。「Copilot はトークン数非開示」という劣化モードの記述が事実と乖離 | コスト節を現状に合わせ更新。将来課題として effort-log の Copilot データソース化を記録 | Copilot公式調査（github.blog 2026-04-27 ほか） |
| P1-3 | ハーネス各文書（非保護中心） | 「Copilot coding agent」が「**cloud agent**」に改名（2026-04-01）。cloud agent は現在 AGENTS.md・skills・`.github/hooks` を honor する（ハーネスがクラウド実行面でも効く） | 呼称更新と、cloud agent が第3の実行面になり得る旨の追記 | Copilot公式調査 |
| P1-4 | `README.md`・`.github/harness/USAGE.md`（非保護） | (a) README フェーズ表に /91 が無い (b) ディレクトリツリーに tools/・DECISIONS.md・docs/06-retrospective/ が無い (c) USAGE セッション分割表に /11・/91 の行が無い (d)「2026年7月時点」表記の更新 | 記載を現状に合わせる。「最終検証日」の慣行を導入 | 内部監査F-1〜F-4 |
| P1-5 | `.github/agents/*.agent.md`（保護・確認のみ） | Copilot の `infer:` フロントマターが廃止（`user-invocable`/`disable-model-invocation` に分離）。モデル廃止（2026-09-01: Claude Sonnet 4.5/4.6・Opus 4.5/4.6 等）も予定 | `infer:` 使用有無と model ピンを点検（validate-harness.py に検査追加も可） | Copilot公式調査 |

### P2: 作られるアプリの品質を上げる（2026年に確立した手法の取り込み）

| # | 対象ファイル | 現状の問題 | 提案 | 根拠 |
|---|---|---|---|---|
| P2-1 | `docs/03-implementation/tasks_template.md`・`implement`/`task-worker` agent（保護） | タスクの「完了」がテスト通過中心で、**タスク単位の実行時検証（done契約）**が構造化されていない。フェーズ末のテスト・レビューまで実行時欠陥が滞留し得る | tasks.md に「完了条件（検証コマンド+実行時確認手順）」列を必須化し、task-worker は完了マーク前に実行時証拠（テスト+起動/操作確認）を要求。実装前に done 契約を確定（交渉）してから書く | Anthropic「Harness design for long-running app development」(2026-03-24) の evaluator/sprint contract。Boris Cherny「自己検証手段を与えると品質2〜3倍」。ソロ実行$9=壊れたアプリ vs フルハーネス$200=完動アプリの実測 |
| P2-2 | `task-worker.agent.md`・`reviewer.agent.md`（保護） | 2026年に語彙化された失敗モードへのチェックが無い: (a)「未実装」と思い込んで再実装（Ralph の search-before-assuming）(b) **落ちるテストの削除・無効化**（Kent Beck が実例報告）(c) フォールバック塗れの防御コードで不変条件違反を隠蔽（Ronacher「The Coming Loop」）(d) プレースホルダ実装 | task-worker に「実装前に必ずコードベースを検索」を明記。reviewer のチェックリストに (b)(c)(d) を追加 | コミュニティ調査 §1・§4。いずれも実害の報告がある具体的失敗モード |
| P2-3 | `implement.agent.md`（保護） | ノンストップ全自動区間に**サーキットブレーカ**が無い（同一タスクの失敗ループを検知して止まる規定が無い） | 「同一タスク3回失敗で自動停止し、状況要約つきで人間へエスカレーション」を明文化（差し戻し2往復超ルールの実装フェーズ版） | Ralph エコシステムの circuit breaker / 一晩ループの実務報告「検証ゲートと反復上限が無いと劣化する」 |
| P2-4 | `gate-check` スキル + フック（一部保護） | GATE_STATUS・tasks.md の完了フラグが Markdown で、エージェントが「通すために書き換える」ことを構造的に防げない | 全面 JSON 化はせず（人間可読性を優先）、**フックで GATE_STATUS ブロックと tasks.md の完了マーク変更を検知して ask/warn する**折衷案を採る | Anthropic (2025-11)「JSONはモデルが要件を書き換えて合格にすることを防ぐ」。折衷の理由: docs-as-memory の人間可読性は本ハーネスの根幹 |
| P2-5 | `AGENTS.md` または新スキル（保護/非保護) | Claude Code の auto memory（既定有効）と learnings.md の**役割分担が未定義**。矛盾したら片方が勝つ根拠が無い | 分担を明文化: learnings.md=キュレート済み・全員可視・クロスプラットフォームの正。auto memory=Claude の私的メモ。矛盾時は learnings.md 優先 | Claude Code 公式調査 §4。両方が並走する現状は放置すると不整合の温床 |
| P2-6 | `docs/06-retrospective/retrospective_template.md`（保護） | 「足りないガードは何か」は問うが「**不要になったガードは何か**」を問わない | de-scaffolding の常設問を追加（モデル向上で外せる足場の点検） | Anthropic (2026-03)「モデルが改善したらハーネスの複雑さを外せ」。2026年の監査は「何が冗長になったか」も問うべき |
| P2-7 | `.github/harness/USAGE.md`（非保護） | セッション分割の閾値が経験則としてのみ記載 | HumanLayer の実測（コンテキスト40%超で想起劣化）を根拠数値として追記 | コミュニティ調査 §4（10万セッション分析由来） |

### P3: 中期戦略（方針決定が必要。個別に計画してから着手）

| # | 対象 | 検討事項 | 根拠 |
|---|---|---|---|
| P3-1 | プロンプト層全体 | **prompts→skills 移行**: Copilot の Agent Host セッション・cloud agent・CLI は prompt files を読まず、VS Code は移行ツールを提供開始。skills が全実行面共通の起動単位に収束中。Claude 側は既にコマンド=スキルであり、正レイヤの入口を skills に寄せる再編を計画する | Copilot公式調査 §5。アダプタ3層構成の簡素化にもつながる |
| P3-2 | `AGENTS.md` 分割 | **constitution（憲法）ファイルの分離**: Spec Kit で最も模倣されたアイデア。現在 AGENTS.md に混在する「不変原則」と「運用手順」を分けるか検討 | コミュニティ調査 §2.1 |
| P3-3 | 国際化・展開 | 全文書が日本語のみ（採用の上限）。Antigravity の Deny List 設定を検証するチェックリストの機械化。Copilot での粗い工数代理計測（AI Credits ベース） | 内部監査G-5/G-9、Copilot公式調査 §7 |
| P3-4 | tools/ のテスト | sync-harness.py・intake-app.py は破壊的隣接操作を持つのにテストが無い（dry-run 既定のみが防御） | 内部監査D。P0-2 の CI に将来載せる |

---

## 4. 見送り（検討の上で現状維持とするもの・理由つき）

| 項目 | 理由 |
|---|---|
| 会話型 Agent Teams への移行 | D034 維持。外部でも「レビューは共有コンテキスト無しの方が高精度」（Cognition 2026-04）が実証され、Agent Teams は resume 不可の制約も未解消。全自動区間の並列化オプションという将来条件も D034 のまま有効 |
| 並列多視点レビューの既定化 | LLM 判事研究の合意も「客観チェックは単一の強い判事、パネルは主観・高リスクのみ」。現行の「ユーザー明示要求時のみ」と一致 |
| Ralph 式の自己再起動無限ループ | 人間ゲート思想と相反。取り込むのは強化部品（done契約=P2-1、サーキットブレーカ=P2-3、search-before-assuming=P2-2）のみ |
| BMAD 式の重量プロセス・多ペルソナ化 | 「重すぎる」が外部でも共通批判。scale-adaptive の精神は /12 の4分類で既に実現。UI 専門エージェントは D034 の将来条件のまま |
| GATE_STATUS/tasks の全面 JSON 化 | 人間可読性（docs-as-memory の根幹)の代償が大きい。折衷案（P2-4 のフック監視）で同じ脅威に対処 |

---

## 5. 適用の進め方

1. **非保護（すぐ適用可)**: P1-4（README/USAGE 鮮度）、P2-7、P1-2 の DECISIONS 補記、スキル追記の一部
2. **保護対象**: P0 群の大半・P1-1・P2-1〜P2-6 は、`/90-apply-retrospective` の手順どおり
   検証済み適用スクリプト+人間の1コマンド実行、または人間が
   `python tools/harness-maintenance.py --on --apply` で保守モードにしてから適用
3. **実機検証が必要**: P0-8（Copilot 二重発火）は適用前に VS Code 実機で挙動確認
4. **方針決定が必要**: P3 群は個別セッションで設計してから
5. 適用時は DECISIONS.md に D 連番で記録し、`generate-adapters.py` → `validate-harness.py` を通す

---

## 6. 主要出典

- Claude Code 公式: [hooks](https://code.claude.com/docs/en/hooks.md) / [sub-agents](https://code.claude.com/docs/en/sub-agents.md) / [memory](https://code.claude.com/docs/en/memory.md) / [changelog](https://code.claude.com/docs/en/changelog.md)
- Anthropic engineering: [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) (2025-11) / [Harness design for long-running app development](https://www.anthropic.com/engineering/harness-design-long-running-apps) (2026-03) / [Building a C compiler with parallel Claudes](https://www.anthropic.com/engineering/building-c-compiler) (2026-02)
- GitHub/VS Code 公式: [custom agents](https://code.visualstudio.com/docs/agent-customization/custom-agents) / [hooks](https://code.visualstudio.com/docs/agent-customization/hooks) / [agent skills](https://code.visualstudio.com/docs/agent-customization/agent-skills) / [AI Credits 移行](https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/) (2026-04-27)
- Cognition: [Multi-Agents: What's Actually Working](https://cognition.com/blog/multi-agents-working) (2026-04)
- Geoffrey Huntley: [Ralph](https://ghuntley.com/ralph/) (2025-07) / Armin Ronacher: [The Coming Loop](https://lucumr.pocoo.org/2026/6/23/the-coming-loop/) (2026-06)
- Every: [Compound Engineering](https://every.to/guides/compound-engineering) / Meta: [ACH mutation testing](https://engineering.fb.com/2025/09/30/security/llms-are-the-key-to-mutation-testing-and-better-compliance/) (2025-09)
- 比較対象: [Spec Kit](https://github.github.com/spec-kit/) / [OpenSpec vs Spec Kit](https://ypyl.github.io/programming/2026/06/03/openspec-vs-spec-kit-sdd.html) / [BMAD](https://github.com/bmad-code-org/BMAD-METHOD/releases) / Kiro (AWS)
