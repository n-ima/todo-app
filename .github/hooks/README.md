# .github/hooks/ について

VS Code の Agent Hooks（Preview機能）を使って、フェーズゲート運用を
「LLMの善意」ではなく機械的に補強するためのフック定義です。

## 含まれるフック

| ファイル | イベント | スクリプト | 動作 |
|---|---|---|---|
| `gate-hooks.json` | PreToolUse | `guard-template-edit.*` | `*_template.md` への直接編集を **deny**（コピーして実体ファイルを作るよう強制）。対象パスは `file_path`/`filePath`/`path`/`uri`/`notebook_path`/apply_patch 本文から網羅収集し（`_paths.*`）、読み取り系ツール（`readFile` 等）は対象外（2026-09-09 再監査 RG-4/CP-1） |
| `gate-hooks.json` | PreToolUse | `guard-dangerous-git.*` | `git push` / `git tag` / `--force` / `reset --hard` / `rm -rf` を **ask**（毎回ユーザー確認）。`git -C <path> push` 等のグローバルオプション経由や `-fr`/`-r -f` のフラグ順不同も検知（D046）。行継続（バックスラッシュ / バッククォート / キャレット＋改行）で分割されたコマンドは照合前に畳む（.sh/.ps1 とも3種。H-1/RG-3）。理由文に「何を: <コマンド先頭 160 字>」を含めて exact-action の承認単位（この操作・この対象・この引数への一回限り）を示し、承認バイパス下（payload の `permission_mode` が bypassPermissions / dontAsk / autopilot 等、または環境変数 `HARNESS_EXTERNAL_EFFECT_MODE=deny`）では ask が消音されるため **deny**（codex 監査 2026-08-31 C-01 / IA-20260831-01。下記「外部反映の承認単位」）。作業ツリー全体を HEAD で書き戻す discard-all 形（`git checkout -- .` / `git checkout .` / `git restore .` / `git restore :/` / `git restore --worktree .`）も **ask**（保護対象を名指ししない git plumbing 経由の保護域書換。R-01。単一パスの restore は対象外＝保護パスなら guard-harness-config-edit が ask。第7波 2026-09-17） |
| `gate-hooks.json` | PreToolUse | `guard-external-effect.*` | git 以外の**外部反映**を **ask**: IaC の apply / destroy（terraform・pulumi・cdk・sam）、kubectl / helm / argocd / flux、aws / gcloud / az / gsutil / rclone の状態変更、PaaS CLI（vercel・netlify・firebase・fly・heroku・railway・wrangler・eb・kamal・amplify・supabase 等）、コンテナレジストリへの push（docker・crane・skopeo・oras）、パッケージ公開（npm・pnpm・yarn・cargo・twine・gem・mvn・gradle・dotnet nuget・goreleaser 等）、gh / glab の書込系（release create・pr merge・secret set・api -X POST 等）、curl / wget / httpie / Invoke-RestMethod / WebClient の書込メソッド、ssh / scp / sftp / rsync、メール送信、本番マーカー（`RAILS_ENV=production`・`--env prod` 等）付きの migrate / deploy / seed、MCP の書込系ツール名（`mcp__<server>__create_*` 等）。理由文に「何を・どこへ（URL ホスト / user@host / 対象フラグ / 引数）・コマンド」を含め、承認は『この操作・この対象・この引数』への一回限り（exact-action。呼出ごとに再評価）。読み取り・`plan`・`--dry-run`・ループバック宛の HTTP・`ssh -T git@…`・`docker build`・`npm publish --dry-run` は allow。承認バイパス下は **deny**。git の push / tag / force は guard-dangerous-git が担当（二重 ask なし）。JSON が読めないときは grep フォールバックでコマンドを拾う（保護側）。49 セグメント以上は評価前に ask（fail-closed）。codex 監査 2026-08-31 C-01 / IA-20260831-01 |
| `gate-hooks.json` | PreToolUse | `guard-done-evidence.*` | `docs/00-overview/progress.md` の `done` 遷移、または `docs/03-implementation/tasks.md` への完了マーク `[x]` の追加を含む書込で、**その書込の新内容**（Edit / MultiEdit: new_string − old_string の行、Write: content − ディスクの現ファイルの行）に証拠 3 点セット（再実行可能なコマンド = バッククォート囲みか「コマンド:」ラベル / 出力の要約 = →・結果:・passed・OK 等 / 実行日時 YYYY-MM-DD HH:MM）が揃っていなければ **deny**（理由文に不足項目と書式）。done / `[x]` の「追加」は新旧の件数比較で判定する（注記の追記や既存 `[x]` の維持では発火しない）。人間の直接編集は対象外（緊急経路）・JSON が読めない・新内容が取れない・対象パスでない場合は allow（fail-open）。役割分担は下記「証拠ゲートの役割分担」（A2-4b / 再監査 2026-09-09 RG-13） |
| `gate-hooks.json` | SessionStart | `inject-progress.*` | セッション開始時に `docs/00-overview/progress.md` のGATE_STATUSを自動でコンテキスト注入。配布鮮度（再監査 2026-09-09 RD-4 / A6-15）: `docs/00-overview/harness-origin.md` の `latest_decision` が本体（`path:` の DECISIONS.md、無ければ CHANGELOG.md）の最新 D 番号より `HARNESS_STALE_GENERATIONS`（既定 1）世代以上古ければ「/91-sync-from-harness を先に実行」を 1 行注入する（SessionStart のみ。本体自身・本体に到達できない・D 番号が読めないときは注入しない＝fail-open。同じ判定は `python tools/doctor.py` の distribution 行）。状態機械の堅牢化（第3波。A7-M-3 / R-04 / IA-20260831-15。正は `.github/harness/STATE-MACHINE.md`）: SessionStart で `logs/session.lock`（session_id・pid・ts・epoch）を置き、別セッションの stale でない lock（`tools/usage-config.json` の `session_lock_stale_minutes`、既定 120 分）があれば上書きせず「GATE_STATUS の書換は片方のセッションだけで」を注入し、stale なら取得する。あわせて progress.md の GATE_STATUS が遷移ログ `logs/gate-transitions.jsonl` の最終記録と違えば source=session-start で 1 行追記する（フック外の書換の突合）。session_id が取れないホストでは lock を扱わない（VS Code の SessionStart ペイロードに session_id が載るかは未検証）。PreCompact では何もしない。fail-open。注入量の上限（第3波 A7-M-8）: 注入文全体を `tools/usage-config.json` の `inject_progress_max_chars`（既定 8700 文字。環境変数 `HARNESS_INJECT_MAX_CHARS` が優先、0 で無効）で打ち切り、末尾 1 行で打ち切りと全文の場所を明示する（GATE_STATUS の閉じタグ欠落で progress.md 全文が注入される事故も有界になる。sh は C.UTF-8 ロケールの文字数、ps1 は .Length＝BMP 文字で同数。selftest 両系で同じ 3 ケース） |
| `gate-hooks.json` | PostToolUse | `warn-stale-gate.*` | 承認済み(done)のフェーズ配下の実体文書（requirements.md・nfr.md・detailed-design/・ADR等すべて。テンプレート除く）が編集されたら、後続フェーズとの整合確認を促す非ブロッキングの警告を出す（D046で代表5ファイル→フェーズ配下全体に拡大）。値は行の最初のトークンで判定する（注記つき `done 2026-08-01` でも警告。再監査 2026-08-31 の sh/ps1 非対称の修正）。`progress.md` 自身が書かれた直後は GATE_STATUS の**完全性**（5 キーの欠落・重複・未知のキー・読めない行・語彙外の値・`GATE_COUNTERS` の不正）と**順序矛盾**（後続が着手済みなのに先行が not_started＝規則 A / 後続が done なのに先行が done でない＝規則 B。「状態: 運用中」の注記があれば規則 B は免除）を 1 行で警告する（規則の正は `tools/gate_status.py`・`.github/harness/STATE-MACHINE.md`。sh/ps1 は同じ規則の鏡で selftest 両系が固定。第3波） |
| `gate-hooks.json` | PostToolUse | `check-doc-chars.*` | `docs/**.md` への書き込み後、不可視文字・文字化け（NUL / 本文中BOM / ハングル / 置換文字 / 生タブ / 全角空白 / BMP外漢字、第3波で ZWSP U+200B / NBSP U+00A0 / 双方向制御文字 U+202A〜202E・U+2066〜2069 を追加＝A7-M-8。U+202E 等はインジェクション隠蔽に使われる）を数え、0件でなければ**警告**する（編集ツールの混入事故対策。docsはlint対象外のため機械検査はここだけ。SessionStartの教訓注入は**新しい50件**+打ち切り明示） |
| `gate-hooks.json` | PostToolUse | `warn-gate-tamper.*` | `docs/00-overview/progress.md` の新内容（new_string / content）に「: done」への変更、または `docs/03-implementation/tasks.md` の新内容に完了マーク `[x]` の追加を検知したら、ユーザーの明示承認・証拠の確認を促す非ブロッキングの**警告**を出す（ゲート状態の無断書き換え対策。P2-4）。implementation / test の done 遷移で独立レビューの記録（`docs/04-test/review-log.md` の日時付きエントリ、または `security-review-report.md`）が無い・最新の完了証拠より古いときは専用の警告「独立レビューの記録がありません」を出す（A6-14。判定規則は末尾「独立レビュー記録の検査」）。同じ done 遷移で、このセッションの `logs/usage/<session_id>.subagents.jsonl` にある直近の reviewer / spec-critic の実行モデル（`resolved_model`。null なら `requested_model`）が役割別モデル方針の allowed（`python tools/model_policy.py --print-role-table`。読めなければ `guard-subagent-model.*` に埋め込まれた同じ生成表）に無ければ専用警告 `model_mismatch` を出す（D077 三層強制の done 契約側。警告のみ。記録が無い＝Copilot 経路等では鳴らさない。第6波）。状態機械の堅牢化（第3波。正は `.github/harness/STATE-MACHINE.md`）: progress.md の書込後に実ファイルの GATE_STATUS を読み、遷移ログ `logs/gate-transitions.jsonl` の最終記録と違えば `_log.*` の `gate_log` で「前→後」の差分・通番 `rev`・session_id・ts・`loops` を 1 行追記する（Write / Edit / パッチ系のどれで書かれても同じ経路）。別セッションの stale でない `session.lock` があれば同時更新の警告、`GATE_COUNTERS` の `implement_test_loops` が上限（`tools/usage-config.json` の `implement_test_loop_max`、既定 3）超なら「/13-converge か人の判断へ」の警告を、他の警告文の末尾に添える（単独なら単独で出す。block しない）。in_progress と pending_approval の警告文は分け、前者だけに改竄の注意を添える（入口の最初のステップとしての in_progress は正規遷移＝D067。A7-H-5） |
| `security-hooks.json` | PreToolUse | `guard-harness-config-edit.*` | `.github/agents/`, `.github/hooks/`, `.github/workflows/`, `.github/prompts/`, `.github/instructions/`, `.github/harness/PLATFORM.md`, `.github/harness/model-policy.yml`, `.github/copilot/`, `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`, `GEMINI.md`, `plugin.json`, `.vscode/settings.json`, `.claude/settings(.local).json`, `.claude/rules/`, アダプタ層、および中核2スキル（request-routing / gate-check。`.github/` と `.claude/` の両方）への編集を **deny**（自己権限昇格・ガードレール解除の防止。他の `.github/skills/` は動的追加を許すため対象外。prompts は D046、settings.local.json と中核スキルは D048、`.claude/rules/`・copilot 設定・常駐指示・model-policy は 2026-09-09 再監査 SC-1/SC-6/MP-3 で追加）。skills 配下の `hooks/hooks.json` の書込は **ask**（SC-2）、権限系 frontmatter は**拡張だけ ask**（A7-G-3。2026-09-17。公式仕様から列挙したキー: 付与系 `allowed-tools` / `tools` / `permissions` / `permission-mode` / `hooks` / `context` / `agent` / `shell` / `mcp` / `mcp-servers` は値のトークン集合が増えたら拡張、真偽値は `disable-model-invocation` true→false と `user-invocable` false→true が拡張、制限系 `disallowed-tools` の削除・縮小が拡張。旧内容はディスク上の現ファイル、置換元は `old_string`（`_paths.*` の `collect_old_text` / `Get-OldText`）。縮小・同値の書き直し・既定値の明示は allow。同じ規則を ConfigChange の `guard-config-change.py` が HEAD 比較で block）。`.github/harness/external-lock.json`（外部 Skill / MCP のロック。IA-20260831-11）も保護対象。reparse point の作成（`ln -s` / `mklink` / `New-Item -ItemType SymbolicLink|Junction|HardLink` / `[IO.File]::CreateSymbolicLink` / `os.symlink` / `fs.symlink`）は保護パス不在でも **ask**（作成後は別名経由で保護域を書けるため。R-01）、git plumbing / worktree で保護パスを名指しする形（`git checkout -- <保護パス>` / `restore` / `switch` / `update-index` / `worktree add <保護域内>` / `hash-object`）は専用タグ `git-plumbing` で **ask**（従来の反転判定でも ask だったものを理由文ごと明示。R-01）。Bash/PowerShell のコマンド文字列は **allowlist 反転**で検査する: 保護パスを含むコマンドは全セグメントの先頭語が読み取り専用語（cat/grep/head/tail/diff/git diff\|log\|show/Get-Content 等）で、リダイレクト・変数代入・書き込み語（python/node/perl/awk/sed/dd/install/cp/mv/tee/mklink/ln/git apply\|checkout\|restore/Set-Content/[IO.File]:: 等）を含まない場合だけ allow、それ以外は **ask**。デコードパイプ・難読化（base64 -d \| sh、-EncodedCommand、Invoke-Expression）とパッチ適用系 git は保護パス不在でも **ask**（RG-1/SC-4。旧実装の固定 allowlist は python -c 等 20 ベクタが素通り）。実行時間の担保（SC-5。timeout 5s 超は fail-open で素通りするため）: セグメント・候補ごとの照合は sh も bash 組み込み（`=~`）のみで外部プロセスを起動せず（Windows 実測: grep/sed 版は 1 セグメント約 370ms で 16 セグメントが 6.5s、組み込み版は 32 セグメントでも約 1.2s）、33 セグメント超は評価前に **ask**（fail-closed）、8.3 短縮名（`GITHUB~1/hooks/x`・コマンド文字列中の `C:/…/GITHUB~1/…` トークン）は長形式に展開してから照合（H-10/RG-7 横展開） |
| `security-hooks.json` | PreToolUse | `guard-secret-leak.*` | クラウド鍵/秘密鍵ヘッダ等の高確度パターン（AWS/GitHub/Slack/Anthropic/Google/Stripe/npm/JWT/Azure AccountKey・SAS sig/OpenAI/GitLab/Hugging Face/PyPI/SendGrid/DigitalOcean/PGP）は **deny**、汎用的な `password=...` 等は **ask**（クォート無しの値も 16 文字以上かつ数字を含めば対象。RG-5）。読み取り系ツールは対象外 |
| `gate-hooks.json` | PreCompact | `inject-progress.* PreCompact` | コンテキスト圧縮前にGATE_STATUS・教訓を再注入（圧縮でSessionStart注入分が失われる穴を塞ぐ） |
| `gate-hooks.json` | PreToolUse | `guard-phase-scope.*` | 進行中フェーズが無い状態でのアプリコード編集を状態依存で強制（in_progress あり=allow / progress.md ありで進行中なし=**deny**・D062 / 未初期化=**ask**）。`/12-change-request` 等の入口を経てフェーズを `in_progress` にしてから作業する運用の機械担保。「状態: 運用中」注記があるときは brownfield 取り込み残余の `test: in_progress` を進行中に数えない（/12 が要件/設計/実装を in_progress にした改修サイクル中は allow。H-4/RG-6）。パスは 8.3 短縮名（`RUNNER~1` 等）を長形式へ展開してから前方一致で照合（.sh は `cygpath -l`・.ps1 は `Get-Item`。H-10/RG-7）。本体判定は「DECISIONS.md または USAGE.md があり、かつ memo がテンプレのまま」（H-11）。ハーネス管理領域 docs/ 等と読み取り系ツールは対象外。deny の緊急経路は人間の直接編集（フックはエージェントのツールだけを見る。D043/D059/D062） |
| `gate-hooks.json` + `.claude/settings.json` | UserPromptSubmit | `route-request.*` | ユーザーの依頼のたびにGATE_STATUS要約と受付ルーチンのルール（分類/入口/影響の冒頭宣言→入口コマンドの自己起動）を注入する（SessionStart 1回の注入は会話が伸びると薄まるため。D043。Copilot へは gate-hooks.json 経由で既定配線=D060。VS Code は `.claude/settings.json` を opt-in でしか読まないため既定の二重発火は無い=D058 実機確認。有効化時のみ注入が重複するが無害。D058 は VS Code の Local ハーネス限定の事実で、既定の Agent Host と Copilot CLI は `.claude/settings.json` も既定で読み重複排除なし・直列実行＝再監査 2026-09-09 CP-3。`.vscode/settings.json` の `chat.hookFilesLocations` で `.claude/settings.json` を false にして抑止する） |
| （Claude Code専用: `.claude/settings.json`） | Stop | `remind-record.py` | アプリのコードを変更したのに docs/ に何も記録していない状態でターンを終えようとしたら**1回だけブロック**して記録（台帳/tasks/learnings）を促す（トランスクリプト解析でこのセッションの編集だけを見る。`stop_hook_active` でループ防止。D043）。Stop ペイロードの `last_assistant_message` を一次入力とし、トランスクリプト末尾の応答と一致しない＝当ターン未反映のときは判定を保留してブロックしない（公式: transcript は非同期書込で Stop 時点の最終メッセージを含まないことがある。CC-14/A6-18）。自己テストは `--selftest`（selftest.sh / selftest.ps1 から呼ばれる） |
| （Claude Code専用: `.claude/settings.json`） | Stop / SessionEnd | `log-effort.py` | 会話ごとの**受領書**（トークン累計・host 推定費用・文脈%・GATE 遷移・tasks [x] 増分・tests・委譲・基準線比較）を `.github/hooks/logs/usage/<sid>.receipt.draft.json`（Stop）→ `.receipt.json`（SessionEnd で rename + `sessions.jsonl` 1 行追記）に書き、区切り（GATE 変化 / フェーズコマンド起動 / Δ費用・Δトークン / N 発話ごと / 文脈閾値）のときだけ `systemMessage` 3 行で提示する。**block は決して返さない**。旧 `docs/00-overview/effort-log.csv` への記録は廃止（`python tools/effort-report.py --migrate` で取り込み。2026-09-09 再監査 RC-4/TM-3）。集計・表示の正は `tools/session-receipt.py`。`docs/00-overview/progress.md` があるプロジェクトのみ・失敗しても常に継続。自己テストは `python .github/hooks/scripts/log-effort.py --selftest`。Python 系フックは `run-python.sh` ラッパ経由で起動し、`python`/`python3` どちらしか無い環境でも動く（D046） |
| （Claude Code専用: `.claude/settings.json`） | SessionStart | `session-baseline.py` | `source` が `startup/clear/resume/fork` のときだけ、セッション開始時点の GATE_STATUS・HEAD sha・tasks [x] 数・`git diff --numstat` を `logs/usage/<sid>.baseline.json` に保存する（受領書の遷移・増分・コミット数の基準。**compact では書かない・存在すれば上書きしない**。resume の再キャッシュ費用欄は記録のみ。stdout 無し。自己テストは `--selftest`） |
| （Claude Code専用: `.claude/settings.json`） | SubagentStop / PostToolUse（matcher `Agent\|Task`） | `log-subagent.py` | サブエージェントの種別・要求モデル→実行モデル（`resolvedModel`/`modelsUsed`）・effort・status を `logs/usage/<sid>.subagents.jsonl` に 1 行追記する（受領書の委譲欄と実行モデルの証拠。`tool_name` の自己フィルタ必須=Copilot CLI の重複読込と VS Code の matcher 無視への耐性。トークンは記録しない=PostToolUse の usage は最終リクエスト分のみ。自己テストは `--selftest`） |
| （Claude Code専用・`statusLine.command`・`.claude/settings.json` に既定配線。無効化・差し替えは `.claude/settings.local.json` の `statusLine` で上書き） | statusline（フックではない） | `statusline.py` | 生の statusline JSON（`cost.total_cost_usd`・`context_window.used_percentage`・`cost.total_lines_*`=対話モードで費用を取れる唯一の公式経路）を `logs/usage/<sid>.status.json` に temp→rename で永続化してから、`[工程] GATE 要約 \| $ \| ctx% \| model effort \| +行/-行 \| 版` の 1 行を表示する。失敗時は前回表示を出す。追加プロセス起動ゼロ（git/jq 不使用）。**ps1 鏡は作らない**（公式: Windows では Git Bash 経由で実行）。既存の statusline がある人は `--chain "<既存コマンド>"` で連結。フック数には数えない（gen-docs の HOOK_HELPER_SCRIPTS） |
| （Claude Code専用: `.claude/settings.json`） | Notification | `notify.*` | 承認待ち等の通知発生時にローカルで音を鳴らし（Windows: SystemSounds / macOS: afplay / その他: ベル文字）、`logs/notify.log` に1行だけ記録する（時刻+通知種別のみ。本文は記録しない）。常に exit 0 の fail-open。VS Code Agent Hooks には Notification に相当するイベント定義が無いため `gate-hooks.json` には配線しない（Claude Code 側のみ） |
| （Claude Code専用: `.claude/settings.json`） | Stop | `draft-learnings.py` | セッション終了時にトランスクリプト末尾を走査し、「ユーザーの訂正→アシスタントの方針変更」パターンを検出したら教訓候補を **ローカルの下書き** `.github/hooks/logs/learnings-draft.local.md`（gitignore 済み＝プライバシー境界の local 欄）に1行起草する（A3-9。**docs/ には書かない**＝旧 `docs/00-overview/learnings-pending.md` は廃止。codex 監査 2026-08-31 H-01 / IA-20260831-03: transcript 由来のユーザー文をコミット対象に残さない）。抜粋は `privacy-patterns.json` の redaction を通した先頭 60 字だけ（`tools/usage-config.json` の `learnings_draft_excerpt_chars`。0 で抜粋なし）、候補行は `learnings_draft_rotate_days`（既定 30 日）で自動的に落ちる。**候補は注入されない**=誤検出しても以後のセッションを汚染しない。`learnings.md` への転記は人か `/10-retrospective`（harness-retrospective スキル）の明示操作だけ（自動昇格しない・原文コピーしない）。起草は同一セッション1回まで・同文は重複追記しない。非ブロッキング。当ターンの応答は Stop ペイロードの `last_assistant_message` を一次入力にし、トランスクリプトに未反映なら末尾に補ってから検出する（CC-14/A6-18）。自己テストは `--selftest`（canary secret の redaction・保持期間・docs/ 不書込を含む。selftest 両系にも同ケース） |
| （Claude Code専用: `.claude/settings.json`） | Stop | `remind-session-boundary.py` | セッション境界の固定様式（D047）の機械検査（A6-19b(1) / 再監査 2026-09-09 PF-4。第3波）。Stop ペイロードの `last_assistant_message` を読み、(A) 「このセッションの作業はここで完了です」の後ろに②「次にやること: …」と括弧書きの理由以外の実質的な行が続く、(B) 完了宣言があるのに transcript の最後の `git push` / `git tag`（Bash / PowerShell）が ask の拒否・結果未着、または応答自体が push / タグを未実施・承認待ちと述べている、のどちらかなら `systemMessage` で **warn する（block しない**=D077 決定3。判定ログは `_log.py`）。完了宣言の無いターンは無出力・無記録、2 行様式どおりなら `allow` を 1 行記録。欄欠落・壊れた JSON・transcript 不在は無出力 exit 0（fail-open）。自己テストは `--selftest`（selftest 両系から呼ぶ）。実機 Stop での発火は開発機 2.1.201 で未検証 |
| （Claude Code専用: `.claude/settings.json`） | PreToolUse（matcher `Agent\|Task`） | `guard-subagent-model.*` | サブエージェント呼出の `model` パラメータを役割別モデル方針（`.github/harness/model-policy.yml` から `tools/generate-adapters.py` が埋め込んだ役割表）と照合し、方針外の明示モデル（既定: haiku 系・方針に無いモデル名）は **deny**、省略時は既定が `inherit` の役割は素通し（既定が具体モデルの役割のみ `updatedInput` で tool_input 全体を複製して model だけ差し替え）。非 Agent ツール・`subagent_type` 欠落・表に無い役割・壊れた JSON は無出力 exit 0（fail-open）。deny（呼出時 literal）／本フック／PostToolUse の実行モデル記録の三層強制の第2層（監査 2026-09-09 §4.5）。`gate-hooks.json` には配線しない（VS Code は matcher を無視し、Copilot の runSubagent は入力形が異なる） |
| （Claude Code専用: `.claude/settings.json`） | ConfigChange（matcher `project_settings\|local_settings\|skills`） | `guard-config-change.py` | 設定・スキル変更の**第2防衛線**（2026-09-09 再監査 RG-1/CC-10/SC-3）。`guard-harness-config-edit` はツール入力（コマンド文字列・パス）を見るため `python tools/x.py` のようなスクリプト経由の間接書込は原理的に検知できない。本フックは「どう書かれたか」ではなく「書かれた結果」を見る: 保守モード（`.claude/settings.json.locked` 実在=`tools/harness-maintenance.py --on`）なら allow、それ以外で `.claude/settings.json` が git HEAD と異なれば **block**（JSON 等価なら空白差は無視）。`.claude/skills/` は中核2スキルの変更・`hooks/hooks.json`/`.claude-plugin/plugin.json` の出現・権限系 frontmatter の新規追加を **block**、それ以外の動的追加は allow。block は仕様上ユーザーにもモデルにも表示されない（判定ログ `logs/hook-decisions.jsonl` に記録＝`_log.py`。A6-20）。git が無い・HEAD に無い・JSON 不正は無出力 exit 0（fail-open）。自己テストは `python .github/hooks/scripts/guard-config-change.py --selftest`。ConfigChange は CHANGELOG 2.1.49 で追加、実機 2.1.201 で利用可（matcher 値 `skills` の対応版は未確認） |
| （補助ライブラリ・フックではない） | — | `_paths.sh` / `_paths.ps1` | 全ガードが source（ドットソース）する共通関数（D072）。`collect_path_candidates`/`Get-PathCandidates`（`file_path`/`filePath`/`path`/`notebook_path`/`uri`（`file://`・`%3A` 復号）/apply_patch 本文の `*** Add\|Update\|Delete File:` を再帰収集・正規化・重複排除・最大64件）、`get_tool_name`/`Get-ToolName`、`is_read_only_tool`/`Test-ReadOnlyTool`（D059 の読み取り系ツール名の二段判定。CP-1）、`collect_new_text`/`Get-NewText`、`collect_old_text`/`Get-OldText`（`old_string` / `edits[].old_string` の置換元。第7波 A7-G-3。sh の 1 回解析 API は 10 欄目 `_HOOK_OLD_TEXT` として末尾に追加）、`path_has_traversal`/`Test-PathTraversal`、`win_longpath`/`ConvertTo-LongPath`（8.3 短縮名 `C:/Users/RUNNER~1/…`・`GITHUB~1/hooks/x` の長形式展開。H-10/RG-7。guard-phase-scope の `expand_83`/`ConvertTo-LongPath`・候補収集・読取除外は第6波で本ライブラリの同名契約関数に差し替え済み＝複製ゼロ）と候補向けの `expand_candidate_83_into`/`ConvertTo-LongPathCandidate`（~数字を含む相対パスは cwd 結合。config-edit / template-edit が比較前に通す）。正規化・重複排除は bash 組み込みのみで行い候補ごとの外部プロセスを起動しない（旧実装は 64 候補で 3s 超=timeout 5s を候補数で押し切れた）。1箇所直せば全ガードに反映される（N面鏡を断つ）。sh 版は `parse_hook_input`（1 回解析 API。A6-20 / D080 残課題）で jq → node → python のどれか 1 プロセスだけを起動して tool_name / session_id / hook_event / tool_use_id / file / command / new_text / 候補 / パッチ本文を変数群 `_HOOK_*` に返し、以降の `get_tool_name` / `collect_path_candidates` / `collect_new_text` はキャッシュを返す（旧実装は 1 ガードあたり 3〜4 回起動。Windows 実測は下記「共通ライブラリ」）。無い環境では各ガードが従来の単一フィールド判定で動く（fail-open）。validate が各ガードの source と 8.3 展開の配線を検査する |
| （補助ライブラリ・フックではない） | — | `_log.sh` / `_log.ps1` / `_log.py` + `privacy-patterns.json` | 判定ログの共通実装（A6-20 / RC-5 / RC-11）。全フックの `hook_log` / `Write-HookLog` / python の `hook_log` はこれを呼び、`logs/hook-decisions.jsonl` に 1 行 JSONL `{ts, session_id, hook_event, tool_name, script, decision, target, tool_use_id, duration_ms, host}` を追記する（旧 4 列 TSV `hook-decisions.log` は書かない。読み手 `tools/session-receipt.py` の hooks 欄は `_log.py` の `iter_decisions` で両形式を読む後方互換）。`target` は `privacy-patterns.json`（redaction と欄分類の唯一の正。3 系統が同じファイルを読み、jq 不在の sh は 1 エントリ 1 行の行読みで拾う）で秘密値を `[REDACTED]` にしてから先頭 120 字に切り詰め、改行は JSON エスケープ。512KB 超は後半 256KB（行境界）だけ残す。無い環境では無記録で動く（fail-open）。`validate-harness.py` (k-3) が旧 TSV の直接参照・自前実装の残存・パターン JSON の妥当性を検査し、selftest 両系の canary secret 回帰（偽トークンが JSONL に平文で残らない）で意味照合する。第3波で同じ 3 系統にゲート遷移ログ（`gate_log` / `Write-GateLog` / `gate_log()` → `logs/gate-transitions.jsonl`。欄順固定 `{ts, rev, session_id, hook_event, tool_name, script, source, before, after, changed, loops}`）と `session.lock` の読み書き（`session_lock_*` / `*-SessionLock` / `session_lock_*`）を追加（`.github/harness/STATE-MACHINE.md` §4・§5） |
| （Claude Code専用: `.claude/settings.json` の SubagentStop に `log-subagent.py` と並べて配線） | SubagentStop | `guard-subagent-output.py` | 判定役（`agent_type` が reviewer / spec-critic）の終了時に、`last_assistant_message`（無ければ `agent_transcript_path` 末尾のアシスタント本文）に重大度トークン（BLOCKER / MAJOR / MINOR / 承認 または CRITICAL / HIGH / MEDIUM / LOW / 問題なし。語境界つき）が 1 つも無ければ `{"decision":"block","reason":"…verdict を明示して終了してください"}` を返す（PF-10 / D073 open。Fable 5.1 はナレーションが減り、verdict 無しの短い返答で「レビュー済み」が成立するため）。同じ session_id + agent_id で 2 回 block したら以後は allow（無限ループ防止。カウンタは `logs/subagent-output-<session>-<agent>.count`、書けなければ allow）。他の agent_type / 欠落 / 壊れた JSON は無出力 exit 0（fail-open）。自己テストは `--selftest` |
| （**opt-in・既定は未配線**） | Stop | `watchdog-continue.py` | GATE_STATUS が `in_progress` のまま停止したとき、直近の応答が承認・質問待ちでなければ有界（セッションごと既定3回）で自動継続を促す番犬。配線方法・歯止めの説明は下記「watchdog-continue の opt-in 配線」参照。継続上限・トークン枯渇で `in_progress` のまま止まったときは `logs/watchdog-stop-<session_id>.json` に事故的停止を記録し（A2-1）、`tools/golden-eval.py` / `tools/e2e-run.py` がそのセッションの done 宣言 / 走行を達成扱いにしない（下記「事故的停止の記録と目標達成評価」）。自己テストは `--selftest` |
| （Claude Code専用: `.claude/settings.json`） | StopFailure | `mark-abnormal-stop.py` | API エラー（rate_limit / overloaded / server_error 等）でターンが途中終了したとき、停止理由（`error_type` / `error_message`）と GATE_STATUS スナップショット・HEAD sha を `logs/abnormal-stop-<session_id>.json` に保存する（再監査 CC-9。Stop フック 3 本は正常終了時しか走らないため、クラッシュ検知の空白を埋める）。次回 SessionStart の `inject-progress.*` が 24 時間以内の記録を見つけたら「前回は異常終了。progress.md / tasks.md の整合を先に確認」を注入する。公式仕様上 出力・exit code は無視される=block 不能。自己テストは `--selftest` |
| （Claude Code専用: `.claude/settings.json`、matcher `session_start\|compact\|include`） | InstructionsLoaded | `log-instructions-loaded.py` | CLAUDE.md / `.claude/rules/*.md` / `@AGENTS.md` の展開が文脈に載るたびに、`file_path`・行数・バイト数を `logs/instructions-loaded.jsonl` に追記する（本文 `file_content` は記録しない）。常駐指示 200 行予算（A5-5）の**実測経路**。集計は `python tools/validate-harness.py` (i) が直近セッションの常駐（`session_start` + `include`）行数を INFO 表示し、200 行超で WARN。遅延ロード（nested_traversal / path_glob_match）は matcher で除外。**第3波の実測（2026-09-17、2.1.201 の 104 セッション）**: matcher が `session_start\|compact` だった間は CLAUDE.md 18 行だけが記録され、`@AGENTS.md`（192 行）は `include` として来るため実測から漏れていた＝`include` を matcher に追加（統合コミットの断片）。出力・exit code は無視される |
| （Claude Code専用: `.claude/settings.json`） | PreModelSwitch / PostModelSwitch | `log-model-switch.py` | メイン会話のモデル切替（`/model`・クライアント側切替・resume 時の復元）を `from_model` / `to_model` / イベント名（+ `source` / `context_tokens` / `estimated_cache_write_usd` が入力にあれば）で `logs/model-switch.jsonl` に追記する（再監査 CC-8 の記録部分。A/B の「1 セッション 1 モデル」前提の崩れを検出する）。**記録のみで block しない**（PreModelSwitch はタイムアウトも切替阻止になるため無出力・即終了、timeout 5 秒）。`--mode ask` または `tools/usage-config.json` の `model_switch_policy: ask` のときだけ、PreModelSwitch で GATE_STATUS に `in_progress` があり切替先が `model-policy.yml` の allowed 外なら `permissionDecision: ask` を返す（既定 `log` は挙動不変。記録は ask でも残し `decision` 欄に log / ask を書く。D074 open。第6波）。サブエージェントには発火しない（MP-5） |

