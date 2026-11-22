import json
import time
import statistics
import os

def benchmark_frontend_logic():
    results = {}
    for n in [100, 1000, 5000, 10000]:
        filepath = f'docs/product_development/benchmarks/data/test_data_{n}.json'
        
        trials = []
        for _ in range(5):
            with open(filepath, 'r') as f:
                start_parse = time.time()
                data = json.load(f) # Simulate JSON.parse
                end_parse = time.time()
                
                # Simulate map/render logic (process filenames)
                start_render = time.time()
                rendered = [f"<div>{v['filename']}</div>" for v in data['videos']]
                end_render = time.time()
                
            trials.append({
                "parse_time": end_parse - start_parse,
                "render_simulation_time": end_render - start_render
            })
            
        results[n] = {
            "median_parse": statistics.median([t["parse_time"] for t in trials]),
            "median_render": statistics.median([t["render_simulation_time"] for t in trials])
        }
    
    with open('docs/product_development/benchmarks/FRONTEND_BENCHMARK_RESULTS.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    benchmark_frontend_logic()
