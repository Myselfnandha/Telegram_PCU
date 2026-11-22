import json
import random
import time
import os

# Use fixed seed for reproducibility
random.seed(42)

def generate_media_entry(idx):
    return {
        "message_id": 10000 + idx,
        "chat_id": "test_chat",
        "filename": f"media_file_{idx}.mkv",
        "file_size": random.randint(100*1024*1024, 2000*1024*1024),
        "size": random.randint(100*1024*1024, 2000*1024*1024),
        "duration": random.randint(300, 7200),
        "resolution": random.choice(["720p", "1080p", "4k"]),
        "date": int(time.time() - idx * 3600),
        "is_series": random.choice([True, False]),
        "series_name": f"Series {idx % 10}",
        "season": random.randint(1, 5),
        "episode": random.randint(1, 20),
        "has_thumb": True,
        "mime_type": "video/x-matroska"
    }

def generate_dataset(n):
    videos = [generate_media_entry(i) for i in range(n)]
    return {
        "chat_id": "test_chat",
        "count": n,
        "videos": videos,
        "updated_at": time.time()
    }

# Create directory
os.makedirs("docs/product_development/benchmarks/data", exist_ok=True)

for n in [100, 1000, 5000, 10000]:
    with open(f'docs/product_development/benchmarks/data/test_data_{n}.json', 'w') as f:
        json.dump(generate_dataset(n), f)
    print(f"Generated test_data_{n}.json")
