<!-- proposals: A6-1..24 -->
# 外部再監査 2026-09-09（前回 2026-08-31 監査以降の変化を踏まえた全体再チェック＋役割別モデル選択・会話ごとの受領書の設計）

- 実施: 前回再監査（`audits/external-reaudit-2026-08-31.md`）とは別セッション。**Workflow で 226 エージェント**をオーケストレーション（最新動向リサーチ 16・観点別監査 12＋網羅性批評 1・敵対的検証 182・設計レビュー 15）。統合（本文書）はエージェントに任せず監査者（本セッション）が一次データと照合しながら執筆した。サブエージェントのトークンはおよそ 2,550 万（リサーチ 258 万・監査 479 万・検証 1,509 万・設計レビュー 304 万）。
- 対象: リポジトリ HEAD `bd9d61b`（2026-08-31 08:11 JST。前回監査以降のコミット 6 本は説明文書のみ）＋未追跡 2 ファイル＋**実運用 3 プロジェクト**（ChronoLines / team-operations-hub / vision-bridge の effort-log.csv とローカル transcript。今回初めて監査対象に加えた）＋ 2026-09-09 時点の一次情報（Claude Code CHANGELOG v2.1.252〜2.1.265・hooks/sub-agents/statusline/costs/permissions 公式ドキュメント・Anthropic のモデル選択/effort/キャッシュ指針・Fable 5.1 プロンプト指針・VS Code 1.135/1.136・Copilot CLI 1.0.81〜1.0.83・docs.github.com hooks-reference/CLI config・Spec Kit v1.0.5・BMAD v6.12・OMC v5.3・GSD Core v1.13・superpowers v6.3・gstack・ECC v2.2.1・Sonar/Vanamo/Faros/Jellyfish の実測記事）。
- 位置づけ: 利用者の依頼は 3 点。①前回監査の指摘に加えて**ハーネス全体を改めて洗い直す**（最新のハーネス事情・AI 事情・信頼できる個人発信・公式情報の変化を織り込む）、②**役割別モデル選択**（オーケストレーターは賢いモデル・単純タスクは別モデル）の本ハーネスへの適用可否とモデルの選定、③**会話ごとに使ったトークン量と成果指標を出し、妥当なトークン量かを分析して次につなげ、実際に使っている人に都度伝える**仕組み。
- 検証規律: 重複排除後 134 指摘のうち critical/high 40 件は「実在性・重複性・妥当性」の 3 レンズで各 1 エージェント、medium 62 件は 3 レンズ統合で 1 エージェントが、実ファイル・実行で「反証」を目標に検証した（**CONFIRMED 87 / PARTIAL 5 / REFUTED 10**、未検証 0）。low/info 32 件は未検証のまま §7 に列挙。設計成果物 5 本は「実現性・品質リスク・導入コスト」の 3 観点で各 1 エージェントがレビューし、その訂正を §4〜§6 に反映した。監査者自身も主要指摘を手元で再現した（§2 各所に実測を明記）。
- 透明性の注記: (1) 初回の検証実行はセッション上限で全エージェントが失敗し、判定器が「投票 0 件」を CONFIRMED と数える欠陥があった。検証器のプロンプトから 23 万字のリサーチ要約必読を外し、投票 0 件を UNVERIFIED として再実行した結果のみを採用している。(2) 前回監査以降の 6 コミット（説明文書の整合）は**本監査者と同じセッション系統が書いたもの**であり、その劣化（RG-12「全PASS」刻印）は本監査で自己指摘した。(3) 実行環境は claude 2.1.201・jq 不在（sh ガードは node→python 経路で検証）・copilot CLI 未導入。

## 総評

**前回 CRITICAL 2 件（CI 赤放置・設定ガードの `python -c` バイパス）は 9 日間手つかず**で、その間の 6 コミットは説明文書だけを整えた。今回はそれに加えて、初めて実運用データを読んだことで「設計どおりに動いていない」型の欠陥が 2 つ露出した。

1. **実運用で独立レビュー（reviewer）が一度も起動していない**（RD-2）。ChronoLines は 26 タスクを done・26 コミットを push 済みだが reviewer の起動は 0 回。/06 は reviewer を要求せず、/08 末尾のレビューは「新しいチャットで /07」という人手の再起動の向こう側にあり、利用者が「実装完了で止める」最頻パターンでは永久にレビューされない。中核主張「別コンテキストの独立レビューを経て done」が、唯一完走した実装フェーズで成立していない。
2. **役割別モデル方針（D040 決定5）の前提データは 08-19 に揃っていたが誰も読んでいなかった**（RD-1）。e2e 由来の「task-worker は総コストの 6〜9%」は実運用では逆転し、**task-worker が 66.8%（ChronoLines 内 75.8%）**・差し戻し 0/26。しかも判定に使う単価表（effort-report.py）は Sonnet 5 を 1.5 倍・Fable 5.1 の cache_read を 4 倍過大に見積もる（TM-1/MS-3）。方針の「支配項」も「1/3 以下」も数字が違う。

計測・提示の側では、**裏で取っている情報が利用者に一度も届いていない**（TM-3/RC-6: statusLine 未設定・Stop フック無出力・/99 に費用欄なし）、**取った CSV が 3 プロジェクトすべてで git 未追跡かつ未 ignore**（RC-4）、**計測→振り返り→還流の下流半分は 38 日間一周もしていない**（RD-4: どのプロジェクトも /10 に未到達、かつ D048 以降の機構が配布先に届いていない）。

前回の主題「applied と記録されたが一部にしか適用されていない」は今回も現在進行形で、台帳 applied 17 行のうち 3 行（A2-4 evidence-gated write／A3-4 鏡の生成物化／A3-5 トークン予算 CI ゲート）は機械ゲートとしては未完成（RG-13）。さらに **08-31 の文書整合コミットが「全PASS」を 6 面＋生成テンプレに刻印**し、CI が赤で ps1 selftest が 2 件 FAIL の状態で未検証の品質主張を機械生成する構造を作った（RG-12。本監査者の自己指摘）。前回・codex の 2 監査分（のべ 200 超）は台帳未登録で、codex 監査ファイル自体が未追跡（RG-14/OP-1）。

一方で反証も出た。「ConfigChange は 2.1.201 では使えない」（SC-3）と「effort frontmatter は 2.1.242 以降」（MS-5）は一次情報の誤読で、C-1 の第 2 防衛線は**今日の実機で配線できる**。「effort-log の input 列はプレースホルダ」（RC-1）も API 意味論の誤読で、値は正しい。

依頼された 2 つの設計への結論は以下（詳細 §4・§5）。

- **役割別モデル選択は「条件付きで妥当」**。判定役（reviewer/spec-critic）と上流（要件・設計）は最強クラス・高 effort を固定し、コーディネーター（implement/change/test＝公式文献の lead。本ハーネスの `orchestrator` は進捗ルーターで lead ではない）は高 effort、**task-worker だけが節約対象**。ただし公式手順は「モデルを分ける前に同一モデルの effort 曲線を描け」であり、実運用で task-worker が 67% を占める以上、レバーは大きいが**測ってから決める**。第 1 段は同一モデルの effort sweep（xhigh→high→medium）、第 2 段で Sonnet 5 high（同一トークン量なら −80%。ただし公式 corpus 実測は精度 −10〜12pt）。Haiku 4.5 は候補外（effort 非対応・200K・retirement 2026-10-15 以降）。Copilot は「子は親のコストティアを超えられない」ため方向が逆転する（親を強く・worker を軽く）。方針の正は `.github/harness/model-policy.yml` の 1 ファイルにし、frontmatter・deny・settings.json を生成物にする。
- **会話ごとの受領書は 2.1.201 で今日から出せる**。対話モードのコストは statusline の stdin JSON（`cost.total_cost_usd`・`context_window.used_percentage`）が唯一の公式経路で、hooks 入力にはコストが来ない（公式 not planned）。statusline で永続化 → Stop フックの `systemMessage` で「区切り」のときだけ 3 行以内で提示 → /99-status に費用欄 → 振り返りで基準線比較、の閉ループ。累計トークンは transcript 由来（非公式・要校正）と出所を明記する。成果指標は「量（GATE 遷移・tasks [x] 増分・行数・コミット）」と「整合（テスト結果・golden-eval・reviewer 起動数・差し戻し）」の二層で、**モデルの自己申告は使わない**。閾値は基準線（実運用 n=11: セッション中央値 $9.65・p90 $60.71）ができるまで警告のみ。

---

## 1. 前回監査（2026-08-31）以降の変化

### 1.1 Claude Code 公式（v2.1.252〜2.1.265、9 日間で 8 リリース）

| 変化 | 版 | 本ハーネスへの影響 |
|---|---|---|
| Fable 5.1（`claude-fable-5-1`、$10/$50、cache read $0.25＝0.025x、1M 文脈）が `fable` alias の既定に | 2.1.257 / API 2026-09-01 | 単価表（effort-report.py）が Fable 5 前提のまま（TM-1）。2.1.201 では Fable 5.1 が API 400 で拒否される（実プローブ）。 |
| `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`（全サブエージェントを単一モデルへ強制） | 2.1.257 | A/B の「同一モデル」固定に必須（EV-2）。 |
| サブエージェント model 解決順が「呼出時 > frontmatter > env > メイン」に反転 | 2.1.251 | D040 決定5 と harness-guide の記述が逆（MS-2/CC-4/PF-7）。 |
| PreModelSwitch/PostModelSwitch、SessionStart(resume) の再キャッシュ費用欄 | 2.1.251 | メイン会話の途中切替監査に使える。サブエージェントには発火しない（MP-5）。 |
| `--permission-prompts none`、`--bare` が将来 `-p` の既定に、`--init-only`、Setup フック | 2.1.259 | e2e-run/CI の起動契約が変わる（CC-11/RG-17）。 |
| permissions: フック決定は permission ルールを迂回できない。deny はサブシェル・リダイレクト内も評価 | 2.1.257 | `Agent(model:…)`/`Agent(name)` deny（2.1.178〜）はフック非依存の最優先ゲート（CC-2）。 |
| hooks イベント 33 種（ConfigChange は **2.1.49 から存在**、InstructionsLoaded、StopFailure、PreModelSwitch 等） | — | ConfigChange は C-1 の第 2 防衛線として今日使える（SC-3 反証）。 |
| hooks の `if` フィールド・exec 形式・`shell: powershell` | 2.1.2xx | 14 本が全ツール呼出で bash を起動している現状の削減手段（CC-6）。 |
| TodoWrite/TaskCreate 系は Opus 4.8/Sonnet 5/Fable 5 以降の既定モデルで提供されない | 2.1.233 | 前回 §8「TaskCompleted を品質ゲートに」は発火しない（CC-5）。request-routing が TodoWrite を前提に書かれている。 |
| サブエージェント既定 background、PostToolUse(Agent) は `async_launched`（usage 無し・resolvedModel のみ） | 2.1.232 / 2.1.198 | 実行モデルの記録経路に制約（MP 設計レビュー）。 |
| `/skill-doctor`、`/insights`、`/usage` の Prompt cache 行、modelPricing 管理設定 | 2.1.242〜2.1.261 | 常駐指示ダイエット（A5-5）と受領書の校正に使える。 |
| Native Windows は sandbox 非対応（WSL2 推奨）が公式明記 | — | Windows ネイティブでは hooks+deny が唯一の防御＝C-1 の重みが増す。 |
| 公式指針: 「effort を先に、モデル分割は単一モデルの effort 曲線全体に勝つ場合のみ」「解決タスクあたりコストで判定」「Opus 5 から始め、Fable 5.1 は evals が届かない時」「キャッシュはモデル・effort 単位（Fable 5.1 は 2.1.260 以降 effort 変更でキャッシュ維持）」 | — | §4 の結論の根拠。 |
| Fable 5.1 プロンプト指針: 過度に指示的なスキルは品質を下げる、進捗テキストの減少、近傍バグの勝手な修正・余剰テスト・全ファイル書換が実失敗モード、低 effort は Read/検索が減る、executor を低 effort にすると相談率が崩壊 | 2026-09-01 | 指示層の棚卸し（PF 群）と A/B の従属変数（Read 回数・Write 全置換・escalate 回数）。 |
| Anthropic: Claude 5 世代向けに Claude Code のシステムプロンプトを 80% 超削減（2026-07-24）。Boris Cherny「CLAUDE.md/skills/hooks は 6 か月ごとに全削除して失敗したものだけ戻す」 | — | 常駐 335 行（AGENTS.md 296＋CLAUDE.md 39、24,039B）の削減圧力（PF-13）。 |

