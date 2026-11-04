import os, re, time, uuid, shutil, struct, secrets, sqlite3, mimetypes
from functools import wraps
from urllib.parse import urlparse, quote
from flask import Flask, g, request, jsonify, session, send_file, send_from_directory, abort
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PANAHGAH_DATA", os.path.join(BASE, "data"))
CHAT_DIR = os.path.join(DATA, "uploads", "chat")
MEDIA_DIR = os.path.join(DATA, "uploads", "media")
AVATAR_DIR = os.path.join(DATA, "uploads", "avatars")
OWNER = os.environ.get("OWNER_USERNAME", "mahbod").lower()
UPLOADS = os.path.join(DATA, "uploads")
ACCEL = os.environ.get("ACCEL_REDIRECT", "0") == "1"   # سرو فایل‌ها توسط nginx (DEPLOY.txt بخش ۱۹)
ONLINE_WINDOW = 40
MB = 1024 * 1024
GB = 1024 * MB
MIN_FREE = int(float(os.environ.get("MIN_FREE_GB", "4")) * GB)
ROLES = ("member", "uploader", "admin")
DB_PATH = os.path.join(DATA, "panahgah.db")
for d in (CHAT_DIR, MEDIA_DIR, AVATAR_DIR):
    os.makedirs(d, exist_ok=True)