**Claude Code 側の配線の補足（D046）**: `.claude/settings.json` の PreToolUse は
`Bash|PowerShell`（コマンド系）と `Edit|Write|MultiEdit|NotebookEdit`（ファイル系）を
マッチさせる。PowerShell ツール経由の `git push` 等が guard-dangerous-git・
`permissions.ask` の両方を素通りしていた穴を塞いだもの（ask には `PowerShell(git push:*)`
等の対も定義）。

## watchdog-continue の opt-in 配線（既定では無効）

自動継続は Stop フックの block で実現するため、歯止めなしでは「block→応答→再Stop→
再block」の無限ループ（Claude Code Issue #55754 型の暴走）になりうる。
そのため**既定ではどこにも配線していない**（`gate-hooks.json` にも配線しない）。
有効化する場合のみ、`.claude/settings.json` の hooks の Stop に、`remind-record.py` と
同じ様式で `bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/watchdog-continue.py`
を追加する。

歯止めは二重に持つ（どちらも外さないこと）:

- **stop_hook_active**: Stop フックの block によって続行した応答が再び Stop に達すると、
  ペイロードの `stop_hook_active` が真になる。真なら即 exit 0 で継続判断をしない
  （`remind-record.py` と同じループ防止。これを無視すると無限ループになる）。
