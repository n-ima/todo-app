# PLATFORM.md — プラットフォーム対応の詳細（AGENTS.md の参照先）

AGENTS.md（憲法）から移設された参照情報。**振る舞いの規範は AGENTS.md が正**であり、
本書は「各環境で何がどう効くか」の詳細事実を保持する。更新するときは AGENTS.md の
要約と矛盾しないこと（矛盾したら AGENTS.md が勝つ）。

## アダプタ構成（プラットフォーム固有の入口。ポインタか正の展開で、独自の振る舞いを持たない）

- **Claude Code**: `CLAUDE.md`（AGENTS.md をインポート）、`.claude/commands/`
  （スラッシュコマンド→正を参照）、`.claude/agents/`（サブエージェント。正 `.github/agents/*.agent.md` の本文を
  展開した生成物。公式どおり本文は起動時にだけ読まれ常駐しないため、ポインタで往復させない。A5-6）、
  `.claude/skills/`（スキルポインタ。Claude Code はプロジェクトスコープでは `.claude/skills/` のみ探索するため。ユーザースコープ ~/.claude/skills とプラグイン由来は別枠）、
  `.claude/settings.json`（同じフックスクリプトを Claude Code スキーマで配線）、
  `.claude/rules/`（パス限定ルール。`.github/instructions/*.instructions.md` の `applyTo` を `paths:` に写した
  生成物＝`tools/generate-adapters.py` 第7節 `tools/path_rules.py`。該当ファイルを読むときだけ載る＝常駐予算の外。
  手書きの rule は置かない）。
  なお Claude Code はユーザー環境の `~/.claude/projects/<project>/memory/` に
  auto memory を書くことがある（正は docs/ と learnings.md。AGENTS.md コンテキスト管理節）
- **Antigravity**: AGENTS.md を直接読む + `.agents/workflows/`（/コマンド→正を参照）
- **Copilot (VS Code)**: AGENTS.md・`.github/agents/`・`.github/prompts/`・
  `.github/skills/`・`.github/hooks/` を直接読む（正の層そのもの）。ただし現在既定の
  **Agent Host は `.github/prompts/` を読まない**（prompt files は Local ハーネス限定・将来削除）
  ため、Agent Host の入口は `.github/skills/<nn>-<name>/SKILL.md`（prompt files から
  `tools/generate-adapters.py` 第5節が生成する user-invocable スキル。振る舞いを持たないポインタ。
  下記「Copilot Agent Host の入口」）

## 読み替え規則

- `runSubagent` は Claude Code では Agent ツール（旧称 Task。`.claude/agents/` の同名
  サブエージェント。フックの matcher は `Agent|Task` で両名に効かせる）、
  Antigravity では Agent Manager の別エージェント会話。
- ハンドオフボタンは Copilot 専用。他環境では send:false のハンドオフは
  「新しいセッションで /コマンド実行」の案内、send:true のハンドオフは
  同一セッション内でのロール切替続行に読み替える（generate-adapters.py が
  アダプタへ自動反映する。D048）。

## ガードレールの強度差（機能別の劣化モード。2026-08 時点の再検証。D046）

- **Copilot (VS Code)**: フック（`.github/hooks/`、Preview）で deny/ask を機械的に強制。
  フェーズ外編集の強制（`guard-phase-scope`。運用中・着手前=deny / 未初期化=ask）も
  PreToolUse で効く。
  現在は **UserPromptSubmit / Stop を含む8イベント**をサポートする。
  **実機確認済み（D058・VS Code 手動E2E 2026-08-30）**: VS Code は `.claude/settings.json`
  のフック定義を「Claude Code フックを使用できます。有効にする」と **opt-in で提案**し、
  **既定では読み込まない**。したがって①同一スクリプトの**二重発火は既定で発生しない**
  （既定は `.github/hooks/` の片系のみ。ユーザーが有効化した場合のみ両系が動くが、
  ガード類は冪等なので実害は警告・ログ・受付注入テキストの重複に留まる）。
  **ただしこれは Local ハーネス限定の事実**: 現在既定の **Agent Host** と Copilot CLI は
  `.claude/settings.json`・`.claude/skills`・`CLAUDE.md` も既定で走査し、同一ガードを
  **重複排除なし・直列**で実行する（再監査 2026-09-09 CP-3。下表）。本ハーネスは
  `.vscode/settings.json` の `chat.hookFilesLocations` / `chat.useClaudeMdFile: false` で
  読込元を固定して抑止する（validate-harness が不変条件として検査。Agent Host での
  実機確認は未了）。②従来
  `.claude/settings.json` にのみ配線されていた受付ルーチン（`route-request`）と
  記録リマインド（`remind-record`）は
  **既定の Copilot には届かない**。このうち受付ルーチンは **D060 で解消**:
  route-request を .ps1 移植し `gate-hooks.json` の UserPromptSubmit に配線した
  （.sh と注入文はバイト同一・selftest 6分岐）。remind-record（Stop）は Copilot 側の
  Stop 未配線のため引き続き Claude Code のみ（記録リマインドは AGENTS.md が代替）。
  **承認モードと自律実行（D068 実測）**: VS Code の承認モードは3種
  （既定の承認 / 承認のバイパス=全ツール自動承認 / **Autopilot(プレビュー)=最初から
  最後まで自律反復**）。ハンドオフの実行先も4種（現セッション/Copilot CLI/クラウド/
  Claude）が選べる。**Autopilot の実測**: エージェントがテスト実行を人に依頼して
  停止した場面を Autopilot が自己続行し、「Assumed tests passed」のままテスト未実行で
  done 遷移・CR 完了まで進んだ（証拠なし done。結果が正しかったのは運）。したがって
  ①Autopilot は**実行権のあるエージェント（implement / test）でのみ**使う
  ②チャット上の人手ゲート（承認①②・証拠待ち）は Autopilot 中は**信頼できない**
  （自己続行が返答を仮定で埋める）— 人手ゲートを跨ぐ工程では使わない
  ③deny 型ガードは Autopilot でも有効（承認ではなく拒否のため）。
  **運用注意（D059）**: VS Code のフック確認で「すべて許可」を選ぶと、以後の同種確認が
  セッション内で包括承認され、ask 型ガードが事実上無効化され得る。**実測（D062）**:
  個別承認でも承認記憶が次セッションに残り ask が発火しない事例を観測したため、
  フェーズ外編集は ask でなく deny に格上げした（deny は承認記憶の影響を受けない）。
  確認は「一度のみ許可」を基本とする。読み取り系ツールへの誤 ask（警報疲れ→包括許可を
  誘発する連鎖の起点）は D059 でツール名フィルタにより除去済み。
  **入口の劣化（Agent Host。A6-17 / CP-2）**: prompt files の `agent:` バインド（D008）は
  Agent Host では効かない（prompt files 自体が読まれない）。入口スキル
  `.github/skills/<nn>-<name>/` の本文が「担当 `.agent.md` を読んで役割を設定し prompt 本文を
  実行する」と指示する**指示層**の役割設定になり、`tools` / `agents` の最小権限とハンドオフは
  エージェントピッカーで担当エージェントを選んで実行した場合にだけ機械的に効く
  （詳細は下記「Copilot Agent Host の入口」）。
- **Claude Code**: 同じフックスクリプト（`.claude/settings.json` の hooks）に加えて
  ツールレベルの `permissions` を併用する。**`permissions.deny` が守るのは
  ファイル編集（Edit/Write）のパスと、サブエージェント呼出の明示 `model` パラメータ
  （`Agent(model:…)`。呼出時に渡された literal のみ一致し、frontmatter / inherit 由来には
  効かない＝「モデル/effort の方針」節の三層の第1層）**（NotebookEdit は `.ipynb` 専用ツールで
  ハーネス設定ファイルには適用されないため deny 不要）であり、コマンド実行
  （Bash / PowerShell の両ツール）はフックに加えて `permissions.ask`
  （`git push`/`git tag`/`git reset --hard` の確認。Bash・PowerShell 両方に定義）で
  二重化している（フックが壊れてもツール層の確認が残る）。ファイル編集・コマンド実行・
  受付・記録のすべてが機械的ガードを持ち、対応環境の中で最も強い。
  **既知の限界**: Bash 経由のファイル書き込み（`echo > file` 等）は `permissions.deny` と
  `guard-phase-scope` のどちらの検査対象にもならない（フックはツール名とパスで判定するため）。
- **Antigravity**（現状維持・凍結。以後の検証・拡張対象は Copilot / Claude Code の2環境。D057）: **IDE(GUI)はプロジェクト内のスクリプトフックを読まない**
  （姉妹プロジェクト ai-manager での実機検証により判明した知見）。機械的な保護は
  IDE の **Deny List（Settings → Permissions → Advanced）** に危険コマンド
  （`git push --force`, `rm -rf` 等）を登録して代替し、それ以外は指示レベルの遵守＋
  Git 側の保護（ブランチ保護/CODEOWNERS）に依存する。重要なリリース判断は
  フックが効く環境（Copilot/Claude Code）で行うことを推奨。

### フックの読込元・実行意味論の環境差（再監査 2026-09-09 CP-3 / CP-6 / SC-5）

