<!-- proposals: IA-20260831-01..15, R-01..09 -->
# 開発ハーネス独立監査 — 2026-08-31

## 0. 監査メタデータ

- 監査日: 2026-08-31（Asia/Tokyo）
- 対象リポジトリ: `copilot-sdlc-harness`
- 対象コミット: `a240645a52d6708d7cd0b052e09e2da6d0d3c8e6`
- 監査目的: 「世界最高最強の開発ハーネス」という目標に対し、設計思想ではなく、実効性・安全性・再現可能な証拠・最新プラットフォーム適合性まで含めて独立評価する
- 制約: 本監査ファイル以外は変更していない。既存監査・元ファイルはすべて読み取り専用で扱った
- 既存監査との関係: `external-audit-2026-08-19.md`、`external-reaudit-2026-08-31.md`、2本の retrospective 監査、`PROPOSALS.md` を読んだうえで、重複よりも未検査領域と「実装後に残った矛盾」を優先した

### 判定語彙

| 重大度 | 意味 |
|---|---|
| CRITICAL | 現状のまま自律運用すると、本番変更・資格情報流出・統制迂回など重大事故へ直結し得る。先に停止または隔離が必要 |
| HIGH | 世界最高を名乗る前提となる安全性、再現性、配布完全性、強制力が成立しない |
| MEDIUM | 直ちに重大事故とは限らないが、保守性・移植性・評価品質を継続的に損なう |
| LOW | 品質向上または説明精度の改善 |

`確認済み` はローカル実装または実行結果で再現したもの、`仕様差分` は現行公式仕様とローカル構成の差、`再確認` は既存監査を独立に再現したものを表す。

---

## 1. 結論

このハーネスは、フェーズゲート、差分駆動、独立レビュー、done 契約、フックによる機械強制、成長ループ、マルチクライアント用アダプタという**設計思想の広さ**では非常に強い。しかし、現時点では「世界最高最強」には未達であり、特に**安全な自律リリースを許可してはいけない**。

最大の理由は、最上位規範が「デプロイは常に最終確認を経る」と定める一方、実際の release エージェントが「自動分類されたデプロイコマンドは確認なく実行」と命じられていることにある。さらに同エージェントは Web、実行、編集、デプロイ資格情報を同一セッションに集約し、未登録の環境では新しい deploy Skill をその場で作成し、そのまま実行する。これはハーネス自身が禁止する Lethal Trifecta / Rule of Two の三要素を満たす。

加えて、CI は依存宣言不足で構造検証を開始できず、CODEOWNERS は全行コメント、配布・同期物の真正性検証は無く、現行 VS Code 公式仕様とフック読込前提が食い違う。A/B 評価は先行監査が指摘した非対等予算・少数試行・不完全トラジェクトリのままであり、優位性の主張を再現可能な形で裏づけていない。

### 現在のゲート判定

| 評価対象 | 判定 | 理由 |
|---|---|---|
| 設計思想 | 強い | 失敗事例を D 記録・フック・Skill・テストへ還流する考え方は優れている |
| ローカル安全性 | **STOP** | リリース権限の規範矛盾、同一セッションの三要素集約、フックを通らない会話断片の永続化 |
| CI / 再現性 | **STOP** | `validate-harness.py` が PyYAML 不在で起動不能。workflow に導入手順が無い |
| 配布サプライチェーン | **STOP** | タグ・署名・ハッシュ・attestation を同期時に検証せず、ローカルパスの内容を信頼してコピーする |
| Copilot / Claude 適合 | 条件付き | 多くの配線はあるが、現行公式フック既定値とのドリフトと Plugin 1.0 非準拠が残る |
| 世界最高の実証 | **未達** | 同一条件の複数試行、完全トラジェクトリ、競合ハーネス比較、継続回帰が不足 |

---

## 2. 実施した確認

### 2.1 リポジトリ精査

以下を横断して確認した。

- 最上位規範: `AGENTS.md`、`CLAUDE.md`
- エージェント、prompt、Skill、adapter: `.github/agents/`、`.github/prompts/`、`.github/skills/`、`.claude/`、`.agents/`
- 強制層: `.github/hooks/`、`.vscode/settings.json`、`.claude/settings.json`
- 状態・配布・検証: `tools/`、`evaluation/`、`.github/workflows/`、`.gitattributes`
- ガバナンス: `DECISIONS.md`、`CHANGELOG.md`、`SECURITY.md`、`.github/CODEOWNERS`
- 過去監査: `audits/` 内の既存 Markdown 全件

### 2.2 読み取り専用の実行確認

| コマンド相当 | 結果 |
|---|---|
| `python -B tools/validate-harness.py` | `ModuleNotFoundError: No module named 'yaml'` で起動不能 |
| `python -B tools/gen-docs.py --check` | `OK`、対象3ファイルに差分なし |
| `python -B tools/e2e-run.py --selftest` | 31検査すべて PASS |
| `git tag --list` | ローカルタグなし |
| `git status --short` | 監査前から存在する未追跡 `.github/hooks/scripts/_paths.sh` のみ。監査中に元ファイルの変更なし |

