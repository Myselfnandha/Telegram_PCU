from playwright.sync_api import sync_playwright
import json
import statistics
import os

def run_benchmark():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Use a simple HTML structure for the benchmark to avoid depending on app load
        page.set_content('<html><body><div id="catalog"></div></body></html>')

        results = {}
        for n in [100, 1000, 5000, 10000]:
            trials = []
            for _ in range(5):
                with open(f'docs/product_development/benchmarks/data/test_data_{n}.json', 'r') as f:
                    dataset = json.load(f)
                
                metrics = page.evaluate(f"""async () => {{
                    const data = {json.dumps(dataset)};
                    const start = performance.now();
                    
                    const container = document.getElementById('catalog');
                    container.innerHTML = data.videos.map(v => '<div class="card">' + v.filename + '</div>').join('');
                    
                    const end = performance.now();
                    
                    return {{
                        render_time: end - start,
                        dom_nodes: document.querySelectorAll('*').length
                    }};
                }}""")
                trials.append(metrics)
            
            results[n] = {
                "median_render": statistics.median([t["render_time"] for t in trials]),
                "avg_dom_nodes": statistics.mean([t["dom_nodes"] for t in trials])
            }
        
        with open('docs/product_development/benchmarks/FRONTEND_BENCHMARK_RESULTS.json', 'w') as f:
            json.dump(results, f, indent=2)
        print(json.dumps(results, indent=2))
        browser.close()

if __name__ == "__main__":
    run_benchmark()
