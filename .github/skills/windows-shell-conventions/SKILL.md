---
name: windows-shell-conventions
description: Windows + Git Bash + PowerShell 併用環境でコマンド実行するときの既知の落とし穴集。ドライブレター大文字小文字、autocrlfとgit stashのCRLF事故、PowerShellインライン呼び出しの構文崩壊、curlの日本語文字化け、heredoc/Editのバックスラッシュ破壊、Voltaシム、パイプのSIGPIPEなど。Windows環境で実装・テスト・リリースのコマンドを実行する全エージェントが使う。
---

# Windows シェル規約（Git Bash / PowerShell 併用環境の既知の落とし穴）

Windows 上で Git Bash と PowerShell を併用する開発環境には、プロジェクトを問わず
再発する実行時の落とし穴がある。複数の実プロジェクトで試行錯誤の末に確立された
回避策の集約であり、**該当環境ではこの一覧を前提にしてから**コマンドを実行する
（同じ発見コストを二度払わない）。プロジェクト固有の確定コマンドは従来どおり
`docs/00-overview/learnings.md` に記録する。

## 共通原則: 複雑なワンライナーを書かない

以下の落とし穴の多くは「シェルのワンライナーに特殊文字・複合手順を詰め込む」ことで
発生する。**パイプライン・特殊文字・複数行ロジックを含む処理は、スクリプトファイル
（`.ps1` / `.sh` / `.mjs`）に書いてファイル実行する**のが最も確実な共通回避策。

## 1. ドライブレターは大文字で（テストランナーのモジュール二重ロード）

cwd が小文字ドライブ（`d:\...`）だと、Vitest 等がモジュール解決の不一致
（同一ファイルの二重ロード）を起こしテストが全滅する
（例: vitest projects 実行が全ファイルで `TypeError: ... (reading 'config')`）。

- **回避**: npm scripts は必ず大文字ドライブパスへ `cd` してから実行する。
  例: `cd "D:/path/to/project" && npm run check`

## 2. `git stash -u` と autocrlf の相性（未追跡ファイルのCRLF化）

`core.autocrlf` 有効時に `git stash -u` を使うと、未追跡ファイルが stash 復元時に
LF→CRLF 変換され、Prettier 等のフォーマットチェックが大量に落ちる。

- **回避**: 未追跡ファイルが多いリポジトリでは `git stash -u` を使わない。
  落ちた場合はフォーマッタの一括適用（例: `npm run format`）で復旧できる。

## 3. Git Bash から PowerShell をインラインで呼ばない

Git Bash から PowerShell をインライン（`powershell -Command "... $_ ..."`）で呼ぶと、
`$_` 等が Bash 側の展開（extglob）に干渉して構文エラーになる。

- **回避**: パイプラインを使う PowerShell は `.ps1` ファイルに書いて
  `powershell -File script.ps1` で実行する。
- **応用（プロセス停止）**: `npm run dev` 等で concurrently 配下に複数の node が残る場合、
  PID 単発 kill では止まらない。`.ps1` で `Get-CimInstance Win32_Process` の
  CommandLine からプロジェクト名を含む node プロセスを特定し、
  `taskkill /F /T /PID <pid>`（`/T` でツリーごと）で停止する。
  **ただし次の §3.1 のガードを必ず併せて実装する**（この手順どおりに素直に書くと暴発する）。

### 3.1 `.ps1` の文字コードと kill 系の必須ガード

上の「`.ps1` に書いて実行する」を素直に実行すると、**日本語コメント入り・BOM なし
UTF-8 の `.ps1`** ができあがる。この組み合わせは実際に**無関係な node プロセスを
全部 kill する実害**を起こしている。原因は次の2つの連鎖であり、どちらも単体では
気づけない。

1. **文字コード**: Windows PowerShell 5.1（ANSI = CP932）は **BOM なし UTF-8 を
   Shift-JIS として読む**。化けた multi-byte 列が**次行の改行を飲み込んで後続行を
   コメント化する**ため、そこで代入していた変数が未定義のまま先へ進む。
2. **`-match $null` は True**: PowerShell は `'任意の文字列' -match $null` を
   **True** と評価する。したがって未定義パターンでの照合は**全件一致**になる。

**規約（プロセス停止に限らず `.ps1` 全般）**

- `.ps1` は **ASCII のみで書く（コメントも英語にする）か、UTF-8 BOM 付きで保存する**。
  このハーネス自身の `.github/hooks/scripts/*.ps1` は BOM 付きで保存してあり、
  同じ事故を免れている。確認: `head -c 3 <file> | xxd` の出力が `efbbbf` であること。
- kill 系は**二重ガードを必須**にする（パターン未定義と、広すぎる一致の両方を止める）。

  ```powershell
  if ([string]::IsNullOrWhiteSpace($pattern)) { throw 'pattern is empty' }
  $procs = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $pattern })
  if ($procs.Count -gt 5) { throw "too many matches: $($procs.Count)" }
  ```

- kill の前に**リスト表示だけの dry-run** を1回挟み、照合結果が意図どおりか目視する
  （`$procs | Select-Object ProcessId, CommandLine`）。
- CommandLine の部分一致（`-like '*main.js*'` 等）は、**照合を実行している PowerShell
  自身のコマンドラインにも一致して自分を kill する**（終了コード255で応答が突然
  途切れた実例＝D039）。まず `Get-CimInstance Win32_Process -Filter "Name='node.exe'"` で
  **プロセス名から絞り**、CommandLine の照合はその結果に対してだけ行う。

