# Untangle

スタッフが過去に送ったメールから判断を全部取り出し、決裁者が決まりにして業務を譲渡する。
個人店・零細企業の決裁者が培ってきた判断を、信頼のおける形で引き出し、共有知にするサービス。
第5回 Agentic AI Hackathon with Google Cloud 提出作品。

![構成図](docs/architecture.png)

- 公開URL（デモ・架空データ）: https://untangle-63500257813.asia-northeast1.run.app
- 説明文（提出用の下書き）: [docs/submission.md](docs/submission.md)

## 流れ

1. Gmail の送ったメールをブラウザの中で読み、ひな形から外れた文（スタッフが判断して書き足した文）だけを取り出す
2. 名前や番号をブラウザの中で伏せる（規則と日本語の辞書。AIを使わない）。伏せ残りの疑いがある行は送らない
3. サーバーでもう一度検査し、伏せた後の文だけを Gemini に渡して、判断のテーマに分ける
4. 決裁者は4つの段を進める: 分類を確認する → 判断を確認する → 例外を確認する（決まりと違う過去の返事）→ 完了（マニュアル）
5. 全テーマが完了すると「業務を譲渡できます」。巡回エージェント（ADK）が、譲渡までにやることを洗い出す

公開URLは、架空の店のメール98通を途中まで進めた状態で開きます。自分の Gmail で試すときは末尾に `?real=1` を付けます。

## 開発

```
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
npm install && npm test
```

デモ用データはすべて架空です（`untangle/static/mail-scan-demo.js`・`untangle/seed.py`）。
