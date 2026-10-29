raise SystemExit('Legacy position/source rewriter disabled. Preserve the documented first-four positions.')
import re

file_path = "/root/penta5-runner/auto_r1_framework.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 修复号码提取切片 (改为取后四位 千、百、十、个)
code = code.replace('nums[:4]', 'nums[-4:]')
code = code.replace('latest["numbers"][:4]', 'latest["numbers"][-4:]')

# 2. 修复 8 项检验逻辑 (针对 二数合02468排1码 误杀问题)
old_tail_logic = """tail2 = (d3 + d4) % 10
if str(tail2) not in he2: ok = False"""

new_tail_logic = """# 7. 二数合02468排1码: 找出被排除的那个码 (例如 02468 排除 4 留 0268)
tail2 = (d3 + d4) % 10
killed_he2 = [x for x in ["0","2","4","6","8"] if x not in he2]
if killed_he2 and str(tail2) == killed_he2[0]:
    ok = False"""

if old_tail_logic in code:
    code = code.replace(old_tail_logic, new_tail_logic)
elif 'str(tail2) not in he2' in code:
    code = re.sub(r'tail2\s*=\s*\(d3\s*\+\s*d4\)\s*%\s*10\s*\n\s*if\s*str\(tail2\)\s*not\s*in\s*he2:\s*ok\s*=\s*False', new_tail_logic, code)

# 3. 升级卡片展示
new_card = '''r1_stats_html = f\'\'\'<!-- 杀码框架真实回测与连中连败统计 -->
<div id="r1_framework_stats_box" style="margin: 14px auto 12px; max-width: 1000px; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 14px 18px; box-shadow: 0 2px 6px rgba(0,0,0,0.04); font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; padding-bottom: 10px; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
        <div style="display: flex; align-items: center; gap: 8px;">
            <span style="background: #0284c7; color: #fff; font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 4px;">DeepSeek-V4.1-Flash</span>
            <span style="font-size: 15px; font-weight: 700; color: #0f172a;">杀码框架 · 今日战绩</span>
        </div>
        <div style="display: flex; align-items: center; gap: 14px; font-size: 13px;">
            <span>总计: <b style="font-size: 16px; color: #0f172a;">{r1_stats["total"]}</b> 期</span>
            <span>全通过: <b style="font-size: 16px; color: #16a34a;">{r1_stats["hits"]}</b> 期</span>
            <span>通过率: <b style="font-size: 16px; color: #0284c7;">{r1_stats["rate"]:.2f}%</b></span>
        </div>
    </div>
    <div style="display: flex; justify-content: space-between; align-items: center; background: #f8fafc; border-radius: 6px; padding: 8px 12px; font-size: 12px; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
        <div>
            <span style="color: #64748b;">今日极值:</span>
            最高连中 <b style="color: #16a34a;">{r1_stats["max_win"]}</b> 手 | 最高连败 <b style="color: #dc2626;">{r1_stats["max_loss"]}</b> 手
        </div>
        <div>
            <span style="color: #64748b;">当前状态:</span>
            连中 <b style="color: #16a34a;">{r1_stats["cur_win"]}</b> 手 | 连败 <b style="color: #dc2626;">{r1_stats["cur_loss"]}</b> 手
        </div>
    </div>
    <div style="font-size: 11px; color: #475569; display: flex; align-items: center; flex-wrap: wrap; gap: 6px;">
        <span style="color: #64748b; font-weight: 600;">逐期严格核对8项（全通过才算中）:</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">①千位排除</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">②百位排除</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">③百十个合尾</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">④千百十合尾</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">⑤单双反向</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">⑥大小反向</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">⑦二数合排1码</span>
        <span style="background: #f1f5f9; border: 1px solid #e2e8f0; padding: 1px 6px; border-radius: 3px;">⑧三数合5码</span>
    </div>
</div>
\'\'\''''

pattern = r"r1_stats_html\s*=\s*f'''[\s\S]*?</div>\s*</div>\s*</div>\s*'''"
code = re.sub(pattern, new_card, code, count=1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)
print("✅ 全部修复完成：号码错位已矫正、二数合排除逻辑已修复、前台卡片已升级！")
