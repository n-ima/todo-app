<!-- proposals: A7-C-1..2, A7-H-1..11, A7-G-1..5, A7-F25-1, A7-A1-1..4, A7-M-1..9 -->
# 外部再監査 2026-08-31（ゼロベース・独立セッションによる総合再監査）

- 実施: ハーネス作成セッションおよび過去監査セッションとは独立した Claude Code セッション。**41 エージェント**（最新動向リサーチ5・観点別監査12＝27観点・ギャップ分析3・敵対的検証20・網羅性批評1）を Workflow でオーケストレーション。総サブエージェントトークン約 479 万・ツール呼び出し 1,110 回。
- 対象: リポジトリ全体（追跡 221 ファイル＋未追跡 1）＋ 2026-08-31 時点の業界一次情報（Anthropic 公式 changelog v2.1.251 / Claude Code hooks・skills・plugins・memory 公式ドキュメント / GitHub Copilot custom agents / Spec Kit・BMAD v6・Kiro・GSD / 著名実務者 / 評価手法）。
- 位置づけ: 「説明用 HTML の修正指示に対しエージェントが 6 本中 1 本しか直さなかった」事故を受け、**過去の監査・修正の適用範囲が狭かったのではないか**という疑いに対する全面的な検証。過去監査（`audits/external-audit-2026-08-19.md` の E1〜E20、`audits/PROPOSALS.md` の全提案）は「現在も直っているか」を実ファイルで回帰確認した。
- 検証規律: critical/high 指摘のうち 20 件を独立した敵対的検証者が「反証」を目標に実ファイル・実行で再検証（**CONFIRMED 18 / PARTIAL 1 / REFUTED 1**）。監査者自身も CRITICAL 2 件を手元で再現した（本文に実測出力）。

## 総評

**設計思想は依然として世界トップ群。しかし「applied と記録された修正が、実際にはごく一部にしか適用されていない」型の欠陥が広範に残存しており、今回の HTML 事故はその氷山の一角にすぎない。**

過去監査は正しく問題を指摘し、正しく「applied」を記録していた。問題は**適用の網羅性**にある。同じ構造の欠陥（＝「N 個のうち 1 個だけ直して applied にした」）が、HTML だけでなく、セキュリティガード・フックの sh/ps1 等価・評価装置・CI・状態機械の各所で確認された。すなわち**このハーネスの最大のリスクは、思想でも個別バグでもなく、「修正の網羅性を機械的に保証する仕組みの不在」＝メタレベルの品質保証の穴**である。

決定的な症状が 2 つある。

1. **CI が導入初日（2026-08-11）から 22 回連続失敗・成功ゼロのまま 19 日間放置**され、全機械検査が CI 上で一度も実行されていない（CRITICAL）。「指示より Hooks で機械強制」という中核教義の自己適用が、最上位の防衛線において実質ゼロだった。
2. **設定ガードがインタプリタ経由の書き込みを検知せず、`python -c` ワンライナーで deny 一覧ごと保護機構全体を無効化できる**（CRITICAL）。自己権限昇格防止という最重要ガードが、最も普遍的な攻撃手段に対して開いている。

この 2 つは「作ったが一度も緑を確認していない」「塞いだが同クラスを塞ぎ切れていない」という、**まさに今回の HTML 事故と同型**の問題である。思想の修正は不要。必要なのは (a) 自己検査（CI・selftest・validate）を実際に緑にして常時運転すること、(b) 修正を「1 面」でなく「全鏡面」に機械的に強制すること、(c) 「世界最高」を名乗る根拠となる A/B 実測を統計的に成立する形でやり直すこと、の 3 点である。

---

## 1. 発端事故の構造的根本原因（最重要）

「HTML を修正して → 6 本中 1 本だけ直った」は、エージェントの理解の狭さだけが原因ではなく、**ハーネスの構造そのものが「1 面だけ直す」を誘発・許容している**。

- **A3-4「鏡の生成物化」（applied / D054）の適用範囲が提案より大幅に狭い。** 提案は「generate-adapters を README 表・HTML・USAGE 表まで拡張し手編集をガード禁止」だが、実装された `tools/gen-docs.py:222-226` の生成ブロックは **README の1表・overview.html の1ブロック・harness/README.md の1ブロックの計3箇所のみ**。`agents.html / skills.html / commands.html / guardrails.html / COMPARISON.html` の本文と `USAGE.md` の表は**生成対象外・マーカーなし・検査対象外**。
- **検査（`validate-harness.py`）も鏡割れを検出できない。** 数値照合は「selftest / 自己テストを含む行の『Nケース』」という狭い正規表現のみで、スキル数・コマンド数・フック数・決定数のハードコードや COMPARISON の「94/71」形式を検出しない。USAGE.md はそもそも走査対象外。
- **結果、`gen-docs --check` も `validate-harness` も緑のまま、複数ファイルで数値が実際に割れている。** エージェントに「HTML を全部直せ」と機械的に強制する仕組みが存在せず、6 本が 1 つの鏡セットであることがコード上どこにも表現されていない。だから 1 本直して「直した」と報告するのが構造的必然だった。

