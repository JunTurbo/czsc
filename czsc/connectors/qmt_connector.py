# -*- coding: utf-8 -*-
"""
author: zengbin93
email: zeng_bin8888@163.com
create_dt: 2022/12/31 16:03
describe: QMT 量化交易平台接口
"""
import time
import random
import pandas as pd
from typing import List
from tqdm import tqdm
from loguru import logger
from deprecated import deprecated
from datetime import datetime, timedelta
from czsc.objects import Freq, RawBar
from czsc.fsa.im import IM
from czsc.traders.base import CzscTrader
from czsc.utils import resample_bars
from xtquant import xtconstant
from xtquant import xtdata
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount


def format_stock_kline(kline: pd.DataFrame, freq: Freq) -> List[RawBar]:
    """QMT A股市场K线数据转换

    :param kline: QMT 数据接口返回的K线数据
                         time   open   high    low  close  volume      amount  \
        0 2022-12-01 10:15:00  13.22  13.22  13.16  13.18   20053  26432861.0
        1 2022-12-01 10:20:00  13.18  13.19  13.15  13.15   32667  43002512.0
        2 2022-12-01 10:25:00  13.16  13.18  13.13  13.16   32466  42708049.0
        3 2022-12-01 10:30:00  13.16  13.19  13.13  13.18   15606  20540461.0
        4 2022-12-01 10:35:00  13.20  13.25  13.19  13.22   29959  39626170.0
              symbol
        0  000001.SZ
        1  000001.SZ
        2  000001.SZ
        3  000001.SZ
        4  000001.SZ
    :param freq: K线周期
    :return: 转换好的K线数据
    """
    bars = []
    dt_key = 'time'
    kline = kline.sort_values(dt_key, ascending=True, ignore_index=True)
    records = kline.to_dict('records')

    for i, record in enumerate(records):
        # 将每一根K线转换成 RawBar 对象
        bar = RawBar(symbol=record['symbol'], dt=pd.to_datetime(record[dt_key]), id=i, freq=freq,
                     open=record['open'], close=record['close'], high=record['high'], low=record['low'],
                     vol=record['volume'] * 100 if record['volume'] else 0,  # 成交量，单位：股
                     amount=record['amount'] if record['amount'] > 0 else 0,  # 成交额，单位：元
                     )
        bars.append(bar)
    return bars


def get_kline(symbol, period, start_time, end_time, count=-1, dividend_type='front_ratio', **kwargs):
    """获取 QMT K线数据，实盘、回测通用

    :param symbol: 股票代码 例如：300001.SZ
    :param period: 周期 分笔"tick" 分钟线"1m"/"5m" 日线"1d"
    :param start_time: 开始时间
    :param end_time: 结束时间
    :param count: 数量 -1全部，n: 从结束时间向前数n个
    :param dividend_type: 除权类型"none" "front" "back" "front_ratio" "back_ratio"

    :return: df Dataframe格式的数据，样例如下
                         time   open   high    low  close  volume      amount  \
        0 2022-12-01 10:15:00  13.22  13.22  13.16  13.18   20053  26432861.0
        1 2022-12-01 10:20:00  13.18  13.19  13.15  13.15   32667  43002512.0
        2 2022-12-01 10:25:00  13.16  13.18  13.13  13.16   32466  42708049.0
        3 2022-12-01 10:30:00  13.16  13.19  13.13  13.18   15606  20540461.0
        4 2022-12-01 10:35:00  13.20  13.25  13.19  13.22   29959  39626170.0
              symbol
        0  000001.SZ
        1  000001.SZ
        2  000001.SZ
        3  000001.SZ
        4  000001.SZ
    """
    start_time = pd.to_datetime(start_time).strftime('%Y%m%d%H%M%S')
    end_time = pd.to_datetime(end_time).strftime('%Y%m%d%H%M%S')
    if kwargs.get("download_hist", True):
        xtdata.download_history_data(symbol, period=period, start_time=start_time, end_time=end_time)

    field_list = ['time', 'open', 'high', 'low', 'close', 'volume', 'amount']
    data = xtdata.get_market_data(field_list, stock_list=[symbol], period=period, count=count,
                                  dividend_type=dividend_type, start_time=start_time,
                                  end_time=end_time, fill_data=kwargs.get("fill_data", False))

    df = pd.DataFrame({key: value.values[0] for key, value in data.items()})
    df['time'] = pd.to_datetime(df['time'], unit='ms') + pd.to_timedelta('8H')
    df.reset_index(inplace=True, drop=True)
    df['symbol'] = symbol
    df = df.dropna()

    if kwargs.get("df", True):
        return df
    else:
        freq_map = {"1m": Freq.F1, "5m": Freq.F5, "1d": Freq.D}
        return format_stock_kline(df, freq=freq_map[period])


