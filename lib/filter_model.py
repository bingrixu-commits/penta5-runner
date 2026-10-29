# -*- coding: utf-8 -*-
"""
前四位（0000-9999）号码排除与过滤脚本
1. 排除指定的 2 个四位形态（如：奇偶形态、大小形态）
2. 从前 4 个位置中选 3 个位置，各排除 1 个指定数字
"""

def get_parity_pattern(num_str):
    """获取奇偶形态：奇/偶"""
    return "".join(["奇" if int(d) % 2 == 1 else "偶" for d in num_str])

def get_size_pattern(num_str):
    """获取大小形态（0-4为小，5-9为大）：大/小"""
    return "".join(["大" if int(d) >= 5 else "小" for d in num_str])

def run_filter():
    # ------------------ 配置区域 ------------------
    # 1. 号码位数：锁定为前四位
    DIGITS = 4 
    
    # 2. 本期要排除的 2 个四位形态（示例：排除全奇、全偶）
    EXCLUDE_PATTERNS = [
        "奇奇奇奇",  # 要排除的形态 1
        "偶偶偶偶",  # 要排除的形态 2
    ]
    
    # 3. 从 4 个位置中选 3 个位置各排除 1 个数字
    # 位置说明：0=第1位, 1=第2位, 2=第3位, 3=第4位
    # 示例：第1位排除3，第2位排除7，第3位排除0
    EXCLUDE_POS_DIGITS = {
        0: 3,  # 第 1 位排除数字 3
        1: 7,  # 第 2 位排除数字 7
        2: 0,  # 第 3 位排除数字 0
    }
    # ---------------------------------------------

    print("=== 开始执行前四位排除过滤 ===")
    total_count = 10 ** DIGITS
    print(f"前四位全集基数: {total_count} 注 (0000-9999)")
    print(f"排除形态: {EXCLUDE_PATTERNS}")
    print(f"位置排除规则: {EXCLUDE_POS_DIGITS}")

    retained = []
    excluded_by_pattern = 0
    excluded_by_pos = 0

    for i in range(total_count):
        num_str = str(i).zfill(DIGITS)

        # 规则 1：形态过滤
        parity_p = get_parity_pattern(num_str)
        size_p = get_size_pattern(num_str)
        if parity_p in EXCLUDE_PATTERNS or size_p in EXCLUDE_PATTERNS:
            excluded_by_pattern += 1
            continue

        # 规则 2：3 个位置定点排除
        hit_pos_exclude = False
        for pos, bad_digit in EXCLUDE_POS_DIGITS.items():
            if int(num_str[pos]) == bad_digit:
                hit_pos_exclude = True
                break

        if hit_pos_exclude:
            excluded_by_pos += 1
            continue

        retained.append(num_str)

    print("\n--- 过滤统计结果 ---")
    print(f"因形态排除注数: {excluded_by_pattern}")
    print(f"因位置杀号排除注数: {excluded_by_pos}")
    print(f"最终保留注数: {len(retained)}")
    print(f"保留前 20 条样例: {retained[:20]}")

    output_file = "filtered_results_4d.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(retained))
    print(f"完整结果已保存至: {output_file}")

if __name__ == "__main__":
    run_filter()
