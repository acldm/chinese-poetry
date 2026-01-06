import json
import os
import math

def split_json_file(input_file, output_dir, chunk_size):
    print(f"Loading {input_file}...")
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        print(f"Error: {input_file} does not contain a list.")
        return

    total_items = len(data)
    num_chunks = math.ceil(total_items / chunk_size)
    print(f"Total items: {total_items}. Splitting into {num_chunks} chunks of {chunk_size}...")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created directory: {output_dir}")

    for i in range(num_chunks):
        start_idx = i * chunk_size
        end_idx = min((i + 1) * chunk_size, total_items)
        chunk = data[start_idx:end_idx]
        
        output_file = os.path.join(output_dir, f"waitlist_chunk_{i+1}.json")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)
        
        print(f"Saved {len(chunk)} items to {output_file}")

    print("Splitting completed successfully.")

if __name__ == "__main__":
    base_dir = r"c:\Users\theway\Documents\projects\chinese-poetry\全唐诗clean"
    input_filename = os.path.join(base_dir, "waitlist_merged.json")
    output_directory = os.path.join(base_dir, "tst")
    chunk_count = 200
    
    split_json_file(input_filename, output_directory, chunk_count)