| 項目 | Claude Code | VS Code Agent Host（既定） | VS Code Local ハーネス | Copilot CLI | Copilot cloud agent |
|---|---|---|---|---|---|
| フック読込元 | `.claude/settings.json`（`.github/hooks` は読まない） | `.github/hooks` ＋ **`.claude/settings.json` も既定で読む** | `.github/hooks`。`.claude/settings.json` は opt-in（D058） | `.github/hooks` ＋ `.claude/settings.json` も既定で読む | **既定ブランチの `.github/hooks` のみ** |
| 重複排除 | あり（同一コマンドは1回） | **なし**（同一ガードが2回走る。`chat.hookFilesLocations` で抑止） | 門あり（既定は片系） | **なし** | — |
| 同一イベントの複数フック | 並列 | **直列** | 直列 | 直列 | 直列 |
| timeout 超過 | fail-open（そのフックの判定なし） | fail-open | fail-open | fail-open | fail-open |
| exit code 意味論 | 0=許可（stdout JSON で decision）・2=ブロック（stderr がモデルへ）・その他=非ブロッキングエラー | stdout JSON の `permissionDecision` が正。非 0 は無視されて許可側 | 同左 | 同左（`version: 1` 形式・`windows` キーの扱いは未確認） | 書込は破棄され GitHub/Copilot ホスト以外へ到達不可 |
| ask の扱い | 対話で確認 | 対話で確認（「すべて許可」で包括承認され得る＝D059） | 同左 | 対話。`--autopilot`/非対話では deny 相当 | **非対話のため deny 扱い**（G-2。push/tag が常に拒否） |
| Native Windows の sandbox | **非対応**（hooks＋deny が唯一の防御。WSL2 推奨） | 非対応 | 非対応 | 非対応 | — |

読み方: Windows でのフック実行は重いガードが直列で走ると timeout 5s を超えて fail-open に
なり得る（SC-5）。フック数を増やすより、matcher・`if` フィールドで発火回数を減らす方向で
対処する（CC-6）。

### Copilot Agent Host の入口（prompts→skills。再監査 2026-09-09 CP-2 / A6-17）

- **事実（公式 agent-customization overview、2026-09-10 取得）**: 「Prompt files are deprecated for
  Agent Host sessions and aren't loaded by Agent Host」。Local ハーネスでは当面動くが Local は将来
  削除される。公式の移行先は Agent Skills（「Convert workspace and user prompt files to agent skills」）。
  `chat.agentFilesLocations` / `chat.promptFilesLocations` / `chat.instructionsFilesLocations` /
  `chat.agentSkillsLocations`（+ `chat.modeFilesLocations`）は Local 向けの非推奨設定で、
  Agent Host セッションは使わない（Agent Host はプロジェクトの `.github/skills`・`.claude/skills`・
  `.agents/skills` を既定で走査する）。
- **正は変えない**: 入口の正は引き続き `.github/prompts/*.prompt.md`（`agent:` バインド含む。D008）。
  Claude Code の `.claude/commands/`（第1節）はこの正から動作実証済み（D049）なので壊さない。
- **生成物**: `python tools/generate-adapters.py` 第5節（`tools/copilot_entry_skills.py`）が prompt
  ごとに `.github/skills/<prompt base 名>/SKILL.md` を生成する。frontmatter は `name`（= フォルダ名 =
  prompt base 名）/ `description`（prompt の description。上限 1,024 字＝VS Code docs・agentskills.io）/
  `user-invocable: true` / `disable-model-invocation: false`（受付ルーチンがモデル起動する必要がある）/
  `metadata.harness-entry: copilot`（入口スキルの印。独自キーは agentskills.io 仕様どおり `metadata` 配下）。
  本文は「担当 `.agent.md` を読んで役割を設定 → prompt 本文を実行 → ハンドオフの読み替え
  （send:false は『新しいチャットで /<スキル名>』、send:true は同一チャットで自動継続）」の薄いアダプタ。
  先頭行の `<!-- generated from … -->` が生成マーカー（手で編集しない。prompt / agent の handoffs を
  変えたら再生成）。`generate-adapters.py --check` と `validate-harness.py` (l) が鮮度・孤児・未生成を検査する。
- **Claude Code 側には `.claude/skills/<nn>-*/` のポインタを作らない**（`/<nn>-…` のスラッシュ名が
  `.claude/commands/<nn>-….md` と衝突する。validate が存在すれば ERROR）。`.vscode/settings.json` の
  `chat.agentSkillsLocations` は `.claude/skills: false` のまま（Local ハーネス向け。Agent Host では非推奨）。
  gen-docs のスキル数（手順部品）には入口スキルを数えない。
- **劣化モード**: Agent Host では役割設定が本文指示のみ（指示層）になり、`tools` / `agents` の
  最小権限とハンドオフボタンはピッカーで担当エージェントを選んだ場合にだけ機械的に効く（上記
  「入口の劣化」）。`runSubagent` は Agent Host でもそのまま動く。
- **未確認（copilot 未導入。COPILOT-E2E §1 の 1-4 / 1-5 で実機確認する）**: Agents ウィンドウでの
  `/<nn>-<name>` の補完表示、本文指示による役割設定の遵守、Local ハーネスで同名の prompt file と
  入口スキルが並んだときの優先順位。
- **Local ハーネス廃止時の撤去条件**: VS Code が Local ハーネスを削除した版を PLATFORM の最低版表に
  記録し、その版以上を要求版にした時点で `.vscode/settings.json` の非推奨 4 設定
  （`chat.agentFilesLocations` / `chat.promptFilesLocations` / `chat.instructionsFilesLocations` /
  `chat.agentSkillsLocations`）と validate (e-3) の `chat.agentSkillsLocations` 検査を撤去する
  （`chat.hookFilesLocations` / `chat.useClaudeMdFile` は Agent Host でも有効なので残す）。

## セキュリティの層構造（強制層と多層防御の区別）

- **強制層（enforcement）= sandbox**: OS レベルの隔離だけが「エージェントが何を
  しても越えられない」境界を作る。Claude Code の `sandbox.enabled` は
  macOS / Linux / WSL2 で利用でき、**対応環境では有効化を推奨**する。
- **多層防御（defense in depth）= deny・フック**: `permissions.deny` とフックは
  ツール呼び出しの経路を検査する層であり、迂回経路（Bash 経由のファイル書き込み等）が
  原理的に残る。「1層が壊れても他の層が残る」ための重ね掛けとして位置づけ、
  単独で完全な防御とはみなさない。
- **ネイティブ Windows は sandbox 非対応**のため、強制層なしの運用になる。代替は
  次のいずれか（併用可）: (1) WSL2 への移行（sandbox が使える）、
  (2) deny + フック + Git 側保護（push protection / ブランチ保護）の重ね掛け、
  (3) E2E 等の自律実行は使い捨てディレクトリ（壊れてよい clone）で行う。
- **ハーネス設定ガードの信頼境界（R-01。codex 監査 2026-08-31 §4 / 再監査 2026-08-31 C-1）**:
  `guard-harness-config-edit` / `guard-dangerous-git`（ツール入力の文字列検査）は**第 1 防衛線**、
  ConfigChange の `guard-config-change.py`（書かれた結果を git HEAD と比べる。D080）は**第 2 防衛線**で、
  どちらも「保護域への全書込経路」を閉じる境界ではない。interpreter 内の書込（`python -c` / `node -e`）・
  git plumbing（`git update-index` / `git apply` / `git checkout -- <保護パス>` / `git restore` /
  `git stash pop` / `git read-tree` / `git worktree add <保護域内>`）・reparse point（`mklink` /
  `New-Item -ItemType SymbolicLink|Junction` / `ln -s` で保護ディレクトリを指す・保護域内に作る）は
  第 1 防衛線が ask に倒す（2026-09-17。selftest 両系で各経路の陽性・陰性を固定）が、文字列パターンは
  網羅を主張しない（変数間接・別名・エンコードは常に残る）。**境界**は OS sandbox（`sandbox.enabled`。
  macOS / Linux / WSL2）・read-only mount（保護域を読み取り専用でマウントする運用）・branch protection
  （GitHub のブランチ保護＋`.github/CODEOWNERS` の required review＝IA-20260831-05。CODEOWNERS は雛形で、
  有効化は人が GitHub 設定で行う。ハーネスは有効化を検査できない）。ネイティブ Windows では境界は
  Git 側保護だけになる。reparse point の Windows 実機（`mklink` は既定で管理者権限が要る）は未検証。

## リリースの権限分離（Rule of Two。codex 監査 2026-08-31 C-01 / C-02 = IA-20260831-01 / 02）

- **Rule of Two（AGENTS.md の Lethal Trifecta 判定の運用形）**: (A) 信頼できない入力（Web・外部 Skill・外部文書）、
  (B) 私的データ・資格情報（デプロイ鍵・CI トークン・環境設定）、(C) 外部反映（push・タグ・デプロイ・公開・
  外部 API の書込）のうち、**1 セッション（1 エージェント）が同時に持つのは 2 つまで**。
- **release の分離**: `release` は Web を持たない（`tools` から `web` を外し、`reviewer` の起動だけ持つ）。
  計画（release: `release-checklist.md` の action packet を作る）→ 独立検証（`reviewer`: 読み取り専用・別コンテキストで
  packet と成果物を照合し、`review-log.md` にフェーズ `release` で記録）→ 承認（人間: packet の行ごとに exact-action で
  承認）→ 実行（release: 承認された行を承認された引数のまま一度だけ）。外部手順の調査や deploy Skill の作成は
  release セッションではなく `skill-authoring` の別セッションで行い、**同一セッションで作成した Skill は実行しない**
  （fresh-session 導入審査。レビュー済みの印「レビュー: 誰が・YYYY-MM-DD」が付いてから新しいチャットで /09 を再開）。
- **承認の単位（exact-action）**: 「何を・どこへ・どの引数・どのコミット・いつまで」を action packet の行に固定し、
  フック（`guard-dangerous-git` = git の push / tag / force、`guard-external-effect` = IaC・クラウド / PaaS CLI・
  コンテナレジストリ・パッケージ公開・gh / glab の書込・HTTP の書込メソッド・ssh / scp / rsync・本番マーカー付きの
  migrate / deploy・MCP の書込系ツール）が実行時にも同じ単位で ask を入れ、理由文に「何を・どこへ」を示す。
  対象や引数が変われば再承認。`environment.md` の「自動」は**準備（ビルド・パッケージング・dry-run / plan・
  ローカル検証・チェックリスト）を確認なしに進めてよい**という意味で、外部反映の承認を省略する意味ではない
  （codex C-01: 規範と release.agent.md の文面が反転していた点の是正。environment_template.md に同じ 1 段落）。