この根本原因は、以下の複数の CONFIRMED 指摘で実証されている（A1-1〜A1-4）。**恒久対策は「6 本の HTML と主要な数値言及を gen-docs の生成対象に含め、validate で一次データ実数との突合を必須化する」こと。**

---

## 2. CRITICAL（設計目的を無効化。監査者が手元で再現済み）

### C-1. 設定ガードがインタプリタ／git plumbing／junction 書き込みを検知せず、`python -c` 一発で保護機構全体を無効化できる（security/S1・E7 の不完全修正・regression）

`guard-harness-config-edit`（sh/ps1 両方）の書き込み判定は `リダイレクト / tee / cp・mv / sed -i / Set-Content 等`の**固定 allowlist**。列挙外の書き込み手段はすべて素通りする。監査者自身の実測（`D:/vscode-worspace/CreateAppl` で実行）:

```
$ printf '{"tool_name":"Bash","tool_input":{"command":"python -c \"open(\x27.claude/settings.json\x27,\x27w\x27).write(\x27{}\x27)\""}}' | bash .github/hooks/scripts/guard-harness-config-edit.sh
{"continue": true}                    ← 素通り（ask も deny も出ない）

$ printf '{"tool_name":"Bash","tool_input":{"command":"echo x >> AGENTS.md"}}' | bash ...guard-harness-config-edit.sh
… permissionDecision: "ask"           ← 対照：既知の書き込み動詞は ask

$ printf '{"tool_name":"Edit","tool_input":{"file_path":"AGENTS.md"}}' | bash ...guard-harness-config-edit.sh
… permissionDecision: "deny"          ← 対照：Edit ツールは deny
```

敵対的検証者はさらに `node -e`・`git apply`・`git checkout HEAD~1 -- AGENTS.md`・`git restore`・`dd of=`・`install`・`mklink /J`（ジャンクション）も sh/ps1 双方で全て素通りすることを確認。`.claude/settings.json` の deny は Edit/Write ツールのみを塞ぐため、`python -c "open('.claude/settings.json','w')..."` は (a) 本ガードが検知せず (b) deny 対象外の Bash 実行、の二重理由で通り、**deny 一覧そのものを消去して保護システム全体を無効化できる**。Copilot 経路は settings.json backstop すら無く、このガード 1 枚が唯一の防御。

**帰結**: プロンプトインジェクションによる自己権限昇格・ガードレール解除という、このハーネスが最も防ぎたい攻撃が、最も普遍的なツール（python/node は run-python.sh 等で常用）で成立する。E7 の「Bash 書き込みが deny 対象外」修正がリダイレクト系のみで同クラスを塞ぎ切れていない不完全修正。

**提案**: 書き込み文脈の allowlist 方式をやめ、保護対象パス文字列が読み取り専用語（cat/grep/less/head/tail/rg/find/ls）以外の文脈で出現したら ask に反転。最低でも `python/python3/node/perl/ruby/awk/dd/install/cp/tee` と `git (apply|checkout|restore|mv|rm)` を書き込み語に追加。シンボリックリンク／ジャンクション作成（`mklink`, `New-Item -ItemType (SymbolicLink|Junction)`, `ln -s`）を検知して ask。selftest に本節の全ベクタを回帰追加。

### C-2. ハーネス CI が導入初日から 22 回連続失敗・成功ゼロのまま 19 日間放置され、全機械検査が CI 上で一度も走っていない（ci-recovery/CI-1・regression）

監査者自身の実測:

```
$ gh run list --limit 8   （harness-ci, main）
2026-08-30  failure  D071 …
2026-08-30  failure  D070 …
… 直近8件すべて failure（敵対的検証者の --limit 100 では 22 runs / success 0）

$ grep -n "import yaml" tools/validate-harness.py   → 37:import yaml
$ grep -n "pip install" .github/workflows/harness-ci.yml   → （空。依存インストール手順なし）
```

`harness-ci.yml` は `setup-python@v5` の直後に `python tools/validate-harness.py` を実行するが `pip install` ステップが全ジョブに無く、`validate-harness.py:37` の `import yaml`（docstring が「依存: PyYAML」と自己申告）で 1 ステップ目から `ModuleNotFoundError: No module named 'yaml'` で即死。したがって `gen-docs --check`・`selftest.sh`・`golden-eval`・`trace-check`・`sync`・`intake` の各検査は **CI 上で一度も実行されていない**。加えて `validate-windows` ジョブも別原因（後述 C-3・BOM/LF 検査）で失敗し続けている。

