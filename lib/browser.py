# Headless Chromium for the tender agent (Playwright). One browser per run, one context per site so each
# site keeps its own login cookies. Cookies are cached in work/browser/ for the session only (never committed).
#
#   setup()                       make Chromium trust the environment's CA bundle (cloud proxy) and find a binary
#   Browser().page_for(site)      a page in that site's context, logged in first when the site has credentials
#   Browser().fetch(site, url)    {"url", "title", "text", "links", "captured", "blocked"} for one page
import glob, json, os, re, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.environ.get("AIS_WORK_DIR", os.path.join(ROOT, "work"))
STATE_DIR = os.path.join(WORK, "browser")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
NSSDB = os.path.expanduser("~/.pki/nssdb")
BLOCK_MARKERS = ("Just a moment...", "Attention Required!", "Access denied", "Checking your browser")


# ---------------------------------------------------------------- environment
def _ca_bundle():
    for env in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        p = os.environ.get(env)
        if p and os.path.exists(p):
            return p
    return None


def trust_ca_bundle():
    """Chromium reads trust anchors from the NSS store, not from SSL_CERT_FILE. Behind the cloud egress
    proxy every site fails with ERR_CERT_AUTHORITY_INVALID until the bundle is imported. Verification stays on."""
    bundle = _ca_bundle()
    if not bundle:
        return "no CA bundle in the environment; using Chromium defaults"
    marker = os.path.join(NSSDB, ".ais-imported")
    stamp = f"{bundle}:{os.path.getmtime(bundle)}"
    if os.path.exists(marker) and open(marker).read() == stamp:
        return "CA bundle already trusted"
    if not shutil.which("certutil"):
        try:
            subprocess.run(["apt-get", "install", "-y", "-q", "libnss3-tools"], capture_output=True, timeout=300)
            if not shutil.which("certutil"):
                subprocess.run(["apt-get", "update", "-q"], capture_output=True, timeout=300)
                subprocess.run(["apt-get", "install", "-y", "-q", "libnss3-tools"], capture_output=True, timeout=300)
        except Exception:
            pass
    if not shutil.which("certutil"):
        return "certutil missing (apt-get install libnss3-tools); HTTPS pages may fail with ERR_CERT_AUTHORITY_INVALID"
    os.makedirs(NSSDB, exist_ok=True)
    if not os.path.exists(os.path.join(NSSDB, "cert9.db")):
        subprocess.run(["certutil", "-N", "-d", f"sql:{NSSDB}", "--empty-password"], capture_output=True)
    pems = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", open(bundle).read(), re.S)
    tmp = os.path.join(STATE_DIR, "ca.pem")
    os.makedirs(STATE_DIR, exist_ok=True)
    ok = 0
    for i, pem in enumerate(pems):
        with open(tmp, "w") as f:
            f.write(pem + "\n")
        r = subprocess.run(["certutil", "-A", "-d", f"sql:{NSSDB}", "-t", "C,,", "-n", f"ais-ca-{i}", "-i", tmp],
                           capture_output=True)
        ok += r.returncode == 0
    with open(marker, "w") as f:
        f.write(stamp)
    return f"imported {ok}/{len(pems)} CA certificates into {NSSDB}"


def chromium_path():
    """Playwright's own build when it is installed, else the pre-installed one (cloud image: /opt/pw-browsers)."""
    if os.environ.get("TENDER_CHROMIUM"):
        return os.environ["TENDER_CHROMIUM"]
    for pat in ("/opt/pw-browsers/chromium", "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")):
        for p in sorted(glob.glob(pat), reverse=True):
            if os.path.exists(p):
                return p
    return None


def setup():
    return {"ca": trust_ca_bundle(), "chromium": chromium_path() or "playwright default"}


def creds(site):
    """(user, password) from TENDER_<SITE_ID>_USER / _PASS (site id upper-cased, '-' -> '_'), or (None, None)."""
    key = re.sub(r"\W", "_", site["id"]).upper()
    return os.environ.get(f"TENDER_{key}_USER"), os.environ.get(f"TENDER_{key}_PASS")