### 1.2 GitHub Copilot 公式（VS Code 1.135/1.136、CLI 1.0.81〜1.0.83、docs 2026-09-02）

- **Agent Host が既定**。prompt files は Agent Host で読み込まれず、Local ハーネスは将来削除（CP-2）。`chat.agentFilesLocations` 等 4 設定が非推奨。→ 本ハーネスの入口 18 本（/00〜/99）の起動経路が消える。
- Agent Host と Copilot CLI は `.claude/settings.json`・`.claude/skills`・`CLAUDE.md` も既定で走査し、**重複排除なしで直列実行、timeout は fail-open**（CP-3/SC-5）。`chat.useClaudeHooks` の門は Local ハーネス限定。
- サブエージェントの model は「runSubagent 引数 > .agent.md（表示名または優先順配列）> 親」。**子は親のコストティアを超えられない**（VS Code は起動拒否、CLI は無言降格 #2758）。`model: auto` は公式 frontmatter 仕様に無い（CP-4/MS-4/MP-1）。
- CLI 1.0.83: model 配列と `model-policy: required`。1.0.70: trusted repo の `.github/copilot/settings.json` で session model・`subagents.agents.<name>.{model,effortLevel,contextTier}` をピン留め（MP-3。**effort は CLI では役割別に指定できる**＝前回・今回の「Copilot は effort 不可」は VS Code 限定の事実）。1.0.81: `--usage-output-file` に per-agent usage。
- Agent Plugins 1.0 が VS Code/CLI/SDK/app で GA（2026-08-31）。portable は MCP と skills のみ、agents/hooks/commands は `com.github.copilot` 名前空間（CP-7/A2-2）。
- 課金は AI Credits のトークン従量（1 credit=$0.01、モデル別単価）。Fable 5.1 は Business/Enterprise で管理者ポリシー既定オフ、Pro は Fable 5.1/Opus 5 不可。2026-10-02 に Opus 4.7 等が廃止（MP-4）。Enterprise-managed 既定モデル指定 GA。
- 応答フッターのホバーにモデル別トークン表示（1.135）。CLI は `~/.copilot/session-state/<id>/events.jsonl` の `session.shutdown`（非公式契約 #3551、/new で欠落 #3994）。VS Code は OTel file exporter が唯一の機械可読経路。cloud agent はフックの書込が破棄され GitHub/Copilot ホスト以外へ到達不可。

### 1.3 競合・参照ハーネスと実務者の発信

- Spec Kit v1.0.0〜1.0.5（毎週リリース、`--ai` は v0.10.0 で削除→`--integration`）。BMAD v6.12（「調査してから儀式量を決める」、全指摘 verdict+evidence ログ）。OMC v5.0〜5.3（closure verifier、alias テレメトリ、cost tracker）。GSD 本体はアーカイブ→ open-gsd/gsd-core v1.13（install 時に model_policy を焼き込み、**skill frontmatter の effort 出力を v1.11.0 で停止**＝キャッシュ破断のため）。superpowers v6.3（fresh context の後退＝実装者を再開）。gstack v1.81（evidence ledger を作業ツリー指紋に紐付け、verify 未通過なら Stop で終了阻止、`gstack-context-bill` でスキルの文脈コスト監査）。ECC v2.2.1（PowerShell/変数代入経路の設定ガード＝C-1 の負例集として流用可、所有ハッシュ方式）。Kiro（Auto とモデル別クレジット倍率、OTel 利用メトリクス）。gh-aw v0.88（agentic firewall）。claude-receipts（SessionEnd で受領書を出す先行 UX）。
- Sonar（2026-09-01）: 800 行 PR＝512 ターン・1.56 億トークン、**請求の 98% がキャッシュ再読**、ターン 10% 減で約 16% 減。Vanamo（2026-07-06）: 12 モデル×3 ハーネスで「マージ済み機能あたりコスト」（coder$ と gate$ の分離。安いコーダーほどゲート費に移る）。Faros 2026: レビューなしマージ +31% がインシデント増と相関。Jellyfish: p90 は中央値の 13 倍。Anthropic: 企業平均 $13/開発者/日、Claude Code のターン時間 99.9 パーセンタイルが 25 分→45 分超。Dex Horthy: 実効文脈 300〜400K。
- 前回 §8 の「AGENTS.md ネイティブ対応」は Issue #6235 が completed で close されたが、実装は `/import`（2.1.213）の一回限りコピーであり `CLAUDE.md → @AGENTS.md` 方式が引き続き正解（CC-13）。

### 1.4 本ハーネス側の変化（dbbc37e..bd9d61b）

機構ファイルの変更ゼロ（`git diff --stat dbbc37e..HEAD -- .github/hooks .claude/settings.json` は README.md と draft-learnings.py の計 4 行のみ）。変更は説明文書（README/README.en/COMPARISON/HTML 6 本/USAGE/PLATFORM/GLOSSARY 新設/gen-docs のフック数一次データ化）。CI は 28/28 失敗のまま（08-31 以降 push 無し）。未追跡: `.github/hooks/scripts/_paths.sh`（D072 相当・未配線）、`audits/independent-harness-audit-2026-08-31-codex.md`（541 行、CRITICAL 2 件を含む独立監査）。

---

## 2. 新規指摘（検証済み CONFIRMED。監査者が一次データで再確認したものは「実測」と明記）

重大度は「監査時→検証後」。検証後の格下げの多くは「前回指摘の言い換え」または「実データで規模が訂正された」ことによる。監査者としての判断が検証結果と異なる箇所は注記した。

### 2.1 CRITICAL（前回からの回帰状態）

**RG-1（CONFIRMED, critical）C-1 は全ベクタで再現。前回に無い 5 ベクタも素通り。ConfigChange の第 2 防衛線は未配線。**
- 実測（検証者 3 名が Python 生成 JSON を stdin 投入し sh/ps1 双方で再現）: `python -c open(...)` / `node -e writeFileSync` / `git apply` / `git checkout HEAD~1 --` / `git restore --source` / `dd of=` / `install` / `mklink /J` / `ln -s` に加え、**`New-Item -ItemType Junction` / `[IO.File]::WriteAllText` / `perl -pi -e` / 変数間接 `f=AGENTS.md; echo x > $f` / python ヒアドキュメント**がすべて PASS-THROUGH。`guard-harness-config-edit.sh:101-106` / `.ps1:81-86` の write_pattern は固定 allowlist のまま、最終変更は 52d1da4（前回監査より前）。`.claude/settings.json` に ConfigChange 配線 0 件（監査者実測）。
- 対策: (1) allowlist を反転（保護パスが読み取り専用語以外の文脈に出たら ask。ECC v2.2.1 GateGuard の負例集を流用）。(2) **ConfigChange（matcher `project_settings|local_settings|skills`）を配線**し、承認済みハッシュ（保守モード ON 時に harness-maintenance.py が更新）と不一致なら deny。ConfigChange は CHANGELOG 上 2.1.49 で追加されており実機 2.1.201 で使える（SC-3 の反証）。matcher 値 `skills` の追加時期は未確認のため selftest で実機確認する。(3) 本監査の 34 ベクタ JSON を selftest 両系に回帰追加。(4) 将来「公認生成器（generate-adapters.py）」を例外扱いする規則（所有ハッシュ等）を封鎖と同時に設計しないと生成と衝突する（MP 設計レビュー）。

**RG-2（PARTIAL, critical）C-2: CI は 28/28 失敗（2026-08-11〜08-30）。ubuntu は step 4 `import yaml` で即死し後続 7 ステップは一度も実行歴なし。windows は selftest.ps1 71/2 FAIL（phase-scope の 8.3 短縮名 2 件＝RG-7）。**
- 実測: `requirements*.txt`/`pyproject.toml` 不在、harness-ci.yml に `pip install` 0 件、`gh run list --limit 100` → failure 28 / success 0。PARTIAL は「前回 C-2 の言い換え」という重複性判定によるもので、事実はすべて再現している。
- 対策: `requirements-dev.txt`（PyYAML 固定）を両ジョブで install するか、validate-harness.py の frontmatter 解析を gen-docs.py と同じ正規表現版に置換して依存ゼロにする（配布思想と整合）。windows の 2 FAIL は RG-7 で消す。**緑を確認した run URL を DECISIONS に記録するまで applied にしない**を運用規則へ。

### 2.2 HIGH（検証後も high）

