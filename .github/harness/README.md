# .github/harness/ — ハーネス所有の使い方文書

このフォルダには、開発ハーネス自体の使い方文書を置く。プロジェクトのルート
`README.md` はアプリのREADME（`/00-start-project` でスタブ化され、リリース時に
正式版が作られる）になるため、ハーネスの文書はルートではなくここに分離してある。

- [GLOSSARY.md](GLOSSARY.md) — 用語集（bare・done契約・ゲート・pass^k 等。初めて読むときはここから）。
- [USAGE.md](USAGE.md) — 実例つき使い方ガイド。セッション分割表を含む、日常運用の正。
- [overview.html](overview.html) — 全体像の図解（ダブルクリックでブラウザ表示できる自己完結HTML）。
<!-- BEGIN GENERATED: harness-doc-counts -->
- [agents.html](agents.html) — エージェント一覧（11の役割・ツール権限・5つの既定サブエージェント経路）。
- [skills.html](skills.html) — スキル一覧（22の手順部品をカテゴリ別に解説）。
- [commands.html](commands.html) — コマンド一覧（18のスラッシュコマンド。いつ・何が起きる・セッションの目安）。
- [guardrails.html](guardrails.html) — ガードレール解説（25のフック + opt-in 1・多層防御・環境別の強度差）。
<!-- END GENERATED: harness-doc-counts -->
- [PLATFORM.md](PLATFORM.md) — プラットフォーム対応の詳細（アダプタ構成・読み替え規則・ガード強度差。AGENTS.md の参照先）。
- [STATE-MACHINE.md](STATE-MACHINE.md) — フェーズゲートの状態機械（GATE_STATUS の語彙・遷移・往復上限・遷移ログ・lock・復旧手順の正）。
- エージェントの振る舞いの正（憲法）は [AGENTS.md](../../AGENTS.md)（プラットフォーム要件のためルート固定。環境差の詳細は PLATFORM.md へ分離する二層構成）。
- ハーネス全体構造の説明（README）は、テンプレート元リポジトリのルートREADMEを参照
  （ハーネス本体リポジトリではルートREADMEがその役割を持つ。プロジェクトには複製しない）。

このフォルダはハーネス所有（`tools/sync-harness.py` の自動同期対象）。
プロジェクト側で編集せず、変更はハーネス本体リポジトリで行う。
