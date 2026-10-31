"""通用面板标记解析（面板 view 共享实现）。

供各 view 模块（ai_control_view、runtime_observability_view 等）复用：
解析 panel.html 中形如 <!-- NAME_START --> ... <!-- NAME_END --> 的命名区块。
"""
import re

_MARKERS = re.compile(r'<!--\s*([A-Z][A-Z0-9_]*)_(START|END)\s*-->')


def _inspect(content):
    if not isinstance(content, str):
        raise TypeError('Panel HTML must be text')
    found, stack = {}, []
    for match in _MARKERS.finditer(content):
        name, side = match.groups()
        if side == 'START':
            if name in found:
                raise ValueError('Duplicate panel marker')
            found[name] = [match.start(), None, None, len(stack)]
            stack.append(name)
        else:
            if not stack or stack[-1] != name:
                raise ValueError('Unbalanced panel marker')
            stack.pop()
            found[name][1:3] = [match.end(), content[found[name][0]:match.end()]]
    if stack:
        raise ValueError('Unbalanced panel marker')
    return found