| ID | 指摘（証拠の要点） | 対策の要点 |
|---|---|---|
| **CC-1** | 実機 2.1.201 は自ら宣言した最低安全版 2.1.217（`--max-budget-usd` のサブエージェント合算修正版）未満。A/B 結果 10 本すべてが 2.1.201 で採取（監査者実測: 全 JSON `claude_version` 2.1.201）。e2e-run.py は `--help` 文字列でフラグ有無しか見ず版数比較なし。validate/doctor にホスト版検査なし。 | PLATFORM 最低版表に 2.1.232/2.1.251/2.1.257 行を追加し要求版を上げる。e2e-run に semver 比較の起動ゲート、validate に `claude --version` 検査。既存 10 結果は「予算条件不成立・参考値」と明記。開発機を 2.1.265 系へ更新。 |
| **CP-1** | VS Code は matcher を無視するため deny 型ガード 2 本（config-edit/template-edit）が `read_file` まで deny（実行再現: PLATFORM.md・gate-check SKILL.md・reviewer.agent.md・`*_template.md` の読取が sh/ps1 とも deny）。D059 の読取除外は guard-phase-scope 1 本にしか入っていない。D059-1「テンプレを使わず独自形式で自作」は読取 deny の帰結と整合。 | 読み取り系ツール名フィルタを共通関数（_paths.sh/.ps1）に抽出し全ガードの冒頭で適用。selftest に VS Code 形（`readFile`+`filePath`）の読取→allow ケース。COPILOT-E2E に「PLATFORM.md/テンプレの読取が deny されない」を追加。 |
| **CP-3** | 「.claude/settings.json は既定で読まれない＝二重発火なし」は Local ハーネス限定。Agent Host/CLI は既定で同一ガード 12 本を重複排除なし・直列で実行（VS Code ソース実読）、`.claude/skills` 21 本と CLAUDE.md も二重読込。 | `.vscode/settings.json` に `chat.hookFilesLocations: {".github/hooks": true, ".claude/settings.json": false, ...}` と `chat.useClaudeMdFile: false`（sync 経由で配布）。validate に不変条件。PLATFORM の環境差表に「読込元・重複排除・直列/並列・fail-open」行。 |
| **EV-1** | 発話単位の予算打切りが harness の**全 PASS 走行**で発火（監査者実測: 0827 call2、0829 call2/10、todo 0822-191211 call6、0822-220104 call1 …）。bare は 0 件。e2e-run.py は `subtype` を捨て、打切り後も台本が次発話を送る。 | 各 call に `subtype`/`truncated_by_call_budget`、totals に `n_truncated_calls`。能力実験では call 上限＝合計上限、打切り 1 件で DNF_BUDGET_CALL。 |
| **EV-2** | model/effort がユーザースコープ設定（`opus[1m]`/effortLevel high）から両アームへ漏れ込み結果に残らない。実モデルは transcript からのみ復元可（全 10 走行 `claude-fable-5`）で、8/22 分は約 9/21 に消える。 | `--model` フル ID 必須、`--effort` 必須、FORCE=1、`modelUsage` 保存、`--session-id` で transcript を退避。**今すぐ** 12 ディレクトリを zip 退避。 |
| **EV-3** | 結果に verdict が無く、予算超過走行（todo-cli-harness-0822-191211: cost $45.47 > 上限 $40、aborted_by_budget=true、9/10 発話）が check.pass=true で PASS に数えられる（監査者実測）。0827 harness も $69.06 > $60（call 間判定の穴で abort フラグ false）。 | `verdict ∈ {PASS, FAIL, DNF_*, INVALID_APPARATUS}` を必須化し `cost > total_budget` で判定（abort フラグ非依存）。README/COMPARISON/HTML 5 面の「$45.47 完走 PASS」「2/2」を同一コミットで改める。 |
| **MP-3** | Copilot CLI の `.github/copilot/settings.json`（親モデル固定・agent 別 effortLevel/contextTier）が未使用・未生成・未保護・未配布（監査者実測: ディレクトリ不在、protected_pattern/deny/CROSS_ITEMS/CODEOWNERS/SYNC_GLOBS のいずれにも無し）。高権限設定（deny リストも変えられる）なので保護対象外のままなら C-1 と同クラス。 | policy から生成し、保護 5 面（guard sh/ps1・deny・CROSS_ITEMS・CODEOWNERS・SYNC_GLOBS）に同時追加。キー名は docs 照合後に確定。 |
| **OP-1** | 監査→台帳→D→CI 緑のパイプライン入口が詰まり、前回再監査（177）と codex 監査（34）が PROPOSALS/DECISIONS に 0 参照。採番規則が無い。validate (e) は登録完全性を検査しない。 | 採番規則（1 監査＝1 接頭辞）、各監査 md 先頭に `<!-- proposals: … -->` 宣言、validate で突合。 |
| **RD-1** | 実運用 3 CSV 17 行を Fable 5 定価で再計算: 総 $300.74、task-worker $201.02（66.8%）、ChronoLines 内 75.8%、差し戻し 0/26（tasks.md `[x]` 26・試行記録 0・Agent 起動 26＝タスク数）。同一トークン量で Sonnet 5 high なら $40.20（−80%）、Fable 5.1 inherit $126.50（−37%、cache read 0.25 のみ）。現行単価表で同データを計算すると Sonnet 5 は $60.30（1.5 倍過大）。 | D040 決定5 の前提と単価を訂正、A/B の第 1 段を effort sweep、第 2 段を Sonnet 5 high vs Fable 5.1 low（§4）。 |
| **RD-4** | 下流半分が動かない真因は「未到達＋未配備」: 3 プロジェクトとも D040 以後に /10 未到達。ChronoLines の配備は 21 ファイル（本体 31）、Stop に draft-learnings 無し、send:true 変換・HH:MM 契約も未配備。テンプレ経路には harness-origin.md が無く /91 の既定本体も無い。 | テンプレ/archive 経路にも harness-origin.md を同梱、inject-progress で本体との版差を注入、/99 に effort-report 要約、3 プロジェクトへ /91 --apply。 |
| **RG-6** | H-4/H-11 回帰: brownfield 運用中（in_progress あり）とテンプレ経路（DECISIONS.md 残留＋実メモ記入）で guard-phase-scope が sh/ps1 とも allow（フィクスチャ実行）。 | `:239` の allow 条件に「運用中」注記を追加、`:233` の AND 条件を修正、route-request/inject-progress/request-routing の 4 面同時。 |
| **RG-7** | H-10 は **悪化**: 8.3 短縮名 fail-open は ps1 だけでなく sh 版でも双方向（ペイロード短縮/cwd 短縮）で再現（GetShortPathNameW で実 8.3 名を取得して実行）。CI windows の 2 FAIL はこれ。 | sh/ps1 とも 8.3 展開（`cmd //c for %I in … %~fI` / `GetLongPathName`）で正規化後に比較。selftest 両系に 2 ケース追加＝CI windows 緑化。 |
| **RG-9** | H-8 回帰＋新証拠: 予算非対等（bare 8/40 vs harness 10/60。監査者実測）・model 未記録は未変更。全 10 走行が 2.1.201 で `max_budget_usd_flag_used: true` を記録しているが停止は効かない版。 | e2e-run の version gate、予算をタスク定義側で共通化、`--model` 必須。 |
| **RG-13** | PROPOSALS applied 17 行を実ファイル照合: **A2-4** は PostToolUse 警告のみで「どの入力でも deny しない」、**A3-4** は生成 3 ブロックのみで手書き数値 9 箇所残存、**A3-5** は AGENTS.md バイト数のみ・由来監査 0 件・CI で一度も実行されていない。A5-9 の仕込み 5 件は evaluation/ に機械成果物なし。他 11 行は真。 | 3 行を `partial` に戻し子 ID（A2-4b/A3-4b/A3-5b）で残作業を起票。「applied は D 番号＋検証コマンド＋CI 緑 run URL の 3 点」を規則化。 |
| **SC-1** | `.claude/rules/` が保護対象ゼロ（Write `.claude/rules/evil.md` → sh/ps1 とも allow。deny・protected_pattern・CROSS_ITEMS・CODEOWNERS すべてに無し）。公式では AGENTS.md と同格の常駐ロード面＝AGENTS.md を deny しても rules 経由で同じ効果が得られる新経路。 | deny・protected_pattern（sh/ps1）・CROSS_ITEMS・CODEOWNERS に `.claude/rules/**` を追加、selftest 両系に回帰。 |

PARTIAL（high。事実は再現、前回指摘の回帰状態として記録）: **RG-3**（H-1 バッククォート/キャレット行継続が sh 版で素通り。ps1 は ask だが Windows でも sh が配線され死にコード）・**RG-4**（H-2 uri/notebook_path/apply_patch が sh/ps1 とも素通り。_paths.sh 未追跡・未 source、_paths.ps1 不在、D072 は DECISIONS/CHANGELOG に無し）・**RG-5**（H-3 npm token/JWT/AccountKey/SAS/非クォート password が両系で無警告）・**SC-2**（G-3 の延長: skills 配下の `hooks:` frontmatter と `hooks/hooks.json` 直置きによる任意コマンド実行が中核 2 スキル以外で無防備。有効経路は frontmatter hooks と hooks.json、`.claude-plugin` は偶然 deny）。

### 2.3 検証後 medium（監査時 high。監査者は次の 6 件を実害の大きさから P0/P1 相当と扱う）

- **RD-2** 実運用で reviewer 起動 0 回（総評 1）。対策: implementation の done 条件に「reviewer 実施記録 1 件以上」を追加し warn-gate-tamper/golden-eval で機械検査。/06 の 10 タスク分割点ごとに reviewer 1 回（$1.5〜3 に対し 10 タスク $77＝+4%）。Claude Code では SubagentStop で「reviewer 出力ファイルが無ければ deny」。受領書に `done タスク数 / reviewer 実施数 / レビュー済み率`（ChronoLines 現在値 26/0/0%）。配備済み /06 アダプタの send:true を /91 で追従。
- **RG-12（監査者の自己指摘）** 08-31 の d6ea2e3/a240645 が「全PASS」を README.md:33 / COMPARISON.md:31,81,260 / COMPARISON.html:194 / guardrails.html:74 / overview.html:192,330 に追加し、**gen-docs.py:99 のテンプレにリテラル `全PASS`、:97 に `2環境で実機検証済み`** を刻印（監査者実測）。count_selftest_cases は `^check` 行を静的に数えるだけで実行しない。同時点の CI は ps1 71/2 FAIL。COMPARISON.md:263「golden-eval selftest 15件」は実測 18（監査者実測）で、validate の走査対象外のため未検出。対策: テンプレからリテラル主張を除去し「件数（実行結果が正）」に戻す。表示するなら selftest を実行して pass/fail を一次データとして取り込み fail>0 なら「N/M PASS（要修正）」を生成。手書き数値を gen-docs のマーカーブロックへ（A5-7 の消化）。validate の走査に `.github/harness/*.md` と「N件」「N/M」形を追加。「CI 緑を確認するまで『全PASS』『検証済み』を生成物に書かない」を DECISIONS に規範化。
- **RG-14** 2 監査分（reaudit 明示 ID 24＋無番号 MEDIUM 81、codex IA-01〜15/R-01〜09）が台帳未登録、codex 監査ファイルと _paths.sh が **git 未追跡**（監査者実測 `??`）。_paths.sh は sync-harness が作業ツリーを収集するため未追跡のまま配布先へ混入する。対策: 今日中に codex 監査をコミット、_paths.sh は「配線＋D072 記録」か「削除」を即決、sync-harness に `git ls-files` 照合。
- **TM-3 / RC-6** 計測結果を利用者に提示する経路がゼロ（statusLine キー無し・Stop 3 本のうち利用者向け出力は remind-record の block のみ・/99 に費用欄無し。監査者実測）。→ §5。
- **RC-4** effort-log.csv が 3 プロジェクトすべてで `??` かつ未 ignore（監査者実測: 13/2/5 行）。`git add -A` 習慣で session_id 付き個人コストが共有リポへ偶発 commit する最悪の中間状態。→ §5.4。
- **MP-1 / CP-4 / MS-4 / MP-5** 役割別モデル方針が 6 面（D007/D040/AGENTS.md/harness-guide/README/agents.html＋実 .agent.md）に分散し役割集合が 4 通り（3/4/5/8 役）。`model: auto` は公式仕様外（監査者実測: 5 本）。Copilot ではティア制約で「レビューだけ強いモデル」が構造的に不成立。方針と上書き（呼出時 model・FORCE・EFFORT_LEVEL・ユーザースコープ `opus[1m]`/effortLevel high・ピッカー・enterprise 既定）の優先順位表が無く、実行モデルの事後検証（resolvedModel/modelUsage/--usage-output-file）が done 契約に接続されていない。→ §4。

### 2.4 その他の CONFIRMED medium（分類別。提案は §8 のロードマップに集約）