def get_raw_bars(symbol, freq, sdt, edt, fq='前复权', **kwargs):
    """获取 CZSC 库定义的标准 RawBar 对象列表

    :param symbol: 标的代码
    :param freq: 周期
    :param sdt: 开始时间
    :param edt: 结束时间
    :param fq: 除权类型
    :param kwargs:
    :return:
    """
    freq = Freq(freq)
    if freq == Freq.F1:
        period = '1m'
    elif freq in [Freq.F5, Freq.F15, Freq.F30, Freq.F60]:
        period = '5m'
    else:
        period = '1d'

    if fq == '前复权':
        dividend_type = 'front_ratio'
    elif fq == '后复权':
        dividend_type = 'back_ratio'
    else:
        assert fq == '不复权'
        dividend_type = 'none'

    kline = get_kline(symbol, period, sdt, edt, dividend_type=dividend_type,
                      download_hist=kwargs.get("download_hist", True), df=True)
    kline['dt'] = pd.to_datetime(kline['time'])
    kline['vol'] = kline['volume']
    bars = resample_bars(kline, freq, raw_bars=True)
    return bars


def get_symbols(step):
    """获取择时策略投研不同阶段对应的标的列表

    :param step: 投研阶段
    :return: 标的列表
    """
    stocks = xtdata.get_stock_list_in_sector('沪深A股')
    stocks_map = {
        "index": ['000905.SH', '000016.SH', '000300.SH', '000001.SH', '000852.SH',
                  '399001.SZ', '399006.SZ', '399376.SZ', '399377.SZ', '399317.SZ', '399303.SZ'],
        "stock": stocks,
        "check": ['000001.SZ'],
        "train": stocks[:200],
        "valid": stocks[200:600],
        "etfs": ['512880.SH', '518880.SH', '515880.SH', '513050.SH', '512690.SH',
                 '512660.SH', '512400.SH', '512010.SH', '512000.SH', '510900.SH',
                 '510300.SH', '510500.SH', '510050.SH', '159992.SZ', '159985.SZ',
                 '159981.SZ', '159949.SZ', '159915.SZ'],
    }
    return stocks_map[step]


