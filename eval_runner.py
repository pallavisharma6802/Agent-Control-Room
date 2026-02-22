"""
Simple eval runner for LLM Sentinel
Runs 20 test prompts and computes accuracy

Usage: python eval_runner.py
"""
import asyncio
import json
import httpx
from datetime import datetime


async def run_eval():
    with open("eval_prompts.json") as f:
        prompts = json.load(f)
    
    print(f"Running {len(prompts)} test prompts...")
    print(f"This will take ~{len(prompts) * 2} seconds\n")
    results = []
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for item in prompts:
            print(f"[{item['id']}/{len(prompts)}] {item['prompt'][:60]}...")
            
            try:
                response = await client.post(
                    "http://localhost:8000/query",
                    json={"prompt": item["prompt"], "session_id": f"eval_{item['id']}"}
                )
                data = response.json()
                
                predicted = data.get("is_hallucinated", False)
                expected = item["expected_hallucinated"]
                correct = predicted == expected
                
                results.append({
                    "id": item["id"],
                    "prompt": item["prompt"],
                    "category": item["category"],
                    "expected": expected,
                    "predicted": predicted,
                    "correct": correct,
                    "sources": data.get("sources_count", 0)
                })
                
                status = "✅" if correct else "❌"
                print(f"   {status} Expected: {expected}, Got: {predicted}\n")
                
            except Exception as e:
                print(f"   ⚠️  Error: {e}\n")
            
            await asyncio.sleep(1)
    
    # Calculate metrics
    correct_count = sum(1 for r in results if r["correct"])
    accuracy = (correct_count / len(results)) * 100 if results else 0
    
    # Save results
    output = {
        "run_date": datetime.now().isoformat(),
        "total": len(results),
        "correct": correct_count,
        "accuracy": round(accuracy, 1),
        "results": results
    }
    
    with open("eval_results.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\n{'='*50}")
    print(f"RESULTS")
    print(f"{'='*50}")
    print(f"Accuracy: {accuracy:.1f}% ({correct_count}/{len(results)})")
    print(f"Saved to eval_results.json")


if __name__ == "__main__":
    asyncio.run(run_eval())
