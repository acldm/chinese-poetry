#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并 waitlist.json, waitlist1.json, waitlist2.json 为一个文件
"""

import json
import os

def merge_waitlist_files():
    """合并三个 waitlist JSON 文件"""
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 要合并的文件列表
    files_to_merge = [
        os.path.join(script_dir, 'waitlist.json'),
        os.path.join(script_dir, 'waitlist1.json'),
        os.path.join(script_dir, 'waitlist2.json')
    ]
    
    # 合并后的数据
    merged_data = []
    
    # 读取并合并每个文件
    for file_path in files_to_merge:
        if os.path.exists(file_path):
            print(f"正在读取: {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print(f"  - 包含 {len(data)} 条记录")
                merged_data.extend(data)
        else:
            print(f"警告: 文件不存在 - {file_path}")
    
    # 输出合并结果
    output_file = os.path.join(script_dir, 'waitlist_merged.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n合并完成!")
    print(f"总共 {len(merged_data)} 条记录")
    print(f"输出文件: {output_file}")

if __name__ == '__main__':
    merge_waitlist_files()
