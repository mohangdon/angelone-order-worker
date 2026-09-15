from typing import Literal
from pydantic import BaseModel, Field


class Contract(BaseModel):
    underlying: str
    expiry: str
    strike: float
    option: Literal["CE", "PE"]
    exchange: Literal["NFO", "BFO"]


class PlaceOrderRequest(BaseModel):
    command_id: str = Field(min_length=8, max_length=100)
    copy_trade_id: str = Field(min_length=4, max_length=100)
    contract: Contract
    transaction_type: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT", "STOPLOSS_LIMIT", "STOPLOSS_MARKET"] = "MARKET"
    product_type: Literal["INTRADAY", "CARRYFORWARD"] = "CARRYFORWARD"
    price: float = 0
    trigger_price: float = 0
    tag: str = "copytrade"


class ModifyOrderRequest(BaseModel):
    command_id: str = Field(min_length=8, max_length=100)
    quantity: int = Field(gt=0)
    order_type: Literal["LIMIT", "STOPLOSS_LIMIT", "STOPLOSS_MARKET"]
    price: float = 0
    trigger_price: float = 0


class CommandRequest(BaseModel):
    command_id: str = Field(min_length=8, max_length=100)


class ProtectionRequest(BaseModel):
    command_id: str = Field(min_length=8, max_length=100)
    contract: Contract
    transaction_type: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    sl_price: float = 0
    target_price: float = 0


class ExitTradeRequest(BaseModel):
    command_id: str = Field(min_length=8, max_length=100)
    contract: Contract
    transaction_type: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
