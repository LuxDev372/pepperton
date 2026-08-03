"""The diner radio — Pepperton's one pipe to the real world.

Real RSS headlines become in-world broadcasts. The villagers' knowledge
cutoffs mean genuinely new events land on them like news reaching
someone who's been off-grid — misreadings included. That's a feature.
"""

import json
import os
import random
import time

import config

_FALLBACK = [
    "We're getting reports of... well, static, mostly. We'll keep trying.",
    "The signal's weak today, folks. In local news: the ducks are fine.",
    "Transmitter trouble again. Stay tuned.",
]


def _fetch_feeds():
    try:
        import feedparser
    except ImportError:
        return None
    headlines = []
    for url in config.RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:10]:
                title = getattr(e, "title", "").strip()
                if title:
                    headlines.append(title)
        except Exception:
            continue
    return headlines or None


def get_headlines():
    """Cached real headlines; refetch when stale; graceful static when offline."""
    cache = config.NEWS_CACHE
    now = time.time()
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                data = json.load(f)
            if now - data.get("fetched_at", 0) < config.NEWS_MAX_AGE_HOURS * 3600:
                return data.get("headlines") or list(_FALLBACK)
        except (json.JSONDecodeError, OSError):
            pass
    headlines = _fetch_feeds()
    if headlines:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": now, "headlines": headlines}, f)
        return headlines
    return list(_FALLBACK)


class Radio:
    def __init__(self):
        self._used = set()
        self.dead_day = None    # set by the Director: static all day

    def bulletin(self, n=None):
        """Next batch of headlines, avoiding repeats within a run."""
        n = n or config.RADIO_HEADLINES_PER_BULLETIN
        headlines = get_headlines()
        fresh = [h for h in headlines if h not in self._used]
        if len(fresh) < n:
            self._used.clear()
            fresh = headlines
        batch = fresh[:n]
        self._used.update(batch)
        return batch

    def maybe_broadcast(self, world):
        if not config.RADIO_ENABLED:
            return
        for t in config.RADIO_BULLETIN_TIMES:
            if world.clock.at(t):
                self._deliver_bulletin(world)

    def _deliver_bulletin(self, world):
        mode = getattr(config, "NEWS_DELIVERY", "both")
        loc = world.radio_location() or "the plaza"
        dead = self.dead_day == world.clock.day

        if mode in ("radio", "both"):
            if dead:
                world.emit("radio", None,
                           "…static. Just static, where the news should be.", loc)
            else:
                world.emit("radio", None,
                           f"This is {config.TOWN_NAME} Community Radio with the news.", loc)
                for h in self.bulletin():
                    world.emit("radio", None, h, loc)
                world.emit("radio", None,
                           "And that's the news. Back to the music.", loc)

        if mode in ("phone", "both"):
            pool = self.bulletin(config.NEWS_POOL_PER_BULLETIN)
            world.emit("radio", None,
                       "(news alerts light up phones all over town)", loc,
                       deliver=False)
            for h in pool:
                world.emit("radio", None, h, loc, deliver=False)
            k = min(config.NEWS_ALERTS_PER_VILLAGER, len(pool))
            for agent in world.agents.values():
                picks = random.sample(pool, k)
                for h in picks:
                    agent.pending.append({
                        "text": (f'Your phone buzzes — news alert: "{h}"'),
                        "interrupt": True,
                        "sim_time": world.clock.hhmm,
                    })