- **承認バイパス下は deny**: VS Code の「承認のバイパス」/ Autopilot、Claude Code の `bypassPermissions` / `dontAsk`
  （payload の `permission_mode`）、または環境変数 `HARNESS_EXTERNAL_EFFECT_MODE=deny` の下では、ask が消音されて
  素通りになるため両フックとも **deny** に倒す（通常モードで人間の承認を経て実行する）。Copilot 経路の payload には
  `permission_mode` が無いため、Autopilot で使う場合は環境変数を設定する（劣化モード。selftest 両系で固定）。
- **既知の限界**: interpreter 内の `fetch()` / `requests.post()`、変数に隠したコマンド（`$cmd`）は文字列検査では
  見えない（R-01 と同じ信頼境界）。境界は OS sandbox・資格情報の非付与（環境変数を渡さない）・Git 側保護。
  49 セグメント以上のコマンドは評価前に ask（fail-closed。分割して実行する）。

## 最低安全バージョン表

**ガードの前提はホストツールの実装品質に依存する**。以下より古い版では、
本ハーネスのガードが設計どおりに効かない既知の問題がある。

<!-- BEGIN GENERATED: platform-requirements -->
（`python tools/gen-docs.py` が `.github/harness/platform-requirements.json`（as_of 2026-09-10）から生成。手で編集しない。検査: `python tools/gen-docs.py --check` / `python tools/validate-harness.py`。**正の分担（1 行）**: ホストに要求する版（min / required / 評価装置の起動門）とフック・予算・CLI フラグ等ホスト機能の導入版は platform-requirements.json、サブエージェントのモデル解決・effort・FORCE などモデル関連機能の導入版は model-policy.yml `min_version`（下の「モデル関連機能の最低版」表）が正。）

| ツール | 最低版 | 機能キー | 理由 |
|---|---|---|---|
| Claude Code | >= 2.1.90 | `deny_bulk_bypass_fix` | deny 規則が50サブコマンド超の一括呼び出しで無音バイパスされるバグの修正版 |
| Claude Code | >= 2.1.217 | `budget_subagent_inclusive` | `--max-budget-usd` がサブエージェント分を合算して強制停止しない問題の修正版(評価装置 v2 の `budget_enforcement=subagent-inclusive` の境界) |
| Claude Code | >= 2.1.232 | `subagent_background_default` | サブエージェントの既定が background 起動に変更(PostToolUse(Agent) は `async_launched` で usage 無し・resolvedModel のみ)。実行モデルの記録経路(`log-subagent.py`)がこの版で変わる |
| Claude Code | >= 2.1.234 | `project_dir_name_env` | `CLAUDE_CODE_PROJECT_DIR_NAME`(評価装置 v2 がユーザースコープ隔離の transcript 探索に使う) |
| Claude Code | >= 2.1.239 | `residency_premium_1_1x` | 費用推定(`--max-budget-usd`・`/cost`・`total_cost_usd`)にデータ常駐ワークスペースの 1.1x プレミアムが加算される版。装置 v1(2.1.201)の費用と定義が異なるため結果 JSON の `conditions.residency_premium_in_estimate` / `data_residency_premium_1_1x` で区別する |
| Claude Code | >= 2.1.251 | `model_switch_hooks` | PreModelSwitch/PostModelSwitch 追加(`log-model-switch.py` の発火下限。未満では発火しないだけで fail-open。2.1.201 で発火しないことを実プローブで確認=再監査 §1.1)。評価装置 v2(`tools/e2e-run.py`)はこの版未満を exit 2 で拒否する(`--allow-old-cli` で続行しても `valid_for_comparison=false`)。同版のサブエージェント model 解決順の反転と Fable 5.1 対応(未満は API 400)は model-policy.yml(`resolution_order` / `models.fable-5.1`)が正 |
| Claude Code | >= 2.1.257 | `deny_subshell_eval` | `permissions.deny` がサブシェル・リダイレクト内も評価。A/B の同一モデル固定(`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`。導入版は model-policy.yml `subagent_model_force` が正)の推奨版で、結果 JSON の `conditions.subagent_model_forced` に記録される。`fable` alias の既定が Fable 5.1 になる版(同じく policy が正) |
| Claude Code | >= 2.1.259 | `permission_prompts_none` | `--permission-prompts none`・`--init-only`・Setup フック。`--bare` が将来 `-p` の既定になる予告(e2e/CI の起動契約が変わる) |
| Claude Code | 2.1.2xx（導入版未確認） | `hooks_if` | hooks の `if`(許可ルール構文でツール呼び出しを絞り、不一致なら起動しない。CC-6 / A2-7)。公式 hooks リファレンス(2026-09-17 確認)に導入版の記載がなく、CHANGELOG(2.1.257 まで)にも無い。guard 系には適用しない(`/usr/bin/git push`・`sh -c`・`git -C . push` が `Bash(git push *)` に一致せず素通り=公式の一致表。A6-18 の判断どおり)。docs 限定の PostToolUse 警告 3 本への `Edit(docs/**)` は候補だが 8.3 短縮名・バックスラッシュ区切りの一致と 2.1.201 での挙動が未検証のため既定では付けない(適用可否は tools/hook-metrics.py の発火数で判断) |
| Claude Code | 2.1.2xx（導入版未確認） | `stop_failure_instructions_loaded` | `StopFailure` / `InstructionsLoaded` の発火下限(`mark-abnormal-stop.py` / `log-instructions-loaded.py`)。公式 hooks リファレンス(2026-09-10)に導入版の記載なし。未満の版では発火しないだけで fail-open。開発機を 2.1.265 以上へ更新後、`logs/abnormal-stop-*.json` / `logs/instructions-loaded.jsonl` の生成で実測して版数を確定する |

**最低版: Claude Code >= 2.1.217**（`budget_subagent_inclusive`。未満は `python tools/doctor.py` が FAIL、`tools/validate-harness.py` が WARN）／**要求版: >= 2.1.257**（`deny_subshell_eval`。未満は doctor が WARN）。`--max-budget-usd` がサブエージェント分を合算して強制停止する最初の版。これ未満ではコスト浪費型の暴走を予算で止められない(doctor は FAIL)。`permissions.deny` がサブシェル・リダイレクト内も評価される版。評価装置 v2 の固定条件 C の推奨版でもある(同版のサブエージェント一括固定 FORCE と fable alias 既定の導入版は model-policy.yml `subagent_model_force` / `models.fable-5.1` が正)。未達は doctor が WARN。開発機の実機版: 2.1.201（開発機の実機版(再監査 2026-09-09 CC-1: 自ら宣言した最低版 2.1.217 未満)。selftest 両系はフックの実機発火を含まず stdin ペイロードでスクリプトを直接実行している。開発機を更新したら本値と model-policy.yml の verified を上げる）。CI の導入版（CI 緑を確認した版。`python tools/host-canary.py` の比較元）: 2.1.267（CI(.github/workflows/harness-ci.yml)の npm 版固定 @anthropic-ai/claude-code@x.y.z で導入し、全ジョブ緑を確認した版(run 35162514385、2026-09-17。d17704a で 2.1.267 に固定)。harness-ci.yml の版固定はこの値の鏡(tools/validate-harness.py (v) が一致を検査)。tools/host-canary.py が CI の実機版と比べ、前進していれば Day-0 再ベンチ儀式(PLATFORM.md)を案内する。pin を上げるときは CI 緑を確認してから本値を更新する(A3-6)）。評価装置 v2（`tools/e2e-run.py`）の起動門は 2.1.251（`model_switch_hooks`。評価装置 v2 の起動門(D078 固定条件 C)。未満は exit 2。推奨版は claude_code.required）。ホスト以外の前提ツール（python / PyYAML / git / Git Bash / node / jq）の要否は同 JSON の `tools` と `python tools/doctor.py` の表が正。
<!-- END GENERATED: platform-requirements -->

再監査 2026-09-09 CC-1: 実機 2.1.201 は自ら宣言した 2.1.217 未満で、A/B 結果 10 本すべてが
予算合算なしの版で採取された（D078 で参考値化）。環境側の検査は `python tools/doctor.py`
（`claude --version` の FAIL/WARN・前提ツール・フック配線・statusLine の実行テスト・ワークスペース信頼・
配布鮮度）が正で、`tools/validate-harness.py` (n) は同じ JSON で最低版未満を WARN、
`tools/e2e-run.py` の起動門も同じ JSON を読む（定数のハードコードなし）。開発機の更新は A6-8 で追う。

## ビルトイン委譲の対応表（自作機構 vs 公式機能）

本ハーネスの自作機構と、後から登場した公式機能の対応。公式が同等以上になった時点で
自作の削除（de-scaffolding）を検討する。

判断には**優位の根拠**（実測 > 論拠 > 未評価）と**再評価トリガ**を必ず添える
（根拠なき「維持」は出遅れの温床。D070）。

