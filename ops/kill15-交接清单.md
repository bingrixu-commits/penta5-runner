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

---

## 六、按框架隔离 AI 调用（2026-11-12 06:52 落地）

### 做了什么

| 层 | 实现 | 效果 |
| --- | --- | --- |
| 开关 | `lib/ai_scope.py` + `data/ai_scope_control.json`，叠在全局开关之上 | 每个框架可单独停启，互不影响 |
| 密钥 | `scripts/with_scope.sh` 按作用域注入独立 key 到 `DEEPSEEK_API_KEY` | 一把 key 泄露/吊销只影响一个框架 |
| 余额 | `ai_balance_guard` 逐 key 核对（240 秒缓存），低于阈值只暂停对应作用域 | 单框架烧爆不会拖停全部 |
| 调度 | 13 条 AI cron 全部 `with_scope.sh <scope>` 包裹 | cron 天然按框架隔离 |

### 10 把独立 key（DeepSeek 账号内）

| key 名 | 绑定作用域 |
| --- | --- |
| penta5-killer | killer |
| penta5-framework | framework |
| penta5-he3 | he3 |
| penta5-kill1 | kill1 |
| penta5-kill3 | kill3 |
| penta5-kill11 | kill11 |
| penta5-kill14 | kill14 |
| penta5-kill15 | kill15 |
| penta5-cold | cold15 + cold30（共用） |
| penta5-replay | replay / 实验 |

kill10 / kill12 / kill13 未分配独立 key，继续回退共享 `DEEPSEEK_API_KEY`。

### 重要限制

**DeepSeek 的余额是账号级的，不按 key 分。** 所以这 10 把 key 共享同一份余额：
- 能做到：独立吊销、独立识别、独立开关；
- 做不到：独立的额度池。要真额度隔离，必须开多个 DeepSeek 账号，各自充值。

### 常用命令

```bash
python3 scripts/ai_scope_control.py status
python3 scripts/ai_scope_control.py stop  --scope kill14
python3 scripts/ai_scope_control.py start --scope kill14
python3 scripts/ai_scope_control.py stop-all
```

### 注意

- `.env` 里 `PENTA5_API_KEY_*` 已填好，改完 key 不必重启，下一次 cron 自动生效；
- `with_scope.sh` 会显式 `source .env`，因为 cron 不继承环境变量；
- 余额守卫的缓存文件 `data/ai_scope_balance_cache.json` 只存 key 尾部 6 位，不存明文。
