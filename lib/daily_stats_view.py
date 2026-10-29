"""Hide stale displayed totals as soon as the Beijing 05:00 day changes."""
import re

SCRIPT = """<script id="penta5-stats-day-control">
(function(){
  function businessDate(now){return new Date(now+3*60*60*1000).toISOString().slice(0,10);}
  function check(){
    var day=businessDate(Date.now());
    document.querySelectorAll('[data-stats-business-date]').forEach(function(element){
      if(element.getAttribute('data-stats-business-date')!==day){
        element.textContent='北京时间05:00已清零，等待本周期数据';
        element.setAttribute('data-stats-day-stale','true');
      }
    });
  }
  window.__PENTA5_CHECK_STATS_DAY__=check;
  check();
  window.addEventListener('pageshow',check);
  document.addEventListener('visibilitychange',check);
  setInterval(check,1000);
})();
</script>"""


def apply_daily_guard(content):
    pattern = r'<script id="penta5-stats-day-control">.*?</script>'
    if re.search(pattern, content, re.S):
        return re.sub(pattern, lambda _: SCRIPT, content, count=1, flags=re.S)
    if '</body>' not in content:
        raise ValueError('Panel body missing; daily statistics guard not installed')
    return content.replace('</body>', SCRIPT + '\n</body>', 1)