- **反復上限**: `logs/` 配下の `watchdog-continue-<session_id>.count` で session_id ごとの
  継続回数を数え、上限（既定3回。環境変数 `WATCHDOG_CONTINUE_LIMIT` で変更可）に達したら
  以後は継続させない。カウンタが書けない環境でも継続させない（安全側）。

継続させた判断・見送った判断はすべて判定ログ `logs/hook-decisions.jsonl`（`_log.py`。A6-20）に記録される。
上限到達（limit-reached）と `stop_reason` が max_tokens の停止は、GATE_STATUS に `in_progress` があれば
`logs/watchdog-stop-<session_id>.json`（kind `watchdog_limit` / `max_tokens`・GATE スナップショット・HEAD sha）にも残す
（A2-1。読み手は `_log.py` の `iter_stop_records`。下記「事故的停止の記録と目標達成評価」）。
直近の応答が承認・質問待ち（`?`/`？`/「承認」「確認してください」等を含む）と
判定された場合は、ユーザーの応答を奪わないため継続させない。

## 共通ライブラリ（`_paths.*` / `_log.*`。フックではない。D072 / D080 / A6-20）

ガードの「入力の解釈」と「判定の記録」は 1 箇所に置き、各フックは冒頭で source（ドットソース）するだけにする
（N 面鏡を断つ。無い環境では各フックが最小判定・無記録で動く fail-open）。gen-docs のフック数には数えない
（`HOOK_HELPER_SCRIPTS`）。

