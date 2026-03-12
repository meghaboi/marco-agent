import logging

from marco_agent.observability import CorrelationIdFilter


def configure_logging(
    *,
    level: int = logging.INFO,
    appinsights_connection_string: str | None = None,
) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | corr=%(correlation_id)s | %(message)s",
    )
    root = logging.getLogger()
    root.addFilter(CorrelationIdFilter())

    if not appinsights_connection_string:
        return
    try:
        from opencensus.ext.azure.log_exporter import AzureLogHandler

        handler = AzureLogHandler(connection_string=appinsights_connection_string)
        handler.setLevel(level)
        root.addHandler(handler)
    except Exception:
        logging.getLogger(__name__).exception("Failed to initialize App Insights logging handler.")
