raise SystemExit('Legacy credential/source rewriter disabled. Use reviewed environment configuration.')
import re, os

new_key = ''
new_model = "deepseek-v4-pro"
dir_path = "/root/penta5-runner"

for fname in ["auto_r1_framework.py", "auto_r1_killer.py", "auto_r1_match5.py"]:
    fpath = os.path.join(dir_path, fname)
    if os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            code = f.read()
        
        # 1. 替换为新密钥
        code = re.sub(r'sk-[a-zA-Z0-9]{20,}', new_key, code)
        code = re.sub(r'sk-这里填入你的新密钥', new_key, code)
        
        # 2. 替换为最强模型 deepseek-v4-pro，并开启低延迟深度推理
        code = re.sub(r'"model":\s*"[^"]+"', f'"model": "{new_model}", "reasoning_effort": "low"', code)
        code = code.replace("timeout=45", "timeout=120")
        
        # 3. 同步前端卡片显示为 DeepSeek-V4.1-Flash
        code = code.replace("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash")
        code = code.replace("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash")
        
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"✅ 成功配置最强模型: {fname}")

for root, _, files in os.walk(dir_path):
    for f in files:
        if f.endswith((".py", ".html")) and not f.endswith(".bak"):
            fp = os.path.join(root, f)
            with open(fp, "r", encoding="utf-8", errors="ignore") as file_in:
                c = file_in.read()
            if "DeepSeek-V4.1-Flash" in c or "DeepSeek-V4.1-Flash" in c:
                c = c.replace("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash").replace("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash")
                with open(fp, "w", encoding="utf-8") as file_out:
                    file_out.write(c)

print("🚀 全部服务配置 DeepSeek-V4.1-Flash 完成！")
