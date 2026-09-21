---
name: deploy-local-zip
description: ホスティング先を持たず、Gitタグとgit archiveによるZIP配布で完結するローカル実行アプリのリリース手順テンプレート。実行権限の罠(core.filemode=false)、CIが生成した実物ZIPの検査(gh run download)、PRとCI起動の確認、タグ打ち直し禁止のロールバック、更新経路(2回目以降のインストール)を含む。environment.mdのデプロイ先が「なし・ZIP配布」のときにリリースエージェントが使う。
---

このスキルの本文の正は `.github/skills/deploy-local-zip/SKILL.md` です。
それを読み、その手順・チェックリストに従ってください(このファイルはClaude Codeが
`.claude/skills/` しか探索しないために置いてある薄いポインタです)。