| 自作機構 | 対応する公式機能 | 現在の判断 | 優位の根拠 | 再評価トリガ |
|---|---|---|---|---|
| watchdog-continue（有界の自動継続） | `/goal`（目標駆動の自律継続） | opt-in 維持で確定（D068） | **実測**（Autopilot で「証拠なし自己続行」の実害を観測。同型リスク） | /goal が証拠検証つき継続を提供したら |
| probing（読み取り専用の接地。指示レベル） | プランモード（`--permission-mode plan` / サブエージェント `permissionMode: plan`） | **採らない**（A5-4。2026-09-17 評価） | 論拠（公式 permission-modes: plan は「編集を全面ブロックし、計画の承認で編集モードへ切替」＝要件・設計フェーズは `docs/` を書く工程で読み取り専用ではない。読み取り専用の強制は `spec-critic` / `reviewer` の `tools`（Read / Grep / Glob）と `guard-phase-scope`（app コードは ask・docs は許可）で既にパス粒度。サブエージェントの `permissionMode` は auto モードでは無視される（公式）。Copilot に対応機能なし。実機 2.1.201 の遷移は未計測） | plan モードが `docs/` 書込を許すパス粒度の許可を得たら／実機で接地の抜け（推測で書き始めた例）が観測されたら |
| git 運用 + done契約 | checkpoint / `/rewind` | git 維持（D069） | 論拠（公式制限: Bash 編集非追跡・サブエージェント編集非復元） | checkpoint が両編集を追跡したら |
| セッション分割 + docs 再読 | `/resume`・`--continue`・auto-compact | 分割維持（D069） | **実測**（分割で $69→$49=29%減・PASS 維持。context rot 40% の外部実測とも整合） | 実測優位が崩れたら |
| reviewer サブエージェント | ビルトイン `/code-review` | reviewer 維持・日常差分は併用可（D071 実測で確定） | **実測**（頭合わせ: 一般バグは 4-5/5 で同等だが、done契約照合=証拠すり替えの検出は reviewer のみ 5/5・ビルトイン 4/5。文脈依存検査に固有優位） | ビルトインが done契約/tasks.md 相当の文脈検査を提供したら |
| release-security-review スキル | ビルトイン security 4層 | **検出=ビルトインへ委譲・統合=自前**（D070。遮蔽解除・改名） | 役割分担のため競合なし。検出力は仕込み脆弱性の頭合わせで実測（D070 記録） | ビルトインがレポート様式・ゲート統合まで提供したら |
| 配布 sync（`tools/sync-harness.py`） | Agent Plugins 1.0 | 併用 | 論拠（brownfield 注入・逆同期は Plugins に無い機能） | Plugins が brownfield 注入を提供したら |

Claude Code のビルトイン security は4層スタックを持つ:
security-guidance（実装中の助言）→ `/security-review`（オンデマンドの差分レビュー）→
プラグイン層 → GitHub Action（PR ごとの自動レビュー）。
**役割分担（D070「検出は委譲・統合を所有」）**: 検出エンジンは継続更新される
ビルトイン群が正（旧 `security-review` スキルはビルトインを同名遮蔽していたため
`release-security-review` に改名して解除）。本ハーネスが所有するのは**リリースゲート
統合**のみ — 検出結果を `security-review-report.md` の様式へ集約し、要件・NFR へ
紐づけ、レポート無き release done 遷移を warn-gate-tamper が機械検知する。
ビルトインが無い環境（Copilot 等）ではスキル内蔵の8ステップ手順（awesome-copilot
公式由来）が検出のフォールバック。検出の凍結コピーを正としない理由: 検出手法は
プラットフォームが継続更新する領域であり、コピーの所有は出遅れの製造装置になるため。
依存の無音破壊はカナリア（`python tools/host-canary.py`。委譲先の台帳は
`.github/harness/builtin-dependencies.json`＝この表の委譲・併用行と CI が前提にする CLI フラグを
`used_by` つきで列挙し、`validate-harness.py` (t) が台帳と本文の言及の食い違いを検査する。CI の
claude 導入後ステップで存在を機械確認し、無ければ WARN＝赤にはしない。A5-8）で検知する。

## エージェント構成の詳細

- `.github/agents/*.agent.md` — フェーズごとの専属エージェント（旧chatmode）。
  `orchestrator` → `requirements` → `design` → `implement` → `test` → `release` の順で
  `handoffs` により引き継ぐ。
- `reviewer.agent.md` — **サブエージェント**。`test` エージェントが `runSubagent` で
  呼び出す、実装とは独立したコンテキストのレビュー担当（読み取り専用）。
  「実装した本人がそのままレビューして自己承認する」ことを防ぐための別セッションレビュー。
- `task-worker.agent.md` — **サブエージェント**。`implement` エージェントがタスクを
  1つ実装するたびに `runSubagent` で呼び出す。実装ループ全体を1つの長い会話に
  しないためのコンテキストロット対策。
- `spec-critic.agent.md` — **サブエージェント**。`requirements`/`design` エージェントが
  ゲート承認を求める前に呼び出す、要件定義書・設計書の独立レビュー担当（読み取り専用）。
  上流の欠陥は下流ほど修正コストが大きいため、コードレビューより先に独立レビューを置く。
- `change.agent.md` — **運用中の変更請求（`/12-change-request`）の専属**エージェント。
  差分駆動の4分類（軽微/要件・設計変更/バグ修正/緊急ホットフィックス）＋小規模
  ファストパスで変更を処理し、サブエージェント3種（task-worker / spec-critic /
  reviewer）を初回構築時と同じ理由で再適用する。
- `harness-maintainer.agent.md` — **ハーネス本体リポジトリ専用**の保守エージェント。
  振り返りの本体適用（`/90-apply-retrospective`）と `DECISIONS.md` 記録を担う。
  個別プロジェクトでは使わない。
- `.github/skills/*/SKILL.md` — 各フェーズで使う手順・チェックリストの部品。
  `deploy-<environment>` や `stack-conventions` はプロジェクトごとに実際の環境・
  スタックが判明した時点で設計/リリースエージェントが動的に追加する
  （全環境・全スタックの事前収録はしない。既存の公式/コミュニティ Skill の流用を優先し、
  ゼロから書くのは最後の手段）。
- `.github/prompts/*.prompt.md` — フェーズを進めるためのスラッシュコマンド。
  frontmatter の `agent:` で専属エージェントにバインドしてあり、どのエージェントを
  選択中でも実行した瞬間に正しいツール制限・振る舞いへ切り替わる。
- `.github/hooks/` — フェーズゲート・セキュリティを補強する機械的なフック。
  Preview 機能のため過信せず、AGENTS.md の指示レベルのルールと併用する。
  一覧と配線は `.github/hooks/README.md`。

### 起動経路の等価性ルール（詳細）

エージェントの起動経路は (1)プロンプト実行 (2)ハンドオフボタン (3)runSubagent の
3つあり、**ハンドオフ経由では `.prompt.md` は読み込まれない**。そのため振る舞いの正は
常に `.agent.md`（またはスキル）に置き、プロンプトには薄い起動指示だけを書く。
プロンプト本文の太り（手順の再記述）は validate-harness.py が WARN で検知する（D048）。

## サブエージェント既定5経路の詳細

- `requirements → spec-critic`（ゲート承認前に1回）: 要件定義書の抜け・曖昧さの独立レビュー
- `design → spec-critic`（ゲート承認前に1回）: 設計書のトレーサビリティ・実装可能性の独立レビュー
- `implement → task-worker`（タスク数だけ）: コンテキストロット対策の実装分離
- `implement / test → reviewer`（implement は完了タスク10個ごと＋全タスク完了時、test は1回）:
  コードの正しさ・セキュリティの独立レビュー。記録（`docs/04-test/review-log.md`）が
  implementation / test の done 条件（A6-14。末尾「独立レビューのチェックポイント」）
- `change → task-worker / spec-critic / reviewer`（運用中の変更請求 `/12`）:
  実装は task-worker に1タスクずつ、分類2の再ゲート前に spec-critic を1回、
  コードを変更した CR の完了前に reviewer を1回（各経路と同じ理由の再適用）

spec-critic の2回は「最も費用対効果の高いレビュー」として既定に含めている
（上流の欠陥ほど下流での修正コストが大きいため）。

## モデル/effort の方針（正は `.github/harness/model-policy.yml`）

役割別のモデル・effort は `.github/harness/model-policy.yml` の 1 ファイルが正で、
`.claude/agents/` の frontmatter・`.github/agents/` の `model:` 行・`.github/copilot/settings.json`・
`permissions.deny` の `Agent(model:…)`・`guard-subagent-model` の役割表・以下の対照表はすべて
生成物（`python tools/generate-adapters.py`）。D007（Copilot の `model: auto` 既定＝仕様外）と
D040 決定5（task-worker の Sonnet 化・旧解決順・旧単価）はこの方針で supersede した（DECISIONS.md）。
AGENTS.md には方針本文を置かず（常時ロード予算）、ここを参照先にする。

<!-- BEGIN GENERATED: model-policy-platform -->
（`python tools/generate-adapters.py` が `.github/harness/model-policy.yml`（取得 2026-09-10）から生成。手で編集しない。検査: `python tools/generate-adapters.py --check` / `python tools/validate-harness.py`）

**上書き（方針より強い層）**: `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` の環境では役割別 `model` は全て無効（fork と `model: inherit` のスキルを除く。v2.1.257+）。`CLAUDE_CODE_EFFORT_LEVEL` は役割別 `effort` と工程別オーバーレイ（`phase_overrides`）を全て上書きする。

### 役割×モデル×effort（既定。A/B 前は挙動不変・`ab_evidence` が入るまで固定）

| 役割 | 種別 | Claude Code model / effort | 生成先 | Copilot VS Code model | Copilot CLI model / effortLevel | 下限固定 | 付帯 | A/B 後の候補 |
|---|---|---|---|---|---|---|---|---|
| `orchestrator` | phase | inherit / inherit | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | — | — | medium / low(判断は GATE_STATUS の機械読取が主。low で progress.md を読まずに答える副作用に注意) |
| `requirements` | phase | inherit / high | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | never_low | — | 変更なし(上流コストの主因は継続セッションの cache_read。対策は分割=D057 と往復削減) |
| `design` | phase | inherit / high | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | never_low | — | 変更なし |
| `implement` | phase | inherit / inherit | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | — | — | medium(第2段。低 effort の executor は相談率が崩壊し escalate 条項が無効化される恐れ) |
| `test` | phase | inherit / inherit | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | — | — | medium(第2段) |
| `release` | phase | inherit / inherit | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | — | — | 変更なし |
| `change` | phase | inherit / inherit | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | — | — | medium(第2段) |
| `harness-maintainer` | maintainer | inherit / inherit | なし（commands 経由。`phase_overrides` のみ） | ピッカー継承 | 継承 / 既定(medium) | — | — | 対象外 |
| `reviewer` | subagent | inherit / high | `.claude/agents/`・`.github/copilot/settings.json` | ピッカー継承 | 継承 / high | never_low | background=false | 変更なし(advisor 実験は別枠) |
| `spec-critic` | subagent | inherit / high | `.claude/agents/`・`.github/copilot/settings.json` | ピッカー継承 | 継承 / high | never_low | background=false | 変更なし |
| `task-worker` | subagent | inherit / inherit | `.claude/agents/`・`.github/copilot/settings.json` | ピッカー継承 | 継承 / 既定(medium) | — | — | — |