一方 `COMPARISON.md:254-255` は「push/PR ごとに ubuntu と windows の両方で実行する」と旗艦文書で宣伝し、`CONTRIBUTING.md:28-29` は「検査項目の正は harness-ci.yml の実ステップ」と規定。P0-2（D046 で「手動検査に頼る自己矛盾を塞ぐ」ため新設）・E6（外部監査で Windows ジョブ拡張）・A3-4・A3-5 はいずれも CI ゲートとして applied 扱いだが、**CI 上では一度も動いていなかった**。README に CI バッジも失敗通知も無く、赤の常態化が可視化されていない。

**帰結**: 回帰が入っても CI は何も検出しない。「指示より Hooks で機械強制」という中核主張の自己適用が最上位の防衛線でゼロ。**これが今回の再監査で最も深刻な発見。**

**提案**: (1) `requirements.txt`（PyYAML 固定）を追加し全ジョブで `pip install -r requirements.txt`、または `validate-harness.py` の frontmatter 解析を正規表現化して PyYAML 依存を除去。(2) 修正後に **CI が実際に緑になることを確認**し、README に CI バッジを追加。(3) main への push で CI 失敗時の通知経路を決める。(4) DECISIONS.md に「**CI は一度緑を確認するまで applied と記録しない**」規律を追記。

---

## 3. HIGH（検証済み CONFIRMED。特定条件で機能不全）

### セキュリティ／フック

- **H-1（hooks-parity/H5-1・regression）** `guard-dangerous-git.sh` / `guard-harness-config-edit.sh` にバッククォート・キャレット行継続の畳み込みが無く、`git ` + backtick + 改行 + `push`（PowerShell の正当な行継続構文で実際に `git push` として実行される）が **ask を素通り**。ps1 版だけ第3回監査で修正済みだが、`.claude/settings.json:82-98` は Windows でも sh 版を bash で実行する配線のため ps1 の修正は死にコード。実測で backtick/caret 継続が両ガードで素通りを確認。selftest は backslash 継続しかテストしていない。→ 畳み込みを `sed -E 's/(\\|`|\^)\r?\n/ /g'` 相当に拡張し ps1 と同一化、selftest 回帰追加。
- **H-2（hooks-parity/H5-2・ci-recovery/CI-3・new/regression）** 共通パス収集ライブラリ `_paths.sh`（自称 D072）が**未コミット・未配線の死蔵コード**。`guard-harness-config-edit` / `guard-template-edit` は `file_path`/`filePath`/`path` の単一値抽出のみで、**`uri`・`notebook_path`・`apply_patch` 本文（`*** Update File:`）経由の保護域編集が sh/ps1 両方で素通り**（実測確認）。`guard-phase-scope` だけは D063/D066 で同型収集を実装済み＝防御非対称。matcher は `NotebookEdit` を含むため notebook_path 経由が直接 fail-open。D072 は DECISIONS.md に記録すら無い。**「N 個のうち 1 個だけ直した」事故の現在進行形。** → `_paths.sh` を全ガードに source（ps1 は `_paths.ps1` 新設）、selftest 両系に uri/apply_patch/notebook_path × 保護域の回帰追加、D072 を正式記録するか削除。
- **H-3（security/S2・new）** `guard-secret-leak` の高確度パターンに **npm token（`npm_…`）・JWT（`eyJ…`）・Azure Storage キー（`AccountKey=`）・SAS（`sig=`）が無く**、これら 2026 年主要形式が無警告で書き込める（両実装で実測）。generic パターンは値の前後にクォート必須のため `password=hunter2longenough16chars`（.env/YAML で頻出）は ask にすら到達しない。→ 主要形式を high_confidence に追加、generic のクォート必須を撤廃。

### ゲート状態機械

- **H-4（agents-statemachine/G11-02・new）** brownfield 由来の運用中プロジェクト（`test=in_progress` ＋「状態: 運用中」注記）では **`guard-phase-scope` が常時 allow** となり、D062 のフェーズ外編集 deny が丸ごと無効化される（実行で確認）。ガードは運用中注記を見ず「in_progress があれば allow」のため、`route-request` が注入する「場当たり編集はフェーズ外ガードが止める」という契約文言が虚偽になる。**D062 が塞いだ「宣言素通り」失敗モードが brownfield 導入直後という最頻シナリオで再開。** → ガードの状態判定に運用中注記の検査を追加し「運用中注記があれば in_progress の有無に関わらず deny（＝/12 経由を要求）」、selftest 追加。
- **H-5（agents-statemachine/G11-01・PARTIAL→medium）** フェーズ開始時に GATE_STATUS を in_progress へ遷移させる責務が入口プロンプト／エージェントに未配線で、`guard-phase-scope.sh:246` の「自分で in_progress に書き換えるのはゲート改竄」という文言と、D067 が「正規遷移」と呼ぶ実運用が矛盾。**ただし** `orchestrator.agent.md:71-74` に機械的 in_progress 更新規則は存在し、E2E ではブロッキング実害ゼロが実測されているため severity は medium に訂正。文言統一と入口への配線明記は必要。

### スキル／プロンプト層（E1「太ったプロンプト」の回帰）

