import json
import time
import statistics
import os
import sqlite3

def benchmark_backend_api():
    # Setup test DB
    db_path = 'docs/product_development/benchmarks/test_db.sqlite'
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, data TEXT)")
    
    results = {}
    for n in [100, 1000, 5000, 10000]:
        filepath = f'docs/product_development/benchmarks/data/test_data_{n}.json'
        with open(filepath, 'r') as f:
            data = json.load(f)
            
        # Bulk insert
        cursor.execute("DELETE FROM videos")
        cursor.execute("INSERT INTO videos (data) VALUES (?)", (json.dumps(data),))
        conn.commit()
        
        trials = []
        for _ in range(5):
            # Measure DB Query
            start_db = time.perf_counter()
            cursor.execute("SELECT data FROM videos")
            row = cursor.fetchone()
            end_db = time.perf_counter()
            
            # Measure Serialization
            start_ser = time.perf_counter()
            json.loads(row[0])
            end_ser = time.perf_counter()
            
            trials.append({
                "db_time": end_db - start_db,
                "ser_time": end_ser - start_ser
            })
            
        results[n] = {
            "median_db": statistics.median([t["db_time"] for t in trials]),
            "median_ser": statistics.median([t["ser_time"] for t in trials])
        }
    
    with open('docs/product_development/benchmarks/BACKEND_API_BENCHMARK.json', 'w') as f:
        json.dump(results, f, indent=2)
    conn.close()

if __name__ == "__main__":
    benchmark_backend_api()
