---
name: harness-stats
description: 現在のセッションの受領書(host 推定費用・文脈使用率・累計トークン・委譲・GATE 遷移・tests・基準線比較)を任意の時点で表示する。利用者が /harness-stats で呼ぶ専用(モデルからの自動呼び出しは無効)。数値の正は .github/hooks/logs/usage の受領書で、集計・表示は tools/session-receipt.py
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash(python tools/session-receipt.py:*), Bash(python3 tools/session-receipt.py:*)
---

<!-- generated from .github/skills/harness-stats/SKILL.md (本文に !`cmd` を含むため本文を転写。編集は正の側で行い python tools/generate-adapters.py で再生成する) -->

# harness-stats（会話ごとの受領書の任意時点表示）

`/harness-stats` で、いま進行中のセッションの受領書を表示する。Stop フック（`log-effort.py`）は
区切りのターンにしか 3 行の受領書を出さないため、任意の時点で見たいときはこのスキルを使う。
集計・レンダリングの正は `tools/session-receipt.py`（フック・/99-status と同じ関数）。

## 現在のセッションの受領書

!`python tools/session-receipt.py --session current`

上が空、または「受領書がまだありません」のときだけ、次を実行して結果をそのまま表示する
（`python` が無い環境は `python3`）:

```
python tools/session-receipt.py --session current
```

## 表示後にすること（短く）

- 出所タグの意味を 1 行で添える: `[公式]` = statusline の stdin JSON（host 推定 $・文脈%・行数。
  list 価格の推定で請求額ではない）、`[transcript]` = 累計トークン（非公式形式・解析率付き）、
  `[推定]` = `tools/prices.json` による自前推定。tests の pass は golden-eval / check.py の
  決定論検証のときだけ。
- 「p90 超」「文脈 ≥ 閾値」（閾値の正は `tools/usage-config.json`）が出ていれば、
  `.github/harness/USAGE.md` のセッション分割表に従い、次のゲートで新しいチャットに切る提案を 1 行で添える。
- **数値を自分で推定・補完しない。** 受領書に無い値は「n/a（未取得）」のまま伝える
  （`host n/a` は statusline 未配線、`基準線 n/a` は `python tools/effort-report.py` 未実行）。
- `current` は「最終更新の受領書」を指す。並行セッションがあると別セッションを指すことがあるため、
  表示の `sess` 先頭 8 文字が違うときは `--session <session_id>` を案内する。
