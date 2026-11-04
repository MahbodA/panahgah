<div align="center">

# 🏠 Panahgah · پناهگاه

### Your own messenger. Even when the internet isn't there.

A tiny, self-hosted Persian messenger for you and your people.<br>
One server, one command, zero external dependencies.

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/backend-Flask-lightgrey)
![SQLite](https://img.shields.io/badge/database-SQLite-003B57)
![Ubuntu](https://img.shields.io/badge/ubuntu-22.04%20%7C%2024.04-E95420)
![Offline](https://img.shields.io/badge/offline%20install-supported-success)

[فارسی](README.fa.md) · [Quick start](#-quick-start) · [Offline kit](#-offline-install-no-internet) · [Features](#-features) · [Administration](#-administration)

</div>

---

## 💡 Why Panahgah?

When the international internet goes down, the apps we rely on go with it. Panahgah is the answer to a simple question: **what if your group had its own messenger, on its own server, that keeps working as long as the local network does?**

You put it on a server, create accounts, and hand out usernames and passwords. That's it. No CDN, no online fonts, no third-party services, no sign-up page, no phone numbers. Everything the app needs ships with it.

It was built for national-network outages, but it's just as good for anyone who wants a private messenger for friends, family or a small team.

## 📸 See it in action

<table>
  <tr>
    <td align="center"><b>Sign in</b><br><img src="screenshots/login.png" alt="Login"></td>
    <td align="center"><b>Group chat</b><br><img src="screenshots/chat.png" alt="Group chat"></td>
  </tr>
  <tr>
    <td align="center"><b>Entertainment</b><br><img src="screenshots/Hobbies.png" alt="Entertainment"></td>
    <td align="center"><b>Management panel</b><br><img src="screenshots/Managment.png" alt="Management panel"></td>
  </tr>
</table>

## ✨ Features

| | |
|---|---|
| 💬 **Chats** | Private messages, groups, and channels where only approved people can post. Replies, message deletion, `@mentions`, read receipts (✓ / ✓✓) |
| 📎 **Files & voice** | Attachments, images (pick, drag-and-drop or paste), voice messages |
| 🎬 **Entertainment** | Music, podcasts, movies & series, PDF books, all playable in the app, plus a shared list of links that open on the domestic network |
| 🛡️ **Roles** | Owner, admin, level-2 uploader, regular user |
| 💾 **Storage management** | Disk guards, per-user quotas, automatic emergency clean-up, owner dashboard |
| 📱 **PWA** | Add it to your phone's home screen and it feels like a native app |
| 🌐 **Persian, RTL, mobile-first** | Designed for phones from the start |
| 🪶 **Tiny stack** | Python + Flask, SQLite, a single HTML file for the frontend. No build step, no Docker needed |

## 🚀 Quick start

**You need:** a server with **Ubuntu 22.04 / 24.04** (other Debian-based systems should work but are untested), root access and Python 3.10+. 1 vCPU and 1 GB RAM is plenty for a small group.

On the server, as root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/MahbodA/panahgah/main/install.sh)
```

The installer asks for the owner's username, password and display name, installs everything under `/opt/panahgah`, and starts a systemd service on **port 8001** (nginx and port 80 are left alone). Then open:

```
http://SERVER_IP:8001
```

<details>
<summary><b>Non-interactive install, updates and options</b></summary>

```bash
OWNER_USER=admin OWNER_PASS='a-strong-password' OWNER_NAME='My Name' PORT=8001 \
  bash <(curl -fsSL https://raw.githubusercontent.com/MahbodA/panahgah/main/install.sh)
```

- **Updating:** run the same command again. The code is updated and all data (users, messages, files) is kept.
- **PyPI blocked?** The installer falls back to a domestic mirror automatically. You can also pass `PIP_INDEX_URL=...`.
- **Different port:** `PORT=8002`.

</details>

> ⚠️ **Do this before an outage.** The online installer needs GitHub, PyPI and apt. If the international internet is already down, use the offline kit below.

## 📦 Offline install (no internet)

Everything the server needs, including every Python library, is in [`offline/`](offline). **Nothing is downloaded**: no apt, no pip, no GitHub. Keep a copy of these two files somewhere safe *now*, so they're in your hands when you need them.

1. Grab `offline/panahgah-offline.zip` and `offline/install.sh`.
2. Copy both to the server's `/opt`:
   ```bash
   scp panahgah-offline.zip install.sh root@SERVER_IP:/opt/
   ```
3. Run the installer:
   ```bash
   ssh root@SERVER_IP
   cd /opt && bash install.sh
   ```
4. Answer the prompts (owner username, password, display name). Done: `http://SERVER_IP:8001`.

The same options work here: `PORT=8002`, `OWNER_USER=... OWNER_PASS=...`, `PANAHGAH_DIR=...`. Running it again with a newer zip updates the code and keeps your data.

## 🛠️ Administration

After installation you get the `panahgah-manage` command:

```bash
panahgah-manage list                                    # list users
panahgah-manage user     NAME PASSWORD "Display name"   # regular user
panahgah-manage uploader NAME PASSWORD "Display name"   # level 2: can upload entertainment content
panahgah-manage admin    NAME PASSWORD "Display name"   # admin
panahgah-manage passwd   NAME NEWPASSWORD               # reset a password
panahgah-manage faststart                               # make old mp4/m4a files start playing faster
```

Day-to-day work (creating users, groups and channels, managing storage) happens in the **Manage** panel of the web app, signed in as the owner.

| Role | Can do |
|---|---|
| **Owner** | Everything: users, admins, groups, channels, storage, any content |
| **Admin** | Delete messages, add and edit regular and level-2 users, manage entertainment content |
| **Level 2** | Everything a regular user can, plus upload entertainment content and add links |
| **User** | Messages, files, voice, replies, delete own messages |

```bash
systemctl status panahgah      # is it running?
journalctl -u panahgah -f      # live logs
systemctl restart panahgah
```

<details>
<summary><b>⚙️ Configuration</b></summary>

Set these in `/etc/systemd/system/panahgah.service`, then run `systemctl daemon-reload && systemctl restart panahgah`.

| Variable | Default | Meaning |
|---|---|---|
| `OWNER_USERNAME` | set by installer | Username that has owner rights |
| `MAX_UPLOAD_MB` | `3072` | Hard limit for a single request |
| `MIN_FREE_GB` | `4` | Uploads are refused when free disk drops below this |
| `HTTPS` | `0` | Set to `1` only when the site is really served over HTTPS (secure cookies) |
| `PANAHGAH_DATA` | `<app>/data` | Where the database and uploads live |
| `ACCEL_REDIRECT` | `0` | `1` lets nginx serve uploaded files (see `docs/DEPLOY.fa.txt`, section 19) |

</details>

<details>
<summary><b>🎙️ HTTPS and voice recording</b></summary>

Browsers only allow direct microphone recording on HTTPS. Over plain HTTP the voice button opens the phone's own recorder instead, which still works fine. For direct in-app recording, put nginx or Caddy with a certificate in front (a self-signed certificate is enough for IP-only setups) and set `HTTPS=1`. Details: [`docs/DEPLOY.fa.txt`](docs/DEPLOY.fa.txt), section 13.

</details>

<details>
<summary><b>🗄️ Backup and restore</b></summary>

Everything lives in `/opt/panahgah/data` (database, uploads, session key).

```bash
systemctl stop panahgah
tar czf /root/panahgah-backup-$(date +%F).tar.gz -C /opt/panahgah data
systemctl start panahgah
```

To restore: stop the service, extract the archive into `/opt/panahgah`, run `chown -R panahgah:panahgah /opt/panahgah/data`, and start the service.

</details>

## 🗂️ Repository layout

```
install.sh                      online one-click installer
app/                            the application (single source of truth)
  app.py  manage.py  requirements.txt  static/
offline/
  install.sh                    offline installer
  panahgah-offline.zip          app + all Python libraries
tools/
  build-offline.sh              rebuild the offline zip from app/
docs/
  DEPLOY.fa.txt                 detailed deployment notes (Persian)
  Maintenance_Handbook.fa.pdf   full maintenance handbook (Persian)
```

Changed something in `app/`? Rebuild the offline kit on a machine with internet (ideally Ubuntu 24.04):

```bash
bash tools/build-offline.sh
```

## 🔒 Security notes

- Messages are **not end-to-end encrypted**. Whoever administers the server can read the database, so only invite people you trust.
- Out of the box the site is plain HTTP on a port. Use HTTPS if you can, and always set strong passwords.
- There's no public sign-up. Only the owner and admins create accounts.
- Keep `data/secret.key` private, and back up `data/`.

## 📄 License

[MIT](LICENSE). The bundled brand font (Grand Hotel) is under the SIL Open Font License.

---

<div align="center">

Built with ❤️ by **Mahbod.A** · If Panahgah helps you, a ⭐ on the repo means a lot.

</div>
