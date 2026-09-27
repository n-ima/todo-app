# DD-07 メモの Markdown

## 1. 変換パイプライン（US-009・NFR-007）

```csharp
var pipeline = new MarkdownPipelineBuilder()
    .DisableHtml()                 // 生 HTML はエスケープした文字列として出る
    .UseEmphasisExtras(EmphasisExtraOptions.Strikethrough)
    .UseTaskLists()
    .UseSoftlineBreakAsHardlineBreak()
    .Build();                      // UsePipeTables・UseAdvancedExtensions は使わない（表は非対応）
```

| 書式 | 対応 |
|---|---|
| 見出し・箇条書き・番号付きリスト・太字・斜体・コードブロック（フェンス・インデント）・インラインコード | CommonMark 標準 |
| 取り消し線 `~~x~~` | `Strikethrough` |
| チェックボックス `- [ ]` `- [x]` | `UseTaskLists`。出力の `<input type="checkbox">` には必ず `disabled` を付ける（表示専用。US-009） |
| 表 | 非対応（パイプ表の拡張を入れない。書いても段落の文字として出る） |
| 画像 `![](url)` | 画像として出さず、代替テキストを通常のリンク規則（§2）で出す（添付は持たない。外部画像の読み込みもしない） |
| 生 HTML・`<script>` | 文字列として表示（`DisableHtml`）。加えて CSP で実行不能 |

## 2. リンクの規則

`MarkdownService.Render(markdown, context)` は AST を走査し、`LinkInline` の URL を次のとおり扱う。

| URL | 扱い |
|---|---|
| `task:<数字>`（例 `[設計レビュー](task:123)`） | 有効なタスク → `<a href="/tasks/123" class="task-link">表示名</a>`。ゴミ箱・削除済みプロジェクト配下・完全削除 → `<span class="task-link-deleted">（削除されたタスク）</span>`（リンクにしない。US-009） |
| `http:` / `https:` | `<a href="..." rel="noopener noreferrer" target="_blank">` |
| 上記以外（`javascript:`・`data:`・`file:`・相対パス等） | リンクにせず表示名だけを文字列で出す |

- タスクの存在確認は、本文中の `task:` ID を集めて 1 回の `SELECT id FROM tasks t JOIN projects p ... WHERE t.id IN (...) AND t.deleted_at IS NULL AND p.deleted_at IS NULL` で行う。
- 自動リンク（URL の裸書き）は使わない（`UseAutoLinks` を入れない）。

## 3. プレビュー

- 詳細画面の閲覧表示: サーバーで描画した HTML を埋め込む。
- 編集中: 「編集」「プレビュー」のタブ切り替えと、幅 1200px 以上では左右 2 分割の並列表示（US-009）。プレビューは `POST /api/markdown/preview`（本文 `{ markdown }`、応答 `{ html }`）を、入力が止まってから 500ms 後に呼ぶ。未保存の内容でも表示できる。この要求は活動に数えない（DD-05）。
- `preview.js` は応答の HTML を `innerHTML` に入れる。サーバー側で安全化済みであることが前提のため、**クライアントで Markdown を変換しない**（変換器を 1 つに保つ）。
- プレビュー上のチェックボックスは `disabled` なのでクリックしても変わらない。

## 4. 長さ（FR-005）

- 保存・自動保存・プレビューでメモが 100,000 文字（DD-04 §2 の数え方）を超えたら `E-MEMO-TOO-LONG`。編集欄に `maxlength="100000"` と残り文字数の表示（残り 1,000 文字を切ったら表示）。

## 5. リンク記法の補助

詳細画面にタスク ID（`#123`）と「リンクをコピー」ボタン（`[タイトル](task:123)` をクリップボードへ）を置く。