候補外（`enforcement.deny_agent_model`）: `Claude Haiku 4.5`。`永久に inherit` ではなく「`ab_evidence` が入るまで既定」であることに注意（A/B の手順は監査 2026-09-09 §4.3・§6）。

### 方針と上書きの優先順位表（Claude Code / Copilot VS Code / Copilot CLI）

| 順位 | Claude Code（サブエージェント model。上が強い） | Copilot VS Code（runSubagent。上が強い） | Copilot CLI（設定は後勝ち = 下が強い） |
|---|---|---|---|
| 1 | `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`（全定義無視。v2.1.257+） | enterprise 管理既定（未指定時の継承先） | 1. 組込既定 |
| 2 | 呼出時 `model` パラメータ（`guard-subagent-model` が方針と照合） | 呼出時 model 引数 | 2. MDM 管理設定 |
| 3 | frontmatter `model`（**方針**・生成物） | `.agent.md` の `model`（**方針**・生成物。配列は CLI 1.0.83+） | 3. user settings（`~/.copilot/settings.json`） |
| 4 | `CLAUDE_CODE_SUBAGENT_MODEL`（v2.1.251+ は frontmatter より弱い） | ピッカーのモデル（親） | 4. **repo `.github/copilot/settings.json`（方針・生成物。trusted なディレクトリのみ）** |
| 5 | メイン会話のモデル（ユーザースコープ `model`・`/model`） | コストティアガード（親超えは起動拒否） | 5. `.github/copilot/settings.local.json`（gitignore） |
| 6 | — | — | 6. 環境変数 → 7. CLI フラグ |
| effort | `CLAUDE_CODE_EFFORT_LEVEL` > frontmatter `effort`（方針） > `/effort`・settings `effortLevel` > 既定 | **指定不可**（劣化モード） | `subagents.agents.<name>.effortLevel`（low / medium / high / xhigh。方針・生成物） |

### 劣化モード（ホスト別に効かないもの）

| ホスト | 効かないもの | 代替・記録 |
|---|---|---|
| Copilot VS Code | 役割別 effort（frontmatter に相当フィールド無し）。`model: auto` は frontmatter 仕様外 | コストを抑えるならピッカーで Auto を選ぶ。実行モデルは応答フッタのホバー（手動転記） |
| Copilot CLI | 親≧子ティアを超える子は**無言で親モデルへ降格**（#2758）。`model-policy: required` は 1.0.83+ 限定 | `--usage-output-file` の per-agent usage を証拠に添付 |
| Copilot cloud agent | custom agent の `model` は**未保証**（生成物から外し SOP のみ） | タスク開始時のピッカー（Auto / Opus 5 / Haiku 4.5）で選ぶ |
| Claude Code | availableModels 外の値は無言で代替（family alias は許可される最新版、他は inherited）。開発機 2.1.201 では FORCE（2.1.257）・解決順（2.1.251）・Fable 5.1 が未対応 | PostToolUse(Agent).`resolvedModel` / `-p` の `modelUsage` / `/tasks`（2.1.242+）を証拠に |

### 強制の三層（Claude Code。指示より機械）

1. `permissions.deny`（`Agent(model:haiku)`, `Agent(model:claude-haiku-4-5)`）— **呼出時の `model` パラメータの literal 一致のみ**に効く（公式: 省略されたパラメータは一致しない・alias はフル ID に一致しない）。frontmatter / inherit 由来には効かない。
2. PreToolUse（matcher `Agent|Task`）`guard-subagent-model.{sh,ps1}` — 役割表（生成物）と照合し、方針外の明示モデルは deny。省略時は既定が `inherit` の役割は素通し（既定が具体モデルの役割のみ `updatedInput` で**tool_input 全体を複製して model だけ差し替え**）。非 Agent ツール・`subagent_type` 欠落・表が読めない・壊れた JSON は無出力 exit 0（fail-open）。
3. PostToolUse(Agent) / SubagentStop の記録（`log-subagent.py`。`logs/usage/<session_id>.subagents.jsonl`）— `resolvedModel` / `modelsUsed` / `status`。**background 役割は `async_launched` しか取れない**（完了時の `modelsUsed` は前面実行のみ）ため、判定役は `background: false`。Stop / SubagentStop での block・再実行要求は使わない（fail-open 規律）。done 遷移時の `model_mismatch` 照合は次波（warn-gate-tamper の拡張）。

### モデル関連機能の最低版（docs 記載版 / 実機検証版）

| ホスト | 機能 | docs 記載版 | 実機検証版 |
|---|---|---|---|
| Claude Code | `resolved_model` | 2.1.174 | 未検証 |
| Claude Code | `agent_model_deny` | 2.1.178 | 未検証 |
| Claude Code | `model_inherit` | 2.1.200 | 未検証 |
| Claude Code | `models_used` | 2.1.212 | 未検証 |
| Claude Code | `tasks_model_display` | 2.1.242 | 未検証 |
| Claude Code | `resolution_order` | 2.1.251 | 未検証 |
| Claude Code | `subagent_model_force` | 2.1.257 | 未検証 |
| Claude Code | `skill_model_interactive` | 2.1.259 | 未検証 |
| Claude Code | `fable_5_1_effort_cache_keep` | 2.1.260 | 未検証 |
| Claude Code | `effort_frontmatter` | docs: 版条件なし / CHANGELOG: 2.1.78（見解が割れている） | 未検証。確認手順: 実機 2.1.201 で reviewer を起動し、SubagentStop の hooks 入力 effort.level が frontmatter の値になるかで確認する |
| Copilot CLI | `settings_json_subagents` | 1.0.70 | 未検証 |
| Copilot CLI | `usage_output_file` | 1.0.81 | 未検証 |
| Copilot CLI | `agent_model_array` | 1.0.83 | 未検証 |

### 保護・配布

- `.github/harness/model-policy.yml` と `.github/copilot/**` は guard sh/ps1・deny・CROSS_ITEMS・CODEOWNERS・SYNC_GLOBS の保護対象。`.github/harness/model-policy.yml` は sync の REVIEW_FILES（プロジェクト固有の `plan_fallbacks` / `session.copilot_cli_pin` を守る）。
- `.claude/settings.json` は REVIEW_FILES のため deny・フック配線の差分は配布先で衝突として提示される（`/91` で手動マージ。harness-sync スキル参照）。
- 生成は通常モードで人間が実行する。保守モード（`.claude/settings.json.locked` が存在）中は `--apply-deny` を拒否する（`harness-maintenance --off` の復元で生成結果が消えるため）。

### 単価表（USD / 1M tokens。取得 2026-09-10。`python tools/model_policy.py --print-prices` が JSON 双子）

| キー | prefix（最長一致） | input | output | cache read | cache write 5m | cache write 1h | 備考 |
|---|---|---|---|---|---|---|---|
| `fable-5.1` | `claude-fable-5-1` | 10 | 50 | 0.25 | 12.5 | 20 | Claude Code では fable alias の既定(2.1.257+)。2.1.201 では API 400 で拒否される(実プローブ) |
| `haiku-4.5` | `claude-haiku-4-5` | 1 | 5 | 0.1 | 1.25 | 2 | 候補外(effort 非対応・200K・tool search 400・advisor 不可・retirement)。enforcement で deny 廃止 2026-10-15 |
| `sonnet-4`（legacy） | `claude-sonnet-4` | 3 | 15 | 0.3 | 3.75 | 6 | Sonnet 4.x($3/$15)。Sonnet 5 は $2/$10 で別行(旧 effort-report 表はこれを 5 に誤適用していた=TM-1) |
| `sonnet-5` | `claude-sonnet-5` | 2 | 10 | 0.2 | 2.5 | 4 | task-worker の軽量候補(B1)は Sonnet 5 一択($2/$10 恒久)。公式 corpus 実測は精度 -10〜12pt |
| `fable-5`（legacy） | `claude-fable-5` | 10 | 50 | 1.0 | 12.5 | 20 | Fable 5(5.1 の前世代)。cache_read は 0.1x |
| `mythos`（legacy） | `claude-mythos` | 10 | 50 | 1.0 | 12.5 | 20 | effort-report.py 旧表の引き継ぎ |
| `opus-4`（legacy） | `claude-opus-4` | 5 | 25 | 0.5 | 6.25 | 10 | 廃止 2026-10-02 |
| `opus-5` | `claude-opus-5` | 5 | 25 | 0.5 | 6.25 | 10 | 公式既定の出発点。Fable 5.1 は Opus 5 の xhigh/max で evals が届かない時の昇格先 |
<!-- END GENERATED: model-policy-platform -->

## Claude Code 固有の運用

- **`/import` は使わない**: 共通指示の正は `CLAUDE.md → @AGENTS.md` の参照方式。
  `/import`（2.1.213〜）は AGENTS.md の一回限りのコピーで、正が二重になる
  （N面鏡の再生産。再監査 2026-09-09 CC-13）。
