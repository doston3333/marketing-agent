# Save data/ to the repo's default branch through the GitHub REST API (one commit per save).
# Routine sessions may not be allowed to `git push`, but they can call api.github.com with GITHUB_TOKEN
# (in Claude cloud environments the variable holds a proxy placeholder; the proxy injects the real token).
import base64, os, time
import requests
import agent_lib as A

REPO = os.environ.get("AIS_REPO", "doston3333/marketing-agent")
API = f"https://api.github.com/repos/{REPO}"


def _h():
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _req(method, path, **kw):
    r = requests.request(method, API + path, headers=_h(), timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub {method} {path}: {r.status_code} {r.text[:200]}")
    return r.json()


def save(message="agent: state", branch=None, tries=3):
    """Commit every data/*.json|*.md file that differs from the branch head. Returns a short status line."""
    branch = branch or _req("GET", "")["default_branch"]
    files = sorted(f for f in os.listdir(A.DATA) if f.endswith((".json", ".md")) and not f.endswith(".tmp"))
    for attempt in range(tries):
        head = _req("GET", f"/git/ref/heads/{branch}")["object"]["sha"]
        base_tree = _req("GET", f"/git/commits/{head}")["tree"]["sha"]
        current = {e["path"]: e["sha"] for e in _req("GET", f"/git/trees/{base_tree}?recursive=1")["tree"]
                   if e["path"].startswith("data/")}
        entries = []
        for f in files:
            raw = open(os.path.join(A.DATA, f), "rb").read()
            import hashlib
            blob_sha = hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
            if current.get(f"data/{f}") == blob_sha:
                continue
            b = _req("POST", "/git/blobs", json={"content": base64.b64encode(raw).decode(), "encoding": "base64"})
            entries.append({"path": f"data/{f}", "mode": "100644", "type": "blob", "sha": b["sha"]})
        if not entries:
            return f"nothing to save (data/ already matches {branch})"
        tree = _req("POST", "/git/trees", json={"base_tree": base_tree, "tree": entries})
        commit = _req("POST", "/git/commits", json={"message": message, "tree": tree["sha"], "parents": [head]})
        try:
            _req("PATCH", f"/git/refs/heads/{branch}", json={"sha": commit["sha"], "force": False})
            return f"saved {len(entries)} file(s) to {branch} as {commit['sha'][:7]}"
        except RuntimeError as ex:
            if "422" in str(ex) and attempt < tries - 1:  # branch moved (another run saved): retry on the new head
                time.sleep(2)
                continue
            raise
