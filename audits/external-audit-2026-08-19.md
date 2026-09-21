# 外部監査 2026-08-19（独立セッションによる総合監査）

- 実施: ハーネス作成セッションとは独立した Claude Code セッション（25エージェント並列: 精読6・業界動向調査4・分析3・敵対的検証12）
- 対象: リポジトリ全体（約180ファイル）+ 2026年8月時点の業界動向（Anthropic公式・Spec Kit・BMAD v6・Kiro・OpenSpec・GSD・Ralph系ループエンジニアリング）
- 位置づけ: 過去2回の内部監査（2026-08-11 / 08-12）に対する外部検証。重要指摘12件は実ファイルとの突き合わせで反証を試みた上で採録（CONFIRMED / 要調整の別を明記）
- 総評: **設計思想は世界トップ群、実装は7割、検証は4割**。指摘の大半は「ハーネス自身が説く原則（正の一本化・指示よりHooks・段階的開示・docs-as-memory・証拠なき完了宣言の禁止）を自分自身に適用しきれていない」型であり、思想の修正は不要。必要なのは自己適用の徹底と実機検証。

## 主要な発見（検証済み）

1. **CRITICAL: プロンプト層がエージェント定義を無効化** — 06-implement-task.prompt.md が直接実装を指示し task-worker 委譲設計を後勝ちで上書き。02/03/08 も同型の太った再記述。さらにアダプタ変換が send:true/false を区別せず一律「新セッションで実行」に変換するため、implement→test の自動ハンドオフが Claude Code / Antigravity で消滅。「実装〜テスト全自動」が3環境中2環境で機能しない。
2. **CRITICAL: E2E通し実行ゼロのままKPIが形骸化** — golden-eval はテンプレート素コピーで全フェーズ通過（WARN正規表現がテンプレート見出しにマッチ・release未チェック[ ]未検査・GATE_STATUSキー破損で空dict→全SKIP exit 0）。
3. **受付ルーチンの契約ドリフト（CONFIRMED）** — 「案内で止めてよい」条件が実質4通り（AGENTS.md 2箇所=無条件 / request-routingスキル=短セッション例外つき / README=環境無限定の過大表現 / route-request.sh注入文=例外なし）。機械層の適用範囲が AGENTS.md（Copilotに届く）と route-request.sh ヘッダ（Claude Code専用・D043）で真逆のまま両論併記（D046再検証後のヘッダ・README更新漏れ）。
4. **停止の4構造要因 =「放置で進まない」の正体** — (a) 新規チャット冒頭でも「新しいチャットを開け」と案内し得る矛盾停止（3の帰結）、(b) 分割4条件の非機械化（「劣化を感じたら」は劣化した本人には検知できない自己矛盾。CONFIRMED）、(c) 案内停止後の再起動経路が人間のコピペのみ、(d) 停止（案内・承認待ち・エスカレーション）の通知チャネルがゼロ（CONFIRMED）。
5. **運用中判定の三者矛盾（要調整で採録）** — brownfield-intake 手順6は test=in_progress を許容したまま手順7で「運用中。次は/12」と書かせるが、request-routing は /12 を全done限定とし構築中は差し戻しへ振る。導入直後の最初の依頼で振り分けが分裂。
6. **sync-harness.py の looks_like_harness() コピー間ドリフト** — intake-app.py 側の修正（intake-report.md 条件）が未反映で、/11 未完了プロジェクトへの再同期を誤拒否。SYNC_GLOBS の欠落（.github/instructions/・.gitignore・docs/各README）で経路A/Bの配布物が非対称。
7. **自己権限昇格ガードの穴4系統（実測確認）** — .claude/settings.local.json が deny/ガード両対象外（permissions.allow 書き込みで確認無効化可能）・.claude/skills/ の中核スキル（request-routing/gate-check）無防備・guard-dangerous-git が Remove-Item -Recurse -Force 素通し・guard-secret-leak が sk-ant- / github_pat_ 未検知。
8. **注入系の静かな全損（実測確認）** — inject-progress.sh / route-request.sh の JSON 生成がバックスラッシュ未エスケープ（教訓に Windows パス1つで注入全損）。inject-progress.ps1 が Get-Content エンコーディング未指定で Windows 経路の教訓注入が全損。CI が ubuntu のみで .ps1 系は一度も実行されず、selftest は「クラッシュ沈黙」と「正常allow」を区別できない。
9. **ループ制御の外部化漏れ** — retry/replan の失敗カウンタ・失敗署名が会話内にのみ存在（tasks.md に記録欄なし）。done 契約の編集時フック検知（両監査合意の P2-4）が未実装。コスト建てサーキットブレーカーなし。事故的停止（in_progress のまま無言でターン終了）を拾う有界 Stop 番犬なし。
10. **業界動向とのギャップ** — セッション境界が「ユーザーに新チャットを開かせる」BMAD式（確立された手法だが、潮流は GSD / Anthropic initializer-coder 式のハーネス駆動自動 fresh context）。scale-adaptive ファストパスなし（「小タスクに33分+2,577行のmarkdown」批判に直撃）。spec drift 後の再収束（re-converge）入口が未定義（業界最頻の失敗モード）。

