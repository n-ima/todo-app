<!-- HARNESS_ORIGIN
path: D:/vscode-worspace/CreateAppl
version: D098
synced: 2026-09-23
source_url: https://github.com/n-ima/copilot-sdlc-harness.git
source_commit: 2cee3e7974bb7b8c1e302115419e1ea273622906
archive_sha256: 8f53e70d7d8378791aecdc0d2c7bec8c0bab3ea0871bf46aa3177f99af289f86
synced_at: 2026-09-23T16:54:09+0900
latest_decision: D098
route: sync
-->

# ハーネス本体の場所と配布鮮度(自動記録)

このプロジェクトのハーネスは `D:/vscode-worspace/CreateAppl` からコピー/同期された(D098 まで・2026-09-23・経路 sync)。
「ハーネスを更新して」の依頼(/91-sync-from-harness)ではこのパスが既定の本体として使われ、
SessionStart フック(inject-progress)と `python tools/doctor.py` は `latest_decision` と本体の
DECISIONS.md の最新 D 番号の差で「/91 を先に実行」を案内する。`tools/sync-harness.py --apply` の
たびに自動更新されるため、手で編集しない(本体を移動した場合は次回 `--harness` で明示するか
`python tools/doctor.py --write-origin --harness <本体パス> --force` で書き直す)。

配布元の同一性: `source_commit` は本体の HEAD(`-dirty` は未コミットの変更を含む作業ツリーからの
同期)、`archive_sha256` は本体の `git archive --format=tar HEAD` の SHA-256、`latest_decision` は
本体 DECISIONS.md の最大 D 番号。`python tools/sync-harness.py --verify` で本体の現在の HEAD と
照合できる(0 = 一致 / 1 = 不一致 / 2 = 照合不能)。
