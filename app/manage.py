#!/usr/bin/env python3
"""مدیریت پناهگاه از خط فرمان.

  python manage.py admin  USERNAME PASSWORD ["نام نمایشی"]   ساخت یا ارتقا به مدیر
  python manage.py uploader USERNAME PASSWORD ["نام"]        ساخت کاربر سطح ۲ (آپلود سرگرمی)
  python manage.py user   USERNAME PASSWORD ["نام نمایشی"]   ساخت کاربر عادی
  python manage.py passwd USERNAME NEWPASSWORD               تغییر رمز
  python manage.py list                                      لیست کاربران
  python manage.py faststart                                 آماده‌سازی فیلم‌ها/صوت‌های قدیمی برای شروع سریع پخش
"""
import sys, time, sqlite3
from werkzeug.security import generate_password_hash
import app as a


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    c = sqlite3.connect(a.DB_PATH, timeout=30)
    if cmd in ("admin", "user", "uploader") and len(args) >= 2:
        un, pw = args[0], args[1]
        name = args[2] if len(args) > 2 else un
        if not a.USERNAME_RE.match(un) or len(pw) < 6:
            print("نام کاربری نامعتبر یا رمز کمتر از ۶ کاراکتر")
            return 1
        adm = 1 if cmd == "admin" else 0
        rl = {"admin": "admin", "user": "member", "uploader": "uploader"}[cmd]
        if c.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
            c.execute("UPDATE users SET password_hash=?, is_admin=?, role=? WHERE username=?", (generate_password_hash(pw), adm, rl, un))
        else:
            c.execute("INSERT INTO users(username,password_hash,display_name,is_admin,role,created_at) VALUES(?,?,?,?,?,?)",
                      (un, generate_password_hash(pw), name, adm, rl, int(time.time())))
        c.commit()
        print("انجام شد:", un)
    elif cmd == "passwd" and len(args) == 2:
        c.execute("UPDATE users SET password_hash=? WHERE username=?", (generate_password_hash(args[1]), args[0]))
        c.commit()
        print("رمز تغییر کرد")
    elif cmd == "faststart":
        import os
        n = ok = 0
        for d in (a.MEDIA_DIR, a.CHAT_DIR):
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(a.FAST_EXT):
                    n += 1
                    p = os.path.join(d, fn)
                    old = os.path.getsize(p)
                    if a.faststart(p):
                        ok += 1
                        new = os.path.getsize(p)
                        c.execute("UPDATE media SET size=? WHERE file=?", (new, fn))
                        c.execute("UPDATE messages SET file_size=? WHERE file=?", (new, fn))
                        print("اصلاح شد:", fn)
        c.commit()
        print("بررسی‌شده: %d — اصلاح‌شده: %d (بقیه از قبل آماده بودند یا قابل اصلاح نبودند)" % (n, ok))
    elif cmd == "list":
        for r in c.execute("SELECT id,username,display_name,is_admin FROM users"):
            print(r[0], r[1], r[2], "(مدیر)" if r[3] else "")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
