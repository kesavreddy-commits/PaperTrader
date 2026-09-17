"""UI controllers: glue objects that live between the widgets and the layers
below them. Currently just the background market-data feed thread.
"""

from .data_feed import DataFeed

__all__ = ["DataFeed"]