- **H-6（skills-quality/SK-01・regression）** `/99` プロンプトが `progress.md`（なければ作成）と**無条件作成**を指示し、`orchestrator.agent.md:34-39` の手順0（brownfield 検知＝作成せず /11 案内で終了）を後勝ちで上書き。既存アプリで `/99` を最初に実行すると brownfield 検知が飛ばされ、テンプレ由来の全 not_started な progress.md が作られてグリーンフィールド一本道に落ちる。E1（P0）と同一クラスの残存。→ 04/05 型の薄い参照に書き換え。
- **H-7（skills-quality/SK-02・regression / SK-03・G11-04）** 同型の太い再記述が **`/07`・`/09`** にも残存（安全手順の欠落）。さらに **`/11-brownfield-intake` は execute を持たない `requirements` エージェントにバインド**されているのに、brownfield-intake スキルは「既存テストの実行（必須）」「intake-app.py 実行」「environment.md の実機確認」「ベースラインコミット」を要求。**Copilot 経路では実行手段がゼロ**（呼べるサブエージェント spec-critic も read/search のみ、ハンドオフ先 design も execute 無し）で、D068 で実証済みの「実行不能→ユーザー依頼→証拠なし done」の再現条件そのもの。converge スキルにはある「実行権が無い環境ではユーザーに実行を依頼」の逃げ道が brownfield-intake には無い。→ `/11` に execute を持つエージェントを割り当てるか、スキルにフォールバック文言を明記。

### 評価・配布

- **H-8（eval-tools/E15-01・C27-2・new）** 主戦場 A/B の予算が**非対等（bare $40 vs harness $60、実 JSON で確認）**で「同一予算」「同一条件」の看板主張と矛盾。harness の PASS 2 走の実コストは $69.06 と $49.10 で**いずれも bare の上限 $40 を超過**（同一予算なら予算打切りで DNF になる算術を、累積コストの call 単位実測で確認）。`evaluation/README.md` 自身が「条件が揃っていない結果同士を比較しない」と規定しながら違反。加えて結果 JSON に **model フィールドが無く**「同一モデル」条件が事後検証不能、トラジェクトリは末尾2000字のみで A3-1 の「トラジェクトリ同梱」を構造的に満たせない。→ 予算をタスク定義側でアーム共通に固定、model 必須記録、トラジェクトリ保存、同一コミット・同一バージョンで再走行。
- **H-9（eval-tools/T16-01・new）** 配布された `validate-harness.py` が **全プロジェクトで必ずエラーになる**（`gen-docs.py` は配布除外なのに validate は存在必須で ERROR 化）。sync 経路（193 ファイル注入）でも git archive 経路でも実測でエラー再現。それでいて `sync-harness.py:466-468` は「プロジェクト側で validate を実行しエラー0を確認」を次手順に指定＝**構造的に達成不能**。→ gen-docs.py と生成先マーカーが揃う場合のみ検査（プロジェクトでは INFO スキップ）、`.gitattributes` に export-ignore 追加で配布経路を一致。

### Windows／導入

- **H-10（ci-recovery/CI-2・new）** `guard-phase-scope.ps1` が**非正規形パス（8.3 短縮名等）で fail-open（allow）**。CI の `validate-windows` が 2 ケースの FAIL として **8 日以上検出し続けているのに放置**（GitHub runner の TEMP が `RUNNER~1` 短縮形のため）。bash 版は `cygpath -m` で正規化しており非対称。ローカルの selftest.ps1 は 73/73 で通るため CI でしか出ない差分。監査者もローカルで 8.3 短縮形 payload → allow / 長形式 → deny を再現。→ ps1 側でパス正規化（GetFullPath＋短縮名解決）、他の ps1 ガードにも横展開、selftest 回帰追加。
- **H-11（concurrency-onboarding/P20-1・new）** 推奨導入経路（GitHub テンプレート）で **DECISIONS.md が残留し、初日から「ハーネス本体」誤検知**。`route-request` / `inject-progress` / `guard-phase-scope` / request-routing スキルの 4 面が「DECISIONS.md がある→本体。アプリ開発の入口（/00, /11, /12）は使わない」と判定し、受付注入が /00 を禁止・phase-scope ガードも無効化。D058 の memo_is_pristine 救済は USAGE.md 分岐にしか掛からず、ユーザーが実メモを書いても本体扱いのまま。テンプレート経路は git archive を使う E2E で一度も検証されていない。→ DECISIONS.md 分岐にも memo_is_pristine を AND 条件で掛け、4 面同時更新、selftest 回帰追加、USAGE に「クローン直後に DECISIONS.md/audits/ を削除」を必須手順化。

---

## 4. HIGH（最新動向ギャップ／敵対的検証は未実施だが証拠は実ファイル確認済み）