## 改善提案表（対象ファイル・問題・提案・根拠）

| # | 優先 | 対象ファイル | 問題 | 提案 | 根拠 |
|---|---|---|---|---|---|
| E1 | P0 | .github/prompts/06,08,02,03 | エージェント定義の手順を太く再記述し中核設計（task-worker委譲等）を上書き | 04/05型の薄い参照（「エージェントのステップN〜Mを実行」）に統一 | 実ファイル確認。等価性ルール（AGENTS.md）自身への違反 |
| E2 | P0 | tools/generate-adapters.py | send:true/false を区別せず一律「新セッションで実行」に変換 | send:true は「同一セッション内でロール切替続行」に二分 | ノンストップ設計（D020）の他環境喪失 |
| E3 | P0 | AGENTS.md / README.md / route-request.sh | 受付ルーチン契約が4通りにドリフト。機械層適用範囲がヘッダと真逆 | 短セッション例外を AGENTS.md 2箇所へ昇格。README を環境別に正確化。route-request.sh ヘッダを D046 の結論に更新し注入文言を環境非依存化 | 検証済みCONFIRMED。新規チャット冒頭の矛盾停止を排除 |
| E4 | P0 | route-request.sh | 全done判定が固定文字列grepで行順序・注記・空白に脆弱 | フェーズ値を個別抽出し全done判定。運用中判定を「全done または運用中注記」の論理和に | /12誘導の静かな消失は受付の信頼性を直撃 |
| E5 | P1 | inject-progress.sh / route-request.sh / inject-progress.ps1 | JSON生成のバックスラッシュ未エスケープ・ps1のエンコーディング未指定 | JSON組み立てをpythonに置換。Get-Content -Encoding UTF8。selftestに回帰ケース | 実測で注入全損を確認。中核機構の静かな死 |
| E6 | P1 | .github/workflows/harness-ci.yml / selftest.sh | CIがubuntuのみ・.ps1未実行・沈黙PASS | windowsジョブ追加・BOM/LF機械検査・allowケースのexit code検証とJSON妥当性検証 | inject-progress.ps1バグがすり抜けた実害の証明 |
| E7 | P1 | .claude/settings.json / guard-harness-config-edit / guard-dangerous-git / guard-secret-leak | 自己昇格ガードの穴4系統 | settings.local.json を deny+ガード両方に追加。中核スキル保護。PowerShell削除パターン追加。sk-ant-/github_pat_ 追加。各修正に selftest 併設 | 実測で素通しを確認 |
| E8 | P1 | tools/sync-harness.py / intake-app.py | looks_like_harness() ドリフト・SYNC_GLOBS欠落・保守モード中配布 | intake-report.md 条件を統一。欠落グロブ補完。settings.json.locked 存在時は同期拒否 | brownfield 導入直後に確実に踏む |
| E9 | P1 | tools/golden-eval.py | テンプレ素コピー全通過・release未チェック未検査・空dict静かに通過 | 定型文除外・release検査追加・キー0件をNG化・docstring修正 | KPI「機械検証通過率90%」の形骸化防止 |
| E10 | P1 | .github/skills/request-routing / gate-check / brownfield-intake / change-request | 運用中判定の三者矛盾・読み取り専用質問の受け皿なし・構築中HFの矛盾 | 運用中= 全done∨運用中注記 に3箇所統一。「質問」分類を追加。構築中HFは該当フェーズで止血に修正 | 検証済み（要調整反映） |
| E11 | P1 | .github/skills/brownfield-intake | 既存テストのベースライン実行なし・経路Bの基準コミットなし | テスト1回実行と結果記録を必須化。配線確認後のベースラインコミット追加 | testゲート初期値が推測になる |
| E12 | P1 | （新規）notify フック | 停止（案内・承認待ち・エスカレーション）が全て不可視 | Notification イベントのローカル通知フック（音）を追加 | CONFIRMED。「放置で進まない」の体感の直接原因 |
| E13 | P2 | （新規）warn-gate-tamper フック | GATE_STATUS/tasks.md [x] の書き換えを検知するフックなし（P2-4未実装） | PostToolUse で GATE_STATUS 差分・[x] 増加を warn する非ブロッキングフック | 両監査合意の未消化項目。早すぎる完了宣言は業界最大の失敗モード |
| E14 | P2 | docs/03-implementation/tasks_template.md / implement.agent.md | 失敗カウンタ・失敗署名が会話内のみ | tasks.md に試行記録欄を追加し、呼び出し前の読み戻しを明記。一時的要因の判定目安とコスト条項を追記 | docs-as-memory 原則の適用漏れ（検証済み） |
| E15 | P2 | （新規・opt-in）watchdog-continue | 事故的停止（in_progress のまま無言終了）を拾えない | 有界 Stop 番犬（stop_hook_active 検査・反復上限・既定は未配線の opt-in） | 8/11監査の「無限ループ不採用」と両立する有界版 |
| E16 | P2 | remind-record.py | 「1回だけブロック」がターン毎にリセットされ毎ターン再ブロック | セッションIDマーカーで再ブロック抑止。領域分類をルート相対前方一致に修正 | 実測確認。コンテキスト税 |
| E17 | P2 | AGENTS.md | 38.7KB 常駐（段階的開示の自己適用欠如）。保護リスト等の多重記述 | 憲法部分（目安1/3）へ圧縮しポインタ化 | 本監査では見送り（別サイクルで慎重に実施すべき大規模改稿） |
| E18 | P2 | USAGE.md ほか | 分割表と切替条件1の実質衝突・前方参照の誤り・/90行欠落 | 表内注記・参照修正・行追加 | 精読で確認 |
| E19 | P3 | （設計検討） | scale-adaptive ファストパスなし / worktree 並列なし / docs乖離後の再収束入口なし / 承認の非同期転送なし | 次サイクルの設計検討として D 記録から開始 | 2026年潮流（BMAD Level 0-4 / Kiro Quick Spec / GSD / HumanLayer） |
| E20 | P0 | （運用） | 実エージェント駆動のE2E通し実行が未実施 | 固定題材での通し実行を期限つきマイルストーンとして D 記録（本監査ではツール層の機械E2Eのみ実施） | 受付矛盾・ハンドオフ劣化・注入死は通し実行1回で全て顕在化する |

