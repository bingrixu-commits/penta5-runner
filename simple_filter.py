# -*- coding: utf-8 -*-
import os
import re

# ================= 4位杀码设置（每期直接改这里的数字） =================
K1 = 3   # 第 1 位杀码
K2 = 7   # 第 2 位杀码
K3 = 0   # 第 3 位杀码
K4 = 5   # 第 4 位杀码

# 排除的 2 个形态
EXCLUDE_PATTERNS = ["奇奇奇奇", "偶偶偶偶"]
# ====================================================================

def run():
    retained = []
    
    for i in range(10000):
        s = "%04d" % i
        # 拆解四位数字：d1对应第1位，d2对应第2位，d3对应第3位，d4对应第4位
        d1, d2, d3, d4 = map(int, s)
        
        # 1. 四个位置各杀一码（命中任意一个位置即排除）
        if d1 == K1 or d2 == K2 or d3 == K3 or d4 == K4:
            continue
            
        # 2. 排除 2 个形态（奇偶或大小）
        parity = "".join(["奇" if int(c) % 2 == 1 else "偶" for c in s])
        size = "".join(["大" if int(c) >= 5 else "小" for c in s])
        if parity in EXCLUDE_PATTERNS or size in EXCLUDE_PATTERNS:
            continue
            
        retained.append(s)

    total_retained = len(retained)
    print("=== 计算完成 ===")
    print("杀码: 第1位!=%d, 第2位!=%d, 第3位!=%d, 第4位!=%d" % (K1, K2, K3, K4))
    print("排除形态:", EXCLUDE_PATTERNS)
    print("全集 10,000 注 ➔ 最终精确剩余: %d 注" % total_retained)

    # 保存文本结果
    res_path = "/root/penta5-runner/filtered_results_4d.txt"
    with open(res_path, "w", encoding="utf-8") as f:
        f.write("\n".join(retained))

    # 极简写入网页 panel.html
    panel_path = "/root/penta5-runner/panel.html"
    if os.path.exists(panel_path):
        with open(panel_path, "r", encoding="utf-8") as f:
            html = f.read()

        html = re.sub(r'<!-- SIMPLE_FILTER_START -->.*?<!-- SIMPLE_FILTER_END -->', '', html, flags=re.DOTALL)
        html = re.sub(r'<!-- ====== 前四位排除过滤模型展示模块 \(START\) ====== -->.*?<!-- ====== 前四位排除过滤模型展示模块 \(END\) ====== -->', '', html, flags=re.DOTALL)

        nums_str = " ".join(retained)
        patterns_str = ", ".join(EXCLUDE_PATTERNS)

        card_html = (
            '<!-- SIMPLE_FILTER_START -->\n'
            '<div style="margin: 15px auto; max-width: 900px; padding: 16px; background: #fff; border: 1px solid #d9d9d9; border-radius: 6px; font-family: -apple-system, sans-serif;">\n'
            '    <div style="font-size: 16px; font-weight: bold; margin-bottom: 8px; color: #333;">\n'
            '        🎯 前四位极简排除（4位各杀1码 + 排除2形态）\n'
            '    </div>\n'
            '    <div style="font-size: 14px; line-height: 1.8; color: #555; margin-bottom: 10px;">\n'
            '        • <b>四位杀码：</b>第1位≠<span style="color:red;">' + str(K1) + '</span>、第2位≠<span style="color:red;">' + str(K2) + '</span>、第3位≠<span style="color:red;">' + str(K3) + '</span>、第4位≠<span style="color:red;">' + str(K4) + '</span><br>\n'
            '        • <b>排除形态：</b><span style="color:red;">' + patterns_str + '</span><br>\n'
            '        • <b>计算结果：</b>基数 10,000 注 ➔ <b>剩余 <span style="color:#1677ff; font-size:17px;">' + str(total_retained) + '</span> 注</b>\n'
            '    </div>\n'
            '    <div style="display: flex; gap: 10px; margin-bottom: 8px;">\n'
            '        <button onclick="navigator.clipboard.writeText(document.getElementById(\'simple-nums-txt\').value);alert(\'已复制全部 ' + str(total_retained) + ' 注号码！\');" \n'
            '                style="padding: 6px 14px; background: #1677ff; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 13px;">\n'
            '            一键复制全部 ' + str(total_retained) + ' 注号码\n'
            '        </button>\n'
            '    </div>\n'
            '    <textarea id="simple-nums-txt" readonly style="width: 100%; height: 80px; font-family: monospace; font-size: 13px; padding: 8px; box-sizing: border-box; border: 1px solid #e0e0e0; border-radius: 4px; background: #fafafa;">' + nums_str + '</textarea>\n'
            '</div>\n'
            '<!-- SIMPLE_FILTER_END -->'
        )

        if "</body>" in html:
            html = html.replace("</body>", card_html + "\n</body>")
        else:
            html = html + "\n" + card_html

        with open(panel_path, "w", encoding="utf-8") as f:
            f.write(html)
        print("已极简更新到网页 panel.html")

if __name__ == "__main__":
    run()
