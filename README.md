# penta5-runner
penta5-runner666

## 2026-11-06 kill8：四全形合并 4 选 1 + 新增跨族混合 16 选 1（单双 / 大小 / 全形 / 跨族混合，整组仍 4 项）

- 依据：用户 2026-11-06 先问「四形态，是取反向冷形态吗？检查一下」（已完成只读核查），再提出「混合形态，比如大小双双，小小双双」并回复 b / 1 / ab，确认 A+B：③④合并为「四全形 4 选 1」、新增「跨族混合 16 选 1」，整组仍 4 项；随后指令「部署」。
- 口径：
  - ①单双、②大小：不变，16 种四位形态各选 1（实际四位形态 != 封存形态即命中）。
  - ③全形（原「全大/全小」+「全单/全双」合并）：在 大大大大 / 小小小小 / 单单单单 / 双双双双 4 选 1；方向按「已约束位置多数锁定」（单票也算锁定）：多数偏大→锁全小、多数偏小→锁全大、多数偏单→锁全双、多数偏双→锁全单；两类同时锁定且方向不同时由 AI 在 2 候选里按冷热二选一；两类都未成型时 4 选。
  - ④跨族混合：16 种（前两位千/百按大小反向、后两位十/个按单双反向，如 大小双双），未约束位置由 AI 按冷热自选。
  - 命中：全形按覆盖号码集合判定（全大=四位全大、全小=四位全小、全单=四位全单、全双=四位全双）；跨族混合=前两位大小 pattern + 后两位单双 pattern 与封存一致；整组仍 4 项全中。
  - 去重：③全形与①单双或②大小同形且该项还有自由位时，翻最靠前自由位；被排除数字钉死的位置不动。
  - 旧 v3–v5 封存（all_size_form/all_parity_form）保留旧键、旧结算分支与逐字攻击语回退，不计入 v6 统计（面板 legacy 归档）。
- 改动文件（3 files）：
  - lib/kill8_tail.py：FORM_KINDS=(parity,size,uniform,mixed)；RULE_VERSION=kill8-tail-v6-merged-four-form-mixed；新增 UNIFORM_FORM_VALUES/SOURCE/NAMES、MIXED_FORM_SPACE/MIXED_HEAD_PALETTE/MIXED_TAIL_PALETTE、LEGACY_FORM_KINDS/FIELDS、pattern_of_mixed、_majority_lock/_uniform_candidates/_mixed_constraints、_uniform_constraint_text/_mixed_constraint_text；form_space/rank_forms/reverse_constraints/build_prompt/normalize_response/_dedupe_forms/create_seal/settle_pending/today_stats/build_attack 同步 v6，旧 all_size/all_parity 键兼容。
  - lib/kill_eight_view.py：4 卡改「全形反向冷形态 / 跨族混合反向冷形态」；整组 σ 用 0.7725（文案 77.25%）；_SETTLE_ITEMS 4 项 + _LEGACY_SETTLE_ITEMS 兜底；data-uniform/data-mixed 属性（JS 回退 data-all-size/data-all-parity）；foot 改为 v6 规则/命中口径。
  - lib/hitrate_baseline.py：登记 ('kill8_tail','kill8-tail-v6-merged-four-form-mixed'): (4, 0.7725)。
- 基线（10^4 开奖空间 × 16384 均匀配置精确枚举，禁单项相乘）：v2 复算 0.87890625 自证一致；v3/v5 复算 0.7733056640625 自证一致；v6 每 draw 命中 15*15*15*(4-m)，Σ=15^3*37500=126,562,500 → 0.7724761962890625，登记 0.7725（低于 v5 0.77330566：四项退化为严格独立 15/16，相关性红利消失）；uniform_hit_factor 分布 m=0:7694 / m=1:2112 / m=2:194；A only（3 项）0.823974609375。证据：/root/ops/kill8-v6-054710/evidence/kill8_baseline_enum_v6.json。
- 验证（改后、上线前）：py_compile 全绿；kill8_v6_test.py PASS=107 FAIL=0（常量/空间/rank/反向约束/prompt/normalize+dedupe/create_seal/settle 五分支含 v2/v3/v5 旧封存兼容/build_attack/today_stats）；kill8_v6_view_probe.py PASS=32 FAIL=0（旧 v5 封存 2 卡+legacy 提示、旧结算 legacy 行、v6 封存 4 卡/77.25%/uniform+mixed 结算行/data 属性、空态、注入替换与异常兜底）；golden_compare.py PASS=48 FAIL=0（golden_before 5 条旧封存 attack 用 v6 build_attack 回退逐字一致、input_sha256 复算 True、settlements 文件 sha256 不变、seals 仅 +1 条 v6、last_settlements 逐字段不变、today legacy 继承改前 3 期 2 中）。
- 真实周期验证（AI 开关 enabled，手动触发）：2026-11-06 06:45:46 published，ref 20261106081 → target 20261106082，封存 单双=单单双单、大小=小大小小、全形=单单单单、跨族混合=小大双单；attack「除单单双单除小大小小除单单单单除小大双单」；面板 4 卡 / 口径 kill8-tail-v6-merged-four-form-mixed / 整组精确基线 77.25%（4 项）/ 待核验 1 期 / 旧口径（历史版本封存）3 期（整组 2 中）归档。
- 备份 / 回滚：改动前文件、patch 脚本、单测与探针、golden before/after、枚举脚本与 JSON 在 /root/ops/kill8-v6-054710/（MANIFEST.txt 含 sha256）。回滚：`git checkout f9a9622 -- lib/kill8_tail.py lib/kill_eight_view.py lib/hitrate_baseline.py`（f9a9622 = 改动前 HEAD），再手动跑一次 kill8 恢复面板（旧封存数据不动；新 v6 封存会按 legacy 渲染）。
- 版本/口径串：RULE_VERSION=kill8-tail-v6-merged-four-form-mixed；面板 4 卡「全形反向冷形态 / 跨族混合反向冷形态」；hitrate_baseline 整组基线 77.25%（4 项）。## 2026-11-05 kill8：反向冷形态扩为 4 项（单双 / 大小 / 全大·全小 / 全单·全双）

