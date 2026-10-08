"""Copies the server owner's new Discord posts to Telegram, following ROUTES below.

Runs on GitHub Actions every few minutes (.github/workflows/relay.yml). All tokens and
IDs come from environment variables (GitHub Secrets); nothing sensitive lives in this repo.
relay_state.json only records the last Discord message id relayed per channel.

    python3 relay.py           # relay anything new
    python3 relay.py --check   # show routes and whether every target resolves
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Discord channel name -> Telegram targets.
# "channel" = public broadcast channel; (group, topic) = a topic in a Telegram group.
ROUTES = {
    "☀️・daily-outlook": ["channel", ("mentorship", "Market Outlook")],
    "📊・trade-recaps": ["channel", ("mentorship", "Trade Recaps")],
    "🎓・education": ["channel", ("mentorship", "Education")],
    "🎁・free-stuff": ["channel", ("mentorship", "PDF Materials")],
    "📢・announcements": [("mentorship", "Announcements")],
    "🎓・curriculum": [("mentorship", "Education")],
}

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "relay_state.json"
DISCORD_API = "https://discord.com/api/v10"
PIN_MARKER = "​"  # set by the server builder on pinned templates; never relay those
CAPTION_MAX = 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
FORUM = 15


def env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        sys.exit(f"Missing environment variable {key}")
    return value


def http(url: str, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            payload = json.loads(err.read() or b"{}")
            if err.code == 429:
                wait = payload.get("retry_after") or payload.get("parameters", {}).get("retry_after", 2)
                time.sleep(float(wait) + 0.5)
                continue
            raise RuntimeError(f"{url.split('/bot')[0]} -> {err.code}: {payload}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    raise RuntimeError("gave up after 5 tries")


def discord(path: str):
    return http(DISCORD_API + path, headers={
        "Authorization": f"Bot {env('DISCORD_BOT_TOKEN')}",
        "User-Agent": "DiscordBot (https://github.com/OkurutEric/cirefx-relay, 1.0)",
    })


def telegram(method: str, **params):
    return http(f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/{method}", params)["result"]


def norm_name(name: str) -> str:
    return name.replace("️", "").strip().lower().replace(" ", "-")


def to_html(text: str) -> str:
    """Discord markdown -> Telegram HTML."""
    text = re.sub(r"<[@#][!&]?\d+>", "", text)  # mentions mean nothing on Telegram
    out = html.escape(text, quote=False)
    out = re.sub(r"```\w*\n?(.*?)```", r"<pre>\1</pre>", out, flags=re.S)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", out)
    out = re.sub(r"__(.+?)__", r"<u>\1</u>", out)
    out = re.sub(r"~~(.+?)~~", r"<s>\1</s>", out)
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out).strip()


def resolve_targets() -> dict:
    topics = json.loads(env("TELEGRAM_TOPICS"))
    resolved = {}
    for channel, targets in ROUTES.items():
        out = []
        for t in targets:
            if t == "channel":
                out.append((env("TELEGRAM_CHANNEL_ID"), None, "broadcast channel"))
            else:
                group, topic = t
                thread = topics.get(group, {}).get(topic) or sys.exit(f"Unknown topic {group}/{topic}")
                out.append((env(f"TELEGRAM_{group.upper()}_GROUP_ID"), thread, f"{group} › {topic}"))
        resolved[norm_name(channel)] = out
    return resolved


def send(chat, thread, text: str, images: list, files: list) -> None:
    base = {"chat_id": chat, **({"message_thread_id": thread} if thread else {})}
    caption_fits = len(text) <= CAPTION_MAX
    if text and not (images and caption_fits):
        telegram("sendMessage", **base, text=text, parse_mode="HTML",
                 link_preview_options={"is_disabled": not re.search(r"https?://", text)})
    if len(images) == 1:
        extra = {"caption": text, "parse_mode": "HTML"} if text and caption_fits else {}
        telegram("sendPhoto", **base, photo=images[0], **extra)
    elif images:
        media = [{"type": "photo", "media": url} for url in images[:10]]
        if text and caption_fits:
            media[0].update(caption=text, parse_mode="HTML")
        telegram("sendMediaGroup", **base, media=media)
    for url in files:
        telegram("sendDocument", **base, document=url)


def text_posts(ch, after) -> list:
    query = f"?limit=50&after={after}" if after else "?limit=1"
    return [{"id": m["id"], "message": m} for m in discord(f"/channels/{ch['id']}/messages{query}")]


def forum_posts(guild, ch) -> list:
    threads = [t for t in discord(f"/guilds/{guild}/threads/active")["threads"] if t["parent_id"] == ch["id"]]
    threads += discord(f"/channels/{ch['id']}/threads/archived/public?limit=20")["threads"]
    posts = {}
    for t in threads:
        if t["id"] in posts:
            continue
        try:  # a forum post's first message has the same id as its thread
            msg = discord(f"/channels/{t['id']}/messages/{t['id']}")
        except RuntimeError:
            continue
        posts[t["id"]] = {"id": t["id"], "title": t["name"], "message": msg}
    return list(posts.values())


def main(check: bool) -> None:
    guild = env("DISCORD_GUILD_ID")
    routes = resolve_targets()
    channels = {norm_name(c["name"]): c for c in discord(f"/guilds/{guild}/channels")}
    owner = discord(f"/guilds/{guild}")["owner_id"]

    if check:
        for name, targets in routes.items():
            ok = "✓" if name in channels else "✗ missing in Discord"
            print(f"{ok} #{name} → " + ", ".join(label for _, _, label in targets))
        return

    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    for name, targets in routes.items():
        ch = channels.get(name)
        if not ch:
            continue
        last = state.get(ch["id"])
        posts = forum_posts(guild, ch) if ch["type"] == FORUM else text_posts(ch, last)
        if last is None:  # first time we see this channel: start from now, don't flood history
            state[ch["id"]] = max([p["id"] for p in posts], key=int, default="0")
            continue
        for post in sorted((p for p in posts if int(p["id"]) > int(last)), key=lambda p: int(p["id"])):
            msg = post["message"]
            if (msg["author"]["id"] == owner and not msg.get("webhook_id")
                    and not msg["content"].startswith(PIN_MARKER)):
                text = to_html(msg["content"])
                if post.get("title"):
                    text = f"<b>{html.escape(post['title'], quote=False)}</b>\n\n{text}".strip()
                atts = msg.get("attachments", [])
                images = [a["url"] for a in atts if a["filename"].lower().endswith(IMAGE_EXT)]
                files = [a["url"] for a in atts if a["url"] not in images]
                if not (text or images or files):
                    # Discord blanks content when Message Content Intent is off; retry next run.
                    print(f"! #{name}: message looks empty. Is Message Content Intent on? Will retry.")
                    break
                for chat, thread, label in targets:
                    send(chat, thread, text, images, files)
                    print(f"→ #{name} → {label}")
            state[ch["id"]] = post["id"]
            STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")  # save progress as we go
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main(check="--check" in sys.argv)
