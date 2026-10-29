
def filter_since_5am(records):
    from datetime import datetime, timedelta
    now = datetime.now()
    if now.hour < 5:
        cutoff = (now - timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
    else:
        cutoff = now.replace(hour=5, minute=0, second=0, microsecond=0)
    
    res = []
    for r in records:
        t_str = ""
        if isinstance(r, dict):
            t_str = r.get("time") or r.get("draw_time") or r.get("created_at") or ""
        elif isinstance(r, (list, tuple)) and len(r) > 1:
            for item in r:
                if isinstance(item, str) and len(item) >= 19 and item[4] == '-' and item[7] == '-':
                    t_str = item
                    break
        
        if t_str:
            try:
                dt = datetime.strptime(t_str[:19], "%Y-%m-%d %H:%M:%S")
                if dt >= cutoff:
                    res.append(r)
                continue
            except Exception:
                pass
        res.append(r)
    return res

import re

file_path = "/root/penta5-runner/auto_r1_framework.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 自动备份原文件
with open(file_path + ".bak", "w", encoding="utf-8") as f:
    f.write(content)

new_stats_html = '''r1_stats_html = f\'\'\'<!-- 杀码框架真实回测与连中连败统计 -->
<div id="r1_framework_stats_box" style="margin: 14px auto 12px; max-width: 1000px; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 14px 18px; box-shadow: 0 2px 6px rgba(0,0,0,0.04); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
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
        <span style="color: #64748b; font-weight: 600;">核对8项（全通过才算中）:</span>
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
if re.search(pattern, content):
    content = re.sub(pattern, new_stats_html, content, count=1)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ 统计器卡片已成功升级！(原文件已备份为 auto_r1_framework.py.bak)")
else:
    print("❌ 未能精确匹配到原卡片代码，请确认文件内容")
