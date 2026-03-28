import logging

from marco_agent.observability import CorrelationIdFilter


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | corr=%(correlation_id)s | %(message)s"


def configure_logging(
    *,
    level: int = logging.INFO,
    appinsights_connection_string: str | None = None,
) -> None:
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
    )
    root = logging.getLogger()
    cid_filter = CorrelationIdFilter()
    root.addFilter(cid_filter)
    formatter = logging.Formatter(LOG_FORMAT, defaults={"correlation_id": "-"})
    for handler in root.handlers:
        handler.addFilter(cid_filter)
        handler.setFormatter(formatter)

    if not appinsights_connection_string:
        return
    try:
        from opencensus.ext.azure.log_exporter import AzureLogHandler

        handler = AzureLogHandler(connection_string=appinsights_connection_string)
        handler.setLevel(level)
        handler.addFilter(cid_filter)
        root.addHandler(handler)
    except Exception:
        logging.getLogger(__name__).exception("Failed to initialize App Insights logging handler.")
