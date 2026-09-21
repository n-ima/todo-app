# CONTRIBUTING.md — ハーネス自体の開発の入口

このファイルは **ハーネス（テンプレート）自体** を改修する人向けです。
テンプレートから作られたプロジェクトでのアプリ開発には適用されません
（そちらは `AGENTS.md` と各フェーズコマンドが正）。

## 変更前に

1. **`DECISIONS.md` を必ず読む**。ハーネス自体の設計判断ログです。同じ議論を
   繰り返したり、一度直したバグを再導入したりしないよう、変更対象に関係する
   D 記録がないか確認してください。
2. **保護対象の編集は保守モード経由**。`AGENTS.md`・フック・deny 設定などの
   保護対象は、通常セッションでは機械的にブロックされます。現行方式は
   「エージェントが検証済みの適用スクリプトを用意し、人間が保守モードで実行」です:

   ```bash
   python tools/harness-maintenance.py --on --apply    # 保守モードへ（ガード解除）
   # …検証済みの適用スクリプトを実行…
   python tools/harness-maintenance.py --off --apply   # 必ず通常モードへ戻す
   ```

3. **挙動の正は1か所**（`AGENTS.md` + `.github/`）。各環境のアダプタ
   （`.claude/commands/` 等）は手で編集せず、`python tools/generate-adapters.py`
   で再生成します。

## 変更後に（検証スイート）

CI（`.github/workflows/harness-ci.yml`）と同じ検査をローカルでも実行してください。
**検査項目の正は `harness-ci.yml` の実ステップです**（この一覧と食い違ったら CI 側が正）:

```bash
python tools/validate-harness.py                      # 構造検証（フロントマター・アダプタ整合・フック配線）
bash .github/hooks/scripts/selftest.sh                # フック自己テスト（ガード判定の回帰）
python .github/hooks/scripts/log-effort.py --selftest # 計測ロガー自己テスト
python tools/golden-eval.py --selftest                # Golden Eval 自己テスト
python tools/trace-check.py --selftest                # トレーサビリティ検査の自己テスト
python tools/sync-harness.py --selftest               # 逆同期ツール自己テスト（本体判定・配布除外・入れ子拒否）
python tools/intake-app.py --selftest                 # 取り込みツール自己テスト（本体判定・テンプレート除外・入れ子拒否）
python tools/e2e-run.py --selftest                    # 評価装置 v2 の自己テスト（計画・分類・判定・妥当性条件。claude は起動しない）
python evaluation/tasks/todo-cli/check.py --selftest  # 決定論チェッカー自己テスト（todo-cli）
python evaluation/tasks/expense-webapp/check.py --selftest  # 同（expense-webapp。矛盾解消跡の負例つき）
python tools/eval-report.py --selftest                # 評価集計（backfill・妥当性検査・REPORT 生成・Fisher/Wilson）の自己テスト
python tools/freshness-lint.py                        # 説明文書の鮮度語と一次データの乖離（WARN 表示のみ。--selftest あり。R-06）
python tools/release-tag.py --check                   # CHANGELOG の版と annotated tag の対応（--selftest あり。A7-M-1）
python tools/host-canary.py --selftest                # ホスト版・ビルトイン依存カナリアの自己テスト（実走は claude 導入後。A3-6 / A5-8）
```

Windows（PowerShell）では、`.ps1` 版フックの自己テストも実行してください
（CI の windows-latest ジョブと同じ検査）:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .github/hooks/scripts/selftest.ps1
```

ファイル規約: `.sh` は LF・`.ps1` は BOM 付き UTF-8 で保存してください
（CI の windows-latest ジョブが機械検査します）。

## 提案の登録

監査・振り返り・外部レビューで出た改善提案は **`audits/PROPOSALS.md`** に
登録してください（採否未定でも open で登録。状態の更新は
`/90-apply-retrospective` が行います）。採否の判断と根拠は `DECISIONS.md` に
D 記録として残します。

## その他

- 変更は main への直接コミットではなく、ブランチ + PR を推奨します。
- 脆弱性（ガードのすり抜け等）を見つけた場合は `SECURITY.md` の窓口へ。

### Python の版

CI（`harness-ci.yml`）は **Python 3.11** で走る。開発機が 3.12 以降でも、3.12 限定構文（同一引用符を入れ子にした
f-string＝PEP 701 など）を使うと CI の selftest だけが落ちる（2026-09-10 に `tools/session-receipt.py` で実際に発生）。
3.11 が入っていれば `py -3.11 -m compileall -q -f tools .github/hooks/scripts evaluation` で事前に検出できる。
