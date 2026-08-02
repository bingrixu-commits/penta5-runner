# 彩票过滤系统 - 稳定数学过滤器

## 概述

本系统实现了彩票号码的稳定数学过滤功能，根据预定义规则过滤不理想的号码组合。
同时提供杀码分析功能，用于分析千百十个位置及合尾的杀码。

## 过滤规则

### 1. 重号过滤 (repeatAvoid)
- **规则**: 禁止出现三个或四个相同的数字
- **示例**: "1111" 被拒绝
- **新增**: 增加三重/四重过滤规则

### 2. 连号过滤 (consecutiveAvoid)
- **规则**: 禁止出现三个或四个连续的数字
- **示例**: "1234" 被拒绝
- **新增**: 增加三连/四连过滤规则

### 3. 奇偶比过滤 (parityRatioAvoid)
- **规则**: 禁止4单0双或0单4双的组合
- **示例**: "1357" (4单0双) 被拒绝
- **新增**: 增加4单0双/0单4双过滤规则

### 4. 大小比过滤 (sizeRatioAvoid)
- **规则**: 禁止4大0小或0大4小的组合
- **示例**: "2468" (0大4小) 被拒绝
- **新增**: 增加4大0小/0大4小过滤规则

### 5. 和值区间过滤 (sumRangeAvoid)
- **规则**: 禁止和值在0-5或31-36范围内的组合
- **示例**: "0000" (和值0) 被拒绝

### 6. 合尾过滤 (tailSumAvoid)
- **规则**: 禁止尾数和为特定值的组合（如0, 5, 10, 15）
- **示例**: "1234" (尾数和10) 被拒绝

### 7. 对数过滤 (logarithmAvoid)
- **规则**: 禁止包含极端数字的组合（如0, 9）
- **示例**: "0123" 被拒绝

### 8. 杀码分析 (killingCodes)
- **功能**: 分析千百十个位置及合尾的杀码
- **说明**: 根据历史数据统计，为每个位置和合尾计算应排除的数字

## 文件结构

- `lottery_filter.py` - 核心过滤逻辑实现
- `run.py` - 运行脚本
- `stable_math_filter_bridge.js` - JavaScript桥接文件
- `README.md` - 本文件

## 使用方法

### Python方式
```python
from lottery_filter import apply_all_filters, analyze_killing_codes

# 过滤号码
result = apply_all_filters("1234")
print(result)

# 分析杀码
draws = ["1234", "5678", "9012"]
killing_codes = analyze_killing_codes(draws)
print(killing_codes)
```

### JavaScript方式
```javascript
const { applyStableMathFilter, getKillingCodesAnalysis } = require('./stable_math_filter_bridge.js');

// 过滤号码
applyStableMathFilter("1234")
  .then(result => console.log(result))
  .catch(error => console.error(error));

// 分析杀码
const draws = ["1234", "5678", "9012"];
getKillingCodesAnalysis(draws)
  .then(result => console.log(result))
  .catch(error => console.error(error));
```

## 性能要求

- 内存使用稳定在300MB以下
- 禁止在循环中无上限地累积DataFrame/全局List/大codes数组
- 预测侧recentExactCode*封存后必须omit，结算按baseUid + lookback重建

## 缓存文件

- `data/stable_math_filter.json` - 算法输出缓存

## 开发规范

- 所有输入必须使用 `normalize_draw` 去掉特殊字符/字母，仅保留0-9四码
- 保留维度：Z-Score、遗漏、全奇偶大小路、跨度0/1/9、和值<6/30
- 与主模型extra.*键名对齐