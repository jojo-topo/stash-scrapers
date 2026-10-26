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

- Uses rule34.xxx's **official API**, because the site's post pages now sit
  behind a CAPTCHA. This needs a free **API key and user ID**: get them from
  your rule34.xxx account options page, then fill `api_key` and `user_id` in
  `rule34-config.json` (created next to the script on first run).
- Matches files by post ID found in the filename, or by URL. Recognised file
  names: `1234567`, `1234567_2`, `rule34_1234567`, `rule34.xxx-1234567` and
  `<name> - r34xxx 1234567`.
- Optional but recommended: run `python import_tags.py` once from the scraper
  folder (about 30-40 minutes, resumable). It downloads the type of every tag
  (artist, character, general...) into a local database, so scrapes don't have
  to look tags up one by one through the rate-limited API.
- Needs `python` to be reachable: either in your PATH, or set in Stash under
  **Settings → System → Python executable path**.
- Resolves studio/performer names against your local Stash database (by
  name and alias) so re-scraping doesn't create duplicates.
- Encodes every artist candidate from a multi-artist post into the scene's
  `details` field as `Artists: name1[id1] | name2 | name3[id3]` - this is
  the convention [Scene Tagger](https://github.com/jojo-topo/stash-plugins)
  reads to offer a choice between multiple studios on a single post.
- Built-in rate limiting (randomized delay + backoff on HTTP 429) shared
  across concurrent scrape processes via a timestamp file. The API cuts off
  at about 60 requests per minute, so the scraper keeps to 50.
- A crash never leaves Stash with an empty answer (`could not unmarshal json
  ... EOF`): the script returns no result and writes the error to the Stash
  logs (Settings → Logs, level Debug).

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
3. For `Rule34VideoFromID`, complete the cookie setup described above before
   using it.

## License

MIT
