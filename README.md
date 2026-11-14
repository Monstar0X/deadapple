# MangaBot

Telegram manga / manhwa downloader: type a title → pick a site (or **♞ ALL SEARCH**) → pick a result →
download a **single chapter** or a **batch** as PDF / CBZ. Button-driven UI.

Built from the best parts of *Manga-Bot* (search → chapters flow, per-user queue, file-id cache) and
*AUTO-MANGA-BOT* (force-sub, admin tools, watermark / thumbnail / caption / file-name branding).
All premium/subscription-for-updates/auto-upload code was removed.

## Run
```bash
cp .env.example .env   # fill API_ID, API_HASH, BOT_TOKEN, OWNER_ID, DB_URL
pip install -r requirements.txt
python bot.py
```
Docker: `docker build -t mangabot . && docker run --env-file .env mangabot`

## Limits & Subscribers
| | Batch size | Monthly quota |
|---|---|---|
| Free user | `MAX_BATCH` (default **5**) | none (500/month available, **OFF** by default) |
| Subscriber | `SUBSCRIBER_MAX_BATCH` (default **20**, `0` = all) | exempt |
| Owner / admins | unlimited | exempt |

* Subscribers are granted **only** by owner/admins: `/addsub <id> [days]`, `/delsub <id>`, `/subs`. No payments in the bot.
* Turn the monthly quota on later: `/monthly on` (or `/monthly 300` to change the number). While OFF everyone downloads unlimited; only the batch limit applies.
* Single chapters are never batch-limited.

## Admin commands
`/admin` (panel) · `/help_admin` lists everything: subscribers, `/addfsub` force-sub, `/ban`, `/broadcast`, `/stats`,
`/setcaption`, `/setformat`, `/setwm`, `/setthumb`, `/clearcache`, `/sites`, `/checksites`, `/addadmin`.

## Sites
Sources live in **`sites.json`** (engine + URL). Engines: `madara`, `themesia`, `mangadex`, `weebcentral`, `kakalot`, `asura`.
Manga sites change domains constantly — when one moves, edit its `url` (or set `"enabled": false`) and restart. No code changes needed.
The bot checks every site on start and every `HEALTH_INTERVAL` seconds (🟢 online / 🔴 offline, `/sites` shows reasons);
ALL SEARCH skips offline sites.
Paid / premium / coin-locked chapters are detected and never offered.

### Not included (on purpose)
* Atsumaru, Comix, Divascans (🔒 in your screenshots – locked/paid)
* WEBTOON official (paid "fast pass" chapters, DRM-style image delivery)
* Novel sites (Novel Archive, KDT Novels) – need a text/EPUB pipeline
* AllManga – GraphQL/encrypted API; needs its own engine
* Adult-content sources and categories are removed; MangaDex requests safe-rated titles only.

## Honest status
* Everything inside the bot (UI flows, limits, queue, PDF/CBZ, caching, admin) is covered by offline tests (`python tests/test_flow.py` etc.).
* **Site scrapers were not run against the live websites** (no network where this was built). Parsers are tested on HTML shaped like each theme;
  domains in `sites.json` are best guesses. Expect to fix a few URLs/engines after your first `/checksites`.
* Scraping identifies itself as `MangaBot/1.0`, checks each host's `robots.txt`, applies per-host throttling, and does not bypass Cloudflare challenges or access restrictions.
* Explicitly adult-labelled sources and categories are removed. MangaDex is restricted to its `safe` rating; known explicit labels in search titles and metadata are filtered. Unlabelled or incorrectly labelled content on third-party sites cannot be guaranteed safe.
* Scraped content is usually copyrighted – you are responsible for how you operate the bot.
