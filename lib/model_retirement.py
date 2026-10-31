"""Explicit user-requested retirement; historical validators remain usable."""
RETIRED_MODELS = frozenset(('selected7', 'selected'))
SELECTED_MARKERS = ('SELECTED_FRAMEWORK', 'SELECTED_FRAMEWORK_ATTACK', 'SELECTED_FRAMEWORK_STATS')
# 2026-11-01: 冷码家族（AI 冷码七项框架 + 冷码观察/冷码杀号战绩/位置冷码）按用户要求整体退役；
# 其面板块在每次重建时被剥除。
COLD_MARKERS = ('COLD_FRAMEWORK', 'COLD_OBSERVATION', 'COLD_HIT_STATS', 'POSITION_COLD')


def is_retired(model):
    return model in RETIRED_MODELS


def strip_retired_panels(page):
    if not isinstance(page,str):raise TypeError('retirement-panel-must-be-text')
    ranges=[]
    for marker in SELECTED_MARKERS + COLD_MARKERS:
        start,end='<!-- '+marker+'_START -->','<!-- '+marker+'_END -->'
        counts=page.count(start),page.count(end)
        if counts==(0,0):continue
        if counts!=(1,1):raise ValueError('retirement-ambiguous-markers')
        left,right=page.index(start),page.index(end)+len(end)
        if left>=right-len(end):raise ValueError('retirement-reversed-markers')
        ranges.append((left,right))
    ranges.sort()
    if any(b[0]<a[1] for a,b in zip(ranges,ranges[1:])):
        raise ValueError('retirement-overlapping-markers')
    for left,right in reversed(ranges):page=page[:left]+page[right:]
    return page
