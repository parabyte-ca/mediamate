import logging

logger = logging.getLogger(__name__)


def notify(apprise_url: str, title: str, body: str, url: str = "/activity",
           watchlist_id: int | None = None) -> None:
    """Send a notification everywhere the user asked for one: Apprise (when a URL is
    configured) and devices subscribed to push notifications. With a watchlist item,
    only the devices of the people following it get the push; without one (system
    alerts) the owner's do (services/people.py). Unlinked devices get everything."""
    try:
        from .webpush import send_all_async

        send_all_async(title, body, url, watchlist_id=watchlist_id, system=watchlist_id is None)
    except Exception as e:
        logger.error("Push notification failed: %s", e)
    if not apprise_url:
        return
    try:
        import apprise  # type: ignore

        ap = apprise.Apprise()
        ap.add(apprise_url)
        ap.notify(title=title, body=body)
    except ImportError:
        logger.warning("apprise package not installed — notifications disabled.")
    except Exception as e:
        logger.error("Notification failed: %s", e)
