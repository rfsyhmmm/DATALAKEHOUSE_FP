"""
social_dw / silver.py — bronze/json (tweets) -> silver/social (Parquet)

Structures, cleans and analyzes the raw social chatter into one row per tweet:
  * flatten the nested tweet + user + _meta objects
  * dedupe on tweet_id
  * derive analysis features: sentiment_score, engagement_total, aspect_en,
    is_spike, event_date, plus a cleaned text column (emojis / @mentions / URLs
    stripped) for downstream NLP.

The synthetic generator already ships ground-truth sentiment/aspect labels in the
`_meta` block, so no ML inference is needed here — this stage normalizes them into
an analytics-ready table.

Output:
  silver/social/sentiment.parquet  — 1 row per tweet
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BRONZE = REPO_ROOT / "medallion_layer" / "bronze" / "json"
SILVER = REPO_ROOT / "medallion_layer" / "silver" / "social"

# label normalization
SENTIMENT_SCORE = {"positive": 1, "neutral": 0, "negative": -1}
ASPECT_EN = {
    "kualitas": "Quality", "pengiriman": "Delivery", "harga": "Price",
    "layanan": "Service", "durabilitas": "Durability", "umum": "General",
}

# emoji / symbol blocks + @mentions + URLs (for clean_text)
RE_URL     = re.compile(r"https?://\S+")
RE_MENTION = re.compile(r"@\w+")
RE_EMOJI   = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symbols, pictographs, emoji, supplemental
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U00002190-\U000021FF"   # arrows
    "\U00002300-\U000023FF"   # misc technical
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "]+"
)
RE_WS      = re.compile(r"\s+")

# conformed output column order
SENTIMENT_COLUMNS = [
    "tweet_id", "created_at", "event_date", "lang", "source",
    "screen_name", "user_name", "followers_count", "verified",
    "favorite_count", "retweet_count", "engagement_total",
    "sentiment", "sentiment_score", "aspect", "aspect_en",
    "product_name", "category", "subcategory", "product_bias", "is_spike",
    "text", "clean_text",
]


def clean_text(s: str) -> str:
    """Strip URLs, @mentions and emojis; drop '#' but keep the tag word."""
    s = RE_URL.sub("", s or "")
    s = RE_MENTION.sub("", s)
    s = RE_EMOJI.sub("", s)
    s = s.replace("#", "")
    return RE_WS.sub(" ", s).strip()


def flatten(rec: dict) -> dict:
    user = rec.get("user") or {}
    meta = rec.get("_meta") or {}
    fav  = int(rec.get("favorite_count") or 0)
    rt   = int(rec.get("retweet_count") or 0)
    sentiment = (meta.get("sentiment") or "").strip().lower()
    aspect    = (meta.get("aspect") or "").strip().lower()
    return {
        "tweet_id":         str(rec.get("id_str") or ""),
        "created_at":       rec.get("created_at"),
        "lang":             rec.get("lang"),
        "source":           rec.get("source"),
        "screen_name":      user.get("screen_name"),
        "user_name":        user.get("name"),
        "followers_count":  int(user.get("followers_count") or 0),
        "verified":         bool(user.get("verified") or False),
        "favorite_count":   fav,
        "retweet_count":    rt,
        "engagement_total": fav + rt,
        "sentiment":        sentiment,
        "sentiment_score":  SENTIMENT_SCORE.get(sentiment, 0),
        "aspect":           aspect,
        "aspect_en":        ASPECT_EN.get(aspect, "General"),
        "product_name":     meta.get("product"),
        "category":         meta.get("category"),
        "subcategory":      meta.get("subcategory"),
        "product_bias":     meta.get("product_bias"),
        "is_spike":         bool(meta.get("spike") or False),
        "text":             rec.get("text"),
        "clean_text":       clean_text(rec.get("text")),
    }


def run() -> dict:
    files = sorted(BRONZE.glob("tweets_*.json")) if BRONZE.exists() else []
    if not files:
        print(f"  [ERROR] no tweet JSON in {BRONZE}", file=sys.stderr)
        sys.exit(1)

    print("[SILVER] bronze/json -> silver/social (structure + analyze tweets)",
          flush=True)
    rows = []
    for fp in files:
        recs = json.loads(fp.read_text(encoding="utf-8"))
        rows.extend(flatten(r) for r in recs)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["tweet_id"]).reset_index(drop=True)

    ts = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df["created_at"] = ts
    df["event_date"] = ts.dt.normalize().dt.tz_localize(None)
    df["followers_count"] = df["followers_count"].astype("Int64")
    df = df[SENTIMENT_COLUMNS]

    SILVER.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SILVER / "sentiment.parquet", index=False)

    pos = int((df["sentiment_score"] == 1).sum())
    neg = int((df["sentiment_score"] == -1).sum())
    neu = int((df["sentiment_score"] == 0).sum())
    print(f"  [OK] sentiment      {len(df):>8,} tweets  "
          f"(pos={pos:,}  neu={neu:,}  neg={neg:,})", flush=True)
    return {"sentiment": len(df)}


if __name__ == "__main__":
    run()