- **Claude Code 追従（CC）**: CC-2 A3-8 の予定設計「SubagentStart で機械強制」は公式仕様上ブロック不可・model 無しで成立しない。正解は `Agent(model:…)`/`Agent(name)` deny＋PreToolUse(Agent) updatedInput＋PostToolUse(Agent).resolvedModel の三層で、いずれも 2.1.201 で使える（旗艦文書 3 面の「SubagentStart で解決予定」を書換）。CC-4 解決順・単価の逆転。CC-6 hooks `if`/exec 形式未使用（Git Bash 不在で全フック fail-open）。CC-7 statusLine 未配布。CC-8 PreModelSwitch/PostModelSwitch/再キャッシュ費用欄未活用。CC-9 StopFailure/InstructionsLoaded 未配線（クラッシュ検知・常駐 200 行の実測に使える）。CC-10 ConfigChange を C-1/G-3 の第 2 防衛線に。CC-11 `--bare` 既定化・`--permission-prompts none` に e2e/CI が未追従。CC-14 Stop フック 3 本が transcript 走査で当ターンを判定しているが公式は「Stop 時点の transcript は最終メッセージを含まないことがある。`last_assistant_message` を使え」。
- **Copilot（CP）**: CP-2 Agent Host で prompt files 非読込＝入口 18 本が死ぬ（→ user-invocable スキルへの移行）。CP-5 「トークン非開示」の陳腐化記述（harness-guide/effort-report.py）。CP-6 cloud/CLI/VS Code の exit code・timeout・OS フィールド意味論差。CP-7 Agent Plugins 1.0 GA に plugin.json が未追従。
- **評価装置（EV）**: EV-4 走行条件が JSON に残らず `--continue` が cwd 依存。EV-5 artifact_stats がハーネス同梱 197 ファイルを生成物として数える。EV-6 トークン種別・モデル別内訳なし。EV-7 予算強制の版数門が `--help` 文字列。EV-8 台本が状態盲目。EV-9 他ハーネス直接 A/B は現装置で不能。
- **計測・受領書（TM/RC/RD）**: TM-1/MS-3 単価表（監査者実測: `claude-sonnet (3,15)`・`CACHE_READ_MULT 0.1`・`claude-fable-5` が 5.1 にも前方一致）。TM-2/RC-8 Copilot 経路の計測経路は存在する（events.jsonl/OTel/usage report）。TM-4/RC-3 フェーズ帰属が先頭コマンド固定（transcript の attributionSkill/effort/promptId/version は捨てられている）。TM-5/RC-2 サブエージェント帰属が tool_result 文面の正規表現依存（meta.json の agentType/spawnDepth/toolUseId が決定的に存在）。TM-6 artifact_stats。TM-7 トークンと成果の結合キーなし。TM-10 transcript の /usage 校正なし・解析率未記録。TM-11/RC-12 基準線なし（実データ n=7〜11）。RC-5 hook-decisions.log は TSV 破損（5,368 行中 41 行）・session_id 無し・hook_log が 18〜20 スクリプトに重複。RC-9 成果指標が二層化されておらず artifact_stats は Python 専用（ChronoLines の Vitest 581 件を 0 と数える）。RC-10 「レビュー無し done」が数字で露出しない。RD-3 CSV↔transcript 突合は 17 行×5 列で 0.00% 一致（帰属は正しい）。RD-5 実運用基準線（§5.5）。RD-7 全走行 2.1.201。RD-8 実運用は 941/941 メッセージが xhigh、task-worker 出力の 47% が thinking（$25.90）、Read 7〜35 回/ワーカー。
- **モデル方針（MS/MP）**: MS-1（e2e 由来の 6〜9%。RD-1 で実運用は 67% と訂正）。MS-2 解決順の記述逆転。MS-7 reviewer/spec-critic の低 effort 既定化は「証拠つき指摘」と「escalate 条項」を無効化する。MS-9 e2e-run に `--effort` が無く model/effort/modelUsage/costBasis が JSON に無い。MP-2 validate に model/effort 検査 0 件。MP-4 プラン別可用性・廃止日・ホスト別記法の吸収層がない。MP-6 工程別か役割別かの決定がない。MP-7 保護 5 面の同時更新差分。
- **セキュリティ（SC）**: SC-5 VS Code/cloud の fail-open＋直列で Windows の重いガードが timeout 5s 超→deny 沈黙バイパス。SC-6 常駐指示/設定として読まれる `.github/copilot-instructions.md`・`.github/copilot/settings.json`・`CLAUDE.local.md`・`GEMINI.md`・`.agents/skills/` が保護対象外。SC-7 MCP/プラグイン提供の書込ツールは matcher と deny の双方の対象外。
- **指示層の棚卸し（PF。Fable 5.1 指針に照らして）**: PF-1 フックで機械強制済みの禁止事項が AGENTS.md と 6 ファイルに文章重複（自己矛盾）。PF-2 受付ルーチンが 5〜6 箇所に再記述。PF-3 同一規範の多重定義（証拠 3 点セット×4・context rot の「なぜ」×6・done=人の承認×16）。PF-4 ナビゲーション責務の 26 行の出力形式強制・機械検査ゼロ。PF-5 「自走せよ」が別文言で分散（公式 autonomy ブロックへ置換）。PF-6 task-worker の禁止事項が旧世代の失敗モード中心。PF-7 陳腐化記述（SUBAGENT_MODEL 優先順・Copilot トークン非開示・TodoWrite・`model: auto`・強いモデル切替勧告 6 箇所）。PF-10 implement/test がサブエージェントの返答テキストを証拠の主経路にしている（Fable 5.1 はナレーションが減る）。
- **運用（OP）**: OP-2 ホスト版の機械チェックなし。OP-4 改善サイクル（監査→適用のリードタイム）が未計測。
- **回帰・CI（RG）**: RG-17 CI に `claude --init-only` と `plugin validate --strict` を足す前提（版固定・依存列挙）。

---

## 3. 反証・部分成立の記録（監査の健全性）

REFUTED 10 件の内訳。**「重複」で反証された 5 件は事実が現状で再現しており、前回指摘の STILL-OPEN として §8 に残す**。

| ID | 反証の種類 | 内容 |
|---|---|---|
| **SC-3** | 一次情報の誤読 | 「ConfigChange は 2.1.230 以降で 2.1.201 では採用不能」→ CHANGELOG 実読で **2.1.49 に追加**（2.1.140 に回帰修正あり）。第 2 防衛線は今日配線できる。 |
| **MS-5** | 一次情報の誤読（要実機確認） | 「subagent frontmatter `effort` は 2.1.242 以降で 2.1.201 では無視される」→ 公式 sub-agents の「Requires v2.1.242」は `/tasks` にモデル・effort を**表示**する文に掛かり、frontmatter `effort` 自体には版条件がない。CHANGELOG では v2.1.78/80 に effort/maxTurns/disallowedTools の記載。**注意**: 設計レビュー 3 本はリサーチ要約に従い「2.1.242」を前提に書いており、本監査内で見解が割れている。A/B 前に開発機を 2.1.265 へ更新すれば論点は消えるが、2.1.201 のまま試すなら hooks 入力の `effort.level`（SubagentStop）で実効 effort を確認してから走らせること。 |
| **RC-1** | API 意味論の誤読 | 「effort-log.csv の input 列はプレースホルダ（1〜2 トークン）」→ `input_tokens` は「最後のキャッシュ境界より後のトークン」であり、`claude -p` の result.usage と transcript が一致（実プローブ）。cache_creation が新規入力量を担う。**置換ロジックを入れると二重計上になる**。受領書の設計（§5）から `input_placeholder_suspected` を撤回。 |
| **OP-3** | 誤読＋重複 | 「GLOSSARY.md が無検査の手書き鏡を再生産」→ 新設は事実だが、指摘の核心証拠（selftest 件数の混入）は誤読。構造論点は前回 §1 の既出。 |
| **RD-6** | 部分反証 | 「先頭コマンド固定でフェーズ誤帰属」→ /model 先頭の 3 セッションは正しく次コマンドへ帰属。崩れるのは「工程コマンド無しの事前会話」と D048 の同一セッション自動継続時のみ（実データ $2.55、0.85%）。effort/version/entrypoint/resolvedModel が transcript にあるのに CSV に無い点は成立。 |
| **MS-6** | 重複（CC-2 と同内容） | 三層強制の設計は CC-2 に統合。事実（deny/PreToolUse(Agent) 未配線・frontmatter model 無し）は成立。 |
| **SC-4** | 重複（C-1 クラス） | 変数間接・`[IO.File]::`・Copy-Item・デコードパイプの 4 ベクタは allow で再現（RG-1 に統合済み）。 |
| **RG-8** | 重複（H-5/H-6/H-7） | /99 の progress.md 無条件作成・/07・/09 の太い再記述・/11 の実行権ゼロは**すべて未変更**（実ファイル確認）。 |
| **RG-10** | 重複（H-9） | 配布先で validate-harness.py が gen-docs.py 不在を ERROR にする不整合は**未変更**。 |
| **RG-11** | 重複（G 群） | **G-1（3 環境等価主張）と G-4（COMPARISON の誤記）は FIXED**。G-2（Copilot CLI/cloud 差）・G-3（skills 経由の昇格）・G-5（核心ギャップの台帳未登録）は STILL-OPEN。plugin.json:3 に「Antigravity対応」が残存。 |

PARTIAL 5 件（RG-2/RG-3/RG-4/RG-5/SC-2）はいずれも「事実は再現、前回指摘の言い換え」であり、SC-2 のみ主張の一部（`.claude-plugin` 経路）が過大評価と判定された。

設計レビュー（15 本）が設計成果物に見つけた誤りは §4〜§6 の本文に織り込んだ。主要なものだけ挙げる: Arm A2 の「Task 呼出時の effort 指定」は Agent ツール入力に effort が無く不成立／phase_overrides の生成先 `.claude/skills/<cmd>/` は存在せず実体は `.claude/commands/<cmd>.md`／policy を `docs/` に置くと配布されない／deny 生成の「保守モード前提」は `--off` の退避復元で生成結果が消えるため逆効果／`updatedInput` は入力全体を置換する／`Agent(model:X)` deny は明示パラメータの literal 一致のみ／background の task-worker は PostToolUse が `async_launched` で modelsUsed を観測できない／Copilot CLI の設定優先順位は公式で「repo settings.json > user settings」（設計表は逆）／effortLevel の許容値は low/medium/high/xhigh（max 無し）／statusline の `context_window.total_*`/`current_usage` は累計ではない／Stop 入力に `stop_reason` は存在しない／「ps1 を gen で生成しバイト等価検査」は現状に無い機構／Windows で statusline「50ms」は不成立（powershell 起動 0.20s）／Spec Kit の `--ai` は削除済み／`--setting-sources project,local` はユーザースコープの skills/plugins/MCP を遮断しない（空ディレクトリでの実プローブ: user skills 16 本・MCP 6 本がロード）／過去 10 走行の workdir は 9/4・9/6 の Temp 自動クリーンアップで消失済み（再検証不能）／check.py の `contradiction_resolved` は `[NEEDS CLARIFICATION: 月末 or 25日?]` を True にする偽陽性経路を持つ（Spec Kit アームが未解決のまま PASS できる）。

---

## 4. 役割別モデル選択（依頼②への回答）

### 4.1 結論

「オーケストレーターは賢いモデル、単純タスクは別モデル」は**条件付きで妥当**。ただし本ハーネスにそのまま当てはめると 3 点が違う。

1. **役割の同定**: 本ハーネスの `orchestrator` は /00・/99 の進捗ルーター（書かない）で、公式文献の orchestrator（lead＝分解・統合・escalate 判断）に当たるのは **implement / change / test**。D007 はこの lead 3 役を「呼び出し頻度が高い＝安価枠（`model: auto`）」に置いており、設問の前提と逆（MS-8）。
2. **順序**: 公式（Optimizing for cost and intelligence）は「単一モデルの effort 曲線を先に描け。マルチモデル構成は曲線全体に勝たねばならない」「解決タスクあたりコストで判定（失敗の再試行を含める）」。本ハーネスの task-worker 委譲はコスト目的ではなく context rot 対策（D034）なので、モデルを分ける前に**同一モデル・effort 差**を測る。
3. **レバーの大きさ**: e2e（小課題）では task-worker 工程は 6〜9% だが、実運用（ChronoLines 26 タスク $265）では **task-worker が 67〜76%**（RD-1）。レバーは大きい。同一トークン量なら Sonnet 5 high で −80%、Fable 5.1 inherit で −37%（cache read 改定のみ）。ただし公式 corpus 実測は Sonnet 5 worker で精度 −10〜12pt、Vanamo は「安いコーダーほどゲート費に移る」。**差し戻し（現状 0/26）が増えないことを測ってから確定**する。

### 4.2 役割×モデル×effort の推奨（A/B 前の既定と、A/B 後の候補）

