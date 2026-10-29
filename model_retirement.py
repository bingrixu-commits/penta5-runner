"""Explicit user-requested retirement; historical validators remain usable."""
RETIRED_MODELS = frozenset(('selected7', 'selected'))
SELECTED_MARKERS = ('SELECTED_FRAMEWORK', 'SELECTED_FRAMEWORK_ATTACK', 'SELECTED_FRAMEWORK_STATS')


def is_retired(model):
    return model in RETIRED_MODELS


def strip_selected7_panel(page):
    if not isinstance(page,str):raise TypeError('retirement-panel-must-be-text')
    ranges=[]
    for marker in SELECTED_MARKERS:
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