- **CLAUDE.md は対応表だけを持つ**（再監査 2026-09-09 PF-12。仕組みの説明は本節が正）。
  スラッシュコマンド（`.claude/commands/`）は `.agent.md` の役割定義と `.prompt.md` の本文を
  読む薄いアダプタ（生成物。`tools/generate-adapters.py`）。`.github/prompts/` は VS Code の
  Local ハーネス限定で、既定の Agent Host では読み込まれない（CP-2。Agent Host の入口は prompt files
  から生成した入口スキル `.github/skills/<nn>-<name>/`＝上記「Copilot Agent Host の入口」節。A6-17 / D081）。
  フックは `.claude/settings.json` に Copilot と同じスクリプトを配線し
  （Windows では Git Bash が必要）、`permissions.deny` がハーネス設定ファイル・テンプレート・
  `Agent(model:…)` を、`permissions.ask` が Bash / PowerShell 両ツールの push / tag / reset を
  二重化する（D046。強度差は「ガードレールの強度差」節。NotebookEdit は `.ipynb` 専用で
  deny 不要）。
- **ハーネス本体の保守モード**: このテンプレート自体を改修するときは、人間が自分の
  ターミナルで `python tools/harness-maintenance.py --on --apply` を実行して deny とフックの
  両層をまとめて一時解除し、作業後に `--off --apply` で必ず戻す（deny 行を手で外すだけでは
  フック層が残り編集できない。D037 以降）。設定変更後の続行は常に新しいチャット（D047）。
- **Stop フックの限界（CC-14）**: Stop の `decision: block` は Claude Code が **8 回連続で上書きして
  ターンを終了する**（`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` で変更可。公式 hooks リファレンス）。
  remind-record の「1 回だけブロック」・watchdog-continue の上限 3 回はいずれもこの上限未満で
  設計しており、Stop フックは気づきの補助であって最終ゲートではない（最終ゲートは CI の
  validate / selftest）。また transcript は非同期書込で Stop 時点の最終メッセージを含まないことが
  あるため、remind-record / draft-learnings は Stop 入力の `last_assistant_message` を一次入力にし、
  トランスクリプト末尾と一致しないターンは判定を保留する（誤ブロックより素通しに倒す
  fail-open。D072）。セッション境界の様式（D047）は `remind-session-boundary.py` が同じ
  `last_assistant_message` を読んで **warn だけ**出す（block しない。A6-19b(1)）。
- **リリースのタグ付け**: Claude Code（autoモード）では `git tag` のローカル作成が
  権限ガード（classifier）に拒否される一方、`git push origin main` は許可される
  非対称な挙動がある。エージェントはタグ付けの実行を試みず、annotated タグの
  コマンド（`git tag -a vX.Y.Z -m "..."` と `git push origin vX.Y.Z`）を提示して
  ユーザーに実行してもらう（この運用で複数リリースの安定実績あり）。
- **dynamic workflows**: 大規模作業（タスク数十超の実装、コードベース全体の監査・移行）
  では、Max/Teamプラン環境なら dynamic workflows（多数サブエージェントの並列実行）の
  利用を、速度向上の理由と**トークンコスト増**を添えてユーザーに提案してよい
  （承認なしに使わない）。結果の記録先（tasks.md・docs/へのレポート保存）は
  通常フローと同じにし、「docs/が正」の原則を崩さない。詳細は
  `large-scale-development` スキル参照。

## E2E・自律実行の予算規律

ヘッドレス実行（`claude -p` 等での E2E 通し実行・自律実行）は、暴走時の損害を
次の3層で有界にする。

1. `--max-turns` を必ず指定する（ターン浪費型の暴走を止める）。
2. `--max-budget-usd`（>= 2.1.217）を必ず併用する（コスト浪費型の暴走を止める。
   どちらか一方だけでは片方の型しか止まらない）。
3. 呼び出し側スクリプトが各セッションの `total_cost_usd` を集計し、
   **想定コストの2〜5倍**をセッション上限として、達した時点で以後の起動を止める。

## コスト計測の詳細（実測の還流）

- GitHub Copilot は 2026-06 に premium request 制から **AI Credits（トークン従量課金）**
  へ移行し、セッション別のトークン/コスト可視化も提供される（モデル選択と
  コンテキスト量がコストに直結する点は Claude Code と同様）。
- **一次データは `.github/hooks/logs/usage/`（ローカル・gitignore 済・90 日ローテーション）の
  受領書だけ**（2026-09-09 再監査 §5、A6-7/A6-12）。D040 の `docs/00-overview/effort-log.csv` は
  廃止（3 実プロジェクトで git 未追跡・未 ignore の個人コストが混入する中間状態を作っていた=RC-4）。
  既存分は `python tools/effort-report.py --migrate <csv>` で取り込む。committed 側は
  `docs/06-retrospective/effort-report.md` と `baselines.json`（集計のみ・匿名化。`effort-report.py`
  が再生成）。振り返りの「トークン効率」検証（モデル×effort の適否・無駄の所在・独立レビューの
  費用対効果・逸脱セッション）に使う。
- **取得経路（Claude Code）**: 対話モードで費用・文脈使用率・行数を機械採取できる公式経路は
  statusline の stdin JSON だけ（hooks 入力にコストは来ない=公式 not planned）。
  `statusline.py`（`statusLine.command`。`.claude/settings.json` に既定配線＝プロジェクトを開くと
  利用者自身の `~/.claude` の statusline より優先される。無効化・差し替えは `.claude/settings.local.json`
  の `statusLine` で上書きし、既存の表示を残すなら `--chain "<既存コマンド>"` で連結）が
  `<sid>.status.json` に永続化し、
  `session-baseline.py`（SessionStart: `startup/clear/resume/fork` のみ。compact では書かない・
  存在すれば上書きしない）が GATE_STATUS・HEAD・tasks [x]・numstat を、
  `log-subagent.py`（SubagentStop / PostToolUse(matcher `Agent|Task`)。tool_name 自己フィルタ）が
  種別と要求/実行モデルを、`log-effort.py`（Stop=draft / SessionEnd=確定 rename と 1 行追記）が
  受領書を書く。集計・表示の正は `tools/session-receipt.py`（フック・`/harness-stats`・
  `/99-status` の費用欄が同じ関数を使う）。トランスクリプトの保持期間は既定 30 日のため都度記録する。
- **出所タグ（モデルの自己申告は使わない）**: `[公式]` = statusline（`cost.total_cost_usd`・
  `context_window.used_percentage`・`cost.total_lines_*`。ホスト版の単価表による list 価格の推定で
  **請求額ではない**。/clear で 0、resume 後は再開以降のみ。`total_*`/`current_usage` は累計ではない
  ので使わない）／`[transcript]` = 累計トークン（main/サブエージェント別・解析率 parsed/total 付き。
  非公式形式。`input_tokens` はキャッシュ境界後の残余で正しい値=RC-1 反証）／`[推定]` =
  `tools/prices.json`（as_of 付き。fable-5-1 の cache read 0.025x 等）／`[git]` = コミット数。
  host と推定の乖離が `tools/usage-config.json` の閾値を超えると `price_table_stale`。
  tests の pass を主張できるのは golden-eval / check.py の決定論検証だけ（PostToolUse(Bash) 抽出は
  main と subagents の両方を走査しても ran/unknown 止まり）。`review_less_done` は同一セッションでは
  なく同フェーズ累計で判定する（ライトパス注記があれば spec-critic 省略は正常扱い）。
- **フック判定の記録（A6-20。第6波）**: 全フックの判定は `.github/hooks/logs/hook-decisions.jsonl`（1 行 JSONL。
  `session_id` 付き。`target` は `.github/harness/privacy-patterns.json` で秘密値を伏せてから先頭 120 字）に
  一本化し、受領書の hooks 欄（deny / ask / warn / block）は session_id で紐付ける（旧 TSV `hook-decisions.log` は
  セッション開始以降の時間窓で数える後方互換の近似。読み手は `_log.py`、集計は `tools/session-receipt.py`）。
  独立レビューの工程累計は spawn 記録と `docs/04-test/review-log.md` の日時付きエントリ数の大きい方（D073 open）。
- **フック計測（R-09 / A2-7。第7波）**: 判定ログに `duration_ms`（スクリプト内の経過。プロセス起動分は含まない）と
  `host`（claude-code / copilot / unknown の近似）を末尾 2 欄で足し、`python tools/hook-metrics.py` が script 別 P50 / P95・
  decision 分布・host 別発火数・false ask 近似を集計して `tools/usage-config.json` の `hook_slo` 超過を WARN する。受領書の
  hooks 欄と `effort-report --kpi` に同じ 1 行要約。実機分布は未計測（2026-09-17。selftest の値だけ）。
- **ホスト別可用性**（監査 §5.1。○ 取得可 / △ 劣化 / × 不可）:

| 欄 | Claude Code CLI / VS Code 拡張（2.1.201 実機） | 同 ≥2.1.251 | `claude -p` | Copilot CLI | Copilot VS Code | Copilot cloud |
|---|---|---|---|---|---|---|
| host 推定 $・文脈%・行数 | ○ statusline（要配線・ワークスペース信頼。実機発火は未実測） | ○ +prompt_cache | ○ result.total_cost_usd / costBasis | △ events.jsonl の session.shutdown（非公式・/new で欠落） | △ UI ホバー転記（人間向け） | × Billing API のみ（2 日遅延） |
| 累計トークン（モデル/エージェント別） | △ transcript（非公式・/usage との校正は未実施） | △ 同左 | ○ result.modelUsage | △ --usage-output-file（1.0.81+・未検証） | △ OTel file exporter（要 opt-in・未検証） | × |
| 実行モデル | ○ toolUseResult / PostToolUse(Agent).resolvedModel（2.1.174+） | ○ +modelsUsed（2.1.212+） | ○ modelUsage | △ | △ | × |
| GATE/tasks 差分・tests・reviewer 起動 | ○ | ○ | ○ | △ sessionStart/agentStop（配線未検証） | △ Stop 配線後（未配線） | × フックの書込は破棄 |
| フック判定（deny/ask/warn/block） | ○ `hook-decisions.jsonl`（session_id 付き。第6波） | ○ | △ JSONL には残るが `--from-result` の受領書には載らない | △ JSONL には残るが `session_id` は null（camelCase 差）。Copilot 受領書は未実装（A6-22） | △ 同左 | × |
| 提示面 | Stop `systemMessage`（3 行）/ statusline / `/99-status` / `/harness-stats` | 同左 | 結果 JSON | CLI の終了サマリ | `/99-status` | PR 本文のみ |