## 4. curl に日本語を直接載せない（ボディ・クエリ文字列とも文字化け）

Git Bash の curl で日本語を含む JSON を `-d '{"title":"日本語"}'` と直書きすると、
コマンドライン経由の文字コード変換で Content-Length が不一致になり
400（バリデーションエラー）になる。

- **回避**: ボディを UTF-8 でファイルに書き、`curl --data-binary @body.json` で送る。
- **クエリ文字列でも同根の文字化けが起きる**: 日本語を検索条件等のクエリ文字列に
  載せると `--data-urlencode` でも不成立。こちらはエラーにならず
  「静かに合致しない」形で現れる（絞り込みが常に0件になる等）ため気づきにくい。
  日本語を含む API 検証は node の `fetch` + `URL.searchParams` で行う。

## 5. heredoc / Edit ツールのバックスラッシュ破壊

Bash ツール経由の heredoc は `\\` が `\` に潰れることがあり、Edit ツールは文中の
`\uXXXX` 表記を実文字に変換して書き込むことがある（check-doc-chars 自身に NUL 文字
リテラルが混入した実例＝D039）。

- **回避**: バックスラッシュを含む文字列は
  `String.fromCharCode(92) + 'u0000'` のように文字コードで組み立てる。
  複数行の node 実行は `node -e` でなくスクリプトファイルに書く
  （複数行 `node -e` は出力が丸ごと消えることがある）。
- **検証**: エスケープが重要なファイルを書いた後は、意図した文字が入ったか
  `grep` やバイト確認で検証する（見た目では気づけない）。

## 6. Volta シムと環境変数差し替えの組み合わせ

`HOME`/`USERPROFILE` を一時フォルダへ差し替えて node を起動すると、Volta のシム
`node` は `Volta error: Could not determine LocalAppData directory` で起動できない。

- **回避**: 先に `REAL_NODE=$(node -e "console.log(process.execPath)")` で実体の
  node.exe を解決し、それを直接実行する。
- **注意**: `A && B & PID=$!` の形は `&` が行全体を背景化して変数代入ごと子シェルに
  行くため意図どおり動かない。起動→疎通確認のような複合手順はスクリプトファイルにする。

## 7. テスト実行の出力を `head` 等へパイプしない（SIGPIPE で中断）

`npx playwright test | head` のようにパイプ先が先に閉じると SIGPIPE で
テストランナー自体が中断され、結果ファイル（test-results 等）が消える。

- **回避**: 長い出力はファイルへリダイレクト（`> result.log 2>&1`）してから読む。

## 8. 再帰削除（`rm -rf` / `Remove-Item -Recurse`）は権限ガードに拒否される（Claude Code）

Claude Code（auto モード）では再帰削除系コマンドが権限ガードに拒否される。

- **回避**: クリーン検証などで「消したい」場合は、**`Move-Item` で scratchpad
  （一時領域）へ退避**すれば同じクリーン状態に到達でき、しかも可逆で安全。
  削除がどうしても必要な場合はコマンドを提示してユーザーに実行してもらう。

## 9. ユーザーに渡すコマンドは「どのシェル向けか」を明示する（既定は PowerShell の1行）

**エージェント自身の実行（Git Bash / Bash ツール）と、ユーザーに渡すコマンドは別物。**
Windows ユーザーの既定シェルは PowerShell であり、bash の行継続 `\` を含む複数行
コマンドを貼ると `単項演算子 '--' の後に式が存在しません` 等で丸ごと失敗する
（`gh release create` の提示コマンドで失敗した実例＝D039）。

- **回避**: ユーザーに提示するコマンドは①**PowerShell 用の1行**に整形する
  （長くても改行しない。複数行が必要なら `.ps1` ファイルとして渡す）
  ②bash 前提のときはその旨を明記する。

## 10. PowerShell 5.1 のパイプは stdin の先頭に UTF-8 BOM を付けることがある

`$json | python script.py` のように文字列をネイティブプログラムへパイプすると、
受け側の stdin 先頭に BOM（U+FEFF）が付き、`json.load(sys.stdin)` が
`Unexpected UTF-8 BOM` で失敗する（log-effort.py の実機検証で発生した実例＝D040）。

- **回避**: 受け側（Python）で `json.loads(sys.stdin.read().lstrip(chr(0xFEFF)))` の
  ように BOM を除去してからパースする。BOM 文字をソースに**リテラルで埋め込まない**
  （不可視文字の混入になる。`chr(0xFEFF)` で組み立てる）。

## 11. PowerShell 5.1 の `ConvertFrom-Json` は大きい/深い JSON 行で失敗する

Claude Code のトランスクリプト JSONL のような深いネスト・巨大文字列を含む行は
`Invalid object passed in` 等でパースに失敗する（行単位でも失敗する）。

- **回避**: JSONL の解析は PowerShell でやらず **Python** で書く
  （`tools/sync-harness.py` や `log-effort.py` と同じ）。

## 運用

- ここに無い落とし穴を新たに確立したら、まずプロジェクトの `learnings.md` に記録し、
  振り返り（/10-retrospective）で汎用性があると判断されたらこのスキルへ還流する。
- 各回避策は「成功したコマンドの形」をそのまま使う（形を変えると別の穴を踏む）。
