"""Isolated typed backtest child. No live trader/parser is created."""
import inspect, json, signal, threading, time
from pathlib import Path
import MyStrategy
from wtpy import BaseCtaStrategy, BaseHftStrategy, BaseSelStrategy, WtBtEngine, EngineType
from wtpy.apps import WtBtAnalyst
engine = None
stopped = threading.Event()
finished = threading.Event()
STRATEGY_TYPE = '$STRATEGY_TYPE$'
BASES = {'cta': BaseCtaStrategy, 'hft': BaseHftStrategy, 'sel': BaseSelStrategy}
ENGINES = {'cta': EngineType.ET_CTA, 'hft': EngineType.ET_HFT, 'sel': EngineType.ET_SEL}
def stop(signum, frame):
    stopped.set()
signal.signal(signal.SIGTERM, stop)
def main():
    global engine
    base = BASES[STRATEGY_TYPE]
    candidates = [v for v in vars(MyStrategy).values() if inspect.isclass(v) and not v.__name__.startswith('_')
                  and v.__module__ == MyStrategy.__name__ and issubclass(v, base) and v is not base]
    if len(candidates) != 1:
        raise ValueError('Strategy module must define exactly one ' + base.__name__ + ' subclass')
    engine = WtBtEngine(ENGINES[STRATEGY_TYPE], 'logcfgbt.json')
    try:
        engine.init('$BASEFILES$', 'configbt.json')
        engine.configBacktest($FROMTIME$, $ENDTIME$)
        engine.commitBTConfig()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        strategy = candidates[0](name='$STRAID$')
        sel_state = {'code': getattr(strategy, 'code', ''), 'period': getattr(strategy, 'period', ''),
                     'stime': $FROMTIME$, 'etime': $ENDTIME$, 'progress': 0, 'elapse': 0,
                     'scheduled_count': 0, 'state_source': 'python_sel_runner', 'progress_kind': 'completion'}
        began = time.monotonic()

        if STRATEGY_TYPE == 'cta':
            engine.set_cta_strategy(strategy, slippage=$SLIPPAGE$, fill_model='$FILL_MODEL$',
                                    event_delay=$EVENT_DELAY$, participation_rate=$PARTICIPATION_RATE$)
        elif STRATEGY_TYPE == 'hft':
            engine.set_hft_strategy(strategy)
        else:
            schedule = getattr(strategy, 'schedule', {'date': 0, 'time': 930, 'period': 'd', 'trdtpl': 'CHINA', 'session': 'TRADING'})
            original_calculate = strategy.on_calculate
            def calculate(context):
                original_calculate(context)
                sel_state['scheduled_count'] += 1
                sel_state['last_calculation'] = int(context.stra_get_date()) * 10000 + int(context.stra_get_time())
            strategy.on_calculate = calculate
            engine.set_sel_strategy(strategy, slippage=$SLIPPAGE$, **schedule)
        errors = []
        def replay():
            try:
                engine.run_backtest(False)
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()
        worker = threading.Thread(target=replay, name='backtest')
        worker.start()
        try:
            while not finished.wait(0.05):
                engine.raise_callback_error()
                if stopped.is_set():
                    engine.stop_backtest()
                    raise KeyboardInterrupt
            if stopped.is_set():
                raise KeyboardInterrupt
            if errors:
                raise errors[0]
        finally:
            engine.stop_backtest()
            worker.join()
        output = Path('outputs_bt') / '$STRAID$'
        if STRATEGY_TYPE == 'sel':
            # SelMocker has no native BT_STATE/btenv output. Publish completion only after
            # an actual schedule callback and nonempty daily funds output have succeeded.
            fund_lines = (output/'funds.csv').read_text().splitlines() if (output/'funds.csv').exists() else []
            if not sel_state['scheduled_count'] or len(fund_lines) <= 1:
                raise RuntimeError('SEL replay has no scheduled calculation or daily funds; check schedule/data range')
            sel_state['progress'] = 100
            sel_state['elapse'] = round((time.monotonic() - began) * 1000)
            state_path = output/'btenv.json'
            temporary = state_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(sel_state), encoding='utf-8')
            temporary.replace(state_path)
        state = json.loads((output/'btenv.json').read_text())
        if state.get('progress', 0) < 100 or not (output/'funds.csv').exists():
            raise RuntimeError('Replay did not finish or no funds output was produced; check subscriptions and available data')
        analyst = WtBtAnalyst()
        analyst.add_strategy('$STRAID$', folder='./outputs_bt/', init_capital=$CAPITAL$, rf=0.02, annual_trading_days=240)
        analyst.run_simple()
    finally:
        engine.stop_backtest()
        engine.release_backtest()
        print('BACKTEST_RELEASE_COMPLETE', flush=True)
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