| 役割 | 手戻りコスト | 現状（Copilot / Claude） | 当面の既定 | A/B 後の候補 | 根拠 |
|---|---|---|---|---|---|
| requirements / design（上流） | 最高 | 無指定 / 無指定 | inherit＋**high 固定**（xhigh/max は既定にしない） | 変更なし | 全後工程が依存。上流 52〜58% の主因は継続セッションの cache_read で、対策は分割（D057 で −29%）と往復削減であって effort 低下ではない |
| spec-critic / reviewer（判定役） | 高 | 無指定 / 無指定 | inherit＋**high 固定（low/medium 禁止）** | 変更なし | fresh-context verifier > 自己批評（公式）。D071 実測 reviewer 5/5。低 effort は「高確信のみ報告」＝見逃し増、Read が減る |
| implement / change / test（lead） | 高 | `model: auto` / 該当なし | inherit＋**high** | medium（第 2 段） | 低 effort の executor は「詰まりを検知せず相談率が崩壊」→ コスト条項（同一タスク 4 回で escalate）が無効化される |
| task-worker（量産） | 中（テストで機械検出可） | `model: auto` / 無指定 | inherit＋**high** | A1: inherit＋medium ／ A2: `task-worker-low`（effort low）＋固定テスト失敗時は `task-worker-high` へ再委譲 ／ B1: Sonnet 5 high | 公式「subagent は low の典型用途」。ただし Fable 5.1 low は Read 減・全ファイル書換増。A2 は「失敗のみ高 effort 再走」（Opus 5 実測: 同品質で約半額）だが、**task-worker は自分でテストを書くため失敗信号が内生的**＝弱いテストで pass すると再走が発火しない。受入テストを worker が書き換えられない固定検体にしてから |
| orchestrator（進捗ルーター） | 低 | `model: auto` / 該当なし | inherit（effort は当面継承） | medium/low | 判断は GATE_STATUS の機械読取が主。1 コマンド $1.5〜2.1（calls[0] 実測）。low で progress.md を読まずに答える副作用に注意 |
| release / harness-maintainer | 高 | 無指定 | inherit＋high | — | 不可逆操作は ask ガードが担う。節約する場面ではない |

**どのモデルか**: メイン会話は利用者選択（公式既定は Opus 5。Fable 5.1 は「Opus 5 の xhigh/max で evals が届かない時」の昇格先）。**task-worker の軽量候補は Sonnet 5 一択**（$2/$10 恒久、cache read $0.20）。Haiku 4.5 は除外（effort 非対応・tool search 400・200K 文脈・advisor 不可・retirement 2026-10-15 以降）。opusplan・`max` 既定・セッション途中のモデル/effort 切替（Opus 5 ではキャッシュ全損。Fable 5.1 のみ 2.1.260 以降 effort 変更でキャッシュ維持）は避ける。Arm D（task-worker Sonnet 5＋advisor Opus）は Anthropic API 限定でセッション全体に advisor が付くため別枠。

### 4.3 A/B 手順（何を固定し、何を変え、何をもって採用するか）

- **前提（装置）**: 開発機を Claude Code ≥ 2.1.265 に更新（2.1.201 では Fable 5.1 が 400 で拒否・FORCE 不可・PreModelSwitch 不可）。e2e-run.py に `--effort`・`--subagent-model-force`・結果 JSON への model/effort/claude_version/modelUsage/costBasis 保存・走行後の effort-log 回収（§6）。effort-report.py の単価訂正。**CI 緑化と C-1 封鎖が先**（機械強制を「保証」と書くなら）。
- **固定**: 課題（expense-webapp＋ChronoLines 26 タスクの再現集合）、memo、ハーネスコミット、`--model claude-opus-5`（プラン既定差を排除。Fable 5.1 は別系列）、`--effort high`（メイン）、`--fresh-on-command`、発話列、予算（アーム共通）、`switchModelsOnFlag: false`、**`CLAUDE_CODE_EFFORT_LEVEL` / `CLAUDE_CODE_SUBAGENT_MODEL` を unset**（残っていると effort アームが Arm 0 と同一になる）、`CLAUDE_CONFIG_DIR` でユーザースコープを隔離。
- **アームの実装は装置側で**: 保護ファイル `.claude/agents/task-worker.md` を手編集せず、e2e-run が展開した使い捨て workdir のアダプタだけを `--agent-frontmatter task-worker:effort=medium` 型で patch し結果 JSON に記録（generate-adapters の再生成で消える・保守モードが要る・1 アーム 1 変数が機械保証、の 3 問題を同時に解く）。
- **アーム順序と停止規則**: Arm 0（基準）→ Arm C（solo。公式が「必ず含めよ」とするベースライン。implement が委譲せず自分で実装＝D034 の例外として計測専用）→ A1（medium）→ A3（lead を medium。skills 移行前は `--effort` で与える）→ A2（low＋再走）→ B1（Sonnet 5 high）。A1 で非劣性が崩れた時点で A2/B1 を打ち切る。
- **従属変数**: check.py pass・golden-eval 整合率・**固定検体（D071 の仕込み 5 件）に対する reviewer 検出率**・差し戻し回数・escalate 回数・Read 呼出数・既存ファイルへの Write 全置換回数・ピーク文脈・num_turns・**cost/accepted task**（訂正単価。coding 費と gate 費を分離）・resolvedModel 一致率。
- **成功基準**: 先に非劣性（pass^k 同等・golden-eval 100%・reviewer NG/escalate 非悪化）、次にコスト差を効果量と信頼区間付きで報告。点推定の「20% 安い」だけで採否を決めない（既存走行の run-to-run 変動は expense $69.06 vs $49.10、todo-cli CoV 0.61）。B1 は「A 案最良アームの cost/accepted task を下回り差し戻しが増えない」場合のみ。
- **費用**: Arm 0/C/A1/A3/A2/B1 × k=3 × n≥2 ＝ 36 走行以上、1 走 $49〜69 実績（Fable 5。Opus 5 固定なら概ね半額）→ 数百〜千数百 USD。

### 4.4 Copilot での扱い（制約の反転）

| 項目 | Claude Code | Copilot VS Code | Copilot CLI | Copilot cloud |
|---|---|---|---|---|
| model 指定 | `.claude/agents` frontmatter（alias/フル ID/inherit） | `.agent.md` 表示名または配列。`auto` は未文書化 | 同左＋配列（1.0.83）＋`model-policy: required`。`.github/copilot/settings.json` の `subagents.agents.<name>.model`（単一文字列） | 文字列 1 本。有効性は未保証（G-2 に反証なし） |
| ティア制約 | なし（availableModels 除外時は無言で inherited） | 親≧子。超過は起動拒否 | 親≧子。無言降格（#2758） | — |
| effort（役割別） | frontmatter `effort:` | **不可** | `subagents.agents.<name>.effortLevel`（low/medium/high/xhigh） | 不可 |
| 一括強制 | `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` | なし | settings.json で親モデルをピン（trusted repo） | 管理者ポリシー |
| 実行モデルの証拠 | PostToolUse(Agent).resolvedModel／`-p` modelUsage／`/tasks` | 応答フッタのホバー（手動） | `--usage-output-file` per-agent（1.0.81、docs 未記載） | 未提供 |
| 設定の優先順位 | 呼出時 > frontmatter > env > メイン | 引数 > frontmatter > 親 | 既定 < MDM < user < **repo settings.json** < settings.local < env < flags（公式） | — |

→ Copilot では「判定役だけ強いモデル」ではなく、**フェーズのメインエージェント（requirements/design/test/change）を強いモデルで起動し task-worker を下位ティアに**置く。reviewer/spec-critic は親と同ティア（inherit）を明記し、「モデル不可の報告を受けたら配列の次候補で再試行、全滅なら『レビュー未実施』として done を失敗させる」を本文化。`model: auto` は配列表記に替える前に「auto が実際に無視されるか」「親 Opus 5/子 Sonnet 5 が通り逆が拒否/降格されるか」を COPILOT-E2E で実測し、D007 の「Auto 割引 10%」喪失分をコスト式に入れる。cloud agent の model は生成物から外し SOP に格下げ。

### 4.5 方針の単一ソースと機械強制

- **正**: `.github/harness/model-policy.yml`（`docs/` は sync 対象外なので不可）。1 モデル＝1 エントリ（claude_code id/alias・copilot 表示名・CLI id・単価 5 列・プラン可用性・cloud 可否・`deprecated_on`・`context_window`・`effort_supported`）＋役割ごとの {claude_code: model/effort/maxTurns/background/memory, copilot_vscode: model 配列, copilot_cli: model/effortLevel/contextTier/model_policy, plan_fallbacks, never_low, ab_evidence}。既定は全役割 `inherit`／判定役 `effort: high`＋`never_low`／それ以外 `effort: inherit`。A/B 前は挙動不変。
- **生成物**（generate-adapters.py 第 4 節。**通常モードで人間が実行**。保守モードは `--off` で settings.json を丸ごと復元するため生成結果が消える）: `.claude/agents/<role>.md` の model/effort/maxTurns/background、`.github/agents/<role>.agent.md` の `model:` 行のみ（`# generated-from` マーカー、`auto` 除去）、`.github/copilot/settings.json`、`.claude/settings.json` の `Agent(model:…)` deny 群、phase_overrides は `.claude/commands/<cmd>.md`（skills ではない。Claude Code 側だけに分岐）、PLATFORM.md/agents.html/README の対照表（既存規約 `<!-- BEGIN GENERATED: model-policy-* -->`。gen-docs.py は配布除外なので配布される generate-adapters 側に置く）。
- **強制の三層**（Claude Code）: (1) `permissions.deny` の `Agent(model:haiku)`・`Agent(model:claude-haiku*)`——**呼出時パラメータの literal 一致のみ**で frontmatter/inherit には効かないことを明記。(2) PreToolUse(matcher `Agent|Task`) `guard-subagent-model.{sh,ps1}`: 役割表と照合し不一致は deny、省略時は `updatedInput` で **tool_input 全体を複製して model だけ差し替え**。既定が inherit の役割は素通し。役割表が読めない／非 Agent ツール／subagent_type 欠落は無出力 exit 0（fail-open。CI windows の固定ペイロードにも耐える）。(3) PostToolUse(Agent) `record-subagent-model`: resolvedModel/modelsUsed/status を `.github/hooks/logs/subagent-models.jsonl`（デバッグ用）と `docs/00-overview/subagent-models.csv`（証拠用。effort-log.csv と同形式・同 progress.md ゲート）へ。background 役割は SubagentStop でも記録。**Stop/SubagentStop での block・再実行要求は使わない**（fail-open 規律と矛盾し無限ループの温床）。done 遷移時の照合は gate-check スキル（指示層）ではなく warn-gate-tamper の拡張（機械層）で warn。記録が無いホストでは毎回警告せず tasks.md の証拠 3 点セットに「実行モデル（出典）」欄を要求。
- **validate 検査**（最初は WARN、CI 連続緑 1 サイクル後に ERROR）: 許容値集合・`model: auto` の全文 grep（frontmatter だけでなく implement.agent.md:145／test.agent.md:73／USAGE.md:447／overview.html:355 の本文も）・プラン行列・廃止 30 日前・親≧子ティア（仮説と明記）・Claude 専用フィールドの Copilot 混入・min_version と PLATFORM 表の一致（docs 版と実機検証版を分ける）・役割集合の一致・`retrieved` 鮮度・`.claude/agents` の実体 3 本と policy.roles の対応（8 役割は「生成先なし」と明示）。
- **保護・配布**: `.github/harness/model-policy.yml` と `.github/copilot/**` を guard sh/ps1・deny・CROSS_ITEMS・CODEOWNERS・SYNC_GLOBS に同一コミットで追加。model-policy.yml は REVIEW_FILES（プロジェクト固有の plan_fallbacks/pin を守る）。`.claude/settings.json` は REVIEW_FILES のため deny 差分は配布先で衝突する→/91 の手順に明記。
- **台帳と文書**: D007 と D040 決定5 を新 D 番号で supersede（価格 $2/$10・cache read 0.025x、解決順 v2.1.251、Copilot ティア制約、Auto 割引喪失）。harness-guide:53/61・README:397・USAGE:447・agents.html ピル×5・overview.html:355・COMPARISON.md:363 を同一コミットで。AGENTS.md（24,039B、25KB WARN 直下）には足さず PLATFORM.md へ。GSD v1.11.0 の先例（skill frontmatter effort の停止）を記録し、対話運用では effort を frontmatter に常設せず「セッション冒頭で model/effort を固定」を USAGE に。effort-report.py の PRICES は policy（JSON 双子）を読み、既記録の生 ID（claude-sonnet-4-6/claude-opus-4-8/claude-fable-5）は prefix 族フォールバックで解決（無音欠落防止）。
- **導入順**: ①CI 緑化＋C-1 封鎖＋D072 決着 → ②policy.yml＋generator（既定 inherit/high、auto 除去）→ ③validate（WARN）→ ④保護 5 面（Copilot 取込の実機確認を先に: `.claude/agents` に Claude 専用キーを持たせた状態で VS Code/CLI の重複・警告を計測。必要なら `.vscode/settings.json` に `chat.agentFilesLocations: {".claude/agents": false}`）→ ⑤record-subagent-model 配線 → ⑥MP-8 実機実験（Copilot CLI 導入後）→ ⑦A/B → ⑧ab_evidence を埋めて初めて model/effort を変える。概算 4〜6 人日＋実験 1〜2 人日。