- **劣化モード**: statusline 未配線 → `host n/a`（文脈%・行数も n/a。トークンと成果層だけ）。
  `tools/session-receipt.py` が無い配布先 → `log-effort` は無出力。Copilot はフックが発火しても
  camelCase ペイロード（`sessionId`）を無視するため記録されない（第 3 段: Copilot CLI の
  `sessionEnd` 後追い取込・VS Code は成果層のみ、は COPILOT-E2E で実機確認してから配線）。
  Antigravity はフック不可（D057 で凍結）。
- **提示**: Stop の `systemMessage`（3 行以内・絵文字なし・「採取: Stop(暫定)」を明記。SessionEnd で
  確定）は区切り（GATE 変化 / フェーズコマンド起動 / Δ費用・Δトークン ≥ 閾値 / N 発話ごと /
  文脈 ≥ 閾値）のときだけ。`decision: block` は使わない。remind-record が block したターンは
  次の Stop に持ち越す（マーカーファイルの存在確認だけで協調し、判定ロジックは複製しない）。
  閾値は `tools/usage-config.json` の 1 か所（USAGE.md セッション分割表の文言も生成）。
  基準線（`docs/06-retrospective/baselines.json`、無ければ監査 RD-5 の暫定値）は n<5 を「暫定」と
  表示し、$ の絶対値では警告しない。
- **プライバシー境界（RC-4）**: committed = 工程×役割×モデルの合計・推定 $（basis・単価表日付）・
  行数・tests・GATE 遷移数・週単位日付。local = session_id・transcript_path・ホスト版・受領書・
  statusline JSON・サブエージェントログ・教訓候補の下書き。never = プロンプト本文・tool 出力本文・ファイル内容・
  commit 本文・秘密値・開発者識別子。
- **教訓候補の下書き（IA-20260831-03 / codex H-01）**: `draft-learnings.py`（Stop）は候補を `docs/` に書かず
  `.github/hooks/logs/learnings-draft.local.md`（local 欄・gitignore 済み・`tools/usage-config.json` の
  `learnings_draft_rotate_days`＝30 日で回転）に、`privacy-patterns.json` の redaction を通した先頭 60 字
  （`learnings_draft_excerpt_chars`。0 で抜粋なし）だけを残す。`learnings.md` への転記は人か `/10-retrospective`
  の明示操作だけ（自動昇格しない）。受領書と同じ境界＝プロンプト本文・tool 出力本文はコミット対象に残さない。
- **配布**: `.claude/settings.json` と `.gitignore` は `sync-harness.py` の REVIEW_FILES（無言上書き
  しない）のため、既存プロジェクトでは statusLine / SessionStart / SubagentStop / PostToolUse(Agent)
  の配線と `effort-log.csv` の ignore 行を人手マージし、`--migrate` で旧 CSV を取り込む
  （`/91-sync-from-harness` の手順）。新規プロジェクトは archive/intake 経路で自動配布される。
- Agent Skills は段階的開示（discovery 時は `name`/`description` のみ、関連時だけ本文）で
  コンテキスト消費を抑える。`description` は具体的でキーワードが立つように書く
  （曖昧だと無関係な場面で読み込まれて浪費するか、必要な場面で読み込まれない）。

## 独立レビューのチェックポイント（A6-14 / RD-2。既定経路 `reviewer` の起動点）

`reviewer` サブエージェントの起動点は次の3つ。いずれも結果を呼び出し元が
`docs/04-test/review-log.md`（`review_log_template.md` の形式）に追記し、その日時付き
エントリ（または `security-review-report.md`）が implementation / test の done 条件になる
（gate-check スキル。`warn-gate-tamper` フックと `golden-eval` が機械検査する）。

| 起動点 | 呼び出し元 | 回数 | BLOCKER 時 |
|---|---|---|---|
| 実装フェーズのチェックポイント | `implement`（implement.agent.md 手順5） | 完了タスク10個ごとに1回 | 該当タスクを `[ ]` に戻し task-worker で修正→再レビュー |
| 全タスク完了時 | `implement`（同 手順6） | 1回 | 同上（解消してからテストへ引き継ぐ） |
| テスト完了後・リリース前 | `test`（test.agent.md 手順5）/ `change`（CR 完了前） | 1回 | 実装へ差し戻し |

既定経路の種類は従来どおり5つ（spec-critic×2・task-worker・reviewer・/12）で、
`reviewer` 経路の起動点が増えただけ。環境差: Claude Code は `.claude/agents/reviewer.md`
（Agent ツール（旧称 Task））、Copilot は `runSubagent`、Antigravity は別のエージェント会話で実行する
（読み替えは各アダプタの記述どおり）。ライトパス（fast-track）はタスク10個超で昇格するため
チェックポイントは発生せず、承認②前の1回が両フェーズの done 条件を満たす。

## 配布経路と plugin マニフェスト（A6-23 / CP-7 / RG-17 / IA-20260831-10）

配布経路は 3 つ。主経路はファイルのコピー（1・2）で、plugin（3）は補助（ビルトイン委譲表の
「配布 sync vs Agent Plugins 1.0 = 併用」の内訳）。

| 経路 | 仕組み | 状態 |
|---|---|---|
| 1. archive / GitHub テンプレート | `git archive`（`.gitattributes` の export-ignore で本体専用物を除外）→ `/00-start-project` | 実機（D049） |
| 2. intake / 逆同期 | `tools/intake-app.py`（brownfield 注入）・`tools/sync-harness.py`（`SYNC_GLOBS` のマニフェスト方式。`.claude-plugin/` を含む） | 実機（D049 / D065） |
| 3. plugin | `plugin.json`（Agent Plugins 1.0）と `.claude-plugin/plugin.json`（Claude Code。生成物） | **マニフェストの機械検査まで。実インストール未検証**（COPILOT-E2E §7） |

plugin マニフェストの規則（正は 1 か所・鏡は生成物）:

- **正は `plugin.json`**（Agent Plugins 1.0 スキーマ。`$schema`・`name` 必須、トップレベルは
  version / description / author / homepage / repository / license / keywords / extensions のみ、
  `additionalProperties: false`）。版数の正でもある（CHANGELOG 規約）。ハーネス固有の情報は
  `extensions` の逆ドメイン名前空間にだけ置く（`com.github.copilot`: agents / hooks / commands の
  位置。`io.github.n-ima.copilot-sdlc-harness`: skills の位置と生成物の一覧）。
- **生成物**は `.claude-plugin/plugin.json`（`skills: ./.github/skills/`、`commands: ./.claude/commands/`、
  `agents: [./.claude/agents/*.md]`、`hooks: ./.claude-plugin/hooks.json`）と
  `.claude-plugin/hooks.json`（`.claude/settings.json` の hooks を `${CLAUDE_PLUGIN_ROOT}` 相対に
  写したもの）。`python tools/generate-adapters.py` 第6節（`tools/plugin_manifests.py`）が書き、
  `--check` と `validate-harness.py` (m) が鏡割れ・スキーマ・パス実在を検査する。
- **hooks の二重化を避ける**: plugin の hooks は plugin 経由の配布時だけの配線。本リポジトリや
  テンプレコピーを直接開く経路（1・2）では `.claude/settings.json` が正で、`--plugin-dir` を併用しない。
- **レイアウトの限界**（open）: Agent Plugins 1.0 の portable 部品は plugin ルート直下の固定位置
  （`skills/`・`mcp.json`）、Copilot 固有部品は `com.github.copilot/` ディレクトリが規約。本ハーネスは
  `.github/` 直下が正のため固定位置に部品を置いておらず、Copilot 側からは「マニフェストだけの
  plugin」に見える可能性がある。`.claude/commands|agents` のポインタは cwd にハーネスのコピーが
  ある前提で書かれており、別リポジトリへの plugin 導入では参照先が無い。
- **CI**（RG-17。版固定）: `npm i -g @anthropic-ai/claude-code@<版>` → `claude plugin validate .`
  （2.1.201 実測: exit 0・警告 1 = plugin ルートの CLAUDE.md。`--strict` はこの警告で赤になるため使わない。
  `--json` は 2.1.201 に無い） → `claude --init-only --settings .claude/settings.json`（2.1.259 以降。
  SessionStart フックを実走して終了。API 鍵不要・費用ゼロかは公式に明記が無く未確認のため
  `continue-on-error`）。
- 保護面: `plugin.json` は従来どおり deny / guard sh・ps1 / CODEOWNERS の対象。`.claude-plugin/plugin.json`
  は guard sh・ps1 の `plugin\.json$` に一致するため deny され、CODEOWNERS と `SYNC_GLOBS` に
  `.claude-plugin/` を追加。`.claude/settings.json` の `permissions.deny` への `.claude-plugin/**` 追加は
  統合時に適用する（validate が WARN で促す）。

## 鏡の生成物（説明文書の生成ブロック。正は一次データ、文書は `tools/gen-docs.py` の出力）

