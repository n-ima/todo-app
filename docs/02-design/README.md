# docs/02-design/ について

- `architecture_template.md` → `architecture.md` : 全体アーキテクチャ設計
- `adr/adr_template.md` → `adr/0001-xxx.md` : 重要な設計判断の記録（連番）
- `detailed-design/` : コンポーネント/画面/API単位の詳細設計（`/04-design-detailed` で作成、フォルダは初回に作成される）
- `interface_contract_template.md` → `interfaces/IF-001-xxx.md` : サブシステム間インターフェース契約（ICD。大規模分割時のみ。`large-scale-development` スキル）
- `ui/` : 画面の自己完結型 HTML モックアップと `design-tokens.md`（ブラウザ UI がある場合。`ui-design-mockup` スキル）

要件 ID（US-/FR-/NFR-）との対応は architecture.md §7 の要件対応表に書く（ID 体系は `docs/00-overview/README.md`）。
