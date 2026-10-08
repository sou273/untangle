"""Gemini（Vertex AI・東京リージョン）とのやり取り。

AI に任せるのは3つだけ:
  - やり取り（伏せた後）から場面を読み取る: 問い・属性・判断・理由
  - 質問の言い回しを整える（選択肢の意味は変えない）
  - 出口②のテスト: 規程を渡した AI が、テスト一式の場面でどう答えるかを見る
合否を数えるのは AI ではなく、このファイルの呼び出し側の決まったプログラム。
渡すのは伏せた後の文だけ。渡した内容は記録に残す。
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

from google import genai
from google.genai import types

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "awesome-highway-306814")
LOCATION = os.environ.get("UNTANGLE_GEMINI_LOCATION", "asia-northeast1")
MODEL = os.environ.get("UNTANGLE_GEMINI_MODEL", "gemini-3.5-flash")

SENT_LOG: list[dict] = []   # AI に渡した記録（いつ・何の用途で・何文字）。本文は残さない


_CLIENT: genai.Client | None = None
_LOCK = threading.Lock()


def client() -> genai.Client:
    """接続は1つだけ作って使い回す（並列で作ると余りが片付けられ、使用中の接続が閉じる）。"""
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            _CLIENT = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
        return _CLIENT


def _log(purpose: str, text: str) -> None:
    SENT_LOG.append({"at": datetime.now(timezone.utc).isoformat(), "purpose": purpose,
                     "model": MODEL, "chars": len(text)})


# 答える前に「考える」分は出力として課金される（短い選択肢の答えでも1回に数百トークン）。
# 用途ごとに考える量を決める: 0 = 考えない、None = モデルの既定
THINKING_BUDGET: dict[str, int | None] = {
    "exit-ai-test": int(os.environ.get("UNTANGLE_THINKING_TEST", "0")),
    "read-scene": 0,
    "propose-theme": 1024,
    "group-patterns": 1024,
    "decision-points": 1024,
    "watch-rules": 1024,
}
USAGE: dict[str, dict[str, int]] = {}   # 用途ごとの使用トークン（入力・出力・考えた分）


def _json(prompt: str, schema: dict, purpose: str, temperature: float = 0.0) -> dict:
    _log(purpose, prompt)
    budget = THINKING_BUDGET.get(purpose)
    r = client().models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature, response_mime_type="application/json", response_schema=schema,
            thinking_config=None if budget is None else types.ThinkingConfig(thinking_budget=budget),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
    )
    u, acc = r.usage_metadata, USAGE.setdefault(purpose, {"calls": 0, "in": 0, "out": 0, "thought": 0})
    if u:
        acc["calls"] += 1
        acc["in"] += u.prompt_token_count or 0
        acc["out"] += u.candidates_token_count or 0
        acc["thought"] += u.thoughts_token_count or 0
    return json.loads(r.text)


ESCALATE_ANSWER = "決裁者に回します"


def answer_schema(options: list[str]) -> dict:
    """答えは規程にある選択肢から選ぶだけにする（自由記述させない＝規程に無い答えを作らせない）。"""
    return {
        "type": "OBJECT",
        "properties": {"decision": {"type": "STRING", "enum": [*options, ESCALATE_ANSWER]},
                       "article": {"type": "INTEGER", "nullable": True}},
        "required": ["decision"],
    }


def answer_with_instruction(instruction: str, situation: str, attributes: dict, question_title: str,
                            options: list[str]) -> dict:
    """出口②のテスト用。規程（AIへの指示）だけを渡し、1つの場面に答えさせる。"""
    prompt = (
        f"{instruction}\n\n---\n問い: {question_title}\n場面: {situation}\n"
        f"読み取り済みの項目: {json.dumps(attributes, ensure_ascii=False)}\n\n"
        "規程の条文で決まる答えを decision に、根拠の条文番号を article に入れてください。"
        f"決裁者に回す場合、または規程に当てはまらない場合は decision を「{ESCALATE_ANSWER}」にしてください。"
    )
    out = _json(prompt, answer_schema(options), "exit-ai-test")
    out["escalate"] = out.get("decision") == ESCALATE_ANSWER
    return out


def run_instruction_test(instruction: str, tests: list[dict], titles: dict[str, str],
                         options: dict[str, list[str]], boundary_cases: list[dict] = (), workers: int = 8) -> dict:
    """テスト一式を AI に解かせ、正解数を数える（数えるのはこの関数＝決まったプログラム）。"""
    cases = [dict(t, boundary=False) for t in tests] + [dict(b, boundary=True) for b in boundary_cases]

    def one(t: dict) -> dict:
        try:
            a = answer_with_instruction(instruction, t["situation"], t["attributes"], titles.get(t["question"], ""),
                                        options.get(t["question"], []))
        except Exception as e:  # noqa: BLE001  1件の失敗で全体を止めない
            return {**t, "got": None, "ok": False, "error": str(e)[:200]}
        if t["boundary"]:
            ok = bool(a.get("escalate"))
        else:
            ok = (a.get("escalate") and t["expected"] == "決裁者に回す") or a.get("decision") == t["expected"]
        return {**t, "got": a, "ok": bool(ok)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, cases))
    normal = [r for r in results if not r["boundary"]]
    edge = [r for r in results if r["boundary"]]
    return {
        "model": MODEL, "location": LOCATION,
        "normal_total": len(normal), "normal_ok": sum(r["ok"] for r in normal),
        "boundary_total": len(edge), "boundary_ok": sum(r["ok"] for r in edge),
        "failures": [{k: r.get(k) for k in ("id", "situation", "expected", "got", "error")} for r in results if not r["ok"]][:20],
    }


def read_scenes(masked_text: str, theme: dict, options: dict[str, list[str]]) -> list[dict]:
    """伏せた後のやり取りから、決まった判断を場面として読み取る（1つのやり取りに複数あってよい）。

    判断はその問いで使われている答えから選ばせる（自由記述だと規程と突き合わせられない）。
    """
    qids = [q["id"] for q in theme["questions"]]
    all_opts = sorted({o for v in options.values() for o in v})
    schema = {
        "type": "OBJECT",
        "properties": {"scenes": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "question_id": {"type": "STRING", "enum": qids},
                "attributes_json": {"type": "STRING"},
                "decision": {"type": "STRING", "enum": all_opts},
                "reason": {"type": "STRING", "nullable": True},
                "decider": {"type": "STRING", "enum": ["owner", "staff"]},
                "situation": {"type": "STRING"},
            },
            "required": ["question_id", "attributes_json", "decision", "decider", "situation"]}}},
        "required": ["scenes"],
    }
    qs = "\n".join(
        f"- {q['id']}: {q['title']}（見分ける項目: {'・'.join(q['key_attributes'])}／答え: {'・'.join(options.get(q['id'], []))}）"
        for q in theme["questions"])
    vocab = "\n".join(f"- {k}: {v}" for k, v in theme["vocabulary"].items())
    prompt = (
        f"テーマ「{theme['name']}」の判断を記録しています。次のやり取りで決まった判断を、問いごと・対象ごとに1件ずつ読み取ってください。\n"
        "例: 「先生は数えない、生徒は子供で」なら、立場=先生→数えない と 立場=生徒→子供 の2件。\n"
        f"問いの候補:\n{qs}\n\n項目の語彙:\n{vocab}\n\n"
        "やり取りの［］内は個人情報を伏せた置き換えです。決裁者（［決裁者］・店主・社長など）の発言やメモで決まったものは decider=owner、スタッフが決めたものは staff。\n"
        "何も決まっていなければ scenes は空にしてください。decision は問いの答えから選んでください。\n"
        "attributes_json には項目名→値のJSONを文字列で入れてください（語彙にない項目は作らない）。"
        "situation にはその対象について判断の前に見えていた事実を一文で（伏せた形のまま）。理由が書かれていなければ reason は null。\n\n"
        f"やり取り:\n{masked_text}"
    )
    out = _json(prompt, schema, "read-scene")
    scenes = []
    for sc in out.get("scenes", []):
        try:
            sc["attributes"] = json.loads(sc.pop("attributes_json") or "{}")
        except json.JSONDecodeError:
            sc["attributes"] = {}
        if sc["decision"] in options.get(sc["question_id"], []):
            scenes.append(sc)
    return scenes


def propose_theme(name: str, description: str, masked_examples: list[str]) -> dict:
    """テーマの名前・説明・例（伏せた後のやり取り）から、問い・答えの選択肢・見分ける項目の案を出す。

    案は決裁者が直してから確定する。ここで出すのは下書きだけ。
    """
    schema = {
        "type": "OBJECT",
        "properties": {
            "questions": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "title": {"type": "STRING"},
                "answers": {"type": "ARRAY", "items": {"type": "STRING"}},
                "key_attributes": {"type": "ARRAY", "items": {"type": "STRING"}},
            }, "required": ["title", "answers", "key_attributes"]}},
            "attributes": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "name": {"type": "STRING"}, "values": {"type": "STRING"},
            }, "required": ["name", "values"]}},
        },
        "required": ["questions", "attributes"],
    }
    ex = "\n---\n".join(masked_examples[:10]) or "（例なし）"
    prompt = (
        f"小さな店の決裁者が、自分の判断を規程にしたいと考えています。テーマは「{name}」です。\n説明: {description}\n\n"
        "このテーマで繰り返し出てくる判断を「問い」として1〜4個に分けてください。例のやり取りに出てこない問いは作らないでください。\n"
        "それぞれの問いに、答えの選択肢（2〜5個）と、判断を見分けるのに使う項目（1〜4個）を出してください。\n"
        "- 答えは「受ける」「断る」「時間をずらして受ける」のような短い行動にし、条件を入れない（×「4歳以上なら受ける」）。条件は項目で表す\n"
        "- 項目は、場面から読み取れる事実にする（例: 曜日・時間帯・人数・子供の年齢）。とりうる値を「・」区切りで書く（例: 平日・土日）\n"
        "- 決裁者に回すべき場面がありそうなら、答えに「決裁者に回す」を入れる\n"
        "［］内は個人情報を伏せた置き換えです。推測で埋めないでください。\n\n"
        f"例のやり取り:\n{ex}"
    )
    return _json(prompt, schema, "propose-theme", temperature=0.3)


def group_patterns(items: list[dict]) -> dict:
    """スタッフが送った返事（伏せた後の、ひな形に無い文）を「判断のまとまり」に分ける。

    items: [{"ref": int, "staff": "スタッフA", "subject": str, "text": str}]
    まとまりは下書き。規程にするかどうかは決裁者が決める。
    """
    schema = {
        "type": "OBJECT",
        "properties": {
            "patterns": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "name": {"type": "STRING"},
                "situation": {"type": "STRING"},
                "rule": {"type": "STRING"},
                "refs": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                "variation": {"type": "STRING"},
                "question": {"type": "STRING"},
            }, "required": ["name", "situation", "rule", "refs", "variation", "question"]}},
            "no_judgment": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        },
        "required": ["patterns", "no_judgment"],
    }
    body = "\n".join(
        f"[{it['ref']}] {it['staff']}｜件名: {it['subject']}\n{it['text']}" for it in items)
    prompt = (
        "小さな店のスタッフがお客様へ送った返事のうち、決まったひな形に無い文だけを集めました。"
        "スタッフがその場で下した判断（受ける・断る・条件をつける・例外を認める・確認を求める・返事を待ってもらう など）を、"
        "話題ごとの「判断のまとまり」に分けてください。\n"
        "- まとまりは広い話題でくくる（例: 「13名なら受ける」と「14名は条件つきで受ける」は1つの「定員を超える申し込み」）。"
        "数は多くても10個。1通にしか出ない話題は、近いまとまりに入れるか、入れられなければ外す\n"
        "- 同じまとまりの中で答え方が違う点は、まとまりを分けずに variation に書く\n"
        "- name: 話題の名前（12字以内。例: 人数変更とキャンセル料）\n"
        "- situation: どんな場面の話か（1文。お客様が何をしてきたときか。例: お客様が満席の回を申し込んだとき）\n"
        "- rule: いまの答え方を1〜2文で。文に書かれていることだけから書き、推測で補わない\n"
        "- refs: そのまとまりに入る返事の番号\n"
        "- variation: 同じ場面なのに答え方が食い違う（人や返事によって受ける・断る、有料・無料などが違う）ところだけ。"
        "場面が違うから答えが違うだけのもの、決まりが書かれていないだけのものは揺れではないので空文字\n"
        "- question: 決裁者に決めてもらうと良い問い（1文）。揺れがあるとき、または決まりが書かれておらずスタッフが迷いそうなときに書く。不要なら空文字\n"
        "- no_judgment: 判断を含まない返事（受け付けた・お礼・謝罪・再送だけ等）の番号\n"
        "［］内は個人情報を伏せた置き換えです。推測で埋めないでください。\n\n"
        f"返事:\n{body}"
    )
    return _json(prompt, schema, "group-patterns", temperature=0.2)


def decision_points(name: str, situation: str, items: list[dict]) -> dict:
    """決裁者が確かめたパターンの実例（伏せた後）から、決裁者に決めてもらうことと、実例に出た答え方を並べる。

    答え方は実例にあるものだけ。決裁者はその中から選ぶか、自分で書く。
    """
    schema = {
        "type": "OBJECT",
        "properties": {"points": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "question": {"type": "STRING"},
            "options": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "label": {"type": "STRING"},
                "refs": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            }, "required": ["label", "refs"]}},
        }, "required": ["question", "options"]}}},
        "required": ["points"],
    }
    body = "\n".join(f"[{it['ref']}] {it['staff']}\n{it['text']}" for it in items)
    prompt = (
        f"小さな店のスタッフがお客様へ送った返事のうち、「{name}」（場面: {situation}）にあたるものです。\n"
        "店の決裁者に、この場面での決まりを決めてもらいます。決めてもらうことを1〜3個に分け、それぞれについて"
        "実例に出てくる答え方を選択肢として並べてください。\n"
        "- question: 決裁者がそのまま答えられる問い（例: 12名を下回る減員に、キャンセル料をとりますか）。"
        "「統一しますか」「定めますか」のような、やり方を聞く問いにはしない\n"
        "- options: 実例に出てくる答え方（2〜4個。例: 2週間前からとる／とらない）。実例に無い答え方は作らない。"
        "答え方が1つしか出てこなければ1個でよい\n"
        "- refs: その答え方をしている実例の番号\n"
        "- 実例に1通しか出てこない細かい話は問いにしない\n"
        "［］内は個人情報を伏せた置き換えです。推測で埋めないでください。\n\n"
        f"実例:\n{body}"
    )
    return _json(prompt, schema, "decision-points", temperature=0.2)


NOT_IN_RULES = "どのパターンにも入らない"
VERDICTS = ["決まりどおり", "決まりと違う", "触れていない"]


def watch_rules(rules: list[dict], items: list[dict]) -> dict:
    """決まりを作ってから送った返事（伏せた後）が、どのパターンに入り、各決まりどおりかを選ぶ（選択肢から選ばせる）。"""
    names = [r["name"] for r in rules]
    schema = {
        "type": "OBJECT",
        "properties": {"results": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "ref": {"type": "INTEGER"},
            "pattern": {"type": "STRING", "enum": [*names, NOT_IN_RULES]},
            "checks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "question_no": {"type": "INTEGER"},
                "verdict": {"type": "STRING", "enum": VERDICTS},
            }, "required": ["question_no", "verdict"]}},
        }, "required": ["ref", "pattern", "checks"]}}},
        "required": ["results"],
    }
    desc = []
    for r in rules:
        pts = "\n".join(f"  問い{i + 1}: {p['question']} → 決まり: {p['decision']}" for i, p in enumerate(r["points"]))
        desc.append(f"■ {r['name']}（場面: {r['situation']}）\n{pts}")
    body = "\n".join(f"[{it['ref']}] {it['staff']}\n{it['text']}" for it in items)
    prompt = (
        "小さな店の決裁者が、次の場面ごとの決まりを作りました。\n" + "\n".join(desc) + "\n\n"
        "下はスタッフが送った返事です（ひな形に無い文だけ）。返事ごとに、どのパターンの場面にあたるかを選び、"
        "あたるならそのパターンの問いごとに、返事が決まりどおりか・決まりと違うか・その問いに触れていないかを選んでください。\n"
        "- どの場面にもあたらない返事は「" + NOT_IN_RULES + "」にし、checks は空にする\n"
        "- question_no は問いの番号（1から）\n"
        "［］内は個人情報を伏せた置き換えです。推測で埋めないでください。\n\n"
        f"返事:\n{body}"
    )
    return _json(prompt, schema, "watch-rules", temperature=0.0)