- **G-1（copilot-gap/C26-1）** D057 で Antigravity を凍結した後も、旗艦文書群に「**3環境等価**」「改善は1回で3環境に届く」の主張が残存（COMPARISON.md/html・overview.html・commands.html・README.md）。Antigravity 経路は E2E 検証を一度も行っていない。**今回の HTML 事故と同型の鏡割れが等価性主張という中核記述で現存。** → 「2環境検証済み＋Antigravity 凍結アダプタ（未検証）」に全文書統一、生成ブロック化。
- **G-2（copilot-gap/C26-2・C26-4）** Copilot 環境の定義が VS Code 単体に限定され、UI から1クリックで到達できる **Copilot CLI / cloud agent の挙動差（hooks 範囲・ask→deny・model 無効・handoffs 無効・59分上限）が未文書化**。cloud agent は非対話のため PreToolUse の ask が deny 扱いになり、`guard-dangerous-git` の ask により **git push/tag がクラウド委譲先で常に拒否されリリース系フローが機能不全**になり得る。しかも Copilot CLI は GA 済み（`--autopilot`・`.github/hooks` 読込）で E2E 自動化が可能なのに、恒久的な人手 60-90 分チェックリストに固定されている。→ PLATFORM.md のガードレール強度差表に Copilot CLI / cloud の行を追加、運用規範を D068 と同様に明文化。
- **G-3（claude-gap/F25-2）** project skill の allowed-tools が **workspace trust 非依存で効く**現仕様に対し、`.claude/skills` を「動的 Skill 追加は中核機能」として無防備に開放。エージェント（またはインジェクション）が `allowed-tools:` 付きスキルを自由に作成・起動でき、permissions.ask の二重化を無効化する自己権限昇格経路が残る。→ guard-harness-config-edit に「skills 配下への書き込み内容に権限系 frontmatter（allowed-tools/hooks/shell/disable-model-invocation）を含む場合は ask」を追加、skill-authoring に点検項目追加、validate で常時監視。
- **G-4（industry-gap/C27-1）** 旗艦比較文書 COMPARISON.md が**自ハーネスの実装済み機能（証拠鮮度規律＝D054）を「未導入」と誤記**し、決定数も同一文書内で 71/53/68 の 3 値混在、章番号も重複。D069 の値是正が一部の数値しか直しておらず、伝播漏れが旗艦文書に現存。→ A5-7 の生成物化対象を「判定文言（未導入/実装済み/open）」まで拡張。
- **G-5（industry-gap/C27-3・C27-4）** 自認済みの核心ギャップ 3 件（**他ハーネス直接 A/B・superpowers 調査・自動 fresh context**）が PROPOSALS.md に未登録＝台帳の自己規則違反。成果証明の統計的強度が 2026 年基準に未達（**n=2・k=2・課題1種・評価の CI 常設なし**）。「世界最高＝測定で優位を証明」の定義上、比較相手が素の Claude Code のみでは定義を満たす経路が台帳上に存在しない。**A3-1（A/B n≥6＋pass^k＋トラジェクトリ）が世界最高の名乗りの必要条件かつ最優先の残距離。** → 3 件を起票、A3-1 を C27-2 の装置修正後に最優先で実施。

---

## 5. 監査の健全性（REFUTED 1 件）

- **F25-1（REFUTED）** 「サブエージェントの fork 型デフォルト化（v2.1.232）で中核前提『まっさらな独立コンテキスト』が崩れる」疑いは、**changelog の誤読**として敵対的検証で棄却。公式仕様では会話を継承するのは明示的に `subagent_type: "fork"` を指定したスポーンのみで、`.claude/agents/` 定義から起動される reviewer/spec-critic/task-worker は fork mode 有効時も従来どおり独立コンテキスト（公式ドキュメント「Subagents spawned from a definition work as usual」で確認）。加えて実機は v2.1.201 で fork mode 自体まだ既定オフ。**この 1 件を棄却できたことは、今回の監査が過剰検出に流れず実証に基づいていることの傍証。** 残る価値は「ホスト既定変更を検知するカナリア追加」という一般的示唆のみ。

---

## 6. カテゴリ別の主要 MEDIUM（81 件から抜粋）

**記録系・衛生**
- CHANGELOG 4 版に対応する git tag がローカル・リモートとも 1 つも無い（スキル自身のタグ作成要求が未履行）。CHANGELOG の Unreleased に配布物変更（D067/D069）が未記載で D 単位の記載基準が未定義。
- `sync-harness.py` は作業ツリーの glob 収集で配布するため、**未コミット・未追跡ファイル（現に `_paths.sh`）がプロジェクトへ混入**。配布 3 経路の除外リスト不整合（gen-docs.py が経路 A だけ配布される）。
- DECISIONS.md（210KB・71件）の単一ファイル運用が「改修前に必ず通読」要求と両立せず、11 日で +78KB の非持続的成長軌道（**H 級として検証済み CONFIRMED**）。索引不在。→ テーマ別索引を gen-docs 生成、「通読」を「索引で該当 D のみ精読」に変更、閾値到達でアーカイブ分割。