def env_names(site):
    key = re.sub(r"\W", "_", site["id"]).upper()
    return f"TENDER_{key}_USER", f"TENDER_{key}_PASS"


# ---------------------------------------------------------------- browser
def _merge_body(body, patch):
    """Overlay patch onto a JSON request body (keys matched case-insensitively), e.g. a bigger page size."""
    try:
        data = json.loads(body or "{}")
    except Exception:
        if body and "=" in body:  # form-encoded body (a=1&b=2)
            from urllib.parse import parse_qsl, urlencode
            pairs = dict(parse_qsl(body, keep_blank_values=True))
            pairs.update({k: str(v) for k, v in patch.items()})
            return urlencode(pairs)
        return body
    if not isinstance(data, dict):
        return body
    lower = {k.lower(): k for k in data}
    for k, v in patch.items():
        data[lower.get(k.lower(), k)] = v
    return json.dumps(data)


def _visible(selectors):
    """Only visible fields: login pages often carry hidden twins (header search boxes, modal copies)."""
    return ", ".join(f"{x}:visible" for x in selectors)


def _visible_text(page):
    try:
        return page.inner_text("body", timeout=10000)
    except Exception:
        try:
            return page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return ""


# Every anchor with the text of its nearest "card" ancestor (the smallest ancestor that holds more text than
# the link itself but not a whole list). Lets one extractor serve sites with very different markup.
_LINKS_JS = r"""
(maxCard) => {
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href;
    if (!href || href.startsWith('javascript') || href.startsWith('mailto') || href.startsWith('tel')) continue;
    const text = (a.innerText || a.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
    let card = a, el = a;
    for (let i = 0; i < 8 && el.parentElement; i++) {
      el = el.parentElement;
      const t = (el.innerText || '').trim();
      if (t.length > maxCard) break;
      card = el;
      if (['TR', 'LI', 'ARTICLE'].includes(el.tagName)) break;
    }
    out.push({href, text: text.slice(0, 300), card: (card.innerText || '').trim().slice(0, maxCard)});
  }
  return out;
}
"""