「同じ事実を書いた複数の面」の手編集ドリフト（再監査 2026-08-31 §1 A1-1〜4・2026-09-09 RG-12/RG-13）は、
検査ではなく生成で塞ぐ。`<!-- BEGIN GENERATED: <name> -->` 〜 `<!-- END GENERATED: <name> -->` の間は
手で書かず、`python tools/gen-docs.py --apply` で埋める（`--check` が CI で差分ゼロを要求し、
`tools/validate-harness.py` (g) が同じ検査を呼ぶ）。件数（スキル数・コマンド数・エージェント数・決定数・
フック数・selftest の静的ケース数）は生成ブロックの外に手で書かない（validate (c)/(c-2) がハードコードを検出する）。

| 面 | ブロック名 | 一次データ |
|---|---|---|
| `README.md` | `phase-command-table` / `selftest-counts` | prompts の frontmatter（`agent:`）・selftest.sh/.ps1 の check 行・フック配線 3 ファイル |
| `.github/harness/README.md` | `harness-doc-counts` | agents / skills / prompts のファイル数・フック配線 |
| `overview.html` | `overview-stats` / `selftest-counts` | 同上 |
| `guardrails.html` | `selftest-counts` / `selftest-counts-table` / `hook-count` | selftest 静的計数・フック配線 |
| `agents.html` | `agents-hero-counts` / `agents-phase-table` / `agents-sub-table` | `.github/agents/*.agent.md` の frontmatter（description / tools / agents / handoffs / user-invocable）と prompts の `agent:` バインド |
| `skills.html` | `skills-hero-count` / `skills-sections` | `.github/skills/*/SKILL.md` の frontmatter（description / user-invocable）。`^NN-` の入口スキルは「ハーネス入口」カテゴリに自動分離 |
| `commands.html` | `commands-hero-count` / `commands-sections` | `.github/prompts/*.prompt.md` の frontmatter（description / agent） |
| `USAGE.md` | `usage-config-thresholds` / `usage-session-table` | `tools/usage-config.json`・prompts の集合（全コマンドがちょうど 1 行に現れることを検査） |
| `COMPARISON.md` | `selftest-counts` / `decision-count` / `comparison-measured` | selftest 静的計数・`DECISIONS.md` の `## D` 見出し・`evaluation/REPORT.md`（`tools/eval-report.py --report` の生成物） |
| `COMPARISON.html` | `selftest-counts` / `decision-count-stat` / `decision-count` / `comparison-measured` | 同上 |
| `PLATFORM.md` / `README.md` / `agents.html` | `model-policy-*` | `.github/harness/model-policy.yml`（生成器は `tools/generate-adapters.py`。gen-docs ではない） |
| `.claude/rules/<name>.md` | （ファイル全体が生成物） | `.github/instructions/<name>.instructions.md`（`applyTo` → `paths:`。生成器は `tools/generate-adapters.py` 第7節＝`tools/path_rules.py`。鮮度・孤児は validate (x)） |

一次データから生成できない列（フェーズ名・成果物・カテゴリ分け・セッション欄・サブエージェントの基本姿勢）は
`tools/gen-docs.py` の引き継ぎ辞書（`PHASE_TABLE_ROWS` / `SKILL_META` / `COMMAND_META` / `USAGE_SESSION_ROWS` 等）が正で、
プロンプト・スキル・エージェントの追加・削除・改名で辞書が食い違うと gen-docs が exit 2 で止まる（未登録・孤児の検出）。
生成物には件数だけを書き、「全PASS」「検証済み」等の品質主張は書かない（D079 決定2。pass/fail の正は各 selftest の実行結果）。
COMPARISON の実測表は `evaluation/REPORT.md` からのみ転記し、比較可能な走行（valid_for_comparison=true）が 0 の間は
優位・費用比の文言を生成しない。`DECISIONS.md` 先頭の索引（`decisions-index`。D 番号 / 短縮題名 / 日付）も生成ブロック
（マーカー未設置なら SKIP する任意ブロック。分割はせず索引で該当 D だけを精読する。A7-M-1）。

## 状態機械（GATE_STATUS。正は `.github/harness/STATE-MACHINE.md`）

ゲート状態の正は `docs/00-overview/progress.md` の `GATE_STATUS`（5 キー固定・4 語彙。値は行の最初のトークン）で、
語彙・許される遷移（`in_progress` は入口の最初のステップ、`pending_approval` はフェーズの最終ステップ、`done` は人の
承認後、巻き戻しは `/12` と `/13` 経由だけ）・implement↔test の往復上限（`GATE_COUNTERS` の `implement_test_loops`。
上限は `tools/usage-config.json` の `implement_test_loop_max`）・遷移ログ（`logs/gate-transitions.jsonl`。通番 `rev`）・
並行更新（`logs/session.lock`。block しない）・復旧手順（`python tools/gate_status.py recover`）は STATE-MACHINE.md が
正（再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15 / A7-H-5）。機械検査は `tools/gate_status.py check`、
`tools/validate-harness.py` (r)（本体のテンプレと配布先の実物）、フック `warn-stale-gate`（完全性・順序矛盾の警告）/
`warn-gate-tamper`（遷移ログ・lock・往復上限）/ `inject-progress`（lock の取得・遷移ログの突合）。**原子的な書換は推奨
手順**（一時ファイル→rename。`gate_status.py set` が実装）で、フックでは強制しない（ホストの Edit / Write は in-place
書込で、PreToolUse で progress.md の直接編集を deny すると正規の入口と人の緊急編集まで止まるため）。

## 衛生・鮮度の検査（説明文書の鮮度語・リリース tag・配布物。A7-M-1 / R-06）

説明文書の「最終更新: YYYY-MM-DD（D0NN）」「YYYY-MM-DD 時点」「version x.y.z」「D0NN まで」は人が「その時点で確かめた」と
主張する鮮度語で、機械では書き換えない（`--fix` は意図して無い＝D079 と同じ理由）。乖離は `python tools/freshness-lint.py` が
一次データ（git のファイル最終変更日・`plugin.json` の version・`DECISIONS.md` の最大 D 番号・基準日）と比べて WARN で列挙し、
人が直す（CI は表示のみ。actions/checkout は depth 1 のため git 日付との比較は INFO に落ちる）。履歴文書 `DECISIONS.md` は
ヘッダの「最終更新」だけを見る。定期の doc-gardening は harness-retrospective スキルの「定期棚卸し」に組み込む。
リリース tag は `python tools/release-tag.py` が `CHANGELOG.md` の版・日付と `plugin.json` の version 変更履歴から
対象コミットを決めて `git tag -a` のコマンド列を出す（実行は人＝push を伴う外部反映。tag 無しの版は
`validate-harness.py` (p)（リリースの不変性。D087）が WARN、(u) が解決先コミットつきの INFO を出す。CI は
`fetch-tags: true` でも depth 1 のため版の導入コミットは unresolved になる＝完全 clone で実行する）。配布物は
`tools/sync-harness.py` が本体の `git ls-files` に無い未追跡ファイルを配布せずレポートに列挙し（D087）、
`tools/intake-app.py` は未追跡の件数を警告する（git archive HEAD には構造的に含まれない）。

## 新モデル / 新ホスト版の Day-0 再ベンチ儀式（A3-6。世代交代をリスクから資産に反転する）

新しいメインモデルや新しい Claude Code / Copilot の版が出たとき、「動くか」を祈らず次の順で吸収する。
CI の「ホスト機能吸収カナリア」（`python tools/host-canary.py`。claude 導入後ステップ）が実機版と
`platform-requirements.json` の `verified_on_ci` の差を INFO/WARN で出したら、この節を起動する合図。

1. **一次情報の取得**: 公式 CHANGELOG / hooks / sub-agents / permissions のリファレンスを当たり、ハーネスの前提に
   関わる新機能・挙動変更を拾う（`audits/` の再監査 §1.1 と同じ粒度）。
2. **`platform-requirements.json` の更新**: 新しいホスト機能は `features` に `version` と `reason` を足す（導入版が
   公式に無ければ `null` のまま実測で確定）。`min` / `required` / `e2e.min_cli` を上げるなら名指しする feature と
   版を一致させる（自己整合は `platform_requirements.validate_structure` が検査）。モデル関連（解決順・effort・FORCE）は
   `model-policy.yml` の `min_version` 側。`as_of` を更新する。
3. **`python tools/gen-docs.py --apply`** で PLATFORM.md「最低安全バージョン表」を再生成し、`python tools/doctor.py` で
   開発機の版判定（FAIL / WARN の境界）を確認する。開発機を更新したら `verified_on_dev` と model-policy の `verified` を上げる。
4. **評価装置の固定条件の更新**（D078 固定条件 C）: `e2e.min_cli` / `required` を上げると `condition_hash` が変わるため、
   旧系列と同じ表には載らない。新系列 ID（`evaluation/experiments/`）を事前登録し、B_cap は再度パイロット 1+1 で確定する。
5. **A/B 系列の再走**: S1（能力）→ S2（効率）→ S3/S4 → S5 の順で n=5 / ABAB（`evaluation/README.md`）。結果は
   `python tools/eval-report.py --report` で `REPORT.md` へ、COMPARISON の実測表は生成物からのみ転記する。
6. **CI の版固定を上げる**: harness-ci.yml の `@anthropic-ai/claude-code@x.y.z` を上げ、全ジョブ緑の run URL を確認してから
   `verified_on_ci` を同じ値にする（順序が逆だと validate (v) が WARN）。`python tools/host-canary.py` でビルトイン依存
   （D070 の委譲先）が新版でも見つかることを確認し、消えていれば台帳 `builtin-dependencies.json` の `on_missing` に従う。
7. **記録と引き算**: DECISIONS.md に新 D 番号で「吸収した機能・上げた版・A/B の結果・撤去できた足場」を記録し、
   harness-retrospective スキルの「定期棚卸し（削除 → 失敗したものだけ復帰）」を同じサイクルで行う（新モデルで不要に
   なった指示層の足場を外す＝ de-scaffolding）。

未検証: この儀式は 2026-09-17 時点で手順として置いただけで、実際の新版（2.1.267 系）での通し実施は
開発機の更新（A6-8）後になる。
