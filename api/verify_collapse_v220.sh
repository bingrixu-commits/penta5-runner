#!/bin/bash
set -e
python3 - <<'PY'
html=open('/root/penta5-runner/panel.html','r',encoding='utf-8').read()
checks={
 'v220':'20260714_remain_collapse_v220' in html,
 'twoToggle':'data-collapse-toggle="txtTwoRemainTop"' in html,
 'threeToggle':'data-collapse-toggle="txtThreeRemainTop"' in html,
 'twoBody':'data-collapse-body="txtTwoRemainTop"' in html,
 'threeBody':'data-collapse-body="txtThreeRemainTop"' in html,
 'restore':'restorePanelCollapsibles(twoTop)' in html,
}
print(checks)
assert all(checks.values())
print('FILE_OK')
PY