- 依据：用户 2026-11-05 17:03 指令「这个框架修改一下，排除数字给出排除四个形态」；同日问卷确认四张卡 = 单双反向冷形态、大小反向冷形态、全大/全小、全单/全双，整组 4 项全中，kill9 候选池不动，③④方向由排除数字锁定。
- 口径：
  - ①单双、②大小：同旧版，AI 从 16 种四位形态各选 1 个作为排除项（实际四位形态 != 封存形态即命中）。
  - ③全大/全小：在「大大大大」「小小小小」2 选 1；④全单/全双：在「单单单单」「双双双双」2 选 1。
  - 方向锁定：四个位置排除数字全部偏大 → ③锁定「小小小小」；全部偏小 → ③「大大大大」；全部偏单 → ④锁定「双双双双」；全部偏双 → ④「单单单单」；方向混杂/未填 → AI 在 2 种里按冷热自选。②在 4 位置同向时也会被位置约束锁定为同形（允许两卡相同形态）。
  - 命中/统计：每项按「实际四位形态 != 封存形态」判定；整组 = 4 项全中；无合尾/连用限制；05:00 清零；只用前 4 球；发布窗口仍为开奖后 target slot−70s。
- 改动文件（commit 0ddfdf4，4 files +247/−100）：
  - lib/kill8_tail.py：FORM_KINDS=(parity,size,all_size,all_parity)、COUNTED_FORMS=4、ALL_FORM_VALUES/FORM_FIELDS/FORM_SOURCE、RULE_VERSION=kill8-tail-v3-four-reverse-cold-forms；form_space/rank_forms/reverse_constraints/build_prompt/normalize_response/create_seal/settle_pending/today_stats/build_attack 同步；旧 v2 封存按 counted_forms=2 结算（不重算历史，视为 legacy）。
  - lib/kill_eight_view.py：标题「🤖 AI 四反向冷形态排除建议」、4 卡、整组统计（4 项全中 + 单项各 93.75% + 整组 77.33% σ）、攻击语 4 个「除」、启动键仍 POST /api/kill8-run、旧口径封存渲染提示。
  - lib/hitrate_baseline.py：登记 ('kill8_tail','kill8-tail-v3-four-reverse-cold-forms'): (4, 0.7733)，LABELS 改「AI 四反向冷形态（4 项）」。
  - scripts/run_kill8_tail.py：发布日志打印 4 形态。
- 基线（10^4 开奖空间 × 1024 均匀配置精确枚举，禁单项相乘）：v2 复算 0.87890625=(15/16)^2 自证通过；v3 整组 = (15/16)^2 × E_draw[P(全大同向|draw)·P(全单同向|draw)] = 0.7733056640625 → 登记 0.7733；朴素 (15/16)^4=0.77247620 不可用（4 项同 draw 正相关）。draw 分类 neither 7694 / size_only 1056 / parity_only 1056 / both 194；单项均 93.75%。证据：/root/ops/kill8-v3-172200/evidence/baseline_enum.json。
- 验证（改后、上线前）：py_compile 全绿；kill8_tail_v3_test.py PASS=56；kill8_view_probe.py PASS=23（旧 v2 seal 渲染 2 卡 + 旧口径提示；合成 v3 seal 渲染 4 卡 / 77.33% / 4 子项结算行 / attack 4 个「除」；空态）。
- 真实周期验证（AI 开关 enabled，手动触发；验证用排除数字 q=5,b=6,s=7,g=8 全部偏大）：
  - 第 1 期：ref 20261105209 → target 20261105210，封存 单双=双单双单、大小=小小小小、全大/全小=小小小小（方向锁定）、全单/全双=双双双双，published 17:25:25；面板 4 卡 / 77.33% / 攻击语「除双单双单除小小小小除小小小小除双双双双」。
  - 结算：开奖 20261105210 实际 双双单单 / 大小小大 → parity、size、all_size、all_parity 四项均 hit=True，counted_forms=4、group_hit=True、verified=True；v2 遗留 pending（20261105111→112）同时按 counted_forms=2 结算成功（向后兼容）。
  - 第 2 期：ref 20261105210 → target 20261105211，封存 双单双单 / 小小小小 / 小小小小 / 双双双双，published 17:30:46；today_stats total=1、group_hits=1、4 单项各 hits=1；legacy={total:2,group_hits:2}（旧口径 2 期归档不计入）；pending=1。
- 备份 / 回滚：改动前文件、全部 patch 脚本、单测与探针脚本、baseline 证据在 /root/ops/kill8-v3-172200/（MANIFEST.txt 含 sha256）。回滚：`git checkout 0ddfdf4^ -- lib/kill8_tail.py lib/kill_eight_view.py lib/hitrate_baseline.py scripts/run_kill8_tail.py`，再手动跑一次 kill8 恢复面板（旧封存数据不动；新 v3 封存会按 legacy 处理）。
- 版本/口径串：RULE_VERSION=kill8-tail-v3-four-reverse-cold-forms；面板标题「🤖 AI 四反向冷形态排除建议」；hitrate_baseline 整组基线 0.7733（4 项）。
