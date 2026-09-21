---
name: skill-authoring
description: 未知のデプロイ環境・技術スタック・ツールに遭遇したとき、その手順や規約をAgent Skill(SKILL.md)として新規に切り出す/公式Skillを取り込む方法。設計エージェント(スタック規約)・リリースエージェント(デプロイ手順)が使う。
---

# Skill作成スキル（メタスキル）

## いつ使うか

このハーネスは特定の技術スタック・デプロイ環境を事前収録していない
（プロジェクトごとに異なるため）。代わりに、**実際に判明した時点でSkillとして育てる**。

- **設計エージェント**: `docs/02-design/architecture.md` で技術スタックが確定したが、
  そのスタック向けの規約Skill（例: `.github/skills/stack-conventions/`）がまだ無いとき。
- **リリースエージェント**: `docs/01-requirements/environment.md` のデプロイ先について、
  `.github/skills/deploy-<environment>/` にまだ手順が無いとき。
- その他、繰り返し使う専門的な手順（特定のCI/CD設定、特定の監視ツール連携等）が
  今後も再利用できそうだと分かったとき。

## 新規作成の前に：公式・コミュニティ製Skillの流用を優先する

ゼロから書く前に、次を確認する。ゼロから書くのは最後の手段。

1. `github/awesome-copilot`（`skills/`配下）に該当するSkillがないか。
2. `agentskills.io` の登録一覧、または技術スタックの公式リポジトリが
   Agent Skills形式で手順を配布していないか。
3. **ホスティング先を持たないローカル完結アプリ**（CLIツール・ローカルサーバー・
   `npx` 実行形式）のリリースなら、同梱の `.github/skills/deploy-local-npx/SKILL.md`
   （汎用テンプレート）を出発点にし、プロジェクト固有値（ポート・起動確認方法等）を
   追記して確定させる（ゼロから書かない）。
4. 見つかった場合は、そのまま `.github/skills/<name>/` にコピーし、
   このプロジェクトのドキュメント構成（`docs/`配下のパス等）に合わせて必要最小限の調整のみ行う
   （このハーネスの `.github/skills/release-security-review/SKILL.md` は `github/awesome-copilot` の
   公式Skillを取り込んだ実例＝D005）。出典を明記する。

## 外部Skill・プラグイン・MCPのサプライチェーン統制（取り込み時に必須。D046）

外部のSkill/プラグインは単なるMarkdownではなく、参照スクリプトやツール操作の誘導を
含み得る**実行コードと同等の信頼境界**である（CI経由のトークン窃取→バックドア版公開と
いう実害事例が2026年に発生。プラットフォーム側でもサードパーティSkillのセキュリティ
スキャンが製品化され始めた）。取り込み時は次を必ず行う。

1. **出所の確認**: 公式リポジトリ/著名なコミュニティリポジトリか。スター数や紹介記事では
   なく、発行元そのものを確認する。
2. **版の固定**: 取得元のコミットハッシュ/タグを SKILL.md の出典に記録する
   （「最新を都度取得」しない。更新時は差分を見てから取り込む）。加えて
   `.github/harness/external-lock.json` に 1 エントリ（name / kind / source / source_url /
   revision または version / sha256（取れなければ null と理由）/ license / local_path / local_patch /
   references）を登録する。ロックに無い外部参照・`@latest` 等の浮動版・ロックと違う版は
   `python tools/validate-harness.py` が ERROR にし、local patch のダイジェストは
   `python tools/external_lock.py --refresh` で更新する（IA-20260831-11 / H-09）。MCP も同じ扱い。
3. **中身のレビュー**: コピーする前に全文（同梱スクリプト含む）を読み、
   (a) 外部への送信・認証情報の読み取り・ガード解除を促す記述がないか、
   (b) このハーネスの方針（承認ゲート・記録）と矛盾する指示がないか確認する。
   レビューを終えたら SKILL.md の冒頭（frontmatter の直後）に `レビュー: <誰が>・YYYY-MM-DD` の 1 行を残す。
   **同一セッションで新規作成・変更した Skill（特に deploy-*）は、そのセッションでは実行しない**
   （fresh-session 導入審査。`release` はレビュー済みの印が付いてから新しいチャットで /09 を再開する。
   codex 監査 2026-08-31 C-02 / IA-20260831-02）。
4. **最小権限**: Skillが要求するツール・接続は必要最小限に留める（AGENTS.md の
   Lethal Trifecta / Rule of Two 判定基準を適用する）。
5. スキャン機能（プラットフォームが提供する場合）は有効化する。

## 命名規則

- デプロイ環境: `.github/skills/deploy-<environment>/`（例: `deploy-vercel`, `deploy-aws-ecs`）
- 技術スタック規約: `.github/skills/stack-conventions/`、または対象を絞る場合
  `.github/skills/<framework>-conventions/`（例: `nextjs-conventions`）
- いずれも `<name>` は小文字・英数字・ハイフンのみとし、フォルダ名とfrontmatterの`name`を一致させる。
- ファイル: 直下に `SKILL.md`。手順が長い場合は `scripts/`（実行スクリプト）、
  `references/`（補足ドキュメント）を同階層に置き、`SKILL.md` から相対パスで参照する。

## 作成手順

