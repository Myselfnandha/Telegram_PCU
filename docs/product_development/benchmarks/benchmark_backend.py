import json
import time
import statistics
import os
import timeit

def benchmark_backend():
    results = {}
    for n in [100, 1000, 5000, 10000]:
        filepath = f'docs/product_development/benchmarks/data/test_data_{n}.json'
        
        trials = []
        for _ in range(5):
            # Load dataset (DB/File simulation)
            start_load = time.perf_counter()
            with open(filepath, 'r') as f:
                data = json.load(f)
            end_load = time.perf_counter()
            
            # Simulate API query (minimal time in this mock)
            start_query = time.perf_counter()
            # Simulation: just accessing the list
            videos = data['videos']
            end_query = time.perf_counter()
            
            # Simulate API serialization
            start_ser = time.perf_counter()
            json_response = json.dumps(data)
            end_ser = time.perf_counter()
            
            trials.append({
                "query_time": end_query - start_query,
                "serialization_time": end_ser - start_ser,
                "total_time": (end_ser - start_load)
            })
            
        results[n] = {
            "median_query": statistics.median([t["query_time"] for t in trials]),
            "median_ser": statistics.median([t["serialization_time"] for t in trials]),
            "median_total": statistics.median([t["total_time"] for t in trials])
        }
    
    with open('docs/product_development/benchmarks/BACKEND_BENCHMARK_RESULTS.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    benchmark_backend()
