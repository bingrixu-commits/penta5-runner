# kill15 交接清单（2026-11-12 06:35 存档）

改天回来，从这里开始看，不用翻聊天记录。

## 一、三条命令看全局

```bash
cd /root/penta5-runner

# 1) AI 开关 + 余额
python3 scripts/ai_api_control.py status
cat data/ai_balance_guard.json | python3 -m json.tool | head -6

# 2) kill15 线上状态（封存/结算/面板统计）
python3 - <<'PY'
import json
s=json.load(open('data/kill15_tail/seals.json'))['records']
t=json.load(open('data/kill15_tail/settlements.json'))['records']
print('seals',len(s),'latest',s[-1]['target_period'],s[-1]['rule_version'],s[-1]['mode'])
gh=sum(1 for r in t if r.get('group_hit'))
print('settled',len(t),'group hits',gh,'=',round(100*gh/len(t),2) if t else 0,'%')
PY

# 3) 健康巡检（只看 kill15）
python3 scripts/ops_health_check.py --root /root/penta5-runner 2>&1 | grep -i kill15
```

## 二、两个回放样本（判断引擎是否真有效）

| 样本 | 窗口 | 结果 | 文件 |
| --- | --- | --- | --- |
| 第一段 | 最新 200 期 | 整组 **74.50%**（CI 67.9–81.1%），单码 92.12% | `data/kill15_tail/replay_v2_200.json` |
| 第二段（复现） | 不相交的 199 期（`20261110253..20261111163`） | 跑至 27/199 时 **59.26%**，需等跑完 | `data/kill15_tail/replay_v2_next.json` |

**判读方法**：两段都用冻结的 v2 代码（哈希 `0c01e41d`）。若第二段最终仍 >72%，这条边值得信；若回到 65% 附近，第一段就是运气。第二段跑完看 `replay_v2_next.log` 末尾的 total。

同窗口对照（不调模型）供参考：池内取遗漏最大 63.0%、取最小 67.0%、取最热 65.0%、固定杀 0 60.5%（整组）。

## 三、待观察 / 待处理

| 事项 | 说明 |
| --- | --- |
| 余额 | 约 37 元；<5 元自动暂停全部 AI，充到 6 元以上才恢复 |
| `coverage` / `hitrate_stability` FAIL | 00:47–04:42 停机留下的 24h 缺口，随窗口滚动自愈，与 kill15 无关 |
| 另一个会话 | 它仍在改 kill15（v4 及之后），提交前注意 `git status` |
| v4 周期闭环 | 40 期一轮，跨轮清空胜率偏好；面板显示「本轮 x/40」 |

## 四、回滚

```bash
cd /root/penta5-runner
git log --oneline -5                 # 找目标提交
git reset --hard <commit>            # 代码回退
pm2 restart penta5-runner            # 若涉及主 runner
# kill15 由 cron 拉起，无需额外重启；账本不随代码回退，按 rule_version 保留
```

数据库全量备份：`/root/backups/db-20261112-043237/penta5.sql.gz`（145 MB，`gzip -t` 通过）。
