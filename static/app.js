const chatEl = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const newChatBtn = document.getElementById("newChat");
const lightbox = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightboxImg");
const lightboxClose = document.querySelector(".lightbox-close");
const convListEl = document.getElementById("convList");
const convTitleEl = document.getElementById("convTitle");
const sidebarEl = document.querySelector(".sidebar");
const sidebarScrim = document.getElementById("sidebarScrim");
const menuBtn = document.getElementById("menuBtn");

let busy = false;
let currentConvId = null; // null = a fresh, unsaved conversation

// ---------- mobile sidebar toggle ----------

const isMobile = () => window.matchMedia("(max-width: 768px)").matches;

function openSidebar() {
  sidebarEl.classList.add("open");
  sidebarScrim.classList.add("open");
}
function closeSidebar() {
  sidebarEl.classList.remove("open");
  sidebarScrim.classList.remove("open");
}
function maybeCloseSidebar() {
  if (isMobile()) closeSidebar();
}

menuBtn?.addEventListener("click", () => {
  if (sidebarEl.classList.contains("open")) closeSidebar();
  else openSidebar();
});
sidebarScrim?.addEventListener("click", closeSidebar);

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(200, input.scrollHeight) + "px";
}
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

newChatBtn.addEventListener("click", () => {
  if (busy) return;
  startFreshConversation();
  maybeCloseSidebar();
});

lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (e) => {
  if (e.target === lightbox) closeLightbox();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLightbox();
});

function openLightbox(src) {
  lightboxImg.src = src;
  lightbox.classList.remove("hidden");
}
function closeLightbox() {
  lightbox.classList.add("hidden");
  lightboxImg.src = "";
}

// ---------- conversation list ----------

async function refreshConversations(selectId) {
  const res = await fetch("/api/conversations");
  if (!res.ok) return;
  const { conversations } = await res.json();
  convListEl.innerHTML = "";
  if (!conversations.length) {
    const hint = document.createElement("div");
    hint.className = "empty-hint";
    hint.textContent = "还没有历史会话";
    convListEl.appendChild(hint);
    return;
  }
  for (const c of conversations) {
    const item = document.createElement("div");
    item.className = "conv-item" + (c.id === selectId ? " active" : "");
    item.dataset.id = c.id;
    const title = document.createElement("span");
    title.textContent = c.title || "新会话";
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = formatTime(c.updated_at);
    item.appendChild(title);
    item.appendChild(meta);

    const del = document.createElement("button");
    del.className = "delete";
    del.title = "删除";
    del.textContent = "×";
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("删除这个会话？")) return;
      const r = await fetch(`/api/conversations/${c.id}`, { method: "DELETE" });
      if (!r.ok) return;
      if (currentConvId === c.id) startFreshConversation();
      else refreshConversations(currentConvId);
    });
    item.appendChild(del);

    item.addEventListener("click", () => {
      loadConversation(c.id);
      maybeCloseSidebar();
    });
    convListEl.appendChild(item);
  }
}

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

async function loadConversation(id) {
  if (busy) return;
  currentConvId = id;
  chatEl.innerHTML = "";
  const res = await fetch(`/api/conversations/${id}`);
  if (!res.ok) {
    renderEmpty();
    return;
  }
  const { conversation, messages } = await res.json();
  convTitleEl.textContent = conversation.title || "新会话";
  if (!messages.length) {
    renderEmpty();
  } else {
    for (const m of messages) renderHistoryMessage(m);
  }
  refreshConversations(id);
  scrollToBottom();
}

function renderHistoryMessage(m) {
  const node = makeMsg(m.role);
  if (m.role === "assistant" && m.intent) {
    node.wrap.classList.add(`intent-${m.intent}`);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = formatTime(m.created_at);
    node.bubble.appendChild(meta);
  }
  const body = document.createElement("div");
  node.bubble.appendChild(body);
  if (m.content) {
    const span = document.createElement("span");
    span.className = "text";
    span.textContent = m.content;
    body.appendChild(span);
  }
  if (m.image_path) {
    renderImage(body, m.image_path, "");
  } else if (m.role === "assistant" && m.intent === "image" && !m.image_path) {
    const e = document.createElement("div");
    e.className = "error";
    e.textContent = "❌ 这条图片生成当时失败了";
    body.appendChild(e);
  }
}

function startFreshConversation() {
  currentConvId = null;
  convTitleEl.textContent = "新会话";
  chatEl.innerHTML = "";
  renderEmpty();
  refreshConversations(null);
  input.focus();
}

// ---------- chat send / stream ----------