---

## 5. 会話ごとの受領書（依頼③への回答）

### 5.1 取れる情報と取れない情報（一次情報で確定）

| 情報 | 経路 | 信頼度・制約 |
|---|---|---|
| セッション累計の推定コスト（USD、list 価格）、追加/削除行数、文脈使用率、モデル、版、effort | **statusline の stdin JSON**（`cost.total_cost_usd`・`cost.total_lines_*`・`context_window.used_percentage`・`model`・`version`・`effort.level`。2.1.251 以降 `prompt_cache`） | 公式。対話モードの唯一のコスト経路。**hooks 入力にコストは来ない**（#50883 not planned）。300ms デバウンス・実行中はキャンセル・ワークスペース信頼が前提。`context_window.total_*`/`current_usage` は**累計ではない**（直近呼出/現在の文脈窓）。`cost.total_cost_usd` はホスト版の単価表で計算される（2.1.201 は Sonnet 5 を $3/$15 で計上＝1.5 倍）。/clear でリセット。 |
| 累計トークン（4 種）・main/subagent 別・thinking | **transcript JSONL**（非公式）。`subagents/agent-<id>.meta.json` の agentType/spawnDepth/toolUseId、各エントリの attributionSkill/effort/promptId/version/entrypoint | 形式保証なし。`/usage` との校正が未実施（TM-10）。`input_tokens` はキャッシュ境界後の残余で正しい値（RC-1 反証）。Stop 時点では最終メッセージを含まないことがある（公式）。 |
| 実行モデル | PostToolUse(matcher Agent) の `tool_response.resolvedModel`（2.1.174〜）/`modelsUsed`（2.1.212〜） | background 起動時は `async_launched` で resolvedModel のみ。 |
| 非対話 | `-p --output-format json` の result（`total_cost_usd`・`modelUsage`・`costBasis`（2.1.246〜）・`subtype`・`permission_denials`） | 公式。予算打切り call では `usage` が全 0（modelUsage を使う）。 |
| Copilot CLI | `~/.copilot/session-state/<id>/events.jsonl` の `session.shutdown`（modelMetrics/codeChanges/totalNanoAiu）、`--usage-output-file`（per-agent、1.0.81） | 非公式契約（#3551）。/new で欠落（#3994）。sessionEnd フック発火時点で書かれている順序は未確認。 |
| Copilot VS Code | 応答フッタのホバー（手動）、OTel file exporter（`github.copilot.chat.otel.*`） | ワークスペース設定で効くかは未確認。hooks 入力に usage 無し。 |
| Copilot cloud | Billing usage API（2 日遅延） | フックの書込は破棄され GitHub/Copilot ホスト以外へ到達不可。 |

### 5.2 受領書の項目（出所タグ付き。モデルの自己申告は使わない）

```
受領書 [06-implement-task] sess a1b2c3 · claude-fable-5-1 · v2.1.201 · 採取: Stop(暫定)
費用: host推定 $8.42 [公式・list価格・請求額ではない] · 文脈 62% [公式] · cache命中 n/a(2.1.251+)
トークン [transcript・解析率 100%]: in 12k / out 38k / cache read 9.6M / write 210k（main 3.1M・task-worker×3 6.7M）
委譲: task-worker×3 · reviewer×0 ⚠ · 要求 inherit→実行 claude-fable-5-1 [PostToolUse]
成果: implementation in_progress→done · tasks [x] +4 · +412/-38 行 [公式] · commit 2 · tests: pytest 3回・最終 28 passed [check.py]
基準線: 同工程 中央値 $60.7 / p90 $121 (n=3・暫定) → 範囲内
```

- 識別: session_id・platform・日付・工程（transcript の attributionSkill 優先、無ければ先頭コマンド）・実行モデル・ホスト版・ハーネスコミット。
- 費用・文脈: statusline 由来のみ（累計 $・文脈%・行数）。トークン累計は transcript。両者の乖離 >10% で `price_table_stale` フラグ。
- 委譲: meta.json の agentType（正規表現は最終フォールバック）・要求→実行モデル・spawn 深さ。
- 成果（量＝代理指標と明記）: GATE_STATUS 遷移（**SessionStart の source=startup/clear/resume でスナップショット。compact では書かない**、存在すれば上書きしない）、tasks [x] 増分、変更ファイル数・行数（statusline の `total_lines_*` を主。`git diff --shortstat` は他ソースの未コミット変更を自セッションに帰属させるため検算限定＝remind-record.py の教訓）、コミット数。
- 成果（整合）: テスト実行回数と最終結果（**pass を主張できるのは check.py/golden-eval の決定論検証のみ**。PostToolUse(Bash) 抽出は `ran=true/result=unknown` 止まり。main と subagents の両方を走査しないと大きく過小）、golden-eval declared/passed、reviewer 起動数と **review_less_done**（同一セッションではなく同フェーズ累計で判定。fast-track 注記があれば spec-critic 省略を正常扱い）、差し戻し（done→in_progress 逆遷移）、フック deny/ask（hook-decisions の JSONL 化後）。
- 比較: baselines.json の同工程 p50/p90（n<5 は「暫定」）。文脈 ≥ 閾値または p90 超過時のみ末尾 1 行「次のゲートで新セッションを推奨（D057: 分割で −29% の実測）」。
- 取らないもの: プロンプト本文・tool 出力本文・ファイル内容・commit 本文・秘密値・開発者識別子・OTel RAW API bodies。

### 5.3 実装場所（2.1.201 で今日動く順に）

1. **statusline（表示＋永続化の二役）**: `.github/hooks/scripts/statusline.py`（または .sh）1 本を正とし `.claude/settings.json` の `statusLine.command` から参照（`.claude/` はポインタのみ、振る舞いは `.github/hooks/scripts/`）。**ps1 鏡は作らない**（公式: Windows では Git Bash 経由で実行。本ハーネスは Git Bash 必須）。処理は「生 stdin を temp→rename で `.github/hooks/logs/usage/<sid>.status.json` に保存 → 表示（工程｜GATE 要約｜$｜文脈%）」の順。制約は「50ms」ではなく「追加プロセス起動ゼロ（git/jq 禁止）・書込 1 回・失敗時は前回表示を出す」。gen-docs の `HOOK_HELPER_SCRIPTS` に追加しないとフック数が 14→15 に化ける。**プロジェクト設定への statusLine 配布は opt-in**（利用者自身の ~/.claude の statusline を置き換えるため。既存があれば出力を先頭に連結するラッパを提示）。
2. **受領書レンダラ**: 正は `tools/session-receipt.py`（単一モジュール）。`log-effort.py` は薄い呼び出し側（Stop で受領書ドラフト `<sid>.receipt.draft.json` を書き、トリガ条件を満たすときだけ `{"systemMessage": …}` を出力。**block は決してしない**。3 行以内・絵文字なし。SessionEnd（1.5s 既定猶予・timeout 明示）は draft→確定の rename と 1 行追記のみ）。トリガ: GATE_STATUS 変化／フェーズコマンド起動／Δ費用 ≥ $2 or Δトークン ≥ 500k／10 ターンごと／文脈 ≥ 閾値。Stop 3 本は並列実行なので remind-record の block と同時表示され得る→重複前提で 1 行に圧縮し、判定ロジックは複製しない（マーカー存在時のみ次 Stop に持ち越す）。`stop_reason` は Stop 入力に存在しないため「区切り」は `last_assistant_message` 非空かつ background_tasks 空かつ GATE 遷移/コマンド起動で判定。
3. **/99-status の費用欄**: `.github/prompts/99-status.prompt.md` のみに追記（`.claude/commands/99-status.md` と `.agents/workflows/99-status.md` は生成ポインタ。手書きすると次回再生成で消える）。前回 H-6（progress.md 無条件作成）を同時に修正。
4. **harness-stats スキル**（user-invocable・disable-model-invocation）で任意時点表示。generate-adapters の skill 生成は name/description しか通さないため frontmatter パススルーの拡張が要る。
5. **-p/CI**: e2e-run.py が result の modelUsage/costBasis/subtype/permission_denials/claude --version を保存（§6）。`--output-format stream-json` の SDKInformationalMessage で同じ受領書を拾える。
6. **SubagentStop / PostToolUse(Agent)**（第 2 段）: `log-subagent.py` が agent_type/agent_id/effort/resolvedModel を `<sid>.subagents.jsonl` へ。matcher は `Agent|Task`、tool_name 自己フィルタ必須（Copilot CLI が `.claude/settings.json` を重複排除なしで読むため）。
7. **Copilot（第 3 段・劣化モード）**: CLI は `sessionEnd`（PascalCase `SessionEnd`＝payload が session_id）を **CLI 用の別生成物**（`version:1`＋bash/powershell 形式。現行 gate-hooks.json は VS Code 形式で CLI が `windows` キーを読む保証なし）に配線し、順序未保証のため**次回 sessionStart で前回 sessionId の session.shutdown を後追い取込**。VS Code は成果層のみ（トークンは「セッション情報ポップオーバー転記」を**人間向け**チェックリストに置く。gate-check スキルには入れない＝エージェントには履行不能）。cloud は release スキルの手順でエージェント自身が集計ブロックを PR 本文に書く（プロンプト本文は載せない）。COPILOT-E2E に「CLI が gate-hooks.json から sessionEnd を発火するか」「発火時点で session.shutdown が存在するか」「VS Code が未知イベントキーを許容するか」を実機項目として追加してから配線。

### 5.4 プライバシー境界と一次データの置き場（RC-4 の解消を含む）

| 分類 | 欄 | 置き場 |
|---|---|---|
| committed（docs/） | 工程×役割×モデルのトークン合計、推定 $（basis・単価表日付）、行数、tests、gate 遷移数、tasks_done、spawn 回数比、rework、週単位日付 | `docs/06-retrospective/effort-report.md`（集計のみ。月次スナップショットを追記し、ローテーションで消えたセッションは前月から合算） |
| local（`.github/hooks/logs/`＝gitignore 済） | session_id、prompt_id、transcript_path、ホスト版、per-session 受領書、statusline 永続 JSON、hook 判定 JSONL、learnings 候補 | `logs/usage/`（90 日ローテーション） |
| never | プロンプト本文、tool 出力本文、ファイル内容、commit 本文、秘密値、git author/email、OTel RAW bodies、Copilot captureContent | どこにも書かない |

