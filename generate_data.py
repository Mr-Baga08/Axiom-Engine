import csv
import random
import uuid
from datetime import datetime, timedelta
import os

# Ensure target directory exists
OUTPUT_DIR = "data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def generate_synthetic_data():
    print("Generating synthetic enterprise data...")

    # 1. Generate Movies
    movies = []
    genres = ['Sci-Fi', 'Fantasy', 'Action', 'Drama', 'Comedy']
    for i in range(1, 51):
        movies.append({
            'movie_id': f"MOV-{i:03d}",
            'title': f"Movie Title {i}",
            'genre': random.choice(genres),
            'release_year': random.randint(2020, 2024),
            'production_budget': random.randint(10, 150) * 1000000
        })

    with open(f"{OUTPUT_DIR}/movies.csv", 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=movies[0].keys())
        writer.writeheader()
        writer.writerows(movies)
    print(f"Created movies.csv ({len(movies)} rows)")

    # 2. Generate Viewers
    viewers = []
    regions = ['NA', 'EU', 'APAC', 'LATAM']
    for i in range(1, 501):
        viewers.append({
            'viewer_id': str(uuid.uuid4())[:8],
            'region': random.choice(regions),
            'subscription_tier': random.choices(['Basic', 'Premium', 'Ultra'], weights=[50, 35, 15])[0],
            'join_date': (datetime.now() - timedelta(days=random.randint(10, 1000))).strftime('%Y-%m-%d')
        })

    with open(f"{OUTPUT_DIR}/viewers.csv", 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=viewers[0].keys())
        writer.writeheader()
        writer.writerows(viewers)
    print(f"Created viewers.csv ({len(viewers)} rows)")

    # 3. Generate Watch Activity (Time-Series)
    activity = []
    for _ in range(5000):
        viewer = random.choice(viewers)
        movie = random.choice(movies)
        watch_date = datetime.now() - timedelta(days=random.randint(1, 365))
        
        activity.append({
            'activity_id': str(uuid.uuid4()),
            'viewer_id': viewer['viewer_id'],
            'movie_id': movie['movie_id'],
            'watch_date': watch_date.strftime('%Y-%m-%d %H:%M:%S'),
            'minutes_watched': random.randint(5, 180),
            'completed': random.choice([True, False])
        })

    with open(f"{OUTPUT_DIR}/watch_activity.csv", 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=activity[0].keys())
        writer.writeheader()
        writer.writerows(activity)
    print(f"Created watch_activity.csv ({len(activity)} rows)")

if __name__ == "__main__":
    generate_synthetic_data()