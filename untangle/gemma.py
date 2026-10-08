"""Gemma（自分の環境で動かす公開モデル）で、辞書と規則で拾えなかった固有名詞を見つける。

生の文を見るのはこの Gemma だけ。外部の Gemini には伏せた後の文しか渡さない。
Gemma は同じコンテナの中の Ollama で動かす（生の文がサービスの外に出ない）。

Gemma には「どこか」ではなく「何という語か」だけを答えさせ、位置はプログラムが探す
（小さなモデルに文字位置を数えさせると外れるため）。
判断に要る語（先生・生徒・38名 など）は消さない。テーマの語彙を守る語として渡す。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

OLLAMA_URL = os.environ.get("UNTANGLE_OLLAMA_URL", "http://127.0.0.1:11434")
GEMMA_MODEL = os.environ.get("UNTANGLE_GEMMA_MODEL", "gemma3:1b")


# 答えは名前の一覧だけにする（CPU では生成が遅いので、出力を短くするほど速い）
SCHEMA = {
    "type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "string"}}},
    "required": ["names"],
}

PROMPT = """文の中の固有名詞（人の名字・名前、会社名、団体名、学校名、店名、地名）を、文に書かれたとおり一字も変えずに抜き出してください。
「さん」「様」「先生」などの敬称は付けません。［］で囲まれた部分は抜き出しません。

例1
文: 田村さんから、明日の予約は8名とのことです。
答え: {{"names": ["田村"]}}

例2
文: みどり商事の岡田部長が、青空小学校の件で来店。
答え: {{"names": ["みどり商事", "岡田", "青空小学校"]}}

文: {text}
答え:"""

MASKED = re.compile(r"［[^］]*］")
HONORIFIC = r"(?=さん|様|さま|くん|君|ちゃん|先生|氏|部長|社長)"

GENERIC_NOUNS = {"小学校", "中学校", "高校", "高等学校", "幼稚園", "保育園", "こども園", "支援学校", "専門学校", "大学",
                 "学校", "旅行会社", "旅行社", "会社", "学園", "店", "お店", "工房", "教室", "ショップ"}

# 判断に要る役割の語。これを含む語は消さない
ROLE_WORDS = {"先生", "生徒", "児童", "園児", "学生", "添乗員", "ガイド", "保育士", "介助員", "引率", "担任",
              "観光客", "家族", "夫妻", "一家", "子供", "大人", "決裁者", "スタッフ", "オーナー", "部長", "担当"}


def _id_token(audience: str) -> str:
    import google.auth.transport.requests
    import google.oauth2.id_token
    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


def enabled() -> bool:
    return bool(os.environ.get("UNTANGLE_OLLAMA_URL")) or os.environ.get("UNTANGLE_GEMMA_LOCAL") == "1"


def _call(text: str, keep: list[str], timeout: float) -> list[dict]:
    body = {
        "model": GEMMA_MODEL,
        "messages": [{"role": "user", "content": PROMPT.format(text=text)}],
        "format": SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "num_thread": int(os.environ.get("UNTANGLE_GEMMA_THREADS", "8"))},
    }
    headers = {"Content-Type": "application/json"}
    if OLLAMA_URL.startswith("https://"):
        # 非公開の Cloud Run。呼べるのは本体のサービスアカウントだけなので、ID トークンを付ける
        headers["Authorization"] = f"Bearer {_id_token(OLLAMA_URL)}"
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(), headers=headers)
    # 起動中や混雑時は Cloud Run が 429/503 を返すので、間を空けて再試行する
    deadline = time.monotonic() + timeout
    wait = 2.0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=max(5.0, deadline - time.monotonic())) as r:
                content = json.loads(r.read())["message"]["content"]
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or time.monotonic() + wait > deadline:
                raise
            time.sleep(wait)
            wait = min(wait * 2, 15.0)
    return [{"text": n, "kind": "名前"} for n in json.loads(content).get("names", []) if isinstance(n, str)]


def make_detector(keep_words: list[str] = (), timeout: float = 60.0):
    """Masker に渡す検出器を作る。keep_words は消してはいけない語（テーマの語彙など）。"""
    keep = sorted(set(keep_words) | ROLE_WORDS)

    def detect(text: str) -> list[tuple[int, int, str]]:
        entities = _call(text, keep, timeout)
        masked_ranges = [m.span() for m in MASKED.finditer(text)]
        spans: list[tuple[int, int, str]] = []
        for e in entities:
            word = (e.get("text") or "").strip()
            if not word or re.search(r"[\d０-９]", word) or any(k in word for k in keep):
                continue
            if word in GENERIC_NOUNS:  # 「幼稚園」「旅行会社」のような種別だけの語は固有名詞ではない
                continue
            # 1文字の語は、敬称が続く名字（林さん・森様）だけを消す
            pat = re.escape(word) + (HONORIFIC if len(word) == 1 else "")
            for m in re.finditer(pat, text):
                s, t = m.start(), m.start() + len(word)
                if any(a <= s < b for a, b in masked_ranges):
                    continue
                spans.append((s, t, e.get("kind") or "固有名"))
        # 重なりを除く（長いものを優先）
        spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        out: list[tuple[int, int, str]] = []
        for sp in spans:
            if out and sp[0] < out[-1][1]:
                continue
            out.append(sp)
        return out

    return detect