**フックの sh/ps1 等価**
- `warn-stale-gate` の done 判定が注記つき値（`done 2026-08-01`）で sh 版だけ警告全損（実測）、ps1 は警告。selftest カバレッジ非対称（sh 96 / ps1 73）で ps1 独自実装の危険箇所（git tag 判定・教訓注入）が ps1 側で未テスト。
- シェルコマンド経由のゲートファイル書き換え（`sed -i progress.md`）が warn-gate-tamper / warn-stale-gate の matcher 対象外。

**状態機械**
- `pending_approval` 状態の生成規則が未定義（消費側だけ存在）。GATE_STATUS のキー欠落・値タイポに対する完全性検査が無く各コンポーネントが黙って劣化。implement↔test の双方向 send:true に横断反復上限が無い。/13-converge→/06 のゲート遷移が未定義。

**テンプレート・導線**
- `security_review_report_template` が D070 改名済みの旧スキルパスを参照。NFR テンプレに ID 列が無く非機能要件のトレーサビリティが構造的に切断。トレーサビリティ ID 体系がテンプレ間・文書間で不一致。docs 各 README の成果物索引に environment.md・interfaces/・ui/ 等が欠落。README.en.md が「3環境同一動作」を宣言し現状と矛盾。

**評価装置**
- expense-webapp の check.py は第2ステージと回帰を**キーワード共起でしか検証しておらず表面的**（todo-cli のような実行検証が無い）。A/B 台本が harness アームに「解消の経緯を要件文書に記録」と**合格条件そのものを指示**しており因果帰属が過大。`e2e-run.py` は check.py タイムアウト（1200s）未捕捉でハング時に走行済みアームの結果が全損、中断・再開機構が無く約 28 USD の走行を最初からやり直す。CI が測定装置（e2e-run/check.py の selftest）を一切実行していない。

**CI・復旧**
- validate-harness.py の警告は exit 0 のため中核不変条件の破れが CI を緑のまま通過。「アダプタ＝ポインタのみ」不変条件の本文ドリフト（description の一致しか見ない）を CI が検査できない。クラッシュ・電源断で GATE_STATUS が破損した場合の検知機構が無い（inject-progress は空ブロックを無言注入）。

**並行性・導入 UX**
- 同一 working tree での 2 セッション並行に排他・楽観的検知が皆無。tasks.md に着手中マーカーの書式が無く重複着手を防げない。worktree 並列の指示が implement と task-worker で矛盾。**Windows + Git Bash（README 指定環境）でツール群の日本語出力が文字化け**（実行で確認）。doctor 不在（A3-3 open）で PyYAML 依存欠落により初日に validate が生 traceback で死ぬ。plugin.json 経路は docs テンプレ・tools・AGENTS.md・.claude 配線を含まず単独では機能しないのに README は「同じ内容が使える」と過大表現。

**コンテキスト経済・文字品質**
- 常駐指示 335 行（AGENTS.md 296＋CLAUDE.md 39）が公式目標 200 行未満を超過（A5-5 未消化・微増）。公式の分担先 `.claude/rules/`（paths frontmatter）が未導入。`inject-progress` の注入量に実質上限なし（GATE_STATUS 閉じタグ欠落で progress.md 全文注入）。毎応答で従う「セッション分割表」が 39KB の USAGE.md の 7% に埋没。`check-doc-chars` に ZWSP・NBSP・双方向制御文字（U+202E 等インジェクション隠蔽文字）の検査クラスが無い。ハーネス名が 3 通りに分裂（copilot-sdlc-harness / app-dev-harness / CreateAppl）。

**Claude Code 最新機能の未活用**
- skills の新 frontmatter（`disable-model-invocation`・`user-invocable`・`argument-hint`・`context: fork`）を全 21 スキルで未使用。hooks の新イベント（`SubagentStart`・`PostToolUseFailure`・`ConfigChange`・`TaskCompleted`）未活用。**特に `SubagentStart` は A3-8（委譲エンフォーサ）の「フックがエージェント識別を得られない」既知の閉塞を解く可能性がある。** plugin.json が Claude Code プラグインスキーマ・Agent Plugins 1.0 のどちらにも準拠しない独自形式（hooks キーが Copilot 形式 JSON を指す）。

---

## 7. 網羅性批評（この監査自体の穴＝次サイクルで埋めるべき空白）

網羅性批評エージェントが「誰も読んでいないファイル・検証されていない主張」を洗い出した結果:

