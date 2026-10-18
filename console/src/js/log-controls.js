/* User-controlled logging for the bundled Vue console. No trading APIs. */
const WtMonitorLogControls = (function (root, factory) {
    var controls = factory(root);
    return controls;
}(typeof window !== "undefined" ? window : globalThis, function (root) {
    "use strict";
    var prefix = "wtpy.monitor.logAuto.";

    function read(groupid, fallback) {
        try {
            var value = root.localStorage.getItem(prefix + groupid);
            if (value === "true") return true;
            if (value === "false") return false;
        } catch (_) { /* Browser storage can be unavailable; keep the default. */ }
        return fallback;
    }

    function stop(view) {
        if (view.logInterval) root.clearInterval(view.logInterval);
        view.logInterval = 0;
    }

    function selectGroup(view, info) {
        if (view.logControlsDisposed) return;
        stop(view);
        view.logControlsRequest = (view.logControlsRequest || 0) + 1;
        view.logScroll = info.datmod === "auto";
        view.autoLog = read(info.id, view.logScroll);
        view.logs = "";
        view.logCache = "";
        view.logLines = 0;
    }

    function save(view) {
        try {
            root.localStorage.setItem(prefix + view.groupinfo.id, String(!!view.autoLog));
        } catch (_) { /* The checkbox still works without persistence. */ }
    }

    function reset(view) {
        stop(view);
        if (view.logControlsDisposed) return;
        // MQ mode appends events; file mode polls only when explicitly enabled.
        if (view.autoLog && !view.logScroll) {
            view.logInterval = root.setInterval(function () { view.queryLogs(); }, 15000);
        }
    }

    function shouldAppend(view) {
        return !view.logControlsDisposed && view.logScroll && view.autoLog;
    }

    function dispose(view) {
        view.logControlsDisposed = true;
        stop(view);
    }

    function beginRead(view) {
        view.logControlsRequest = (view.logControlsRequest || 0) + 1;
        return {groupid: view.groupinfo.id, serial: view.logControlsRequest};
    }

    function acceptRead(view, request) {
        return !view.logControlsDisposed && request.groupid === view.groupinfo.id &&
            request.serial === view.logControlsRequest;
    }

    return {selectGroup: selectGroup, save: save, reset: reset,
            shouldAppend: shouldAppend, stop: stop, dispose: dispose,
            beginRead: beginRead, acceptRead: acceptRead};
}));

export default WtMonitorLogControls;