| ファイル | 契約 | 呼び出し側の書き方 |
|---|---|---|
| `_paths.sh` / `_paths.ps1` | `parse_hook_input`（sh のみ。1 回解析 API）、`get_tool_name` / `Get-ToolName`、`is_read_only_tool` / `Test-ReadOnlyTool`、`collect_path_candidates[_into]` / `Get-PathCandidates`、`collect_new_text` / `Get-NewText`、`path_has_traversal` / `Test-PathTraversal`、`win_longpath[_into]` / `ConvertTo-LongPath`、`expand_candidate_83_into` / `ConvertTo-LongPathCandidate` | sh: `source "$(dirname "$0")/_paths.sh" 2>/dev/null \|\| true` の直後に `type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"`。以降は `_HOOK_*` 変数か従来の関数（キャッシュ）。ps1: `. (Join-Path $PSScriptRoot '_paths.ps1')`（ConvertFrom-Json 1 回で解析済みのため 1 回解析 API は不要） |
| `_log.sh` / `_log.ps1` / `_log.py` | `hook_log <decision> <target>` / `Write-HookLog $decision $target` / `hook_log(decision, target, payload=, script=, log_dir_override=)`。書式は 1 行 JSONL、置き場は `logs/hook-decisions.jsonl`（`HARNESS_HOOK_LOG_DIR` で差し替え可＝selftest 用） | sh: `source "$(dirname "$0")/_log.sh" 2>/dev/null \|\| true; type hook_log >/dev/null 2>&1 \|\| hook_log() { :; }`。ps1: ドットソース後に `if (-not (Get-Command Write-HookLog …)) { function Write-HookLog($d, $t) { } }`。py: `import _log as _hooklog`（無ければ None） |
| `privacy-patterns.json` | redaction（`redact[]`: name / pattern / replace / ignore_case）と欄分類（`fields`: committed / local / never）の正。パターンは POSIX ERE・.NET・Python re の共通部分だけ（`\s \d \w` 不可）、1 エントリ 1 行 | 3 系統が同じファイルを読む。3 系統のバイト同一は要求せず、selftest 両系の canary secret 回帰で意味照合（監査 §5.4 の方針） |

