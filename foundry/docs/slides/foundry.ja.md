---
marp: true
theme: default
paginate: true
---

# foundry

一人の責任者のもとで、多くの作業単位を並行して進める
Claude Code プラグイン。

全体像は `docs/concepts.md` にある。

---

## 1. 課題

- 一人に、多くのチケット、多くのリポジトリ、多くの Claude セッション。
- セッションはルールを忘れ、際限なく再試行し、ポーリングで待ち、何でも人に聞く。
- 本番、秘密情報、夜間の push には人が要る。それ以外は要らないはず。

foundry は人を判断に集中させ、残りをセッションに任せる。

---

## 2. 連鎖

```
owner ──チャット──> foreman ──launch/resume──> line-lead (unit ごとに 1)
                      │                   └─> shop-boss (repo ごとに 1)
                      │                          │
                      └─ dispatcher, judge       └─Agent─> hands (light / 通常 / heavy)
```

- owner は foreman とだけ話す。
- lead はそれぞれ独立した `--bg` セッション。lead 同士は話さない。
- 連鎖を飛ばす呼び出し(owner→lead、foreman→hand)は hook が拒否する。

---

## 3. ルールは記憶ではなく hook に

- PreToolUse、PermissionRequest、SessionStart、Stop などの hook が、連鎖、
  git の方針、Slack の静音時間と重複防止、外向けテキストのフッターを強制する。
- プロンプトにだけあるルールは願い、hook は保証。
- 拒否には正しいコマンドが添えられ、モデルは正しい形で一度だけやり直す。

---

## 4. 段階的な承認

```
tool 呼び出し ─> auto mode 分類器 ─拒否─> 確定 (triage: 代替 / 再試行 / owner カード)
                    │
                    └─ask ルール─> tier 0 ルール ─> tier 1 台帳 ─> tier 2 foreman (+advisor)
                                       └─ owner 専用 ──────────────> tier 3 owner
```

- owner 専用: 本番、秘密情報とログイン、設定、支払い、公開。
- tier 3 はボードにカードで出る: 理由、根拠、advisor のメモ、allow / deny。

---

## 5. 状態は一本の journal

- 事実はすべて `journal.jsonl` に一行ずつ追記され、snapshot はそこから組み直す。
- unit、待ち、決定、承認、PR、デプロイ: すべて同じログの reducer。
- 大事なことはモデルの文脈だけに置かない: `/clear` で何も失わない。

---

## 6. モデルなしで待つ

```
stream スクリプト (PR/CI、セッション、PC、同期、詰まり) ──変化時に一行──> foreman (Monitor)
cron (引き継ぎ、preflight、受付) ──────────────────────────────────────────> foreman
```

- 見張るのはスクリプト。モデルは一行が来たときだけ起きる。
- lead は 30 分までは Monitor で待ち、それより長い待ちは lead を止めてイベントで再開する。

---

## 7. ボード

- サイドペイン(`/foundry-board`)、テキストのボード、`board.html`。
- 状態別・リポジトリ別の unit、決定カード、承認カード、詰まり、PR 在庫、
  PC 負荷、朝の引き継ぎ。
- ボタンはすべて `foundry ... --by owner` を実行する。ペイン自身は何も書かない。
- 画面の文言は英語・日本語・韓国語(`prefs ui.language`)。

---

## 8. 夜と朝

- 夜の時間帯(既定 22:00–08:00): push しない、Slack 送信は待つ、toast は出さない。
- 夜の作業は dev デプロイとその検証で止まる。
- 08:00 の引き継ぎ: 終わったこと、未決の決定、詰まり、PR の変化、コスト。
- 静音時間を外せるのは owner の明示的な「今送って」だけで、一チャンネル分。

---

## 9. 安全

- 整理は自分で再確認したものだけに手を出す: マージ済みかクローズ済みの PR、
  汚れのない worktree、未 push なし、中で動くプロセスなし。それ以外は提案になる。
- 本番は owner のもの: 本番デプロイにはカード上の owner の指示が要る。
- マージはレビューした head コミットに固定する。

---

## 10. インストールと起動

```sh
sh <marketplace>/foundry/install.sh
foundry init --repo app=~/src/app --tracker none
foundry foreman start        # foreman が既に動いていれば拒否
claude attach foreman        # 最初のプロンプト: foundry up
```

`foundry foreman line` は手で起動するための同じ起動行を出力する。

---

## 11. 現状と限界

- 一人が一台のマシンで毎日使っている。他の人・他のマシン向けに一般化済み。
- トラッカー: Jira、Linear、GitHub Issues、またはなし。
- journal に書かれたテキストは書かれたときの言語のまま残る。
- `--bg` セッション、Monitor、mod API を持つ Claude Code が必要。

既知の限界は `docs/oss-readiness.md` を参照。
