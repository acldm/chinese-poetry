import os
import json
import requests
import glob
import time
from typing import List, Dict

# 配置
API_URL = "https://api.vectorengine.ai/v1/chat/completions"
# 请在此处填写您的 token，或者从环境变量中读取
TOKEN = "sk-z5fALOQvOt5XoRmtSfBEK9ilSy1PQDawSVSCXyVb7MD9ycni" 
SOURCE_DIR = "全唐诗"
TARGET_DIR = "全唐诗clean"
BATCH_SIZE = 20
MAX_RETRIES = 3

def get_system_prompt():
    """读取 prompt.md 作为系统指令"""
    with open("prompt.md", "r", encoding="utf-8") as f:
        return f.read()

def process_poems_batch(poems_batch: List[Dict]):
    """执行单次 API 请求"""
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {TOKEN}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        "messages": [
            {
                "role": "system",
                "content": get_system_prompt()
            },
            {
                "role": "user",
                "content": json.dumps(poems_batch, ensure_ascii=False)
            }
        ],
        "model": "gemini-3-flash-preview-thinking",
        "temperature": 0.3,
        "top_p": 1,
        "stream": False
    }
    
    # 给 5 分钟超时
    response = requests.post(API_URL, headers=headers, json=payload, timeout=300) 
    response.raise_for_status()
    data = response.json()
    
    content = data['choices'][0]['message']['content']
    
    # 清理 Markdown 代码块
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
        
    return json.loads(content)

def process_batch_with_completion(batch_to_send: List[Dict], max_retries=MAX_RETRIES):
    """
    增量补全模式：针对缺失的诗词进行重叠请求，直到补齐 100%。
    """
    all_results_dict = {} # key: paragraphs_str, value: result_obj
    
    # 辅助函数：获取内容的唯一标识
    def get_id(p):
        return "".join(p.get("paragraphs", [])).strip()

    attempt = 0
    current_batch = batch_to_send
    
    while attempt <= max_retries and current_batch:
        try:
            if attempt > 0:
                print(f"    正在进行增量重试 (第 {attempt} 次)，剩余 {len(current_batch)} 首...")
            
            results = process_poems_batch(current_batch)
            
            if isinstance(results, list):
                new_count = 0
                for r in results:
                    rid = get_id(r)
                    if rid not in all_results_dict:
                        all_results_dict[rid] = r
                        new_count += 1
                
                print(f"    本次成功获取 {len(results)} 首，其中新获得 {new_count} 首。")
            
            # 计算还缺哪些
            missing_batch = []
            for original in batch_to_send:
                if get_id(original) not in all_results_dict:
                    missing_batch.append(original)
            
            if not missing_batch:
                # 全部补齐
                break
            
            current_batch = missing_batch
            attempt += 1
            if attempt <= max_retries:
                time.sleep(2) # 失败后的短延时
                
        except Exception as e:
            print(f"    请求出错 (Attempt {attempt}): {e}")
            attempt += 1
            if attempt <= max_retries:
                time.sleep(5)

    # 返回按照原始顺序排列的结果
    final_ordered_list = []
    for original in batch_to_send:
        oid = get_id(original)
        if oid in all_results_dict:
            final_ordered_list.append(all_results_dict[oid])
        else:
            # 如果重试多次还是缺，为了不让程序崩溃，先跳过这首或记录错误
            print(f"    Warning: 经过多次重试仍无法获取诗词: {original.get('title')[:10]}")
            
    return final_ordered_list

def main():
    # 确保目标目录存在
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)
        
    # 获取所有 poet 开头的 json 文件
    source_pattern = os.path.join(SOURCE_DIR, "poet.*.json")
    files = glob.glob(source_pattern)
    
    if not files:
        print(f"未找到匹配的文件: {source_pattern}")
        return

    print(f"找到 {len(files)} 个待处理文件。")
    
    for file_path in files:
        file_name = os.path.basename(file_path)
        target_path = os.path.join(TARGET_DIR, file_name)
        
        # 加载已有进度（断点续传）
        processed_all = []
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                try:
                    processed_all = json.load(f)
                    print(f"发现已有进度 {file_name}，已处理 {len(processed_all)} 首，继续处理...")
                except Exception as e:
                    print(f"读取已有进度文件 {file_name} 失败: {e}，将重新开始处理。")
                    processed_all = []

        # 读取源文件
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                all_poems = json.load(f)
            except Exception as e:
                print(f"读取文件 {file_name} 失败: {e}")
                continue
            
        num_poems = len(all_poems)
        processed_count = len(processed_all)
        
        if processed_count >= num_poems:
            print(f"文件 {file_name} 已全部处理完成，跳过。")
            continue
            
        print(f"开始/继续处理 {file_name}，共 {num_poems} 首诗。")
        
        # 从断点开始循环
        for i in range(processed_count, num_poems, BATCH_SIZE):
            batch = all_poems[i : i + BATCH_SIZE]
            current_batch_num = i // BATCH_SIZE + 1
            total_batches = (num_poems - 1) // BATCH_SIZE + 1
            
            print(f"  正在处理 batch {current_batch_num}/{total_batches} (索引 {i} 到 {min(i + BATCH_SIZE, num_poems)}) ...")
            
            # 提取 API 需要的字段
            batch_to_send = []
            for p in batch:
                batch_to_send.append({
                    "title": p.get("title", ""),
                    "author": p.get("author", ""),
                    "paragraphs": p.get("paragraphs", [])
                })
            
            results = process_batch_with_completion(batch_to_send)
            
            if results:
                processed_all.extend(results)
                
                # 每次处理完一个 batch 立即保存
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(processed_all, f, ensure_ascii=False, indent=4)
                
                print(f"    Batch {current_batch_num} 处理成功并已保存。")
            else:
                print(f"    Batch {current_batch_num} 最终处理失败，停止处理该文件以防数据错位。")
                break
            
            # 适当延时
            time.sleep(1)
            
        print(f"文件 {file_name} 处理阶段结束。\n")


if __name__ == "__main__":
    main()
