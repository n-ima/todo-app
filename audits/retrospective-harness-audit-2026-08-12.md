# ハーネス総点検レポート第2部（2026-08-12）— 参考資料3本の分析と評価枠組みの適用

第1部（[retrospective-harness-audit-2026-08-11.md](retrospective-harness-audit-2026-08-11.md)）の
続編。ユーザー提供の『参考資料（ハーネス調査）』3本
（AI開発ハーネスの包括的調査 / compass_artifact（2025-2026包括調査）/ deep-research-report）を
精読し、記載された参考サイト・主張の裏取り（15項目の一次情報検証）を行った上で、
第1部の結論を更新・精緻化する。**修正はまだ行っていない**（提案のみ）。

---

## 1. 総評（第1部からの変化）

3資料は相互補完的で、第1部の調査と大枠で一致しつつ、**第1部に無かった評価の道具**
（役割分担の判断表・状態機械の形式化・100点チェックリスト・「複雑化の導入条件」ルール）を
提供している。これらを適用した結論は次の3点。

1. **本ハーネスの立ち位置を正確に言語化できた**: 本ハーネスは OpenHands や Claude Code の
   ような「ランタイムハーネス」ではなく、商用ランタイムの上に載る「**プロセスハーネス
   （SDLC層）**」である。3資料のチェックリストの約3分の1（サンドボックス・ACI・
   イベントストリーム・OTel）はランタイム層の責務であり、本ハーネスは
   「自前実装せず、プラットフォームから継承し、劣化モードを文書化する」戦略を既に
   採っている。この戦略自体が3資料の「simple first / 複雑性には導入根拠を要求」原則に合致する。
2. **最大のギャップは「検証の粒度」と「自己計測」に確定した**: 3資料すべてが最重要と
   位置づけるのは (a) タスク単位の機械的完了条件（done契約）と (b) ハーネス自体の効果測定
   （Golden Eval）であり、これは第1部の P2-1 と内部監査 G-2 に対応する。第1部では P2
   （品質向上）に置いたが、**3資料の一致度を踏まえ優先度を引き上げるべき**。
3. **「取り込まない判断」の裏付けが揃った**: 会話型マルチエージェント不採用・全面JSON化
   見送り・Graph Engineering 不採用などの現状維持判断は、3資料の「過剰」分類と一致する。
   ただし単なる不採用ではなく、**導入トリガー条件つきの見送り**として記録する
   （3資料の「新しいコンポーネントには観測済み失敗モードと評価が必要」ルールの適用）。

---

## 2. 決定的な視点: ランタイム層とプロセス層の2層構造

3資料が「ハーネス」と呼ぶものの大半は、モデルを直接駆動する実行基盤
（ツールディスパッチ・サンドボックス・イベントログ・compaction）である。
本ハーネスの構造はこう整理できる。

| 層 | 担うもの | 本ハーネスでの実体 |
|---|---|---|
| ランタイム層 | ACI（ツール群）・サンドボックス・compaction・トランスクリプト・権限実行・OTel | **Claude Code / Copilot / Antigravity から継承**（自前実装しない） |
| プロセス層（本ハーネスの本体） | フェーズ状態機械・ゲート・独立レビュー経路・docs-as-memory・受付ルーチン・成長ループ・変更管理・計測の集約 | AGENTS.md・agents・skills・hooks・docs/・tools/ |
| 接続層 | ランタイムの設定・制約の配線 | .claude/settings.json・.github/hooks・.vscode/settings.json・劣化モード文書 |

この区別の含意:
- 3資料のチェックリストで「無い」と見える項目（Dockerサンドボックス・イベントストリーム・
  ACI最適化）は、**作るべきものではなく、ランタイムから継承すべきもの**。継承の質
  （設定・穴の有無・劣化モードの正直さ）がプロセス層の責任範囲であり、第1部 P0 群
  （PowerShell穴・二重発火・フック修正）はまさにこの「接続層の品質」の指摘だった。
