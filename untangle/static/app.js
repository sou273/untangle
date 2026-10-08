(() => {
  const S = { themeDraft: { name: "", desc: "", examples: "" }, evalRes: {}, evalLedger: false, view: "home", themeId: null, themeTab: "articles", exitTab: "manual", exitData: {}, pasteResult: null, aiRun: {}, shop: null, shops: [] };
  try { S.shop = localStorage.getItem("untangle-shop"); } catch (_) {}
  const q = (path) => S.shop ? `${path}${path.includes("?") ? "&" : "?"}shop=${encodeURIComponent(S.shop)}` : path;
  const $main = document.getElementById("main");

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let SID = null;
  try { SID = localStorage.getItem("untangle-session"); } catch (_) {}
  if (!SID) { SID = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)); try { localStorage.setItem("untangle-session", SID); } catch (_) {} }
  // 自分の作業場（実データ）。ID を知っているブラウザだけが開ける。#ws-<ID> のリンクで別の端末でも開ける
  const ls = { get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (_) {} } };
  if (location.hash.startsWith("#ws-")) { ls.set("untangle-ws", location.hash.slice(4)); ls.set("untangle-mode", "ws"); history.replaceState(null, "", location.pathname); }
  S.wsId = ls.get("untangle-ws");
  S.ws = S.wsId && ls.get("untangle-mode") !== "demo" ? S.wsId : null;
  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", "X-Untangle-Session": SID };
    if (S.ws) headers["X-Untangle-Workspace"] = S.ws;
    const res = await fetch(path, { headers, ...opts });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `エラー（${res.status}）`);
    return body;
  }
  const KIND = {
    inconsistent: ["同じ条件なのに判断が違う", "p-warn"],
    new_type: ["初めての型", "p-ruri"],
    uncovered: ["答えられない場面が溜まった", "p-gray"],
    against_rule: ["規程と違う判断", "p-bad"],
    fact: ["事実の確認", "p-gray"],
  };
  const OUTCOME = {
    match: ["一致", "p-ok"], escalated: ["決裁者に回す", "p-ruri"], uncovered: ["答えられない", "p-gray"],
    rule_miss: ["規則の外れ", "p-bad"], extraction_miss: ["読み取りの外れ", "p-bad"], waver: ["揺れ", "p-warn"], conflict: ["条文の衝突", "p-bad"],
  };
  const STATUS = { draft: ["下書き", "p-gray"], confirmed: ["決裁者が確認済み", "p-ruri"], passed: ["テスト合格", "p-ok"], not_a_rule: ["規程にしない", "p-gray"] };
  const pill = ([t, c]) => `<span class="pill ${c}">${esc(t)}</span>`;
  const attrs = (a) => Object.entries(a || {}).map(([k, v]) => `${esc(k)}：${esc(v)}`).join("　");

  function checksHTML(checks) {
    return `<div class="checks">${checks.map((c) => `<div class="check ${c.ok ? "ok" : "ng"}"><span class="ic">${c.ok ? "✓" : "・"}</span><span>${esc(c.label)}</span><span class="a">${esc(c.answer)}</span>${c.detail ? `<span class="d">${esc(c.detail)}</span>` : ""}</div>`).join("")}</div>`;
  }
  function stateOf(t) {
    if (t.released_version) return ["完了", "p-ok"];
    if (t.open_questions) return ["聞き返し待ち", "p-warn"];
    return ["集めている", "p-gray"];
  }
  function remaining(t) { return t.checks.filter((c) => !c.ok).length; }

  const TOOL_WORD = { get_theme_status: "テーマの状態を見た", review_new_scenes: "新しい場面を見た", find_what_to_ask: "聞くべきことを探した（決まった規則）",
    rewrite_question: "質問の言い回しを整えた", test_rules: "条文をテストした", error: "止まった" };
  function runHTML(r, open) {
    if (!r) return "";
    return `<details class="agentlog" ${open ? "open" : ""}><summary><b>エージェントの作業記録</b>　<span class="meta">${esc(r.trigger)}</span></summary>
      <ol class="steps">${r.steps.map((x) => `<li><span class="tool">${esc(TOOL_WORD[x.tool] || x.tool)}</span><span class="meta">${esc(x.summary)}</span></li>`).join("")}</ol>
      ${r.report ? `<div class="report">${esc(r.report).replace(/\n/g, "<br>")}</div>` : ""}
      <p class="meta">使える道具は上の5つだけです。条文を確定する・質問に答える道具は持たせていません。（${esc(r.model)}・ADK）</p></details>`;
  }

  async function home() {
    const [themes, qs, runs] = await Promise.all([api(q("/api/themes")), api(q("/api/questions")), api(q("/api/agent/runs"))]);
    const open = qs.filter((q) => !q.answer && q.to === "owner").length;
    return `<div class="view">
      <div class="spread"><div><h1>テーマ</h1><p class="sub">${esc(S.shop)}で引き出している判断です。押すと中身が開きます。</p></div>${S.ws ? `<button class="btn" data-go="newTheme">＋ テーマを立てる</button>` : ""}</div>
      ${S.ws && !themes.length ? `<div class="card"><h2>まずテーマを立てましょう</h2><p class="sub">「予約の受け方」のように、引き出したい判断の名前と、実際のやり取りを何件か入れると、問いと答えの選択肢の案をGeminiが出します。あなたは直して確定するだけです。</p><div><button class="btn" data-go="newTheme">テーマを立てる</button></div></div>` : ""}
      ${open ? `<div class="card" style="flex-direction:row;justify-content:space-between;align-items:center;flex-wrap:wrap"><div><h2>あなたへの質問が ${open} 件あります</h2><p class="sub">選択肢から選ぶだけで答えられます。答えるたびに規程が進みます。</p></div><button class="btn" data-go="questions">質問に答える</button></div>` : `<div class="complete">あなたへの質問はありません<span>新しい記録が届くと、エージェントが必要なときだけ聞きに来ます。</span></div>`}
      <div class="grid">${themes.map((t) => `<div class="card"><div class="spread"><h2>${esc(t.name)}</h2>${pill(stateOf(t))}</div>
        <p class="meta">場面 ${t.scene_count}件 ・ 規程 ${t.released_version ? `第${t.released_version}版` : "下書き"}</p>
        ${checksHTML(t.checks)}
        ${t.released_version ? `<div class="exitbox"><span class="meta">出口（第${t.released_version}版）</span><div class="row"><button class="btn ghost sm" data-exit="${t.id}:manual">① スタッフ向け</button><button class="btn ghost sm" data-exit="${t.id}:ai">② AIへの指示</button><button class="btn ghost sm" data-exit="${t.id}:spec">③ 仕様書</button></div></div>` : `<div class="nearbox">出口まで：あと ${remaining(t)} つの確認で第1版が出せます</div>`}
        <div><button class="btn ghost" data-theme="${t.id}">開く</button></div></div>`).join("")}</div>
      ${runs.length ? `<div class="card">${runHTML(runs[0], false)}</div>` : ""}
    </div>`;
  }

  function questionCard(q) {
    const [label, tone] = KIND[q.kind];
    const scenes = `<div class="cases">${q.scenes.slice(0, 4).map((s) => `<div class="case">${esc(s.occurred_on)}　${esc(s.situation)}<b>${esc(s.decision)}</b><span class="meta">${esc(s.decider_role)}</span></div>`).join("")}${q.scenes.length > 4 ? `<div class="case">ほか ${q.scenes.length - 4} 件</div>` : ""}</div>`;
    if (q.answer) {
      return `<div class="q"><div class="spread">${pill([label, tone])}<span class="meta">${esc(q.answered_on)}</span></div><h3>${esc(q.text)}</h3><div class="answered">「${esc(q.answer)}」と答えました。</div></div>`;
    }
    const draft = q.draft_sentence ? `<div class="draft">条文案：${esc(q.draft_sentence)}</div>` : "";
    let form;
    if (q.kind === "fact") {
      const s = q.scenes[0];
      form = `<p class="meta">記録に書いていない事実があれば直してください。</p><div class="kv">${Object.entries(s.attributes).map(([k, v]) => `<label class="k" for="f-${q.id}-${esc(k)}">${esc(k)}</label><input id="f-${q.id}-${esc(k)}" data-fact="${esc(k)}" value="${esc(v)}">`).join("")}</div>
        <div class="choices">${q.choices.map((c) => `<button class="choice" data-answer="${q.id}" data-choice="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
    } else {
      form = `<label class="meta" for="r-${q.id}">理由（一言で。「条件が違った」を選ぶときは必須）</label><input id="r-${q.id}" class="reason" placeholder="例：小中学生は子供料金の対象だから">
        <div class="choices">${q.choices.map((c, i) => `<button class="choice ${i >= 2 ? "minor" : ""}" data-answer="${q.id}" data-choice="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
    }
    const orig = q.original_text ? `<details><summary class="meta">決まった規則で作った元の文面</summary><p class="meta">${esc(q.original_text)}</p></details>` : "";
    return `<div class="q" id="card-${q.id}"><div class="spread">${pill([label, tone])}<span class="meta">${q.to === "staff" ? "スタッフへ" : "決裁者へ"}</span></div><h3>${esc(q.text)}</h3>${orig}${draft}${scenes}${form}<p class="meta err" hidden></p></div>`;
  }

  async function questions() {
    const [qs, themes] = await Promise.all([api(q("/api/questions")), api(q("/api/themes"))]);
    const done = themes.find((t) => t.released_version);
    const owner = qs.filter((q) => q.to === "owner" && !q.answer);
    const staff = qs.filter((q) => q.to === "staff" && !q.answer);
    const answered = qs.filter((q) => q.answer);
    return `<div class="view"><div><h1>あなたへの質問</h1><p class="sub">名前は伏せた形で表示しています。AIにもこの形でしか渡っていません。</p></div>
      ${done ? `<div class="complete">${esc(done.name)}について、すべての解析が終わりました。<span>規程の第${done.released_version}版を出しました。テーマの画面から3つの出口を取り出せます。</span><div class="row" style="margin-top:8px"><button class="btn" data-theme="${done.id}">テーマを開いて出口へ</button></div></div>` : ""}
      ${owner.map(questionCard).join("") || (done ? "" : `<p class="sub">いまは質問がありません。</p>`)}
      ${staff.length ? `<h2>スタッフに確認中の事実</h2>${staff.map(questionCard).join("")}` : ""}
      ${answered.length ? `<details><summary class="meta">答えた質問（${answered.length}件）</summary>${answered.map(questionCard).join("")}</details>` : ""}
    </div>`;
  }

  async function theme() {
    const t = await api(`/api/themes/${S.themeId}`);
    const tabs = [["articles", "規程"], ["scenes", "場面"], ["criteria", "完了の基準"]];
    let body = "";
    if (S.themeTab === "articles") {
      body = `<div class="card"><div class="spread"><h2>規程　${t.released_version ? `第${t.released_version}版` : "下書き"}</h2><span class="meta">構造から自動で作った文章です。直すときは条文を直します。</span></div><div>${t.articles.map((a) => `<div class="art"><span class="n">第${a.number}条</span><div><p>${esc(a.sentence)}</p><span class="why">理由：${esc(a.reason || "未確認")}</span><div class="foot">${pill(STATUS[a.status])}<span>${esc(t.questions.find((q) => q.id === a.question_id)?.title)}</span>${a.exception_of ? `<span>例外（元の条文あり）</span>` : ""}</div></div></div>`).join("")}</div></div>`;
    } else if (S.themeTab === "scenes") {
      body = `<div class="tablewrap"><table><thead><tr><th>日付</th><th>出どころ</th><th>状況（伏せた後）</th><th>属性</th><th>判断</th><th>判断者</th><th>結果</th></tr></thead><tbody>${t.scenes.slice().reverse().map((s) => `<tr><td class="num">${esc(s.occurred_on)}</td><td>${s.source === "conversation" ? "やり取り" : "直した跡"}</td><td>${esc(s.situation)}</td><td class="meta">${attrs(s.attributes)}</td><td>${esc(s.decision)}</td><td>${esc(s.decider_role)}</td><td>${s.flags.length ? pill(["テスト対象外", "p-gray"]) : s.outcome ? pill(OUTCOME[s.outcome]) : ""}</td></tr>`).join("")}</tbody></table></div>`;
    } else {
      const c = t.criteria;
      body = `<div class="card"><h2>完了の基準（決裁者が決めたもの）</h2><div class="tablewrap"><table><tbody>
        <tr><td>答えられない場面は残っていないか</td><td>${Math.round(c.coverage_min * 100)}% 以上に当てはまる条文がある（「決裁者に回す」も含む）</td></tr>
        <tr><td>答えはあなたと同じか</td><td>直近 ${c.recent_window} 件で違う答え ${c.max_mismatches} 件以下</td></tr>
        <tr><td>もう新しい話は出てこないか</td><td>直近 ${c.novelty_window} 件で新しい型 ${c.max_new_types} 件以下</td></tr>
        <tr><td>あなたへの質問は残っていないか</td><td>0 件</td></tr></tbody></table></div></div>`;
    }
    const exitsBlock = t.released_version ? await exitsHTML(t) : `<div class="nearbox">出口まで：あと ${remaining(t)} つの確認で、ここに3つの出口が出ます</div>`;
    return `<div class="view"><div><button class="link" data-go="home">← テーマ一覧</button></div>
      <div class="spread"><div><h1>${esc(t.name)}</h1><p class="sub">場面 ${t.scene_count}件 ・ 問い ${t.questions.length}つ</p></div>${pill(stateOf(t))}</div>
      ${checksHTML(t.checks)}
      ${exitsBlock}
      <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" aria-selected="${S.themeTab === k}" data-tt="${k}">${l}</button>`).join("")}</div>
      ${body}</div>`;
  }

  async function exitsHTML(t) {
    const k = S.exitTab;
    const key = `${t.id}:${k}`;
    if (!S.exitData[key]) S.exitData[key] = await api(`/api/themes/${t.id}/exits/${k}`);
    const d = S.exitData[key];
    const tabs = [["manual", "① スタッフ向けマニュアル"], ["ai", "② AIへの指示"], ["spec", "③ 自動化の仕様書"]];
    let body = "";
    if (k === "manual") {
      body = `<div class="doc">${d.sections.map((s) => `<h4>${esc(s.title)}</h4><ul>${s.items.map((i) => `<li>${esc(i.text)}${i.reason ? `<br><span class="meta">理由：${esc(i.reason)}</span>` : ""}</li>`).join("")}</ul>`).join("")}<h4>迷ったら決裁者へ</h4><ul>${d.ask_owner.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
    } else if (k === "ai") {
      body = `<div class="split"><div class="card"><h2>AIへの指示</h2><pre class="code">${esc(d.instruction)}</pre><p class="meta">テスト一式（場面${d.tests.length}件と正解）を同梱しています。</p></div>
        <div class="card"><h2>渡す前のテスト</h2><p class="sub">この指示をGeminiに渡し、同梱のテスト一式と規程に無い場面で、何件正解するかを数えます。数えるのはAIではなくUntangleです。</p>${aiRunHTML(t)}</div></div>`;
    } else {
      body = `<div class="card"><h2>判断表</h2><div class="tablewrap"><table><thead><tr><th>問い</th><th>優先</th><th>条文</th><th>内容</th></tr></thead><tbody>${d.decision_table.map((r) => `<tr><td>${esc(r.question)}</td><td class="num">${r.priority}</td><td class="num">第${r.article}条</td><td>${esc(r.text)}</td></tr>`).join("")}</tbody></table></div>
        <div class="kv"><span class="k">境目</span><span>${d.boundary.map(esc).join("／")}</span><span class="k">テスト一式</span><span>${d.tests.length}件</span><span class="k">任せ方</span><span>${d.rollout.map(esc).join(" → ")}。${esc(d.promotion_rule)}</span></div></div>`;
    }
    return `<div class="card"><div class="spread"><h2>出口　第${t.released_version}版</h2><span class="meta">条文から自動で作ります。手では直しません。</span></div><div class="tabs" role="tablist">${tabs.map(([x, l]) => `<button role="tab" aria-selected="${k === x}" data-et="${x}">${l}</button>`).join("")}</div>${body}</div>`;
  }

  function aiRunHTML(t) {
    const r = S.aiRun[t.id];
    if (!r) return `<div class="row"><button class="btn" id="runAi" data-t="${t.id}">Geminiでテストする</button></div>`;
    if (r === "running") return `<div class="bar"><i style="width:60%"></i></div><p class="meta">Geminiが1件ずつ答えています（1分ほどかかります）…</p>`;
    if (r.error) return `<p class="answered" style="background:var(--bad-soft);color:var(--bad)">${esc(r.error)}</p><button class="btn ghost" id="runAi" data-t="${t.id}">もう一度</button>`;
    return `<div class="${r.passed ? "answered" : "answered bad"}">通常の場面 ${r.normal_total}件中${r.normal_ok}件で正解。<br>規程に無い場面 ${r.boundary_total}件中${r.boundary_ok}件で「決裁者に回します」と答えました。<br><b>${r.passed ? "決裁者の基準を満たしました。この指示はAIに渡せます。" : "基準を満たしていません。外れた場面を確かめてください。"}</b><br><span class="meta">${esc(r.model)}（${esc(r.location)}）</span></div>
      ${r.failures.length ? `<div class="tablewrap"><table><thead><tr><th>場面</th><th>正解</th><th>AIの答え</th></tr></thead><tbody>${r.failures.map((f) => `<tr><td>${esc(f.situation)}</td><td>${esc(f.expected ?? "決裁者に回す")}</td><td>${esc(f.got?.decision ?? f.error)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <button class="btn ghost" id="runAi" data-t="${t.id}">もう一度テストする</button>`;
  }

  const samples = () => (S.shops.find((x) => x.name === S.shop) || S.shops[0] || { samples: [] }).samples;
  const OUT_WORD = { match: "規程と一致", uncovered: "まだ規程に無い型", rule_miss: "規程と違う判断", waver: "揺れ", escalated: "決裁者に回す", extraction_miss: "読み取りの外れ", conflict: "条文の衝突" };

  // ---- 作業場の「記録を送る」: 生の文はこの端末から出さない ----
  async function wsIntake() {
    S.wsThemes = await api("/api/themes").catch(() => []);
    if (!S.wsThemes.length) return `<div class="view"><h1>記録を送る</h1><div class="card"><h2>まずテーマを立てましょう</h2><div><button class="btn" data-go="newTheme">テーマを立てる</button></div></div></div>`;
    if (!S.local) S.local = { avail: await LocalAI.availability() };
    const L = S.local;
    const nanoOk = L.avail === "available" || L.avail === "readily";
    const AVAIL = nanoOk ? `<span class="pill p-ok">端末内AI（Gemini Nano）が使えます</span>`
      : `<span class="pill p-warn">この端末では端末内AIが使えません</span><span class="meta">辞書と決まった規則で伏せた形を、あなたが確かめてから送ります。名前を自動で探す機能は使えません。パソコン版Chromeの最新版なら使えます。</span>`;
    let body = "";
    if (L.busy) body = `<div class="bar"><i style="width:55%"></i></div><p class="meta">${esc(L.busy)}</p>`;
    if (L.error) body += `<p class="answered bad">${esc(L.error)}</p>`;
    if (L.auto) {
      const a = L.auto;
      body += `<h2>自動で送った値 ${a.accepted.length}件</h2><p class="meta">サーバーに届いたのは、下の表の値だけです。文そのものは送っていません。</p>
        ${a.accepted.length ? `<div class="tablewrap"><table><thead><tr><th>問い</th><th>項目の値</th><th>判断</th><th>決めた人</th><th>規程との照合</th></tr></thead><tbody>${a.accepted.map((x) => `<tr><td>${esc(x.question_id)}</td><td class="meta">${attrs(x.attributes)}</td><td>${esc(x.decision)}</td><td>${esc(x.decider_role)}</td><td>${x.outcome ? pill(OUTCOME[x.outcome]) : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}
        ${a.rejected && a.rejected.length ? `<p class="meta">サーバーが受け取らなかった値: ${a.rejected.length}件（選択肢に無い値）</p>` : ""}
        ${runHTML(a.agent_run, false)}`;
    }
    if (L.review) {
      const r = L.review;
      body += `<div class="card" style="border-color:var(--warn)"><h2>確かめてから送る</h2>
        <p class="sub">${esc(r.why)}。この端末の中で伏せた形です。名前などが残っていないか確かめ、必要なら直してから送ってください。送るとサーバーのGeminiが読み取ります。</p>
        ${r.problems && r.problems.length ? `<p class="meta">自動で送らなかった理由: ${r.problems.map(esc).join("・")}</p>` : ""}
        <textarea id="reviewText" style="min-height:140px">${esc(r.masked)}</textarea>
        <div class="row"><button class="btn" id="approveSend">この形で送ってよい</button><button class="btn ghost" id="discardReview">送らない</button></div></div>`;
    }
    if (L.approvedResult) {
      const a = L.approvedResult;
      body += `<div class="answered">確かめた文から、場面を ${a.accepted.length}件 加えました。</div>${runHTML(a.agent_run, false)}`;
    }
    return `<div class="view"><div><h1>記録を送る</h1><p class="sub">やり取りを貼ってください。何件まとめてもかまいません。<b>文はこの端末の中で読み取り、サーバーには選択肢の値だけを送ります。</b>読みきれなかったものは、伏せた形をあなたが確かめてから送ります。</p></div>
      <div class="card"><div class="row">${AVAIL}</div>
        <label class="meta" for="itheme">どのテーマの記録か</label><select id="itheme">${S.wsThemes.map((t) => `<option value="${esc(t.id)}" ${t.id === S.intakeTheme ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select>
        <label class="meta" for="paste">やり取りの文</label><textarea id="paste" style="min-height:160px">${esc(S.pasteText || "")}</textarea>
        <div class="row"><button class="btn" id="localRead" ${L.busy ? "disabled" : ""}>${nanoOk ? "端末の中で読み取る" : "伏せた形を作る"}</button></div>
        ${body}</div></div>`;
  }

  async function localRead() {
    const L = S.local;
    S.pasteText = document.getElementById("paste").value;
    S.intakeTheme = document.getElementById("itheme").value;
    Object.assign(L, { auto: null, review: null, approvedResult: null, error: null });
    if (!S.pasteText.trim()) { L.error = "文を貼ってください"; return render(); }
    const dict = await api("/api/dictionary").catch(() => []);
    const nanoOk = L.avail === "available" || L.avail === "readily";
    try {
      if (!nanoOk) {
        L.busy = "この端末の中で伏せています…"; render();
        const m = await LocalAI.mask(S.pasteText, dict, false);
        if (m.masked === null) { L.busy = null; L.error = m.reason; return render(); }
        L.review = { masked: m.masked, why: "端末内AIが使えないため、自動では送りません" };
      } else {
        L.busy = "この端末の中で読み取っています（Gemini Nano）…"; render();
        const schema = await api(`/api/themes/${S.intakeTheme}/schema`);
        let items = [];
        try { items = await LocalAI.extract(S.pasteText, schema); } catch (e) { items = null; }
        const ok = (items || []).filter((x) => x.ok);
        const ng = (items || []).filter((x) => !x.ok);
        if (ok.length) {
          L.busy = "選択肢の値だけを送っています…"; render();
          L.auto = await api("/api/intake/structured", { method: "POST", body: JSON.stringify({ theme_id: S.intakeTheme, scenes: ok.map((x) => x.send) }) });
        }
        if (items === null || ng.length) {
          L.busy = "読みきれなかった分を、この端末の中で伏せています…"; render();
          const m = await LocalAI.mask(S.pasteText, dict, true);
          if (m.masked === null) L.error = m.reason;
          else L.review = { masked: m.masked, problems: [...new Set(ng.flatMap((x) => x.problems))], why: items === null ? "端末内AIが読み取りに失敗したため、自動では送りません" : `${ng.length}件は自動では送れませんでした` };
        } else if (!items.length) {
          L.error = "決まった判断が見つからなかったので、何も送っていません";
        }
      }
    } catch (e) { L.error = e.message; }
    L.busy = null; S.exitData = {};
    render();
  }

  async function intake() {
    if (S.ws) return wsIntake();
    const r = S.pasteResult;
    const text = S.pasteText ?? (samples()[0] || {}).text ?? "";
    let result = "";
    if (S.pasting) result = `<div class="bar"><i style="width:55%"></i></div><p class="meta">伏せています（Gemma）→ 場面を読み取っています（Gemini）→ エージェントが巡回しています…（20〜40秒）</p>`;
    else if (r && r.error) result = `<p class="answered bad">${esc(r.error)}</p>`;
    else if (r) {
      if (r.masked === null) result = `<div class="answered bad">${esc(r.rejected_reason)}。AIには何も渡していません。</div>`;
      else {
        result = `<p class="meta">AIに届いた形（${r.gemma ? "辞書・決まった規則・Gemma" : "辞書・決まった規則"}で伏せた後）</p><div class="case" style="font-size:.9rem;white-space:pre-wrap">${esc(r.masked).replace(/［([^］]*)］/g, '<span class="m">$1</span>')}</div>
          <p class="meta">置き換え ${r.replacements.length} か所 ・ 消し残しの検査：${r.held ? `<b>要確認（${esc(r.residual_kinds.join("・"))}）</b>` : "0件"}</p>`;
        if (r.held) result += `<div class="answered bad">消し残しの疑いがあるので、Geminiには渡さず保留にしました。</div>`;
        else if (!r.scenes.length) result += `<div class="nearbox">${esc(r.note || "場面にはしませんでした")}</div>`;
        else result += `<h2>できた場面 ${r.scenes.length}件</h2><div class="tablewrap"><table><thead><tr><th>状況</th><th>属性</th><th>判断</th><th>理由</th><th>判断者</th><th>規程との照合</th></tr></thead><tbody>${r.scenes.map((x) => `<tr><td>${esc(x.situation).replace(/［([^］]*)］/g, '<span class="m">$1</span>')}</td><td class="meta">${attrs(x.attributes)}</td><td>${esc(x.decision)}</td><td>${esc(x.reason || "—")}</td><td>${esc(x.decider_role)}</td><td>${x.outcome ? pill(OUTCOME[x.outcome]) : ""}</td></tr>`).join("")}</tbody></table></div>
          <p class="meta">${r.new_questions.length ? `エージェントが質問を ${r.new_questions.length} 件作りました。` : "分かっている型、または理由が書かれているので、エージェントは聞き返しませんでした。"}</p>
          ${runHTML(r.agent_run, true)}`;
      }
    }
    return `<div class="view"><div><h1>記録を送る</h1><p class="sub">普段のやり取りや、業務で直した跡を送ります。送った文はサーバーの中で伏せてから場面にし、元の文は保存しません。</p></div>
      <div class="card"><h2>貼り付けて送る</h2>
      <div class="row">${samples().map((x, i) => `<button class="btn ghost sm" data-sample="${i}">${esc(x.label)}</button>`).join("")}</div>
      ${S.ws ? `<label class="meta" for="itheme">どのテーマの記録か</label><select id="itheme">${(S.wsThemes || []).map((t) => `<option value="${esc(t.id)}" ${t.id === S.intakeTheme ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select>` : ""}
      <label class="meta" for="paste">${S.ws ? "やり取りの文（何件まとめて貼ってもかまいません。伏せてから場面にし、元の文は保存しません）" : "やり取りの文（例は架空です）"}</label><textarea id="paste">${esc(text)}</textarea>
      <div class="row"><button class="btn" id="doPaste" ${S.pasting ? "disabled" : ""}>伏せて場面にする</button></div>
      ${result}</div></div>`;
  }

  function evalHTML(key, title, note) {
    const r = S.evalRes[key];
    let body = `<div class="row"><button class="btn" data-eval="${key}">テストする</button></div>`;
    if (r === "running") body = `<div class="bar"><i style="width:55%"></i></div><p class="meta">Gemmaで20文を伏せて数えています（約3分。GemmaはCPUで動いているため）…</p>`;
    else if (r && r.error) body = `<p class="answered bad">${esc(r.error)}</p>${body}`;
    else if (r) {
      const verdict = r.undecided ? "判定できず" : r.passed ? "合格" : "不合格";
      body = `<div class="${r.passed ? "answered" : "answered bad"}"><b>${verdict}</b>${r.undecided ? `（${r.held}文が保留になり、数えきれませんでした。もう一度テストしてください）` : ""}　AIに届いてしまう名前 ${r.leaks}件（${r.names}語中）・判断に要る語の消しすぎ ${r.over}件<br><span class="meta">基準：${esc(r.criteria)}・${r.recorded ? `本番で実行した記録（${esc(r.measured_at || "2026-10-01")}・${r.seconds}秒）` : r.cached ? "1時間以内の結果" : `いま実行（${r.seconds}秒）`}</span></div>
        <details ${r.passed ? "" : "open"}><summary class="meta">文ごとの結果（${r.cases}文）</summary><div class="tablewrap"><table><thead><tr><th>#</th><th>AIに届く形</th><th>漏れ</th><th>消しすぎ</th></tr></thead><tbody>${r.rows.map((x) => `<tr><td class="num">${x.no}</td><td>${x.masked === null ? "（取り込まない）" : esc(x.masked).replace(/［([^］]*)］/g, '<span class="m">$1</span>')}${x.held ? ` <span class="pill p-warn">保留：${esc((x.held_reason || []).join("・"))}</span>` : ""}</td><td>${x.leaks.map(esc).join("・") || "—"}</td><td>${x.over.map(esc).join("・") || "—"}</td></tr>`).join("")}</tbody></table></div></details>
        <div class="row"><button class="btn ghost sm" data-eval="${key}">いまテストし直す（約3分）</button></div>`;
    }
    return `<div class="card"><h2>${title}</h2><p class="sub">${note}</p>${body}</div>`;
  }

  async function masking() {
    if (S.ws && !S.dict) S.dict = await api("/api/dictionary").catch(() => []);
    const ledger = S.evalLedger;
    if (!S.evalLatestLoaded) {
      S.evalLatestLoaded = true;
      const latest = await api("/api/masking/eval/latest").catch(() => ({}));
      for (const [k, v] of Object.entries(latest)) if (!S.evalRes[k]) S.evalRes[k] = v;
    }
    return `<div class="view"><div><h1>伏せ方</h1><p class="sub">AIに何を見せてよいかも、決裁者が決めます。名前を見つけるのは店の台帳・決まった規則・Gemma（このサービスの中だけで動く公開モデル）で、外部のGeminiには伏せた後の文しか渡しません。</p></div>
      ${S.ws ? dictHTML() : ""}
      <div class="card"><h2>伏せ方のテスト</h2><p class="sub">伏せ方も、決裁者の基準を満たしてから使います。架空の例文で、AIに届いてしまう名前と、判断に要る語（人数など）の消しすぎを数えます。</p>
        <label class="row" for="ledger"><input type="checkbox" id="ledger" style="width:auto" ${ledger ? "checked" : ""}> 店の台帳（取引先の一覧）を辞書として使う</label>
        <p class="meta">外すと、例文に出てくる名前はどれも台帳に無い前提（いちばん厳しい条件）で測ります。</p></div>
      <div class="grid">
        ${evalHTML(`holdout:${ledger ? 1 : 0}`, "確認用の20文", "伏せ方の規則や指示を直すときに見ていない例文です。本当の実力はこちらで測ります。")}
        ${evalHTML(`tune:${ledger ? 1 : 0}`, "調整用の20文", "伏せ方の規則や指示を直すときに見ながら使った例文です。")}
      </div>
      <div class="grid">
        <div class="card"><h2>決まった規則</h2><div class="kv"><span class="k">消す</span><span>電話・メール・URL・郵便番号・9桁以上の番号、「さん・様」の直前の名前</span><span class="k">区分にする</span><span>日付 → 平日／土日・季節。学校・会社は種別だけ残す（あおば幼稚園 → 幼稚園）</span><span class="k">取り込まない</span><span>パスワードや鍵を含むメッセージは丸ごと</span></div></div>
        <div class="card"><h2>消し残しの検査</h2><p class="sub">伏せた後の文にもう一度かけ、残っている疑いがあればGeminiに渡さず、決裁者の確認待ちにします。Gemmaが使えないときも渡しません。</p></div>
      </div></div>`;
  }

  async function newTheme() {
    const d = S.themeDraft;
    const idea = S.themeIdea;
    let draftHTML = "";
    if (S.proposing) draftHTML = `<div class="bar"><i style="width:55%"></i></div><p class="meta">例を伏せて（Gemma）、案を作っています（Gemini）…</p>`;
    else if (idea && idea.error) draftHTML = `<p class="answered bad">${esc(idea.error)}</p>`;
    else if (idea) {
      draftHTML = `<h2>案（直してから確定してください）</h2>
        ${idea.skipped && idea.skipped.length ? `<p class="meta">使わなかった例: ${idea.skipped.map((x) => `${x.no}件目（${esc(x.reason)}）`).join("・")}</p>` : ""}
        <p class="meta">問い（答えの選択肢と、見分ける項目は「、」区切り）</p>
        ${idea.questions.map((x, i) => `<div class="case qrow"><input data-q="${i}" data-f="title" value="${esc(x.title)}" aria-label="問い${i + 1}"><label class="meta">答えの選択肢<input data-q="${i}" data-f="answers" value="${esc(x.answers.join("、"))}"></label><label class="meta">見分ける項目<input data-q="${i}" data-f="key_attributes" value="${esc(x.key_attributes.join("、"))}"></label><button class="link" data-delq="${i}">この問いを外す</button></div>`).join("")}
        <p class="meta">項目ととりうる値（値は「・」区切り）</p>
        ${idea.attributes.map((a, i) => `<div class="row arow"><input data-a="${i}" data-f="name" value="${esc(a.name)}" aria-label="項目${i + 1}" style="max-width:12em"><input data-a="${i}" data-f="values" value="${esc(a.values)}" aria-label="値${i + 1}"></div>`).join("")}
        <div class="row"><button class="btn ghost sm" id="addAttr">項目を足す</button></div>
        <div class="row"><button class="btn" id="createTheme">この内容でテーマを立てる</button><span class="meta">入れた例（伏せた形）は、そのまま最初の記録として取り込みます</span></div>`;
    }
    return `<div class="view"><div><button class="link" data-go="home">← テーマ一覧</button></div><div><h1>テーマを立てる</h1><p class="sub">引き出したい判断の名前と、実際のやり取りを何件か入れてください。例は伏せてからGeminiに渡します。</p></div>
      <div class="card"><label class="meta" for="tname">テーマの名前</label><input id="tname" value="${esc(d.name)}" placeholder="例：予約の受け方">
        <label class="meta" for="tdesc">どんな判断か（一言）</label><input id="tdesc" value="${esc(d.desc)}" placeholder="例：電話やメールで来る予約を受けるか、どう受けるか">
        <label class="meta" for="tex">実際のやり取り（1件ごとに空行で区切る・3〜10件）</label><textarea id="tex" style="min-height:200px">${esc(d.examples)}</textarea>
        <div class="row"><button class="btn" id="propose" ${S.proposing ? "disabled" : ""}>案を出す</button></div>
        ${draftHTML}</div></div>`;
  }

  async function newWs() {
    return `<div class="view"><div><h1>自分の作業場を作る</h1><p class="sub">実際のやり取りを溜めて、あなたの判断を規程にするための場所です。デモとは別に保存されます。</p></div>
      <div class="card"><label class="meta" for="wsname">店の名前（画面に出るだけです）</label><input id="wsname" placeholder="例：切子体験工房">
      <p class="meta">作業場は推測できない長いIDで区切られ、そのIDを知っているブラウザだけが開けます。別の端末で開くためのリンクは、作った後に出ます。リンクは人に渡さないでください。</p>
      <div class="row"><button class="btn" id="createWs">作る</button></div></div></div>`;
  }

  function dictHTML() {
    const rows = S.dict || [];
    return `<div class="card"><h2>伏せる辞書（${esc(S.shop)}）</h2><p class="sub">スタッフ・取引先・常連のお客様などの名前と、置き換える言葉を登録します。辞書にある名前は必ず伏せます。辞書はこの作業場の中にだけ保存されます。</p>
      <div class="tablewrap"><table><thead><tr><th>名前（書かれたとおり）</th><th>置き換える言葉</th><th>種類</th><th></th></tr></thead><tbody>
      ${rows.map((r, i) => `<tr><td><input data-d="${i}" data-f="surface" value="${esc(r.surface)}" aria-label="名前${i + 1}"></td><td><input data-d="${i}" data-f="replacement" value="${esc(r.replacement)}" aria-label="置き換え${i + 1}"></td><td><input data-d="${i}" data-f="kind" value="${esc(r.kind)}" aria-label="種類${i + 1}" placeholder="スタッフ"></td><td><button class="link" data-deld="${i}">外す</button></td></tr>`).join("")}
      </tbody></table></div>
      <div class="row"><button class="btn ghost sm" id="addDict">行を足す</button><button class="btn" id="saveDict">保存する</button><span class="meta" id="dictMsg"></span></div>
      <p class="meta">例：佐藤 → ショップ担当A（スタッフ）／○○観光 → 旅行会社（取引先）</p></div>`;
  }

  function shopSelect(id) {
    if (S.ws) return `<span class="meta">自分の作業場</span><b>${esc(S.shop)}</b><div class="row"><button class="link" data-wslink="1">別の端末で開くリンク</button><button class="link" data-mode="demo">デモを見る</button></div><span class="meta wsmsg"></span>`;
    return `<label class="meta" for="${id}">店（架空のデモ）</label><select id="${id}" class="shopsel">${S.shops.map((x) => `<option ${x.name === S.shop ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
      <div class="row">${S.wsId ? `<button class="link" data-mode="ws">自分の作業場に戻る</button>` : `<button class="link" data-go="newWs">自分の作業場を作る</button>`}<button class="link" data-reset="1">デモを最初の状態に戻す</button></div>`;
  }
  async function setMode(mode) {
    ls.set("untangle-mode", mode); S.ws = mode === "ws" ? S.wsId : null;
    S.dict = null; S.pasteText = null; S.pasteResult = null; S.exitData = {}; S.themeIdea = null; S.local = null;
    await loadShops(); go("home");
  }
  async function loadShops() {
    S.shops = await api("/api/shops").catch(() => []);
    if (S.ws) S.shop = (S.shops[0] || {}).name;
    else if (!S.shops.find((x) => x.name === S.shop)) S.shop = S.shops[0] ? S.shops[0].name : null;
    const side = document.getElementById("shopSide"); if (side) side.innerHTML = shopSelect("shop1");
  }
  // サイドバー（$main の外）のボタン
  document.getElementById("shopSide").addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.go) return go(t.dataset.go);
    if (t.dataset.mode) return setMode(t.dataset.mode);
    if (t.dataset.reset) return resetDemo();
    if (t.dataset.wslink) {
      const url = `${location.origin}/#ws-${S.ws}`, m = t.closest("[data-shopbox]").querySelector(".wsmsg");
      try { await navigator.clipboard.writeText(url); m.textContent = "リンクをコピーしました。人に渡さないでください"; }
      catch (_) { m.textContent = url; }
    }
  });
  function setShop(name) {
    S.shop = name; try { localStorage.setItem("untangle-shop", name); } catch (_) {}
    S.pasteText = null; S.pasteResult = null; S.exitData = {};
    const side = document.getElementById("shopSide"); if (side) side.innerHTML = shopSelect("shop1");
    go("home");
  }
  document.addEventListener("change", (e) => {
    if (e.target.classList && e.target.classList.contains("shopsel")) setShop(e.target.value);
    if (e.target.id === "ledger") { S.evalLedger = e.target.checked; render(); }
  });

  function collectIdea() {
    if (!S.themeIdea) return;
    document.querySelectorAll("[data-q]").forEach((el) => {
      const x = S.themeIdea.questions[+el.dataset.q]; if (!x) return;
      x[el.dataset.f] = el.dataset.f === "title" ? el.value : el.value.split(/[、,，]/).map((v) => v.trim()).filter(Boolean);
    });
    document.querySelectorAll("[data-a]").forEach((el) => { const a = S.themeIdea.attributes[+el.dataset.a]; if (a) a[el.dataset.f] = el.value; });
    const n = document.getElementById("tname"); if (n) S.themeDraft.name = n.value;
  }
  function collectDict() {
    document.querySelectorAll("[data-d]").forEach((el) => { const r = S.dict[+el.dataset.d]; if (r) r[el.dataset.f] = el.value; });
  }

  const VIEWS = { home, questions, theme, intake, masking, newTheme, newWs };
  async function render() {
    try {
      $main.innerHTML = `<div class="mshop" data-shopbox>${shopSelect("shop2")}</div>` + await VIEWS[S.view]();
    } catch (e) {
      $main.innerHTML = `<div class="view"><p class="answered" style="background:var(--bad-soft);color:var(--bad)">${esc(e.message)}</p></div>`;
    }
    document.querySelectorAll(".menu button").forEach((b) => b.setAttribute("aria-current", b.dataset.v === S.view || (S.view === "theme" && b.dataset.v === "home") ? "page" : "false"));
    const qs = await api(q("/api/questions")).catch(() => []);
    const n = qs.filter((q) => !q.answer && q.to === "owner").length;
    const qb = document.getElementById("qbadge");
    qb.textContent = n; qb.hidden = n === 0;
  }
  function go(v) { S.view = v; render(); window.scrollTo(0, 0); }

  document.querySelector(".menu").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) go(b.dataset.v); });
  async function resetDemo() { await api("/api/demo/reset", { method: "POST" }); S.exitData = {}; S.pasteResult = null; S.aiRun = {}; go("home"); }
  $main.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.go) return go(t.dataset.go);
    if (t.dataset.theme) { S.themeId = t.dataset.theme; S.themeTab = "articles"; return go("theme"); }
    if (t.dataset.exit) { const [id, k] = t.dataset.exit.split(":"); S.themeId = id; S.exitTab = k; return go("theme"); }
    if (t.dataset.tt) { S.themeTab = t.dataset.tt; return render(); }
    if (t.dataset.et) { S.exitTab = t.dataset.et; return render(); }
    if (t.dataset.answer) {
      const id = t.dataset.answer, card = document.getElementById(`card-${id}`);
      const body = { choice: t.dataset.choice };
      const r = card.querySelector(".reason"); if (r && r.value.trim()) body.reason = r.value.trim();
      const facts = card.querySelectorAll("[data-fact]");
      if (facts.length) { body.fact = {}; facts.forEach((f) => { body.fact[f.dataset.fact] = f.value; }); }
      card.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try { await api(`/api/questions/${id}/answer`, { method: "POST", body: JSON.stringify(body) }); S.exitData = {}; render(); }
      catch (err) { const m = card.querySelector(".err"); m.textContent = err.message; m.hidden = false; card.querySelectorAll("button").forEach((b) => (b.disabled = false)); }
      return;
    }
    if (t.dataset.eval) {
      const key = t.dataset.eval, [set, led] = key.split(":");
      S.evalRes[key] = "running"; render();
      try { S.evalRes[key] = await api(`/api/masking/eval?set=${set}&ledger=${led === "1"}`, { method: "POST" }); }
      catch (err) { S.evalRes[key] = { error: err.message }; }
      return render();
    }
    if (t.id === "runAi") {
      const id = t.dataset.t; S.aiRun[id] = "running"; render();
      try { S.aiRun[id] = await api(`/api/themes/${id}/exits/ai/run`, { method: "POST" }); }
      catch (err) { S.aiRun[id] = { error: err.message }; }
      return render();
    }
    if (t.dataset.mode) return setMode(t.dataset.mode);
    if (t.dataset.reset) return resetDemo();
    if (t.dataset.wslink) {
      const url = `${location.origin}/#ws-${S.ws}`, m = t.closest("[data-shopbox]").querySelector(".wsmsg");
      try { await navigator.clipboard.writeText(url); m.textContent = "リンクをコピーしました。人に渡さないでください"; }
      catch (_) { m.textContent = url; }
      return;
    }
    if (t.id === "createWs") {
      const name = document.getElementById("wsname").value.trim();
      t.disabled = true;
      try { const w = await api("/api/workspaces", { method: "POST", body: JSON.stringify({ shop: name }) }); S.wsId = w.id; ls.set("untangle-ws", w.id); return setMode("ws"); }
      catch (err) { t.disabled = false; alert(err.message); }
      return;
    }
    if (t.id === "propose") {
      S.themeDraft = { name: document.getElementById("tname").value, desc: document.getElementById("tdesc").value, examples: document.getElementById("tex").value };
      const examples = S.themeDraft.examples.split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean);
      S.proposing = true; S.themeIdea = null; render();
      try { S.themeIdea = await api("/api/themes/propose", { method: "POST", body: JSON.stringify({ name: S.themeDraft.name, description: S.themeDraft.desc, examples }) }); }
      catch (err) { S.themeIdea = { error: err.message }; }
      S.proposing = false; return render();
    }
    if (t.dataset.delq !== undefined) { collectIdea(); S.themeIdea.questions.splice(+t.dataset.delq, 1); return render(); }
    if (t.id === "addAttr") { collectIdea(); S.themeIdea.attributes.push({ name: "", values: "" }); return render(); }
    if (t.id === "createTheme") {
      collectIdea();
      const body = { name: S.themeDraft.name, questions: S.themeIdea.questions, attributes: S.themeIdea.attributes.filter((a) => a.name.trim()), masked_examples: S.themeIdea.masked_examples || [] };
      t.disabled = true; t.textContent = "テーマを立てて、例を場面にしています…";
      try { await api("/api/themes", { method: "POST", body: JSON.stringify(body) }); S.themeIdea = null; S.themeDraft = { name: "", desc: "", examples: "" }; await loadShops(); return go("home"); }
      catch (err) { alert(err.message); }
      return;
    }
    if (t.id === "addDict") { collectDict(); S.dict.push({ surface: "", replacement: "", kind: "" }); return render(); }
    if (t.dataset.deld !== undefined) { collectDict(); S.dict.splice(+t.dataset.deld, 1); return render(); }
    if (t.id === "saveDict") {
      collectDict();
      try { const r = await api("/api/dictionary", { method: "PUT", body: JSON.stringify(S.dict.filter((x) => x.surface.trim())) }); S.dict = await api("/api/dictionary"); render(); setTimeout(() => { const m = document.getElementById("dictMsg"); if (m) m.textContent = `${r.count}件を保存しました`; }, 50); }
      catch (err) { document.getElementById("dictMsg").textContent = err.message; }
      return;
    }
    if (t.id === "localRead") return localRead();
    if (t.id === "approveSend") {
      const L = S.local, text = document.getElementById("reviewText").value;
      L.busy = "確かめた文を送り、Geminiが読み取っています…"; L.review = null; render();
      try { L.approvedResult = await api("/api/intake/approved", { method: "POST", body: JSON.stringify({ theme_id: S.intakeTheme, masked_text: text, approved: true }) }); }
      catch (err) { L.error = err.message; L.review = { masked: text, why: "サーバーが受け取りませんでした" }; }
      L.busy = null; return render();
    }
    if (t.id === "discardReview") { S.local.review = null; return render(); }
    if (t.dataset.sample !== undefined) { S.pasteText = samples()[+t.dataset.sample].text; S.pasteResult = null; return render(); }
    if (t.id === "doPaste") {
      S.pasteText = document.getElementById("paste").value; S.pasting = true; S.pasteResult = null; render();
      const sel = document.getElementById("itheme"); if (sel) S.intakeTheme = sel.value;
      try { S.pasteResult = await api("/api/intake", { method: "POST", body: JSON.stringify({ text: S.pasteText, shop: S.shop, theme_id: S.intakeTheme || null }) }); }
      catch (err) { S.pasteResult = { error: err.message }; }
      S.pasting = false; S.exitData = {};
      return render();
    }
  });
  (async () => {
    await loadShops();
    render();
  })();
})();
