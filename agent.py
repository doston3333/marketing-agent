#!/usr/bin/env python3
"""Command line for the AI Station marketing agent. Every command loads state from data/ and saves it back.

  python3 agent.py check                      # env + token sanity check (getMe), no side effects
  python3 agent.py peek                       # read-only: count of waiting Telegram updates and pending actions
  python3 agent.py start                      # daily run: poll, queue lead actions for the hourly handler, print leads
  python3 agent.py actions                    # hourly run: consume queued + new actions, print them as JSON
  python3 agent.py knowledge                  # voice guide + learnings + recent topics/posts/approved captions
  python3 agent.py show POST_ID               # print a stored post as JSON (to edit and resend)
  python3 agent.py lint FILE                  # lint a post JSON file; exit 1 if there are problems
  python3 agent.py send FILE [--id ID] [--note TEXT] [--history] [--no-minimax]
                                              # render + send the package; --history records a new topic
  python3 agent.py ack CB_ID TEXT             # answer a button press
  python3 agent.py status POST_ID STATUS      # approved / rejected / sent
  python3 agent.py say HTML [--chat CHAT_ID]  # message the leads (or one chat)
  python3 agent.py learn TEXT                 # store a standing rule from the leads' feedback
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import agent_lib as A  # noqa: E402


def _post(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="AI Station marketing agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("peek")
    sub.add_parser("start")
    sub.add_parser("actions")
    sub.add_parser("knowledge")
    p = sub.add_parser("show"); p.add_argument("post_id")
    p = sub.add_parser("lint"); p.add_argument("file")
    p = sub.add_parser("send"); p.add_argument("file"); p.add_argument("--id"); p.add_argument("--note")
    p.add_argument("--history", action="store_true"); p.add_argument("--no-minimax", action="store_true")
    p = sub.add_parser("ack"); p.add_argument("cb_id"); p.add_argument("text", nargs="?", default="")
    p = sub.add_parser("status"); p.add_argument("post_id"); p.add_argument("status")
    p = sub.add_parser("say"); p.add_argument("html"); p.add_argument("--chat", type=int)
    p = sub.add_parser("learn"); p.add_argument("text")
    a = ap.parse_args()

    if a.cmd == "check":
        s = A.secrets()
        print("TELEGRAM_BOT_TOKEN:", "set" if s.get("telegram_bot_token") else "MISSING")
        print("MINIMAX_API_KEY:", "set" if s.get("minimax_key") else "MISSING")
        if s.get("telegram_bot_token"):
            me = A.tg("getMe", {})["result"]
            print("bot:", "@" + me.get("username", "?"))
        st = A.load_state()
        print("leads:", st["leads"], "offset:", st["offset"], "posts:", len(st["posts"]))
        return
    if a.cmd == "peek":
        st = A.load_state()
        print("UPDATES", len(A.peek()), "PENDING", len(st["pending"]))
        return

    state = A.load_state()
    if a.cmd == "start":
        acts = A.poll(state)
        queued = [x for x in acts if x["type"] in ("button", "text")]
        if queued:
            state["pending"] += queued
            A.save_state(state)
        print(json.dumps({"leads": state["leads"], "lead_names": state["lead_names"],
                          "registered": [x for x in acts if x["type"] == "registered"],
                          "queued_for_hourly": len(queued)}, ensure_ascii=False, indent=1))
    elif a.cmd == "actions":
        print(json.dumps(A.take_actions(state), ensure_ascii=False, indent=1))
    elif a.cmd == "knowledge":
        A.load_knowledge(state)
    elif a.cmd == "show":
        post = state["posts"].get(a.post_id)
        if not post:
            sys.exit(f"no post {a.post_id}; known: {', '.join(sorted(state['posts']))}")
        print(json.dumps(post, ensure_ascii=False, indent=1))
    elif a.cmd == "lint":
        probs = A.lint(_post(a.file))
        print(json.dumps(probs, ensure_ascii=False, indent=1))
        sys.exit(1 if probs else 0)
    elif a.cmd == "send":
        post = _post(a.file)
        probs = A.lint(post)
        if probs:
            sys.exit("lint failed, not sending:\n" + "\n".join(probs))
        pid = a.id or A.new_post_id(state)
        post.pop("file_ids", None)
        post.setdefault("status", "sent")
        A.send_package(state, pid, post, note=a.note, use_minimax=not a.no_minimax)
        if a.history:
            state["history"].append({"date": pid[:8], "post_id": pid, "topic": post.get("topic", ""),
                                     "source": (post.get("sources") or [""])[0]})
            A.save_state(state)
        print("POST_ID", pid)
    elif a.cmd == "ack":
        A.ack(a.cb_id, a.text)
    elif a.cmd == "status":
        A.set_status(state, a.post_id, a.status)
        A.save_state(state)
    elif a.cmd == "say":
        print(A.say(state, a.html, [a.chat] if a.chat else None))
    elif a.cmd == "learn":
        A.add_learning(state, a.text)
        A.save_state(state)


if __name__ == "__main__":
    main()
