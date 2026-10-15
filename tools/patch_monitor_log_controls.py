"""Maintain the small hooks in the upstream's bundled console (Vue sources absent).

Run only against the bundled app.5fc723073e2fadbc897d.js version. The readable
behavior is in monitor-log-controls.js; this script neither downloads nor builds
another frontend. It is idempotent and rejects unexpected upstream snippets.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / 'wtpy/monitor/static/console'
APP = CONSOLE / 'static/js/app.5fc723073e2fadbc897d.js'
INDEX = CONSOLE / 'index.html'

def main():
    text = APP.read_text(encoding='utf-8')
    replacements = [
        ('this.logScroll="auto"==t.datmod,',
         'window.WtMonitorLogControls.selectGroup(this,t),'),
        ('handleCheckAutoLog:function(t){this.resetLogInterval()}',
         'handleCheckAutoLog:function(t){window.WtMonitorLogControls.save(this),this.resetLogInterval()}'),
        ('resetLogInterval:function(){var t=this;this.autoLog?(0!=this.logInterval&&clearInterval(this.logInterval),this.logInterval=setInterval(function(){t.queryLogs()},15e3)):0!=this.logInterval&&clearInterval(this.logInterval)}',
         'resetLogInterval:function(){window.WtMonitorLogControls.reset(this)}'),
        ('if(!t.logScroll)return;t.processLog(e)',
         'if(!window.WtMonitorLogControls.shouldAppend(t))return;t.processLog(e)'),
        ('s("el-row",{directives:[{name:"show",rawName:"v-show",value:!t.logScroll,expression:"!logScroll"}]}',
         's("el-row",{}'),
        ('content:"每个15秒刷新一次"',
         'content:t.logScroll?"接收实时日志；取消后暂停追加，刷新可补读文件":"每15秒刷新日志文件"'),
        ('mounted:function(){var t=this;t.$on("notify",function(e){if(e.groupid==t.groupinfo.id)',
         'beforeDestroy:function(){window.WtMonitorLogControls.dispose(this)},mounted:function(){var t=this;t.$on("notify",function(e){if(e.groupid==t.groupinfo.id)'),
    ]
    if 'window.WtMonitorLogControls.selectGroup' not in text:
        for before, after in replacements:
            assert text.count(before) == 1, (before[:90], text.count(before))
            text = text.replace(before, after, 1)
    for _, after in replacements:
        assert after in text, after
    assert 's("el-row",{},[s("el-col"' in text
    read_hooks = [
        ('queryLogs:function(t){var e=this;t=t||!1;var s=this;',
         'queryLogs:function(t){var e=this;t=t||!1;var s=this,n=window.WtMonitorLogControls.beginRead(this);'),
        ('this.$api.getLogs(this.groupinfo.id,this.logfilter,function(a){a.result<0?',
         'this.$api.getLogs(this.groupinfo.id,this.logfilter,function(a){if(!window.WtMonitorLogControls.acceptRead(e,n))return;a.result<0?'),
    ]
    if 'window.WtMonitorLogControls.beginRead(this)' not in text:
        for before, after in read_hooks:
            assert text.count(before) == 1, before
            text = text.replace(before, after, 1)
    for _, after in read_hooks: assert after in text
    APP.write_text(text, encoding='utf-8')
    html = INDEX.read_text(encoding='utf-8')
    script = '<script type=text/javascript src=./monitor-log-controls.js?rev=3></script>'
    vendor = '<script type=text/javascript src=./static/js/vendor.52f437eab58f59eb892d.js></script>'
    html = re.sub(r'src=\./monitor-log-controls\.js\?rev=\d+',
                  'src=./monitor-log-controls.js?rev=3', html)
    if script not in html:
        assert html.count(vendor) == 1
        html = html.replace(vendor, script + vendor)
    html = re.sub(r'src=\./static/js/app\.5fc723073e2fadbc897d\.js(?:\?rev=log-controls-\d+)?',
                  'src=./static/js/app.5fc723073e2fadbc897d.js?rev=log-controls-3', html)
    INDEX.write_text(html, encoding='utf-8')
    print('Bundled console hooks and readable log controls are installed.')

if __name__ == '__main__': main()