class TraderCallback(XtQuantTraderCallback):
    """基础回调类，主要是一些日志和IM通知功能"""
    def __init__(self, **kwargs):
        super(TraderCallback, self).__init__()
        self.kwargs = kwargs

        if kwargs.get('feishu_app_id', None) and kwargs.get('feishu_app_secret', None):
            self.im = IM(app_id=kwargs['feishu_app_id'], app_secret=kwargs['feishu_app_secret'])
            self.members = kwargs['feishu_members']
        else:
            self.im = None
            self.members = None

        file_log = kwargs.get('file_log', None)
        if file_log:
            logger.add(file_log, rotation='1 day', encoding='utf-8', enqueue=True)
        self.file_log = file_log
        logger.info(f"TraderCallback init: {kwargs}")

    def on_disconnected(self):
        """连接断开"""
        logger.info("connection lost")

    def on_stock_order(self, order):
        """委托回报推送

        :param order: XtOrder对象
        """
        logger.info(f"on order callback: {order.stock_code} {order.order_status} {order.order_sysid}")

    def on_stock_asset(self, asset):
        """资金变动推送

        :param asset: XtAsset对象
        """
        logger.info(f"on asset callback: {asset.account_id} {asset.cash} {asset.total_asset}")

    def on_stock_trade(self, trade):
        """成交变动推送

        :param trade: XtTrade对象
        """
        logger.info(f"on trade callback: {trade.account_id} {trade.stock_code} {trade.order_id}")

    def on_stock_position(self, position):
        """持仓变动推送

        :param position: XtPosition对象
        """
        logger.info(f"on position callback: {position.stock_code} {position.volume}")

    def on_order_error(self, order_error):
        """委托失败推送

        :param order_error:XtOrderError 对象
        """
        logger.info(f"on order_error callback: {order_error.order_id} {order_error.error_id} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        """撤单失败推送

        :param cancel_error: XtCancelError 对象
        """
        logger.info(f"{cancel_error.order_id} {cancel_error.error_id} {cancel_error.error_msg}")

    def on_order_stock_async_response(self, response):
        """异步下单回报推送

        :param response: XtOrderResponse 对象
        """
        logger.info(f"on_order_stock_async_response: {response.order_id} {response.seq}")

    def on_account_status(self, status):
        """账户状态变化推送

        :param status: XtAccountStatus 对象
        """
        logger.info(f"on_account_status: {status.account_id} {status.account_type} {status.status}")


class QmtTradeManager:
    """QMT交易管理器"""

    def __init__(self, mini_qmt_dir, account_id, **kwargs):
        """

        :param mini_qmt_dir: mini QMT 路径；如 D:\\国金QMT交易端模拟\\userdata_mini
        :param account_id: 账户ID
        :param kwargs:

        """
        self.symbols = kwargs.get('symbols', [])  # 交易标的列表
        self.strategy = kwargs.get('strategy', [])  # 交易策略
        self.symbol_max_pos = kwargs.get('symbol_max_pos', 0.5)  # 每个标的最大持仓比例
        self.trade_sdt = kwargs.get('trade_sdt', '20220601')     # 交易跟踪开始日期
        self.mini_qmt_dir = mini_qmt_dir
        self.account_id = account_id
        self.base_freq = self.strategy(symbol='symbol').sorted_freqs[0]
        self.delta_days = int(kwargs.get('delta_days', 1))  # 定时执行获取的K线天数

        self.session = random.randint(10000, 20000)
        self.xtt = XtQuantTrader(mini_qmt_dir, session=self.session, callback=TraderCallback())
        self.acc = StockAccount(account_id, 'STOCK')
        self.xtt.start()
        self.xtt.connect()
        assert self.xtt.connected, "交易服务器连接失败"
        _res = self.xtt.subscribe(self.acc)
        assert _res == 0, "账号订阅失败"
        self.traders = self.__create_traders(**kwargs)

    def __create_traders(self, **kwargs):
        """创建交易策略"""
        traders = {}
        for symbol in tqdm(self.symbols, desc="创建交易对象", unit="个"):
            try:
                bars = get_raw_bars(symbol, self.base_freq, '20180101', datetime.now(), fq="前复权", download_hist=True)

                trader: CzscTrader = self.strategy(symbol=symbol).init_trader(bars, sdt=self.trade_sdt)
                traders[symbol] = trader
                pos_info = {x.name: x.pos for x in trader.positions}
                logger.info(f"{symbol} trader pos：{pos_info} | ensemble_pos: {trader.get_ensemble_pos('mean')}")
            except Exception as e:
                logger.exception(f'创建交易对象失败，symbol={symbol}, e={e}')
        return traders

    def get_assets(self):
        """获取账户资产"""
        return self.xtt.query_stock_asset(self.acc)

    def query_stock_orders(self, cancelable_only=False):
        """查询股票市场的委托单

        http://docs.thinktrader.net/pages/ee0e9b/#%E5%A7%94%E6%89%98%E6%9F%A5%E8%AF%A2

        :param cancelable_only:
        :return:
        """
        return self.xtt.query_stock_orders(self.acc, cancelable_only)

    def is_order_exist(self, symbol, order_type, volume=None):
        """判断是否存在相同的委托单"""
        orders = self.query_stock_orders(cancelable_only=True)
        for o in orders:
            if o.stock_code == symbol and o.order_type == order_type:
                if not volume or o.order_volume == volume:
                    return True
        return False

    def is_allow_open(self, symbol, price):
        """判断是否允许开仓

        :param symbol: 股票代码
        :param price: 股票现价
        :return: True 允许开仓，False 不允许开仓
        """
        # 如果 未成交的开仓委托单 存在，不允许开仓
        if self.is_order_exist(symbol, order_type=23):
            logger.warning(f"存在未成交的开仓委托单，symbol={symbol}")
            return False

        # 如果 symbol_max_pos 为 0，不允许开仓
        if self.symbol_max_pos <= 0:
            return False

        # 如果已经有持仓，不允许开仓
        if self.query_stock_positions().get(symbol, None):
            return False

        # 如果资金不足，不允许开仓
        assets = self.get_assets()
        if assets.cash < price * 120:
            logger.warning(f"资金不足，无法开仓，symbol={symbol}")
            return False

        return True

    def query_stock_positions(self):
        """查询股票市场的持仓单

        http://docs.thinktrader.net/pages/ee0e9b/#%E6%8C%81%E4%BB%93%E6%9F%A5%E8%AF%A2
        """
        res = self.xtt.query_stock_positions(self.acc)
        if len(res) > 0:
            res = {x.stock_code: x for x in res}
        else:
            res = {}
        return res

    def send_stock_order(self, **kwargs):
        """股票市场交易下单

        http://docs.thinktrader.net/pages/ee0e9b/#%E8%82%A1%E7%A5%A8%E5%90%8C%E6%AD%A5%E6%8A%A5%E5%8D%95
        http://docs.thinktrader.net/pages/198696/#%E6%8A%A5%E4%BB%B7%E7%B1%BB%E5%9E%8B-price-type

        stock_code: 证券代码, 例如"600000.SH"
        order_type: 委托类型, 23:买, 24:卖
        order_volume: 委托数量, 股票以'股'为单位, 债券以'张'为单位
        price_type: 报价类型, 详见帮助手册
            xtconstant.LATEST_PRICE	5	最新价
            xtconstant.FIX_PRICE	11	限价
        price: 报价价格, 如果price_type为限价, 那price为指定的价格, 否则填0
        strategy_name: 策略名称
        order_remark: 委托备注

        :return: 返回下单请求序号, 成功委托后的下单请求序号为大于0的正整数, 如果为-1表示委托失败
        """
        stock_code = kwargs.get('stock_code')
        order_type = kwargs.get('order_type')
        order_volume = kwargs.get('order_volume')
        price_type = kwargs.get('price_type', xtconstant.LATEST_PRICE)
        price = kwargs.get('price', 0)
        strategy_name = kwargs.get('strategy_name', "程序下单")
        order_remark = kwargs.get('order_remark', "程序下单")

        if not self.xtt.connected:
            self.xtt.connect()
            self.xtt.start()

        if order_volume % 100 != 0:
            order_volume = order_volume // 100 * 100

        assert self.xtt.connected, "交易服务器连接断开"
        _id = self.xtt.order_stock(self.acc, stock_code, order_type, order_volume,
                                   price_type, price, strategy_name, order_remark)
        return _id

    def update_traders(self):
        """更新交易策略"""
        holds = self.query_stock_positions()
        kline_sdt = datetime.now() - timedelta(days=self.delta_days)

        for symbol in self.traders.keys():
            try:
                trader = self.traders[symbol]
                bars = get_raw_bars(symbol, self.base_freq, kline_sdt, datetime.now(), fq="前复权", download_hist=True)

                news = [x for x in bars if x.dt > trader.end_dt]
                if news:
                    logger.info(f"{symbol} 需要更新的K线数量：{len(news)} | 最新的K线时间是 {news[-1].dt}")
                    for bar in news:
                        trader.on_bar(bar)

                    # 根据策略的交易信号，下单【股票只有多头】
                    if trader.get_ensemble_pos(method='vote') == 1 and self.is_allow_open(symbol, price=news[-1].close):
                        assets = self.get_assets()
                        order_volume = min(self.symbol_max_pos * assets.total_asset, assets.cash) // news[-1].close
                        self.send_stock_order(stock_code=symbol, order_type=23, order_volume=order_volume)

                    # 平多头
                    if trader.get_ensemble_pos(method='vote') == 0 and symbol in holds.keys():
                        order_volume = holds[symbol].can_use_volume
                        self.send_stock_order(stock_code=symbol, order_type=24, order_volume=order_volume)

                    # 更新交易对象
                    self.traders[symbol] = trader
                else:
                    logger.info(f"{symbol} 没有需要更新的K线，最新的K线时间是 {trader.end_dt}")

                pos_info = {x.name: x.pos for x in trader.positions}
                logger.info(f"{symbol} trader pos：{pos_info} | ensemble_pos: {trader.get_ensemble_pos('mean')}")

            except Exception as e:
                logger.error(f"{symbol} 更新交易策略失败，原因是 {e}")

    def run(self, mode='30m'):
        """运行策略"""
        if mode.lower() == '15m':
            _times = ["09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
                      "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00"]
        elif mode.lower() == '30m':
            _times = ["09:45", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
        elif mode.lower() == '60m':
            _times = ["10:30", "11:30", "13:45", "14:30"]
        else:
            raise ValueError("mode 只能是 15m, 30m, 60m")

        while 1:
            if datetime.now().strftime("%H:%M") in _times:
                self.update_traders()
            else:
                time.sleep(3)

            # 如果断开，重新连接交易服务器
            if not self.xtt.connected:
                self.xtt.connect()
                self.xtt.start()


def test_get_kline():
    # 获取所有板块
    slt = xtdata.get_sector_list()
    stocks = xtdata.get_stock_list_in_sector('沪深A股')

    df = get_kline(symbol='000001.SZ', period='1m', count=1000, dividend_type='front',
                   start_time='20200427', end_time='20221231')
    assert not df.empty
    df = get_kline(symbol='000001.SZ', period='5m', count=1000, dividend_type='front',
                   start_time='20200427', end_time='20221231')
    assert not df.empty
    df = get_kline(symbol='000001.SZ', period='1d', count=1000, dividend_type='front',
                   start_time='20200427', end_time='20221231')
    assert not df.empty


def test_get_symbols():
    symbols = get_symbols('index')
    assert len(symbols) > 0
    symbols = get_symbols('stock')
    assert len(symbols) > 0
    symbols = get_symbols('check')
    assert len(symbols) > 0
    symbols = get_symbols('train')
    assert len(symbols) > 0
    symbols = get_symbols('valid')
    assert len(symbols) > 0
    symbols = get_symbols('etfs')
    assert len(symbols) > 0