function makeMsg(role) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "我" : "AI";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  chatEl.appendChild(wrap);
  scrollToBottom();
  return { wrap, bubble };
}

function scrollToBottom() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

function clearEmpty() {
  const empty = chatEl.querySelector(".empty");
  if (empty) empty.remove();
}

function renderEmpty() {
  const el = document.createElement("div");
  el.className = "empty";
  el.innerHTML = `
    <h2>开始一段对话</h2>
    <div>需要画图就直接说"画…"，否则就普通聊天。</div>
    <div class="examples">
      <button data-q="用三句话解释一下相对论">用三句话解释一下相对论</button>
      <button data-q="画一只戴墨镜的橘猫，赛博朋克风格">画一只戴墨镜的橘猫，赛博朋克风格</button>
      <button data-q="写一段 Python 快速排序">写一段 Python 快速排序</button>
      <button data-q="生成一张清晨海边的极简日式插画">生成一张清晨海边的极简日式插画</button>
    </div>
  `;
  chatEl.appendChild(el);
  el.querySelectorAll("button[data-q]").forEach((b) => {
    b.addEventListener("click", () => {
      input.value = b.dataset.q;
      autosize();
      form.requestSubmit();
    });
  });
}

async function send(message) {
  if (busy || !message.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  clearEmpty();

  const userMsg = makeMsg("user");
  userMsg.bubble.textContent = message;

  const aiMsg = makeMsg("assistant");
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = "thinking…";
  aiMsg.bubble.appendChild(meta);

  const body = document.createElement("div");
  body.className = "body cursor";
  aiMsg.bubble.appendChild(body);

  let acc = "";

  try {
    const res = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation_id: currentConvId }),
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const data = line.slice(5).trim();
        if (!data) continue;
        let obj;
        try { obj = JSON.parse(data); } catch { continue; }

        if (obj.type === "intent") {
          aiMsg.wrap.classList.add(`intent-${obj.intent}`);
          meta.textContent = obj.intent === "image" ? "正在生成图片…" : "回复中…";
          if (obj.conversation_id && !currentConvId) {
            currentConvId = obj.conversation_id;
            refreshConversations(currentConvId);
          }
        } else if (obj.type === "status") {
          const s = document.createElement("div");
          s.className = "status";
          s.textContent = obj.message;
          body.appendChild(s);
          scrollToBottom();
        } else if (obj.type === "text_delta") {
          acc += obj.delta;
          renderText(body, acc);
          scrollToBottom();
        } else if (obj.type === "image") {
          renderImage(body, obj.url, obj.prompt);
          scrollToBottom();
        } else if (obj.type === "error") {
          const e = document.createElement("div");
          e.className = "error";
          e.textContent = "❌ " + obj.message;
          body.appendChild(e);
        } else if (obj.type === "done") {
          if (obj.full_text) acc = obj.full_text;
        }
      }
    }
    meta.textContent = "完成";
  } catch (err) {
    const e = document.createElement("div");
    e.className = "error";
    e.textContent = "请求失败：" + err.message;
    body.appendChild(e);
    meta.textContent = "出错了";
  } finally {
    body.classList.remove("cursor");
    busy = false;
    sendBtn.disabled = false;
    input.value = "";
    autosize();
    input.focus();
    refreshConversations(currentConvId);
  }
}

function renderText(container, text) {
  let span = container.querySelector(".text");
  if (!span) {
    span = document.createElement("span");
    span.className = "text";
    container.appendChild(span);
  }
  span.textContent = text;
}

function renderImage(container, url, prompt) {
  const card = document.createElement("div");
  card.className = "image-card";

  const img = document.createElement("img");
  img.src = url;
  img.alt = prompt || "generated image";
  img.loading = "lazy";
  img.addEventListener("click", () => openLightbox(url));

  const actions = document.createElement("div");
  actions.className = "image-actions";

  const filename = (url.split("/").pop() || "image.png");

  const dl = document.createElement("a");
  dl.href = url;
  dl.download = filename;
  dl.textContent = "💾 另存为…";
  dl.title = "下载到本地";

  const open = document.createElement("a");
  open.href = url;
  open.target = "_blank";
  open.rel = "noopener";
  open.textContent = "🔗 在新标签打开";

  actions.appendChild(dl);
  actions.appendChild(open);
  card.appendChild(img);
  card.appendChild(actions);
  container.appendChild(card);
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const v = input.value.trim();
  if (!v) return;
  send(v);
});

// boot
refreshConversations(null);
renderEmpty();
input.focus();
