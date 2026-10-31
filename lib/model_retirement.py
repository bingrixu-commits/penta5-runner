"""Explicit user-requested retirement; historical validators remain usable."""
RETIRED_MODELS = frozenset(('selected7', 'selected', 'cold_fixed'))
SELECTED_MARKERS = ('SELECTED_FRAMEWORK', 'SELECTED_FRAMEWORK_ATTACK', 'SELECTED_FRAMEWORK_STATS')
COLD_FIXED_MARKERS = ('COLD_FIXED_FRAMEWORK',)
RETIRED_PANEL_MARKERS = SELECTED_MARKERS + COLD_FIXED_MARKERS


def is_retired(model):
    return model in RETIRED_MODELS


def strip_retired_panels(page):
    if not isinstance(page,str):raise TypeError('retirement-panel-must-be-text')
    ranges=[]
    for marker in RETIRED_PANEL_MARKERS:
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


# Historical callers kept working; the strip set is now the full retirement list.
strip_selected7_panel = strip_retired_panels
