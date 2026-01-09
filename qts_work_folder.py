import os
import json
import requests
import glob
import time
import signal
import threading
import argparse
from json_repair import repair_json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

# 配置
API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
TOKEN = "sk-ck5t8uacuegk8iu97db8nr4tqgr0tsnnvq3lwvnte4d3nojc"

BATCH_SIZE = 5
MAX_RETRIES = 3
MAX_WORKERS = 40  # 并发处理的文件数量
CHUNK_SIZE = 200  # 每个分片文件保存的诗词数量

# 全局变量：用于优雅退出
shutdown_event = threading.Event()
progress_lock = threading.Lock()  # 进度文件的线程锁
waitlist_lock = threading.Lock()  # waitlist 文件的线程锁
file_locks = {}  # 每个文件的独立锁

# 全局路径变量（由命令行参数设置）
SOURCE_DIR = ""
TARGET_DIR = ""
PROGRESS_FILE = ""
WAITLIST_FILE = ""

def signal_handler(signum, frame):
    """处理 Ctrl+C 信号，设置停止标志"""
    if shutdown_event.is_set():
        # 第二次 Ctrl+C，强制退出
        print("\n\n❌ 强制退出！")
        os._exit(1)
    print("\n\n⚠️  收到中断信号，正在优雅地停止所有任务...（再按一次 Ctrl+C 强制退出）")
    shutdown_event.set()

