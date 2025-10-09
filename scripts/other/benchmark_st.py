#!/usr/bin/env python3
"""
Benchmark SentenceTransformer encoding speed.

Usage:
  python benchmark_st.py --model "sentence-transformers/all-MiniLM-L6-v2" --text "Hello world"
"""

import argparse
import time
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_DIMENSIONS = {
    # SentenceTransformers MiniLM family
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-MiniLM-L12-v2": 384,
    "sentence-transformers/paraphrase-MiniLM-L6-v2": 384,
    "sentence-transformers/paraphrase-MiniLM-L12-v2": 384,

    # DistilRoberta
    "sentence-transformers/all-distilroberta-v1": 768,

    # MPNet
    "sentence-transformers/all-mpnet-base-v2": 768,

    # Multilingual
    "sentence-transformers/distiluse-base-multilingual-cased-v2": 512,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    "sentence-transformers/LaBSE": 768,

    # RoBERTa large
    "sentence-transformers/all-roberta-large-v1": 1024,

    # E5 family
    "intfloat/e5-base-v2": 768,
    "intfloat/e5-large-v2": 1024,

    # BGE family
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,

    # Misc ST classics
    "sentence-transformers/multi-qa-MiniLM-L6-cos-v1": 384,
    "sentence-transformers/multi-qa-mpnet-base-dot-v1": 768,
    "sentence-transformers/msmarco-distilbert-base-tas-b": 768,
    "sentence-transformers/multi-qa-distilbert-cos-v1": 768,
}

def main():
    parser = argparse.ArgumentParser(description="Benchmark SentenceTransformer encode speed.")
    parser.add_argument("--model", required=True, help="Model name (e.g., sentence-transformers/all-MiniLM-L6-v2)")
    parser.add_argument("--text", default="The quick brown fox jumps over the lazy dog", help="Sentence to embed")
    parser.add_argument("--duration", type=int, default=10, help="Duration to run benchmark (seconds)")
    args = parser.parse_args()

    model_name = args.model.strip()
    text = args.text
    duration = args.duration

    print(f"🚀 Loading model: {model_name}")
    model = SentenceTransformer(model_name)

    dim = EMBEDDING_MODEL_DIMENSIONS.get(model_name, None)
    if dim:
        print(f"🧠 Expected embedding dimension: {dim}")
    else:
        print("⚠️ Unknown model dimension (not in lookup table).")

    print(f"🕒 Benchmarking for {duration} seconds with input: \"{text}\"")

    start_time = time.perf_counter()
    end_time = start_time + duration
    count = 0

    while time.perf_counter() < end_time:
        _ = model.encode(text)
        count += 1

    total_time = time.perf_counter() - start_time
    per_second = count / total_time

    print("\n📊 Benchmark Results:")
    print(f"  Model: {model_name}")
    print(f"  Runs: {count}")
    print(f"  Duration: {total_time:.2f}s")
    print(f"  Avg speed: {per_second:.2f} embeddings/sec")
    print(f"  Dim: {dim if dim else 'unknown'}")


if __name__ == "__main__":
    main()
