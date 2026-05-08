"""ChatGPT-style web app with username-only auth and per-user history.

- /login          GET shows form, POST sets cookie
- /logout         clears cookie
- /               main UI (requires login)
- /api/conversations  list / create / delete
- /api/conversations/<id>  load messages
- /api/send       SSE stream: classifies intent, calls chat or image API,
                  persists user + assistant turns to SQLite, saves images
                  under data/images/<user>/<conv>/.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from pathlib import Path

import requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import auth
import db


API_BASE = os.environ.get("YUN_API_BASE", "https://api.yun-apis.com/v1")
API_KEY = os.environ.get("YUN_API_KEY", "sk-AgzoHXoFFahVmtVb4MzGl14alQ2wtXG24ilUH0LZgoMQxFu4")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-5.5")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-2")

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
db.init()


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


# ---------- intent classifier ----------

_IMAGE_HINTS = re.compile(
    r"(画|绘制|生成图|出图|配图|图片|插画|海报|图标|logo|draw|paint|render|illustrate|generate (?:an? )?image|create (?:an? )?image|make (?:an? )?picture)",
    re.IGNORECASE,
)


def classify_intent(message: str) -> str:
    if _IMAGE_HINTS.search(message):
        return "image"
    payload = {
        "model": CHAT_MODEL,
        "stream": True,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an intent classifier. Read the user's message and "
                    "reply with EXACTLY one lowercase word: 'image' if they want "
                    "you to generate or draw a picture/illustration/logo/photo, "
                    "otherwise 'text'. No punctuation, no other words."
                ),
            },
            {"role": "user", "content": message},
        ],
    }
    try:
        verdict = "".join(_iter_chat_text(payload, timeout=20)).strip().lower()
    except Exception:
        return "text"
    return "image" if "image" in verdict else "text"


# ---------- chat streaming ----------

def _iter_chat_text(payload: dict, timeout: int = 120):
    with requests.post(
        f"{API_BASE}/chat/completions",
        headers=auth_headers(),
        json=payload,
        stream=True,
        timeout=timeout,
    ) as r:
        r.raise_for_status()
        r.encoding = "utf-8"
        for raw_bytes in r.iter_lines(decode_unicode=False):
            if not raw_bytes:
                continue
            raw = raw_bytes.decode("utf-8", errors="replace")
            if not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            for ch in obj.get("choices", []) or []:
                delta = ch.get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content


def stream_chat_reply(history: list[dict], user_message: str):
    messages = list(history) + [{"role": "user", "content": user_message}]
    payload = {"model": CHAT_MODEL, "stream": True, "messages": messages}
    full: list[str] = []
    try:
        for piece in _iter_chat_text(payload, timeout=180):
            full.append(piece)
            yield _sse({"type": "text_delta", "delta": piece})
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:
            pass
        yield _sse({"type": "error", "message": f"chat HTTP {e.response.status_code}: {body}"})
        yield _sse({"type": "done", "full_text": "".join(full)})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"chat error: {e}"})
        yield _sse({"type": "done", "full_text": "".join(full)})
        return
    yield _sse({"type": "done", "full_text": "".join(full)})


# ---------- image generation ----------

IMAGE_MAX_WAIT_SECONDS = 100  # total budget for image generation including retries


def generate_image(prompt: str, dest_dir: Path, url_prefix: str, size: str = "1024x1024"):
    """Yield SSE events; saves the produced image under dest_dir.

    Retries on 5xx / network errors until a success or the total budget
    (IMAGE_MAX_WAIT_SECONDS) is exhausted, whichever comes first.
    """
    yield _sse({"type": "status", "message": f"正在用 {IMAGE_MODEL} 生成图片（最长等 {IMAGE_MAX_WAIT_SECONDS}s）…"})
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "n": 1, "size": size}

    deadline = time.monotonic() + IMAGE_MAX_WAIT_SECONDS
    last_err = None
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        # Cap each call's read timeout by the remaining budget so a slow upstream
        # can't blow past the user-visible wait limit.
        remaining = deadline - time.monotonic()
        per_call_timeout = max(5.0, remaining)
        try:
            r = requests.post(
                f"{API_BASE}/images/generations",
                headers=auth_headers(),
                json=payload,
                timeout=per_call_timeout,
            )
        except requests.RequestException as e:
            last_err = f"network error: {e}"
            if time.monotonic() + 1.5 >= deadline:
                break
            time.sleep(1.5)
            continue

        if r.status_code == 200:
            try:
                body = r.json()
            except ValueError:
                last_err = f"non-JSON response: {r.text[:200]}"
                break
            saved = _persist_image(body, dest_dir, url_prefix)
            if not saved:
                last_err = f"unexpected image response shape: {str(body)[:300]}"
                break
            url, fs_path = saved
            yield _sse({"type": "image", "url": url, "prompt": prompt, "fs_path": fs_path})
            yield _sse({"type": "done", "full_text": ""})
            return

        last_err = f"HTTP {r.status_code}: {r.text[:300]}"
        if r.status_code < 500:
            # Client error — retrying won't help.
            break
        # Backoff capped by remaining budget.
        backoff = min(2.0 * attempt, 8.0)
        if time.monotonic() + backoff >= deadline:
            break
        time.sleep(backoff)

    yield _sse({"type": "error", "message": f"图片生成失败：{last_err or '超时'}"})
    yield _sse({"type": "done", "full_text": ""})


def _persist_image(body: dict, dest_dir: Path, url_prefix: str):
    items = body.get("data") or []
    if not items:
        return None
    item = items[0]
    dest_dir.mkdir(parents=True, exist_ok=True)
    name_stem = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"

    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
        path = dest_dir / f"{name_stem}.png"
        path.write_bytes(raw)
        return f"{url_prefix}/{path.name}", str(path)

    url = item.get("url")
    if url:
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            ext = ".png"
            ctype = resp.headers.get("content-type", "")
            if "jpeg" in ctype:
                ext = ".jpg"
            elif "webp" in ctype:
                ext = ".webp"
            path = dest_dir / f"{name_stem}{ext}"
            path.write_bytes(resp.content)
            return f"{url_prefix}/{path.name}", str(path)
        except requests.RequestException:
            return url, ""
    return None


# ---------- helpers ----------

def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def _derive_title(text: str) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t[:30] or "新会话"


# ---------- routes: auth ----------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        if auth.current_user():
            return redirect(url_for("index"))
        return render_template("login.html", error=None)

    username = (request.form.get("username") or "").strip()
    if not auth.valid_username(username):
        return render_template(
            "login.html",
            error="用户名不合法（1-32 位，支持字母/数字/下划线/连字符/中文）",
        ), 400
    db.upsert_user(username)
    nxt = request.args.get("next") or url_for("index")
    if not nxt.startswith("/"):
        nxt = url_for("index")
    return auth.attach_login_cookie(make_response(redirect(nxt)), username)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    return auth.clear_login_cookie(make_response(redirect(url_for("login_page"))))


# ---------- routes: pages ----------

@app.route("/")
@auth.login_required
def index():
    return render_template("index.html", username=auth.current_user())


# ---------- routes: images ----------

@app.route("/images/<username>/<int:conv_id>/<path:name>")
@auth.login_required
def serve_image(username: str, conv_id: int, name: str):
    me = auth.current_user()
    if me != username:
        abort(403)
    folder = IMAGES_DIR / username / str(conv_id)
    if not folder.is_dir():
        abort(404)
    return send_from_directory(folder, name)


# ---------- routes: conversations ----------

@app.route("/api/me")
@auth.login_required
def api_me():
    return jsonify({"username": auth.current_user()})


@app.route("/api/conversations", methods=["GET", "POST"])
@auth.login_required
def api_conversations():
    me = auth.current_user()
    if request.method == "GET":
        return jsonify({"conversations": db.list_conversations(me)})
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "新会话").strip()[:120] or "新会话"
    cid = db.create_conversation(me, title)
    return jsonify({"id": cid, "title": title})


@app.route("/api/conversations/<int:conv_id>", methods=["GET", "DELETE", "PATCH"])
@auth.login_required
def api_conversation(conv_id: int):
    me = auth.current_user()
    conv = db.get_conversation(conv_id, me)
    if not conv:
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        return jsonify({"conversation": conv, "messages": db.list_messages(conv_id)})
    if request.method == "DELETE":
        db.delete_conversation(conv_id, me)
        return jsonify({"ok": True})
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if title:
        db.rename_conversation(conv_id, me, title)
    return jsonify({"ok": True})


# ---------- routes: send ----------

@app.route("/api/send", methods=["POST"])
@auth.login_required
def api_send():
    me = auth.current_user()
    body = request.get_json(force=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    conv_id = body.get("conversation_id")
    if not conv_id:
        conv_id = db.create_conversation(me, _derive_title(message))
    else:
        conv = db.get_conversation(int(conv_id), me)
        if not conv:
            return jsonify({"error": "conversation not found"}), 404
        conv_id = conv["id"]
        # If conv title is still the default, retitle from this first user message.
        if conv["title"] == "新会话":
            db.rename_conversation(conv_id, me, _derive_title(message))

    intent = classify_intent(message)
    history = db.text_history_for_llm(conv_id, limit=20) if intent == "text" else []

    # Persist the user turn before streaming back.
    db.add_message(conv_id, "user", content=message, intent=None)

    def gen():
        yield _sse({"type": "intent", "intent": intent, "conversation_id": conv_id})

        if intent == "image":
            dest = IMAGES_DIR / me / str(conv_id)
            url_prefix = f"/images/{me}/{conv_id}"
            captured: dict[str, str] = {}
            for ev in generate_image(message, dest, url_prefix):
                # Inspect each event so we can capture the saved URL for DB.
                try:
                    line = ev.decode("utf-8")
                    payload = json.loads(line[5:].strip())
                    if payload.get("type") == "image":
                        captured["url"] = payload["url"]
                except Exception:
                    pass
                yield ev
            db.add_message(
                conv_id,
                "assistant",
                content="" if captured.get("url") else "(image generation failed)",
                intent="image",
                image_path=captured.get("url"),
            )
        else:
            full: list[str] = []
            for ev in stream_chat_reply(history, message):
                try:
                    line = ev.decode("utf-8")
                    payload = json.loads(line[5:].strip())
                    if payload.get("type") == "text_delta":
                        full.append(payload.get("delta", ""))
                    elif payload.get("type") == "done" and payload.get("full_text"):
                        full = [payload["full_text"]]
                except Exception:
                    pass
                yield ev
            db.add_message(conv_id, "assistant", content="".join(full), intent="text")

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# ---------- health ----------

@app.route("/healthz")
def healthz():
    return "ok\n", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
