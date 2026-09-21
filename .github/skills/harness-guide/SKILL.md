---
name: harness-guide
description: このハーネス自体の使い方ガイド。どのプロンプト・エージェントをいつ使うか、チャットを分けるタイミング、コスト最適化を案内する。「使い方が分からない」「次に何をすればいい」という質問に答えるときに使う。
---

# ハーネス使い方ガイド（ナビゲーション用）

## ユーザーの状況 → 案内する入口

| ユーザーの状況 | 案内する入口 |
|---|---|
| 進捗が分からない・全体を把握したい | `/99-status` |
| これから開発を始める | `/00-start-project` → `/01-requirements-intake` |
| 要件について相談・追記したい | requirementsエージェント（`/02-requirements-deepdive`） |
| 設計について相談・変更したい | designエージェント（`/03-design-architecture` または `/04-design-detailed`） |
| 実装を進めたい・再開したい | `/06-implement-task`（計画が無ければ先に `/05-implementation-plan`） |
| テストを実行したい | `/07-test-plan` → `/08-test-execute` |
| リリースしたい | `/09-release-checklist` |
| リリース後・運用中の改修・バグ修正・機能追加 | `/12-change-request`（差分駆動の4分類 → 影響範囲の特定 → 該当フェーズだけ再ゲート → 実装・テスト・記録） |
| ハーネス自体を改善したい | `/10-retrospective`（提案作成）→ 本体リポジトリを開いた新しいチャットで `/90-apply-retrospective`（適用）→ プロジェクトに戻って `/91-sync-from-harness`（逆同期。これで還流が完了） |
| 本体の改善をこのプロジェクトに取り込みたい・「ハーネスを更新して」 | `/91-sync-from-harness`（本体パスは `harness-origin.md` に自動記録済み。dry-run はエージェント、`--apply` の1コマンドだけ人間） |
| 環境が正しく動くか確かめたい（導入直後・同期直後・「フックが効いていない気がする」） | コマンドではなくツール: `python tools/doctor.py`（Claude Code の版・Git Bash・python・フック配線・statusLine の実行・ワークスペース信頼・配布鮮度・logs/usage を pass/warn/fail の表で出す。構造側は `python tools/validate-harness.py`。SessionStart に「本体より N 世代古い」が注入されたら `/91` を先に） |
| 大規模案件（US30超・複数サブシステム・複数チーム）を始める | `/00-start-project` → `large-scale-development` スキルの2層構造で進める（要件・設計エージェントが規模判定時に自動適用） |
| **既存のコードベース**にハーネスを導入したい | `/11-brownfield-intake`（as-is逆起こし→整合検証→差分駆動サイクルへ接続） |
| 使い方が分からない | `/98-harness-help` |

プロンプト番号の規則: 00=開始/ナビ、01-02=要件定義、03-04=設計、05-06=実装、
07-08=テスト、09=リリース、10=振り返り、11=既存コードベース導入、12=運用中の変更請求、
90番台=ハーネス保守
（90=還流適用〔本体リポジトリ専用〕、91=本体からの逆同期〔プロジェクト側で実行〕）、
98=ヘルプ、99=進捗確認。

この表は**エージェントが自分で入口を選ぶため**のもの（受付の責務と手順の正は AGENTS.md
「受付ルーチン」と `request-routing` スキル。ユーザーに入口を指定させない）。

## セッション分割の要点

`.github/harness/USAGE.md` のセッション分割表が正。原則: フェーズの切り替わり（要件→設計、設計→実装）では
新チャット。実装〜テストは1セッションでよい（実装はtask-workerに隔離されるため）。
継続中でも「完了タスク10個超 / 差し戻し2往復超 / 応答の劣化を感じた / 中断して時間を空ける」
のいずれかで新チャットへ。再開は新チャットで該当プロンプトを実行するだけ
（`docs/` が正の状態なので何も失われない）。

## コスト最適化の案内

- モデル/effort の方針は `.github/harness/model-policy.yml` が正（役割別。既定は全役割
  `inherit`、判定役 reviewer / spec-critic と上流 requirements / design は effort high を下限に
  固定、`task-worker` の軽量化は A/B 実測後にのみ。対照表は PLATFORM.md「モデル/effort の
  方針」）。`model: auto` は Copilot の frontmatter 仕様に無いため使わない（コストを抑えたい
  ときはピッカーで Auto を選ぶ）。モデルと effort はセッション冒頭で決めて途中で変えない
  （切替はキャッシュ全損。Fable 5.1 の effort 切替のみ 2.1.260 以降は維持）。
- 長いログ・ファイル全文を会話に貼らない（ファイルパスを伝えればエージェントが読む）。
- 同じ訂正を繰り返しているなら learnings.md への記録を促す（自動注入で再発を防ぐ）。
- トークン・費用の一次データは `.github/hooks/logs/usage/` の受領書（Claude Code の
  Stop/SessionEnd フックが自動記録。ローカル・git 管理外）。いまのセッションは
  `/harness-stats`、区切りのターンでは Stop フックが 3 行で自動提示する。集計は
  `python tools/effort-report.py`（工程・エージェント・モデル×effort 別、基準線、逸脱一覧）。
  振り返りの「トークン効率」の検証に使う（旧 `effort-log.csv` は `--migrate` で取り込む。
  Copilot の取得経路と劣化モードは PLATFORM.md「コスト計測の詳細」）。
- Claude Code のサブエージェントのモデル解決順（v2.1.251 以降）は「呼出時の `model`
  パラメータ > frontmatter `model:` > 環境変数 `CLAUDE_CODE_SUBAGENT_MODEL` > メイン会話」。
  frontmatter（生成物。既定 `inherit`）がある以上、環境変数だけでは一括変更できない。
  一括で強制するときは `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`（v2.1.257 以降。fork と
  `model: inherit` のスキルは除く）を併用する。effort は `CLAUDE_CODE_EFFORT_LEVEL` が
  frontmatter より優先される。Copilot は VS Code では役割別 effort を指定できず（劣化モード）、
  CLI は生成物 `.github/copilot/settings.json` の `subagents.agents.<name>.effortLevel` で指定できる。

## 案内の原則

- 次の一手は**1つだけ**提示する（選択肢の羅列はしない）。
- 「何を / どのチャットで / なぜ」の3点で短く案内する。
- **必ず実行可能なコマンド形式で示す**（例: 「新しいチャットで `/03-design-architecture` を
  実行」）。「◯◯エージェントに切り替えて」という案内はしない — プロンプトが対応
  エージェントに自動バインドされるため切り替えは不要で、切り替えだけでは
  プロンプトの起動指示が実行されない。
- ハーネス設定（.github/agents等）の変更相談には「エージェントは自動編集できない。
  人間が編集し、本体の DECISIONS.md に記録する」運用を案内する。