- 逆に、プロセス層に固有の責務（ゲートの完全性・レビュー経路の強制・**プロセス自体の
  効果測定**）はどのランタイムも提供しない。ここが「世界最高」の勝負どころである。

---

## 3. 参考資料の検証結果（15項目の一次情報突き合わせ）

3資料の引用の信頼性は高い。15項目中 10件完全確認・5件部分確認・捏造ゼロ。
主要な確認結果と補正:

| 主張 | 判定 | 補正・出典 |
|---|---|---|
| LangChain: ハーネス改良のみで Terminal-Bench 52.8→66.5（モデル固定） | ほぼ確認 | 日付は2026-02-17（資料の3月は誤り）。[langchain.com/blog/improving-deep-agents-with-harness-engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) |
| Meta「Agents Rule of Two」（[A]非信頼入力 [B]機密アクセス [C]状態変更/外部通信 のうち2つまで） | 確認 | 2025-10-31。[ai.meta.com/blog/practical-ai-agent-security](https://ai.meta.com/blog/practical-ai-agent-security/) |
| Arize: LLM判事は自分と同系統の出力を優遇（OpenAI +9.4 / Anthropic +4.27） | **要補正** | 数値は未較正のTest 1のみ。**人間キャリブレーション後（Test 2）は逆転**: OpenAI≈0、Anthropic **-7.4**（自分に厳しい）、真の自己バイアスはGoogleのみ。「判事は必ず自己優遇する」を根拠にした設計判断は不可 |
| Windsurf → Devin Desktop（Agent Command Center化・ACP対応） | 確認 | 2026-06-02。[cognition.com/blog/introducing-devin-desktop](https://cognition.com/blog/introducing-devin-desktop) |
| AGENTS.md を Linux Foundation（Agentic AI Foundation）へ寄贈・6万超採用 | 確認 | 2025-12。AGENTS.md 正の採用（D025）の追加の裏付け |
| Anthropic: サードパーティSkill/pluginセキュリティスキャン（Enterprise beta） | 確認 | **2026-08-06**（先週）。Skillサプライチェーンが実リスク認定された証左 |
| TraceCompiler（トレース→ほぼ決定的ワークフローへのコンパイル） | 確認 | arXiv 2608.02680（2026-08） |
| Cursor CVE-2026-22708（allowlist済みコマンドの環境汚染悪用） | 確認 | shellビルトインでenv汚染→allowlist済み `git branch` がペイロード運搬。**allowlist方式自体の限界**を示す実例 |
| hackerbot-claw（GitHub Actions悪用→LiteLLMのPyPIトークン窃取→バックドア版公開） | 確認 | 2026-02〜03。バックドア版 1.82.7/1.82.8 が実際に公開された |
| 計画研究「不適切な計画は計画なしより有害」 | ほぼ確認 | arXiv 2604.12147（trajectory数は現版21,120）。「A subpar plan hurts performance even more than no plan at all」は原文どおり |
| arXiv「AI Harness Engineering」P1-P5原則 | 確認 | 2605.13357。実在 |
| mini-SWE-agent が SWE-agent の推奨後継（bashのみ・線形履歴） | 確認 | 公式README。「ハーネス≠複雑さ」の反例として本物 |
| Anthropic: Opus 4.6 後に sprint分解を撤去 | ほぼ確認 | context reset の撤去は4.5世代で先行（時系列のみ補正）。「ハーネス要素はモデルの欠落の仮定をエンコードし、モデル改善で陳腐化する」は原文に忠実 |
| Claude Code ソース流出（2026-03-31・51.2万行） | 確認 | npmパッケージング事故（.npmignore漏れ）。一次声明は未発見（複数の独立報道で確認） |
| サンドボックスで承認プロンプト激減・承認率93% | 確認 | 削減は**84%**（公式）。93%はAnthropic自身のテレメトリ由来（独立調査ではない）。approval fatigue の実在の裏付け |

**Arize補正の設計への影響**: 「レビュワーは実装と別ベンダーのモデルにすべき」という
推奨は根拠が弱まった（較正後、Anthropic判事はむしろ自己に厳しい）。従って D040 の
「reviewer/spec-critic は inherit（強いモデル）」の維持は正しい。取り込むべきは
モデル分離ではなく**懐疑チューニング**（後述 N5）である。

---

## 4. 3資料の中核主張 × 本ハーネスの対応マップ

### 4a. 既に実装済み（3資料が追認する優位点）

| 3資料の主張 | 本ハーネスの実体 |
|---|---|
| 開発ループを明示的な状態機械にする（doc3の状態遷移図） | フェーズ×GATE_STATUS がまさに状態機械。差し戻し=Diagnose→Replan 遷移も定義済み |
| 作業者と評価者の分離（3資料すべて） | spec-critic×2 + reviewer（フレッシュコンテキスト・読み取り専用） |
| Bounded Autonomy（安全域内は自律・境界のみ承認。承認疲れ対策） | 全自動区間 + 破壊的操作のみ ask、の設計そのもの。承認率93%/削減84%のデータが裏付け |
| Progressive disclosure による Skill 管理 | D002 で採用済み。description設計指針も明文化済み |
| Context Reset + 構造化ハンドオフ（PLAN/STATE/FINDINGS） | docs/ + progress.md + learnings.md + セッション分割表 = 同型（名前が違うだけ） |
| 「新コンポーネントには観測済み失敗モードと評価が必要」（doc3の複雑化ルール） | **DECISIONS.md の運用そのもの**。全45決定が「観測された摩擦→対策」の対で記録されている。この規律を要求する資料はあるが、実装済みの公開例は無い |
| simple first / mini-SWE-agent 的抑制 | 会話型MA不採用（D034）・既定サブエージェント経路5つに限定・「全環境事前収録しない」方針 |
| 計画は不変の命令でなく作業仮説（計画研究） | 差分駆動の原則（承認後も変更部分だけ再ゲートして更新可能） |
| トークン/コストの計測 | effort-log（D040）。doc3のEfficiency評価面を個人規模で先取り |

### 4b. 第1部で提案済み（3資料が補強・精緻化するもの）

| 第1部の提案 | 3資料による補強・精緻化 |
|---|---|
| P2-1 タスク単位done契約 | **3資料すべてが最重要級で一致**（sprint contract / 機械的oracle束縛 / COMPLETE論理式）。優先度をP2→実質P1へ引き上げるべき |
| P2-3 サーキットブレーカ | doc3が精緻化: 回数でなく**retry（同戦略再試行）/ replan（仮説変更）/ escalate（自律範囲超え）の3状態を区別**し、「同一失敗署名の反復→replan昇格→進捗なし→人間へ」と規定する |
| P2-4 GATE_STATUS/tasks書き換え検知 | 3資料ともJSON化推し。ただし精密な脅威分析（§5）により折衷案維持+昇格条件つきが正 |
| P2-6 de-scaffolding常設問 | doc2/3の「ablation文化」（モデル更新ごとに要素を外して効果測定）。context reset撤去の実例つき |
| P0群（ガード穴・CI） | doc3のleast privilege表・policy gateway と一致。CVE-2026-22708 は「allowlist自体が攻撃面になる」実例で、パターン検査の限界（fail-open前提の多層化）を裏付け |
| P2-5 learnings/auto memory分担 | doc3が拡張: メモリには provenance（出典）・scope・鮮度/TTL の概念を持たせる |

### 4c. 新規に取り込むべきもの（第1部に無かった提案）

改善提案表（対象・問題・提案・根拠の4点セット。/90 適用可能形式）:

| # | 対象ファイル | 現状の問題 | 提案 | 根拠 |
|---|---|---|---|---|
| N1 | 新設: `tools/` + `.github/workflows/`（保護） | **ハーネス自体の効果を測る手段が無い**（内部監査G-2）。改善が「主張」ベースで、モデル更新時に何を外せるか（ablation）も判断材料が無い | **Golden Harness Eval の最小版**を新設: 小さな題材プロジェクト（要件メモ→リリースまで一式）を固定し、パイプライン通し実行の再現テストとする。中核KPIは doc2 Stage 0 の「**エージェントが完了と宣言した成果物のうち、決定論的検証を通る割合**」（目標90%+）。将来はP0-2のCIに接続 | 3資料すべて（Golden Repository Task Suite / Stage 0ベンチマーク / trajectory評価）。「世界最高」は測定なしに名乗れない |
| N2 | `gate-check` スキル・`implement.agent.md`（一部保護） | ループの終了・継続・中断条件が散文で、「成功停止」と「失敗停止」の区別が暗黙 | doc3 の論理式を明文化: COMPLETE = 受入基準×テスト×ゲート×成果物の全充足 / ESCALATE = 予算超過∨同一失敗反復∨進捗なし∨方針境界∨要件曖昧。P2-3の3状態（retry/replan/escalate）と一体で規定 | doc3 §Loop。「while not done」の構造的欠陥への確立された対処 |
| N3 | `skill-authoring`・`security-review` スキル・AGENTS.mdセキュリティ節（一部保護) | 外部Skill/公式Skill流用・MCP導入時の**サプライチェーン統制が指示レベルにも無い**（pin・ハッシュ・レビューの規定なし） | 外部Skill/プラグイン/MCPの導入手順に「出所確認・版のpin・導入前レビュー・最小権限」を必須化。Anthropicのスキャン機能（Enterprise beta）は利用可能なら有効化を案内 | hackerbot-claw実害（バックドア版がPyPIに公開済み）+ Anthropicが2026-08-06にスキャンを製品化 = 業界がリスク認定 |
| N4 | AGENTS.md セキュリティ節（保護） | MCP最小権限は記載済みだが、**判断の語彙**（何が揃うと危険か）が無い | Lethal Trifecta（私的データ×非信頼コンテンツ×外部送信）と Rule of Two（[A]非信頼入力 [B]機密 [C]状態変更/外部通信のうち2つまで）を、MCP接続・ブラウザ検証・Web検索を許可する際の判定基準として1段落追加 | Willison 2025-06 / Meta 2025-10-31（検証済み）。既存の「最小権限」を運用可能な判定規則にする |
| N5 | `reviewer.agent.md`・`spec-critic.agent.md`（保護） | レビュワーへの指示が「検出せよ」型で、LLM評価者の寛大バイアスへの対策が明示されていない | **懐疑チューニング**を明文化: 「反証を試みる」「合格がデフォルトではない」「客観チェック（テスト・ビルド）で判定できることを主観評価しない」。※別ベンダーモデル必須化は**採らない**（Arize較正後データで根拠が消滅。D040のinherit維持） | doc2/3 + Arize検証結果（§3）。Anthropic自身も「評価者は懐疑的にチューニングせよ」と明記 |
| N6 | `learnings_template`・`harness-retrospective` スキル（一部保護） | P2-5（auto memoryとの分担）に加え、メモリ項目の**出典・鮮度**の概念が無い（古い教訓が失効しても気づけない） | learnings の各行は日付済み（現状OK）。振り返りに「learnings棚卸し: 失効した教訓の削除・昇格（スキル化）」を1項目追加 | doc3 Memory governance（provenance/scope/TTL）。TraceCompiler の方向性（手順の資産化）とも整合 |

### 4d. 根拠つき不採用（導入トリガー条件を記録する）

| 項目 | 判断 | 導入トリガー条件 |
|---|---|---|
| Graph Engineering（ASTコードグラフ） | 不採用 | doc3自身が「repository localization failure が主要失敗になった段階で追加」と規定。本ハーネスの対象規模（小〜中アプリ）では agentic search で足りる。**brownfield大規模案件で「対象ファイルを見つけられない」失敗が実測されたら** AST/LSP→依存グラフの順で検討（Aider型=Contextランカーとしての限定利用から） |
| イベントストリーム/OTelの自前実装 | 不採用 | ランタイム層の責務（Claude Code/CodexはOTel export対応済み）。D040に記載済みの「組織規模で必要になったら公式OTelメトリクスへ移行」を維持 |
| ACI（LLM専用ツールインターフェース）の自前実装 | 不採用 | ランタイム層の責務。プラットフォームのツール群を使う戦略（D025）が Anthropic「build on tools Claude already knows」と一致 |
| GATE_STATUS/tasksの全面JSON化 | 不採用（折衷維持） | 精密な脅威分析: ゲート遷移は人間承認が挟まるため上流は守られている。**露出しているのは全自動区間の tasks.md 完了マークとテストファイル**のみ→ P2-1（done契約）+ P2-2（テスト削除検知）+ P2-4（完了マーク変更のフック検知）で塞ぐ方が、docs-as-memoryの人間可読性を保てる。**完了マークの改竄が実測されたら** passesフラグのみJSON分離へ昇格 |
| 会話型マルチエージェント・常設5〜10役割 | 不採用（D034維持） | 3資料とも「過剰」分類。doc3「同一Context から独立レビュワーを大量生成するのは coordination cost だけ増やす」。条件はD034記載のまま |
| 毎ステップreflection・LLM判事のみの品質ゲート | 不採用 | doc3「過剰」分類 + ICLR 2024（外部フィードバックなしの自己修正は無効〜有害）。本ハーネスは外部oracle（テスト）主体で設計済み |

---

## 5. 外部チェックリストによる採点（deep-research-report 100点法）

最も厳密な doc3 の基準（各項目0-4点。4=計測・強制・回帰検知まで）で正直に採点した。
「現状」= 本リポジトリの現在の実装状態。「適用後」= 第1部P0〜P2+本レポートN1〜N6を
全適用した場合の見込み。

| カテゴリ | 現状 | 適用後見込み | 主な失点源（現状） |
|---|---:|---:|---|
| Context / Repository（20） | 16 | 17 | メモリのprovenance/分担未定義(2)・AGENTS.md常時ロードの肥大傾向(3) |
| Loop / State（20） | 13 | 18 | 停止条件・予算上限なし(1)・retry/replan未区別(2) |
| Validation / Evaluation（20） | 10 | 16 | **Golden Eval なし(0)・regression比較なし(1)**・タスク単位oracle未強制(3) |
| Skill / Agent / Tool（12） | 10 | 11 | Skillのtrigger test/ownerなし(2) |
| Security / Governance（16） | 8 | 12 | サプライチェーン統制なし(1)・ガード実穴(2)・ネイティブWindowsはサンドボックスなし(2) |
| Observability / Operations（12） | 7 | 9 | 単一トレース相関なし(2)・本体CIなし(2) |
| **合計** | **64** | **83** | |

- 現状64点は doc3 基準で「実用可能だが、長時間・安全性・評価のどこかに大きな穴」帯。
  さらに doc3 のゲート規則（Security 8/16未満 または Validation 10/20未満なら総合点に
  かかわらず高自律と評価しない）に対し、**両方ともちょうど失格ライン上**にいる。
- ただし §2 の2層構造の補正が必要: サンドボックス等はランタイム継承項目であり、
  Copilot cloud agent 実行や WSL2 運用では Security +1〜2 相当。プロセス層固有の
  失点は「Golden Eval(0)」「停止条件(1)」「サプライチェーン(1)」に集中している。
- **重要な符合**: 採点で最も伸びる場所（Validation +6・Loop +5・Security +4）が、
  第1部P0/P2群+N1/N2/N3 とちょうど一致する。外部の評価枠組みからも提案の優先順位が
  裏付けられた。

（参考: doc1 の100点チェックリストはランタイム項目比重が更に高く、本ハーネス単体への
適用は不適。doc2 の Stage 0-3 ロードマップでは、本ハーネスは Stage 0 の大半と
Stage 2 の骨格を満たすが、Stage 0 の計測KPI「完了宣言の検証通過率」と Stage 1 の
「標準化eval」が未達 — doc3 採点と同じ結論になる。）

---

## 6. 結論: 優先順位の最終形

3資料の分析により、第1部の優先順位を次のとおり更新する（適用はすべて未実施・要指示）。

1. **[最優先] 機械ガードの実穴と本体CI**（第1部P0群） — doc3「Execution boundaryを
   固めるのが最優先」と一致。PowerShell穴は即修正級
2. **[最優先へ昇格] タスク単位の検証とループ制御**（P2-1 done契約 + P2-3/N2 の
   3状態・論理式化） — 3資料が一致して最重要とする「正しく終われる」能力。
   作られるアプリの品質に最も直結する
3. **[新設・高] Golden Harness Eval 最小版**（N1） — 「世界最高」の主張を測定に変える。
   以後のすべての改善（モデル更新時のablation含む）の判断基盤になる
4. **[高] 鮮度更新**（P1群: Copilotフック活用・課金/計測記述・改名対応）
5. **[中] セキュリティ語彙とサプライチェーン**（N3/N4） — 実害事例が出た領域
6. **[中] レビュワー懐疑化・メモリ統治・その他**（N5/N6・P2-5〜P2-7）
7. **[方針決定] P3群**（prompts→skills移行・constitution分離等）は個別に計画

本ハーネスの核（文書媒介の状態機械・独立レビュー・Bounded Autonomy・成長ループ・
DECISIONS.md の「失敗モード⇔コンポーネント」規律）は、3資料の推奨アーキテクチャと
高い精度で一致しており、複数の点で公開実践の先を行く。残る本質的ギャップは
「**タスク単位で機械的に完了を証明する力**」と「**ハーネス自身を測る力**」の2つであり、
これが次の改修サイクルの中心テーマである。

---

## 7. 出典

- 参考資料3本: `参考資料（ハーネス調査）/` 配下（検証結果は§3のとおり。引用の信頼性は
  高いが、Arizeの件のみ結論に影響する省略があった）
- 検証で確認した一次情報: [Meta Rule of Two](https://ai.meta.com/blog/practical-ai-agent-security/) /
  [Arize self-bias実験](https://arize.com/blog/should-i-use-the-same-llm-for-my-eval-as-my-agent-testing-self-evaluation-bias/) /
  [LangChain harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) /
  [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) /
  [Anthropic sandboxing/封じ込め](https://www.anthropic.com/engineering/how-we-contain-claude) /
  [Devin Desktop](https://cognition.com/blog/introducing-devin-desktop) /
  [計画遵守研究 arXiv 2604.12147](https://arxiv.org/abs/2604.12147) /
  [AI Harness Engineering arXiv 2605.13357](https://arxiv.org/abs/2605.13357) /
  [TraceCompiler arXiv 2608.02680](https://arxiv.org/abs/2608.02680) /
  [Cursor CVE-2026-22708](https://github.com/cursor/cursor/security/advisories/GHSA-82wg-qcm4-fp2w) /
  [hackerbot-claw](https://www.stepsecurity.io/blog/hackerbot-claw-github-actions-exploitation) /
  [SWE-agent/mini-SWE-agent](https://github.com/SWE-agent/SWE-agent) /
  [Anthropic Skillスキャン release notes](https://support.claude.com/en/articles/12138966-release-notes)
- 第1部レポート: [retrospective-harness-audit-2026-08-11.md](retrospective-harness-audit-2026-08-11.md)
