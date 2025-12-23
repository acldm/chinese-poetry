import requests
import json
import os
import sys

# --- 配置区域 ---
# 你可以在这里修改你的 API Key 和 Base URL
API_KEY = os.environ.get("OPENAI_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:3000/v1")
MODEL = "gpt-3.5-turbo"  # 修改为你想要测试的模型名称
# ----------------

def test_chat_completion_stream():
    # 这里拼接后面一段
    url = f"{BASE_URL}/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    
    data = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "你好，请写一段关于人工智能简短的介绍。"}
        ],
        "stream": True,
        "stream_options": {"include_usage": True}  # 关键：请求在流式结束时包含 usage
    }
    
    print(f"正在请求接口 (流式): {url}")
    print(f"使用模型: {MODEL}")
    print("-" * 30)
    print("回复内容:", end="", flush=True)
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data), stream=True)
        response.raise_for_status()
        
        usage = None
        
        for line in response.iter_lines():
            if not line:
                continue
            
            # 移除 'data: ' 前缀
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                line_str = line_str[6:]
            
            if line_str == '[DONE]':
                break
                
            try:
                chunk = json.loads(line_str)
                
                # 提取内容
                if 'choices' in chunk and len(chunk['choices']) > 0:
                    delta = chunk['choices'][0].get('delta', {})
                    if 'content' in delta:
                        print(delta['content'], end="", flush=True)
                
                # 提取 Usage (通常在流的最后一个数据块或倒数第二个)
                if 'usage' in chunk and chunk['usage'] is not None:
                    usage = chunk['usage']
                    
            except json.JSONDecodeError:
                continue

        print("\n" + "-" * 30)
        
        # 打印 Token 消耗情况
        if usage:
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            total_tokens = usage.get('total_tokens', 0)
            
            print("Token 消耗统计 (流式):")
            print(f"  - 提示词 (Prompt) Token:    {prompt_tokens}")
            print(f"  - 回复词 (Completion) Token: {completion_tokens}")
            print(f"  - 总计 (Total) Token:        {total_tokens}")
        else:
            print("提示: 未在流式输出中捕获到 usage 信息。")
            print("注意: 部分旧版模型或特定中转接口可能不支持在 stream 中返回 usage。")
            
    except requests.exceptions.RequestException as e:
        print(f"\n请求发生错误: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"错误详情: {e.response.text}")

if __name__ == "__main__":
    if API_KEY == "YOUR_API_KEY_HERE" and not os.environ.get("OPENAI_API_KEY"):
        print("提示: 你还没有配置 API_KEY，请在脚本中修改或设置环境变量 OPENAI_API_KEY。")
    
    test_chat_completion_stream()