- **依存管理の観点が監査全体から欠落**: PyYAML が requirements.txt・README・CI のどこにも宣言されず、実測で validate ジョブが常時赤（C-2 と同根）。外部依存の宣言・固定・オフライン動作という監査軸が無かった。
- **実行検証の空白が最大の弱点**: (a) 現行コミットでの **Copilot 実機フック発火検証ゼロ**（大規模ガード刷新後に ps1 フックが Copilot 上で実際に deny する確認が無い）。(b) **e2e 実走行（claude 実呼び出し）が今回未実行**で効果主張が旧 JSON 頼み。(c) **第3環境「汎用エージェント」（.agents/workflows 18本）はいかなる実エージェントでも一度も追従テストされていない**。(d) **plugin.json 経由の実インストール実験が皆無**。(e) intake-app.py --apply の実ブラウンフィールド適用が未検証（sync 側だけ実適用された非対称）。
- **性能観点の欠落**: 毎ツール呼び出しのフック起動レイテンシ（Windows の powershell -NoProfile 起動は百 ms 級になりうる）が未計測。14 種配線で数百呼び出しなら分単位のオーバーヘッドの可能性。
- **法務・プライバシー・履歴**: 配布経路への LICENSE 同梱が未検査。HTML 6 本が fonts.googleapis.com を外部参照（オフライン劣化・閲覧時外部送信）。フックが logs/ に書き出す内容（プロンプト本文・秘密の転写）の機微性が未監査。**git 履歴全体のシークレットスキャン（gitleaks 相当）が未実施**。
- **未読の一次資料**: DECISIONS.md の D001〜D041（913 行・全体の 4 割）が全観点で未読。初期決定（custom agents 採用・フックゲート強制・reviewer 別セッション）が現実装と食い違っていないかの回帰が欠落。.github/CODEOWNERS がフック保護範囲との整合契約を宣言しているのに誰も突合していない。
- **HTML の実描画・a11y 未検証**: 説明用 HTML の修正漏れが発端の監査で、成果物の見た目の検収（SVG 崩れ・ダークモード・コントラスト）が省かれている。

---

## 8. 最新動向リサーチの要点と「世界最高」への示唆（2026-08-31 時点）

### 確定した前提

- **AGENTS.md ネイティブ対応は Anthropic 公式に「not planned」で決着**（Issue #6235・5,200+ リアクションで最多要望）。公式ワークアラウンドは `CLAUDE.md → @AGENTS.md` インポート。**本ハーネスの「AGENTS.md を正・CLAUDE.md は薄いインポート」方式は公式解と一致しており正しい**（D025 の前提は現時点で有効）。シムリンクは Windows で管理者権限が要るためインポート方式一択。
- **常駐指示は 200 行未満が公式目標**。手順的内容は skills（オンデマンドロード）へ、パス限定の規約は **`.claude/rules/*.md` の paths frontmatter** へ移すのが公式の分担ルール。`@import` はコンテキスト削減にならない。→ **A5-5（335 行→200 行未満）は公式数値に沿った最優先の構造改善**。実務者基準（HumanLayer 60 行未満）はさらに厳しい。
- **強制したい規約は CLAUDE.md でなく hooks へ**（公式が「CLAUDE.md はコンテキストであり強制ではない。ブロックは PreToolUse hook で」と明言）。本ハーネスの方向性は正しいが、C-1/C-2 のとおり**その hooks 自身が緑になっていない・塞ぎ切れていない**のが問題。

### 世界最高への具体的な取り込み候補

- **long-running agent の公式ハーネスパターン**（Effective harnesses for long-running agents, 2025-11 / Harness design for long-running apps, 2026-03）: `initializer が init.sh・progress ファイル・初回コミットを作成 → 各セッション冒頭で git log+progress 読込 → 機能リスト JSON（全件 failing 開始・テスト改変禁止）→ 1 機能ずつ → E2E 検証 → コミット`。**E2E 通し実行の残項目にこの構造をそのまま適用できる。**
- **generator-evaluator 分離**（自己評価は過大評価に陥る）と**採点可能な基準への変換**は、本ハーネスの reviewer 分離・evidence-gated write の思想と一致。強化方向は「主観評価を数値化された基準（機能リスト pass/fail・Lint・E2E 成否）に変換」。
- **モデル向上に応じて足場を削る**（Opus 4.6 では sprint 分解等のスキャフォールドを撤去できた）: ハーネスに「仮定の棚卸し」工程を設け、モデル世代ごとに強制 context reset 等を見直す（A3-6 の Day-0 再ベンチ儀式に接続）。
- **配布はプラグイン化が本命**: hooks+skills+agents+MCP を plugin.json にまとめ marketplace（git/GitHub/archive）配布、userConfig で環境差を吸収。→ A2-2（Agent Plugins 1.0 準拠）を open のままにせず消化。
- **新 hooks イベントの活用**: `SubagentStart` で委譲エンフォーサ（A3-8）の閉塞を解く。`TaskCompleted`・`PostToolBatch`・`ConfigChange` を品質ゲートに。
- **skills 統合時代への追従**: カスタムスラッシュコマンドは skills に統合済み。副作用ワークフローに `disable-model-invocation`、背景知識に `user-invocable: false`、独立タスクに `context: fork`。他ツール互換が必要なスキルは Agent Skills 標準の 6 フィールドに限定。

---

## 9. 今回追加した監査観点（27 観点。依頼「追加の観点を 20 個以上」への回答）

