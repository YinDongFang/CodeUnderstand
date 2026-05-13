/** P3 · Hash 路由 Web UI · 调用同域 /api/v1 */

const api = "/api/v1";

type Stage = {
  stage: string;
  status: string;
  started_at?: string | null;
  ended_at?: string | null;
  log_tail?: string;
};
type Job = {
  job_id: string;
  repo: string;
  zip_url: string;
  status: string;
  created_at: string;
  artifact_zip_path: string;
  session_id?: string;
  stages: Stage[];
};

async function readErrorResp(r: Response): Promise<string> {
  try {
    const j: unknown = await r.json();
    if (typeof j === "object" && j !== null && "detail" in j) {
      const d = (j as { detail: unknown }).detail;
      return typeof d === "string" ? d : JSON.stringify(j);
    }
    return JSON.stringify(j);
  } catch {
    return await r.text();
  }
}

async function jfetch<T>(
  path: string,
  init?: RequestInit,
): Promise<{ ok: boolean; status: number; body: T | null }> {
  const r = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (r.status === 204) {
    return { ok: r.ok, status: r.status, body: null };
  }
  try {
    return { ok: r.ok, status: r.status, body: (await r.json()) as T };
  } catch {
    throw new Error(await readErrorResp(r));
  }
}

function parseHash(): string[] {
  const h = location.hash.slice(1) || "/";
  return h.split("/").filter(Boolean);
}

function statusClass(st: string): string {
  if (st === "success") return "badge-success";
  if (st === "failed" || st === "cancelled") return "badge-fail";
  if (st === "running") return "badge-run";
  return "badge-pending";
}

async function route(): Promise<void> {
  const app = document.getElementById("app");
  if (!app) return;

  const parts = parseHash();
  if (parts.length === 0) {
    app.replaceChildren(await viewList());
    return;
  }
  if (parts[0] === "jobs") {
    const id = decodeURIComponent(parts[1] ?? "");
    if (!id) {
      location.hash = "#/";
      return;
    }
    if (parts[2] === "rewrite") {
      app.replaceChildren(await viewRewrite(id));
      return;
    }
    app.replaceChildren(await viewJob(id));
    return;
  }
  app.innerHTML =
    `<div class="card">未知路由</div><p><a href="#/">首页</a></p>`;
}

window.addEventListener("hashchange", () => {
  route().catch(console.error);
});

