import math
import numpy as np
from numpy import ndarray
from pams.agents import Agent
from pams.logs import Logger
from pams.market import Market
from pams.order import Cancel
from pams.order import LIMIT_ORDER
from pams.order import MARKET_ORDER
from pams.order import Cancel
from pams.order import Order
from pams.order import OrderKind
from pams.simulator import Simulator
from pams.utils import JsonRandom
from scipy import optimize
from typing import Any, Optional
from typing import TypeVar
import random
import warnings

AgentID = TypeVar("AgentID")
MarketID = TypeVar("MarketID")

class aFCNAgent(Agent):
    """asymmetric FCN Agent (aFCNAgent) class

    aFCNAgent's order decision mechanism is mostly based on Chiarella et al., 2008.
    The difference from the paper mentiond above is that aFCNAgent's chart/noise weight change through time
    by parameters "feedbackAsymmetry" and "noiseAsymmetry".

    References:
        - Chiarella, C., Iori, G., & Perello, J. (2009). The impact of heterogeneous trading rules
        on the limit order book and order flows,
        Journal of Economic Dynamics and Control, 33 (3), 525-537. https://doi.org/10.1016/j.jedc.2008.08.001

    note: The original paper consider market order, but aFCNAgent here is not allowed to submit market order
        because it unstabilize the simulation due to the specification of PAMS.
    """
    def __init__(
        self,
        agent_id: AgentID,
        prng: random.Random,
        simulator: Simulator,
        name: str,
        logger: Optional[Logger]
    ) -> None:
        super().__init__(agent_id, prng, simulator, name, logger)

    def is_finite(self, x: float) -> bool:
        """determine if it is a valid value.

        Args:
            x (float): value.

        Returns:
            bool: whether or not it is a valid (not NaN, finite) value.
        """
        return not math.isnan(x) and not math.isinf(x)

    def setup(
        self,
        settings: dict[str, Any],
        accessible_markets_ids: list[MarketID],
        *args: Any,
        **kwargs: Any
    ) -> None:
        """agent setup. Usually be called from simulator / runner automatically.

        Args:
            settings (dict[str, Any]): agent configuration. Thie must include the parameters:
                - fundamentalWeight: weight given to the fundamentalist component.
                - chartWeight: weight given to the chartist component.
                - feedbackAsymmetry: feedback asymmetry.
                    Chart weight is amplified when the observed stock return is negative
                    by feedbackAsymmetry coefficient.
                - noiseWeight: weight given to the chartist component.
                - noiseAsymmetry: noise asymmetry.
                    Noise weight is amplified when the observed stock return is negative
                    by noiseAsymmetry coefficient.
                - noiseScale: the scale of noise component.
                - timeWindowSize: time horizon.
                - riskAversionTerm: reference level of risk aversion.
                    The precise relative risk aversion coefficient is calculated
                    by using fundamental/chart weights.
                and can include
                - meanReversionTime: time scale over which
                    the fundamentalist component for the mean reversion of the price to the fundamental.
            accessible_market_ids (list[MarketID]): _description_

        If feedbackAsymmetry and noiseAsymmetry are both 0, aFCNAgent is equivalent to FCNAgent.
        """
        super().setup(
            settings=settings, accessible_markets_ids=accessible_markets_ids
        )
        if 2 <= len(accessible_markets_ids):
            warnings.warn(
                "order decision for multiple assets has not implemented yet."
            )
        json_random: JsonRandom = JsonRandom(prng=self.prng)
        self.w_f: float = json_random.random(json_value=settings["fundamentalWeight"])
        self.w_c: float = json_random.random(json_value=settings["chartWeight"])
        self.a_feedback: float = json_random.random(
            json_value=settings["feedbackAsymmetry"]
        )
        self.w_n: float = json_random.random(json_value=settings["noiseWeight"])
        self.a_noise: float = json_random.random(
            json_value=settings["noiseAsymmetry"]
        )
        self.noise_scale: float = json_random.random(json_value=settings["noiseScale"])
        self.time_window_size = int(
            json_random.random(json_value=settings["timeWindowSize"])
        )
        self.risk_aversion_term: float = json_random.random(
            json_value=settings["riskAversionTerm"]
        )
        if "meanReversionTime" in settings:
            self.mean_reversion_time: int = int(
                json_random.random(json_value=settings["meanReversionTime"])
            )
        else:
            self.mean_reversion_time: int = self.time_window_size
        self.unexecuted_orders: list[Order] = []

    def submit_orders(
        self, markets: list[Market]
    ) -> list[Order | Cancel]:
        """submit orders based on aFCN-based calculation.
        """
        orders: list[Order | Cancel] = sum(
            [
                self.submit_orders_by_market(market=market) for market in markets
            ], []
        )
        return orders

    def submit_orders_by_market(self, market: Market) -> list[Order | Cancel]:
        """submit orders by market (internal usage).

        aFCNAgent submit orders by following procedure.
            1. cancel orders remaining unexecuted in the market.
            2. calculate temporal FCN weights and time window size.
            3. calculate expected future price by FCN rule.
            4. calculate expected volatility and temporal risk aversion.
            5. create new order using the demand function induced from CARA utility.

        Args:
            market (Market): market to order.

        Returns:
            orders (list[Order | Cancel]): order list
        """
        orders: list[Order | Cancel] = []
        if not self.is_market_accessible(market_id=market.market_id):
            return orders
        orders.extend(self._cancel_orders())
        time: int = market.get_time()
        time_window_size: int = min(time, self.time_window_size)
        weights: list[float] = self._calc_weights(market, time_window_size)
        fundamental_weight: float = weights[0]
        chart_weight: float = weights[1]
        noise_weight: float = weights[2]
        assert 0 <= fundamental_weight
        assert 0 <= chart_weight
        assert 0 <= noise_weight
        time_window_size: int = self._calc_temporal_time_window_size(
            time_window_size, time, fundamental_weight, chart_weight
        )
        risk_aversion_term: float = self._calc_temporal_risk_aversion_term(
            fundamental_weight, chart_weight
        )
        assert 0 <= time_window_size
        assert 0 < risk_aversion_term
        expected_future_price: float = self._calc_expected_future_price(
            market, fundamental_weight, chart_weight, noise_weight, time_window_size
        )
        assert self.is_finite(expected_future_price)
        expected_volatility: float = self._calc_expected_volatility(
            market, time_window_size
        )
        assert self.is_finite(expected_volatility)
        orders.extend(
            self._create_order(
                market, expected_future_price, expected_volatility, risk_aversion_term
            )
        )
        return orders

    def _calc_weights(
        self,
        market: Market,
        time_window_size: int
    ) -> list[float]:
        """calculate temporal FCN weights.

        Chartist component in FCNAgent can be regarded as positive feedback trader. Also, noise component is
        noise trader. feedback/noise traders are thought to be the factor to cause asymmetric volatility change
        because they tend to react more to the decline of stock price than price rising phase.
        aFCNAgent is implemented to reproduce this stylized fact
        by amplifying the chart/noise weight when market price is declining.

        Args:
            market (Market): market to order.
            time_window_size (int): time window size

        Returns:
            weights(list[float]): weights list. [fundamental weight, chartist weight, noise weight]
        """
        time: int = market.get_time()
        market_price: float = market.get_market_price()
        chart_scale: float = 1.0 / max(time_window_size, 1)
        chart_log_return: float = chart_scale * 100 * math.log(
            market_price / market.get_market_price(time - time_window_size)
        )
        chart_weight: float = max(
            0, self.w_c - min(0, self.a_feedback * chart_log_return)
        )
        noise_weight: float = max(
            0, self.w_n - min(0, self.a_noise * chart_log_return)
        )
        weights: list[float] = [self.w_f, chart_weight, noise_weight]
        return weights

    def _calc_temporal_time_window_size(
        self,
        time_window_size: int,
        time: int,
        fundamental_weight: float,
        chart_weight: float
    ) -> int:
        """calculate time window size.

        Assume that agent time horizon depends on its charactetistics. In detail, agent who emphasize
        fundamentalist strategy typically have longer time horizon. On the other hand, agent mostly rely on
        chartist strategy, referring short term price fluctuation, tend to have shorter time horizon.

        Args:
            time_window_size (int): time horizon.
            time (int): market time.
            fundamental_weight (float): reference level of the agent's fundamental weight.
            chart_weight (float): reference level of the agent's chart weight.

        Returns:
            temporal_time_window_size (int): calculated the agent's temporal time horizon.
        """
        temporal_time_window_size: int = min(time, int(
            time_window_size * (1 + fundamental_weight) / (1 + chart_weight)
        ))
        return temporal_time_window_size

    def _calc_temporal_risk_aversion_term(
        self,
        fundamental_weight: float,
        chart_weight: float
    ) -> float:
        """calculate temporal relative risk aversion term in CARA utility.

        Args:
            fundamental_weight (float): temporal fundamental weight.
            chart_weight (float): temporal chart weight.

        Returns:
            risk_aversion_term (float): calculated the agent's temporal risk aversion term.
        """
        risk_aversion_term: float = self.risk_aversion_term * (
            (1 + fundamental_weight) / (1 + chart_weight)
        )
        return risk_aversion_term

    def _calc_expected_future_price(
        self,
        market: Market,
        fundamental_weight: float,
        chart_weight: float,
        noise_weight: float,
        time_window_size: int
    ) -> float:
        """calculate expected future price by FCN rule.

        ..seealso:
            - :func: `pams.agents.FCNAgent.submit_orders_by_market'
        """
        time: int = market.get_time()
        market_price: float = market.get_market_price()
        fundamental_price: float = market.get_fundamental_price()
        fundamental_scale: float = 1.0 / max(self.mean_reversion_time, 1)
        fundamental_log_return: float = fundamental_scale * math.log(
            fundamental_price / market_price
        )
        assert self.is_finite(fundamental_log_return)
        chart_scale: float = 1.0 / max(time_window_size, 1)
        chart_log_return: float = chart_scale * math.log(
            market_price / market.get_market_price(time - time_window_size)
        )
        assert self.is_finite(chart_log_return)
        noise_log_return: float = self.noise_scale * self.prng.gauss(mu=0.0, sigma=1.0)
        assert self.is_finite(noise_log_return)
        expected_log_return: float = (
            1.0 / (fundamental_weight + chart_weight + noise_weight)
        ) * (
            fundamental_weight * fundamental_log_return
            + chart_weight * chart_log_return
            + noise_weight * noise_log_return
        )
        assert self.is_finite(expected_log_return)
        expected_future_price: float = market_price * math.exp(
            expected_log_return * self.time_window_size
        )
        return expected_future_price

    def _calc_expected_volatility(
        self,
        market: Market,
        time_window_size: int
    ) -> float:
        """calculate expected volatility.

        aFCNAgent estimate volatility as the variabce of past log returns.
        If order execution is not allowed in current session, the market price never changes.
        In such a case, replace expected volatility to sufficientlly small value: 1e-10.

        Args:
            market (Market): market to order.
            time_window_size (int): time horizon.

        Returns:
            float: expectef volatility
        """
        time: int = market.get_time()
        market_prices: list[float] = market.get_market_prices(
            range(time-time_window_size,time+1)
        )
        log_returns: ndarray = np.log(market_prices[1:]) - np.log(market_prices[:len(market_prices)-1])
        avg_log_return: float = np.sum(log_returns) / (time_window_size + 1e-10)
        expected_volatility: float = np.sum((log_returns - avg_log_return)**2) / (time_window_size + 1e-10)
        assert self.is_finite(expected_volatility)
        expected_volatility = max(1e-10, expected_volatility)
        return expected_volatility

    def _create_order(
        self,
        market: Market,
        expected_future_price: float,
        expected_volatility: float,
        risk_aversion_term: float
    ) -> list[Order | Cancel]:
        """create new orders.

        This method create new order according to the demand of the agent indiced from CARA utility
        in following procedure.
            1. estimate numerically the price level at which the agent is satisfied with the composition
                of his or her current portfolio.
            2. set the maximum selling price p_M at which demand(p_M) being 0 to ensure that
                short selling is not allowed.
            3. set the minimum buying price p_m at which p_m (demand(p_m) - current_stock_position) is equal to
                current cash position to impose budget constraint.
            4. Having determined the interval [p_m, p_M] in which the agent is willing to trade,
                randomly draw a price from the interval and decide order type and volume according to the demand.

        Args:
            market (Market): market to order.
            expected_future_price (float): expected future price.
            expected_volatility (float): expected volatility.
            risk_aversion_term (float): temporal risk aversion term.

        Returns:
            orders (list[Order | Cancel]): created orders to submit
        """
        asset_volume: int = self.get_asset_volume(market.market_id)
        cash_amount: float = self.get_cash_amount()
        lower_bound: float = 1e-10
        satisfaction_price: float = optimize.brentq(
            self._calc_additional_demand,
            a=lower_bound, b=expected_future_price,
            args=(expected_future_price, risk_aversion_term,
                expected_volatility, asset_volume)
        )
        max_sell_price: float = expected_future_price
        min_buy_price: float = optimize.brentq(
            self._calc_remaining_cash,
            a=lower_bound, b=satisfaction_price,
            args=(expected_future_price, risk_aversion_term,
                expected_volatility, asset_volume, cash_amount)
        )
        assert min_buy_price <= satisfaction_price
        assert satisfaction_price <= max_sell_price
        price: Optional[float] = self.prng.uniform(min_buy_price, max_sell_price)
        order_kind: OrderKind = LIMIT_ORDER
        if price < satisfaction_price:
            is_buy: bool = True
            best_sell_price: float = market.get_best_sell_price()
            if best_sell_price is None:
                best_sell_price = market.get_market_price()
            if best_sell_price < price:
                best_sell_price: float = np.clip(best_sell_price, min_buy_price, max_sell_price)
                price = best_sell_price
                demand: float = self._calc_demand(
                    best_sell_price , expected_future_price, risk_aversion_term, expected_volatility
                )
            else:
                demand: float = self._calc_demand(
                    price, expected_future_price, risk_aversion_term, expected_volatility
                )
            order_volume: int = int(demand - asset_volume)
        else:
            is_buy: bool = False
            best_buy_price: float = market.get_best_buy_price()
            if best_buy_price is None:
                best_buy_price = market.get_market_price()
            if price < best_buy_price:
                best_buy_price: float = np.clip(best_buy_price, min_buy_price, max_sell_price)
                price = best_buy_price
                demand: float = self._calc_demand(
                    best_buy_price, expected_future_price, risk_aversion_term, expected_volatility
                )
            else:
                demand: float = self._calc_demand(
                    price, expected_future_price, risk_aversion_term, expected_volatility
                )
            order_volume: int = int(asset_volume - demand)
        orders: list[Order | Cancel] = []
        if not order_volume == 0:
            orders: list[Order | Cancel] = [
                Order(
                    agent_id=self.agent_id,
                    market_id=market.market_id,
                    is_buy=is_buy,
                    kind=order_kind,
                    volume=order_volume,
                    price=price,
                    ttl=self.time_window_size
                )
            ]
            self.unexecuted_orders.extend(orders)
        return orders

    def _calc_demand(
        self,
        price: float,
        expected_future_price: float,
        risk_aversion_term: float,
        expected_volatility: float
    ) -> float:
        """demand function. D(price|expected_future_price, risk_aversion_term, expected_volatility)

        Args:
            price (float): price level at which the demand is calculated.
            expected_future_price (float): expected future price. constant variable.
            risk_aversion_term (float): temporal risk aversion term. constant variable.
            expected_volatility (float): expected_volatility. constant variable.

        Returns:
            demand (float): calculate demand.
        """
        demand: float = (
            np.log(expected_future_price / price)
        ) / (
            risk_aversion_term * expected_volatility * price
        )
        return demand

    def _calc_additional_demand(
        self,
        price: float,
        expected_future_price: float,
        risk_aversion_term: float,
        expected_volatility: float,
        asset_volume: int
    ) -> float:
        """calculate additional demand.

        Additional demand means the amount of stock that the agent is willing to buy (sell, if negative)
        at the given price level: D(price) - current_stock_position.

        Args:
            price (float): price level at which the additional demand is calculated.
            expected_future_price (float): expected future price. constant variable.
            risk_aversion_term (float): temporal risk aversion term. constant variable.
            expected_volatility (float): expected_volatility. constant variable.
            asset_volume (int): currently holding asset position.

        Returns:
            additional_demand (float): calculated additional demand.
        """
        demand: float = self._calc_demand(
            price, expected_future_price, risk_aversion_term, expected_volatility
        )
        additional_demand: float = demand - asset_volume
        return additional_demand

    def _calc_remaining_cash(
        self,
        price: float,
        expected_future_price: float,
        risk_aversion_term: float,
        expected_volatility: float,
        asset_volume: int,
        cash_amount: float
    ) -> float:
        """calculate remaining cash.

        remaining cash means the cash volume remained if the agent buy (additional_demand(price)) units of
        stocks at the given price level:
            current_cash_position - price * (D(price) - current_stock_position)

        Args:
            price (float): price level at which the remaining cash is calculated.
            expected_future_price (float): expected future price. constant variable.
            risk_aversion_term (float): temporal risk aversion term. constant variable.
            expected_volatility (float): expected_volatility. constant variable.
            asset_volume (int): currently holding asset position.
            cash_amount (float): currently holding cash position.

        Returns:
            remaining_cash (float): calculated remaining cash.
        """
        buying_price: float = price * (
            self._calc_demand(
                price, expected_future_price, risk_aversion_term, expected_volatility
            ) - asset_volume
        )
        remaining_cash: float = cash_amount - buying_price
        return remaining_cash

    def _cancel_orders(self) -> list[Cancel]:
        """cancel orders remaining unexecuted in the market.
        """
        cancels: list[Cancel] = []
        for order in self.unexecuted_orders:
            if not order.volume == 0:
                cancels.append(Cancel(order))
        if len(cancels) == 0:
            self.unexecuted_orders = []
        return cancels