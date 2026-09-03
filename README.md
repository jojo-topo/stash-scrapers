# Stash Scrapers

Two Stash scene scrapers for rule34.xxx and rule34video.com, built to work
well with [Scene Tagger](https://github.com/jojo-topo/stash-plugins) but
usable on their own through Stash's normal scrape UI too.

## Scrapers

### rule34-python

Scrapes scenes and images from **rule34.xxx**. Python standard library only
- no `pip install` required.

- Matches files by post ID found in the filename, or by URL.
- Resolves studio/performer names against your local Stash database (by
  name and alias) so re-scraping doesn't create duplicates.
- Encodes every artist candidate from a multi-artist post into the scene's
  `details` field as `Artists: name1[id1] | name2 | name3[id3]` - this is
  the convention [Scene Tagger](https://github.com/jojo-topo/stash-plugins)
  reads to offer a choice between multiple studios on a single post.
- Built-in rate limiting (randomized delay + backoff on HTTP 429) shared
  across concurrent scrape processes via a timestamp file.

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

1. In Stash: **Settings → Scrapers → Add Source**, with the raw URL of this
   repo's `index.yml` file (e.g.
   `https://raw.githubusercontent.com/jojo-topo/stash-scrapers/main/index.yml`).
2. Install the scraper(s) you want from the list.
3. For `Rule34VideoFromID`, complete the cookie setup described above before
   using it.

## License

MIT