def load_secret():
    p = os.path.join(DATA, "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip()


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config.update(
    SECRET_KEY=load_secret(),
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "3072")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 90,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chats(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  name TEXT,
  dm_key TEXT UNIQUE,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS members(
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  last_read INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text TEXT, reply_to INTEGER,
  file TEXT, file_name TEXT, file_mime TEXT, file_size INTEGER,
  created_at INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL, title TEXT NOT NULL, collection TEXT,
  file TEXT NOT NULL, file_name TEXT, mime TEXT, size INTEGER,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, url TEXT NOT NULL,
  uploader_id INTEGER, created_at INTEGER NOT NULL);
"""


def init_db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    for tbl, col, ddl in (("users", "last_seen", "INTEGER NOT NULL DEFAULT 0"), ("users", "avatar", "TEXT"),
                          ("chats", "epoch", "INTEGER NOT NULL DEFAULT 0"),
                          ("users", "role", "TEXT NOT NULL DEFAULT 'member'"), ("media", "uploader_id", "INTEGER"),
                          ("members", "can_post", "INTEGER NOT NULL DEFAULT 1"),
                          ("chats", "avatar", "TEXT"), ("chats", "about", "TEXT")):
        if col not in [r[1] for r in c.execute("PRAGMA table_info(%s)" % tbl)]:
            c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tbl, col, ddl))
            if col == "role":
                c.execute("UPDATE users SET role='admin' WHERE is_admin=1")
    c.execute("UPDATE users SET is_admin=1 WHERE lower(username)=?", (OWNER,))
    c.commit()
    c.close()


init_db()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA synchronous=NORMAL")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


@app.errorhandler(HTTPException)
def http_err(e):
    msgs = {400: "درخواست نامعتبر", 403: "دسترسی ندارید", 404: "پیدا نشد",
            413: "حجم فایل بیشتر از حد مجاز است", 405: "درخواست نامعتبر"}
    return jsonify(error=msgs.get(e.code, e.description)), e.code


@app.errorhandler(Exception)
def any_err(e):
    app.logger.exception(e)
    return jsonify(error="خطای داخلی سرور"), 500


# ---------- auth ----------
def me():
    uid = session.get("uid")
    if not uid:
        return None
    if not hasattr(g, "me"):
        g.me = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return g.me


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not me():
            session.clear()
            return jsonify(error="unauthorized"), 401
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if role(me()) not in ("owner", "admin"):
            abort(403)
        return f(*a, **k)
    return w


def uploader_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if role(me()) == "member":
            abort(403)
        return f(*a, **k)
    return w


def is_owner(u):
    return u["username"].lower() == OWNER


def role(u):
    if is_owner(u):
        return "owner"
    r = u["role"] if u["role"] in ROLES else "member"
    return "admin" if (r == "member" and u["is_admin"]) else r


_FA = dict(zip("ضصثقفغعهخحشسیبلاتنمظطزرذدپ", "qwertyuiopasdfghjklzxcvbnm"))
_FA.update({"ي": "d", "ى": "d", "ـ": ""})
_FA.update({chr(0x06F0 + i): str(i) for i in range(10)})
_FA.update({chr(0x0660 + i): str(i) for i in range(10)})
_FA.update({"\u200c": "", "\u200f": "", "\u200e": ""})


def latin(s):
    """نام کاربری‌ای که با کیبورد فارسی تایپ شده را به معادل انگلیسی کلیدها برمی‌گرداند."""
    return "".join(_FA.get(c, c) for c in str(s or "")).strip()


def avatar_url(u):
    return "/files/avatar/" + u["avatar"] if u["avatar"] else ""


def chat_av(c):
    """آدرس عکس پروفایل گروه/کانال (خالی اگر نگذاشته باشند)."""
    return "/files/avatar/" + c["avatar"] if c["avatar"] else ""


def uj(u):
    r = role(u)
    return {"id": u["id"], "username": u["username"], "name": u["display_name"], "role": r,
            "is_admin": r in ("owner", "admin"), "is_owner": r == "owner",
            "can_upload": r != "member", "avatar": avatar_url(u)}


def touch_seen(u):
    now = int(time.time())
    if now - (u["last_seen"] or 0) >= 15:
        db().execute("UPDATE users SET last_seen=? WHERE id=?", (now, u["id"]))
        db().commit()


def owner_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if not is_owner(me()):
            abort(403)
        return f(*a, **k)
    return w


@app.after_request
def headers(r):
    if request.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


fails = {}


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    ip, now = request.remote_addr, time.time()
    f = [t for t in fails.get(ip, []) if now - t < 300]
    if len(f) >= 10:
        return jsonify(error="تلاش بیش از حد؛ چند دقیقه بعد دوباره امتحان کن"), 429
    u = db().execute("SELECT * FROM users WHERE username=?", (latin(d.get("username", "")),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], str(d.get("password", ""))):
        f.append(now)
        fails[ip] = f
        return jsonify(error="نام کاربری یا رمز عبور اشتباه است"), 401
    fails.pop(ip, None)
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    db().execute("UPDATE users SET last_seen=? WHERE id=?", (int(time.time()), u["id"]))
    db().commit()
    return jsonify(user=uj(u))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def get_me():
    u = uj(me())
    if u["is_owner"]:
        u["low_space"] = shutil.disk_usage(DATA).free < MIN_FREE
    return jsonify(user=u)


@app.post("/api/me/password")
@login_required
def change_pw():
    d = request.get_json(silent=True) or {}
    u = me()
    if not check_password_hash(u["password_hash"], str(d.get("old", ""))):
        return jsonify(error="رمز فعلی اشتباه است"), 400
    new = str(d.get("new", ""))
    if len(new) < 6:
        return jsonify(error="رمز جدید باید حداقل ۶ کاراکتر باشد"), 400
    db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), u["id"]))
    db().commit()
    return jsonify(ok=True)


def img_ext(h):
    if h.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if h.startswith(b"\x89PNG"):
        return ".png"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return ".webp"
    if h[:3] == b"GIF":
        return ".gif"
    return None


@app.post("/api/me/avatar")
@login_required
def set_avatar():
    u = me()
    e = guard_upload("avatar", request.content_length or 0)
    if e:
        return e
    fs = request.files.get("file")
    if not fs:
        abort(400)
    ext = img_ext(fs.stream.read(16))
    fs.stream.seek(0)
    if not ext:
        return jsonify(error="فرمت تصویر نامعتبر است"), 400
    name = uuid.uuid4().hex + ext
    path = os.path.join(AVATAR_DIR, name)
    fs.save(path)
    e = guard_upload("avatar", os.path.getsize(path), post=True)
    if e:
        os.remove(path)
        return e
    rm(AVATAR_DIR, [u["avatar"]])
    db().execute("UPDATE users SET avatar=? WHERE id=?", (name, u["id"]))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/me/avatar")
@login_required
def del_avatar():
    u = me()
    rm(AVATAR_DIR, [u["avatar"]])
    db().execute("UPDATE users SET avatar=NULL WHERE id=?", (u["id"],))
    db().commit()
    return jsonify(ok=True)


@app.get("/files/avatar/<name>")
@login_required
def avatar_file(name):
    name = os.path.basename(name)
    return serve(os.path.join(AVATAR_DIR, name), guess(name), name)


# ---------- helpers ----------
MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
        ".wav": "audio/wav", ".flac": "audio/flac", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".pdf": "application/pdf", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def guess(name):
    ext = os.path.splitext(name)[1].lower()
    return MIME.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def clean_name(n):
    n = os.path.basename((n or "").replace("\\", "/"))
    n = re.sub(r"[\x00-\x1f\"<>|?*]", "", n).strip()
    return (n or "file")[:120]


def _patch_offsets(buf, start, end, delta):
    """جدول آفست چانک‌ها (stco/co64) را به اندازه‌ی delta جابه‌جا می‌کند."""
    p = start
    while p + 8 <= end:
        sz, typ = struct.unpack(">I4s", bytes(buf[p:p + 8]))
        if sz < 8 or p + sz > end:
            raise ValueError("bad atom")
        if typ in (b"trak", b"mdia", b"minf", b"stbl"):
            _patch_offsets(buf, p + 8, p + sz, delta)
        elif typ == b"stco":
            n = struct.unpack(">I", bytes(buf[p + 12:p + 16]))[0]
            for i in range(n):
                o = p + 16 + 4 * i
                v = struct.unpack(">I", bytes(buf[o:o + 4]))[0] + delta
                if v > 0xFFFFFFFF:
                    raise ValueError("offset overflow")
                buf[o:o + 4] = struct.pack(">I", v)
        elif typ == b"co64":
            n = struct.unpack(">I", bytes(buf[p + 12:p + 16]))[0]
            for i in range(n):
                o = p + 16 + 8 * i
                buf[o:o + 8] = struct.pack(">Q", struct.unpack(">Q", bytes(buf[o:o + 8]))[0] + delta)
        p += sz


def faststart(path):
    """اگر اطلاعات پخش (moov) ته فایل MP4/M4A باشد، آن را به ابتدای فایل می‌آورد (بدون بازکدگذاری).
    بدون این کار مرورگر تا انتهای فایل را نگیرد پخش را شروع نمی‌کند و شروع فیلم خیلی دیر می‌شود."""
    tmp = path + ".fs"
    try:
        size = os.path.getsize(path)
        atoms, pos = [], 0
        with open(path, "rb") as f:
            while pos < size:
                f.seek(pos)
                h = f.read(16)
                if len(h) < 8:
                    return False
                sz, typ = struct.unpack(">I4s", h[:8])
                hdr = 8
                if sz == 1:
                    if len(h) < 16:
                        return False
                    sz, hdr = struct.unpack(">Q", h[8:16])[0], 16
                elif sz == 0:
                    sz = size - pos
                if sz < hdr or pos + sz > size:
                    return False
                atoms.append((typ, pos, sz, hdr))
                pos += sz
            types = [a[0] for a in atoms]
            if (not atoms or types[0] != b"ftyp" or types.count(b"moov") != 1 or b"mdat" not in types
                    or b"moof" in types):
                return False
            mi = types.index(b"moov")
            _, mo, ms, mh = atoms[mi]
            if mh != 8 or any(a[0] == b"mdat" and a[1] > mo for a in atoms):
                return False
            first_mdat = min(a[1] for a in atoms if a[0] == b"mdat")
            if mo < first_mdat:
                return False                     # قبلاً faststart است
            if shutil.disk_usage(DATA).free < size + 256 * MB:
                return False
            f.seek(mo)
            moov = bytearray(f.read(ms))
            _patch_offsets(moov, 8, ms, ms)
            ins = atoms[0][1] + atoms[0][2]      # بلافاصله بعد از ftyp
            with open(tmp, "wb") as o:
                def cp(a, b):
                    f.seek(a)
                    while a < b:
                        chunk = f.read(min(8 * MB, b - a))
                        if not chunk:
                            raise ValueError("short read")
                        o.write(chunk)
                        a += len(chunk)
                cp(0, ins)
                o.write(moov)
                cp(ins, mo)
                cp(mo + ms, size)
        os.replace(tmp, path)
        return True
    except Exception as e:
        app.logger.warning("faststart failed for %s: %s", path, e)
        return False
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


FAST_EXT = (".mp4", ".m4v", ".mov", ".m4a")


def save_upload(fs, folder):
    orig = clean_name(fs.filename)
    ext = re.sub(r"[^a-z0-9.]", "", os.path.splitext(orig)[1].lower())[:10]
    stored = uuid.uuid4().hex + ext
    path = os.path.join(folder, stored)
    fs.save(path)
    if ext in FAST_EXT:
        faststart(path)
    return stored, orig, os.path.getsize(path), guess(orig)


def rm(folder, names):
    for n in names:
        if n:
            try:
                os.remove(os.path.join(folder, n))
            except OSError:
                pass


def is_member(cid, uid):
    return db().execute("SELECT 1 FROM members WHERE chat_id=? AND user_id=?", (cid, uid)).fetchone() is not None


def chat_type(cid):
    r = db().execute("SELECT type FROM chats WHERE id=?", (cid,)).fetchone()
    return r["type"] if r else None


def can_post(cid, u):
    """در کانال فقط مالک و اعضای دارای اجازه پیام می‌دهند؛ در پیوی و گروه همه."""
    if chat_type(cid) != "channel":
        return True
    if is_owner(u):
        return True
    r = db().execute("SELECT can_post FROM members WHERE chat_id=? AND user_id=?", (cid, u["id"])).fetchone()
    return bool(r and r["can_post"])


def serve(path, mime, name):
    if not os.path.isfile(path):
        abort(404)
    inline = (mime.startswith(("image/", "audio/", "video/")) and mime != "image/svg+xml") or mime == "application/pdf"
    dl = request.args.get("dl") == "1"
    if ACCEL and os.path.abspath(path).startswith(UPLOADS + os.sep):
        # nginx خودش فایل را (با Range و sendfile) می‌فرستد؛ پایتون فقط مجوز را بررسی می‌کند.
        r = app.response_class(status=200, mimetype=mime)
        r.headers["X-Accel-Redirect"] = "/_protected/" + quote(os.path.relpath(path, UPLOADS).replace(os.sep, "/"))
        r.headers["Content-Disposition"] = ("attachment" if (dl or not inline) else "inline") + "; filename*=UTF-8''" + quote(name or "file")
    else:
        r = send_file(path, mimetype=mime, as_attachment=(dl or not inline), download_name=name, conditional=True)
    r.headers["Cache-Control"] = "private, max-age=604800"
    return r


# ---------- chats ----------
def reads_of(cid, uid):
    """آخرین پیام خوانده‌شده‌ی بقیه‌ی اعضای گفتگو: {user_id: last_read}"""
    rows = db().execute("SELECT user_id, last_read FROM members WHERE chat_id=? AND user_id!=?", (cid, uid)).fetchall()
    return {str(r["user_id"]): r["last_read"] for r in rows}


def tick_state(mid, reads):
    """1 = ارسال شد، 2 = بعضی‌ها دیده‌اند (گروه)، 3 = همه دیده‌اند"""
    if not reads:
        return 1
    n = sum(1 for r in reads if r >= mid)
    return 1 if n == 0 else (3 if n == len(reads) else 2)


def chat_list(uid):
    d = db()
    rows = d.execute("SELECT c.*, m.last_read, m.can_post FROM chats c JOIN members m ON m.chat_id=c.id WHERE m.user_id=?", (uid,)).fetchall()
    out = []
    for c in rows:
        title, uname, count, av, online, seen = c["name"], None, 0, "", False, 0
        if c["type"] == "dm":
            o = d.execute("SELECT u.username, u.display_name, u.avatar, u.last_seen FROM members m JOIN users u ON u.id=m.user_id "
                          "WHERE m.chat_id=? AND m.user_id!=?", (c["id"], uid)).fetchone()
            if not o:
                continue
            title, uname, av = o["display_name"], o["username"], avatar_url(o)
            online = time.time() - (o["last_seen"] or 0) < ONLINE_WINDOW
            seen = 0 if online else (o["last_seen"] or 0)
        else:
            count = d.execute("SELECT COUNT(*) FROM members WHERE chat_id=?", (c["id"],)).fetchone()[0]
            av = chat_av(c)
        last = d.execute("SELECT m.*, u.display_name AS sender FROM messages m JOIN users u ON u.id=m.sender_id "
                         "WHERE m.chat_id=? ORDER BY m.id DESC LIMIT 1", (c["id"],)).fetchone()
        unread = d.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND id>? AND sender_id!=? AND deleted=0",
                           (c["id"], c["last_read"], uid)).fetchone()[0]
        prev, at, tick = "", c["created_at"], 0
        if last:
            at = last["created_at"]
            if last["deleted"]:
                prev = "پیام حذف شد"
            else:
                prev = (last["text"] or "").replace("\n", " ")[:60] or "📎 " + (last["file_name"] or "فایل")
            if c["type"] in ("group", "channel") and not last["deleted"]:
                prev = last["sender"].split(" ")[0] + ": " + prev
            if last["sender_id"] == uid and not last["deleted"]:
                tick = tick_state(last["id"], list(reads_of(c["id"], uid).values()))
        mention = count_mentions(c["id"], c["last_read"], uid, me()["username"]) if (c["type"] == "group" and unread) else 0
        postable = c["type"] != "channel" or is_owner(me()) or bool(c["can_post"])
        out.append({"id": c["id"], "type": c["type"], "title": title or ("کانال" if c["type"] == "channel" else "گروه"),
                    "can_post": postable, "username": uname, "count": count,
                    "preview": prev, "at": at, "has_last": bool(last), "unread": unread,
                    "avatar": av, "about": c["about"] or "", "mention": mention,
                    "online": online, "seen": seen, "tick": tick})
    out.sort(key=lambda x: x["at"], reverse=True)
    return out


MSG_SQL = ("SELECT m.*, u.display_name AS sender, u.username AS sender_un, r.text AS r_text, r.file_name AS r_file, r.deleted AS r_del, "
           "ru.display_name AS r_sender FROM messages m JOIN users u ON u.id=m.sender_id "
           "LEFT JOIN messages r ON r.id=m.reply_to LEFT JOIN users ru ON ru.id=r.sender_id WHERE m.chat_id=?")


def msg_json(r):
    m = {"id": r["id"], "chat": r["chat_id"], "uid": r["sender_id"], "sender": r["sender"], "un": r["sender_un"],
         "at": r["created_at"], "deleted": bool(r["deleted"])}
    if not r["deleted"]:
        m["text"] = r["text"] or ""
        if r["file"]:
            m["file"] = {"url": "/files/chat/" + r["file"], "name": r["file_name"],
                         "mime": r["file_mime"], "size": r["file_size"]}
        elif r["file_name"]:
            m["gone"] = r["file_name"]
        if r["reply_to"]:
            t = "پیام حذف شد" if r["r_del"] else ((r["r_text"] or "")[:100] or "📎 " + (r["r_file"] or "فایل"))
            m["reply"] = {"id": r["reply_to"], "sender": r["r_sender"] or "", "text": t}
    return m


# ---------- منشن (@username) در گروه‌ها ----------
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)")


def attach_mentions(cid, msgs):
    """به هر پیام گروه، فهرست نام‌کاربری‌های منشن‌شده‌ی معتبر (عضو همان گروه) را اضافه می‌کند."""
    if chat_type(cid) != "group":
        return msgs
    names = set()
    for m in msgs:
        if m.get("text"):
            names.update(x.lower() for x in MENTION_RE.findall(m["text"]))
    if not names:
        return msgs
    names = list(names)[:200]
    rows = db().execute("SELECT u.username FROM members mb JOIN users u ON u.id=mb.user_id WHERE mb.chat_id=? "
                        "AND lower(u.username) IN (%s)" % ",".join("?" * len(names)), [cid] + names).fetchall()
    valid = {r["username"].lower() for r in rows}
    for m in msgs:
        if m.get("text"):
            found = sorted({x.lower() for x in MENTION_RE.findall(m["text"])} & valid)
            if found:
                m["mentions"] = found
    return msgs


def count_mentions(cid, after, uid, username):
    """تعداد پیام‌های خوانده‌نشده‌ای که این کاربر در آن‌ها منشن شده."""
    rx = re.compile(r"(?<![A-Za-z0-9_])@" + re.escape(username) + r"(?![A-Za-z0-9_]|\.[A-Za-z0-9_])", re.I)
    rows = db().execute("SELECT text FROM messages WHERE chat_id=? AND id>? AND sender_id!=? AND deleted=0 AND text LIKE ?",
                        (cid, after, uid, "%@" + username + "%")).fetchall()
    return sum(1 for r in rows if rx.search(r["text"] or ""))


def fetch_messages(cid, after=0, before=0, limit=50):
    d = db()
    if after:
        rows = d.execute(MSG_SQL + " AND m.id>? ORDER BY m.id ASC LIMIT 200", (cid, after)).fetchall()
    elif before:
        rows = d.execute(MSG_SQL + " AND m.id<? ORDER BY m.id DESC LIMIT ?", (cid, before, limit)).fetchall()[::-1]
    else:
        rows = d.execute(MSG_SQL + " ORDER BY m.id DESC LIMIT ?", (cid, limit)).fetchall()[::-1]
    return attach_mentions(cid, [msg_json(r) for r in rows])


def mark_read(cid, uid, mid):
    db().execute("UPDATE members SET last_read=MAX(last_read,?) WHERE chat_id=? AND user_id=?", (mid, cid, uid))
    db().commit()


@app.get("/api/poll")
@login_required
def poll():
    u = me()
    cid = request.args.get("chat", 0, type=int)
    after = request.args.get("after", 0, type=int)
    out = {}
    if request.args.get("v") == "1":
        touch_seen(u)
    if cid and is_member(cid, u["id"]):
        out["epoch"] = db().execute("SELECT epoch FROM chats WHERE id=?", (cid,)).fetchone()[0]
        msgs = fetch_messages(cid, after=after)
        out["messages"] = msgs
        if msgs and request.args.get("read") == "1":
            mark_read(cid, u["id"], msgs[-1]["id"])
        out["reads"] = reads_of(cid, u["id"])
    out["chats"] = chat_list(u["id"])
    return jsonify(out)


@app.get("/api/chats/<int:cid>/messages")
@login_required
def get_messages(cid):
    u = me()
    if not is_member(cid, u["id"]):
        abort(403)
    msgs = fetch_messages(cid, before=request.args.get("before", 0, type=int))
    if msgs and request.args.get("read") == "1":
        mark_read(cid, u["id"], msgs[-1]["id"])
    return jsonify(messages=msgs, reads=reads_of(cid, u["id"]),
                   epoch=db().execute("SELECT epoch FROM chats WHERE id=?", (cid,)).fetchone()[0])


@app.get("/api/chats/<int:cid>/members")
@login_required
def chat_members(cid):
    if not is_member(cid, me()["id"]):
        abort(403)
    rows = db().execute("SELECT u.*, m.can_post AS cp FROM members m JOIN users u ON u.id=m.user_id "
                        "WHERE m.chat_id=? ORDER BY u.display_name", (cid,)).fetchall()
    ch = chat_type(cid) == "channel"
    out = []
    for r in rows:
        j = uj(r)
        j["can_post"] = (not ch) or is_owner(r) or bool(r["cp"])
        out.append(j)
    return jsonify(members=out)


@app.post("/api/chats/<int:cid>/messages")
@login_required
def send_message(cid):
    u = me()
    if not is_member(cid, u["id"]):
        abort(403)
    if not can_post(cid, u):
        return jsonify(error="در این کانال فقط افراد مجاز می‌توانند پیام بدهند"), 403
    if (request.content_length or 0) > 65536:
        e = guard_upload("chat", request.content_length)
        if e:
            return e
    form = request.form if (request.content_type or "").startswith("multipart") else (request.get_json(silent=True) or {})
    text = str(form.get("text") or "").strip()[:4000]
    reply = form.get("reply_to")
    fs = request.files.get("file")
    has_file = bool(fs and fs.filename)
    if not text and not has_file:
        return jsonify(error="پیام خالی است"), 400
    reply_id = None
    if reply and str(reply).isdigit():
        if db().execute("SELECT 1 FROM messages WHERE id=? AND chat_id=?", (int(reply), cid)).fetchone():
            reply_id = int(reply)
    f = (None, None, None, None)
    if has_file:
        f = save_upload(fs, CHAT_DIR)
        e = guard_upload("chat", f[2], post=True)
        if e:
            rm(CHAT_DIR, [f[0]])
            return e
        vm = {".webm": "audio/webm", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
              ".mp3": "audio/mpeg", ".wav": "audio/wav", ".aac": "audio/aac"}
        ext = os.path.splitext(f[1])[1].lower()
        if str(form.get("voice")) == "1" and ext in vm:
            f = (f[0], "voice-" + f[1].replace("voice-", "", 1), f[2], vm[ext])
    touch_seen(u)
    cur = db().execute("INSERT INTO messages(chat_id,sender_id,text,reply_to,file,file_name,file_mime,file_size,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?)", (cid, u["id"], text, reply_id, f[0], f[1], f[3], f[2], int(time.time())))
    mid = cur.lastrowid
    db().commit()
    mark_read(cid, u["id"], mid)
    row = db().execute(MSG_SQL + " AND m.id=?", (cid, mid)).fetchone()
    return jsonify(message=attach_mentions(cid, [msg_json(row)])[0])


@app.delete("/api/messages/<int:mid>")
@login_required
def del_message(mid):
    u = me()
    m = db().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m or not is_member(m["chat_id"], u["id"]):
        abort(404)
    if m["sender_id"] != u["id"] and role(u) not in ("owner", "admin"):
        abort(403)
    db().execute("UPDATE messages SET deleted=1, text=NULL, file=NULL WHERE id=?", (mid,))
    db().commit()
    rm(CHAT_DIR, [m["file"]])
    return jsonify(ok=True)


@app.post("/api/chats/dm")
@login_required
def start_dm():
    u = me()
    d = request.get_json(silent=True) or {}
    o = db().execute("SELECT * FROM users WHERE username=?", (latin(d.get("username", "")).lstrip("@"),)).fetchone()
    if not o:
        return jsonify(error="کاربری با این نام کاربری پیدا نشد"), 404
    if o["id"] == u["id"]:
        return jsonify(error="نمی‌توانی به خودت پیام بدهی"), 400
    key = "%d-%d" % (min(u["id"], o["id"]), max(u["id"], o["id"]))
    c = db().execute("SELECT id FROM chats WHERE dm_key=?", (key,)).fetchone()
    if c:
        return jsonify(chat_id=c["id"])
    cur = db().execute("INSERT INTO chats(type,dm_key,created_at) VALUES('dm',?,?)", (key, int(time.time())))
    cid = cur.lastrowid
    db().executemany("INSERT INTO members(chat_id,user_id) VALUES(?,?)", [(cid, u["id"]), (cid, o["id"])])
    db().commit()
    return jsonify(chat_id=cid)


@app.get("/files/chat/<name>")
@login_required
def chat_file(name):
    m = db().execute("SELECT * FROM messages WHERE file=? AND deleted=0", (name,)).fetchone()
    if not m or not is_member(m["chat_id"], me()["id"]):
        abort(404)
    return serve(os.path.join(CHAT_DIR, os.path.basename(name)), m["file_mime"], m["file_name"])


# ---------- admin ----------
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,32}$")


@app.get("/api/admin/users")
@admin_required
def a_users():
    rows = db().execute("SELECT * FROM users ORDER BY id").fetchall()
    out = []
    for r in rows:
        j = uj(r)
        j["online"] = time.time() - (r["last_seen"] or 0) < ONLINE_WINDOW
        out.append(j)
    return jsonify(users=out)


@app.post("/api/admin/users")
@admin_required
def a_user_add():
    d = request.get_json(silent=True) or {}
    un, pw = str(d.get("username", "")).strip(), str(d.get("password", ""))
    name = str(d.get("name", "")).strip()[:40] or un
    if not USERNAME_RE.match(un):
        return jsonify(error="نام کاربری باید ۳ تا ۳۲ حرف انگلیسی، عدد، _ یا . باشد"), 400
    if len(pw) < 6:
        return jsonify(error="رمز عبور باید حداقل ۶ کاراکتر باشد"), 400
    if db().execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
        return jsonify(error="این نام کاربری قبلاً ثبت شده"), 400
    nr = d.get("role") or ("admin" if d.get("is_admin") else "member")
    if nr not in ROLES:
        return jsonify(error="سطح دسترسی نامعتبر است"), 400
    if nr == "admin" and not is_owner(me()):
        return jsonify(error="فقط مالک می‌تواند ادمین اضافه کند"), 403
    db().execute("INSERT INTO users(username,password_hash,display_name,is_admin,role,created_at) VALUES(?,?,?,?,?,?)",
                 (un, generate_password_hash(pw), name, 1 if nr == "admin" else 0, nr, int(time.time())))
    db().commit()
    return jsonify(ok=True)


@app.put("/api/admin/users/<int:uid>")
@admin_required
def a_user_edit(uid):
    d = request.get_json(silent=True) or {}
    u = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    actor = me()
    if not is_owner(actor) and (is_owner(u) or (role(u) == "admin" and u["id"] != actor["id"])):
        abort(403)
    name = str(d.get("name") or u["display_name"]).strip()[:40]
    cur_role = role(u)
    new_role = d.get("role") if d.get("role") in ROLES else cur_role
    if is_owner(u):
        new_role = u["role"]
    elif new_role != cur_role and not is_owner(actor) and (new_role == "admin" or cur_role == "admin" or u["id"] == actor["id"]):
        abort(403)
    adm = 1 if (new_role == "admin" or is_owner(u)) else 0
    pw = u["password_hash"]
    if d.get("password"):
        if len(str(d["password"])) < 6:
            return jsonify(error="رمز عبور باید حداقل ۶ کاراکتر باشد"), 400
        pw = generate_password_hash(str(d["password"]))
    db().execute("UPDATE users SET display_name=?, is_admin=?, role=?, password_hash=? WHERE id=?",
                 (name, adm, new_role, pw, uid))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/admin/users/<int:uid>")
@owner_required
def a_user_del(uid):
    if uid == me()["id"]:
        return jsonify(error="نمی‌توانی خودت را حذف کنی"), 400
    d = db()
    tu = d.execute("SELECT avatar FROM users WHERE id=?", (uid,)).fetchone()
    if tu:
        rm(AVATAR_DIR, [tu["avatar"]])
    dms = [r["chat_id"] for r in d.execute("SELECT m.chat_id FROM members m JOIN chats c ON c.id=m.chat_id "
                                           "WHERE m.user_id=? AND c.type='dm'", (uid,))]
    files = [r["file"] for r in d.execute("SELECT file FROM messages WHERE sender_id=?", (uid,))]
    for c in dms:
        files += [r["file"] for r in d.execute("SELECT file FROM messages WHERE chat_id=?", (c,))]
        d.execute("DELETE FROM chats WHERE id=?", (c,))
    d.execute("DELETE FROM users WHERE id=?", (uid,))
    d.commit()
    rm(CHAT_DIR, files)
    return jsonify(ok=True)


def set_members(cid, ids, posters=None):
    """posters: فقط برای کانال؛ مجموعه‌ی کاربرانی که اجازه‌ی پیام دادن دارند (مالک همیشه دارد)."""
    d = db()
    valid = {r["id"] for r in d.execute("SELECT id FROM users")}
    ids = {int(i) for i in ids if str(i).isdigit() and int(i) in valid} | {me()["id"]}
    cur = {r["user_id"] for r in d.execute("SELECT user_id FROM members WHERE chat_id=?", (cid,))}
    for uid in cur - ids:
        d.execute("DELETE FROM members WHERE chat_id=? AND user_id=?", (cid, uid))
    last = d.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=?", (cid,)).fetchone()[0]
    if posters is not None:
        posters = {int(i) for i in posters if str(i).isdigit()} & ids | {me()["id"]}
    for uid in ids - cur:
        d.execute("INSERT INTO members(chat_id,user_id,last_read,can_post) VALUES(?,?,?,?)",
                  (cid, uid, last, 1 if posters is None or uid in posters else 0))
    if posters is not None:
        for uid in ids & cur:
            d.execute("UPDATE members SET can_post=? WHERE chat_id=? AND user_id=?", (1 if uid in posters else 0, cid, uid))


@app.get("/api/admin/groups")
@owner_required
def a_groups():
    d = db()
    out = []
    for c in d.execute("SELECT * FROM chats WHERE type='group' ORDER BY id DESC"):
        mem = [r["user_id"] for r in d.execute("SELECT user_id FROM members WHERE chat_id=?", (c["id"],))]
        out.append({"id": c["id"], "name": c["name"], "members": mem, "about": c["about"] or "", "avatar": chat_av(c)})
    return jsonify(groups=out)


@app.post("/api/admin/groups")
@owner_required
def a_group_add():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()[:60]
    if not name:
        return jsonify(error="نام گروه را وارد کن"), 400
    cur = db().execute("INSERT INTO chats(type,name,about,created_at) VALUES('group',?,?,?)",
                       (name, about_of(d), int(time.time())))
    set_members(cur.lastrowid, d.get("members") or [])
    db().commit()
    return jsonify(chat_id=cur.lastrowid)


@app.put("/api/admin/groups/<int:cid>")
@owner_required
def a_group_edit(cid):
    d = request.get_json(silent=True) or {}
    c = db().execute("SELECT * FROM chats WHERE id=? AND type='group'", (cid,)).fetchone()
    if not c:
        abort(404)
    name = str(d.get("name") or c["name"]).strip()[:60]
    db().execute("UPDATE chats SET name=?, about=? WHERE id=?", (name, about_of(d, c["about"]), cid))
    set_members(cid, d.get("members") or [])
    db().commit()
    return jsonify(ok=True)


@app.post("/api/admin/groups/<int:cid>/clear")
@owner_required
def a_group_clear(cid):
    d = db()
    if not d.execute("SELECT 1 FROM chats WHERE id=? AND type IN ('group','channel')", (cid,)).fetchone():
        abort(404)
    rows = d.execute("SELECT file, file_size FROM messages WHERE chat_id=?", (cid,)).fetchall()
    files = [r["file"] for r in rows]
    freed = sum(r["file_size"] or 0 for r in rows)
    d.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
    d.execute("UPDATE chats SET epoch=epoch+1 WHERE id=?", (cid,))
    d.commit()
    rm(CHAT_DIR, files)
    return jsonify(ok=True, freed=freed)


@app.delete("/api/admin/groups/<int:cid>")
@owner_required
def a_group_del(cid):
    d = db()
    if not d.execute("SELECT 1 FROM chats WHERE id=? AND type IN ('group','channel')", (cid,)).fetchone():
        abort(404)
    files = [r["file"] for r in d.execute("SELECT file FROM messages WHERE chat_id=?", (cid,))]
    av = d.execute("SELECT avatar FROM chats WHERE id=?", (cid,)).fetchone()
    d.execute("DELETE FROM chats WHERE id=?", (cid,))
    d.commit()
    rm(CHAT_DIR, files)
    rm(AVATAR_DIR, [av["avatar"] if av else None])
    return jsonify(ok=True)


# ---------- channels (فقط مالک می‌سازد؛ فقط افراد مجاز پیام می‌دهند) ----------
@app.get("/api/admin/channels")
@owner_required
def a_channels():
    d = db()
    out = []
    for c in d.execute("SELECT * FROM chats WHERE type='channel' ORDER BY id DESC"):
        rows = d.execute("SELECT user_id, can_post FROM members WHERE chat_id=?", (c["id"],)).fetchall()
        out.append({"id": c["id"], "name": c["name"], "members": [r["user_id"] for r in rows],
                    "about": c["about"] or "", "avatar": chat_av(c),
                    "posters": [r["user_id"] for r in rows if r["can_post"]]})
    return jsonify(channels=out)


@app.post("/api/admin/channels")
@owner_required
def a_channel_add():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()[:60]
    if not name:
        return jsonify(error="نام کانال را وارد کن"), 400
    cur = db().execute("INSERT INTO chats(type,name,about,created_at) VALUES('channel',?,?,?)",
                       (name, about_of(d), int(time.time())))
    set_members(cur.lastrowid, d.get("members") or [], d.get("posters") or [])
    db().commit()
    return jsonify(chat_id=cur.lastrowid)


@app.put("/api/admin/channels/<int:cid>")
@owner_required
def a_channel_edit(cid):
    d = request.get_json(silent=True) or {}
    c = db().execute("SELECT * FROM chats WHERE id=? AND type='channel'", (cid,)).fetchone()
    if not c:
        abort(404)
    name = str(d.get("name") or c["name"]).strip()[:60]
    db().execute("UPDATE chats SET name=?, about=? WHERE id=?", (name, about_of(d, c["about"]), cid))
    set_members(cid, d.get("members") or [], d.get("posters") or [])
    db().commit()
    return jsonify(ok=True)


# ---------- پروفایل گروه/کانال (عکس و توضیحات؛ فقط مالک) ----------
def about_of(d, cur=""):
    """توضیحات گروه/کانال؛ اگر در درخواست نبود مقدار فعلی حفظ می‌شود."""
    if d.get("about") is None:
        return cur or ""
    return str(d["about"]).strip()[:300]


def store_avatar():
    """فایل عکس درخواست را اعتبارسنجی و ذخیره می‌کند. خروجی: (نام فایل, پاسخ خطا)."""
    e = guard_upload("avatar", request.content_length or 0)
    if e:
        return None, e
    fs = request.files.get("file")
    if not fs:
        abort(400)
    ext = img_ext(fs.stream.read(16))
    fs.stream.seek(0)
    if not ext:
        return None, (jsonify(error="فرمت تصویر نامعتبر است"), 400)
    name = uuid.uuid4().hex + ext
    path = os.path.join(AVATAR_DIR, name)
    fs.save(path)
    e = guard_upload("avatar", os.path.getsize(path), post=True)
    if e:
        os.remove(path)
        return None, e
    return name, None


@app.post("/api/admin/chats/<int:cid>/avatar")
@owner_required
def a_chat_avatar_set(cid):
    c = db().execute("SELECT * FROM chats WHERE id=? AND type IN ('group','channel')", (cid,)).fetchone()
    if not c:
        abort(404)
    name, e = store_avatar()
    if e:
        return e
    rm(AVATAR_DIR, [c["avatar"]])
    db().execute("UPDATE chats SET avatar=? WHERE id=?", (name, cid))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/admin/chats/<int:cid>/avatar")
@owner_required
def a_chat_avatar_del(cid):
    c = db().execute("SELECT * FROM chats WHERE id=? AND type IN ('group','channel')", (cid,)).fetchone()
    if not c:
        abort(404)
    rm(AVATAR_DIR, [c["avatar"]])
    db().execute("UPDATE chats SET avatar=NULL WHERE id=?", (cid,))
    db().commit()
    return jsonify(ok=True)


# ---------- entertainment ----------
CATS = {
    "music": {"aac", "flac", "m4a", "mp3", "ogg", "opus", "wav"},
    "podcast": {"aac", "flac", "m4a", "mp3", "ogg", "opus", "wav"},
    "video": {"m4v", "mkv", "mov", "mp4", "webm"},
    "book": {"pdf"},
}


def uploader_name(r):
    if r["up_name"]:
        return r["up_name"]
    return "کاربر حذف‌شده" if r["uploader_id"] else ""


@app.get("/api/media")
@login_required
def media_list():
    cat = request.args.get("category", "music")
    if cat not in CATS:
        abort(400)
    rows = db().execute("SELECT m.*, u.display_name AS up_name FROM media m LEFT JOIN users u ON u.id=m.uploader_id "
                        "WHERE m.category=? ORDER BY COALESCE(m.collection,''), m.id", (cat,)).fetchall()
    me_ = me()
    return jsonify(items=[{"id": r["id"], "title": r["title"], "collection": r["collection"] or "",
                           "url": "/files/media/" + r["file"], "size": r["size"], "mime": r["mime"],
                           "uploader": uploader_name(r),
                           "can_del": role(me_) in ("owner", "admin") or r["uploader_id"] == me_["id"]} for r in rows])


@app.get("/api/media/summary")
@login_required
def media_summary():
    d = db()
    counts = {r["category"]: r["n"] for r in d.execute("SELECT category, COUNT(*) AS n FROM media GROUP BY category")}
    counts["links"] = d.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    return jsonify(counts=counts)


# ---------- links (لینک‌های در دسترس در اینترنت ملی) ----------
def clean_url(raw):
    raw = str(raw or "").strip()
    if not raw or len(raw) > 500 or re.search(r"\s", raw):
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw.lstrip("/")
    try:
        pu = urlparse(raw)
        pu.port  # پورت نامعتبر (مثل javascript:alert(1)) ValueError می‌دهد
    except ValueError:
        return None
    if pu.scheme not in ("http", "https") or not pu.hostname:
        return None
    return raw


@app.get("/api/links")
@login_required
def links_list():
    rows = db().execute("SELECT l.*, u.display_name AS up_name FROM links l LEFT JOIN users u ON u.id=l.uploader_id "
                        "ORDER BY l.id DESC").fetchall()
    me_ = me()
    return jsonify(items=[{"id": r["id"], "title": r["title"], "url": r["url"], "at": r["created_at"],
                           "uploader": uploader_name(r),
                           "can_del": role(me_) in ("owner", "admin") or r["uploader_id"] == me_["id"]} for r in rows])


@app.post("/api/links")
@uploader_required
def links_add():
    d = request.get_json(silent=True) or {}
    url = clean_url(d.get("url"))
    if not url:
        return jsonify(error="آدرس لینک نامعتبر است (باید با http یا https باشد)"), 400
    title = str(d.get("title") or "").strip()[:120] or urlparse(url).hostname
    db().execute("INSERT INTO links(title,url,uploader_id,created_at) VALUES(?,?,?,?)",
                 (title, url, me()["id"], int(time.time())))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/links/<int:lid>")
@login_required
def links_del(lid):
    r = db().execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
    if not r:
        abort(404)
    if role(me()) not in ("owner", "admin") and r["uploader_id"] != me()["id"]:
        abort(403)
    db().execute("DELETE FROM links WHERE id=?", (lid,))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/media")
@uploader_required
def media_add():
    e = guard_upload("media", request.content_length or 0)
    if e:
        return e
    cat = request.form.get("category", "")
    fs = request.files.get("file")
    if cat not in CATS or not fs or not fs.filename:
        return jsonify(error="درخواست نامعتبر"), 400
    ext = os.path.splitext(fs.filename)[1].lower().lstrip(".")
    if ext not in CATS[cat]:
        return jsonify(error="فرمت فایل برای این بخش مجاز نیست (." + ext + ")"), 400
    stored, orig, size, mime = save_upload(fs, MEDIA_DIR)
    e = guard_upload("media", size, post=True)
    if e:
        rm(MEDIA_DIR, [stored])
        return e
    title = request.form.get("title", "").strip()[:120] or os.path.splitext(orig)[0]
    coll = request.form.get("collection", "").strip()[:80]
    db().execute("INSERT INTO media(category,title,collection,file,file_name,mime,size,created_at,uploader_id) VALUES(?,?,?,?,?,?,?,?,?)",
                 (cat, title, coll, stored, orig, mime, size, int(time.time()), me()["id"]))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/media/<int:mid>")
@login_required
def media_del(mid):
    r = db().execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        abort(404)
    if role(me()) not in ("owner", "admin") and r["uploader_id"] != me()["id"]:
        abort(403)
    db().execute("DELETE FROM media WHERE id=?", (mid,))
    db().commit()
    rm(MEDIA_DIR, [r["file"]])
    return jsonify(ok=True)


@app.get("/files/media/<name>")
@login_required
def media_file(name):
    r = db().execute("SELECT * FROM media WHERE file=?", (name,)).fetchone()
    if not r:
        abort(404)
    return serve(os.path.join(MEDIA_DIR, os.path.basename(name)), r["mime"], r["file_name"])


# ---------- storage management ----------
DEFAULTS = {"chat_file_mb": 100, "quota_mb": 1024, "media_file_mb": 2048, "media_quota_mb": 5120,
            "retention_days": 0, "emergency_purge": 1}


def settings():
    if "st" not in g:
        out = dict(DEFAULTS)
        for r in db().execute("SELECT k,v FROM settings"):
            if r["k"] in DEFAULTS:
                try:
                    out[r["k"]] = int(r["v"])
                except ValueError:
                    pass
        g.st = out
    return g.st


def usage(uid):
    d = db()
    c = d.execute("SELECT COALESCE(SUM(file_size),0) FROM messages WHERE sender_id=? AND file IS NOT NULL", (uid,)).fetchone()[0]
    m = d.execute("SELECT COALESCE(SUM(size),0) FROM media WHERE uploader_id=?", (uid,)).fetchone()[0]
    return c, m


def guard_upload(kind, size, post=False):
    """None اگر آپلود مجاز است، وگرنه پاسخ خطا. kind: chat | media | avatar"""
    u, st = me(), settings()
    if shutil.disk_usage(DATA).free - (0 if post else size) < MIN_FREE:
        return jsonify(error="فضای خالی سرور کم است و آپلود موقتاً در همه‌ی بخش‌ها بسته شده؛ به مالک خبر بده"), 507
    if kind == "avatar":
        return (jsonify(error="حجم عکس باید کمتر از ۵ مگابایت باشد"), 413) if size > 5 * MB else None
    if is_owner(u):
        return None
    if kind == "chat":
        limit, quota, used = st["chat_file_mb"], st["quota_mb"], usage(u["id"])[0]
    else:
        limit, used = st["media_file_mb"], usage(u["id"])[1]
        quota = st["media_quota_mb"] if role(u) == "uploader" else 0
    slack = 0 if post else 2 * MB
    if limit and size > limit * MB + slack:
        return jsonify(error="حداکثر حجم هر فایل %d مگابایت است" % limit), 413
    if quota and used + size > quota * MB + slack:
        return jsonify(error="سهمیه‌ی آپلود تو پر شده؛ فایل‌های قبلی را حذف کن یا از مالک بخواه"), 413
    return None


@app.get("/api/upload/check")
@login_required
def upload_check():
    kind = request.args.get("kind", "chat")
    if kind not in ("chat", "media", "avatar") or (kind == "media" and role(me()) == "member"):
        abort(403)
    e = guard_upload(kind, request.args.get("size", 0, type=int))
    return jsonify(ok=e is None, error=e[0].get_json()["error"] if e else "")


def dir_size(p):
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t


def purge_rows(rows):
    """حذف فایل‌های پیام‌ها (متن پیام می‌ماند). خروجی: بایت آزادشده."""
    freed, d = 0, db()
    for r in rows:
        p = os.path.join(CHAT_DIR, r["file"])
        try:
            freed += os.path.getsize(p)
            os.remove(p)
        except OSError:
            pass
        d.execute("UPDATE messages SET file=NULL, file_mime=NULL, file_size=NULL WHERE id=?", (r["id"],))
    d.commit()
    return freed


def sweep_orphans():
    """فایل‌های بی‌صاحب (آپلود ناموفق و ...) که بیش از یک ساعت از عمرشان گذشته."""
    d, freed, cutoff = db(), 0, time.time() - 3600
    groups = ((CHAT_DIR, "SELECT file FROM messages WHERE file IS NOT NULL"),
              (MEDIA_DIR, "SELECT file FROM media"),
              (AVATAR_DIR, "SELECT avatar FROM users WHERE avatar IS NOT NULL UNION SELECT avatar FROM chats WHERE avatar IS NOT NULL"))
    for folder, sql in groups:
        refs = {r[0] for r in d.execute(sql)}
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            try:
                if n not in refs and os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    freed += os.path.getsize(p)
                    os.remove(p)
            except OSError:
                pass
    return freed


def vacuum():
    c = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("VACUUM")
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()


def db_bytes():
    return sum(os.path.getsize(DB_PATH + x) for x in ("", "-wal", "-shm") if os.path.exists(DB_PATH + x))


def claim(key, interval):
    d, now = db(), int(time.time())
    d.execute("INSERT OR IGNORE INTO settings(k,v) VALUES(?,'0')", (key,))
    cur = d.execute("UPDATE settings SET v=? WHERE k=? AND CAST(v AS INTEGER)<=?", (str(now), key, now - interval))
    d.commit()
    return cur.rowcount == 1


bg_last = 0


@app.before_request
def background():
    """کارهای خودکار؛ هر چند دقیقه یک بار (بین ورکرها هماهنگ می‌شود)."""
    global bg_last
    now = time.time()
    if request.path != "/api/poll" or now - bg_last < 120:
        return
    bg_last = now
    try:
        st, d = settings(), db()
        if st["emergency_purge"] and claim("bg_emergency", 240) and shutil.disk_usage(DATA).free < MIN_FREE:
            while shutil.disk_usage(DATA).free < MIN_FREE + GB:
                rows = d.execute("SELECT id, file FROM messages WHERE file IS NOT NULL ORDER BY id LIMIT 20").fetchall()
                if not rows:
                    break
                purge_rows(rows)
        if claim("bg_retention", 3600):
            if st["retention_days"] > 0:
                rows = d.execute("SELECT id, file FROM messages WHERE file IS NOT NULL AND created_at<?",
                                 (int(now - st["retention_days"] * 86400),)).fetchall()
                purge_rows(rows)
            sweep_orphans()
    except Exception:
        app.logger.exception("background job failed")


@app.get("/api/owner/storage")
@owner_required
def o_storage():
    d, du = db(), shutil.disk_usage(DATA)
    users = []
    for r in d.execute("SELECT id, display_name, username FROM users ORDER BY id"):
        c, m = usage(r["id"])
        users.append({"id": r["id"], "name": r["display_name"], "username": r["username"], "chat": c, "media": m})
    groups = [{"id": r["id"], "name": r["name"], "bytes": r["b"], "count": r["n"]} for r in d.execute(
        "SELECT c.id, c.name, COALESCE(SUM(m.file_size),0) AS b, COUNT(m.id) AS n FROM chats c "
        "LEFT JOIN messages m ON m.chat_id=c.id AND m.deleted=0 WHERE c.type IN ('group','channel') GROUP BY c.id")]
    dm = d.execute("SELECT COALESCE(SUM(m.file_size),0) FROM messages m JOIN chats c ON c.id=m.chat_id "
                   "WHERE c.type='dm'").fetchone()[0]
    return jsonify(disk={"total": du.total, "used": du.used, "free": du.free, "min_free": MIN_FREE},
                   bytes={"chat": dir_size(CHAT_DIR), "media": dir_size(MEDIA_DIR),
                          "avatar": dir_size(AVATAR_DIR), "db": db_bytes()},
                   users=users, groups=groups, dm_bytes=dm, settings=settings())


@app.put("/api/owner/settings")
@owner_required
def o_settings():
    d = request.get_json(silent=True) or {}
    for k in DEFAULTS:
        if k in d:
            try:
                v = int(d[k])
            except (TypeError, ValueError):
                return jsonify(error="مقدار نامعتبر است"), 400
            if v < 0 or v > 10 ** 7:
                return jsonify(error="مقدار نامعتبر است"), 400
            if k == "emergency_purge":
                v = 1 if v else 0
            db().execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, str(v)))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/owner/purge-files")
@owner_required
def o_purge_old():
    days = (request.get_json(silent=True) or {}).get("days", 0)
    if not isinstance(days, int) or days < 1:
        return jsonify(error="تعداد روز باید حداقل ۱ باشد"), 400
    rows = db().execute("SELECT id, file FROM messages WHERE file IS NOT NULL AND created_at<?",
                        (int(time.time() - days * 86400),)).fetchall()
    return jsonify(freed=purge_rows(rows), count=len(rows))


@app.post("/api/owner/purge-user")
@owner_required
def o_purge_user():
    uid = (request.get_json(silent=True) or {}).get("user_id")
    rows = db().execute("SELECT id, file FROM messages WHERE file IS NOT NULL AND sender_id=?", (uid,)).fetchall()
    return jsonify(freed=purge_rows(rows), count=len(rows))


@app.post("/api/owner/maintenance")
@owner_required
def o_maintenance():
    before = db_bytes()
    freed = sweep_orphans()
    vacuum()
    return jsonify(freed=freed + max(0, before - db_bytes()))


# ---------- frontend ----------
@app.get("/")
def index():
    r = send_from_directory(app.static_folder, "index.html")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                                            "frame-ancestors 'none'")
    return r


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), threaded=True)