- **effort-log.csv は廃止し logs/usage を唯一の一次データにする**（docs/ に残すと正が二重化し、effort-report が logs を読む段階で誰も読まない死蔵ファイルになる）。移行スクリプト `effort-report.py --migrate` で既存 CSV（ChronoLines 13 行等。transcript は 30 日で消えるため再導出不能）を取り込む。sync-harness は `.gitignore` と `.claude/settings.json` を REVIEW_FILES 扱いにするため 3 プロジェクトは手動マージ→/91 の手順に明記。PLATFORM.md:216-225・hooks/README.md 表・harness-guide:56・COMPARISON.md:149・overview.html:330・retrospective_template:30-44 の 6 面を同一コミットで更新し、validate に「docs/ 配下の effort-log.csv 言及は error」を追加。
- redaction/欄分類の正は 1 ファイル（`_privacy-patterns.json`）を sh/ps1/py が読む形にし、「3 系統のバイト同一」は要求しない（方言差で常時 FAIL になる。既存 CROSS_ITEMS 方式の意味照合＋canary secret 回帰で強制）。hook-decisions.log の JSONL 化と session_id 列追加は writer 19〜20 ファイルの同時改修になるため、hook_log を `_paths.sh` 同様の共通関数に寄せてから（第 1 弾から外す）。

### 5.5 「妥当なトークン量」の分析ループ

- **実運用の基準線（RD-5。11 セッション、Fable 5 定価）**: 1 セッション中央値 **$9.65**・p90 **$60.71**・最大 $121.13。工程別 06 実装 [$40.8, $60.7, $121.1]、03 設計 [$19.1, $26.1]、01 要件 [$9.5, $9.7]。1 日 $103〜162（公式の企業平均 $13/開発者/日の 8〜12 倍）。実アプリ 1 本（ChronoLines 26 タスク・581 テスト）$265＝**$7.73/タスク**（task-worker）＋$0.83（コーディネータ按分）。コスト構成 cache_read 46% / output 28% / cache_write 25%。task-worker のピーク文脈は 26 中 11 が 150k 超（最大 220k）、出力の 47% が thinking。サブエージェントのキャッシュ書込は全て 5 分 TTL で毎 spawn 再書込（cache_w5m 3.69M＝$46、task-worker コストの 23%。RD-9）。
- **閾値の意味論**: (1) n<20 の間は絶対値ではなく「同工程の暫定基準線 p90 超」と「文脈使用率」の 2 系統で**警告のみ**（$ は表示のみ。単価が 1.5〜4 倍過大な現状の表で絶対値判定すると常時鳴って無視される）。(2) n≥20 で同プロジェクトの p90/中央値×2 に切替（effort-report が baselines.json を生成）。(3) hard stop はフックで一切行わない。閾値超過時は gate-check の done 承認で「受領書添付と理由 1 行」を求める（承認ゲート化）。(4) **文脈閾値は 1 か所に**: USAGE.md のセッション分割表（約 40% で想起劣化＋4 条件）と設計の 70% が別基準で存在するため、`tools/usage-config.json` に集約し USAGE/受領書/振り返りの文言を gen-docs で生成。
- **校正を前提条件に**: 配布前に 5 セッション以上で `/usage` のモデル別トークン・コストと transcript 集計×prices.json の差を記録し、相対誤差（目標 ±5%）と解析率（≥95%）を DECISIONS に載せる。
- **還流**: effort-report に「セッション×工程の分布表（n/p50/p90/max）」「成果あたり費用（1 ゲート遷移／1 タスク／1 CR）」「モデル×effort 別」「逸脱一覧」を追加し、retrospective_template §3 を「effort-report から転記」に統一。逸脱セッションは draft-learnings と同様式で learnings-pending に候補行を自動起草。A/B（§6）の結果 JSON も同じ受領書スキーマに揃える。外部アンカー（Sonar 800 行 PR ≈ $41、Anthropic $13/日、Jellyfish p90＝中央値×13）は目標ではなく参考として併記。
- **段階導入と各段階で測る指標**: 第 1 段（statusline 永続化＋受領書：host $・文脈%・トークン・GATE 遷移・解析率＋selftest）→ statusline 書込成功率と p95 実行時間（Windows Git Bash）・transcript vs /usage の校正誤差・Stop 時点で transcript 末尾が last_assistant_message と一致した割合・受領書フィクスチャ 6 種（statusline 無/resume/clear/サブエージェントのみテスト/block ターン/-p）の selftest。第 2 段（SubagentStop/PostToolUse(Agent)・SessionStart スナップショット・テスト抽出）。第 3 段（baselines.json・逸脱の自動起草・Copilot 劣化モード）。概算 3〜4 セッション、変更対象 45〜55 ファイル（うち約 20 が鏡面）。CI 緑化が前提。

---

## 6. 評価やり直しの手順（A3-1 の再定義）

- **用語**: `verdict ∈ {PASS, FAIL, DNF_BUDGET_TOTAL, DNF_BUDGET_CALL, DNF_QUOTA, DNF_TIMEOUT, DNF_ERROR, DNF_TURNS, INVALID_APPARATUS}`＋`valid_for_comparison`＋`invalid_reasons[]`。判定は `is_error`＋`api_error_status`＋`errors[]`＋result 文言の複合（`subtype` 単独は信頼できない: 2.1.201 で Fable 5.1 が 400 なのに `subtype: success`）。合計超過は `cost > total_budget`（abort フラグ非依存）。
- **能力実験と効率実験の分離**: 能力＝予算が拘束にならない条件（call 上限を `min(call_budget, total_budget − spent)` で動的付与し合計が上限を超えない。妥当性: DNF/INVALID 無し・打切り 0・budget_used_ratio ≤ 0.7・budget_enforcement=subagent-inclusive・主モデル一致かつ補助モデル費用比 <1%・condition_hash 単一）。効率＝同額の事前登録ラダー（expense $12/$24/$48、todo-cli $4/$12）で DNF 率が成果。事前登録は `evaluation/experiments/<series>.json` にハッシュ化し、系列開始より前の変更日時を eval-report が検査。
- **固定条件 C**: `--model` フル ID 必須・`--effort` 必須・**CLI ≥ 2.1.251（Fable 5.1 対応。推奨 2.1.257 で FORCE）**・**ユーザースコープ隔離は `CLAUDE_CONFIG_DIR`（資格情報のみ複製）＋`--strict-mcp-config`**（`--setting-sources project,local` は skills/plugins/MCP を遮断しない。過去 10 走行はユーザースキル `ai-video-publish` が混入＝既に汚染）・`--settings <series>.json` で `switchModelsOnFlag: false`・各 call を stream-json で起動し `system/init` の skills/agents/mcp_servers/version を保存して condition_hash に含める・`conditions.auth_mode ∈ {api_key, oauth}`（oauth は「定価換算」と表示、Fable 5.1 は usage credits を同意なしに消費し 0822 の DNF_QUOTA が再発しうる）・2.1.239 以降の 1.1× データ常駐プレミアムの有無を記録。
- **対称性**: 督促（wait_for_state の nudge）は `kind: nudge` として calls[] に記録し予算に算入、bare にも同数の継続発話。`# expect:` は経路（fast-track/フル）別に定義するか到達状態のみ。permission_denials は全アーム共通に記録し、INVALID 化は共有 allowedTools 外の要求で verdict 未到達の場合のみ。
- **装置**: 走行 workdir を Temp 外（`evaluation/runs/<series>/`、gitignore）に置き、終了時に `git bundle`＋check.py 出力＋GATE 最終値を退避して「再検証可能性」を valid の条件に加える。**過去 10 走行の workdir は既に消失**（9/4・9/6 の自動クリーンアップ）＝再検証不能・参考値として表から外す。transcript（20MB）は**今すぐ** zip＋sha256 で退避（8/22 分は 9/21 に消える。`_`→`-` 置換の encoded cwd ではなく session_id で探索）。artifact_stats は git-diff スコープ・全言語。arm spec は JSON/TOML（CI に PyYAML が無い）。`--continue` 廃止（selftest の該当ケースも書換）。系列実行器 `--series-plan` で ABAB・n 本・ラダーを自動採番。
- **check.py**: `contradiction_resolved` を「同一文内に決定動詞＋単一の締め日」へ強化し、`NEEDS CLARIFICATION`/未定/TBD/「or・または」併記を負例に。偽陽性 3 種＋未解決併記を selftest に追加し FP/FN=0 を CI で確認してから系列開始（設計の「check.py 変更不要」を撤回）。
- **統計**: n=5/アーム（完全分離で Fisher 両側 p=0.008、4/5 vs 0/5 で p=0.048、3/5 vs 0/5 は差を主張しない）。pass^5 の Wilson 下限 0.57。分離しなければ n=8 まで拡張（p≈0.0002）。DNF 除外で有効 n<5 の補充規則も事前登録。費用は**比を作らず範囲で報告**（PASS 費用÷FAIL 費用の比は意味を持たない。COMPARISON.md:114「約7.7倍」・:315「約14倍」、D056「4.9倍」、README「46%減/29%減」を範囲表記へ）。
- **backfill**: 既存 10 JSON に verdict/valid/invalid_reasons（`cli<2.1.217`・`user_scope_unisolated`・`budget_enforcement=main-only`）を付与。**todo-cli-harness-0822-191211 は DNF_BUDGET_TOTAL**（README.md/README.en.md/COMPARISON.md/COMPARISON.html/overview.html の 5 面が「本ハーネス（完走）PASS $45.47」を掲示中）、0827 harness は $69.06 > $60 で能力・効率いずれでも比較可能な PASS ではない。5 面の更新と「n≥6」表記（README:440・overview.html:521・COMPARISON.html:491・A3-1）の n=5 への改訂を同一コミットで。evaluation/README の記録方針を「除外は装置欠陥または事前登録条件のどちらか。results/ は削除しない」に改訂。
- **外部アーム**: Spec Kit は `uvx --from git+https://github.com/github/spec-kit.git@v1.0.5 specify init --here --integration claude --non-interactive --force`（`--ai` は v0.10.0 で削除）、`allowed_tools_extra: ["Bash(bash .specify/*)"]` を**全アームに適用**して条件 C を保つ。OMC は `--plugin-dir <pinned checkout>`（FORCE で tier が無効化されることを明記）。Copilot ランナーは開発機に CLI が無く検証不能→別提案に切り出す。
- **順序と費用**: ①transcript 退避と backfill → ②e2e-run v2＋eval-report＋check.py 強化 → selftest 緑 → CI 追加 → ③開発機 2.1.265 化・パイロット 1+1 走行（B_cap を実測から決める）→ ④S1 expense 能力（Opus 5 high、n=5、ABAB）→ ⑤S2 効率ラダー → ⑥S3/S4 todo-cli → ⑦S5 Spec Kit → ⑧REPORT.md を生成し COMPARISON の実測表は生成物からのみ（A5-7 と統合、gen-docs に `comparison-measured` ブロック）。費用 $900〜1,300＋パイロット $100＋再走行 $300〜450/回、実時間は harness 1 本 65〜78 分で逐次 35〜40 機械時間。

---

## 7. 未検証の low/info（32 件。次サイクルで検証）