- **1 回解析の効果（Windows 11 / Git Bash / jq 不在＝node 経路。5 回平均、2026-09-14 実測、main b873dde 比）**: guard-phase-scope 1,172→491ms、guard-harness-config-edit（Write）707→414ms・（Bash `cat AGENTS.md`）1,328→651ms、guard-template-edit 681→410ms、warn-gate-tamper 1,000→634ms、guard-dangerous-git 330→373ms（元から 1 回起動。共通ライブラリ 2 本の source 分だけ増）。フックの timeout 5s 超過は fail-open で素通りするため、基本コストの削減は安全性でもある（SC-5）。
- **判定ログの欄**: `ts`（ローカル時刻＋オフセット）・`session_id`・`hook_event`・`tool_name`・`script`・`decision`（deny / ask / warn / allow / block / hold / skip / inject）・`target`（redaction → 先頭 120 字 → JSON エスケープ。切り詰めは redaction の後＝境界で切れた秘密値の前半を残さない）・`tool_use_id`・`duration_ms`（スクリプトが `_log` を読み込んだ時点から記録までの経過ミリ秒＝スクリプト開始の近似。sh は `$EPOCHREALTIME`（無ければ `date +%s%N`）、ps1 は Stopwatch、py は `time.perf_counter`。プロセス起動分は含まない）・`host`（claude-code / copilot / unknown。ペイロードの欄名 snake_case / camelCase → 環境変数 `CLAUDE_PROJECT_DIR` の順の近似。公式にホスト製品を識別する欄・環境変数は無い）。無い欄は null（2026-09-17 より前の行は末尾 2 欄が無く、読み手は null で読む）。受領書（`tools/session-receipt.py`）の hooks 欄は session_id で JSONL を絞り所要 P50 / P95 を 1 行に載せ、旧 TSV はセッション開始以降の時間窓で数える（後方互換の近似）。集計は `python tools/hook-metrics.py`（下記「フック計測」）。
- **検査**: `validate-harness.py` (k) が `_paths` の source（guard-phase-scope 含む）、(k-2) が 8.3 展開、(k-3) が `_log` の一本化（旧 TSV `hook-decisions.log` の直接参照・自前 `hook_log` / `Write-HookLog` の残存＝ERROR、`privacy-patterns.json` の JSON 妥当性・1 エントリ 1 行・pattern のコンパイル）、(k-4) が 3 系統（`_log.py` の FIELD_ORDER・`_log.sh` の printf 書式・`_log.ps1` の行組立）の欄順一致と末尾 `duration_ms` / `host` を見る。`_log.py --selftest` / `guard-subagent-output.py --selftest` と selftest 両系の第6波ケース（canary / UTF-8 / model_mismatch / SubagentStop / ask モード）。

