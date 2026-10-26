# Stash Scrapers

Two Stash scene scrapers for rule34.xxx and rule34video.com, built to work
well with [Scene Tagger](https://github.com/jojo-topo/stash-plugins) but
usable on their own through Stash's normal scrape UI too.

These are personal tools, built for my own setup and shared as-is because
they turned out complete enough to be useful to others. They're tested on
Windows with Python installed directly on the host - not verified on
Docker/Linux setups, and I'm not actively maintaining broad compatibility.
Pull requests / forks welcome if you adapt them for your environment.

## Scrapers

### rule34-python

Scrapes scenes and images from **rule34.xxx**. Python standard library only
- no `pip install` required.

> **Version 2.0 needs a rule34.xxx API key.** The site's post pages now sit
> behind a CAPTCHA, so the scraper uses the official API instead of reading
> the HTML. See *Setup* below, it takes two minutes.

#### Setup

1. **Install it** (see *Installation* below) and make sure `python` can be
   found: either it is in your PATH, or you set it in Stash under
   **Settings → System → Python executable path**.
2. **Get your API key and user ID** from your rule34.xxx account options page
   (<https://rule34.xxx/index.php?page=account&s=options>).
3. **Run a scrape once.** It will fail and create `rule34-config.json` next to
   the script. The Stash log (Settings → Logs) tells you the exact path.
4. **Fill in `api_key` and `user_id`** in that file, then scrape again.
5. *(Optional, recommended)* **Download the tag database once**: open a
   terminal in the scraper folder and run

   ```
   python import_tags.py
   ```

   It takes about 30-40 minutes, can be interrupted with `Ctrl+C` and resumed
   by running it again. It stores the type of every tag (artist, character,
   general...) in a local `rule34-tags.db`, so scrapes don't have to look tags
   up one by one through the rate-limited API. You can keep scraping while it
   runs, and skip it entirely: the database also fills itself up as you scrape,
   only slower at first.

#### What it does

- Matches files by the post ID in the file name, or by URL. Recognised names:

  | File name | Post ID |
  |---|---|
  | `1234567` / `1234567_2` | 1234567 |
  | `rule34_1234567` | 1234567 |
  | `rule34.xxx-1234567` | 1234567 |
  | `<name> - r34xxx 1234567` | 1234567 |

- Fills date, studio (the artist), performers (characters), tags, source URLs
  and the thumbnail. Translation notes of the post are added to the details.
- Resolves studio/performer names against your local Stash database (by
  name and alias) so re-scraping doesn't create duplicates.
- Encodes every artist candidate from a multi-artist post into the scene's
  `details` field as `Artists: name1[id1] | name2 | name3[id3]` - this is
  the convention [Scene Tagger](https://github.com/jojo-topo/stash-plugins)
  reads to offer a choice between multiple studios on a single post.
- Built-in rate limiting shared across concurrent scrape processes: the API
  cuts off at about 60 requests per minute, so the scraper keeps to 50.
- Never leaves Stash with an empty answer: if something goes wrong the script
  returns no result and writes the error to the Stash logs.

#### Troubleshooting

Turn the log level to **Debug** (Settings → Logs) and look for lines starting
with `[Scrape / rule34-python]`.

| Message | Cause and fix |
|---|---|
| `api_key / user_id are missing` | Fill both fields in `rule34-config.json` (the log gives its path). |
| `API key refused? check api_key / user_id` | Wrong key or user ID. Copy them again from your account options page. |
| `Could not extract rule34 post ID` | The file name isn't recognised. Rename it (see the table above) or scrape by URL. |
| `Post ... not found (deleted?)` | The post was removed from rule34.xxx. |
| `scrapeSceneURL ... http error 403` | Not this scraper: another scraper that handles rule34.xxx URLs (like a community `Rule34xxx` one) is reading the CAPTCHA-protected page. Disable it. |
| `could not unmarshal json from script output: EOF` | The script crashed before answering (fixed in 2.0, which now logs the cause). Update, and send the `Traceback` from the log if it still happens. |
| Python not found | Put `python` in your PATH or set the executable in Stash settings (see Setup, step 1). |

### Rule34VideoFromID

Scrapes scenes from **rule34video.com** by resolving the numeric video ID
found in the filename or title into a full URL, bypassing that site's
requirement for a full slug in the URL.

- Dependencies: `pip install requests lxml` (`lxml` optional but
  recommended - without it, only title/date/image are extracted).
- **Requires manual setup**: rule34video.com is protected by DDoS-Guard,
  which needs a set of session cookies to let scraper requests through.
  Open the `.py` file and follow the instructions in its header docstring
  to fill in your own cookie values (copied from your browser's DevTools).
  These expire periodically and need to be refreshed the same way when
  scraping starts failing with a 403 or a challenge page.

## Installation

1. In Stash: **Settings → Metadata Providers → Add Source**, with the raw URL of this
   repo's `index.yml` file (e.g.
   `https://raw.githubusercontent.com/jojo-topo/stash-scrapers/master/index.yml`).
2. Install the scraper(s) you want from the list.
3. For `rule34-python`, follow its *Setup* section (API key). For
   `Rule34VideoFromID`, complete the cookie setup described above before
   using it.

## Changelog

### rule34-python 2.0.0
- Switched to the official rule34.xxx API (the HTML pages are behind a
  CAPTCHA). **An API key and user ID are now required.**
- New: `import_tags.py` downloads the tag types once into a local database.
- New: file names like `rule34.xxx-1234567.mp4` are recognised.
- Fix: a crash no longer ends in `could not unmarshal json ... EOF`; the error
  is written to the Stash logs. A locked or read-only tag database and network
  timeouts no longer abort the scrape.
- Log messages are now in English and tell you where to put the API key.

## License

MIT