MS-8 orchestrator は lead ではない／MS-10 キャッシュ分断の公式注意はセッション途中の切替と opusplan・fork のみ／MS-11 Haiku 4.5 除外／MS-12 Copilot `model: auto` の割引適用は未検証／TM-8 hook-decisions に session_id 無し／TM-9 Stop は並列実行／TM-12 計測データが docs 索引に無い／TM-13 「effort-log」が公式の effort と衝突／CC-3 「Task ツール」表記が残存（実機は `Agent`。CLAUDE.md・PLATFORM.md・生成器・18 コマンド）／CC-5 TaskCompleted は既定モデルで発火しない／CC-12 subagent の新フィールド（maxTurns/permissionMode/background/memory/isolation/cacheTtl）と spawn 抑制 env 未使用／CC-13 `/import` 使用禁止の規範なし／CP-8 Copilot CLI の組込 Critic・code review・Agent Merge との役割分担未評価／CP-9 Copilot 側のホスト版カナリア無し／PF-8 旧モデル向けヘッジと 2 ホップ・アダプタ／PF-9 指示層に逸話 73 箇所／PF-11 reviewer/spec-critic に証拠拘束文が無い（低 effort で file:line 捏造）／PF-12 CLAUDE.md 39 行の半分が仕組みの解説／PF-13 「削除→失敗したものだけ復帰」の儀式未接続／EV-10 n と k の統計根拠（n=2 の 2/2 vs 0/2 は p=0.33）／EV-11 トラジェクトリ退避（期限付き）／OP-5 4 版すべてで tag 欠落・CHANGELOG 古い／RD-9 サブエージェントの 5 分 TTL 再書込／RD-10 08-12 の 5 セッションは 09-11 前後に消失／RG-15 cp932 で文字化けする 3 本／RG-16 自己検査ベースライン（jq 経路未検証）／RC-7 SessionEnd 1.5 秒予算の責務分割規範／RC-11 共通ライブラリの置き場未定義／RC-13 statusline は 2.1.201 で既に使える／RC-14 8/12 の transcript は 2.1.228 なのに現在 2.1.201＝ホスト版の後退を誰も検知していない／MP-8 Copilot CLI ティア実験の装置無し／MP-9 単一ソース→生成は業界標準形（Codex/Gemini/Cursor/OpenCode が per-agent model/effort を宣言的に持つ）。

---

## 8. 優先順位付きロードマップ（前回 §10 と codex P0 を統合）

**P0（今週。前回 P0 は未着手のまま）**
1. `git add audits/independent-harness-audit-2026-08-31-codex.md` をコミット。`_paths.sh` は配線＋D072 記録かつ selftest、または削除を即決（RG-14/RG-4）。
2. PROPOSALS に採番規則を明文化し、前回再監査・codex・本監査の全指摘を登録（付記）。「applied は D 番号＋検証コマンド＋CI 緑 run URL」を規則化。A2-4/A3-4/A3-5 を partial に戻す（OP-1/RG-13）。
3. **CI 緑化**: requirements-dev.txt か frontmatter 解析の正規表現化。sh/ps1 の 8.3 短縮名正規化（RG-7）で windows の 2 FAIL を消す。緑 run URL を DECISIONS に記録（RG-2）。
4. **C-1 封鎖**: allowlist 反転＋ConfigChange 配線＋34 ベクタ selftest（RG-1）。同時に `.claude/rules/**`・`.github/copilot/**`・SC-6 の常駐ファイル群を保護 4 面へ、skills 配下の権限系 frontmatter/hooks.json 書込を ask（SC-1/SC-2）。
5. gen-docs.py:97,99 のリテラル「全PASS」「2環境で実機検証済み」を除去し、8 面の手書き数値を生成物化またはニュートラル表記へ。COMPARISON.md:263 の golden-eval 件数を訂正（RG-12。監査者の自己修正）。
6. `~/.claude/projects/` の e2e-* 12 ディレクトリを zip＋sha256 で退避（9/21 期限。EV-2/EV-11）。
7. 単価表を `tools/prices.json` に外出しして訂正（Sonnet 5 $2/$10、Fable 5.1 cache read 0.025x、5.1 を 5 より先に前方一致）、D040 決定5 の単価・解決順を訂正（TM-1/MS-2）。
8. 3 プロジェクトの effort-log.csv を logs/usage へ移行し `.gitignore`（RC-4）。
9. 開発機を Claude Code 2.1.265 系に更新し selftest を再実行。PLATFORM 最低版表を更新（CC-1）。
10. codex P0（48h 項目）: リリース自動デプロイの無効化、release の planner/reviewer/executor 分離、draft-learnings の生ユーザーテキスト保存停止、playwright MCP のピン、PyYAML ロック＋CI 緑（IA-20260831-xx）。本監査は codex の C-01/C-02 を独立再検証していない（重複した H-01 プライバシー・`chat.hookFilesLocations`・H-10 台帳のみ確認）。次サイクルで再現すること。

**P1（2〜4 週間）**
- 役割別モデル方針: model-policy.yml＋生成器＋validate（WARN）＋保護 5 面＋三層強制＋D007/D040 の supersede（§4.5）。
- 受領書 第 1〜2 段（§5.3）と基準線の校正、`/99` の H-6 修正、hook_log の共通関数化。
- 評価装置 v2（§6）と backfill、5 面の「完走 PASS $45.47」訂正、check.py 強化。
- **reviewer の実施を done 条件へ**（/06 の 10 タスクごとのチェックポイント、warn-gate-tamper で「レビュー無し done」を警告、受領書に review_ratio）（RD-2/RC-10）。
- 3 プロジェクトへ /91 --apply（send:true・HH:MM・draft-learnings・harness-origin.md）（RD-4）。
- Copilot: `.vscode/settings.json` に `chat.hookFilesLocations`/`chat.useClaudeMdFile: false`、deny 型ガード 2 本に読取フィルタ（CP-1/CP-3/SC-5）、prompts→user-invocable skills 移行の方針決定（CP-2。generate-adapters/validate/guard/deny/sync/CLAUDE.md の 6 面を触る別案件）。
- Stop フック 3 本を `last_assistant_message` 優先へ（CC-14）。hooks の `if` で bash 起動を削減（CC-6）。StopFailure/InstructionsLoaded 配線（CC-9）。
- 指示層の棚卸し（PF-1〜7/10）: フックで機械強制済みの禁止事項の削除、規範の正を 1 箇所に、公式 autonomy/scope ブロックへの置換、陳腐化記述の修正。AGENTS.md は減量方向のみ。

**P2（1〜2 か月）**
- A/B 系列 S1〜S5（§6）と effort sweep（§4.3）。結果を REPORT.md → COMPARISON 生成。
- Copilot CLI 導入と MP-8 実機実験（ティア順序・settings.json の優先順位・`auto` の実挙動・二重発火とレイテンシ A2-7）。Copilot 側の受領書（第 3 段）。
- Agent Plugins 1.0 ＋ `.claude-plugin` を同一ソースから生成（CP-7/A2-2）。CI に `claude --init-only`・`plugin validate --strict`（RG-17）。
- doctor（A3-3: ホスト版・依存・配線・配布鮮度の pass/fail）と改善サイクルの計測（OP-4）。
- 常駐指示 200 行への再圧縮（A5-5）と Boris Cherny 型の棚卸し儀式（PF-13）。

---

## 付記: 提案台帳（PROPOSALS.md）への登録案

採番規則の提案: **1 監査＝1 接頭辞、出典ファイル名から機械決定**（回数依存を廃止）。本監査は `A6-`、前回再監査（2026-08-31）の未登録分は `A7-`、codex 監査は既存 ID `IA-20260831-01〜15`・`R-01〜09` をそのまま使う。各監査 md の先頭に `<!-- proposals: A6-1..A6-24 -->` を宣言し validate で突合する（OP-1）。

| ID | 提案（1 行） | 出典 | 推奨 |
|---|---|---|---|
| A6-1 | C-1 封鎖: allowlist 反転＋ConfigChange 第 2 防衛線＋34 ベクタ selftest | RG-1/CC-10/SC-3 | P0 |
| A6-2 | CI 緑化（依存宣言 or 正規表現化）＋8.3 短縮名正規化＋「緑 run URL まで applied にしない」 | RG-2/RG-7 | P0 |
| A6-3 | 保護対象の拡張: `.claude/rules/**`・`.github/copilot/**`・常駐ファイル群・skills の権限系 frontmatter/hooks.json | SC-1/SC-2/SC-6/MP-3 | P0 |
| A6-4 | gen-docs のリテラル品質主張を除去、手書き数値の生成物化、validate 走査の拡張 | RG-12/A5-7 | P0 |
| A6-5 | 台帳の採番規則・監査 md の宣言・登録完全性の validate、applied 3 点規則、A2-4b/A3-4b/A3-5b 起票、codex 監査のコミット | OP-1/RG-13/RG-14 | P0 |
| A6-6 | 単価表の外出し（prices.json）と訂正、D040 決定5 の単価・解決順訂正 | TM-1/MS-2/MS-3/CC-4 | P0 |
| A6-7 | effort-log.csv の廃止と logs/usage への一本化（移行・gitignore・6 面更新） | RC-4/RC-6 | P0 |
| A6-8 | ホスト版ゲート: PLATFORM 最低版表更新・e2e-run/validate の版数比較・開発機更新 | CC-1/OP-2/RD-7/RG-9 | P0 |
| A6-9 | e2e トラジェクトリの即時退避と保持方針 | EV-2/EV-11/RD-10 | P0 |
| A6-10 | model-policy.yml 単一ソース＋生成器＋validate＋三層強制＋D007/D040 supersede | MP-1〜7/CC-2/MS 群 | P1 |
| A6-11 | Copilot のモデル方針反転（親を強く・worker を軽く）、CLI settings.json ピン、cloud は SOP | CP-4/MS-4/MP-3 | P1 |
| A6-12 | 会話ごとの受領書（statusline 永続化・Stop systemMessage・/99 費用欄・harness-stats・基準線） | TM-3/RC-6/RC-12/TM-11 | P1 |
| A6-13 | サブエージェント帰属の meta.json 化、attributionSkill によるフェーズ帰属、PostToolUse(Agent) の実行モデル記録 | RC-2/RC-3/TM-4/TM-5/RD-3 | P1 |
| A6-14 | reviewer 実施を implementation の done 条件へ（チェックポイント・warn・review_ratio） | RD-2/RC-10 | P1 |
| A6-15 | 3 プロジェクトへの /91 適用と harness-origin.md の同梱、inject-progress の版差注入 | RD-4 | P1 |
| A6-16 | 評価装置 v2（verdict・条件固定・隔離・対称性・check.py 強化・backfill・REPORT 生成） | EV-1〜9/EV-10 | P1 |
| A6-17 | Copilot Agent Host 対応: hookFilesLocations・読取フィルタ・prompts→skills 移行の決定 | CP-1/CP-2/CP-3/SC-5 | P1 |
| A6-18 | Stop フックの last_assistant_message 優先化、hooks `if`、StopFailure/InstructionsLoaded 配線 | CC-14/CC-6/CC-9 | P1 |
| A6-19 | 指示層の棚卸し（機械強制済み禁止事項の削除・規範の一元化・Fable 5.1 公式ブロック置換・陳腐化修正） | PF-1〜7/PF-10 | P1 |
| A6-20 | hook-decisions の JSONL 化と hook_log 共通化、redaction パターンの 1 ファイル化 | RC-5/RC-11 | P1 |
| A6-21 | A/B 系列（能力・効率・effort sweep・Sonnet 5 worker・Spec Kit アーム） | §4.3/§6 | P2 |
| A6-22 | Copilot CLI 導入と実機実験（ティア・優先順位・auto・二重発火レイテンシ）＋Copilot 受領書 | MP-8/A2-7/TM-2/RC-8 | P2 |
| A6-23 | Agent Plugins 1.0＋.claude-plugin の同一ソース生成、CI の init-only/plugin validate | CP-7/RG-17/A2-2 | P2 |
| A6-24 | doctor と改善サイクル計測、常駐指示の再圧縮と棚卸し儀式 | A3-3/OP-4/A5-5/PF-13 | P2 |

前回再監査の未登録分（A7-）: C-1/C-2/H-1〜H-11/G-2/G-3/G-5/F25-1/A1-1〜A1-4 と §6 の MEDIUM 群（本監査で 8 カテゴリに付番して出典 §6 を指す）。G-1・G-4 は FIXED として登録し閉じる。

---

*本文書は監査者の統合であり、各指摘の一次証拠（実行コマンドと出力・実ファイルの行番号・出典 URL）は検証セッションの成果物（`scratchpad/wf2-verified.json`・`wf2-design-reviews.md`・`research-digest.md`）に残っている。台帳登録時に必要なら本文へ転記する。*