def load_progress() -> Dict:
    """加载进度文件"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  读取进度文件失败: {e}，将重新开始。")
    return {}

def save_progress(progress: Dict):
    """保存进度文件（线程安全）"""
    with progress_lock:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)

def update_file_progress(file_name: str, processed_count: int, total_count: int, status: str = "processing"):
    """更新单个文件的进度（线程安全）"""
    with progress_lock:
        progress = load_progress()
        progress[file_name] = {
            "processed_count": processed_count,
            "total_count": total_count,
            "status": status,
            "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)

def get_file_lock(file_name: str) -> threading.Lock:
    """获取或创建文件的独立锁"""
    with progress_lock:
        if file_name not in file_locks:
            file_locks[file_name] = threading.Lock()
        return file_locks[file_name]

def get_system_prompt():
    """读取 prompt.md 作为系统指令"""
    # 从脚本所在目录读取 prompt.md
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(script_dir, "prompt.md")
    with open(prompt_path, "r", encoding="utf-8") as f:
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
        "model": "mimo-v2-flash",
        "temperature": 0.3,
        "top_p": 0.95,
        # "thinking": {
        #     "type": "enabled"
        # },
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
    
    # 尝试解析 JSON，失败则使用 json_repair 修复
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"    ⚠️ JSON 解析失败: {e}，尝试修复...")
        repaired = repair_json(content)
        return json.loads(repaired)

def load_waitlist() -> List[Dict]:
    """加载 waitlist 文件"""
    if os.path.exists(WAITLIST_FILE):
        try:
            with open(WAITLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  读取 waitlist 文件失败: {e}，将创建新文件。")
    return []

def save_to_waitlist(poems: List[Dict], source_file: str):
    """将未完成的诗词保存到 waitlist（线程安全）"""
    if not poems:
        return
    with waitlist_lock:
        waitlist = load_waitlist()
        for poem in poems:
            # 添加来源文件信息便于追踪
            poem_entry = {
                "source_file": source_file,
                "title": poem.get("title", ""),
                "author": poem.get("author", ""),
                "paragraphs": poem.get("paragraphs", []),
                "added_time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            waitlist.append(poem_entry)
        with open(WAITLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(waitlist, f, ensure_ascii=False, indent=2)
        print(f"    📋 已将 {len(poems)} 首未完成诗词添加到 waitlist.json")

def process_batch_with_completion(batch_to_send: List[Dict], max_retries=MAX_RETRIES):
    """
    增量补全模式：针对缺失的诗词进行重叠请求，直到补齐 100%。
    批量请求失败后，改为逐首单独调用。
    返回：(成功结果列表, 未完成诗词列表)
    """
    all_results_dict = {} # key: paragraphs_str, value: result_obj
    
    # 辅助函数：获取内容的唯一标识
    def get_id(p):
        return "".join(p.get("paragraphs", [])).strip()

    def try_batch_request(current_batch, max_attempts):
        """
        批量请求 API
        返回：剩余未处理的诗词列表
        """
        attempt = 0
        # 只处理当前传入的批次（已排除已成功的诗词）
        batch = [p for p in current_batch if get_id(p) not in all_results_dict]
        
        if not batch:
            print(f"    所有诗词已有结果，无需请求。")
            return []
        
        while attempt <= max_attempts and batch:
            try:
                if attempt > 0:
                    print(f"    正在进行增量重试 (第 {attempt} 次)，剩余 {len(batch)} 首...")
                else:
                    print(f"    正在批量处理 {len(batch)} 首诗词...")
                
                results = process_poems_batch(batch)
                
                if isinstance(results, list):
                    new_count = 0
                    for r in results:
                        rid = get_id(r)
                        if rid not in all_results_dict:
                            all_results_dict[rid] = r
                            new_count += 1
                    
                    print(f"    本次成功获取 {len(results)} 首，其中新获得 {new_count} 首。")
                
                # 只从当前批次中计算还缺哪些（避免重复请求已有结果的诗）
                missing_batch = [p for p in batch if get_id(p) not in all_results_dict]
                
                if not missing_batch:
                    # 当前批次全部补齐
                    return []
                
                batch = missing_batch
                attempt += 1
                if attempt <= max_attempts:
                    time.sleep(2) # 失败后的短延时
                    
            except Exception as e:
                print(f"    批量请求出错 (Attempt {attempt}): {e}")
                attempt += 1
                if attempt <= max_attempts:
                    time.sleep(5)
        
        return batch  # 返回未处理完成的诗词

    def try_single_request(poem):
        """
        单首诗词请求 API
        返回：是否成功
        """
        poem_title = poem.get('title', '未知')[:20]
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    print(f"      单首重试 (第 {attempt} 次): {poem_title}")
                else:
                    print(f"      单独处理: {poem_title}")
                
                results = process_poems_batch([poem])
                
                if isinstance(results, list) and len(results) > 0:
                    for r in results:
                        rid = get_id(r)
                        if rid not in all_results_dict:
                            all_results_dict[rid] = r
                    
                    # 检查是否成功获取了这首诗
                    if get_id(poem) in all_results_dict:
                        print(f"      ✓ 单独处理成功: {poem_title}")
                        return True
                
                time.sleep(1)
                
            except Exception as e:
                print(f"      单首请求出错 (Attempt {attempt}): {e}")
                if attempt < max_retries:
                    time.sleep(3)
        
        return False

    # 第一步：批量请求
    remaining = try_batch_request(batch_to_send, max_retries)
    
    # 第二步：如果批量失败，逐首单独调用
    if remaining:
        print(f"    🔄 批量请求重试 {max_retries} 次后仍有 {len(remaining)} 首未完成，改为逐首单独调用...")
        still_failed = []
        for poem in remaining:
            if get_id(poem) not in all_results_dict:
                success = try_single_request(poem)
                if not success:
                    still_failed.append(poem)
        remaining = still_failed

    # 返回按照原始顺序排列的结果，以及未完成的诗词
    final_ordered_list = []
    failed_poems = []
    for original in batch_to_send:
        oid = get_id(original)
        if oid in all_results_dict:
            final_ordered_list.append(all_results_dict[oid])
        else:
            print(f"    ⚠️ 经过批量和单首处理仍无法获取诗词: {original.get('title', '未知')[:20]}")
            failed_poems.append(original)
            
    return final_ordered_list, failed_poems

def get_chunk_file_path(base_path: str, chunk_index: int) -> str:
    """
    根据分片索引生成分片文件路径
    chunk_index 0: poet.song.1000.json (基础文件，保存 0~199)
    chunk_index 1: poet.song.1000.1.json (保存 200~399)
    chunk_index 2: poet.song.1000.2.json (保存 400~599)
    """
    if chunk_index == 0:
        return base_path
    # 移除 .json 后缀，添加分片编号
    base_without_ext = base_path[:-5]  # 移除 ".json"
    return f"{base_without_ext}.{chunk_index}.json"




def process_single_file(file_path: str) -> bool:
    """
    处理单个文件（支持断点续传和分片保存）
    返回 True 表示完成，False 表示失败或被中断
    
    分片规则：
    - 每 CHUNK_SIZE (200) 首诗词保存到一个文件
    - poet.song.1000.json 保存第 0~199 首
    - poet.song.1000.1.json 保存第 200~399 首
    - poet.song.1000.2.json 保存第 400~599 首
    """
    file_name = os.path.basename(file_path)
    target_path = os.path.join(TARGET_DIR, file_name)
    file_lock = get_file_lock(file_name)
    
    # 检查是否需要停止
    if shutdown_event.is_set():
        return False
    
    with file_lock:
        # 从 progress.json 读取已处理数量
        progress = load_progress()
        file_progress = progress.get(file_name, {})
        processed_count = file_progress.get("processed_count", 0)
        
        if processed_count > 0:
            print(f"📄 [{file_name}] 发现已有进度，已处理 {processed_count} 首，继续处理...")

        # 读取源文件
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                all_poems = json.load(f)
        except Exception as e:
            print(f"❌ [{file_name}] 读取源文件失败: {e}")
            update_file_progress(file_name, 0, 0, "error")
            return False
            
        num_poems = len(all_poems)
        
        if processed_count >= num_poems:
            print(f"✅ [{file_name}] 已全部处理完成，跳过。")
            update_file_progress(file_name, processed_count, num_poems, "completed")
            return True
            
        print(f"🚀 [{file_name}] 开始/继续处理，共 {num_poems} 首诗，已处理 {processed_count} 首。")
        update_file_progress(file_name, processed_count, num_poems, "processing")
        
        # 当前分片的数据缓存
        current_chunk_index = processed_count // CHUNK_SIZE
        chunk_start = current_chunk_index * CHUNK_SIZE
        
        # 加载当前分片已有的数据（如果存在）
        current_chunk_path = get_chunk_file_path(target_path, current_chunk_index)
        current_chunk_data = []
        if os.path.exists(current_chunk_path):
            try:
                with open(current_chunk_path, "r", encoding="utf-8") as f:
                    current_chunk_data = json.load(f)
            except Exception:
                current_chunk_data = []
        
        # 从断点开始循环
        for i in range(processed_count, num_poems, BATCH_SIZE):
            # 检查是否需要停止
            if shutdown_event.is_set():
                print(f"⏸️  [{file_name}] 收到停止信号，保存当前进度后退出...")
                # 保存当前分片
                with open(current_chunk_path, "w", encoding="utf-8") as f:
                    json.dump(current_chunk_data, f, ensure_ascii=False, indent=4)
                update_file_progress(file_name, processed_count, num_poems, "paused")
                return False
            
            batch = all_poems[i : i + BATCH_SIZE]
            current_batch_num = i // BATCH_SIZE + 1
            total_batches = (num_poems - 1) // BATCH_SIZE + 1
            
            print(f"  📝 [{file_name}] 正在处理 batch {current_batch_num}/{total_batches} (索引 {i} 到 {min(i + BATCH_SIZE, num_poems)}) ...")
            
            # 提取 API 需要的字段
            batch_to_send = []
            for p in batch:
                batch_to_send.append({
                    "title": p.get("title", ""),
                    "author": p.get("author", ""),
                    "paragraphs": p.get("paragraphs", [])
                })
            
            results, failed_poems = process_batch_with_completion(batch_to_send)
            
            # 若有未完成的诗词，保存到 waitlist
            if failed_poems:
                save_to_waitlist(failed_poems, file_name)
            
            # 即使有部分失败，也要继续处理成功的部分
            if results:
                # 添加结果到当前分片
                current_chunk_data.extend(results)
                processed_count += len(results) + len(failed_poems)  # 失败的也计入已处理，因为已存入 waitlist
                
                # 检查是否需要切换到下一个分片
                new_chunk_index = (processed_count - 1) // CHUNK_SIZE if processed_count > 0 else 0
                
                if new_chunk_index > current_chunk_index:
                    # 当前分片已满，保存并切换到新分片
                    # 分割数据：前200首给当前分片，剩余的给新分片
                    items_for_current = CHUNK_SIZE - (len(current_chunk_data) - len(results))
                    
                    # 保存满的分片
                    with open(current_chunk_path, "w", encoding="utf-8") as f:
                        json.dump(current_chunk_data[:CHUNK_SIZE - (len(current_chunk_data) - len(results)) + items_for_current - len(results)], f, ensure_ascii=False, indent=4)
                    
                    # 更新分片信息
                    current_chunk_index = new_chunk_index
                    chunk_start = current_chunk_index * CHUNK_SIZE
                    current_chunk_path = get_chunk_file_path(target_path, current_chunk_index)
                    # 新分片只包含溢出的数据
                    current_chunk_data = current_chunk_data[CHUNK_SIZE:]
                
                # 保存当前分片
                with open(current_chunk_path, "w", encoding="utf-8") as f:
                    json.dump(current_chunk_data, f, ensure_ascii=False, indent=4)
                
                # 更新进度文件
                update_file_progress(file_name, processed_count, num_poems, "processing")
                
                print(f"    ✓ [{file_name}] Batch {current_batch_num} 已保存到 {os.path.basename(current_chunk_path)}。当前进度: {processed_count}/{num_poems}")
            elif failed_poems:
                # 全部失败但已存入 waitlist，继续处理下一个 batch
                processed_count += len(failed_poems)
                update_file_progress(file_name, processed_count, num_poems, "processing")
                print(f"    ⚠️ [{file_name}] Batch {current_batch_num} 全部失败已存入 waitlist，继续处理下一批...")
            
            # 适当延时
            time.sleep(1)
        
        print(f"✅ [{file_name}] 处理完成！共 {processed_count} 首诗。\n")
        update_file_progress(file_name, processed_count, num_poems, "completed")
        return True


def main():
    """主函数：使用多线程并发处理多个文件"""
    global SOURCE_DIR, TARGET_DIR, PROGRESS_FILE, WAITLIST_FILE
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description="诗词处理脚本 - 从指定输入文件夹读取 JSON 文件，处理后保存到指定输出文件夹",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python qts_work_folder.py -i ./input_folder -o ./output_folder
  python qts_work_folder.py --input ./诗词原始 --output ./诗词清洗
  python qts_work_folder.py -i ./data -o ./result --pattern "*.json"
        """
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="输入文件夹路径，包含待处理的 JSON 文件"
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="输出文件夹路径，处理后的文件将保存在此"
    )
    parser.add_argument(
        "-p", "--pattern",
        default="*.json",
        help="文件匹配模式，默认为 '*.json'（匹配所有 JSON 文件）"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"并发线程数，默认为 {MAX_WORKERS}"
    )
    
    args = parser.parse_args()
    
    # 设置全局路径变量
    SOURCE_DIR = os.path.abspath(args.input)
    TARGET_DIR = os.path.abspath(args.output)
    PROGRESS_FILE = os.path.join(TARGET_DIR, "progress.json")
    WAITLIST_FILE = os.path.join(TARGET_DIR, "waitlist.json")
    max_workers = args.workers
    file_pattern = args.pattern
    
    # 验证输入目录存在
    if not os.path.exists(SOURCE_DIR):
        print(f"❌ 输入文件夹不存在: {SOURCE_DIR}")
        return
    
    if not os.path.isdir(SOURCE_DIR):
        print(f"❌ 输入路径不是文件夹: {SOURCE_DIR}")
        return
    
    # 注册信号处理器（支持 Ctrl+C 优雅退出）
    signal.signal(signal.SIGINT, signal_handler)
    
    # 确保目标目录存在
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)
        
    # 获取所有匹配的 json 文件
    source_pattern = os.path.join(SOURCE_DIR, file_pattern)
    files = glob.glob(source_pattern)
    
    if not files:
        print(f"未找到匹配的文件: {source_pattern}")
        return
    
    # 加载之前的进度，优先处理未完成的文件
    progress = load_progress()
    
    # 根据进度排序：处理中/暂停的 > 未开始的 > 已完成的
    # 优先处理那些已经开始但还没完成的文件
    def file_priority(file_path):
        file_name = os.path.basename(file_path)
        if file_name not in progress:
            return 1  # 未开始的次之
        status = progress[file_name].get("status", "")
        if status in ("processing", "paused", "error"):
            return 0  # 处理中/暂停/出错的优先级最高（优先恢复）
        if status == "completed":
            return 2  # 已完成的最后
        return 1
    
    files.sort(key=file_priority)
    
    # 过滤掉已完成的文件
    pending_files = []
    completed_count = 0
    for file_path in files:
        file_name = os.path.basename(file_path)
        if file_name in progress and progress[file_name].get("status") == "completed":
            completed_count += 1
        else:
            pending_files.append(file_path)
    
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📂 输入目录: {SOURCE_DIR}")
    print(f"📂 输出目录: {TARGET_DIR}")
    print(f"📚 找到 {len(files)} 个文件，已完成 {completed_count} 个，待处理 {len(pending_files)} 个。")
    print(f"🔧 并发线程数: {max_workers}")
    print(f"💾 进度文件: {PROGRESS_FILE}")
    print(f"💡 提示: 按 Ctrl+C 可以优雅地停止并保存进度")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    
    if not pending_files:
        print("🎉 所有文件已处理完成！")
        return
    
    # 使用线程池并发处理文件
    success_count = 0
    failed_count = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_file = {executor.submit(process_single_file, file_path): file_path for file_path in pending_files}
        
        try:
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                file_name = os.path.basename(file_path)
                
                try:
                    result = future.result()
                    if result:
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    print(f"❌ [{file_name}] 处理时发生异常: {e}")
                    failed_count += 1
                
                # 如果收到停止信号，取消剩余任务
                if shutdown_event.is_set():
                    print("\n⏹️  正在取消剩余任务...")
                    for f in future_to_file:
                        f.cancel()
                    break
                    
        except KeyboardInterrupt:
            print("\n\n⚠️  捕获到键盘中断，正在保存进度...")
            shutdown_event.set()
    
    # 打印最终统计
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📊 处理统计:")
    print(f"   ✅ 成功: {success_count} 个文件")
    print(f"   ❌ 失败/中断: {failed_count} 个文件")
    if shutdown_event.is_set():
        print(f"   💾 进度已保存，下次运行将从断点继续")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