class Browser:
    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        setup()
        self._pw = sync_playwright().start()
        kw = {"headless": headless, "args": ["--disable-blink-features=AutomationControlled"]}
        try:
            self.browser = self._pw.chromium.launch(**kw)
        except Exception:
            path = chromium_path()
            if not path:
                raise
            self.browser = self._pw.chromium.launch(executable_path=path, **kw)
        self.contexts, self.logins = {}, {}
        os.makedirs(STATE_DIR, exist_ok=True)

    def _keeps_state(self, site):
        return bool(site.get("login")) and all(creds(site))

    def close(self):
        for sid, ctx in self.contexts.items():
            if self.logins.get(sid) == "ok":
                try:
                    ctx.storage_state(path=os.path.join(STATE_DIR, f"{sid}.json"))
                except Exception:
                    pass
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def context(self, site):
        sid = site["id"]
        if sid not in self.contexts:
            kw = {"user_agent": UA, "locale": site.get("locale", "en-US"), "viewport": {"width": 1366, "height": 900}}
            st = os.path.join(STATE_DIR, f"{sid}.json")
            if self._keeps_state(site) and os.path.exists(st):  # only sign-in cookies are worth carrying over;
                kw["storage_state"] = st                       # saved page state (e.g. table sizes) breaks feeds
            ctx = self.browser.new_context(**kw)
            ctx.set_default_timeout(30000)
            self.contexts[sid] = ctx
        return self.contexts[sid]

    # ---------------------------------------------------------------- login
    def login(self, site, force=False):
        """Sign in with TENDER_<ID>_USER/_PASS using site["login"] (url + optional selectors; sensible
        defaults otherwise). Returns "ok", "no credentials", "not configured" or "failed: ...". Never raises."""
        sid = site["id"]
        if sid in self.logins and not force:
            return self.logins[sid]
        cfg = site.get("login")
        user, pw = creds(site)
        if not cfg:
            res = "not configured"
        elif not (user and pw):
            res = "no credentials"
        else:
            res = self._do_login(site, cfg, user, pw)
        self.logins[sid] = res
        return res

    def _do_login(self, site, cfg, user, pw):
        page = self.context(site).new_page()
        try:
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
            self._settle(page)
            if cfg.get("open_selector"):  # sites that show the form in a modal
                try:
                    page.click(cfg["open_selector"], timeout=8000)
                except Exception:
                    pass
            if self._logged_in(page, cfg):
                return "ok"
            u_sel = cfg.get("user_selector") or _visible(["input[type=email]", "input[name*=mail i]", "input[name*=user i]",
                                                     "input[name*=login i]", "input[id*=mail i]", "input[id*=user i]",
                                                     "input[id*=login i]", "input[type=text]"])
            p_sel = cfg.get("pass_selector") or "input[type=password]:visible"
            page.locator(u_sel).first.fill(user, timeout=15000)
            if cfg.get("two_step"):  # email first, then password on the next screen
                page.keyboard.press("Enter")
                page.wait_for_timeout(2500)
            page.locator(p_sel).first.fill(pw, timeout=15000)
            submit = page.locator(cfg["submit_selector"]) if cfg.get("submit_selector") else \
                page.locator("button:visible, input[type=submit]:visible").filter(
                    has_text=re.compile(r"log ?in|sign ?in|войти|kirish|вход", re.I))
            try:
                submit.first.click(timeout=8000)
            except Exception:
                page.locator(p_sel).first.press("Enter")
            self._settle(page, 20000)
            page.wait_for_timeout(1500)
            if self._logged_in(page, cfg):
                return "ok"
            body = _visible_text(page)[:4000]
            err = re.search(r"(invalid|incorrect|wrong|неверн|xato|captcha|not match)[^\n]{0,80}", body, re.I)
            return "failed: " + (err.group(0) if err else f"still on {page.url} (check selectors / success text)")
        except Exception as ex:
            return f"failed: {str(ex).splitlines()[0][:200]}"
        finally:
            page.close()

    def _logged_in(self, page, cfg):
        body = _visible_text(page)
        if cfg.get("success_text"):
            return re.search(cfg["success_text"], body, re.I) is not None
        if page.locator("input[type=password]").count():
            return False
        return re.search(r"log ?out|sign ?out|выйти|выход|chiqish|my account|dashboard|profile", body, re.I) is not None

    # ---------------------------------------------------------------- pages
    def _settle(self, page, ms=15000):
        try:
            page.wait_for_load_state("networkidle", timeout=ms)
        except Exception:
            pass

    def _act(self, page, actions):
        """Steps run after load, e.g. to set a search filter:
        {"select": css, "value": v} | {"fill": css, "value": v} | {"click": css} | {"press": key} | {"wait": ms}
        | {"eval": js}. A failing step is skipped (reported in the result) so the page is still read."""
        errors = []
        for a in actions or []:
            try:
                if "select" in a:
                    page.select_option(a["select"], a["value"], timeout=10000)
                elif "fill" in a:
                    page.fill(a["fill"], a["value"], timeout=10000)
                elif "click" in a:
                    page.click(a["click"], timeout=10000)
                elif "press" in a:
                    page.keyboard.press(a["press"])
                elif "eval" in a:
                    page.evaluate(a["eval"])
                if "wait" in a:
                    page.wait_for_timeout(a["wait"])
                elif "select" in a or "click" in a or "press" in a:
                    self._settle(page, 10000)
            except Exception as ex:
                errors.append(f"{json.dumps(a)[:80]}: {str(ex).splitlines()[0][:120]}")
        return errors

    def _dismiss_popups(self, page):
        """Close announcement modals that sit over the page and swallow clicks."""
        try:
            page.keyboard.press("Escape")
            page.evaluate("""() => { for (const b of document.querySelectorAll(
                '.modal.show .close, .modal.show [data-dismiss=modal], [role=dialog] [aria-label=Close], [role=dialog] .btn-close'))
                { try { b.click(); } catch (e) {} } }""")
            page.wait_for_timeout(500)
        except Exception:
            pass

    def _tabs(self, page, tabs, text):
        """Click each tab/section label and append only the lines it reveals."""
        if not tabs:
            return ""
        self._dismiss_popups(page)
        seen = set(text.split("\n"))
        out = []
        for t in tabs:
            # a JS click on the innermost clickable element with that label works even under leftover overlays
            hit = page.evaluate("""(label) => {
                const els = [...document.querySelectorAll('a, button, [role=tab], li, summary, h2, h3, h4, span')]
                  .filter(e => (e.innerText || '').trim().toLowerCase() === label.toLowerCase());
                const el = els.find(e => ['A', 'BUTTON'].includes(e.tagName) || e.getAttribute('role') === 'tab') || els[0];
                if (!el) return false;
                el.click();
                return true;
            }""", t)
            if not hit:
                continue
            page.wait_for_timeout(2000)
            new = [l for l in _visible_text(page).split("\n") if l.strip() and l not in seen]
            seen.update(new)
            if new:
                out.append(f"\n\n===== {t} =====\n" + "\n".join(new))
        return "".join(out)

    def fetch(self, site, url, capture=None, scroll=0, max_card=1500, wait_for=None, actions=None, tabs=None):
        """Open url in the site's context. capture={"match": regex, "body": {...}} records the JSON responses
        the page's own scripts load (and can enlarge their page size). Returns a dict, never raises."""
        if site.get("login"):
            self.login(site)
        page = self.context(site).new_page()
        captured = []
        try:
            if capture:
                rx = re.compile(capture["match"], re.I)
                if capture.get("body"):
                    def _route(route, request):
                        if request.method == "POST" and rx.search(request.url):
                            route.continue_(post_data=_merge_body(request.post_data, capture["body"]))
                        else:
                            route.continue_()
                    page.route("**/*", _route)

                def _resp(resp):
                    if rx.search(resp.url):
                        try:
                            captured.append({"url": resp.url, "status": resp.status,
                                             "request": resp.request.post_data, "json": resp.json()})
                        except Exception:
                            pass
                page.on("response", _resp)
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._settle(page)
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=15000)
                except Exception:
                    pass
            step_errors = self._act(page, actions)
            for _ in range(scroll):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(800)
            if capture and not captured:
                page.wait_for_timeout(4000)
            title = page.title()
            text = _visible_text(page)
            links = page.evaluate(_LINKS_JS, max_card)
            text += self._tabs(page, tabs, text)
            buttons = page.evaluate(r"""() => [...document.querySelectorAll('button, [role=button]')]
                .map(e => (e.innerText || '').trim()).filter(t => /yuklab|download|скачать|yuklash|файл|fayl/i.test(t))""")
            blocked = any(m.lower() in title.lower() for m in BLOCK_MARKERS) or \
                (len(text) < 600 and re.search(r"verify you are human|enable javascript and cookies", text, re.I))
            return {"url": page.url, "status": resp.status if resp else None, "title": title, "text": text,
                    "links": links, "captured": captured, "blocked": bool(blocked), "download_buttons": buttons,
                    "error": "; ".join(step_errors) or None}
        except Exception as ex:
            return {"url": url, "status": None, "title": "", "text": "", "links": [], "captured": captured,
                    "blocked": False, "error": str(ex).splitlines()[0][:300]}
        finally:
            page.close()

    def download_by_click(self, site, url, label, folder, nth=0):
        """Open url, click the nth button/link labelled label (e.g. "Faylni yuklab olish") and save the download
        that starts. Returns the saved path or raises."""
        if site.get("login"):
            self.login(site)
        page = self.context(site).new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._settle(page)
            self._dismiss_popups(page)
            loc = page.locator("a, button, [role=button]").filter(has_text=label).nth(nth)
            with page.expect_download(timeout=60000) as dl:
                loc.evaluate("e => e.click()")
            d = dl.value
            path = os.path.join(folder, re.sub(r"[^\w.\-]+", "_", d.suggested_filename)[-100:] or "document")
            d.save_as(path)
            return path
        finally:
            page.close()

    def download(self, site, url, path):
        """Save a document (PDF/DOC) with the site's cookies. Returns the path or raises."""
        r = self.context(site).request.get(url, timeout=60000)
        if not r.ok:
            raise RuntimeError(f"download {url}: HTTP {r.status}")
        with open(path, "wb") as f:
            f.write(r.body())
        return path
