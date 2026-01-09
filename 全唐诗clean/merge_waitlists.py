import json
import os

def merge_json_files(input_files, output_file):
    merged_data = []
    
    for file_path in input_files:
        print(f"Loading {file_path}...")
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    merged_data.extend(data)
                    print(f"Added {len(data)} items from {file_path}.")
                else:
                    print(f"Warning: {file_path} does not contain a list.")
        else:
            print(f"Error: {file_path} not found.")

    print(f"Total items merged: {len(merged_data)}")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)
    
    print(f"Successfully saved merged data to {output_file}.")

if __name__ == "__main__":
    # 使用绝对路径确保脚本能找到文件
    base_dir = r"c:\Users\theway\Documents\projects\chinese-poetry\全唐诗clean"
    files_to_merge = [
        os.path.join(base_dir, "waitlist.json"),
        os.path.join(base_dir, "waitlist1.json"),
        os.path.join(base_dir, "waitlist2.json")
    ]
    output_filename = os.path.join(base_dir, "waitlist_merged.json")
    
    merge_json_files(files_to_merge, output_filename)