## スクリプトの編集規則（事故防止）

- **`.ps1` はUTF-8 BOM付きが必須。** Windows PowerShell 5.1はBOMなしUTF-8の日本語を
  パースできず、フックが全滅する（派生ハーネスの初版で実際に発生した事故）。
  編集後の確認: `head -c 3 <file> | xxd` の出力が `efbbbf` であること。
- `.sh` はLF改行必須（`.gitattributes` で強制済み。CRLF化するとbashが実行できない）。
- **JSONペイロードを素朴な grep で読まない。** 値中のエスケープ済み引用符（`\"`）で
  抽出が切れ、危険判定に到達しないまま fail-open する（`cd "D:/…" && git push` が
  素通しになった実例あり）。`jq`/`node`/`python` によるJSON解析を主とし、grepは
  すべて無い環境のフォールバックに限る。パス照合は `\` 区切り（Windows）を `/` に
  正規化してから行う（`.ps1` 版は `[\\/]` 表記で両対応）。
- **フックスクリプトを変更したら `bash scripts/selftest.sh` を実行する。**
  フックは壊れていても静かに通る（fail-open）ため、「判定ログが空」なのが
  「発火する場面が無かった」のか「壊れている」のかは自己テストでしか切り分けられない
  （検知漏れ3件が2サイクル気づかれなかった実例あり）。

## 前提・注意点（正直な情報）

- Agent Hooksは執筆時点（2026年8月）で **Preview機能** であり、設定フォーマットや
  stdin/stdoutのペイロード形状は今後変わる可能性があります。
- スクリプトはペイロードのキー名を複数パターン（`file_path`/`filePath`/`path`、`command`等）で
  緩く探索し、**パース失敗時は安全側（`continue: true`、ブロックしない）に倒す**設計にしています。
  実際の挙動確認後、ペイロード形状に合わせて調整してください。
- 組織によっては `chat.useCustomAgentHooks` や関連設定が組織管理下で無効化されている場合があり、
  その場合フックは発火しません。フックが効かなくても、`AGENTS.md` の指示レベルのルールは
  引き続き有効です（二重の安全網という位置づけ）。
- Windowsでは `windows` フィールドのPowerShellスクリプトが、Git Bash/Linux/macでは
  `command`/`linux`/`osx` のbashスクリプトが使われます。

## 動作確認方法

`/hooks` をCopilot Chatで実行すると、GUIでフックの一覧・有効状態を確認できます。

## 独立レビュー記録の検査（warn-gate-tamper の追加判定。A6-14 / RD-2）

実運用で `reviewer` が一度も起動しないまま実装フェーズが `done` になった実例（2026-09-09
再監査 RD-2）への機械検査。`warn-gate-tamper.*` は `progress.md` の新内容に
`implementation: done` または `test: done` が含まれるとき、次の順で判定する
（非ブロッキング警告。release の `security-review-report.md` 検査＝D070 と同型で、
一般の done 警告より優先する）。

1. `docs/04-test/review-log.md` に日時付きエントリ（`YYYY-MM-DD HH:MM`。形式は
   `docs/04-test/review_log_template.md`）が無く、`docs/04-test/security-review-report.md`
   も無ければ「独立レビューの記録がありません」を警告する。
2. 記録はあるが、その最新日付が当該フェーズの最新の完了証拠（`tasks.md` / `test-report.md` の
   `YYYY-MM-DD HH:MM` の最大値）より前の日なら、同文言を含む鮮度警告を出す
   （最終タスク完了後に再レビューしていない状態の検出）。
   「当該フェーズ開始以降」は機械可読な開始日が無いため、この「最新の完了証拠と同日以降」で
   近似している（sh / ps1 同一規則。selftest 両系で固定）。
3. 記録はあるが、このセッションの `logs/usage/<session_id>.subagents.jsonl` の直近の reviewer / spec-critic の実行モデルが
   役割別モデル方針（`model-policy.yml`）の allowed 外なら `model_mismatch` の専用警告（D077 三層強制の done 契約側。
   記録が無ければ鳴らさない。sh / ps1 同一規則で selftest 両系に 3 ケース固定）。
4. どれにも当たらなければ従来の一般 done 警告に落ちる。

同じ規則を `python tools/golden-eval.py <プロジェクト>` が WARN として再検査する。
done 条件の正は gate-check スキル、`reviewer` の呼び出し点は implement.agent.md（手順5・6）/
test.agent.md / change.agent.md、記録の出力契約は reviewer.agent.md「記録」節。

## plugin 経由の配布時の配線（`.claude-plugin/hooks.json`。生成物）

**`.github/hooks/` には置かない**: Copilot CLI（1.0.86 で実測、2026-09-21）は `.github/hooks/` 配下の JSON を
すべてフック設定として読み、`${CLAUDE_PLUGIN_ROOT}` が未展開のこのファイルを実行して失敗し、PreToolUse を
fail-closed（拒否）にしていた（D097）。同じ理由で redaction の正 `privacy-patterns.json` も `.github/harness/` に置く。

`.claude-plugin/hooks.json` は `.claude/settings.json` の `hooks` を Claude Code plugin の
`hooks.json` 形式に写した**生成物**（`python tools/generate-adapters.py` 第6節。
`tools/plugin_manifests.py`。手で編集しない）。スクリプトのパスだけを
`"${CLAUDE_PLUGIN_ROOT}/.github/hooks/scripts/…"` に書き換え、matcher・timeout は同一。
`.claude-plugin/plugin.json` の `hooks` がこれを指す。

- 読まれるのは **plugin として導入したとき**（`claude --plugin-dir <ハーネス>` 等）だけ。
  このリポジトリやテンプレコピーを直接開く経路では `.claude/settings.json` が正で、
  `--plugin-dir` を併用すると同じフックが二重に走る（冪等のため実害は表示の重複だが、しない）。
- `.claude/settings.json` の hooks を変えたら再生成する（`--check` が鏡割れを検出。validate も ERROR）。
- plugin 経由での PreToolUse の実発火（Claude Code Issue #2540）は未確認。実インストールも未検証
  （`.github/harness/COPILOT-E2E.md` §7）。

## 説明文書の件数はここにも手で書かない（gen-docs の生成ブロック）

フック数・selftest の静的ケース数・スキル数・コマンド数・エージェント数・決定数を説明文書に書くときは、
`tools/gen-docs.py` の生成ブロック（`<!-- BEGIN GENERATED: <name> -->`）に入れる。フック数の一次データは
`.claude/settings.json` / `gate-hooks.json` / `security-hooks.json` の配線（`HOOK_HELPER_SCRIPTS` の補助 4 本＝
`selftest` / `run-python` / `_paths` / `statusline` は数えない）、selftest の件数は `^check` / `^Check` 行の
静的計数（**1 ケース 1 増分行・ループ禁止**＝D079 決定2。実行件数と一致させるため）。生成対象の一覧は
PLATFORM.md「鏡の生成物」節、ハードコードの検出は `python tools/validate-harness.py` (c)/(c-2)。
python 系ツール（golden-eval / trace-check / e2e-run 等）の selftest 件数は文書に書かない（正は `--selftest` の実行結果）。
## 証拠ゲートの役割分担（guard-done-evidence と warn-gate-tamper。A2-4b / RG-13）

| 層 | イベント | 判定 | 見るもの |
|---|---|---|---|
| `guard-done-evidence.*` | PreToolUse（書込前） | **deny** | 証拠 3 点セット（コマンド・出力要約・日時）が**同じ書込の新内容**に揃っているか、という**書式**だけ |
| `warn-gate-tamper.*` | PostToolUse（書込後） | 警告のみ | ユーザーの明示承認の有無・独立レビュー記録（`review-log.md`）の有無と鮮度・GATE_STATUS の形式・in_progress 遷移 |
| `reviewer` 観点5 / `tools/golden-eval.py` | 人・随時 | 判定・WARN | 証拠の**真偽**（コマンドの実行痕跡・成果物の実体と一致するか） |

deny 型は証拠の「有無」しか見ない（真偽は見ない）ので、`[x]` を付けるときは実際に実行した証拠を 1 行で書く
（`docs/03-implementation/tasks_template.md` の書式: `` - 証拠: `<コマンド>` → <出力の要約> (YYYY-MM-DD HH:MM) ``）。
緊急時は人間が直接編集する（フックはエージェントのツール呼出だけに発火する）。

## 外部反映の承認単位（guard-external-effect / guard-dangerous-git。codex 監査 2026-08-31 C-01 / C-02）

- **分担**: git の push / tag / force / reset --hard / rm -rf = `guard-dangerous-git`、それ以外の外部反映 =
  `guard-external-effect`（対象の一覧は上の表）。
- **判定モード**: 通常は **ask**（理由文に「何を・どこへ・コマンド」）。承認バイパス下（payload の `permission_mode` が
  `bypassPermissions` / `dontAsk` / autopilot 等、または環境変数 `HARNESS_EXTERNAL_EFFECT_MODE=deny`）では **deny**。
  Copilot 経路の payload には `permission_mode` が無いため、Autopilot で使うときは環境変数を設定する（劣化モード）。
- **承認の単位**: 「この操作・この対象・この引数」への一回限り。ツール呼出ごとに再評価されるので、対象や引数が変われば
  再度 ask になる（`docs/05-release/release-checklist.md` の action packet の行と同じ単位。計画 → 独立検証（reviewer）
  → 承認 → 実行の分離は release.agent.md 手順 3〜7・PLATFORM.md「リリースの権限分離」）。
- **実行時間**: セグメント（`;` `&` `|` 改行）ごとに bash 組み込み / .NET regex で分類し、外部プロセスは JSON 解析の
  1 回だけ。49 セグメント以上は評価前に ask（fail-closed。長大なコマンドは分割して実行する）。
- **既知の限界**: interpreter 内の `fetch()` / `requests.post()` や変数に隠したコマンドは文字列検査では見えない
  （R-01 と同じ信頼境界。境界は OS sandbox・資格情報の非付与・Git 側保護）。

## フック計測（`tools/hook-metrics.py`。R-09 / A2-7）

判定ログの `duration_ms` / `host` 欄（上記「共通ライブラリ」）を一次データに、`python tools/hook-metrics.py` が script 別の
所要 P50 / P95 / max・decision 分布（deny / ask / warn / allow / block）・host 別発火数・「false ask/deny」の近似（同じ
session_id・script で、ある target への ask / deny の直後の記録が同じ target への allow）を集計し、`tools/usage-config.json` の
`hook_slo`（P95 上限・ask 率上限・false ask 率上限・最小件数）超過を WARN する（`--strict` で exit 1。`--json` / `--kpi-line` /
`--session` / `--since` / `--logs` / `--include-legacy`）。同じ 1 行要約を受領書の hooks 欄と `python tools/effort-report.py --kpi`
が載せる。読み手と P50 / P95 の定義（最近傍順位法）は `_log.py` が正で、本ツールは複製しない。

- 所要はスクリプト内の経過（`_log` 読込 → 記録）でプロセス起動分（bash / powershell / python の起動）を含まない。体感レイテンシは
  起動分を上乗せして読む。
- ask 率は allow を記録するスクリプト（guard-harness-config-edit / guard-phase-scope / guard-template-edit 等）に限る（ask / deny
  しか記録しないスクリプトでは分母が無い）。
- 実機の分布は 2026-09-17 時点で未計測（selftest の値だけ。開発機の判定ログは selftest 由来が大半）。`hook_slo` の値は初期値で、
  実機分布で校正する。
- 起動回数を減らす公式の hooks `if`（hooks リファレンス: 許可ルール構文でツール呼び出しを絞り、不一致なら起動しない。導入版は
  公式リファレンス・CHANGELOG（2.1.257 まで）に記載なし）は、guard 系には適用しない（`/usr/bin/git push`・`sh -c '...'`・
  `git -C . push` が `Bash(git push *)` に一致せず素通りする＝公式の一致表。A6-18 の判断どおり）。docs 配下だけを見る
  PostToolUse の警告 3 本（warn-stale-gate / check-doc-chars / warn-gate-tamper）への `"if": "Edit(docs/**)"` は候補（`Edit`
  規則は Write / MultiEdit / NotebookEdit にも効き、`Write(...)` 規則は参照されない）だが、8.3 短縮名・バックスラッシュ区切りの
  パスが一致しない可能性と 2.1.201 での挙動が未検証のため既定では付けない（適用の可否は hook-metrics の発火数で判断する）。

## 事故的停止の記録と目標達成評価（A2-1）

| 記録 | 書き手 | 中身 |
|---|---|---|
| `logs/abnormal-stop-<session_id>.json` | `mark-abnormal-stop.py`（StopFailure。配線済み） | `error_type` / `error_message`（500 字）・GATE_STATUS スナップショット・HEAD sha・cwd |
| `logs/watchdog-stop-<session_id>.json` | `watchdog-continue.py`（Stop。opt-in）。継続上限到達（`watchdog_limit`）・`stop_reason` max_tokens（`max_tokens`）で `in_progress` のまま止まったとき | `kind` / `reason`・GATE_STATUS スナップショット・HEAD sha・cwd |

読み手は `_log.py` の `iter_stop_records()` / `stop_records_for_sessions()`（2 種類を共通 dict
`{kind, reason, session_id, recorded_at, gate_status, head_sha, cwd, path}` で返す。書き手側の形式を変えるときはここも変える）。

- `tools/golden-eval.py`: 記録の GATE_STATUS スナップショットが今の GATE_STATUS と同じ（＝その後に整合確認の編集が無い）なら、
  SessionStart の baseline（`logs/usage/<sid>.baseline.json` の `gate`）と比べてそのセッション中に `done` になったフェーズを
  **達成扱いにしない**（NG・通過数から除く・exit 1）。baseline が無ければ WARN（帰属不能）、done 遷移が無ければ WARN（整合確認の
  督促）。GATE_STATUS が変わっていれば何も出さない。確認後は記録ファイルを消す。
- `tools/e2e-run.py`: 走行の session_id に記録があれば `verdict` を `DNF_ABNORMAL_STOP`（TURNS より後・BUDGET より先）にし、
  結果 JSON の `abnormal_stops[]` と `totals.n_abnormal_stops` に残す（能力実験では `valid_for_comparison=false`）。
- 次回 SessionStart の `inject-progress.*` が 24 時間以内の `abnormal-stop-*.json` を注入する挙動は従来どおり
  （`watchdog-stop-*.json` は注入しない）。
