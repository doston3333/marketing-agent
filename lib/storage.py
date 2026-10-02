# Memory that survives fresh cloud sessions without a git push.
# The routines start from a fresh clone and may not be allowed to push, so the data/ folder is also kept as a
# pinned .tar.gz file in a private Telegram channel ("storage chat") that only the bot and the owner can see.
#   restore(): download the pinned bundle and unpack it over data/ when it is newer than the repo copy
#   backup():  upload data/ as a new pinned bundle and delete the previous one
import io, json, os, tarfile, time
import requests
import agent_lib as A

META = "meta.json"
CONFIG = os.path.join(A.ROOT, "config.json")  # committed once: {"storage_chat": -100...}


def storage_chat():
    if os.environ.get("AIS_STORAGE_CHAT"):
        return int(os.environ["AIS_STORAGE_CHAT"])
    try:
        return json.load(open(CONFIG)).get("storage_chat")
    except Exception:
        return None


def _meta():
    try:
        return json.load(open(os.path.join(A.DATA, META)))
    except Exception:
        return {"saved_at": 0}


def bundle_bytes():
    meta = {"saved_at": time.time()}
    with open(os.path.join(A.DATA, META), "w") as f:
        json.dump(meta, f)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name in sorted(os.listdir(A.DATA)):
            if name.endswith((".json", ".md")) and not name.endswith(".tmp"):
                t.add(os.path.join(A.DATA, name), arcname=name)
    return buf.getvalue(), meta


def backup():
    chat = storage_chat()
    if not chat:
        return "no storage chat configured (config.json storage_chat)"
    data, meta = bundle_bytes()
    path = os.path.join(A.WORK, "ais-memory.tar.gz")
    with open(path, "wb") as f:
        f.write(data)
    prev = None
    try:
        prev = (A.tg("getChat", {"chat_id": chat})["result"].get("pinned_message") or {}).get("message_id")
    except Exception:
        pass
    d = A.tg("sendDocument", {"chat_id": chat, "caption": f"ais-memory {time.strftime('%Y-%m-%d %H:%M', time.gmtime(meta['saved_at']))} UTC",
                              "disable_notification": True}, files={"document": path})
    mid = d["result"]["message_id"]
    A.tg("pinChatMessage", {"chat_id": chat, "message_id": mid, "disable_notification": True})
    if prev and prev != mid:
        try:
            A.tg("deleteMessage", {"chat_id": chat, "message_id": prev}, tries=1)
        except Exception:
            pass  # older than 48h or already gone: harmless, the pin is what counts
    return f"backed up {len(data) // 1024} KB to storage chat (message {mid})"


def restore():
    chat = storage_chat()
    if not chat:
        return "no storage chat configured; using the repo copy"
    pin = A.tg("getChat", {"chat_id": chat})["result"].get("pinned_message") or {}
    doc = pin.get("document")
    if not doc:
        return "storage chat has no pinned bundle yet; using the repo copy"
    path = A.tg("getFile", {"file_id": doc["file_id"]})["result"]["file_path"]
    tok = A.secrets()["telegram_bot_token"]
    r = requests.get(f"https://api.telegram.org/file/bot{tok}/{path}", timeout=60)
    r.raise_for_status()
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as t:
        remote_meta = json.load(t.extractfile(META)) if META in t.getnames() else {"saved_at": 0}
        if remote_meta["saved_at"] <= _meta().get("saved_at", 0):
            return "repo copy is newer than or equal to the pinned bundle; kept it"
        for m in t.getmembers():
            if m.isfile() and "/" not in m.name and m.name.endswith((".json", ".md")):
                with open(os.path.join(A.DATA, m.name), "wb") as f:
                    f.write(t.extractfile(m).read())
    return f"restored data/ from the pinned bundle of {time.strftime('%Y-%m-%d %H:%M', time.gmtime(remote_meta['saved_at']))} UTC"


def find_new_channels(state):
    """Channels where the bot was just made admin (from my_chat_member updates kept by poll)."""
    return state.get("settings", {}).get("admin_channels", [])
