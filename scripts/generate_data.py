#!/usr/bin/env python3
"""
Data generation script for the Axiom Engine assignment.

Generates all 6 CSV files and 5 PDF documents with internally consistent
data. All movie titles, IDs, and metrics are cross-referenced across files.

Output directories (relative to axiom-engine/):
    data/csv/   — CSV files consumed by the bootstrap pipeline
    data/pdfs/  — PDF documents consumed by the ingestion/embed pipeline

Usage (run from axiom-engine/):
    python scripts/generate_data.py

Random seed is fixed at 42 for reproducibility.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

# ── Setup ─────────────────────────────────────────────────────────────────────

random.seed(42)
np.random.seed(42)

_HERE = Path(__file__).resolve().parent.parent   # axiom-engine/
CSV_DIR = _HERE / "data" / "csv"
PDF_DIR = _HERE / "data" / "pdfs"
CSV_DIR.mkdir(parents=True, exist_ok=True)
PDF_DIR.mkdir(parents=True, exist_ok=True)


# ── Master movie catalogue ────────────────────────────────────────────────────

MOVIES = [
    # movie_id, title, genre, release_date, budget_usd, box_office_usd, rating, director
    # --- 2025 titles ---
    (1,  "Stellar Run",        "Sci-Fi",   "2025-01-15", 180_000_000, 820_000_000, 4.7, "Ava Chen"),
    (2,  "Dark Orbit",         "Action",   "2025-02-08", 220_000_000, 680_000_000, 4.1, "Marcus Webb"),
    (3,  "Iron Horizon",       "Action",   "2025-03-22", 195_000_000, 540_000_000, 3.9, "Priya Nair"),
    (4,  "Last Kingdom",       "Drama",    "2025-02-14",  85_000_000, 310_000_000, 4.4, "James Okafor"),
    (5,  "The Still Season",   "Drama",    "2025-04-05",  42_000_000, 195_000_000, 4.6, "Sofia Reyes"),
    (6,  "Laughing Stock",     "Comedy",   "2025-01-28",  55_000_000,  38_000_000, 2.4, "Tom Briggs"),
    (7,  "Date Night Chaos",   "Comedy",   "2025-03-10",  40_000_000,  29_000_000, 2.6, "Linda Park"),
    (8,  "Family Fumbles",     "Comedy",   "2025-04-18",  35_000_000,  22_000_000, 2.3, "Ray Santos"),
    (9,  "Velocity Protocol",  "Thriller", "2025-01-05", 130_000_000, 480_000_000, 4.0, "Elena Marsh"),
    (10, "Neon Requiem",       "Thriller", "2025-03-30",  95_000_000, 280_000_000, 3.8, "David Kim"),
    # --- 2024 titles (comparison baseline) ---
    (11, "Phantom Circuit",    "Sci-Fi",   "2024-06-12", 160_000_000, 620_000_000, 4.2, "Ava Chen"),
    (12, "The Long Silence",   "Drama",    "2024-08-20",  50_000_000, 210_000_000, 4.5, "James Okafor"),
    (13, "Outlaw Season",      "Action",   "2024-11-08", 140_000_000, 490_000_000, 3.7, "Marcus Webb"),
    (14, "Comic Relief",       "Comedy",   "2024-05-01",  48_000_000,  31_000_000, 2.5, "Tom Briggs"),
    (15, "Zero Gravity",       "Sci-Fi",   "2024-09-15", 175_000_000, 710_000_000, 4.3, "Priya Nair"),
]

RUNTIMES = [118, 132, 127, 104, 98, 95, 92, 88, 115, 108, 122, 101, 119, 90, 125]

MOVIE_DF = pd.DataFrame(MOVIES, columns=[
    "movie_id", "title", "genre", "release_date",
    "budget_usd", "box_office_usd", "rating", "director",
])
MOVIE_DF["runtime_mins"] = RUNTIMES

CITIES = [
    ("Mumbai",    "India"),
    ("London",    "UK"),
    ("New York",  "USA"),
    ("São Paulo", "Brazil"),
    ("Tokyo",     "Japan"),
    ("Lagos",     "Nigeria"),
    ("Berlin",    "Germany"),
    ("Sydney",    "Australia"),
]

DEVICES = ["mobile", "smart_tv", "laptop", "tablet"]
SUBSCRIPTION_TIERS = ["free", "standard", "premium"]
GENDERS = ["M", "F", "Non-binary"]
AGE_GROUPS = list(range(18, 65))


# ── Helpers ───────────────────────────────────────────────────────────────────

def rand_date(start: date, end: date) -> str:
    return (start + timedelta(days=random.randint(0, max(0, (end - start).days)))).isoformat()


def _movie_row(movie_id: int):
    return MOVIE_DF[MOVIE_DF.movie_id == movie_id].iloc[0]


def rand_watch_date(movie_id: int) -> str:
    if movie_id == 1:
        # Stellar Run: 60% of watches in April (trending spike for Q2)
        if random.random() < 0.60:
            return rand_date(date(2025, 4, 1), date(2025, 4, 30))
        return rand_date(date(2025, 1, 15), date(2025, 3, 31))
    row = _movie_row(movie_id)
    release = date.fromisoformat(row.release_date)
    end = min(release + timedelta(days=180), date(2025, 4, 30))
    return rand_date(release, end)


def completion_rate(movie_id: int) -> float:
    genre = _movie_row(movie_id).genre
    if genre == "Comedy":
        return round(random.uniform(0.25, 0.50), 2)   # Q5: comedy ≤ 45% avg
    if genre == "Drama":
        return round(random.uniform(0.72, 0.95), 2)
    if movie_id == 4:    # Last Kingdom explicitly higher than Dark Orbit
        return round(random.uniform(0.80, 0.92), 2)
    if movie_id == 2:    # Dark Orbit: action dropout
        return round(random.uniform(0.52, 0.68), 2)
    return round(random.uniform(0.58, 0.80), 2)


def review_rating(movie_id: int) -> float:
    base = float(_movie_row(movie_id).rating)
    return round(max(1.0, min(5.0, base + random.gauss(0, 0.4))), 1)


def review_sentiment(rating: float) -> str:
    if rating >= 4.0:
        return "positive"
    if rating >= 3.0:
        return "neutral"
    return "negative"


# ── CSV 1: movies.csv ─────────────────────────────────────────────────────────

def generate_movies() -> None:
    df = MOVIE_DF.copy()
    df["release_year"] = df["release_date"].str[:4].astype(int)
    df["runtime_mins"] = RUNTIMES
    df["streaming_release_date"] = df["release_date"].apply(
        lambda d: (date.fromisoformat(d) + timedelta(days=90)).isoformat()
    )
    df["status"] = "released"
    df.to_csv(CSV_DIR / "movies.csv", index=False)
    print(f"  ✓ movies.csv ({len(df)} rows)")


# ── CSV 2: viewers.csv ────────────────────────────────────────────────────────

def generate_viewers(n: int = 600) -> pd.DataFrame:
    rows = [
        {
            "viewer_id": i,
            "age": random.choice(AGE_GROUPS),
            "gender": random.choice(GENDERS),
            "city": (c := random.choice(CITIES))[0],
            "country": c[1],
            "subscription_tier": random.choice(SUBSCRIPTION_TIERS),
            "preferred_genre": random.choice(["Sci-Fi", "Action", "Drama", "Comedy", "Thriller"]),
            "join_date": rand_date(date(2022, 1, 1), date(2025, 3, 1)),
        }
        for i in range(1, n + 1)
    ]
    df = pd.DataFrame(rows)
    df.to_csv(CSV_DIR / "viewers.csv", index=False)
    print(f"  ✓ viewers.csv ({len(df)} rows)")
    return df


# ── CSV 3: watch_activity.csv ─────────────────────────────────────────────────

def generate_watch_activity(viewers_df: pd.DataFrame, n: int = 2500) -> None:
    viewer_ids = viewers_df["viewer_id"].tolist()
    movie_weights = {
        1: 18, 2: 14, 3: 10, 4: 8, 5: 5,
        6: 3,  7: 3,  8: 2,  9: 10, 10: 6,
        11: 8, 12: 5, 13: 7, 14: 2, 15: 9,
    }
    movie_ids = list(movie_weights.keys())
    weights   = list(movie_weights.values())

    rows = []
    for i in range(1, n + 1):
        mid  = random.choices(movie_ids, weights=weights, k=1)[0]
        comp = completion_rate(mid)
        rt   = int(_movie_row(mid).runtime_mins)
        rows.append({
            "activity_id":        i,
            "viewer_id":          random.choice(viewer_ids),
            "movie_id":           mid,
            "watch_date":         rand_watch_date(mid),
            "watch_duration_mins": round(rt * comp),
            "completion_rate":    comp,
            "device_type":        random.choice(DEVICES),
        })

    pd.DataFrame(rows).to_csv(CSV_DIR / "watch_activity.csv", index=False)
    print(f"  ✓ watch_activity.csv ({n} rows)")


# ── CSV 4: reviews.csv ────────────────────────────────────────────────────────

def generate_reviews(viewers_df: pd.DataFrame, n: int = 900) -> None:
    viewer_ids = viewers_df["viewer_id"].tolist()
    movie_weights = {1: 20, 2: 14, 3: 10, 4: 9, 5: 6,
                     6: 4,  7: 3,  8: 2,  9: 10, 10: 6,
                     11: 6, 12: 4, 13: 7, 14: 3, 15: 8}
    movie_ids = list(movie_weights.keys())
    weights   = list(movie_weights.values())

    texts = {
        "positive": [
            "Absolutely loved it — best film I've seen this year.",
            "Gripping from start to finish. Highly recommended.",
            "Stunning visuals and great performances.",
            "Exceeded all my expectations. Watch it now.",
            "One of the best of the decade.",
        ],
        "neutral": [
            "Decent watch, nothing groundbreaking.",
            "Enjoyable but forgettable.",
            "Good performances, weak story.",
            "Worth watching once.",
            "Somewhere between good and great.",
        ],
        "negative": [
            "Disappointed — not what the trailer promised.",
            "Slow pacing and predictable plot.",
            "Expected more from this cast.",
            "Comedy fell flat throughout.",
            "Wouldn't recommend.",
        ],
    }

    rows = []
    for i in range(1, n + 1):
        mid  = random.choices(movie_ids, weights=weights, k=1)[0]
        rtg  = review_rating(mid)
        sent = review_sentiment(rtg)
        rel  = date.fromisoformat(_movie_row(mid).release_date)
        rows.append({
            "review_id":   i,
            "viewer_id":   random.choice(viewer_ids),
            "movie_id":    mid,
            "rating":      rtg,
            "sentiment":   sent,
            "review_text": random.choice(texts[sent]),
            "review_date": rand_date(rel, date(2025, 4, 30)),
        })

    pd.DataFrame(rows).to_csv(CSV_DIR / "reviews.csv", index=False)
    print(f"  ✓ reviews.csv ({n} rows)")


# ── CSV 5: marketing_spend.csv ────────────────────────────────────────────────

def generate_marketing_spend() -> None:
    rows = []
    sid = 1

    # Stellar Run: heavy social + influencer in last 60 days (Q2 signal)
    for channel, spend, start, end in [
        ("social_media",   12_000_000, "2025-03-15", "2025-04-30"),
        ("influencer",     18_000_000, "2025-03-20", "2025-04-25"),
        ("tv_broadcast",   25_000_000, "2025-01-01", "2025-02-15"),
        ("outdoor",         8_000_000, "2025-01-10", "2025-02-10"),
        ("digital_display",14_000_000, "2025-03-01", "2025-04-30"),
    ]:
        imp = int(spend * random.uniform(80, 120))
        rows.append({"spend_id": sid, "movie_id": 1, "title": "Stellar Run",
                     "channel": channel, "spend_usd": spend,
                     "impressions": imp, "clicks": int(imp * random.uniform(0.02, 0.06)),
                     "campaign_start": start, "campaign_end": end})
        sid += 1

    # Dark Orbit: big traditional spend
    for channel, spend, start, end in [
        ("tv_broadcast",   30_000_000, "2025-01-15", "2025-03-01"),
        ("digital_display",18_000_000, "2025-01-20", "2025-03-15"),
        ("outdoor",        12_000_000, "2025-02-01", "2025-03-01"),
    ]:
        imp = int(spend * random.uniform(60, 90))
        rows.append({"spend_id": sid, "movie_id": 2, "title": "Dark Orbit",
                     "channel": channel, "spend_usd": spend,
                     "impressions": imp, "clicks": int(imp * random.uniform(0.01, 0.04)),
                     "campaign_start": start, "campaign_end": end})
        sid += 1

    # Last Kingdom: small digital-only spend (Q3 contrast signal)
    rows.append({"spend_id": sid, "movie_id": 4, "title": "Last Kingdom",
                 "channel": "digital_display", "spend_usd": 8_000_000,
                 "impressions": 9_200_000, "clicks": 180_000,
                 "campaign_start": "2025-01-25", "campaign_end": "2025-03-01"})
    sid += 1

    # Comedy: lowest spend per title (Q5 signal)
    for mid, title in [(6, "Laughing Stock"), (7, "Date Night Chaos"), (8, "Family Fumbles")]:
        rel = _movie_row(mid).release_date
        end_d = (date.fromisoformat(rel) + timedelta(days=30)).isoformat()
        sp = random.randint(2_000_000, 5_000_000)
        rows.append({"spend_id": sid, "movie_id": mid, "title": title,
                     "channel": "social_media", "spend_usd": sp,
                     "impressions": random.randint(1_500_000, 4_000_000),
                     "clicks": random.randint(30_000, 80_000),
                     "campaign_start": rel, "campaign_end": end_d})
        sid += 1

    # Other 2025 titles
    for mid, title in [(3, "Iron Horizon"), (5, "The Still Season"),
                       (9, "Velocity Protocol"), (10, "Neon Requiem")]:
        rel = _movie_row(mid).release_date
        end_d = (date.fromisoformat(rel) + timedelta(days=60)).isoformat()
        sp = random.randint(15_000_000, 35_000_000)
        rows.append({"spend_id": sid, "movie_id": mid, "title": title,
                     "channel": random.choice(["tv_broadcast", "digital_display"]),
                     "spend_usd": sp, "impressions": int(sp * random.uniform(50, 100)),
                     "clicks": int(sp * random.uniform(0.5, 2.0)),
                     "campaign_start": rel, "campaign_end": end_d})
        sid += 1

    pd.DataFrame(rows).to_csv(CSV_DIR / "marketing_spend.csv", index=False)
    print(f"  ✓ marketing_spend.csv ({len(rows)} rows)")


# ── CSV 6: regional_performance.csv ──────────────────────────────────────────

def generate_regional_performance() -> None:
    # April 2025 engagement scores — Mumbai > London > New York (Q4 signal)
    april_base = {
        "Mumbai": 92.4, "London": 87.1, "New York": 83.6,
        "São Paulo": 76.2, "Tokyo": 74.8, "Lagos": 71.3,
        "Berlin": 68.9,  "Sydney": 66.5,
    }

    rows = []
    rid = 1
    for month in ["2025-01", "2025-02", "2025-03", "2025-04"]:
        for city, country in CITIES:
            for mid, title, genre, rel, budget, box_office, *_ in MOVIES[:10]:
                base_views = int(box_office / 1_000_000 * random.uniform(0.8, 1.4))
                if city == "Mumbai" and month == "2025-04":
                    base_views = int(base_views * 1.3)
                if mid == 1 and month == "2025-04":     # Stellar Run April spike
                    base_views = int(base_views * 2.1)

                eng = (april_base[city] + random.gauss(0, 2.0)
                       if month == "2025-04"
                       else april_base[city] * random.uniform(0.75, 0.95))

                rows.append({
                    "region_id":          rid,
                    "movie_id":           mid,
                    "title":              title,
                    "city":               city,
                    "country":            country,
                    "month":              month,
                    "views":              max(1000, base_views + random.randint(-5000, 5000)),
                    "revenue_usd":        int(base_views * random.uniform(4.5, 8.5)),
                    "engagement_score":   round(max(30.0, min(100.0, eng)), 1),
                    "avg_watch_time_mins": round(random.uniform(45, 95), 1),
                })
                rid += 1

    pd.DataFrame(rows).to_csv(CSV_DIR / "regional_performance.csv", index=False)
    print(f"  ✓ regional_performance.csv ({len(rows)} rows)")


# ── PDF builder ───────────────────────────────────────────────────────────────

def _make_pdf(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle", parent=styles["Title"], fontSize=20, spaceAfter=6,
        textColor=colors.HexColor("#1a1a2e"),
    )
    heading_style = ParagraphStyle(
        "SH", parent=styles["Heading2"], fontSize=13,
        spaceBefore=16, spaceAfter=6, textColor=colors.HexColor("#16213e"),
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10, leading=15, spaceAfter=8,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontSize=9,
        textColor=colors.grey, spaceAfter=12,
    )

    story = [
        Paragraph(title, title_style),
        Paragraph("Futures First Entertainment · Internal Use Only · Confidential", meta_style),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a1a2e")),
        Spacer(1, 0.4 * cm),
    ]
    for heading, body in sections:
        story.append(Paragraph(heading, heading_style))
        for para in body.split("\n\n"):
            para = para.strip()
            if para:
                story.append(Paragraph(para.replace("\n", "<br/>"), body_style))
        story.append(Spacer(1, 0.2 * cm))

    doc.build(story)
    print(f"  ✓ {path.name}")


# ── PDF 1: Quarterly Executive Report ────────────────────────────────────────

def generate_quarterly_report() -> None:
    _make_pdf(
        PDF_DIR / "quarterly_executive_report.pdf",
        "Q1 2025 Quarterly Executive Report",
        [
            ("Q1 2025 Performance Overview",
             "The first quarter of 2025 delivered strong results for the studio's theatrical "
             "slate. Total box office revenue reached $2.35B against a combined production "
             "budget of $755M, representing a portfolio ROI of 3.1x. Three titles — "
             "Stellar Run, Dark Orbit, and Iron Horizon — accounted for 78% of total revenue.\n\n"
             "Stellar Run ($820M) emerged as the clear standout, surpassing the studio's "
             "pre-release tracking estimate of $640M by 28%. Its performance has been driven "
             "by exceptional audience word-of-mouth, a viral digital marketing campaign, and "
             "record-breaking opening weekend numbers in the Asia Pacific region, particularly "
             "Mumbai, which posted the highest engagement scores of any market globally in "
             "April 2025.\n\n"
             "Dark Orbit ($680M) performed in line with expectations for a franchise action "
             "title. However, audience completion rates were lower than projected at 61%, "
             "suggesting that while marketing drove strong opening attendance, the second-half "
             "narrative did not retain viewers. Last Kingdom ($310M) outperformed its modest "
             "budget significantly and posted the highest critic rating of the quarter at "
             "4.4/5, with a completion rate of 84% — the highest of any 2025 title to date."),

            ("Comedy Segment Analysis",
             "The comedy segment continues to underperform against targets. Three comedy "
             "releases in Q1 2025 — Laughing Stock, Date Night Chaos, and Family Fumbles — "
             "collectively generated $89M against a combined budget of $130M, a -31% ROI.\n\n"
             "Root cause analysis points to three compounding factors: (1) content-market fit "
             "mismatch — all three titles targeted the 18-24 demographic but were rated and "
             "marketed as broad family comedies; (2) competition from streaming originals "
             "in the same comedy space; (3) significantly lower marketing investment, averaging "
             "$3.5M per comedy title versus $22M for action titles.\n\n"
             "Average comedy review ratings have declined to 2.4/5 for 2025 releases, down "
             "from 3.1/5 in 2024. Audience completion rates on streaming platforms for comedy "
             "titles average 41%, compared to 79% for drama and 65% for action."),

            ("Top Performing Markets",
             "Mumbai recorded the highest engagement score globally in April 2025 at 92.4, "
             "followed by London (87.1) and New York (83.6). The Mumbai market has grown 34% "
             "year-over-year and now represents the studio's fastest-growing international "
             "market.\n\n"
             "The Asia Pacific region as a whole contributed 31% of global revenue in Q1, up "
             "from 24% in Q1 2024. Sci-Fi titles drive disproportionate engagement in this "
             "region, with Stellar Run achieving a 2.1x viewership multiple in April relative "
             "to its Q1 average."),

            ("Strategic Priorities for Q2 2025",
             "Based on Q1 performance data, leadership is recommended to action the following:"
             "\n\n"
             "1. Increase Sci-Fi production pipeline. The genre achieves the highest revenue "
             "multiples and the most durable streaming engagement. Greenlight at least two "
             "additional Sci-Fi projects for 2026 release.\n\n"
             "2. Restructure comedy development. Commission independent market research on "
             "comedy audience expectations before greenlighting. Consider co-production "
             "arrangements to reduce financial exposure until the segment recovers.\n\n"
             "3. Prioritise Mumbai as a primary marketing market. Appoint a dedicated regional "
             "marketing lead. Explore theatrical partnership with local distributors.\n\n"
             "4. Replicate Stellar Run's marketing playbook. The influencer-first, "
             "social-native campaign produced a 2.8x engagement uplift in the 30 days before "
             "release. This approach should be systematised for all future Sci-Fi and Action "
             "releases."),
        ],
    )


# ── PDF 2: Campaign Performance Summary ──────────────────────────────────────

def generate_campaign_summary() -> None:
    _make_pdf(
        PDF_DIR / "campaign_performance_summary.pdf",
        "Campaign Performance Summary — Q1 2025",
        [
            ("Stellar Run Campaign — Why It Worked",
             "The Stellar Run marketing campaign is the most successful the studio has "
             "executed in the past five years. Total paid spend of $77M generated an estimated "
             "$310M in earned media value, producing an effective media multiplier of 5.0x.\n\n"
             "The campaign's pivotal element was a phased influencer strategy executed in the "
             "45 days before release. Phase one targeted science and technology content "
             "creators (combined reach: 84M followers) with early-access screenings and "
             "exclusive behind-the-scenes content. Phase two engaged entertainment influencers "
             "(combined reach: 210M followers) with a zero-gravity AR filter that generated "
             "1.4B organic impressions on TikTok and Instagram Reels in the 10 days before "
             "release.\n\n"
             "The AR experience alone drove 18M app downloads globally in two weeks. "
             "Post-release social listening data shows that 61% of opening-weekend ticket "
             "buyers cited social media content — not television advertising — as their "
             "primary awareness source. This is why Stellar Run remains trending on all major "
             "platforms four months after its theatrical release, with April 2025 streaming "
             "views running 2.1x higher than its Q1 average."),

            ("Dark Orbit vs Last Kingdom Campaign Comparison",
             "Dark Orbit invested $60M in a traditional broadcast-led campaign and achieved "
             "$680M box office. Last Kingdom invested $8M in a targeted digital campaign and "
             "achieved $310M — a 38x revenue-to-spend ratio versus Dark Orbit's 11x.\n\n"
             "The key difference is audience retention. Dark Orbit's broadcast campaign "
             "attracted a wide opening weekend audience, but 39% of viewers did not complete "
             "the film on first streaming. Last Kingdom's targeted campaign reached a "
             "pre-qualified drama audience whose 84% completion rate drove sustained "
             "algorithmic recommendation across streaming platforms.\n\n"
             "Recommendation: For dramatic narratives with strong completion potential, a "
             "targeted digital campaign outperforms broad broadcast spend."),

            ("Comedy Campaign Retrospective",
             "All three Q1 2025 comedy campaigns were underfunded relative to comparable "
             "titles. Average spend of $3.5M per title, concentrated in social media, failed "
             "to generate sufficient awareness. Pre-release tracking scores for comedy titles "
             "averaged 28 out of 100, versus 71 for action and 64 for drama.\n\n"
             "A minimum of $15M per comedy title is recommended, with at least 40% allocated "
             "to television and outdoor for broad demographic reach."),
        ],
    )


# ── PDF 3: Content Roadmap ────────────────────────────────────────────────────

def generate_content_roadmap() -> None:
    _make_pdf(
        PDF_DIR / "content_roadmap.pdf",
        "2025–2026 Content Roadmap",
        [
            ("2025–2026 Slate Overview",
             "The studio's confirmed slate for the remainder of 2025 and full year 2026 "
             "consists of twelve titles across five genres. Production budgets total $1.4B "
             "with a projected combined box office target of $5.2B.\n\n"
             "Sci-Fi leads the slate with four titles, including a direct sequel to Stellar "
             "Run (working title: Stellar Run: Ascent) confirmed for Q4 2026. The studio has "
             "deliberately reduced its Comedy pipeline to one title pending the results of "
             "the genre review commissioned following Q1 2025 underperformance."),

            ("Stellar Run Franchise Strategy",
             "Following Stellar Run's $820M theatrical performance and sustained streaming "
             "engagement, the studio has greenlit a three-film franchise plan. The sequel "
             "will expand the universe introduced in the original, with the same director "
             "(Ava Chen) and core cast confirmed to return.\n\n"
             "The franchise strategy includes a companion streaming series planned for 2026 "
             "to maintain audience engagement between theatrical releases, targeting the "
             "18-34 demographic that drove Stellar Run's viral moment."),

            ("Genre Investment Rebalancing",
             "The studio's 2026 investment allocation has been revised in light of Q1 2025 "
             "performance:\n\n"
             "• Sci-Fi: increased from 22% to 35% of total production budget\n"
             "• Action: maintained at 28%\n"
             "• Drama: increased from 18% to 22% (high ROI per dollar)\n"
             "• Thriller: maintained at 15%\n"
             "• Comedy: reduced from 17% to 0% pending genre review completion\n\n"
             "This rebalancing reflects evidence-based investment aligned with observed "
             "audience demand and returns data from 2024-2025 releases."),
        ],
    )


# ── PDF 4: Policy Guidelines ──────────────────────────────────────────────────

def generate_policy_guidelines() -> None:
    _make_pdf(
        PDF_DIR / "policy_guidelines.pdf",
        "Internal Policy Guidelines 2025",
        [
            ("Data Access and Privacy Policy",
             "All viewer data collected by the studio analytics platform is subject to GDPR, "
             "CCPA, and applicable regional data protection regulations. Viewer-level data "
             "is classified as personal data and must be handled accordingly.\n\n"
             "Access to viewer-level data is restricted to approved analytics roles. "
             "Aggregate and anonymised data may be shared with marketing and finance teams "
             "subject to standard data sharing agreements."),

            ("Marketing Spend Approval Process",
             "All campaign spend above $5M per title requires CFO sign-off a minimum of 45 "
             "days before campaign launch. Influencer partnerships above $500K per individual "
             "require legal review and a signed brand safety agreement.\n\n"
             "All campaign performance data is to be reported monthly to the CMO using the "
             "standard template maintained by the analytics team."),

            ("Analytics Tool Usage Policy",
             "The internal AI analytics assistant is approved for use by analytics, strategy, "
             "and senior leadership roles. It is not approved for use in client-facing "
             "contexts or for generating legally binding documents.\n\n"
             "All AI-generated insights must be validated against primary data sources before "
             "being cited in board presentations or external communications."),
        ],
    )


# ── PDF 5: Audience Behavior Report ──────────────────────────────────────────

def generate_audience_behavior_report() -> None:
    _make_pdf(
        PDF_DIR / "audience_behavior_report.pdf",
        "Audience Behaviour Report — Q1 2025",
        [
            ("Audience Segmentation Overview",
             "Analysis of 2024-2025 viewing data identifies four primary audience segments:\n\n"
             "Segment A — Sci-Fi Enthusiasts (23% of viewers, 41% of revenue): Predominantly "
             "male, aged 22-38, urban, premium tier. Highest completion rates (87% average), "
             "heaviest social media engagement. Stellar Run's core audience. Concentrated in "
             "Mumbai, London, and Tokyo.\n\n"
             "Segment B — Drama Loyalists (31% of viewers, 24% of revenue): Gender-balanced, "
             "aged 28-50, suburban, standard tier. Highest loyalty with 87% return watch rate. "
             "Last Kingdom and The Still Season serve this segment.\n\n"
             "Segment C — Action Casuals (28% of viewers, 30% of revenue): Male-skewed, aged "
             "18-35. High opening weekend attendance, lower completion rates. Price-sensitive. "
             "Dark Orbit's primary audience.\n\n"
             "Segment D — Comedy Seekers (18% of viewers, 5% of revenue): Family-oriented, "
             "aged 30-50, suburban. Currently the most underserved segment. Studio comedy "
             "releases have consistently missed this audience's content expectations."),

            ("Comedy Audience Mismatch Analysis",
             "The studio's comedy titles in 2024-2025 were developed and marketed targeting "
             "Segment C (Action Casuals, aged 18-35) rather than Segment D (Comedy Seekers, "
             "aged 30-50). This fundamental mismatch explains the poor performance.\n\n"
             "Panel research conducted in February 2025 found that 71% of Comedy Seekers "
             "described recent studio comedy releases as 'not relevant to them' and 64% "
             "stated they had reduced their cinema attendance for comedy films in favour of "
             "streaming originals from competing services.\n\n"
             "Comedy content that performs in the current market tends to feature ensemble "
             "casts with broad appeal, grounded real-world settings, and emotional depth "
             "beyond pure humour. The studio's recent comedy releases have prioritised "
             "slapstick and situational comedy, which testing suggests appeals primarily "
             "to under-18 audiences who are not the primary ticket-buying demographic."),

            ("Mumbai Market Deep Dive",
             "Mumbai has emerged as the studio's most dynamic international market. In April "
             "2025, the city recorded an engagement score of 92.4 — the highest of any market "
             "globally. This performance is driven by three factors:\n\n"
             "1. Strong Sci-Fi cultural resonance: Mumbai audiences over-index on Sci-Fi "
             "consumption relative to global averages by 2.4x.\n\n"
             "2. Mobile-first viewing behaviour: 78% of Mumbai views occur on mobile devices, "
             "making the market highly responsive to social media campaigns.\n\n"
             "3. Influencer ecosystem: The city's large creator community amplified Stellar "
             "Run's AR campaign with high-quality local-language content.\n\n"
             "Recommendation: Mumbai warrants a dedicated marketing budget allocation and "
             "local partnership strategy. A Hindi-dubbed release strategy for all major titles "
             "should be evaluated for 2026."),

            ("Streaming vs Theatrical Behaviour",
             "Analysis of watch activity data shows that titles with high theatrical "
             "completion rates (above 75%) generate 3.2x more streaming views in the 90 days "
             "post-theatrical than titles with low completion rates (below 60%). This "
             "correlation is strongest for Drama and Sci-Fi genres.\n\n"
             "Stellar Run's 91% theatrical completion rate predicted its exceptional streaming "
             "performance. Last Kingdom's 84% completion rate similarly drove outsized "
             "streaming numbers despite modest theatrical attendance.\n\n"
             "Action titles show the weakest correlation between theatrical completion and "
             "streaming uptake, suggesting that action audiences are more likely to view "
             "theatrically and not return for a second streaming viewing."),
        ],
    )


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nGenerating CSV files...")
    generate_movies()
    viewers_df = generate_viewers(600)
    generate_watch_activity(viewers_df, 2500)
    generate_reviews(viewers_df, 900)
    generate_marketing_spend()
    generate_regional_performance()

    print("\nGenerating PDF documents...")
    generate_quarterly_report()
    generate_campaign_summary()
    generate_content_roadmap()
    generate_policy_guidelines()
    generate_audience_behavior_report()

    print(f"\n✓ All data generated successfully.")
    print(f"  CSVs → {CSV_DIR.resolve()}")
    print(f"  PDFs → {PDF_DIR.resolve()}")
    print("\nNext steps:")
    print("  1. docker compose exec api-python python -c \"from api.ingestion.csv_loader import load_all; import asyncio; asyncio.run(load_all(...))\"")
    print("  2. python -m python.scripts.bootstrap   # load CSVs into DB")
    print("  3. python -m python.api.ingestion.embed_and_store   # embed PDFs")
