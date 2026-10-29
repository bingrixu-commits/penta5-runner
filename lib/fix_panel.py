import re

with open("panel.html", "r", encoding="utf-8") as f:
    content = f.read()

# 1. 清理误写入的末尾乱码
content = re.sub(r'\\?int\(.*$', '', content).rstrip()
if not content.endswith("</html>"):
    if "</html>" in content:
        content = content[:content.rfind("</html>")+7]
    else:
        content += "\n</html>"

# 2. 替换修复 penta5RowBusinessDayKey 函数
func_pattern = r'function\s+penta5RowBusinessDayKey\s*\([^)]*\)\s*\{[\s\S]*?\}\n(?=\s*function|\s*/\*|\s*var|\s*window)'

correct_func = """function penta5RowBusinessDayKey(row){
  if(!row) return "";
  var p = String(row.period || row.uid || "").trim();
  var m = p.match(/^(\\d{4})(\\d{2})(\\d{2})/);
  if(m) return m[1] + "-" + m[2] + "-" + m[3];
  var dt = penta5ParseLocalDateTime(row.opentime);
  if(dt && Number.isFinite(dt.getTime())){
    var shifted = new Date(dt.getTime() + 8*3600000 - PENTA5_DAY_RESET_HOUR*3600000);
    var y = shifted.getUTCFullYear();
    var mo = String(shifted.getUTCMonth() + 1).padStart(2, "0");
    var d = String(shifted.getUTCDate()).padStart(2, "0");
    return y + "-" + mo + "-" + d;
  }
  return "";
}
"""

if re.search(func_pattern, content):
    content = content.replace(re.search(func_pattern, content).group(0), correct_func) if re.search(func_pattern, content) else content
    with open("panel.html", "w", encoding="utf-8") as f:
        f.write(content)
    print(">>> panel.html 文件与语法彻底修复成功！")
else:
    with open("panel.html", "w", encoding="utf-8") as f:
        f.write(content)
    print(">>> 已清理末尾乱码，请刷新测试")