async function viewList(): Promise<HTMLElement> {
  const wrap = document.createElement("div");
  wrap.innerHTML = `<h1>作业列表</h1>
    <div class="card">
      <label>新建（GitHub archive ZIP）</label><br/>
      <input type="url" id="zip" placeholder="https://github.com/o/r/archive/refs/heads/main.zip"/>
      <br/><label style="margin-top:.5rem;display:inline-block">job_id（可选）</label>
      <input type="text" id="jid" placeholder="留空自动生成"/>
      <br/><button type="button" id="mk">创建</button>
      <button type="button" id="rl">刷新</button>
      <span id="msg"></span>
    </div>
    <div id="lst"></div>`;

  const loadList = async () => {
    const r = await jfetch<Job[]>(`${api}/jobs`);
    const lst = wrap.querySelector("#lst")!;
    if (!r.ok || !r.body) {
      lst.innerHTML = `<p class="badge-fail">加载失败</p>`;
      return;
    }
    lst.innerHTML =
      "<h2>全部</h2>" +
      (r.body.length
        ? r.body
            .map((j) => {
              const e = encodeURIComponent(j.job_id);
              return `<div class="card"><strong><a href="#/jobs/${e}">${j.job_id}</a></strong>
                <span class="${statusClass(j.status)}"> ${j.status}</span> · ${j.repo}<br/><small>${j.created_at}</small></div>`;
            })
            .join("")
        : `<p class="badge-pending">暂无作业</p>`);
  };

  wrap.querySelector("#mk")!.addEventListener("click", async () => {
    const zip = (wrap.querySelector("#zip") as HTMLInputElement).value.trim();
    const jid = (wrap.querySelector("#jid") as HTMLInputElement).value.trim();
    const msg = wrap.querySelector("#msg")!;
    msg.textContent = "";
    const body: Record<string, string> = { zip_url: zip };
    if (jid) body.job_id = jid;
    const r = await fetch(`${api}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      msg.textContent = await readErrorResp(r);
      return;
    }
    const jb = (await r.json()) as Job;
    location.hash = `#/jobs/${encodeURIComponent(jb.job_id)}`;
  });
  wrap.querySelector("#rl")!.addEventListener("click", () => {
    loadList().catch(console.error);
  });
  await loadList();
  return wrap;
}

async function viewJob(jobId: string): Promise<HTMLElement> {
  const wrap = document.createElement("div");
  const esc = encodeURIComponent(jobId);
  wrap.innerHTML = `<p><a href="#/">« 列表</a> <button type="button" id="rf">刷新</button></p>
    <div id="root"></div>
    <div class="card" style="margin-top:.5rem"><label><input type="checkbox" id="tail"/> SSE 事件（步骤）</label>
    <pre id="ev" class="log" style="display:none"></pre></div>`;

  let es: EventSource | null = null;

  const load = async () => {
    const root = wrap.querySelector("#root")!;
    const r = await jfetch<Job>(`${api}/jobs/${esc}`);
    if (!r.ok || !r.body) {
      root.innerHTML = `<p class="badge-fail">加载失败或无此作业</p>`;
      return;
    }
    const j = r.body;
    const compileOk =
      j.stages.find((s) => s.stage === "compile")?.status === "success";
    const nextStage = (): string | null => {
      for (const s of j.stages) {
        if (s.status !== "success") return s.stage;
      }
      return null;
    };
    const ns = nextStage();
    root.innerHTML = `<h2>${escapeHtml(jobId)}</h2>
      <div class="card">
        <p>repo <strong>${escapeHtml(j.repo)}</strong></p>
        <p>ZIP <small>${escapeHtml(j.zip_url)}</small></p>
        <p>状态 <span class="${statusClass(j.status)}">${j.status}</span></p>
        <p>${j.artifact_zip_path ? `<a href="${api}/jobs/${esc}/artifacts/zip">下载 zip</a>` : "尚无 zip"}</p>
      </div>
      <div class="card"><h3>阶段</h3>${j.stages
        .map(
          (s) =>
            `<div><strong>${s.stage}</strong> <span class="${statusClass(
              s.status,
            )}">${s.status}</span>` +
            (s.log_tail
              ? ` <small>${escapeHtml(s.log_tail.slice(0, 300))}</small>`
              : "") +
            "</div>",
        )
        .join("")}</div>
      <div class="card"><h3>操作</h3>
      ${
        ns
          ? `<button type="button" id="run">运行 ${ns}</button>`
          : "<span class='badge-success'>全流程 success</span>"
      }
      <button type="button" id="rbd">rerun build</button>
      <button type="button" id="can">cancel</button>
      <button type="button" id="del" style="color:#faa">delete</button>
      <a href="#/jobs/${esc}/rewrite"><button type="button" ${
        compileOk ? "" : "disabled"
      }>编辑题目</button></a></div>`;

    if (ns) {
      root.querySelector("#run")!.addEventListener("click", async () => {
        await fetch(`${api}/jobs/${esc}/stages/${ns}/run`, { method: "POST" });
        await load();
      });
    }
    root.querySelector("#rbd")!.addEventListener("click", async () => {
      await fetch(`${api}/jobs/${esc}/stages/build/rerun`, { method: "POST" });
      await load();
    });
    root.querySelector("#can")!.addEventListener("click", async () => {
      await fetch(`${api}/jobs/${esc}/cancel`, { method: "POST" });
      await load();
    });
    root.querySelector("#del")!.addEventListener("click", async () => {
      await fetch(`${api}/jobs/${esc}`, { method: "DELETE" });
      location.hash = "#/";
    });
  };

  await load();
  wrap.querySelector("#rf")!.addEventListener("click", () => {
    load().catch(console.error);
  });

  const cb = wrap.querySelector("#tail") as HTMLInputElement;
  const evBox = wrap.querySelector("#ev") as HTMLPreElement;
  cb.addEventListener("change", () => {
    es?.close();
    es = null;
    evBox.style.display = "none";
    if (!cb.checked) return;
    es = new EventSource(`${api}/jobs/${esc}/events`);
    evBox.style.display = "block";
    evBox.textContent = "";
    es.onmessage = (e: MessageEvent) => {
      evBox.textContent += `${e.data}\n`;
      evBox.scrollTop = evBox.scrollHeight;
    };
  });

  return wrap;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => {
    const m: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return m[c] ?? c;
  });
}

async function viewRewrite(jobId: string): Promise<HTMLElement> {
  const esc = encodeURIComponent(jobId);
  const wrap = document.createElement("div");
  wrap.innerHTML = `<p><a href="#/jobs/${esc}">« 详情</a></p>
    <div class="card"><h3>会话题目</h3><p><small>每行一条单行用户提问文本；顺序与 JSONL 中一致。</small></p>
      <textarea id="ta" rows="16"></textarea>
      <p><button type="button" id="save">保存到会话 JSONL</button>
      <button type="button" id="build">运行 build</button></p>
      <p id="er" class="badge-fail"></p></div>`;
  const er = wrap.querySelector("#er") as HTMLElement;
  const loadQ = async () => {
    const r = await jfetch<{ lines: string[] }>(
      `${api}/jobs/${esc}/rewrite/questions`,
    );
    if (!r.ok || !r.body) {
      er.textContent =
        typeof (r.body as { detail?: string } | null)?.detail === "string"
          ? (r.body as { detail: string }).detail
          : "无法读取（需 compile 成功且存在会话文件）";
      return;
    }
    (wrap.querySelector("#ta") as HTMLTextAreaElement).value = r.body.lines.join(
      "\n",
    );
  };
  await loadQ();
  wrap.querySelector("#save")!.addEventListener("click", async () => {
    er.textContent = "";
    const raw = (wrap.querySelector("#ta") as HTMLTextAreaElement).value;
    const lines = raw.split("\n").filter((ln) => ln.length > 0);
    const r = await fetch(`${api}/jobs/${esc}/rewrite/questions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines }),
    });
    if (!r.ok) {
      er.textContent = await readErrorResp(r);
      return;
    }
    er.textContent = "已保存";
    er.className = "badge-success";
  });
  wrap.querySelector("#build")!.addEventListener("click", async () => {
    er.textContent = "";
    er.className = "badge-fail";
    await fetch(`${api}/jobs/${esc}/stages/build/run`, { method: "POST" });
    er.textContent = "已触发 build（见详情 SSE/刷新）";
  });
  return wrap;
}

route().catch(console.error);