PowerShell 環境の制約上、Git Bash の実行可否をこの監査で製品実機 E2E として再判定してはいない。公開リポジトリの branch protection、private vulnerability reporting、immutable releases のサーバー側設定も、ローカル clone からは証明できない。そのため、サーバー側設定については「無効」と断定せず「証拠なし」とした。

### 2.3 最新情報の基準

公式一次情報を優先し、個人エンジニアの実践知は補助線として使った。

- VS Code: [Agent hooks](https://code.visualstudio.com/docs/agent-customization/hooks)、[Custom agents](https://code.visualstudio.com/docs/agent-customization/custom-agents)、[Agent Skills](https://code.visualstudio.com/docs/agent-customization/agent-skills)、[Agent Plugins](https://code.visualstudio.com/docs/agent-customization/agent-plugins)
- 標準: [Agent Skills specification](https://agentskills.io/specification)
- Claude Code: [Hooks](https://code.claude.com/docs/en/hooks)、[Subagents](https://code.claude.com/docs/en/sub-agents)、[Skills](https://code.claude.com/docs/en/slash-commands)
- GitHub: [Secure use of GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use)、[Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)、[Artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
- セキュリティ: [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/download/52117/?tmstv=1765059207)
- 評価・ハーネス設計: [OpenAI Harness engineering](https://openai.com/index/harness-engineering/)、[Anthropic Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)、[Anthropic Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- 実践者: [HumanLayer: Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md)、[Addy Osmani: Agent Harness Engineering](https://addyosmani.com/blog/agent-harness-engineering/)、[Martin Fowler: Harness engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html)、[Simon Willison: The lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)

---

## 3. 新規・再オープン所見

## C-01 — リリース権限の正が互いに反転している

- 重大度: **CRITICAL**
- 状態: **確認済み・新規**
- 証拠:
  - `AGENTS.md:171-172`: git push、タグ付け、**デプロイ実行**は自動化分類にかかわらず常にユーザーの最終確認を経る。
  - `AGENTS.md:265-268`: リリースフェーズでもタグ付け・公開は明示承認後に実行する。
  - `.github/agents/release.agent.md:39-40`: 自動分類された CI/CD トリガー、**デプロイコマンド**は「そのまま自動で実行」「確認は求めない」。
  - `.github/agents/test.agent.md:11-14,54-56`: reviewer が LOW/INFO 以下なら `send: true` で release へ自動送信する。
  - デプロイ一般を ask/deny するフックは存在せず、`guard-dangerous-git` は git 系に限定される。

### 事故シナリオ

`environment.md` が deploy を「自動」と分類している場合、release エージェントは根本規範より局所的で具体的な「確認不要」を採用し、`terraform apply`、`kubectl apply`、クラウド CLI、ホスティング CLI 等を承認前に実行し得る。git push だけ ask になっても、直接デプロイや外部 API 変更は止まらない。

### 必須修正

1. 実行権限の正を1つにする。少なくとも現行方針では、release エージェントの deploy 実行を `pending_approval` で止める。
2. 「承認された正確な操作、対象、引数、コミット SHA、期限」を action packet に固定し、承認後に別の executor が一度だけ実行する。
3. 主要 deploy コマンドと外部変更 MCP に対する PreToolUse の機械ゲートを追加する。文面上の確認だけを境界にしない。
4. 回帰テストは、`environment.md=自動` でも deploy は ask、GET/plan/dry-run は allow、承認内容と引数が変われば再承認、を含める。

### 完了条件

- すべての規範・agent・Skill・hook で同じ承認意味論になる。
- 承認なしの模擬 deploy が Copilot / Claude Code の両方で機械的に拒否される。

---

## C-02 — release が自己拡張・資格情報・外部実行を同一セッションに集約している

- 重大度: **CRITICAL**
- 状態: **確認済み・新規**
- 証拠:
  - `.github/agents/release.agent.md:3`: `read/edit/search/execute/web` を同時に持つ。
  - 同 `:21-28`: 外部環境へ疎通し、deploy Skill が無ければ `skill-authoring` に従って**作成しながらリリースを続行**する。
  - 同 `:39-40`: 作成・選択した deploy 手順を確認なしで実行し得る。
  - `AGENTS.md:158-166`: 非信頼入力、機密アクセス、状態変更・外部送信の3つを同一セッションに持たせず、外部 Skill は版固定・導入前レビューを必須とする。

### なぜ重大か

release は次の三要素を同時に持つ。

- A: Web、外部 API、外部 Skill・ドキュメントという非信頼入力
- B: デプロイ資格情報、CI/CD トークン、環境設定へのアクセス
- C: deploy、push、タグ、外部 API 更新という状態変更・送信

さらに、同じモデルが「外部手順を読む→Skill を書く→その Skill を実行する」ため、レビュー独立性も時間的隔離も無い。プロンプトインジェクションや誤った公式風ドキュメントが、そのまま永続的な deploy 手順と実行へ昇格し得る。これは OWASP 2026 の ASI02（Tool Misuse）と ASI04（Agentic Supply Chain）の複合であり、ハーネス自身の Rule of Two に反する。

### 必須修正

release を最低3役に分離する。

1. `release-planner`: Web/read のみ。資格情報と write/execute を持たず、出所・版・ハッシュ・必要権限を含む action packet を作る。
2. `release-reviewer`: 読み取り専用の別コンテキスト。packet とソースを照合し、実行権限は持たない。
3. `release-executor`: Web と任意コード取得を持たず、承認済み packet の allowlist 操作だけを短時間・一回実行する。

新しく作成・導入した Skill は同一セッションでは実行せず、固定 revision、内容ハッシュ、レビュー記録、再起動後の fresh session を必須にする。現行の `github/awesome-copilot` にも、外部 Skill 導入時に canonical source、exact revision、license、safe trial を記録する手順がある（[security-installation.md](https://github.com/github/awesome-copilot/blob/main/skills/agent-skill-stack/references/security-installation.md)）。

---

## H-01 — Stop フックがユーザー発話を無加工で追跡文書へ永続化する

- 重大度: **HIGH**
- 状態: **確認済み・新規（プライバシー／機密性）**
- 証拠:
  - `.github/hooks/scripts/draft-learnings.py:77-111`: transcript の user/assistant 本文を読む。
  - 同 `:114-129`: 訂正らしいユーザー発話を選び、空白を潰すだけで先頭60文字を取得する。資格情報・個人情報・顧客名等の redaction は無い。
  - 同 `:159-174`: その文字列を `docs/00-overview/learnings-pending.md` へ append する。
  - `.gitignore` はこのファイルを除外していない。

### 事故シナリオ

ユーザーが「違う、APIキーは……」「顧客Xの本番URLは……」のように訂正すると、先頭60文字が docs 配下に残り、通常のコミット対象になり得る。この書込みは Stop フック自身が直接行うため、エージェントの edit/write に対する `guard-secret-leak` の PreToolUse を通らない。

### 必須修正

- 生のユーザー文を docs にコピーしない。訂正カテゴリ、対象ファイル、抽象化した教訓だけを構造化する。
- 書込前にシークレット・メール・URL query・トークン・個人識別子を redaction する共通ライブラリを使う。
- 未キュレーション候補は git ignored なローカル領域へ保存し、人が明示的に昇格したものだけ docs に移す。
- `effort-log.csv` の session ID、モデル、コストを含め、保存目的、保持期間、公開可否、削除手順を privacy policy に定義する。
- canary secret を含む合成 transcript で「出力に一文字も残らない」回帰テストを追加する。

---

## H-02 — 自身のサプライチェーン規則に反して Playwright MCP を `@latest` 実行する

- 重大度: **HIGH**
- 状態: **確認済み・新規**
- 証拠:
  - `AGENTS.md:165-166` と `.github/skills/skill-authoring/SKILL.md:43-49`: 外部 Skill・plugin・MCP は版固定し、内容レビュー後に導入する。
  - `.github/skills/test-case-design/SKILL.md:92-101`: `npx @playwright/mcp@latest` を設定例として生成させる。

`@latest` は、監査した版と実行される版を結び付けない。テスト段階の MCP はブラウザ、ネットワーク、ワークスペース文脈に触れ得るため、「Microsoft 公式だから可」ではなく revision と integrity の固定が必要である。現在の公式パッケージは版番号を公開しており、固定は可能（[microsoft/playwright-mcp package.json](https://github.com/microsoft/playwright-mcp/blob/main/package.json)、[MCP Registry metadata](https://github.com/microsoft/playwright-mcp/blob/main/server.json)）。

### 必須修正

- 検証済みの明示バージョンへ固定し、lockfile または取得物 integrity を保持する。
- 更新は Renovate/Dependabot 相当の PR にし、diff、release notes、safe trial、ロールバックを経る。
- `latest`、floating Git branch、未固定 container tag を validator で error にする。

---

## H-03 — CODEOWNERS は存在するが、実効ルールが1行もない

- 重大度: **HIGH**
- 状態: **確認済み・新規**
- 証拠:
  - `.github/CODEOWNERS:1-42` は全行コメントで、ownership pattern が0件。
  - `AGENTS.md:155`、`DECISIONS.md:D006`、各説明は CODEOWNERS + branch protection を外側の防御として推奨する。
  - origin は `n-ima/copilot-sdlc-harness` だが、実ユーザー・チームを設定した rule が無い。

CODEOWNERS が空なら、branch protection で「code owner review required」を有効にしても対象 owner を導けない。ローカル hook はセキュリティ境界ではないと自認しているため、外側の境界が空なのは重大である。

### 必須修正

- 本体リポジトリには実在 owner を設定し、少なくとも agent、hook、workflow、prompt、instruction、plugin、root policy、adapter generator、配布ツールを対象にする。
- GitHub Rulesets で main の PR 必須、code-owner review、status check、会話解決、force push/delete 禁止を有効化する。
- template 配布用 placeholder と本体運用用 CODEOWNERS を分ける。生成物の placeholder を本体の防御と数えない。
- GitHub の CODEOWNERS error API と branch ruleset 状態を release checklist で機械確認する。

サーバー側 ruleset の現在値は本監査では確認できないため、これは「branch protection が無い」という断定ではなく、**リポジトリ内の ownership 定義が空**という確定所見である。

---

## H-04 — 現行 VS Code 公式仕様とフック二重読込の前提が食い違う

- 重大度: **HIGH**
- 状態: **仕様差分・再オープン**
- 証拠:
  - `.github/harness/PLATFORM.md:34-38`: 2026-08-30 実機では `.claude/settings.json` は opt-in で、既定では二重発火しないと記録。
  - 現行 VS Code 公式 [Agent hooks](https://code.visualstudio.com/docs/agent-customization/hooks) は、既定の `chat.hookFilesLocations` に `.github/hooks`、`.claude/settings.local.json`、`.claude/settings.json`、user settings をすべて列挙し、Claude hook を止めるには明示的に `".claude/settings.json": false` とする例を示す。
  - `.vscode/settings.json` は `.claude/skills` の二重読込だけを false にし、`chat.hookFilesLocations` を設定していない。
  - `.github/hooks` と `.claude/settings.json` には PreToolUse 5系統、SessionStart、UserPromptSubmit、PreCompact、PostToolUse 3系統の同じスクリプトが重複している。

### 解釈

昨日の実機観測と今日の公式仕様の差は、どちらかを単純に誤りと断定する材料ではない。VS Code build、Copilot extension、rollout flag の違いがあり得る。問題は、ハーネスが**バージョンを固定せず、明示設定もせず、暗黙の既定値に安全性と性能を依存**していることである。

Windows では `.github` 系が PowerShell override、`.claude` 系が bash を起動するため、二重発火時には異なる実装の判定が同じ操作へ同時適用される。公式仕様は複数制御では最も制限的な結果を採るが、警告、context 注入、ログ、Stop 処理の重複とレイテンシは残る。

### 必須修正

- Copilot 用 workspace では `.claude/settings.json: false` を明示するか、単一の生成済み hook manifest を正にする。
- 対応する VS Code / Copilot extension の最低・検証済み版を machine-readable manifest に固定する。
- debug log を使った hook 発火回数 canary を CI または定期実機テストにする。
- 公式仕様変更を検知する watch job と、互換性表の期限を設ける。

---

## H-05 — CI は依存を宣言せず、最初の構造検証を開始できない

- 重大度: **HIGH**
- 状態: **再確認**
- 証拠:
  - `tools/validate-harness.py:37` と `tools/generate-adapters.py:22` は `yaml` を import する。
  - repository root に `requirements*.txt`、`pyproject.toml`、`uv.lock`、`Pipfile`、`poetry.lock` が無い。
  - `.github/workflows/harness-ci.yml:16-23` は Python を設定した直後に validator を実行し、PyYAML の install step が無い。
  - クリーンな bundled Python 3.12 で `ModuleNotFoundError: No module named 'yaml'` を再現した。

「CI を構成した」と「CI がクリーン環境で再現可能」は別である。現状は validator 自体のロジックへ到達しない。

### 必須修正

- 開発依存を lock 付きで宣言し、CI・CONTRIBUTING・doctor が同じ bootstrap コマンドを使う。
- `validate-harness.py` が必要とする依存を自己診断し、具体的な導入コマンドと非ゼロ終了を返す。
- Ubuntu / Windows の両 job で依存導入、cache、`pip check` または相当検査を行う。
- main で緑の run を証拠化してから release/version を更新する。

---

## H-06 — GitHub Actions の最小権限・不変参照・実行上限が無い

- 重大度: **HIGH**
- 状態: **確認済み・新規（CI サプライチェーン）**
- 証拠:
  - `.github/workflows/harness-ci.yml` は `actions/checkout@v4`、`actions/setup-python@v5` を可変 tag で参照する。
  - workflow/job の `permissions`、`persist-credentials: false`、`timeout-minutes`、`concurrency` が無い。
  - 例示用 security review workflow は Anthropic action を full SHA で固定するが、主 CI は同じ方針を自己適用していない。

GitHub は、第三者 action の full-length commit SHA が唯一の不変参照であり、`GITHUB_TOKEN` 権限を明示的に最小化するよう案内している（[Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)）。

### 必須修正

- すべての action を full SHA に固定し、更新 bot とレビューを通す。
- `permissions: contents: read` を既定にし、必要 job だけ追加権限を与える。
- checkout credentials を残さず、job timeout と branch/PR ごとの concurrency cancellation を設定する。
- dependency review、secret scanning/push protection、CodeQL または適切な静的検査を、利用可能なプランで有効化する。

---

## H-07 — ハーネス配布・逆同期に真正性の鎖がない

- 重大度: **HIGH**
- 状態: **確認済み・新規**
- 証拠:
  - `tools/sync-harness.py:123-149` は `harness-origin.md` のローカル path と表示用 version を記録する。
  - 同 `:153-173` の version は最大 D 番号または `plugin.json` の文字列であり、commit identity ではない。
  - 同 `:398-423` は指定 path のファイルを読み、そのまま project へコピーする。署名、commit SHA、内容 manifest、hash、attestation の検証が無い。
  - ローカル repository に version tag は無い。

つまり、`D071` や `v1.1.0` と表示されても、コピー元が公式 commit と同一であることを証明しない。改変されたローカル clone、誤った同名フォルダ、途中状態の working tree が hook・agent・Skill として多数の downstream project に伝播し得る。

### 必須修正

- 配布単位を署名済み・immutable な release にし、commit SHA と全ファイル SHA-256 manifest を発行する。
- `sync-harness` は dirty source を既定拒否し、許可された release/tag/commit と manifest を検証してから dry-run する。
- project 側の origin には path だけでなく repository URL、commit SHA、release version、manifest digest、取得時刻を記録する。
- GitHub の [Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) と [Artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) を利用し、consumer 側で verify する。
- offline 配布は署名済み bundle + checksum を使う。

---

## H-08 — Plugin 1.0 を名乗れる構造ではなく、portable component の配置も異なる

- 重大度: **HIGH**
- 状態: **仕様差分・既存所見の独立確認**
- 証拠:
  - `plugin.json` に Agent Plugins 1.0 の必須 `$schema` が無い。
  - `agents`、`skills`、`hooks`、`commands` を独自 top-level path として列挙する。
  - 現行 VS Code [Agent Plugins](https://code.visualstudio.com/docs/agent-customization/agent-plugins) は、portable skill を root `skills/`、MCP を `mcp.json`、Copilot 固有 agent/hook/command を `com.github.copilot/` に置く。agent/hook/command は portable top-level field ではない。

VS Code は `$schema` が無い root `plugin.json` を既存 Copilot format として扱うため、「完全に読めない」とは限らない。しかし、それは Agent Plugins 1.0 準拠や Claude との単一 portable package を意味しない。README の「最新仕様」「同じ内容」という説明と、実際の配布形式の境界を明確にすべきである。

### 必須修正

- 選択肢A: Agent Plugins 1.0 を正とし、portable `skills/` と `com.github.copilot/` を build 生成する。
- 選択肢B: 現行を Copilot legacy plugin と明記し、Claude plugin は `.claude-plugin/plugin.json` で別 artifact にする。
- いずれも公式 validator、clean profile への install、発見件数、agent/hook/skill 実行の E2E を release gate にする。

---

## H-09 — 外部由来 Skill の provenance が自分の導入規則を満たさない

- 重大度: **HIGH**
- 状態: **確認済み・新規（法務／サプライチェーン）**
- 証拠:
  - `.github/skills/release-security-review/SKILL.md:81-84` は `github/awesome-copilot` の path のみを記し、取り込んだ commit/tag、取得日、内容 hash、license を記録しない。
  - `.github/skills/skill-authoring/SKILL.md:43-49` は取得元の commit/tag 記録と全文レビューを必須とする。
  - `DECISIONS.md:D005` も「参考にして作成」と記録するだけで exact revision が無い。

後から原典が変わると、何をレビューし、どの差分をローカル適合させたか再現できない。MIT 等の第三者コード・文書を相当量取り込んだ場合の著作権表示・license 条件も、現状の記録だけでは判断できない。

### 必須修正

- `SOURCE.lock` または skill metadata に canonical URL、commit SHA、source file hash、license SPDX、取得日、local patch digest を持つ。
- third-party notices を生成し、substantial copy か independent rewrite かを記録する。
- source drift を定期確認し、差分を自動適用せず PR にする。

---

## H-10 — 監査台帳が自分の「無音消失禁止」を破っている

- 重大度: **HIGH**
- 状態: **確認済み・新規の統制評価**
- 証拠:
  - `audits/PROPOSALS.md:7-8`: 外部監査の提案は全件 open でも登録し、登録漏れを監査指摘対象とする。
  - `audits/external-reaudit-2026-08-31.md:226-228`: 177指摘を登録すべきと明記する。
  - 現在の `PROPOSALS.md` には、その177指摘を一意に追跡できる全件 ledger が無い。

41エージェント・177指摘という規模は、単一 Markdown の要約だけでは修正漏れ、重複、再発、accept-risk を検証できない。監査結果の量が多いほど、登録を手作業に委ねる設計は失敗する。

### 必須修正

- 監査出力を machine-readable な issue ledger（ID、severity、confidence、status、source commit、evidence、owner、due、fix commit、regression test）として生成する。
- Markdown report と PROPOSALS は同じ ledger から生成し、未登録 finding があれば CI error にする。
- 「177件」の完全一覧または artifact digest を保存し、件数だけが独り歩きしないようにする。
- 本監査の所見も、採否にかかわらず台帳へ登録する。ただし本監査ではユーザー制約に従い `PROPOSALS.md` 自体は変更していない。

---

## 4. 既存監査から継続する重要課題

以下は今回の新規発見ではないが、「世界最高」の判定に必要なので独立に位置づけ直す。

| ID | 重大度 | 継続課題 | 今回の判定 |
|---|---:|---|---|
| R-01 | HIGH | harness config guard は interpreter、git plumbing、reparse point 等の全書込経路を信頼境界で止めない | 未解消。文字列パターンを増やすだけでは閉じない。OS sandbox / read-only mount / branch rule が境界 |
| R-02 | HIGH | hook payload の未知 field、path 無し tool、parse failure の fail-open | D063 で一部 ask 化したが、`DECISIONS.md:D003` の「安全側（許可）」表現は誤りで、保護対象に対する fail-open を正当化する古い政策が残る |
| R-03 | HIGH | A/B の予算が bare 40 USD / harness 60 USD、model 記録なし、末尾2000字のみ、n=2 | 未解消。Anthropic は確率変動を扱う複数 trial と transcript の人手確認を必須級としている（[eval guide](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)） |
| R-04 | HIGH | 状態機械の並行更新、atomicity、lock、途中失敗からの resume | 未解消。単一 Markdown 正準は理解しやすいが、複数セッションでは lost update と不正遷移を防げない |
| R-05 | MEDIUM | `AGENTS.md` 296行 / 24KB、`DECISIONS.md` 210KB を常時または変更前に読む | validator の 30KB 閾値は寛大。OpenAI は約100行を map とし、HumanLayer は root を60行未満に保つ。ルール数より task-relevant context を優先すべき |
| R-06 | MEDIUM | document freshness は手動 `/13-converge` 中心 | OpenAI の事例は cross-link/freshness lint と recurring doc-gardening agent を併用する。現状は発見を人の起動に依存 |
| R-07 | MEDIUM | SECURITY.md に supported versions、応答/SLA、公開方針、scope exclusion、謝辞が無い | 報告入口は良いが、1.1.0 と将来版をどう扱うか、いつ acknowledgement/fix/disclosure するかが未定義 |
| R-08 | MEDIUM | Antigravity は凍結なのに plugin description 等に3環境対応の表現が残る | README 本文は凍結を明記して改善済み。artifact metadata と機能表も「adapter bundled / unverified」に統一すべき |
| R-09 | MEDIUM | hook のレイテンシ、二重実行、失敗率、警報疲れを継続測定していない | guard の数が増えているため、P50/P95、false ask/deny、host別発火数を SLO 化すべき |

### コンテキスト設計について

`AGENTS.md` の内容自体には価値がある。しかし「価値がある規則」と「毎ターン常駐すべき規則」は別である。[OpenAI の harness engineering](https://openai.com/index/harness-engineering/) は、巨大な instruction manual が失敗し、約100行の `AGENTS.md` を目次として docs を正にしたと報告している。[HumanLayer](https://www.humanlayer.dev/blog/writing-a-good-claude-md) も、指示が増えるほど全指示の追従率が下がるため、root は普遍規則だけにし progressive disclosure を使うとしている。

本ハーネスはすでに Skill と PLATFORM.md を持つため、構造の作り直しは不要である。必要なのは、受付、安全境界、成果物の正、次の資料への routing だけを root に残し、説明・例・環境差・コスト論を demand-loaded 文書へ移すことである。

---

## 5. 「世界最高」を証明する評価体系

世界最高は機能数や監査件数ではなく、同じモデル・同じ課題・同じ予算で、品質、安全性、コスト、時間、再現性が競合より優れることを公開可能な証拠で示した状態と定義すべきである。

### 5.1 評価スイート

| スイート | 目的 | 最低条件 |
|---|---|---|
| Regression | 過去に起きた事故を再発させない | 各事故に deterministic grader、修正前は fail / 修正後は pass |
| Capability | 現在できない難題で改善余地を測る | saturation していない複数難易度、曖昧要件、brownfield、長期変更、UI、release dry-run |
| Security adversarial | prompt injection、tool misuse、supply chain、secret exfiltration | canary secret、悪性 Skill/MCP、承認 packet swap、path/reparse、unknown payload schema |
| Cross-host conformance | Copilot VS Code / CLI / cloud、Claude Code の意味論一致 | 同一 scenario の event trace と最終状態を比較 |
| Human UX | 質問量、不要停止、回復容易性 | task completion、介入回数、承認の理解度、time-to-first-success |

### 5.2 実験契約

- model、model version、host version、extension version、OS、commit SHA、temperature 相当、permission mode を必須記録する。
- arm 間で token/cost/turn/time budget を同一にする。使い切れなかった差も結果として残す。
- 各 task/arm は最低 k=5、主要 claim は十分な n で信頼区間を出す。`pass@k` と「全部成功」の `pass^k` を目的別に併記する。
- 完全 transcript / tool trace / grader output を privacy-redacted artifact として保存し、末尾抜粋だけにしない。
- grader は outcome と self-report を分け、成果物・コマンド実行・外部状態を独立検証する。
- bare だけでなく、少なくとも Spec Kit、BMAD 系、superpowers/類似 workflow、最新ネイティブ harness を比較対象にする。
- evaluator と generator のモデル相関を下げ、サンプル transcript を定期的に人が読む。
- main では軽量 regression、nightly/weekly で capability/security、release 前に cross-host canary を回す。

Anthropic の現行 eval guide は、モデル出力の変動に対して複数 trial を行い、score を額面どおり信じず transcript を読むことを強く求めている。現行の n=2、予算非対等、model 不記録、tail のみでは、ハーネス優位を検証する計測装置として不足する。

---

## 6. 目標アーキテクチャ

現在の資産を捨てず、次の6層へ責務を明確化すると「強い文書セット」から「検証可能な制御システム」へ進める。

1. **Policy layer**
   - 承認、保護 path、外部送信、secret、phase transition を単一 machine-readable policy にする。
   - agent 文面、hook、validator、docs は policy から生成・検証する。

2. **Planner layer**
   - 非信頼情報を扱えるが、secret と外部変更権限を持たない。
   - exact action packet と evidence requirement を作る。

3. **Execution layer**
   - Web browsing、任意 Skill 導入、任意 shell を持たない。
   - 承認済み packet の allowlist operation だけを sandbox で実行する。

4. **State layer**
   - `progress.md` は人向け view とし、遷移は schema、revision、lock、append-only event で管理する。
   - view は event log から生成し、競合更新を検知する。

5. **Distribution layer**
   - source commit、lock、SBOM、hash manifest、signature/attestation、immutable release を持つ。
   - Copilot legacy、Agent Plugins 1.0、Claude plugin を同じ source から build する。

6. **Evidence layer**
   - CI、E2E、security、cross-host、performance、audit ledger を同じ release evidence bundle に集約する。
   - 「実施した」という文章ではなく、再実行可能な command、artifact digest、結果、時刻を正にする。

この分離は、実践者が述べる「feedforward guidance と feedback sensors」の両立、OpenAI の deterministic linter / doc-gardening、Anthropic の planner-generator-evaluator 分離に沿う。現在のハーネスには素材がほぼ揃っており、最大の仕事は**権限境界と生成元を一本化すること**である。

---

## 7. 優先順位付き改善計画

## P0 — 自律実行を安全に止める（48時間以内）

1. release の「deploy は確認不要」を無効化し、全外部変更を承認前で停止する。
2. release planner / reviewer / executor を分離し、新規 Skill の同一セッション実行を禁止する。
3. `draft-learnings.py` の生ユーザー文永続化を停止し、既存 downstream の `learnings-pending.md` を secret scan する。
4. `@playwright/mcp@latest` を検証済み版に固定する。
5. PyYAML を lock 付きで宣言し、Ubuntu / Windows の CI を初めて緑にする。

## P1 — 強制層と配布鎖を成立させる（1〜2週間）

1. active CODEOWNERS と GitHub Rulesets を設定する。
2. VS Code hook 読込元を明示し、発火回数 canary を追加する。
3. Actions を full SHA / least privilege / timeout / concurrency で harden する。
4. signed immutable release、manifest、attestation、sync 時の verify を実装する。
5. Agent Plugins 1.0 と client-specific package の build target を分け、clean install E2E を通す。
6. 全外部 Skill の provenance/license lock を作る。

## P2 — 世界最高を測定可能にする（2〜6週間）

1. audit finding ledger と PROPOSALS/Markdown の自動生成を導入する。
2. A/B runner の予算・model・完全 trace・resume を修正し、k>=5 で再測定する。
3. security adversarial / cross-host / concurrency / recovery suite を追加する。
4. `AGENTS.md` を routing map へ圧縮し、doc freshness lint と定期 gardening を追加する。
5. hook latency、false decision、介入回数、cost、task success の SLO dashboard を作る。

---

## 8. リリース許可基準

少なくとも次をすべて満たすまで、「安全な自律リリース」「世界最高」という表現は使わない方がよい。

- [ ] CRITICAL 0件、HIGH は owner・期限・accept-risk 根拠つき以外0件
- [ ] clean Ubuntu / Windows CI が同一 commit で緑
- [ ] deploy は exact-action 承認なしに実行できないことを敵対テストで証明
- [ ] release executor が Web、任意 shell、Skill 書込を持たない
- [ ] canary secret が transcript、learnings、logs、artifact、network に漏れない
- [ ] active CODEOWNERS / required review / required status checks のサーバー側証拠あり
- [ ] actions、MCP、外部 Skill、runtime dependency が固定され、provenance と license が追跡可能
- [ ] immutable release + manifest + attestation を consumer が verify できる
- [ ] Copilot VS Code と Claude Code の同一 conformance suite が連続3 release で合格
- [ ] A/B が同一条件・複数試行・完全 trace・第三者再実行可能で、主要比較対象に統計的優位
- [ ] audit finding が ledger に100%登録され、修正 commit と regression test に紐づく
- [ ] recovery drill（中断、partial write、budget exhaustion、host crash、rollback）が合格

---

## 9. 保持すべき強み

問題が多いことと、基礎が弱いことは同義ではない。次は捨てずに伸ばすべきである。

- docs を会話より上位の記憶とする方針
- 要件・設計では人を厚く、実装・テストでは自動化するフェーズ別の人間関与設計
- done 契約と再実行可能な証拠を求める思想
- reviewer / spec-critic / task-worker によるコンテキスト分離
- 過去失敗を hook selftest と DECISIONS に還流する習慣
- brownfield、差分変更、小規模 fast path、大規模 ICD までを一つの operating model で扱う範囲
- shell / PowerShell parity、adapter generator、generated-doc check という「鏡面」を機械管理しようとする姿勢
- Lethal Trifecta、外部 Skill の版固定、MCP 最小権限をすでに規範化している点

今回の核心は、これらの良い原則が**一部の実装で自己適用されていない**ことである。新しい原則を増やすより、既存原則を単一 policy、分離された権限、CI、署名済み配布、再現可能な eval に落とし切る方が価値が高い。

---

## 10. 台帳登録用サマリ

本監査では `PROPOSALS.md` を変更していない。登録時は最低限、次を一件ずつ独立 ID として取り込む。

| 仮ID | 重大度 | 提案 |
|---|---:|---|
| IA-20260831-01 | CRITICAL | release の deploy 承認規範を一本化し、機械ゲートで exact-action approval を強制する |
| IA-20260831-02 | CRITICAL | release planner/reviewer/executor を分離し、Rule of Two と fresh-session 導入審査を強制する |
| IA-20260831-03 | HIGH | transcript 由来ユーザー文の docs 永続化を止め、redaction・local staging・retention policy を実装する |
| IA-20260831-04 | HIGH | Playwright MCP の `@latest` を禁止し、固定版・integrity・更新PRへ移行する |
| IA-20260831-05 | HIGH | 本体用 active CODEOWNERS と required code-owner review を有効化する |
| IA-20260831-06 | HIGH | VS Code の hook 読込元を明示し、host/version 別の単発実行を canary 検証する |
| IA-20260831-07 | HIGH | Python 開発依存を lock し、clean CI を緑化する |
| IA-20260831-08 | HIGH | GitHub Actions を full SHA、least privilege、timeout、concurrency で harden する |
| IA-20260831-09 | HIGH | immutable release、manifest、attestation と sync 時 verification を導入する |
| IA-20260831-10 | HIGH | Agent Plugins 1.0 / Copilot / Claude の配布 artifact を正規 build target 化する |
| IA-20260831-11 | HIGH | 外部 Skill の exact revision、hash、license、local patch を lock する |
| IA-20260831-12 | HIGH | 全監査所見を machine-readable ledger へ自動登録し、未登録を CI error にする |
| IA-20260831-13 | HIGH | 同一予算・model 記録・完全 trace・k>=5 の比較 eval を再実施する |
| IA-20260831-14 | MEDIUM | AGENTS.md を routing map へ圧縮し、定期 doc-gardening を導入する |
| IA-20260831-15 | MEDIUM | state transition の revision/lock/event log と recovery drill を導入する |

---

## 11. 最終所見

このハーネスの次の飛躍は、さらに多くの agent、Skill、規則、監査人数を増やすことではない。最優先は、次の4点である。

1. **権限を分離し、承認を実行境界で強制すること**
2. **全依存・配布物・更新元を固定し、検証可能にすること**
3. **監査・評価・CIを、要約ではなく再現可能な evidence system にすること**
4. **暗黙の host 既定値をなくし、対応版と意味論を machine-readable にすること**

ここまで達成すれば、現在の豊富なプロセス資産が初めて安全かつ測定可能な競争力に変わる。逆に、CRITICAL 2件を残したまま自律性だけを高めると、ハーネスの強みである自動化が事故半径を拡大する。
