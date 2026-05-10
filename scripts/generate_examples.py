"""
Generate DSPy training examples from the live DuckDB analytics database.

Each example is a {question, sql_results, doc_context, answer} dict that
represents a real Q&A pair grounded in the actual data. These are used by
compile_dspy.py to run BootstrapFewShot, and also loaded directly at startup
as few-shot demonstrations when compilation has not been run yet.

Usage (run from the axiom-engine root):
    python scripts/generate_examples.py

Output:
    data/dspy_examples.json   (50 examples)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow running from the project root or the scripts directory
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend-python" / "api"))

DUCKDB_PATH = os.getenv("DUCKDB_PATH", str(ROOT / "data" / "duckdb" / "analytics.duckdb"))
OUTPUT_PATH = ROOT / "data" / "dspy_examples.json"


def run(conn, sql: str) -> list[dict]:
    try:
        return conn.execute(sql).df().to_dict(orient="records")
    except Exception:
        return []


def fmt(rows: list[dict]) -> str:
    return json.dumps(rows, default=str)


def build_examples(conn) -> list[dict]:
    examples = []

    # 1. Genre with highest total box office revenue
    rows = run(conn, """
        SELECT genre, SUM(box_office_usd) AS total_revenue
        FROM movies GROUP BY genre ORDER BY total_revenue DESC LIMIT 1
    """)
    if rows:
        g = rows[0]["genre"]
        r = f"${rows[0]['total_revenue']:,.0f}"
        examples.append({
            "question": "Which genre had the highest total box office revenue?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"The {g} genre had the highest total box office revenue at {r}.",
        })

    # 2. Top 3 movies by box office
    rows = run(conn, """
        SELECT title, box_office_usd FROM movies
        ORDER BY box_office_usd DESC LIMIT 3
    """)
    if rows:
        titles = ", ".join(r["title"] for r in rows)
        examples.append({
            "question": "What are the top 3 movies by box office revenue?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"The top 3 movies by box office revenue are {titles}.",
        })

    # 3. Average rating by genre
    rows = run(conn, """
        SELECT genre, ROUND(AVG(rating), 2) AS avg_rating
        FROM movies GROUP BY genre ORDER BY avg_rating DESC
    """)
    if rows:
        summary = "; ".join(f"{r['genre']} ({r['avg_rating']})" for r in rows)
        examples.append({
            "question": "What is the average movie rating per genre?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"Average ratings by genre: {summary}.",
        })

    # 4. Most active viewer subscription tier
    rows = run(conn, """
        SELECT subscription_tier, COUNT(*) AS viewer_count
        FROM viewers GROUP BY subscription_tier ORDER BY viewer_count DESC LIMIT 1
    """)
    if rows:
        tier = rows[0]["subscription_tier"]
        count = rows[0]["viewer_count"]
        examples.append({
            "question": "Which subscription tier has the most viewers?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"The '{tier}' tier has the most viewers with {count:,} subscribers.",
        })

    # 5. Average watch duration by device
    rows = run(conn, """
        SELECT device_type, ROUND(AVG(watch_duration_mins), 1) AS avg_mins
        FROM watch_activity GROUP BY device_type ORDER BY avg_mins DESC
    """)
    if rows:
        top = rows[0]
        examples.append({
            "question": "Which device type has the highest average watch duration?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": (
                f"'{top['device_type']}' users watch the longest on average "
                f"at {top['avg_mins']} minutes per session."
            ),
        })

    # 6. Movies with completion rate above 0.8
    rows = run(conn, """
        SELECT m.title, ROUND(AVG(w.completion_rate), 2) AS avg_completion
        FROM watch_activity w JOIN movies m ON w.movie_id = m.movie_id
        GROUP BY m.title HAVING AVG(w.completion_rate) > 0.8
        ORDER BY avg_completion DESC LIMIT 5
    """)
    if rows:
        titles = ", ".join(r["title"] for r in rows)
        examples.append({
            "question": "Which movies have an average completion rate above 80%?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"Movies with average completion above 80%: {titles}.",
        })

    # 7. Most reviewed genre
    rows = run(conn, """
        SELECT m.genre, COUNT(r.review_id) AS review_count
        FROM reviews r JOIN movies m ON r.movie_id = m.movie_id
        GROUP BY m.genre ORDER BY review_count DESC LIMIT 1
    """)
    if rows:
        examples.append({
            "question": "Which genre has received the most reviews?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": (
                f"The {rows[0]['genre']} genre has received the most reviews "
                f"({rows[0]['review_count']:,} total)."
            ),
        })

    # 8. Average sentiment breakdown
    rows = run(conn, """
        SELECT sentiment, COUNT(*) AS count
        FROM reviews GROUP BY sentiment ORDER BY count DESC
    """)
    if rows:
        total = sum(r["count"] for r in rows)
        summary = "; ".join(
            f"{r['sentiment']} ({r['count']/total*100:.0f}%)" for r in rows
        )
        examples.append({
            "question": "What is the breakdown of review sentiment across all movies?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"Review sentiment breakdown: {summary}.",
        })

    # 9. Top country by regional revenue
    rows = run(conn, """
        SELECT country, SUM(revenue_usd) AS total_revenue
        FROM regional_performance GROUP BY country
        ORDER BY total_revenue DESC LIMIT 3
    """)
    if rows:
        countries = ", ".join(r["country"] for r in rows)
        examples.append({
            "question": "Which countries generate the most regional revenue?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": f"The top countries by regional revenue are {countries}.",
        })

    # 10. Movies with highest ROI
    rows = run(conn, """
        SELECT title, genre,
               ROUND((box_office_usd - budget_usd) * 100.0 / NULLIF(budget_usd, 0), 1) AS roi_pct
        FROM movies ORDER BY roi_pct DESC LIMIT 3
    """)
    if rows:
        top = rows[0]
        examples.append({
            "question": "Which movies had the highest return on investment?",
            "sql_results": fmt(rows),
            "doc_context": "",
            "answer": (
                f"'{top['title']}' had the highest ROI at {top['roi_pct']}%, "
                f"followed by {', '.join(r['title'] for r in rows[1:])}."
            ),
        })

    # 11–15: more genre/viewer/performance questions
    additional_queries = [
        (
            "How many movies were released per genre?",
            "SELECT genre, COUNT(*) AS movie_count FROM movies GROUP BY genre ORDER BY movie_count DESC",
            lambda rows: f"Movie count by genre: " + ", ".join(f"{r['genre']} ({r['movie_count']})" for r in rows) + ".",
        ),
        (
            "What is the average budget for movies by genre?",
            "SELECT genre, ROUND(AVG(budget_usd)/1e6, 1) AS avg_budget_m FROM movies GROUP BY genre ORDER BY avg_budget_m DESC",
            lambda rows: f"Average budgets (millions): " + "; ".join(f"{r['genre']} (${r['avg_budget_m']}M)" for r in rows) + ".",
        ),
        (
            "Which movies have the lowest viewer ratings?",
            "SELECT title, genre, rating FROM movies ORDER BY rating ASC LIMIT 5",
            lambda rows: f"Lowest rated movies: " + ", ".join(f"{r['title']} ({r['rating']})" for r in rows) + ".",
        ),
        (
            "What is the total watch activity by device type?",
            "SELECT device_type, COUNT(*) AS sessions FROM watch_activity GROUP BY device_type ORDER BY sessions DESC",
            lambda rows: f"Watch sessions by device: " + ", ".join(f"{r['device_type']} ({r['sessions']:,})" for r in rows) + ".",
        ),
        (
            "Which genre has the highest average viewer completion rate?",
            "SELECT m.genre, ROUND(AVG(w.completion_rate)*100, 1) AS pct FROM watch_activity w JOIN movies m ON w.movie_id=m.movie_id GROUP BY m.genre ORDER BY pct DESC LIMIT 1",
            lambda rows: f"The {rows[0]['genre']} genre has the highest average completion rate at {rows[0]['pct']}%." if rows else "No data.",
        ),
        (
            "How many unique viewers have watched at least one movie?",
            "SELECT COUNT(DISTINCT viewer_id) AS unique_viewers FROM watch_activity",
            lambda rows: f"{rows[0]['unique_viewers']:,} unique viewers have watched at least one movie." if rows else "No data.",
        ),
        (
            "What is the monthly trend in watch activity?",
            "SELECT STRFTIME(watch_date, '%Y-%m') AS month, COUNT(*) AS sessions FROM watch_activity GROUP BY month ORDER BY month DESC LIMIT 6",
            lambda rows: f"Recent monthly watch sessions: " + ", ".join(f"{r['month']}: {r['sessions']:,}" for r in rows) + ".",
        ),
        (
            "Which preferred genre do viewers most commonly have?",
            "SELECT preferred_genre, COUNT(*) AS count FROM viewers GROUP BY preferred_genre ORDER BY count DESC LIMIT 1",
            lambda rows: f"The most common preferred genre among viewers is {rows[0]['preferred_genre']} ({rows[0]['count']:,} viewers)." if rows else "No data.",
        ),
        (
            "What is the average age of viewers by subscription tier?",
            "SELECT subscription_tier, ROUND(AVG(age), 1) AS avg_age FROM viewers GROUP BY subscription_tier ORDER BY avg_age DESC",
            lambda rows: f"Average age by tier: " + "; ".join(f"{r['subscription_tier']} ({r['avg_age']})" for r in rows) + ".",
        ),
        (
            "Which movies were released in the last year?",
            f"SELECT title, genre, release_date FROM movies WHERE release_year >= 2024 ORDER BY release_date DESC LIMIT 10",
            lambda rows: f"{len(rows)} movies released in 2024 or later, including: " + ", ".join(r['title'] for r in rows[:5]) + ".",
        ),
    ]

    for question, sql, answer_fn in additional_queries:
        rows = run(conn, sql)
        if rows:
            examples.append({
                "question": question,
                "sql_results": fmt(rows),
                "doc_context": "",
                "answer": answer_fn(rows),
            })

    # Pad to 50 with variations if needed
    variations = [
        ("What percentage of movies are rated above 4.0?",
         "SELECT ROUND(COUNT(*) FILTER (WHERE rating > 4.0) * 100.0 / COUNT(*), 1) AS pct FROM movies"),
        ("Which director has directed the most movies?",
         "SELECT director, COUNT(*) AS count FROM movies GROUP BY director ORDER BY count DESC LIMIT 1"),
        ("What is the total revenue across all regions?",
         "SELECT SUM(revenue_usd) AS total FROM regional_performance"),
        ("How many movies are currently in 'released' status?",
         "SELECT COUNT(*) AS count FROM movies WHERE status = 'released'"),
        ("What is the average completion rate across all watch sessions?",
         "SELECT ROUND(AVG(completion_rate)*100, 1) AS avg_pct FROM watch_activity"),
        ("Which month has the highest watch activity?",
         "SELECT STRFTIME(watch_date, '%m') AS month, COUNT(*) AS sessions FROM watch_activity GROUP BY month ORDER BY sessions DESC LIMIT 1"),
        ("What is the ratio of male to female viewers?",
         "SELECT gender, COUNT(*) AS count FROM viewers WHERE gender IN ('M','F') GROUP BY gender"),
        ("Which city has the most regional views?",
         "SELECT city, SUM(views) AS total FROM regional_performance GROUP BY city ORDER BY total DESC LIMIT 1"),
        ("What is the average engagement score by genre?",
         "SELECT m.genre, ROUND(AVG(rp.engagement_score),1) AS avg_score FROM regional_performance rp JOIN movies m ON rp.movie_id=m.movie_id GROUP BY m.genre ORDER BY avg_score DESC"),
        ("How many viewers joined in the last 12 months?",
         "SELECT COUNT(*) AS count FROM viewers WHERE join_date >= '2024-01-01'"),
        ("What is the total number of watch sessions per genre?",
         "SELECT m.genre, COUNT(*) AS sessions FROM watch_activity w JOIN movies m ON w.movie_id=m.movie_id GROUP BY m.genre ORDER BY sessions DESC"),
        ("Which movie has the longest average watch time?",
         "SELECT m.title, ROUND(AVG(w.watch_duration_mins),1) AS avg_mins FROM watch_activity w JOIN movies m ON w.movie_id=m.movie_id GROUP BY m.title ORDER BY avg_mins DESC LIMIT 1"),
        ("What is the distribution of movies by release year?",
         "SELECT release_year, COUNT(*) AS count FROM movies GROUP BY release_year ORDER BY release_year DESC"),
        ("Which genre has the best review sentiment?",
         "SELECT m.genre, COUNT(*) FILTER (WHERE r.sentiment='positive') * 100.0 / COUNT(*) AS positive_pct FROM reviews r JOIN movies m ON r.movie_id=m.movie_id GROUP BY m.genre ORDER BY positive_pct DESC LIMIT 1"),
        ("What is the most common country for regional performance data?",
         "SELECT country, COUNT(*) AS entries FROM regional_performance GROUP BY country ORDER BY entries DESC LIMIT 1"),
        ("How does the average rating compare between Comedy and Thriller?",
         "SELECT genre, ROUND(AVG(rating),2) AS avg FROM movies WHERE genre IN ('Comedy','Thriller') GROUP BY genre"),
        ("Which subscription tier has the highest average age?",
         "SELECT subscription_tier, ROUND(AVG(age),1) AS avg_age FROM viewers GROUP BY subscription_tier ORDER BY avg_age DESC LIMIT 1"),
        ("What is the total number of reviews with negative sentiment?",
         "SELECT COUNT(*) AS count FROM reviews WHERE sentiment='negative'"),
        ("Which movie has the most watch sessions?",
         "SELECT m.title, COUNT(*) AS sessions FROM watch_activity w JOIN movies m ON w.movie_id=m.movie_id GROUP BY m.title ORDER BY sessions DESC LIMIT 1"),
        ("What is the average runtime of movies by genre?",
         "SELECT genre, ROUND(AVG(runtime_mins),0) AS avg_runtime FROM movies GROUP BY genre ORDER BY avg_runtime DESC"),
    ]

    for question, sql in variations:
        if len(examples) >= 50:
            break
        rows = run(conn, sql)
        if rows:
            examples.append({
                "question": question,
                "sql_results": fmt(rows),
                "doc_context": "",
                "answer": f"Based on the data: {fmt(rows[:3])}",
            })

    return examples[:50]


def main() -> None:
    try:
        import duckdb
    except ImportError:
        print("duckdb not installed. Run: pip install duckdb")
        sys.exit(1)

    if not Path(DUCKDB_PATH).exists():
        print(f"DuckDB file not found at {DUCKDB_PATH}")
        print("Run the stack first to auto-load CSV data, then re-run this script.")
        sys.exit(1)

    conn = duckdb.connect(DUCKDB_PATH, read_only=True)

    print(f"Connected to {DUCKDB_PATH}")
    examples = build_examples(conn)
    conn.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as fh:
        json.dump(examples, fh, indent=2)

    print(f"Generated {len(examples)} examples -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
