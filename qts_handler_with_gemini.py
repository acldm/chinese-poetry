
import asyncio
import os
import json
import time
import glob
from pathlib import Path
from typing import List, Dict
from gemini_webapi import GeminiClient
from gemini_webapi.constants import Model

# 配置
# 请从环境变量获取或直接填写
GEMINI_1PSID = "g.a0004gjGL8pn20toy8awNBy71jQIpWG0Qde12hN39XxMhWwEBYwuc_hybJpptWhA8MXn5vmevAACgYKAakSARUSFQHGX2MiKzdToZ0VHOxO6Gd_qYI4gRoVAUF8yKqhcT5_sbqnHgBDPnCrJmqT0076"
GEMINI_1PSIDTS = "sidts-CjIBflaCdUU8EGSVi4_wE7xdsXJlCm1DFoItYrZXDf9wBnsFY0f-lD8UiY-vFriu8LU17xAA"

SOURCE_DIR = "全唐诗"
TARGET_DIR = "全唐诗clean"
BATCH_SIZE = 100
MAX_RETRIES = 999

def get_system_prompt():
    """读取 prompt.md 作为系统指令"""
    if os.path.exists("prompt.md"):
        with open("prompt.md", "r", encoding="utf-8") as f:
            return f.read()
    return ""

async def process_poems_batch(client: GeminiClient, poems_batch: List[Dict]):
    """执行单次 Gemini 请求"""
    system_prompt = get_system_prompt()
    user_content = json.dumps(poems_batch, ensure_ascii=False)
    
    # 构造 Prompt，模拟 System Prompt + User Message
    full_prompt = f"{system_prompt}\n\n---\n\n{user_content}"
    
    # 调用 Gemini 生成内容
    # 使用 generate_content 进行单轮问答
    response = await client.generate_content(full_prompt, model=Model.G_3_0_FLASH_THINKING)
    
    content = response.text
    
    # 清理 Markdown 代码块
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
        
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print(f"    JSON 解析失败，返回原始内容片段: {content[:100]}...")
        return None

async def process_batch_with_retry(client: GeminiClient, batch_to_send: List[Dict], max_retries=MAX_RETRIES):
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
            
            results = await process_poems_batch(client, current_batch)
            
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
                await asyncio.sleep(2) # 失败后的短延时
                
        except Exception as e:
            print(f"    请求出错 (Attempt {attempt}): {e}")
            attempt += 1
            if attempt <= max_retries:
                await asyncio.sleep(5)

    # 返回按照原始顺序排列的结果
    final_ordered_list = []
    for original in batch_to_send:
        oid = get_id(original)
        if oid in all_results_dict:
            final_ordered_list.append(all_results_dict[oid])
        else:
            # 如果重试多次还是缺，为了不让程序崩溃，先跳过这首或记录错误
            print(f"    Warning: 经过多次重试仍无法获取诗词: {original.get('title', '')[:10]}")
            
    return final_ordered_list

async def main():
    if not GEMINI_1PSID or not GEMINI_1PSIDTS:
        print("警告: 未设置 GEMINI_1PSID 或 GEMINI_1PSIDTS 环境变量。")
        print("请在代码中设置或使用 export GEMINI_1PSID='...' 设置。")
        # 允许继续尝试，如果用户已经安装了 browser-cookie3 且登录了，可能自动获取
    
    # 初始化 Client
    print("正在初始化 Gemini Client...")
    
    # 如果 browser-cookie3 可用，可以不传 cookies
    # 但为了稳定性，建议传入 cookies
    client = GeminiClient(GEMINI_1PSID, GEMINI_1PSIDTS, proxy=None)
    
    # 初始化
    await client.init(timeout=300, auto_close=False, close_delay=300, auto_refresh=True)
    
    print("Gemini Client 初始化完成。")

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
            
            results = await process_batch_with_retry(client, batch_to_send)
            
            if results:
                processed_all.extend(results)
                
                # 每次处理完一个 batch 立即保存
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(processed_all, f, ensure_ascii=False, indent=4)
                
                print(f"    Batch {current_batch_num} 处理成功并已保存。")
            else:
                print(f"    Batch {current_batch_num} 最终处理失败，停止处理该文件以防数据错位。")
                break
            
            # 适当延时，避免频率过高
            await asyncio.sleep(2)
            
        print(f"文件 {file_name} 处理阶段结束。\n")
        
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被用户中断。")