1. 対象（環境 or スタック）に固有の情報を集める。
   - デプロイ環境: `environment.md` のホスティング・CI/CD・認証情報の種類・自動化範囲。
   - 技術スタック: `architecture.md` で確定したフレームワーク・ライブラリ・規約。
2. 実際に手を動かしながら（デプロイを試行する／実装の中でパターンを見つける）、
   再現可能な手順・規約を記録する。
3. `SKILL.md` に以下を含める。
   - **frontmatter**: `name`（フォルダ名と一致）, `description`（何を対象に、いつ使うか。
     他のSkillと区別できる具体的なキーワードを含める）
   - **前提条件**: 必要な認証情報・CLIツール・アクセス権（デプロイ系の場合）
   - **前提リソースの冪等な初期化手順**（デプロイ系は必須）: 初回実行に必要なリソース
     （ディレクトリ・DB・バケット等）を、既に存在しても安全に実行できる形で作成する手順。
     「デプロイは成功するが初回実行で落ちる」の典型原因のため省略しない。
   - **自動化できる手順 / 規約とその理由**: 実際に使ったコマンドや採用したパターン
     （スクリプト化できるものは `scripts/` に置く）
   - **人手が必要な手順**（デプロイ系の場合）: 具体的に何を、どこで、どう操作すればよいか
   - **検証手順**: 成功をどう確認するか
   - **ロールバック手順**（デプロイ系の場合）
4. デプロイ環境Skillを作った場合は `environment.md` の該当欄にパスを追記する。
5. **Claude Code用ポインタを同時に作る**: `python tools/generate-adapters.py` を実行すれば
   全アダプタ（`.claude/skills/` ポインタ含む）が正から自動再生成される（冪等）。
   Pythonが使えない環境では手動で `.claude/skills/<name>/SKILL.md` を作成し、
   frontmatter（name/descriptionは正と同一）+ 本文1行
   「このスキルの本文の正は `.github/skills/<name>/SKILL.md` です。それを読み従ってください」
   を書く。作成後は `python tools/validate-harness.py` で乖離が無いことを確認する。
   **例外**: `.github/skills/<nn>-<name>/`（`00-start-project` 等。Copilot Agent Host 向けの入口
   スキル）は prompt files からの生成物で、`.claude/skills/` ポインタを**作らない**（Claude Code の
   入口は `.claude/commands/` で、同名ポインタを作るとスラッシュ名が衝突する）。手で作らず
   `python tools/generate-adapters.py` に任せる（A6-17）。

## CI/CDワークフローの扱い（参照される仕組みには実体を用意する）

このハーネスは技術スタック非依存のため、具体的なCI設定（lint/test/deployのworkflow）は
事前に同梱していない。**設計フェーズでスタックが確定した時点で、このスキルの手順に従い
CIワークフローを生成する**（設計文書がCIを前提にするなら、その実体もこのタイミングで作る。
「文書がCIを参照しているのに実体がない」状態を放置しない）。
生成した `.github/workflows/` はハーネス保護フックの対象になるため、
以後の変更は人間のレビューを経由する。

## 品質基準

- 次に同じ対象（環境・スタック）を扱う際、**このSKILL.mdだけを読めば人手を介さず
  （人手が必要な部分は明示された上で）再現できる**状態を目指す。
- 認証情報そのもの（APIキー等）は絶対にSkillに書かない。保管場所への参照のみ記載する。
- `description` は簡潔かつキーワードが具体的であること。Skillは常時全文が読み込まれる
  わけではなく、まず`name`/`description`だけが読まれて関連度判定されるため
  （段階的開示）、ここが曖昧だと必要な場面で読み込まれず、無関係な場面で無駄に
  読み込まれてトークンを浪費する。

## 権限系 frontmatter の扱い（A7-G-3 / SC-2。指示ではなくフックが機械強制）

skills 配下（`.github/skills/**` / `.claude/skills/**`）への書込で権限を**拡張**する frontmatter は
`guard-harness-config-edit`（PreToolUse。sh / ps1）が **ask** にし、Claude Code では `guard-config-change.py`
（ConfigChange）が保守モード外の反映を **block** する。縮小・同値の書き直し・既定値の明示は allow。
対象キー（公式仕様 2026-09-17 取得: Claude Code skills リファレンス / VS Code agent-skills / agentskills.io 仕様）:

- 付与系（値のトークンが増えたら拡張）: `allowed-tools`（ツールの事前承認）、`hooks`（スキル起動時に
  登録され以後のセッションで走る任意コマンド）、`context: fork` / `agent`（サブエージェント文脈での実行）、
  `shell`（`` !`command` `` の実行シェル）、`mcp` / `mcp-servers`、`tools` / `permissions` / `permission-mode`
  （Copilot / 派生ホストの権限欄。ホストが無視するキーでも書込は ask）
- 真偽値: `disable-model-invocation: true → false`（モデルからの自動起動の再開）、`user-invocable: false → true`
- 制限系: `disallowed-tools` の削除・縮小（制限の解除＝拡張）

skills 配下の `hooks/hooks.json` の書込は常に ask、中核 2 スキル（request-routing / gate-check）は deny。
人間の明示指示で権限を足すときだけ ask を許可し、理由を `learnings.md` か本体の DECISIONS に残す。