過去監査（E1〜E20）と重複しないよう、以下 27 観点で監査した（①〜㉔＋ギャップ㉕〜㉗）。太字は過去監査に無かった新規観点。

1. 生成物と手編集のドリフト（鏡の完全性）　2. 説明用 HTML 群の品質　3. **リポジトリ衛生（pyc/ログ/参考資料の配布混入・巨大 blob）**　4. **記録系の持続可能性（DECISIONS.md 210KB・tag・CHANGELOG）**　5. フックの sh/ps1 等価性　6. フック配線の網羅性　7. セキュリティバイパスの新規探索　8. プロンプトインジェクション耐性　9. **スキル 21 本の全数品質**　10. プロンプト/コマンド層の薄さ回帰　11. エージェント定義の品質　12. **ゲート状態機械の健全性（未定義遷移・破損時挙動）**　13. **docs テンプレート 16 本の実用性**　14. **ドキュメント読者導線（情報アーキテクチャ）**　15. **評価系の統計的妥当性（予算対等性・n・model 記録）**　16. tools/ Python 10 本の品質　17. CI の実効性　18. **障害復旧・部分失敗の扱い**　19. **並行性・マルチセッション安全性**　20. **導入 UX（初日体験・doctor 不在）**　21. 哲学の自己適用の再検査　22. **COMPARISON 等の主張の正確性（数値照合）**　23. **常駐コンテキストの実測とトークン経済**　24. **多言語・文字品質（不可視文字・用語ゆれ）**　25. **Claude Code / Anthropic 最新機能への追従ギャップ**　26. **GitHub Copilot 最新機能への追従ギャップとマルチ環境等価の限界**　27. **業界フレームワーク比較での競争力ギャップ（世界最高への残距離）**

---

## 10. 優先順位付き修正ロードマップ

### P0（今すぐ・世界最高を名乗る前の必須地固め）
1. **CI を実際に緑にする**（C-2）: requirements.txt 追加＋全ジョブ pip install、緑を確認、バッジ追加、「緑を確認するまで applied にしない」規律。**これを先に直さないと以降の全修正が CI で守られない。**
2. **設定ガードのインタプリタ／plumbing／junction バイパスを塞ぐ**（C-1）: allowlist 反転＋書き込み語追加＋selftest 全ベクタ回帰。
3. **保護域ガードの入力フィールド網羅**（H-2）: _paths.sh を全ガードに配線、uri/apply_patch/notebook_path 回帰、D072 記録。
4. **Windows fail-open の修正**（H-1・H-10）: backtick/caret 継続畳み込みの sh 移植、ps1 パス正規化、CI の 2 件 FAIL を消す。

### P1（構造的品質保証＝「1 面だけ直す」の根絶）
5. **鏡の生成物化を完遂**（発端事故・A1 群・A5-7）: 6 本の HTML と主要数値言及を gen-docs 生成対象に、validate で一次データ実数との突合を必須化、USAGE.md を走査対象に。
6. **秘密検知パターンの現代化**（H-3）、**brownfield 誤検知の解消**（H-4・H-11）、**/99・/07・/09・/11 のプロンプト薄化と実行権整合**（H-6・H-7）。
7. **評価装置の統計的健全化**（H-8・H-9）: 予算アーム共通固定、model 必須記録、トラジェクトリ保存、validate のプロジェクトモード。

### P2（世界最高の証明・追従）
8. **A/B 実測のやり直し**（A3-1 最優先）: 装置修正後に k=3〜5・regression＋capability の 2 スイート・他ハーネス（Spec Kit/OMC）直接 A/B・週次 cron CI。
9. **常駐 200 行未満化**（A5-5・公式目標）: 手順を skills へ、パス規約を .claude/rules へ、契約はバイト照合で保全。
10. **最新機能の取り込み**: plugin 化（A2-2）、SubagentStart で委譲エンフォーサ（A3-8）、long-running 公式パターンで E2E 通し実行、skills 新 frontmatter。
11. **3環境等価主張の是正**（G-1）と Copilot CLI/cloud の運用規範文書化（G-2）。

### P3（網羅性の空白を埋める＝次監査の入力）
12. 実行検証の空白（Copilot 実機・e2e 実走行・汎用エージェント・plugin インストール・intake --apply）、性能（フックレイテンシ）、法務/プライバシー/git 履歴シークレットスキャン、DECISIONS.md D001〜D041 の回帰、CODEOWNERS 突合、HTML 実描画/a11y。

---

## 付記: 提案台帳（PROPOSALS.md）への登録推奨

本再監査の全 177 指摘は PROPOSALS.md の運用規則（「監査の提案は必ず登録する。登録漏れは提案の無音消失」）に従い登録すべき。特に上記 P0〜P2 の 11 項目は D 記録つきで採否を明示すること。本監査ファイル自体を出典（`external-reaudit-2026-08-31`）として参照可能にする。