## 業界動向の要点（2026-08 時点）

- Anthropic 公式: auto-compact / microcompaction が既定。長時間走行は「initializer/coder + progress ファイル + feature list JSON + git 規律」でハーネスが自動的にフレッシュコンテキストを開始する設計が公式解。「フェーズ境界の意図的リセット」自体は今も公式推奨（本ハーネスの思想は正しい。実現方法の自動化が課題）。
- Claude Code は AGENTS.md を現在もネイティブ読み込みしない（Issue #6235）。CLAUDE.md → @AGENTS.md は公式推奨ワークアラウンドで D025 の前提は現時点で正しい。ただし AGENTS.md は Linux Foundation（AAIF）管理の事実上の標準（6万+リポジトリ）であり、対応された瞬間にアダプタ層の大半が不要になるため前提の鮮度監視が必要。
- セッション境界: BMAD は「新チャットをユーザーに開かせる」方式を明示採用（本ハーネスと同方式・確立された手法）。GSD（59K stars）はサブエージェントで自動 fresh context。潮流は後者。
- ループ: Ralph Wiggum は公式プラグイン化（Stop hook + --max-iterations + 完了合言葉）。完了判定は「エージェントが書き換えられない外部ファイル + 決定論的検証をハードゲート、LLM検証者は助言層」の二層が標準論。「早すぎる完了宣言ループ」が最大の失敗モード。
- 確認待ち問題の業界解: 権限の事前設計 → 通知フック → 承認の非同期転送（--permission-prompt-tool + MCP / HumanLayer 型 approval-as-API）で「止まらずに待つ」設計へ。
